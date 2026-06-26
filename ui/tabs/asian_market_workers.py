# -*- coding: utf-8 -*-
"""Worker threads and shared cache state for Asian market tab."""

from __future__ import annotations

import concurrent.futures
import datetime
import html as html_lib
import json
import os
import re
import threading
import time

import requests
import yfinance as yf
from PyQt6.QtCore import QThread, pyqtSignal

from app.services.asian_market_service import (
    build_yf_session,
    get_yf_rate_limit_status,
    is_yf_rate_limit_error,
    mark_yf_rate_limited,
    sync_asian_kline_cache,
)
from app.services.runtime_constants import CACHE_DIR
from app.services.ui_market_calendar_service import MarketCalendar
from core.logger import get_logger
from ui.services.asian_market_http import (
    ASIAN_MARKET_HTTP_HEADERS,
    ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    asian_market_get,
    asian_market_headers,
    is_http_success,
    response_status_code,
)

log = get_logger(__name__)

JSON_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
RT_JSON_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")
GLOBAL_ASIAN_RT_CACHE: dict[str, dict] = {}
_ASIAN_MARKET_CODES = ("TW", "HK", "T", "KS")
_PE_REFRESH_INTERVAL_SEC = 12 * 60 * 60
_YF_FETCH_MAX_WORKERS = 2
_FETCH_UPDATES_TIMEOUT_SEC = 45
_FETCH_TIMEOUT_MARKET_BACKOFF_SEC = 30 * 60
_FETCH_TIMEOUT_CYCLE_BACKOFF_SEC = 30 * 60
_OPTIONAL_NETWORK_MIN_REMAINING_SEC = 25
_SOURCE_PAYLOAD_CODE_BACKOFF_SEC = 10 * 60

_EMPTY_NUMERIC_MARKERS = {"", "-", "--", "---", "—", "－", "None", "null"}
_NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DEFAULT_HTTP_HEADERS = ASIAN_MARKET_HTTP_HEADERS
_HTTP_TIMEOUT_SEC = ASIAN_MARKET_HTTP_TIMEOUT_SEC


class AsianRealtimePayloadError(ValueError):
    """Raised when a direct Asian realtime source returns an unusable payload."""


def infer_asian_markets(codes) -> list[str]:
    markets: list[str] = []
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if not code:
            continue
        market = MarketCalendar.normalize_market(MarketCalendar.infer_market(code))
        if market not in _ASIAN_MARKET_CODES or market in markets:
            continue
        markets.append(market)
    return markets or list(_ASIAN_MARKET_CODES)


def is_asian_quote_refresh_time(codes) -> bool:
    return any(MarketCalendar.is_quote_refresh_time(market) for market in infer_asian_markets(codes))


def _asian_quote_fetch_priority(code: str) -> int:
    suffix = str(code or "").strip().upper().split(".")[-1]
    return {
        "HK": 0,
        "KS": 1,
        "TW": 2,
        "TWO": 2,
        "T": 3,
    }.get(suffix, 4)


def _asian_market_suffix(code: str) -> str:
    return str(code or "").strip().upper().split(".")[-1]


