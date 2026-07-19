"""Pure background preparation for K-line chart rendering."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.services.kline_open_context import KlineOpenContext
from infra.tasks.lifecycle import raise_if_cancelled


def _as_pandas_frame(frame: object) -> pd.DataFrame:
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    converter = getattr(frame, "to_pandas", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, pd.DataFrame):
            return converted.copy()
    return pd.DataFrame(frame).copy()


def _normalize_history_frames(frame: object) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized = _as_pandas_frame(frame)
    date_column = next((key for key in ("datetime", "date") if key in normalized.columns), None)
    if date_column is not None:
        normalized = normalized.set_index(date_column)
    normalized.index = pd.to_datetime(normalized.index, errors="coerce")
    normalized = normalized.loc[~normalized.index.isna()].sort_index()
    normalized = normalized.loc[~normalized.index.duplicated(keep="last")]
    required = ("open", "high", "low", "close", "volume")
    missing = [column for column in required if column not in normalized.columns]
    if missing:
        raise ValueError(f"K-line data is missing columns: {', '.join(missing)}")
    normalized = normalized.loc[:, list(required)].apply(pd.to_numeric, errors="coerce")
    normalized = normalized.ffill().bfill()
    for period, key in (
        (10, "ma10"),
        (20, "ma20"),
        (50, "ma50"),
        (150, "ma150"),
        (200, "ma200"),
    ):
        normalized[key] = normalized["close"].rolling(period, min_periods=period).mean()
    normalized["volMa20"] = normalized["volume"].rolling(20, min_periods=20).mean()
    ema_fast = normalized["close"].ewm(span=12, adjust=False).mean()
    ema_slow = normalized["close"].ewm(span=26, adjust=False).mean()
    normalized["MACD"] = ema_fast - ema_slow
    normalized["MACD_Signal"] = normalized["MACD"].ewm(span=9, adjust=False).mean()
    normalized["MACD_Hist"] = normalized["MACD"] - normalized["MACD_Signal"]
    return normalized, normalized.iloc[-250:].copy()


def _bind_frame_identity(
    frame: pd.DataFrame,
    *,
    owner_id: str,
    generation: int,
    code: str,
    snapshot_version: int,
) -> None:
    frame.attrs = {
        "kline_window_id": str(owner_id),
        "kline_generation": int(generation),
        "kline_code": str(code),
        "kline_snapshot_version": int(snapshot_version),
    }


def _safe_script_json(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _snapshot_envelope(owner_id, generation, snapshot_version, context, title, chart_data, points) -> dict:
    return {
        "windowId": str(owner_id),
        "window_id": str(owner_id),
        "generation": int(generation),
        "snapshotVersion": int(snapshot_version),
        "snapshot_version": int(snapshot_version),
        "code": context.code,
        "points": int(points),
        "title": title,
        "data": chart_data,
    }


def _owned_history_frames(frame, *, owner_id, generation, code, snapshot_version):
    history_frame, display_frame = _normalize_history_frames(frame)
    if display_frame.empty:
        raise ValueError("K-line data is empty")
    identity = {
        "owner_id": owner_id,
        "generation": generation,
        "code": code,
        "snapshot_version": snapshot_version,
    }
    _bind_frame_identity(history_frame, **identity)
    _bind_frame_identity(display_frame, **identity)
    return history_frame, display_frame


def _prepared_render(
    *,
    owner_id,
    generation,
    snapshot_version,
    context,
    chart_data,
    display_frame,
    history_frame,
    source,
    degraded,
    degradation_reason,
):
    title = str(chart_data.get("title") or f"{context.name} ({context.code}) 日线")
    envelope = _snapshot_envelope(
        owner_id,
        generation,
        snapshot_version,
        context,
        title,
        chart_data,
        len(display_frame),
    )
    return PreparedKlineRender(
        owner_id=str(owner_id),
        generation=int(generation),
        snapshot_version=int(snapshot_version),
        code=context.code,
        title=title,
        payload_json=_safe_script_json(envelope),
        point_count=len(display_frame),
        _display_frame=display_frame,
        _history_frame=history_frame,
        source=str(source or ""),
        degraded=bool(degraded),
        degradation_reason=str(degradation_reason or ""),
    )


@dataclass(frozen=True, slots=True)
class PreparedKlineRender:
    """An immutable render envelope owned by one window generation."""

    owner_id: str
    generation: int
    snapshot_version: int
    code: str
    title: str
    payload_json: str
    point_count: int
    _display_frame: pd.DataFrame | None = field(default=None, repr=False, compare=False)
    _history_frame: pd.DataFrame | None = field(default=None, repr=False, compare=False)
    source: str = ""
    degraded: bool = False
    degradation_reason: str = ""

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.owner_id, self.generation, self.code

    @property
    def display_frame(self) -> pd.DataFrame:
        if self._display_frame is None:
            raise RuntimeError("prepared render has no display frame")
        return self._display_frame.copy(deep=True)

    @property
    def history_frame(self) -> pd.DataFrame:
        if self._history_frame is None:
            raise RuntimeError("prepared render has no history frame")
        return self._history_frame.copy(deep=True)

    def take_owned_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Transfer the worker-built frames once without copying them on the GUI thread."""
        display_frame = self._display_frame
        history_frame = self._history_frame
        if display_frame is None or history_frame is None:
            raise RuntimeError("prepared render frames were already transferred")
        object.__setattr__(self, "_display_frame", None)
        object.__setattr__(self, "_history_frame", None)
        return display_frame, history_frame


class KlineRenderPreparer:
    """Prepare chart data off the GUI thread using an injected payload builder."""

    def __init__(self, payload_builder: Callable[..., Mapping[str, Any]]) -> None:
        if not callable(payload_builder):
            raise TypeError("payload_builder must be callable")
        self._payload_builder = payload_builder

    def prepare(
        self,
        frame: object,
        *,
        context: KlineOpenContext,
        owner_id: str,
        generation: int,
        snapshot_version: int = 1,
        payload_kwargs: Mapping[str, Any] | None = None,
        source: str = "",
        degraded: bool = False,
        degradation_reason: str = "",
        cancellation_token=None,
    ) -> PreparedKlineRender:
        raise_if_cancelled(cancellation_token)
        history_frame, display_frame = _owned_history_frames(
            frame,
            owner_id=owner_id,
            generation=generation,
            code=context.code,
            snapshot_version=snapshot_version,
        )
        raise_if_cancelled(cancellation_token)
        chart_data = self._payload_builder(
            display_frame,
            code=context.code,
            name=context.name,
            vcp_data=context.mutable_vcp_data(),
            **dict(payload_kwargs or {}),
        )
        raise_if_cancelled(cancellation_token)
        return _prepared_render(
            owner_id=owner_id,
            generation=generation,
            snapshot_version=snapshot_version,
            context=context,
            chart_data=chart_data,
            display_frame=display_frame,
            history_frame=history_frame,
            source=source,
            degraded=degraded,
            degradation_reason=degradation_reason,
        )


__all__ = ["KlineRenderPreparer", "PreparedKlineRender"]
