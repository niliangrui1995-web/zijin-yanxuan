"""Application-facing Asian-market cache repository."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from domains.market_calendar import MarketCalendar
from infra.storage.asian_market_cache import (
    ASIAN_KLINE_CACHE,
    ASIAN_REALTIME_CACHE,
    cache_mtime,
    read_json_cache,
    write_json_cache,
)


def read_mapping_cache(path: str) -> dict:
    payload = read_json_cache(path, default={})
    return payload if isinstance(payload, dict) else {}


def load_cached_asian_stock(path: str, code: str) -> dict | None:
    stocks = read_mapping_cache(path).get("stocks", [])
    if not isinstance(stocks, list):
        return None
    normalized_code = str(code or "").strip()
    for stock in stocks:
        if isinstance(stock, dict) and str(stock.get("ticker") or "").strip() == normalized_code:
            return stock
    return None


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
    for item in read_mapping_cache(path).get("stocks", []):
        parsed = _latest_trade_date_item(item)
        if parsed is None:
            continue
        market, trade_date = parsed
        latest_dates[market] = max(trade_date, latest_dates.get(market, trade_date))
    return latest_dates

__all__ = [
    "ASIAN_KLINE_CACHE",
    "ASIAN_REALTIME_CACHE",
    "cache_mtime",
    "load_cached_asian_stock",
    "load_latest_trade_dates",
    "read_mapping_cache",
    "read_json_cache",
    "write_realtime_quote_cache",
    "write_json_cache",
]