def _seconds_until_monotonic(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return float(deadline) - time.monotonic()


def _has_optional_network_budget(deadline: float | None) -> bool:
    remaining = _seconds_until_monotonic(deadline)
    return remaining is None or remaining >= _OPTIONAL_NETWORK_MIN_REMAINING_SEC


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed else None

    text = str(value).strip()
    if text in _EMPTY_NUMERIC_MARKERS:
        return None
    compact = text.replace(",", "").replace("¥", "").replace("￥", "").replace("원", "").replace("%", "")
    match = _NUMERIC_TOKEN_RE.search(compact)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def _normalize_pe_value(value) -> float | None:
    pe_value = _to_float(value)
    return pe_value if pe_value is not None and pe_value > 0 else None


def _pe_result(value, source: str) -> tuple[float | None, str]:
    pe_value = _normalize_pe_value(value)
    return (pe_value, source) if pe_value is not None else (None, "")


def _round_pct(value) -> float:
    return round(_to_float(value) or 0.0, 2)


def save_global_asian_rt_cache() -> None:
    try:
        cache_friendly = {}
        for key, value in GLOBAL_ASIAN_RT_CACHE.items():
            cache_friendly[key] = {
                "date": value.get("date", ""),
                "close": value.get("close", 0.0),
                "open": value.get("open", 0.0),
                "high": value.get("high", 0.0),
                "low": value.get("low", 0.0),
                "volume": value.get("volume", 0.0),
                "previous_close": value.get("previous_close", 0.0),
                "pct": _round_pct(value.get("pct", 0.0)),
                "pe": value.get("pe"),
                "pe_source": value.get("pe_source", ""),
                "pe_updated_at": value.get("pe_updated_at", 0.0),
                "pct_5": _round_pct(value.get("pct_5", 0.0)),
                "pct_10": _round_pct(value.get("pct_10", 0.0)),
                "pct_20": _round_pct(value.get("pct_20", 0.0)),
                "currency": value.get("currency", ""),
                "source": value.get("source", ""),
                "quote_quality": value.get("quote_quality", ""),
            }
        cache_dir = os.path.dirname(RT_JSON_CACHE)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(RT_JSON_CACHE, "w", encoding="utf-8") as file_obj:
            json.dump(cache_friendly, file_obj, ensure_ascii=False)
    except (PermissionError, OSError, TypeError, ValueError) as exc:
        log.error(f"[AsianTab] persist RT cache failed: {exc}")


def _strip_html_text(value) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _first_book_price(raw_value) -> float | None:
    for chunk in str(raw_value or "").split("_"):
        price = _to_float(chunk)
        if price is not None and price > 0:
            return price
    return None


def _normalize_trade_date(raw_value) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _history_previous_close(df, quote_date: str | None = None) -> float | None:
    if df is None or getattr(df, "empty", True):
        return None

    if len(df) == 1:
        return _to_float(df.iloc[-1].get("Close"))

    quote_iso = _normalize_trade_date(quote_date)
    last_date = None
    try:
        last_idx = df.index[-1]
        if getattr(last_idx, "tzinfo", None) is not None:
            last_idx = last_idx.tz_localize(None)
        last_date = str(last_idx)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        last_date = None

    if quote_iso and last_date and quote_iso > last_date:
        return _to_float(df.iloc[-1].get("Close"))

    return _to_float(df.iloc[-2].get("Close"))


def _resolve_previous_close(*, realtime_quote: dict | None, fast_info, df) -> float | None:
    quote_payload = realtime_quote or {}
    quote_source = str(quote_payload.get("source") or "").strip().lower()
    quote_prev = _to_float(quote_payload.get("previous_close"))
    if quote_prev is not None and quote_prev > 0 and quote_source != "yfinance":
        return quote_prev

    regular_prev = _to_float(fast_info.get("regularMarketPreviousClose"))
    if regular_prev is not None and regular_prev > 0:
        return regular_prev

    history_prev = _history_previous_close(df, quote_payload.get("date"))
    if history_prev is not None and history_prev > 0:
        return history_prev

    if quote_prev is not None and quote_prev > 0:
        return quote_prev

    fast_prev = _to_float(fast_info.get("previousClose"))
    if fast_prev is not None and fast_prev > 0:
        return fast_prev

    if df is None or getattr(df, "empty", True):
        return None
    last_close = _to_float(df.iloc[-1].get("Close"))
    return last_close if last_close is not None and last_close > 0 else None


def _has_history_rows(df) -> bool:
    return df is not None and not getattr(df, "empty", True)


def _resolve_daily_field(
    *,
    realtime_quote: dict | None,
    fast_info,
    df,
    quote_key: str,
    fast_info_key: str,
    history_column: str,
    default: float | None = None,
) -> float | None:
    quote_payload = realtime_quote or {}
    value = _to_float(quote_payload.get(quote_key)) or _to_float(fast_info.get(fast_info_key))
    if (value is None or value <= 0) and _has_history_rows(df):
        value = float(df.iloc[-1][history_column])
    if value is None or value <= 0:
        return default
    return value


def _resolve_previous_close_value(
    *,
    close_price: float,
    realtime_quote: dict | None,
    fast_info,
    df,
    cached_payload: dict,
) -> float:
    prev_close = _resolve_previous_close(realtime_quote=realtime_quote, fast_info=fast_info, df=df)
    if (prev_close is None or prev_close <= 0) and _has_history_rows(df):
        prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else float(df.iloc[-1]["Close"])
    if prev_close is None or prev_close <= 0:
        prev_close = _to_float((cached_payload or {}).get("previous_close")) or close_price
    return prev_close


def _past_pct_from_history(close_price: float, df, cached_payload: dict, days_ago: int) -> float:
    cache_key = f"pct_{days_ago}"
    if not _has_history_rows(df):
        return _to_float(cached_payload.get(cache_key)) or 0.0
    if len(df) <= days_ago:
        return _to_float(cached_payload.get(cache_key)) or 0.0
    past_close = float(df.iloc[-(days_ago + 1)]["Close"])
    if past_close <= 0:
        return _to_float(cached_payload.get(cache_key)) or 0.0
    return ((close_price / past_close) - 1.0) * 100.0


def _resolve_quote_date(realtime_quote: dict | None, df) -> str | None:
    quote_date = (realtime_quote or {}).get("date")
    if quote_date or not _has_history_rows(df):
        return quote_date
    try:
        last_idx = df.index[-1]
        if getattr(last_idx, "tzinfo", None) is not None:
            last_idx = last_idx.tz_localize(None)
        return str(last_idx)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _format_cooldown_eta(seconds: float) -> str:
    remaining = max(1, int(round(float(seconds or 0.0))))
    if remaining >= 60:
        minutes = (remaining + 59) // 60
        return f"{minutes} 分钟"
    return f"{remaining} 秒"


def _response_body_is_blank(response) -> bool:
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


def _load_realtime_json(response, *, source: str) -> dict:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        payload_state = "empty body" if _response_body_is_blank(response) else "bad JSON"
        raise AsianRealtimePayloadError(f"{source} returned {payload_state}: {exc}") from exc
    if not isinstance(payload, dict):
        payload_type = "empty" if payload is None else type(payload).__name__
        raise AsianRealtimePayloadError(f"{source} returned unexpected JSON payload: {payload_type}")
    return payload


def _pick_twse_price(info: dict, prev_close: float | None) -> tuple[float | None, str]:
    for key, quality in (("z", "last"), ("pz", "match")):
        price = _to_float(info.get(key))
        if price is not None and price > 0:
            return price, quality

    bid_price = _first_book_price(info.get("b"))
    ask_price = _first_book_price(info.get("a"))
    if bid_price is not None and ask_price is not None:
        return round((bid_price + ask_price) / 2.0, 4), "indicative_mid"
    if bid_price is not None:
        return bid_price, "bid_only"
    if ask_price is not None:
        return ask_price, "ask_only"

    open_price = _to_float(info.get("o"))
    if open_price is not None and open_price > 0:
        return open_price, "open_fallback"
    if prev_close is not None and prev_close > 0:
        return prev_close, "prev_close_fallback"
    return None, "missing"


def _fetch_tw_realtime_quote(code: str, http_session) -> dict | None:
    suffix = str(code or "").split(".")[-1].upper()
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    market_prefix = "otc" if suffix == "TWO" else "tse"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={market_prefix}_{base_code}.tw&json=1&delay=0"
    response = asian_market_get(
        url,
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://mis.twse.com.tw/"},
        timeout=15,
    )
    data = _load_realtime_json(response, source="twse_mis")
    info = (data.get("msgArray") or [{}])[0]
    if not info:
        return None

    prev_close = _to_float(info.get("y"))
    close_price, quote_quality = _pick_twse_price(info, prev_close)
    if close_price is None or close_price <= 0:
        return None

    open_price = _to_float(info.get("o")) or prev_close or close_price
    high_price = _to_float(info.get("h")) or max(open_price or 0.0, close_price)
    low_price = _to_float(info.get("l")) or min(open_price or close_price, close_price)
    quote_date = _normalize_trade_date(info.get("d") or info.get("^"))
    volume = _to_float(info.get("v")) or 0.0
    return {
        "date": quote_date,
        "close": close_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": volume,
        "previous_close": prev_close,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": quote_quality,
    }


def _fetch_kr_realtime_quote(code: str, http_session) -> dict | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{base_code}"
    response = asian_market_get(
        url,
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    data = _load_realtime_json(response, source="naver_realtime")
    info = (data.get("datas") or [{}])[0]
    if not info:
        return None

    close_price = _to_float(info.get("closePriceRaw") or info.get("closePrice"))
    if close_price is None or close_price <= 0:
        return None

    ratio = _to_float(info.get("fluctuationsRatioRaw") or info.get("fluctuationsRatio"))
    diff_value = _to_float(info.get("compareToPreviousClosePriceRaw") or info.get("compareToPreviousClosePrice"))
    direction_code = str((info.get("compareToPreviousPrice") or {}).get("code") or "").strip()
    prev_close = None
    if diff_value is not None:
        signed_diff = diff_value
        if ratio is not None and ratio != 0:
            signed_diff = abs(diff_value) if ratio > 0 else -abs(diff_value)
        elif direction_code in {"4", "5"}:
            signed_diff = -abs(diff_value)
        elif direction_code:
            signed_diff = abs(diff_value)
        prev_close = close_price - signed_diff
    if prev_close is None and ratio is not None and abs(1.0 + ratio / 100.0) > 1e-9:
        prev_close = close_price / (1.0 + ratio / 100.0)

    return {
        "date": _normalize_trade_date(info.get("localTradedAt")),
        "close": close_price,
        "open": _to_float(info.get("openPriceRaw") or info.get("openPrice")) or close_price,
        "high": _to_float(info.get("highPriceRaw") or info.get("highPrice")) or close_price,
        "low": _to_float(info.get("lowPriceRaw") or info.get("lowPrice")) or close_price,
        "volume": _to_float(info.get("accumulatedTradingVolumeRaw") or info.get("accumulatedTradingVolume")) or 0.0,
        "previous_close": prev_close,
        "currency": ((info.get("currencyType") or {}).get("code") or "KRW"),
        "source": "naver_realtime",
        "quote_quality": "last",
    }


def _fetch_hk_realtime_quote(code: str, http_session) -> dict | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    quote_code = base_code.zfill(5) if base_code.isdigit() else base_code
    url = f"https://qt.gtimg.cn/q=hk{quote_code}"
    response = asian_market_get(
        url,
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://stockapp.finance.qq.com/"},
        timeout=15,
    )
    raw_content = getattr(response, "content", None)
    if raw_content is not None:
        text = raw_content.decode("gb18030", errors="replace")
    else:
        text = str(getattr(response, "text", "") or "")

    match = re.search(r'v_hk\d+="([^"]*)"', text)
    if not match:
        return None

    fields = match.group(1).split("~")

    def field(index: int) -> str:
        return fields[index] if index < len(fields) else ""

    close_price = _to_float(field(3) or field(35))
    if close_price is None or close_price <= 0:
        return None

    prev_close = _to_float(field(4))
    open_price = _to_float(field(5)) or prev_close or close_price
    high_price = _to_float(field(33)) or max(open_price, close_price)
    low_price = _to_float(field(34)) or min(open_price, close_price)

    return {
        "date": _normalize_trade_date(field(30)),
        "close": close_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": _to_float(field(6) or field(36)) or 0.0,
        "previous_close": prev_close,
        "currency": "HKD",
        "source": "tencent_hk",
        "quote_quality": "free_delayed",
    }


def _fetch_jp_realtime_quote(code: str, http_session) -> dict | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    response = asian_market_get(url, session=http_session, headers=_DEFAULT_HTTP_HEADERS, timeout=_HTTP_TIMEOUT_SEC)
    html = response.text
    quote = _parse_jp_realtime_page(html)
    if quote:
        return quote

    status_code = response_status_code(response)
    if status_code >= 400 or "現在表示できません" in html:
        retry_response = asian_market_get(
            url,
            headers=asian_market_headers(
                {
                    "Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6",
                    "Referer": "https://finance.yahoo.co.jp/",
                }
            ),
            timeout=_HTTP_TIMEOUT_SEC,
        )
        if is_http_success(retry_response):
            return _parse_jp_realtime_page(retry_response.text)

    return None


def _parse_jp_realtime_page(html: str) -> dict | None:
    prefix = "__PRELOADED_STATE__ = "
    start = html.find(prefix)
    if start >= 0:
        end = html.find("</script>", start)
        if end >= 0:
            payload = json.loads(html[start + len(prefix) : end].strip())
            board = (payload.get("mainStocksPriceBoard") or {}).get("priceBoard") or {}
            detail = (payload.get("mainStocksDetail") or {}).get("detail") or {}
            page_info = payload.get("pageInfo") or {}

            close_price = _to_float(board.get("price"))
            if close_price is not None and close_price > 0:
                quote_date = None
                current_ms = _to_float(page_info.get("currentDateTime"))
                if current_ms is not None and current_ms > 0:
                    quote_date = (
                        datetime.datetime.fromtimestamp(
                            current_ms / 1000.0,
                            tz=datetime.timezone.utc,
                        )
                        .astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                        .date()
                        .isoformat()
                    )

                low_price = _to_float(detail.get("lowPrice"))
                high_price = _to_float(detail.get("highPrice"))

                return {
                    "date": quote_date or MarketCalendar.now("T").date().isoformat(),
                    "close": close_price,
                    "open": _to_float(detail.get("openPrice")) or close_price,
                    "high": high_price or close_price,
                    "low": low_price or close_price,
                    "volume": _to_float(detail.get("volume")) or 0.0,
                    "previous_close": _to_float(detail.get("previousPrice")),
                    "currency": "JPY",
                    "source": "yj_finance_page",
                    "quote_quality": "free_delayed",
                }

    close_price = _extract_jp_page_price(html)
    if close_price is None or close_price <= 0:
        return None

    previous_close, _previous_date = _extract_jp_indicator_value(html, "previousPrice")
    open_price, open_date = _extract_jp_indicator_value(html, "openPrice")
    high_price, high_date = _extract_jp_indicator_value(html, "highPrice")
    low_price, low_date = _extract_jp_indicator_value(html, "lowPrice")
    volume, volume_date = _extract_jp_indicator_value(html, "volume")
    quote_date = _latest_normalized_date(open_date, high_date, low_date, volume_date)

    return {
        "date": quote_date or MarketCalendar.now("T").date().isoformat(),
        "close": close_price,
        "open": open_price or close_price,
        "high": high_price or close_price,
        "low": low_price or close_price,
        "volume": volume or 0.0,
        "previous_close": previous_close,
        "currency": "JPY",
        "source": "yj_finance_page",
        "quote_quality": "free_delayed",
    }


def _extract_jp_page_price(page_text: str) -> float | None:
    match = re.search(
        r"CommonPriceBoard__price_[^>]*>.*?StyledNumber__value_[^>]*>([^<]+)</span>",
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return _to_float(match.group(1))


def _extract_jp_indicator_value(page_text: str, key: str) -> tuple[float | None, str | None]:
    for pattern in (
        rf'\\"{re.escape(key)}\\":\{{.*?\\"value\\":\\"([^\\"]*)\\".*?\\"updateDateMeta\\":\\"([^\\"]*)\\"',
        rf'"{re.escape(key)}":\{{.*?"value":"([^"]*)".*?"updateDateMeta":"([^"]*)"',
    ):
        match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if match:
            return _to_float(match.group(1)), match.group(2)
    return None, None


def _latest_normalized_date(*raw_dates: str | None) -> str | None:
    dates = [parsed for parsed in (_normalize_trade_date(raw_date) for raw_date in raw_dates) if parsed]
    return max(dates) if dates else None


def _fetch_yfinance_realtime_quote(code: str, yf_session) -> dict | None:
    ticker = yf.Ticker(code, session=yf_session)
    fast_info = ticker.fast_info
    df = ticker.history(period="5d", interval="1d", timeout=15)
    if df.empty:
        return None

    close_price = _to_float(fast_info.get("lastPrice")) or float(df.iloc[-1]["Close"])
    if close_price is None or close_price <= 0:
        return None

    prev_close = _resolve_previous_close(
        realtime_quote={"source": "yfinance"},
        fast_info=fast_info,
        df=df,
    )
    if prev_close is None or prev_close <= 0:
        prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else float(df.iloc[-1]["Close"])

    quote_date = None
    try:
        last_idx = df.index[-1]
        if getattr(last_idx, "tzinfo", None) is not None:
            last_idx = last_idx.tz_localize(None)
        quote_date = str(last_idx)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        quote_date = None

    return {
        "date": quote_date,
        "close": close_price,
        "open": _to_float(fast_info.get("open")) or float(df.iloc[-1]["Open"]),
        "high": _to_float(fast_info.get("dayHigh")) or float(df.iloc[-1]["High"]),
        "low": _to_float(fast_info.get("dayLow")) or float(df.iloc[-1]["Low"]),
        "volume": _to_float(fast_info.get("lastVolume")) or float(df.iloc[-1].get("Volume", 0) or 0),
        "previous_close": prev_close,
        "currency": fast_info.get("currency", "USD"),
        "source": "yfinance",
        "quote_quality": "last",
        "df_today": df,
    }


def _fetch_twse_pe(code: str, http_session) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""

    response = asian_market_get(
        "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&selectType=ALL",
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://www.twse.com.tw/"},
        timeout=15,
    )
    data = response.json()
    fields = list(data.get("fields") or [])
    rows = data.get("data") or []
    if not fields or not rows:
        return None, ""

    try:
        code_idx = fields.index("證券代號")
        pe_idx = fields.index("本益比")
    except ValueError:
        return None, ""

    for row in rows:
        if len(row) <= max(code_idx, pe_idx):
            continue
        if str(row[code_idx]).strip() != base_code:
            continue
        return _pe_result(row[pe_idx], "twse_per")
    return None, ""


def _fetch_tpex_pe(code: str, http_session) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""

    response = asian_market_get(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://www.tpex.org.tw/"},
        timeout=15,
    )
    rows = response.json()
    if not isinstance(rows, list):
        return None, ""

    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("SecuritiesCompanyCode") or "").strip() != base_code:
            continue
        return _pe_result(row.get("PriceEarningRatio"), "tpex_per")
    return None, ""


def _fetch_kr_naver_pe(code: str, http_session) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""

    response = asian_market_get(
        f"https://finance.naver.com/item/main.naver?code={base_code}",
        session=http_session,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    match = re.search(
        r"<em\s+id=[\"']_per[\"'][^>]*>(.*?)</em>",
        response.text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, ""
    return _pe_result(_strip_html_text(match.group(1)), "naver_per")


def _parse_jp_yahoo_pe_from_html(page_text: str) -> tuple[float | None, str]:
    json_match = re.search(
        r'"per"\s*:\s*\{[^{}]*"value"\s*:\s*"([^"]+)"',
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    if json_match:
        pe_value = _normalize_pe_value(json_match.group(1))
        if pe_value is not None:
            return pe_value, "yahoo_jp_per"

    item_match = re.search(
        r"<span[^>]*>\s*PER\s*</span>.*?</dt>\s*<dd[^>]*>(.*?)</dd>",
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not item_match:
        return None, ""
    return _pe_result(_strip_html_text(item_match.group(1)), "yahoo_jp_per")


def _fetch_jp_kabutan_pe(base_code: str) -> tuple[float | None, str]:
    response = asian_market_get(
        f"https://kabutan.jp/stock/?code={base_code}",
        headers=asian_market_headers(
            {
                "Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6",
                "Referer": "https://kabutan.jp/",
            }
        ),
        timeout=_HTTP_TIMEOUT_SEC,
    )
    if not is_http_success(response):
        return None, ""

    match = re.search(
        r"<th[^>]*>\s*PER\s*</th>\s*<td[^>]*>(.*?)</td>",
        response.text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, ""
    return _pe_result(_strip_html_text(match.group(1)), "kabutan_per")


def _fetch_jp_yahoo_pe(code: str, http_session) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""

    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    headers = asian_market_headers(
        {
            "Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6",
            "Referer": "https://finance.yahoo.co.jp/",
        }
    )
    response = asian_market_get(
        url,
        session=http_session,
        headers=headers,
        timeout=_HTTP_TIMEOUT_SEC,
    )
    page_text = response.text
    pe_value, source = _parse_jp_yahoo_pe_from_html(page_text)
    if pe_value is not None:
        return pe_value, source

    status_code = response_status_code(response)
    yahoo_error_page = "ご覧になろうとしているページは現在表示できません" in page_text
    if status_code < 400 and not yahoo_error_page:
        return _fetch_jp_kabutan_pe(base_code)

    retry_response = asian_market_get(url, headers=headers, timeout=_HTTP_TIMEOUT_SEC)
    if is_http_success(retry_response):
        pe_value, source = _parse_jp_yahoo_pe_from_html(retry_response.text)
        if pe_value is not None:
            return pe_value, source
    return _fetch_jp_kabutan_pe(base_code)


def _fetch_asian_pe_fallback(code: str, http_session) -> tuple[float | None, str]:
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code or http_session is None:
        return None, ""

    suffix = normalized_code.split(".")[-1]
    try:
        if suffix == "TW":
            return _fetch_twse_pe(normalized_code, http_session)
        if suffix == "TWO":
            return _fetch_tpex_pe(normalized_code, http_session)
        if suffix == "KS":
            return _fetch_kr_naver_pe(normalized_code, http_session)
        if suffix == "T":
            if get_yf_rate_limit_status()["active"]:
                return _fetch_jp_kabutan_pe(normalized_code.split(".")[0])
            return _fetch_jp_yahoo_pe(normalized_code, http_session)
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        requests.RequestException,
        json.JSONDecodeError,
    ) as exc:
        log.debug(f"[AsianTab] PE 兜底源拉取失败 {normalized_code}: {exc}")
    return None, ""


def fetch_asian_realtime_quote(
    code: str,
    *,
    yf_session=None,
    allow_yfinance_fallback: bool = True,
    raise_on_source_payload_error: bool = False,
):
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code:
        return None

    session = yf_session or build_yf_session()
    suffix = normalized_code.split(".")[-1]

    try:
        if suffix in {"TW", "TWO"}:
            quote = _fetch_tw_realtime_quote(normalized_code, session)
        elif suffix == "HK":
            quote = _fetch_hk_realtime_quote(normalized_code, session)
        elif suffix == "KS":
            quote = _fetch_kr_realtime_quote(normalized_code, session)
        elif suffix == "T":
            quote = _fetch_jp_realtime_quote(normalized_code, session)
        else:
            quote = None
    except AsianRealtimePayloadError as exc:
        if raise_on_source_payload_error:
            raise
        log.debug(f"[AsianTab] 替代实时源载荷不可用 {normalized_code}: {exc}")
        return None
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        log.debug(f"[AsianTab] 替代实时源拉取失败 {normalized_code}: {exc}")
        quote = None

    if quote:
        return quote

    if not allow_yfinance_fallback:
        log.debug("[AsianTab] Skip optional yfinance realtime fallback %s: low time budget", normalized_code)
        return None

    rate_limit_status = get_yf_rate_limit_status()
    if rate_limit_status["active"]:
        log.debug(
            "[AsianTab] 跳过 yfinance 实时回退 %s: 冷却剩余 %s",
            normalized_code,
            _format_cooldown_eta(rate_limit_status["remaining_sec"]),
        )
        return None

    try:
        return _fetch_yfinance_realtime_quote(normalized_code, session)
    except Exception as exc:
        if is_yf_rate_limit_error(exc):
            remaining_sec = mark_yf_rate_limited(exc)
            log.warning(
                "[AsianTab] yfinance 实时回退触发限流 %s: %s | 冷却 %s",
                normalized_code,
                exc,
                _format_cooldown_eta(remaining_sec),
            )
            return None
        if isinstance(
            exc,
            (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ),
        ):
            log.debug(f"[AsianTab] yfinance 实时回退失败 {normalized_code}: {exc}")
            return None
        raise


class AsianMarketWorker(QThread):
    progress = pyqtSignal(str)
    result_ready = pyqtSignal(dict)

    def __init__(self, codes):
        super().__init__()
        self.codes = list(codes or [])
        self._is_running = True
        self._pause_mode = False
        self._manual_refresh_requested = False
        self._cycle_done = threading.Event()
        self._cycle_done.set()
        self._last_status = ""
        self._market_backoff_until: dict[str, float] = {}
        self._code_backoff_until: dict[str, float] = {}
        self._backoff_lock = threading.Lock()
        self._timeout_backoff_until = 0.0
        self._fetch_deadline_monotonic: float | None = None
        self._last_fetch_timed_out = False
        self._last_fetch_source_degraded = False

    def stop(self):
        self._is_running = False
        self._manual_refresh_requested = False
        self._cycle_done.set()

    def pause_for_cache_sync(self):
        self._pause_mode = True

    def resume_auto_refresh(self):
        self._pause_mode = False

    def trigger_refresh(self):
        self._manual_refresh_requested = True

    def wait_for_cycle_idle(self, timeout_sec: float = 30.0) -> bool:
        return self._cycle_done.wait(timeout_sec)

    def _emit_status_once(self, message: str):
        if message != self._last_status:
            self._last_status = message
            self.progress.emit(message)

    def _sleep_with_break(self, seconds: float) -> bool:
        deadline = time.time() + seconds
        while self._is_running and time.time() < deadline:
            time.sleep(0.1)
        return self._is_running

    def _handle_optional_yahoo_error(self, code: str, exc: Exception, context: str) -> bool:
        if is_yf_rate_limit_error(exc):
            remaining_sec = mark_yf_rate_limited(exc)
            log.warning(
                "[AsianTab] %s 触发 Yahoo Finance 限流 %s: %s | 冷却 %s",
                context,
                code,
                exc,
                _format_cooldown_eta(remaining_sec),
            )
            return True
        if isinstance(
            exc,
            (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ),
        ):
            log.debug(f"[AsianTab] {context}失败 {code}: {exc}")
            return False
        raise exc

    def _prune_market_backoff(self, now_ts: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        self._market_backoff_until = {
            market: until_ts
            for market, until_ts in self._market_backoff_until.items()
            if float(until_ts or 0.0) > now
        }

    def _prune_code_backoff(self, now_ts: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            self._code_backoff_until = {
                code: until_ts
                for code, until_ts in self._code_backoff_until.items()
                if float(until_ts or 0.0) > now
            }

    def _mark_market_backoff(self, market: str, *, now_ts: float | None = None) -> None:
        normalized_market = str(market or "").strip().upper()
        if not normalized_market:
            return
        now = time.time() if now_ts is None else float(now_ts)
        self._market_backoff_until[normalized_market] = now + _FETCH_TIMEOUT_MARKET_BACKOFF_SEC

    def _mark_code_backoff(self, code: str, *, now_ts: float | None = None) -> None:
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            self._code_backoff_until[normalized_code] = now + _SOURCE_PAYLOAD_CODE_BACKOFF_SEC

    def _mark_timeout_backoff(self, *, now_ts: float | None = None, duration_sec: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        duration = float(duration_sec or _FETCH_TIMEOUT_CYCLE_BACKOFF_SEC)
        self._timeout_backoff_until = max(
            float(self._timeout_backoff_until or 0.0),
            now + duration,
        )

    def defer_auto_refresh(self, seconds: float, reason: str = "") -> None:
        duration = max(0.0, float(seconds or 0.0))
        if duration <= 0:
            return
        self._mark_timeout_backoff(duration_sec=duration)
        if reason:
            log.info("[AsianTab] Auto refresh deferred for %.0fs: %s", duration, reason)

    def _timeout_backoff_remaining(self, *, now_ts: float | None = None) -> float:
        now = time.time() if now_ts is None else float(now_ts)
        remaining = float(self._timeout_backoff_until or 0.0) - now
        if remaining <= 0:
            self._timeout_backoff_until = 0.0
            return 0.0
        return remaining

    def _clear_timeout_backoff(self) -> None:
        self._timeout_backoff_until = 0.0

    def _is_market_backoff_active(self, code: str, *, now_ts: float | None = None) -> bool:
        market = _asian_market_suffix(code)
        if not market:
            return False
        now = time.time() if now_ts is None else float(now_ts)
        return float(self._market_backoff_until.get(market, 0.0) or 0.0) > now

    def _is_code_backoff_active(self, code: str, *, now_ts: float | None = None) -> bool:
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return False
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            return float(self._code_backoff_until.get(normalized_code, 0.0) or 0.0) > now

    def _mark_source_payload_degraded(self) -> None:
        with self._backoff_lock:
            self._last_fetch_source_degraded = True

    def _source_payload_degraded(self) -> bool:
        with self._backoff_lock:
            return bool(self._last_fetch_source_degraded)

    def _fetch_yahoo_enrichment(self, code: str, yf_session, *, allow_network: bool = True):
        if not allow_network:
            return {}, None, None
        if get_yf_rate_limit_status()["active"]:
            return {}, None, None

        ticker = yf.Ticker(code, session=yf_session)
        fast_info = {}
        df = None

        try:
            fast_info = dict(ticker.fast_info or {})
        except Exception as exc:
            if self._handle_optional_yahoo_error(code, exc, "Yahoo fast_info"):
                return fast_info, df, ticker

        if get_yf_rate_limit_status()["active"]:
            return fast_info, df, ticker

        try:
            history_df = ticker.history(period="2mo", interval="1d", timeout=15)
            if not getattr(history_df, "empty", True):
                df = history_df
        except Exception as exc:
            self._handle_optional_yahoo_error(code, exc, "Yahoo 日线补充")

        return fast_info, df, ticker

    def _refresh_pe_if_needed(
        self,
        code: str,
        *,
        ticker,
        info_session,
        pe_value,
        pe_source: str,
        pe_updated_at: float,
        allow_optional_network: bool = True,
    ):
        now_ts = time.time()
        if (now_ts - pe_updated_at) < _PE_REFRESH_INTERVAL_SEC:
            return pe_value, pe_source, pe_updated_at

        market = MarketCalendar.normalize_market(MarketCalendar.infer_market(code))
        if MarketCalendar.is_quote_refresh_time(market):
            return pe_value, pe_source, pe_updated_at
        if not allow_optional_network:
            return pe_value, pe_source, pe_updated_at

        yf_status = get_yf_rate_limit_status()
        yahoo_allowed = not MarketCalendar.is_quote_refresh_time(market) and not yf_status["active"]

        if yahoo_allowed:
            try:
                info_ticker = ticker if ticker is not None else yf.Ticker(code, session=info_session)
                info = info_ticker.info
                trailing_pe = self._normalize_pe(info.get("trailingPE"))
                forward_pe = self._normalize_pe(info.get("forwardPE"))
                if trailing_pe is not None:
                    return trailing_pe, "trailing", time.time()
                if forward_pe is not None:
                    return forward_pe, "forward", time.time()
                pe_value = None
                pe_source = ""
                pe_updated_at = time.time()
            except Exception as exc:
                self._handle_optional_yahoo_error(code, exc, "PE 拉取")

        fallback_pe, fallback_source = _fetch_asian_pe_fallback(code, info_session)
        if fallback_pe is not None:
            return fallback_pe, fallback_source, time.time()

        return pe_value, pe_source, time.time()

    def _fetch_single_code(self, code: str, yf_session, info_session):
        deadline = getattr(self, "_fetch_deadline_monotonic", None)
        remaining = _seconds_until_monotonic(deadline)
        if remaining is not None and remaining <= 0:
            return code, None

        realtime_quote = None
        try:
            realtime_quote = fetch_asian_realtime_quote(
                code,
                yf_session=yf_session,
                allow_yfinance_fallback=_has_optional_network_budget(deadline),
                raise_on_source_payload_error=True,
            )
        except AsianRealtimePayloadError as exc:
            self._mark_code_backoff(code)
            self._mark_source_payload_degraded()
            log.debug(
                "[AsianTab] 替代实时源不可解析 %s: %s，已跳过单票回退并短退避 %s",
                code,
                exc,
                _format_cooldown_eta(_SOURCE_PAYLOAD_CODE_BACKOFF_SEC),
            )
            return code, None
        except Exception as exc:
            self._handle_optional_yahoo_error(code, exc, "单票实时回退")

        fast_info = {}
        df = (realtime_quote or {}).get("df_today")
        ticker = None
        quote_source = str((realtime_quote or {}).get("source") or "").strip().lower()
        if _has_optional_network_budget(deadline) and (not realtime_quote or quote_source == "yfinance"):
            fast_info, history_df, ticker = self._fetch_yahoo_enrichment(
                code,
                yf_session,
                allow_network=_has_optional_network_budget(deadline),
            )
            if history_df is not None:
                df = history_df

        close_price = _resolve_daily_field(
            realtime_quote=realtime_quote,
            fast_info=fast_info,
            df=df,
            quote_key="close",
            fast_info_key="lastPrice",
            history_column="Close",
        )
        if close_price is None or close_price <= 0:
            return code, None

        day_open = _resolve_daily_field(
            realtime_quote=realtime_quote,
            fast_info=fast_info,
            df=df,
            quote_key="open",
            fast_info_key="open",
            history_column="Open",
            default=close_price,
        )
        day_high = _resolve_daily_field(
            realtime_quote=realtime_quote,
            fast_info=fast_info,
            df=df,
            quote_key="high",
            fast_info_key="dayHigh",
            history_column="High",
            default=max(day_open, close_price),
        )
        day_low = _resolve_daily_field(
            realtime_quote=realtime_quote,
            fast_info=fast_info,
            df=df,
            quote_key="low",
            fast_info_key="dayLow",
            history_column="Low",
            default=min(day_open, close_price),
        )

        cached_payload = GLOBAL_ASIAN_RT_CACHE.get(code, {}) or {}
        prev_close = _resolve_previous_close_value(
            close_price=close_price,
            realtime_quote=realtime_quote,
            fast_info=fast_info,
            df=df,
            cached_payload=cached_payload,
        )

        pct = 0.0
        if prev_close > 0:
            pct = ((close_price / prev_close) - 1.0) * 100.0
        quote_date = _resolve_quote_date(realtime_quote, df)

        pe_value = cached_payload.get("pe")
        pe_source = cached_payload.get("pe_source", "")
        pe_updated_at = float(cached_payload.get("pe_updated_at", 0.0) or 0.0)
        pe_value, pe_source, pe_updated_at = self._refresh_pe_if_needed(
            code,
            ticker=ticker,
            info_session=info_session,
            pe_value=pe_value,
            pe_source=pe_source,
            pe_updated_at=pe_updated_at,
            allow_optional_network=_has_optional_network_budget(deadline),
        )

        payload = {
            "date": quote_date,
            "close": close_price,
            "open": day_open,
            "high": day_high,
            "low": day_low,
            "volume": _to_float((realtime_quote or {}).get("volume")) or 0.0,
            "previous_close": prev_close,
            "pct": pct,
            "pct_5": _past_pct_from_history(close_price, df, cached_payload, 5),
            "pct_10": _past_pct_from_history(close_price, df, cached_payload, 10),
            "pct_20": _past_pct_from_history(close_price, df, cached_payload, 20),
            "currency": (realtime_quote or {}).get("currency") or fast_info.get("currency", "USD"),
            "pe": pe_value,
            "pe_source": pe_source,
            "pe_updated_at": pe_updated_at,
            "source": (realtime_quote or {}).get("source", "yfinance"),
            "quote_quality": (realtime_quote or {}).get("quote_quality", ""),
            "df_today": df,
        }
        GLOBAL_ASIAN_RT_CACHE[code] = payload
        return code, payload

    @staticmethod
    def _normalize_pe(value):
        return _normalize_pe_value(value)

    def _fetch_updates(self) -> dict:
        updates = {}
        yf_session = build_yf_session()
        info_session = yf_session
        raw_codes = list(dict.fromkeys(str(code).strip() for code in self.codes if str(code).strip()))
        if not raw_codes:
            return updates
        now_ts = time.time()
        self._prune_market_backoff(now_ts)
        self._prune_code_backoff(now_ts)
        skipped_markets = sorted(
            {_asian_market_suffix(code) for code in raw_codes if self._is_market_backoff_active(code, now_ts=now_ts)}
        )
        skipped_codes = sorted({code for code in raw_codes if self._is_code_backoff_active(code, now_ts=now_ts)})
        eligible_codes = [
            code
            for code in raw_codes
            if not self._is_market_backoff_active(code, now_ts=now_ts)
            and not self._is_code_backoff_active(code, now_ts=now_ts)
        ]
        if skipped_markets:
            log.info("[AsianTab] Skip markets in short backoff: %s", ",".join(skipped_markets))
        if skipped_codes:
            log.debug("[AsianTab] Skip codes in source-payload backoff: %s", ",".join(skipped_codes))
        if not eligible_codes:
            return updates
        codes = [
            code
            for _idx, code in sorted(
                enumerate(eligible_codes),
                key=lambda item: (_asian_quote_fetch_priority(item[1]), item[0]),
            )
        ]

        max_workers = max(1, min(_YF_FETCH_MAX_WORKERS, len(codes)))
        deadline = time.monotonic() + _FETCH_UPDATES_TIMEOUT_SEC
        previous_deadline = getattr(self, "_fetch_deadline_monotonic", None)
        self._fetch_deadline_monotonic = deadline
        self._last_fetch_timed_out = False
        with self._backoff_lock:
            self._last_fetch_source_degraded = False
        timed_out = False
        cancelled = False
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(self._fetch_single_code, code, yf_session, info_session): code for code in codes}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=_FETCH_UPDATES_TIMEOUT_SEC):
                if not self._is_running:
                    cancelled = True
                    break
                code = futures[future]
                try:
                    result_code, payload = future.result(timeout=1)
                    if payload:
                        updates[result_code] = payload
                except Exception as exc:
                    if is_yf_rate_limit_error(exc):
                        remaining_sec = mark_yf_rate_limited(exc)
                        log.warning(
                            "[AsianTab] future 结果触发 Yahoo Finance 限流 %s: %s | 冷却 %s",
                            code,
                            exc,
                            _format_cooldown_eta(remaining_sec),
                        )
                        continue
                    if isinstance(
                        exc,
                        (
                            concurrent.futures.CancelledError,
                            AttributeError,
                            KeyError,
                            OSError,
                            RuntimeError,
                            TypeError,
                            ValueError,
                        ),
                    ):
                        log.debug(f"[AsianTab] 单票拉取失败 {code}: {exc}")
                        continue
                    raise
        except concurrent.futures.TimeoutError:
            timed_out = True
            self._last_fetch_timed_out = True
            unfinished_markets = sorted(
                {_asian_market_suffix(code) for future, code in futures.items() if not future.done()}
            )
            for market in unfinished_markets:
                self._mark_market_backoff(market)
            self._mark_timeout_backoff()
            if unfinished_markets:
                log.warning("[AsianTab] Timeout degraded markets: %s", ",".join(unfinished_markets))
            log.warning(
                "[AsianTab] 本轮亚洲报价抓取达到 %s 秒上限，已取消未完成请求并启用短暂市场降级",
                _FETCH_UPDATES_TIMEOUT_SEC,
            )
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            executor.shutdown(wait=not (timed_out or cancelled or not self._is_running), cancel_futures=True)
            self._fetch_deadline_monotonic = previous_deadline

        return updates

    def run(self):
        while self._is_running:
            auto_refresh_allowed = is_asian_quote_refresh_time(self.codes)
            manual_refresh = self._manual_refresh_requested

            if self._pause_mode and not manual_refresh:
                self._emit_status_once("亚洲市场后台刷新已暂停，等待缓存同步完成")
                if not self._sleep_with_break(0.5):
                    return
                continue

            if not auto_refresh_allowed and not manual_refresh:
                self._emit_status_once("盘后静默中，可点击刷新亚洲市场")
                if not self._sleep_with_break(1.0):
                    return
                continue

            rate_limit_status = get_yf_rate_limit_status()
            if rate_limit_status["active"]:
                self._emit_status_once(
                    "Yahoo Finance 限流冷却中，约 "
                    f"{_format_cooldown_eta(rate_limit_status['remaining_sec'])} 后重试，仍尝试交易所实时报价"
                )
            else:
                self._last_status = ""

            if not manual_refresh:
                timeout_backoff_remaining = self._timeout_backoff_remaining()
                if timeout_backoff_remaining > 0:
                    self._emit_status_once(
                        "亚洲市场后台刷新已短暂降级，约 "
                        f"{_format_cooldown_eta(timeout_backoff_remaining)} 后重试"
                    )
                    if not self._sleep_with_break(min(30.0, timeout_backoff_remaining)):
                        return
                    continue

            self._cycle_done.clear()
            try:
                now = MarketCalendar.now("CN")
                self.progress.emit(f"[{now.strftime('%H:%M:%S')}] 正在拉取亚洲市场最新报价...")
                updates = self._fetch_updates()
                if manual_refresh and updates and not getattr(self, "_last_fetch_timed_out", False):
                    self._clear_timeout_backoff()
                if self._is_running and updates:
                    save_global_asian_rt_cache()
                    source_payload_degraded = self._source_payload_degraded()
                    defer_ui_update = (
                        getattr(self, "_last_fetch_timed_out", False) or source_payload_degraded
                    ) and not manual_refresh
                    if not defer_ui_update:
                        self.result_ready.emit(updates)
                    message = (
                        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 亚洲市场报价更新完成，获取 {len(updates)} 只"
                    )
                    if defer_ui_update:
                        reason = (
                            "source payload degraded"
                            if source_payload_degraded and not getattr(self, "_last_fetch_timed_out", False)
                            else "timed out"
                        )
                        message = (
                            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                            f"Asian market quote refresh {reason}; cached {len(updates)} updates and deferred UI repaint"
                        )
                    self.progress.emit(message)
                    log.info(f"[AsianTab] {message}")
            except Exception as exc:
                error_text = str(exc)
                if is_yf_rate_limit_error(exc):
                    remaining_sec = mark_yf_rate_limited(exc)
                    hint = (
                        "Yahoo Finance 返回 429，已进入冷却，约 "
                        f"{_format_cooldown_eta(remaining_sec)} 后重试，当前沿用本地缓存"
                    )
                    self.progress.emit(hint)
                    log.warning(f"[AsianTab] {hint} | Native Error: {exc}")
                elif isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
                    if "Timeout" in error_text or "Connection" in error_text or "Max retries" in error_text:
                        hint = "连接 Yahoo Finance 失败，请检查外网或代理"
                    elif "NoneType" in error_text and "subscriptable" in error_text:
                        hint = "上游返回了空响应，请切换网络后重试"
                    else:
                        hint = f"亚洲行情拉取异常: {error_text}"
                    self.progress.emit(hint)
                    log.error(f"[AsianTab] {hint} | Native Error: {exc}")
                else:
                    raise
            finally:
                self._manual_refresh_requested = False
                self._cycle_done.set()

            if not self._is_running:
                return

            if not auto_refresh_allowed:
                continue

            if not self._sleep_with_break(120.0):
                return


class AsianCacheFetcherThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_success = False
        self.result_message = ""

    def run(self):
        try:
            success, message, _report = sync_asian_kline_cache(
                max_workers=3,
                period="1y",
            )
            self.result_success = bool(success)
            self.result_message = str(message or "")
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.result_success = False
            self.result_message = f"盘后拉取异常: {exc}"
