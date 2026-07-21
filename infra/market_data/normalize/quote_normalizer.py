# -*- coding: utf-8 -*-
"""Pure normalization and composition for Asian quote payloads."""

from __future__ import annotations

import datetime
import html as html_lib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

EMPTY_NUMERIC_MARKERS = {"", "-", "--", "---", "—", "－", "None", "null"}
NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")

QuotePayload = dict[str, Any]
QuoteFetcher = Callable[..., QuotePayload | None]
YahooErrorHandler = Callable[[str, Exception, str], bool]


class AsianRealtimePayloadError(ValueError):
    """Raised when a direct Asian realtime source returns an unusable payload."""


def to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed else None

    text = str(value).strip()
    if text in EMPTY_NUMERIC_MARKERS:
        return None
    compact = text.replace(",", "").replace("¥", "").replace("￥", "").replace("원", "").replace("%", "")
    match = NUMERIC_TOKEN_RE.search(compact)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def text(value: object) -> str:
    return "" if value is None else str(value)


def ticker_base(code: object) -> str:
    return text(code).split(".")[0].strip()


def ticker_suffix(code: object) -> str:
    return text(code).split(".")[-1].strip().upper()


def currency_for_ticker(code: object) -> str:
    return {
        "TW": "TWD",
        "TWO": "TWD",
        "KS": "KRW",
        "T": "JPY",
        "HK": "HKD",
    }.get(ticker_suffix(code), "USD")


def first_present(*values: object) -> object | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def first_float(*values: object) -> float | None:
    for value in values:
        parsed = to_float(value)
        if parsed is not None:
            return parsed
    return None


def positive_float(value: object) -> float | None:
    parsed = to_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def first_positive(*values: object) -> float | None:
    for value in values:
        parsed = positive_float(value)
        if parsed is not None:
            return parsed
    return None


def first_mapping_item(value: object) -> Mapping[str, Any]:
    if not isinstance(value, list) or not value:
        return {}
    item = value[0]
    return item if isinstance(item, Mapping) else {}


def as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_pe_value(value: object) -> float | None:
    pe_value = to_float(value)
    return pe_value if pe_value is not None and pe_value > 0 else None


def pe_result(value: object, source: str) -> tuple[float | None, str]:
    pe_value = normalize_pe_value(value)
    return (pe_value, source) if pe_value is not None else (None, "")


def round_pct(value: object) -> float:
    return round(to_float(value) or 0.0, 2)


def strip_html_text(value: object) -> str:
    stripped = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html_lib.unescape(stripped)).strip()


def first_book_price(raw_value: object) -> float | None:
    for chunk in str(raw_value or "").split("_"):
        price = to_float(chunk)
        if price is not None and price > 0:
            return price
    return None


