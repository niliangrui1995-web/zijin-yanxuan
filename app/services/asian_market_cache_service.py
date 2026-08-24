"""Application-facing Asian-market cache repository."""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Iterator, Mapping, MutableMapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from domains.market_calendar import MarketCalendar
from infra.storage.asian_market_cache import (
    ASIAN_KLINE_CACHE,
    ASIAN_REALTIME_CACHE,
    cache_mtime,
    read_json_cache,
    write_json_cache,
)
from infra.tasks.lifecycle import raise_if_cancelled


class AsianQuoteCacheStore(MutableMapping[str, dict[str, object]]):
    """Lock-protected realtime quote cache shared by UI and worker threads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quotes: dict[str, dict[str, object]] = {}

    @staticmethod
    def _code(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _payload(value: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError("Asian realtime quote payload must be a mapping")
        return dict(value)

    def __getitem__(self, key: str) -> dict[str, object]:
        code = self._code(key)
        with self._lock:
            return dict(self._quotes[code])

    def __setitem__(self, key: str, value: Mapping[str, object]) -> None:
        self.set_quote(key, value)

    def __delitem__(self, key: str) -> None:
        code = self._code(key)
        with self._lock:
            del self._quotes[code]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._quotes))

    def __len__(self) -> int:
        with self._lock:
            return len(self._quotes)

    def get(self, key: str, default=None):
        code = self._code(key)
        with self._lock:
            value = self._quotes.get(code)
            return dict(value) if value is not None else default

    def items(self):
        return tuple(self.snapshot().items())

    def set_quote(self, code: str, payload: Mapping[str, object]) -> None:
        normalized_code = self._code(code)
        if not normalized_code:
            return
        with self._lock:
            self._quotes[normalized_code] = self._payload(payload)

    def merge_quote(self, code: str, payload: Mapping[str, object]) -> None:
        normalized_code = self._code(code)
        if not normalized_code:
            return
        update = self._payload(payload)
        with self._lock:
            self._quotes[normalized_code] = {**self._quotes.get(normalized_code, {}), **update}

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {code: dict(payload) for code, payload in self._quotes.items()}


GLOBAL_ASIAN_RT_CACHE: MutableMapping[str, dict[str, object]] = AsianQuoteCacheStore()


def get_realtime_quote(cache: Mapping[str, Mapping[str, object]], code: str) -> dict[str, object]:
    value = cache.get(str(code or "").strip()) if isinstance(cache, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def set_realtime_quote(
    cache: MutableMapping[str, dict[str, object]],
    code: str,
    payload: Mapping[str, object],
) -> None:
    if isinstance(cache, AsianQuoteCacheStore):
        cache.set_quote(code, payload)
        return
    cache[str(code or "").strip()] = dict(payload or {})


def merge_realtime_quote(
    cache: MutableMapping[str, dict[str, object]],
    code: str,
    payload: Mapping[str, object],
) -> None:
    if isinstance(cache, AsianQuoteCacheStore):
        cache.merge_quote(code, payload)
        return
    normalized_code = str(code or "").strip()
    cache[normalized_code] = {**dict(cache.get(normalized_code) or {}), **dict(payload or {})}


def snapshot_realtime_quotes(cache: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    if isinstance(cache, AsianQuoteCacheStore):
        return cache.snapshot()
    return {
        str(code): dict(payload)
        for code, payload in cache.items()
        if isinstance(payload, Mapping)
    }


def read_mapping_cache(path: str) -> dict:
    payload = read_json_cache(path, default={})
    return payload if isinstance(payload, dict) else {}


def _cache_signature(path: str) -> tuple[str, int, int] | None:
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
        stat = resolved.stat()
    except (OSError, TypeError, ValueError):
        return None
    return str(resolved), int(stat.st_mtime_ns), int(stat.st_size)


@lru_cache(maxsize=4)
def _load_asian_ticker_index(signature: tuple[str, int, int]) -> dict[str, dict]:
    payload = read_json_cache(signature[0], default={})
    stocks = payload.get("stocks", []) if isinstance(payload, dict) else []
    if not isinstance(stocks, list):
        return {}
    return {
        str(stock.get("ticker") or "").strip().upper(): stock
        for stock in stocks
        if isinstance(stock, dict) and str(stock.get("ticker") or "").strip()
    }


def _ticker_index_for_path(path: str, cancellation_token=None) -> dict[str, dict]:
    raise_if_cancelled(cancellation_token)
    signature = _cache_signature(path)
    if signature is None:
        return {}
    index = _load_asian_ticker_index(signature)
    raise_if_cancelled(cancellation_token)
    return index


def clear_asian_ticker_index_cache() -> None:
    _load_asian_ticker_index.cache_clear()


def load_cached_asian_stock(path: str, code: str, *, cancellation_token=None) -> dict | None:
    normalized_code = str(code or "").strip().upper()
    stock = _ticker_index_for_path(path, cancellation_token).get(normalized_code)
    raise_if_cancelled(cancellation_token)
    return deepcopy(stock) if stock is not None else None


def _round_percentage(value: object) -> float:
    try:
        parsed = float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        parsed = 0.0
    return round(parsed, 2)


def _serialize_realtime_quote(value: Mapping[str, object]) -> dict:
    return {
        "date": value.get("date", ""),
        "close": value.get("close", 0.0),
        "open": value.get("open", 0.0),
        "high": value.get("high", 0.0),
        "low": value.get("low", 0.0),
        "volume": value.get("volume", 0.0),
        "previous_close": value.get("previous_close", 0.0),
        "pct": _round_percentage(value.get("pct", 0.0)),
        "pe": value.get("pe"),
        "pe_source": value.get("pe_source", ""),
        "pe_updated_at": value.get("pe_updated_at", 0.0),
        "pct_5": _round_percentage(value.get("pct_5", 0.0)),
        "pct_10": _round_percentage(value.get("pct_10", 0.0)),
        "pct_20": _round_percentage(value.get("pct_20", 0.0)),
        "currency": value.get("currency", ""),
        "source": value.get("source", ""),
        "quote_quality": value.get("quote_quality", ""),
    }


def write_realtime_quote_cache(
    quotes: Mapping[str, Mapping[str, object]],
    path: str = ASIAN_REALTIME_CACHE,
) -> None:
    payload = {str(code): _serialize_realtime_quote(quote) for code, quote in quotes.items()}
    write_json_cache(path, payload)


def _latest_trade_date_item(item: object) -> tuple[str, dt.date] | None:
    if not isinstance(item, dict):
        return None
    ticker = str(item.get("ticker") or "").strip().upper()
    klines = item.get("klines")
    if "." not in ticker or not isinstance(klines, list) or not klines or not isinstance(klines[-1], dict):
        return None
    raw_date = str(klines[-1].get("date") or "").strip()
    try:
        trade_date = dt.datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return MarketCalendar.normalize_market(ticker.rsplit(".", 1)[-1]), trade_date


def load_latest_trade_dates(path: str = ASIAN_KLINE_CACHE) -> dict[str, dt.date]:
    latest_dates: dict[str, dt.date] = {}
    for item in _ticker_index_for_path(path).values():
        parsed = _latest_trade_date_item(item)
        if parsed is None:
            continue
        market, trade_date = parsed
        latest_dates[market] = max(trade_date, latest_dates.get(market, trade_date))
    return latest_dates

__all__ = [
    "ASIAN_KLINE_CACHE",
    "ASIAN_REALTIME_CACHE",
    "AsianQuoteCacheStore",
    "cache_mtime",
    "clear_asian_ticker_index_cache",
    "get_realtime_quote",
    "load_cached_asian_stock",
    "load_latest_trade_dates",
    "merge_realtime_quote",
    "read_mapping_cache",
    "read_json_cache",
    "set_realtime_quote",
    "snapshot_realtime_quotes",
    "write_realtime_quote_cache",
    "write_json_cache",
]
