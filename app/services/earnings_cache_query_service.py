# -*- coding: utf-8 -*-
"""Lightweight, dataframe-free read model for the earnings cache."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import SupportsFloat, SupportsIndex, cast

from domains.industry_chain.pool_service import normalize_ai_chain_code

DEFAULT_EARNINGS_KEEP_DAYS = 30
EARNINGS_QOQ_MIN_PCT = 30.0

_FloatInput = str | bytes | bytearray | memoryview | SupportsFloat | SupportsIndex
_ContextMapping = Mapping[object, object] | Mapping[str, object]


def _raise_if_cancelled(cancellation_token=None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _runtime_float(value: object, default: float) -> float:
    """Keep builtin ``float`` coercion while documenting its runtime-checked input."""

    return float(cast(_FloatInput, value or default))


def _china_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _record_reveal_date(record: Mapping[str, object]) -> str:
    return str(record.get("揭晓日") or record.get("公告日期") or record.get("源公告日期") or "").strip()


def _normalize_record_dates(record: dict, fallback_capture_time: str) -> None:
    capture_time = str(record.get("发现时间") or record.get("discovered_at") or fallback_capture_time or "").strip()
    reveal_date = _record_reveal_date(record)
    if capture_time:
        record["发现时间"] = capture_time
    if reveal_date:
        record["揭晓日"] = reveal_date


def _eligible_cached_record(record: Mapping[str, object], *, now: datetime, keep_days: int) -> bool:
    try:
        current_profit = _runtime_float(record.get("单季净利润_新增", 0.0), 0.0)
        qoq = _runtime_float(record.get("环比增速_百分比", 0.0), 0.0)
        yoy = _runtime_float(record.get("同比增速_百分比", -1.0), -1.0)
    except (TypeError, ValueError):
        return False
    if current_profit <= 0 or qoq < EARNINGS_QOQ_MIN_PCT or yoy <= 0:
        return False

    reveal_date = _record_reveal_date(record)[:10]
    try:
        reveal_time = datetime.strptime(reveal_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return (now - reveal_time).days <= keep_days


def _sort_key(record: Mapping[str, object]) -> tuple[str, float]:
    try:
        qoq = _runtime_float(record.get("环比增速_百分比", 0.0), 0.0)
    except (TypeError, ValueError):
        qoq = 0.0
    return str(record.get("揭晓日", "") or ""), qoq


def _normalize_allowed_codes(stock_codes: Iterable[object] | None) -> set[str] | None:
    if stock_codes is None:
        return None
    result = set()
    for value in stock_codes:
        code = normalize_ai_chain_code(value)
        if code:
            result.add(code)
    return result


def _normalize_context_map(context_map: _ContextMapping | None) -> dict[str, str]:
    result = {}
    for raw_code, raw_value in (context_map or {}).items():
        code = normalize_ai_chain_code(raw_code)
        value = str(raw_value or "").strip()
        if code and value:
            result[code] = value
    return result


def _prepare_cached_record(
    raw_record: object,
    *,
    allowed_codes: set[str] | None,
    context_map: Mapping[str, str],
    updated_at: str,
    now: datetime,
    keep_days: int,
) -> dict | None:
    if not isinstance(raw_record, Mapping):
        return None
    record = dict(raw_record)
    if not _eligible_cached_record(record, now=now, keep_days=keep_days):
        return None
    code = normalize_ai_chain_code(record.get("股票代码") or record.get("代码") or record.get("stock_code"))
    if allowed_codes is not None and code not in allowed_codes:
        return None
    _normalize_record_dates(record, updated_at)
    record["所属行业与概念"] = context_map.get(code, "--")
    return record


def prepare_cached_earnings_rows(
    payload: Mapping[str, object] | None,
    *,
    updated_at: str = "",
    stock_codes: Iterable[object] | None = None,
    context_map: _ContextMapping | None = None,
    now: datetime | None = None,
    keep_days: int = DEFAULT_EARNINGS_KEEP_DAYS,
    cancellation_token=None,
) -> list[dict]:
    """Apply the engine's cache-view rules without importing its scan stack."""

    raw_records = payload.get("records", []) if isinstance(payload, Mapping) else []
    if not isinstance(raw_records, (list, tuple)):
        return []

    allowed_codes = _normalize_allowed_codes(stock_codes)
    normalized_context = _normalize_context_map(context_map)
    current_time = now or _china_now()
    normalized_keep_days = max(0, int(keep_days))
    result: list[dict] = []
    for raw_record in raw_records:
        _raise_if_cancelled(cancellation_token)
        record = _prepare_cached_record(
            raw_record,
            allowed_codes=allowed_codes,
            context_map=normalized_context,
            updated_at=str(updated_at or ""),
            now=current_time,
            keep_days=normalized_keep_days,
        )
        if record is not None:
            result.append(record)

    _raise_if_cancelled(cancellation_token)
    return sorted(result, key=_sort_key, reverse=True)


def _load_read_only_state() -> tuple[dict, str]:
    from infra.storage.stock_context_repository import load_earnings_state_payload

    payload, updated_at = load_earnings_state_payload()
    if payload:
        return payload, updated_at

    # Read-only compatibility for installations that have not migrated the legacy JSON yet.
    from core.runtime_paths import PROJECT_ROOT

    cache_path = Path(PROJECT_ROOT) / "data" / "earnings_state.json"
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            legacy_payload = json.load(handle)
        if not isinstance(legacy_payload, dict):
            return {}, ""
        updated_at = datetime.fromtimestamp(cache_path.stat().st_mtime).isoformat(sep=" ", timespec="seconds")
        return legacy_payload, updated_at
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, ""


def _load_cached_stock_codes() -> set[str]:
    from app.services.ui_industry_chain_service import load_cached_ai_industry_chain_stock_codes

    try:
        return set(load_cached_ai_industry_chain_stock_codes() or set())
    except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return set()


def _load_cached_context_map() -> dict[str, str]:
    from app.services.ui_industry_chain_service import load_cached_ai_industry_chain_context_map

    try:
        return dict(load_cached_ai_industry_chain_context_map() or {})
    except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return {}


def load_cached_earnings_rows(
    *,
    cancellation_token=None,
    state_loader: Callable[[], tuple[dict, str]] | None = None,
    stock_codes_loader: Callable[[], Iterable[object]] | None = None,
    context_map_loader: Callable[[], Mapping[object, object]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Read and prepare cached rows without constructing ``EarningsEngine``."""

    _raise_if_cancelled(cancellation_token)
    payload, updated_at = (state_loader or _load_read_only_state)()
    _raise_if_cancelled(cancellation_token)
    stock_codes = (stock_codes_loader or _load_cached_stock_codes)()
    context_map = (context_map_loader or _load_cached_context_map)()
    return prepare_cached_earnings_rows(
        payload,
        updated_at=updated_at,
        stock_codes=stock_codes,
        context_map=context_map,
        now=now,
        cancellation_token=cancellation_token,
    )


__all__ = [
    "DEFAULT_EARNINGS_KEEP_DAYS",
    "EARNINGS_QOQ_MIN_PCT",
    "load_cached_earnings_rows",
    "prepare_cached_earnings_rows",
]
