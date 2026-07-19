# -*- coding: utf-8 -*-
"""Pure period-return payload construction for the AI industry-chain tab."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from infra.tasks.lifecycle import raise_if_cancelled

_HISTORY_ERRORS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)


def _empty_returns(periods: Sequence[int]) -> dict[int, float | None]:
    return {int(period): None for period in periods}


def _coerce_frame(frame):
    try:
        return frame.to_pandas() if hasattr(frame, "to_pandas") else frame
    except _HISTORY_ERRORS:
        return None


def _close_series(frame):
    frame = _coerce_frame(frame)
    if frame is None or not hasattr(frame, "columns") or len(frame) == 0:
        return None
    close_col = "close" if "close" in frame.columns else ("收盘" if "收盘" in frame.columns else "")
    if not close_col:
        return None
    try:
        frame = frame.sort_index() if hasattr(frame, "sort_index") else frame
        return frame[close_col].dropna().astype(float)
    except _HISTORY_ERRORS:
        return None


def period_returns_from_frame(frame, periods: Sequence[int]) -> dict[int, float | None]:
    periods = tuple(int(period) for period in periods)
    closes = _close_series(frame)
    if closes is None or len(closes) == 0:
        return _empty_returns(periods)
    latest = float(closes.iloc[-1])
    return {period: _period_return(closes, latest, period) for period in periods}


def _period_return(closes, latest: float, period: int) -> float | None:
    if len(closes) <= period:
        return None
    base = float(closes.iloc[-period - 1])
    if base <= 0 or latest <= 0:
        return None
    return (latest / base - 1.0) * 100.0


def _coerce_close_values(values) -> tuple[float, ...] | None:
    try:
        closes = []
        for value in values:
            if value is None:
                continue
            close = float(value)
            if not math.isnan(close):
                closes.append(close)
    except (TypeError, ValueError):
        return None
    return tuple(closes)


def _period_return_from_close_values(closes, latest: float, period: int) -> float | None:
    if len(closes) <= period:
        return None
    base = closes[-period - 1]
    if base <= 0 or latest <= 0:
        return None
    return (latest / base - 1.0) * 100.0


def _period_returns_from_close_tail(values, periods: Sequence[int]) -> dict[int, float | None]:
    periods = tuple(int(period) for period in periods)
    closes = _coerce_close_values(values)
    if not closes:
        return _empty_returns(periods)
    latest = closes[-1]
    return {
        period: _period_return_from_close_values(closes, latest, period)
        for period in periods
    }


def _returns_from_close_tail_batch(data_provider, codes, periods, cancellation_token=None):
    batch_reader = getattr(data_provider, "get_close_tail_batch", None)
    if not callable(batch_reader):
        return None
    raise_if_cancelled(cancellation_token)
    try:
        payload = batch_reader(codes, max(periods, default=0) + 1)
    except _HISTORY_ERRORS:
        payload = None
    raise_if_cancelled(cancellation_token)
    if not isinstance(payload, Mapping):
        return None
    return {
        code: _period_returns_from_close_tail(payload.get(code, ()), periods)
        for code in codes
    }


def _history_frames(data_provider, codes: tuple[str, ...], cancellation_token=None) -> dict[str, object]:
    raise_if_cancelled(cancellation_token)
    batch_reader = getattr(data_provider, "get_data_batch", None)
    if callable(batch_reader):
        try:
            payload = batch_reader(codes)
        except _HISTORY_ERRORS:
            payload = None
        raise_if_cancelled(cancellation_token)
        if isinstance(payload, Mapping):
            return {str(code or "").strip(): frame for code, frame in payload.items()}
    return _history_frames_one_by_one(data_provider, codes, cancellation_token)


def _history_frames_one_by_one(data_provider, codes: tuple[str, ...], cancellation_token=None) -> dict[str, object]:
    getter = getattr(data_provider, "get_data", None)
    if not callable(getter):
        return {}
    frames: dict[str, object] = {}
    for code in codes:
        raise_if_cancelled(cancellation_token)
        try:
            frames[code] = getter(code)
        except _HISTORY_ERRORS:
            frames[code] = None
    return frames


def _returns_by_code(frames, codes, periods, cancellation_token=None) -> dict[str, dict[int, float | None]]:
    result: dict[str, dict[int, float | None]] = {}
    for code in codes:
        raise_if_cancelled(cancellation_token)
        result[code] = period_returns_from_frame(frames.get(code), periods)
    return result


def _apply_returns(rows, returns_by_code, period_columns, placeholder, cancellation_token=None) -> None:
    periods = tuple(int(period) for period in period_columns)
    for row in rows:
        raise_if_cancelled(cancellation_token)
        values = returns_by_code.get(str(row.get("代码") or "").strip()) or _empty_returns(periods)
        for period, column in period_columns.items():
            value = values.get(int(period))
            row[column] = placeholder if value is None else value


def build_period_return_rows(
    rows: Sequence[Mapping],
    *,
    data_provider,
    period_columns: Mapping[int, str],
    placeholder: object,
    cancellation_token=None,
) -> list[dict]:
    prepared = [dict(row) for row in rows]
    codes = tuple(dict.fromkeys(str(row.get("代码") or "").strip() for row in prepared if row.get("代码")))
    periods = tuple(int(period) for period in period_columns)
    returns_by_code = _returns_from_close_tail_batch(data_provider, codes, periods, cancellation_token)
    if returns_by_code is None:
        frames = _history_frames(data_provider, codes, cancellation_token)
        returns_by_code = _returns_by_code(frames, codes, periods, cancellation_token)
    _apply_returns(prepared, returns_by_code, period_columns, placeholder, cancellation_token)
    return prepared


__all__ = ["build_period_return_rows", "period_returns_from_frame"]
