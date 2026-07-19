"""Unified background data loading for CN and Asian K-line windows."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

import pandas as pd

from app.services.asian_market_cache_service import load_cached_asian_stock
from app.services.kline_open_context import KlineOpenContext
from domains.market_calendar import MarketCalendar
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded, raise_if_cancelled
from infra.tasks.owner_lifecycle import invoke_with_cancellation

_ASIAN_MARKETS = frozenset({"HK", "TW", "T", "KS"})


def _as_history_frame(value: object, cancellation_token=None) -> pd.DataFrame | None:
    raise_if_cancelled(cancellation_token)
    if value is None:
        return None
    frame = _coerce_history_frame(value)
    raise_if_cancelled(cancellation_token)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if "vol" in frame.columns and "volume" not in frame.columns:
        frame = frame.rename(columns={"vol": "volume"})
    return _normalize_history_index(frame, cancellation_token)


def _coerce_history_frame(value: object) -> object:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    converter = getattr(value, "to_pandas", None)
    return converter() if callable(converter) else pd.DataFrame(value)


def _normalize_history_index(frame: pd.DataFrame, cancellation_token=None) -> pd.DataFrame | None:
    raise_if_cancelled(cancellation_token)
    date_column = next((key for key in ("datetime", "date") if key in frame.columns), None)
    if date_column is not None:
        frame = frame.set_index(date_column)
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[~frame.index.isna()].sort_index()
    frame = frame.loc[~frame.index.duplicated(keep="last")]
    raise_if_cancelled(cancellation_token)
    return frame.copy() if not frame.empty else None


def _latest_trade_date(frame: pd.DataFrame | None) -> dt.date | None:
    if frame is None or frame.empty:
        return None
    latest = pd.Timestamp(cast(Any, frame.index.max()))
    return None if pd.isna(latest) else cast(dt.date, latest.date())


def _date_value(value: object) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        timestamp = pd.Timestamp(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return None if pd.isna(timestamp) else cast(dt.date, timestamp.date())


def _provider_source(provider: object, default: str) -> str:
    status_reader = getattr(provider, "get_market_data_source_status", None)
    if not callable(status_reader):
        return default
    try:
        status = status_reader() or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return default
    return str(status.get("active_layer") or default) if isinstance(status, dict) else default


@dataclass(frozen=True, slots=True)
class KlineDataResult:
    code: str
    market: str
    data: pd.DataFrame | None
    source: str
    degraded: bool
    degradation_reason: str = ""
    latest_trade_date: dt.date | None = None

    @property
    def frame(self) -> pd.DataFrame | None:
        return self.data

    @property
    def has_data(self) -> bool:
        return self.data is not None and not self.data.empty


class KlineDataService:
    """Load one owned history frame without touching Qt or chart rendering."""

    def __init__(self, provider=None, *, asian_stock_loader=load_cached_asian_stock) -> None:
        self._provider = provider
        self._asian_stock_loader = asian_stock_loader

    def load(
        self,
        context: KlineOpenContext,
        *,
        asian_cache_path: str = "",
        target_trade_date: object = None,
        refresh_if_stale: bool = True,
        cancellation_token=None,
    ) -> KlineDataResult:
        market = MarketCalendar.infer_market(context.code)
        raise_if_cancelled(cancellation_token)
        if market in _ASIAN_MARKETS:
            return self._load_asian(context, market, asian_cache_path, cancellation_token)
        return self._load_cn(
            context,
            market,
            _date_value(target_trade_date),
            bool(refresh_if_stale),
            cancellation_token,
        )

    def _load_asian(self, context, market, cache_path, cancellation_token) -> KlineDataResult:
        stock = invoke_with_cancellation(
            self._asian_stock_loader,
            cancellation_token,
            cache_path,
            context.code,
        )
        klines = stock.get("klines") if isinstance(stock, dict) else None
        frame = _as_history_frame(klines, cancellation_token)
        raise_if_cancelled(cancellation_token)
        reason = "" if frame is not None else "asian_history_unavailable"
        return KlineDataResult(
            code=context.code,
            market=market,
            data=frame,
            source="asian_json_cache",
            degraded=frame is None,
            degradation_reason=reason,
            latest_trade_date=_latest_trade_date(frame),
        )

    def _load_cn(self, context, market, target_date, refresh_if_stale, cancellation_token) -> KlineDataResult:
        provider = self._provider
        getter = getattr(provider, "get_data", None)
        if not callable(getter):
            return self._unavailable(context.code, market, "provider_unavailable")
        initial = invoke_with_cancellation(getter, cancellation_token, context.code)
        frame = _as_history_frame(initial, cancellation_token)
        stale = target_date is not None and (_latest_trade_date(frame) or dt.date.min) < target_date
        degraded = False
        reason = ""
        if refresh_if_stale and stale:
            frame, degraded, reason = self._refresh_cn(context.code, frame, cancellation_token)
        raise_if_cancelled(cancellation_token)
        if frame is None:
            degraded, reason = True, reason or "history_unavailable"
        return KlineDataResult(
            code=context.code,
            market=market,
            data=frame,
            source=_provider_source(provider, "cn_history_provider"),
            degraded=degraded,
            degradation_reason=reason,
            latest_trade_date=_latest_trade_date(frame),
        )

    def _refresh_cn(self, code, fallback, cancellation_token) -> tuple[pd.DataFrame | None, bool, str]:
        refresher = getattr(self._provider, "get_data_fresh_for_chart", None)
        if not callable(refresher):
            return fallback, True, "fresh_provider_unavailable"
        try:
            fresh = invoke_with_cancellation(
                refresher,
                cancellation_token,
                code,
                force_sync=True,
            )
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError):
            return fallback, True, "fresh_unavailable_using_local"
        normalized = _as_history_frame(fresh, cancellation_token)
        if normalized is None:
            return fallback, True, "fresh_unavailable_using_local"
        return normalized, False, ""

    @staticmethod
    def _unavailable(code: str, market: str, reason: str) -> KlineDataResult:
        return KlineDataResult(
            code=code,
            market=market,
            data=None,
            source="unavailable",
            degraded=True,
            degradation_reason=reason,
        )


__all__ = ["KlineDataResult", "KlineDataService"]