def normalize_trade_date(raw_value: object) -> str | None:
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return None
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    )
    for date_format in formats:
        try:
            return datetime.datetime.strptime(raw_text, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def has_history_rows(frame: Any) -> bool:
    return frame is not None and not getattr(frame, "empty", True)


def history_previous_close(frame: Any, quote_date: str | None = None) -> float | None:
    if not has_history_rows(frame):
        return None
    if len(frame) == 1:
        return to_float(frame.iloc[-1].get("Close"))

    quote_iso = normalize_trade_date(quote_date)
    try:
        last_index = frame.index[-1]
        if getattr(last_index, "tzinfo", None) is not None:
            last_index = last_index.tz_localize(None)
        last_date = str(last_index)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        last_date = None
    if quote_iso and last_date and quote_iso > last_date:
        return to_float(frame.iloc[-1].get("Close"))
    return to_float(frame.iloc[-2].get("Close"))


def resolve_previous_close(
    *,
    realtime_quote: dict[str, Any] | None,
    fast_info: Mapping[str, Any],
    frame: Any,
) -> float | None:
    quote_payload = realtime_quote or {}
    quote_source = str(quote_payload.get("source") or "").strip().lower()
    quote_previous = positive_float(quote_payload.get("previous_close"))
    if quote_source != "yfinance" and quote_previous is not None:
        return quote_previous
    candidates = (
        positive_float(fast_info.get("regularMarketPreviousClose")),
        positive_float(history_previous_close(frame, quote_payload.get("date"))),
        quote_previous,
        positive_float(fast_info.get("previousClose")),
    )
    for candidate in candidates:
        if candidate is not None:
            return candidate
    if not has_history_rows(frame):
        return None
    return positive_float(frame.iloc[-1].get("Close"))


def resolve_daily_field(
    *,
    realtime_quote: dict[str, Any] | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    quote_key: str,
    fast_info_key: str,
    history_column: str,
    default: float | None = None,
) -> float | None:
    quote_payload = realtime_quote or {}
    value = to_float(quote_payload.get(quote_key)) or to_float(fast_info.get(fast_info_key))
    if (value is None or value <= 0) and has_history_rows(frame):
        value = float(frame.iloc[-1][history_column])
    if value is None or value <= 0:
        return default
    return value


def resolve_previous_close_value(
    *,
    close_price: float,
    realtime_quote: dict[str, Any] | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
    resolver: Callable[..., float | None] = resolve_previous_close,
) -> float:
    previous_close = resolver(realtime_quote=realtime_quote, fast_info=fast_info, frame=frame)
    if (previous_close is None or previous_close <= 0) and has_history_rows(frame):
        previous_close = float(frame.iloc[-2]["Close"]) if len(frame) >= 2 else float(frame.iloc[-1]["Close"])
    if previous_close is None or previous_close <= 0:
        previous_close = to_float((cached_payload or {}).get("previous_close")) or close_price
    return previous_close


def past_pct_from_history(
    close_price: float,
    frame: Any,
    cached_payload: Mapping[str, Any],
    days_ago: int,
) -> float:
    cache_key = f"pct_{days_ago}"
    if not has_history_rows(frame) or len(frame) <= days_ago:
        return to_float(cached_payload.get(cache_key)) or 0.0
    past_close = float(frame.iloc[-(days_ago + 1)]["Close"])
    if past_close <= 0:
        return to_float(cached_payload.get(cache_key)) or 0.0
    return ((close_price / past_close) - 1.0) * 100.0


def resolve_quote_date(realtime_quote: dict[str, Any] | None, frame: Any) -> str | None:
    quote_date = (realtime_quote or {}).get("date")
    if quote_date or not has_history_rows(frame):
        return quote_date
    try:
        last_index = frame.index[-1]
        if getattr(last_index, "tzinfo", None) is not None:
            last_index = last_index.tz_localize(None)
        return str(last_index)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def response_body_is_blank(response: Any) -> bool:
    raw_content = getattr(response, "content", None)
    if raw_content is not None:
        try:
            return len(raw_content) <= 0
        except TypeError:
            pass
    raw_text = getattr(response, "text", None)
    if raw_text is not None:
        return not str(raw_text or "").strip()
    return False


def load_realtime_json(response: Any, *, source: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        state = "empty body" if response_body_is_blank(response) else "bad JSON"
        raise AsianRealtimePayloadError(f"{source} returned {state}: {exc}") from exc
    if not isinstance(payload, dict):
        payload_type = "empty" if payload is None else type(payload).__name__
        raise AsianRealtimePayloadError(f"{source} returned unexpected JSON payload: {payload_type}")
    return payload


def pick_twse_price(info: Mapping[str, Any], previous_close: float | None) -> tuple[float | None, str]:
    for key, quality in (("z", "last"), ("pz", "match")):
        price = positive_float(info.get(key))
        if price is not None:
            return price, quality
    bid_price = first_book_price(info.get("b"))
    ask_price = first_book_price(info.get("a"))
    if bid_price is None:
        if ask_price is not None:
            return ask_price, "ask_only"
    elif ask_price is None:
        return bid_price, "bid_only"
    else:
        return round((bid_price + ask_price) / 2.0, 4), "indicative_mid"
    open_price = positive_float(info.get("o"))
    if open_price is not None:
        return open_price, "open_fallback"
    positive_previous = positive_float(previous_close)
    if positive_previous is not None:
        return positive_previous, "prev_close_fallback"
    return None, "missing"


def fetch_quote_sources(
    code: str,
    yf_session: Any,
    *,
    allow_optional_network: bool,
    realtime_fetcher: QuoteFetcher,
    enrichment_fetcher: Callable[..., tuple[dict[str, Any], Any, Any]],
    error_handler: YahooErrorHandler,
) -> tuple[QuotePayload | None, dict[str, Any], Any, Any]:
    realtime_quote = None
    try:
        realtime_quote = realtime_fetcher(
            code,
            yf_session=yf_session,
            allow_yfinance_fallback=allow_optional_network,
            raise_on_source_payload_error=True,
        )
    except AsianRealtimePayloadError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        error_handler(code, exc, "single quote fallback")
    fast_info: dict[str, Any] = {}
    frame = (realtime_quote or {}).get("df_today")
    ticker = None
    quote_source = str((realtime_quote or {}).get("source") or "").strip().lower()
    if allow_optional_network and (not realtime_quote or quote_source == "yfinance"):
        fast_info, history_frame, ticker = enrichment_fetcher(
            code,
            yf_session,
            allow_network=allow_optional_network,
        )
        if history_frame is not None:
            frame = history_frame
    return realtime_quote, fast_info, frame, ticker


def daily_ohlc(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    *, field_resolver: Callable[..., float | None] = resolve_daily_field,
) -> tuple[float, float, float, float] | None:
    close_price = field_resolver(
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        quote_key="close",
        fast_info_key="lastPrice",
        history_column="Close",
    )
    if close_price is None or close_price <= 0:
        return None
    open_price = field_resolver(
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        quote_key="open",
        fast_info_key="open",
        history_column="Open",
        default=close_price,
    )
    if open_price is None:
        open_price = close_price
    high_price = field_resolver(
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        quote_key="high",
        fast_info_key="dayHigh",
        history_column="High",
        default=max(open_price, close_price),
    )
    if high_price is None:
        high_price = max(open_price, close_price)
    low_price = field_resolver(
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        quote_key="low",
        fast_info_key="dayLow",
        history_column="Low",
        default=min(open_price, close_price),
    )
    if low_price is None:
        low_price = min(open_price, close_price)
    return close_price, open_price, high_price, low_price


def price_snapshot(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
    *,
    ohlc_builder: Callable[..., tuple[float, float, float, float] | None] = daily_ohlc,
    previous_close_resolver: Callable[..., float] = resolve_previous_close_value,
    quote_date_resolver: Callable[[dict[str, Any] | None, Any], str | None] = resolve_quote_date,
) -> QuotePayload | None:
    daily_values = ohlc_builder(realtime_quote, fast_info, frame)
    if daily_values is None:
        return None
    close_price, open_price, high_price, low_price = daily_values
    previous_close = previous_close_resolver(
        close_price=close_price,
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        cached_payload=cached_payload,
    )
    return {
        "date": quote_date_resolver(realtime_quote, frame),
        "close": close_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "previous_close": previous_close,
        "pct": ((close_price / previous_close) - 1.0) * 100.0 if previous_close > 0 else 0.0,
    }


def complete_payload(
    snapshot: Mapping[str, Any],
    *,
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
    pe_value: Any,
    pe_source: str,
    pe_updated_at: float,
    float_parser: Callable[[object], float | None] = to_float,
    history_pct: Callable[[float, Any, Mapping[str, Any], int], float] = past_pct_from_history,
) -> QuotePayload:
    close_price = snapshot["close"]
    return {
        **snapshot,
        "volume": float_parser((realtime_quote or {}).get("volume")) or 0.0,
        "pct_5": history_pct(close_price, frame, cached_payload, 5),
        "pct_10": history_pct(close_price, frame, cached_payload, 10),
        "pct_20": history_pct(close_price, frame, cached_payload, 20),
        "currency": (realtime_quote or {}).get("currency") or fast_info.get("currency", "USD"),
        "pe": pe_value,
        "pe_source": pe_source,
        "pe_updated_at": pe_updated_at,
        "source": (realtime_quote or {}).get("source", "yfinance"),
        "quote_quality": (realtime_quote or {}).get("quote_quality", ""),
        "df_today": frame,
    }


__all__ = [
    "AsianRealtimePayloadError",
    "as_mapping",
    "complete_payload",
    "currency_for_ticker",
    "daily_ohlc",
    "fetch_quote_sources",
    "first_book_price",
    "first_float",
    "first_mapping_item",
    "first_positive",
    "first_present",
    "has_history_rows",
    "history_previous_close",
    "load_realtime_json",
    "normalize_pe_value",
    "normalize_trade_date",
    "past_pct_from_history",
    "pe_result",
    "pick_twse_price",
    "positive_float",
    "price_snapshot",
    "resolve_daily_field",
    "resolve_previous_close",
    "resolve_previous_close_value",
    "resolve_quote_date",
    "response_body_is_blank",
    "round_pct",
    "strip_html_text",
    "text",
    "ticker_base",
    "ticker_suffix",
    "to_float",
]
