# -*- coding: utf-8 -*-
"""Asian-market realtime quote providers and payload normalization."""

from __future__ import annotations

import datetime
import html as html_lib
import importlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from core.logger import get_logger
from domains.market_calendar import MarketCalendar
from infra.market_data.asian_market_http import (
    ASIAN_MARKET_HTTP_HEADERS,
    ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    RequestException,
    asian_market_get,
    asian_market_headers,
    is_http_success,
    requests_module,
    response_status_code,
)
from infra.market_data.yfinance_session import (
    build_yf_session,
    get_yf_rate_limit_status,
    is_yf_rate_limit_error,
    mark_yf_rate_limited,
)

log = get_logger(__name__)

PE_REFRESH_INTERVAL_SEC = 12 * 60 * 60
EMPTY_NUMERIC_MARKERS = {"", "-", "--", "---", "—", "－", "None", "null"}
NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")
DEFAULT_HTTP_HEADERS = ASIAN_MARKET_HTTP_HEADERS
HTTP_TIMEOUT_SEC = ASIAN_MARKET_HTTP_TIMEOUT_SEC
_T = TypeVar("_T")
QuotePayload = dict[str, Any]
QuoteFetcher = Callable[..., QuotePayload | None]
PeFetcher = Callable[..., tuple[float | None, str]]
RateLimitStatusGetter = Callable[[], Mapping[str, Any]]
RateLimitPredicate = Callable[[BaseException | None], bool]
RateLimitMarker = Callable[..., float]
YahooErrorHandler = Callable[[str, Exception, str], bool]


class _LazyYFinanceModule:
    """Keep yfinance outside module import and service construction."""

    def __init__(self) -> None:
        self._module = None

    def __getattr__(self, name: str) -> Any:
        module = self._module
        if module is None:
            module = importlib.import_module("yfinance")
            self._module = module
        return getattr(module, name)


yf = _LazyYFinanceModule()


class AsianRealtimePayloadError(ValueError):
    """Raised when a direct Asian realtime source returns an unusable payload."""


class YFinanceOperationError(RuntimeError):
    """Wrap an arbitrary failure raised by the optional yfinance client."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _call_yfinance(operation: Callable[[], _T]) -> _T:
    """Isolate yfinance's intentionally open third-party exception surface."""

    try:
        return operation()
    except Exception as exc:
        raise YFinanceOperationError(exc) from exc


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


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _ticker_base(code: object) -> str:
    return _text(code).split(".")[0].strip()


def _ticker_suffix(code: object) -> str:
    return _text(code).split(".")[-1].strip().upper()


def _first_present(*values: object) -> object | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _first_float(*values: object) -> float | None:
    for value in values:
        parsed = to_float(value)
        if parsed is not None:
            return parsed
    return None


def _positive_float(value: object) -> float | None:
    parsed = to_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _first_positive(*values: object) -> float | None:
    for value in values:
        parsed = _positive_float(value)
        if parsed is not None:
            return parsed
    return None


def _first_mapping_item(value: object) -> Mapping[str, Any]:
    if not isinstance(value, list) or not value:
        return {}
    item = value[0]
    return item if isinstance(item, Mapping) else {}


def _mapping(value: object) -> Mapping[str, Any]:
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
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def first_book_price(raw_value: object) -> float | None:
    for chunk in str(raw_value or "").split("_"):
        price = to_float(chunk)
        if price is not None and price > 0:
            return price
    return None


def normalize_trade_date(raw_value: object) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
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
            return datetime.datetime.strptime(text, date_format).date().isoformat()
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
    quote_previous = _positive_float(quote_payload.get("previous_close"))
    if quote_source != "yfinance" and quote_previous is not None:
        return quote_previous
    candidates = (
        _positive_float(fast_info.get("regularMarketPreviousClose")),
        _positive_float(history_previous_close(frame, quote_payload.get("date"))),
        quote_previous,
        _positive_float(fast_info.get("previousClose")),
    )
    for candidate in candidates:
        if candidate is not None:
            return candidate
    if not has_history_rows(frame):
        return None
    return _positive_float(frame.iloc[-1].get("Close"))


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
) -> float:
    previous_close = resolve_previous_close(realtime_quote=realtime_quote, fast_info=fast_info, frame=frame)
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


def format_cooldown_eta(seconds: float) -> str:
    remaining = max(1, int(round(float(seconds or 0.0))))
    if remaining >= 60:
        return f"{(remaining + 59) // 60} 分钟"
    return f"{remaining} 秒"


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
        price = _positive_float(info.get(key))
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
    open_price = _positive_float(info.get("o"))
    if open_price is not None:
        return open_price, "open_fallback"
    positive_previous = _positive_float(previous_close)
    if positive_previous is not None:
        return positive_previous, "prev_close_fallback"
    return None, "missing"


def fetch_tw_realtime_quote(code: str, http_session: Any) -> dict[str, Any] | None:
    suffix = _ticker_suffix(code)
    base_code = _ticker_base(code)
    if not base_code:
        return None
    market_prefix = "otc" if suffix == "TWO" else "tse"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={market_prefix}_{base_code}.tw&json=1&delay=0"
    response = asian_market_get(
        url,
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://mis.twse.com.tw/"},
        timeout=15,
    )
    data = load_realtime_json(response, source="twse_mis")
    info = _first_mapping_item(data.get("msgArray"))
    if not info:
        return None
    previous_close = to_float(info.get("y"))
    close_price, quote_quality = pick_twse_price(info, previous_close)
    if close_price is None:
        return None
    open_price = _first_positive(info.get("o"), previous_close, close_price)
    if open_price is None:
        return None
    return {
        "date": normalize_trade_date(_first_present(info.get("d"), info.get("^"))),
        "close": close_price,
        "open": open_price,
        "high": _first_positive(info.get("h"), max(open_price, close_price)),
        "low": _first_positive(info.get("l"), min(open_price, close_price)),
        "volume": _first_float(info.get("v"), 0.0),
        "previous_close": previous_close,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": quote_quality,
    }


def _kr_previous_close(info: Mapping[str, Any], close_price: float) -> float | None:
    ratio = _first_float(info.get("fluctuationsRatioRaw"), info.get("fluctuationsRatio"))
    difference = _first_float(
        info.get("compareToPreviousClosePriceRaw"),
        info.get("compareToPreviousClosePrice"),
    )
    direction = _mapping(info.get("compareToPreviousPrice"))
    direction_code = _text(direction.get("code")).strip()
    if difference is not None:
        if ratio is not None:
            if ratio != 0:
                signed = abs(difference) if ratio > 0 else -abs(difference)
                return close_price - signed
        if direction_code in {"4", "5"}:
            return close_price + abs(difference)
        if direction_code:
            return close_price - abs(difference)
        return close_price - difference
    if ratio is None:
        return None
    denominator = 1.0 + ratio / 100.0
    return close_price / denominator if abs(denominator) > 1e-9 else None


def fetch_kr_realtime_quote(code: str, http_session: Any) -> dict[str, Any] | None:
    base_code = _ticker_base(code)
    if not base_code:
        return None
    response = asian_market_get(
        f"https://polling.finance.naver.com/api/realtime/domestic/stock/{base_code}",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    data = load_realtime_json(response, source="naver_realtime")
    info = _first_mapping_item(data.get("datas"))
    if not info:
        return None
    close_price = _first_positive(info.get("closePriceRaw"), info.get("closePrice"))
    if close_price is None:
        return None
    currency = _mapping(info.get("currencyType"))
    return {
        "date": normalize_trade_date(info.get("localTradedAt")),
        "close": close_price,
        "open": _first_positive(info.get("openPriceRaw"), info.get("openPrice"), close_price),
        "high": _first_positive(info.get("highPriceRaw"), info.get("highPrice"), close_price),
        "low": _first_positive(info.get("lowPriceRaw"), info.get("lowPrice"), close_price),
        "volume": _first_float(
            info.get("accumulatedTradingVolumeRaw"),
            info.get("accumulatedTradingVolume"),
            0.0,
        ),
        "previous_close": _kr_previous_close(info, close_price),
        "currency": _first_present(currency.get("code"), "KRW"),
        "source": "naver_realtime",
        "quote_quality": "last",
    }


def _decode_hk_response(response: Any) -> str:
    raw_content = getattr(response, "content", None)
    if raw_content is not None:
        return raw_content.decode("gb18030", errors="replace")
    return str(getattr(response, "text", "") or "")


def fetch_hk_realtime_quote(code: str, http_session: Any) -> dict[str, Any] | None:
    base_code = _ticker_base(code)
    if not base_code:
        return None
    quote_code = base_code.zfill(5) if base_code.isdigit() else base_code
    response = asian_market_get(
        f"https://qt.gtimg.cn/q=hk{quote_code}",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://stockapp.finance.qq.com/"},
        timeout=15,
    )
    matched = re.search(r'v_hk\d+="([^"]*)"', _decode_hk_response(response))
    if not matched:
        return None
    fields = matched.group(1).split("~")
    field = lambda index: fields[index] if index < len(fields) else ""  # noqa: E731
    close_price = _first_positive(field(3), field(35))
    if close_price is None:
        return None
    previous_close = _positive_float(field(4))
    open_price = _first_positive(field(5), previous_close, close_price)
    if open_price is None:
        return None
    return {
        "date": normalize_trade_date(field(30)),
        "close": close_price,
        "open": open_price,
        "high": _first_positive(field(33), max(open_price, close_price)),
        "low": _first_positive(field(34), min(open_price, close_price)),
        "volume": _first_float(field(6), field(36), 0.0),
        "previous_close": previous_close,
        "currency": "HKD",
        "source": "tencent_hk",
        "quote_quality": "free_delayed",
    }


def extract_jp_page_price(page_text: str) -> float | None:
    matched = re.search(
        r"CommonPriceBoard__price_[^>]*>.*?StyledNumber__value_[^>]*>([^<]+)</span>",
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    return to_float(matched.group(1)) if matched else None


def extract_jp_indicator_value(page_text: str, key: str) -> tuple[float | None, str | None]:
    patterns = (
        rf'\\"{re.escape(key)}\\":\{{.*?\\"value\\":\\"([^\\"]*)\\".*?\\"updateDateMeta\\":\\"([^\\"]*)\\"',
        rf'"{re.escape(key)}":\{{.*?"value":"([^"]*)".*?"updateDateMeta":"([^"]*)"',
    )
    for pattern in patterns:
        matched = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if matched:
            return to_float(matched.group(1)), matched.group(2)
    return None, None


def latest_normalized_date(*raw_dates: str | None) -> str | None:
    dates = [parsed for parsed in (normalize_trade_date(raw_date) for raw_date in raw_dates) if parsed]
    return max(dates) if dates else None


def _jp_preloaded_quote(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    stock_board = _mapping(payload.get("mainStocksPriceBoard"))
    board = _mapping(stock_board.get("priceBoard"))
    stock_detail = _mapping(payload.get("mainStocksDetail"))
    detail = _mapping(stock_detail.get("detail"))
    page_info = _mapping(payload.get("pageInfo"))
    close_price = _positive_float(board.get("price"))
    if close_price is None:
        return None
    current_ms = to_float(page_info.get("currentDateTime"))
    quote_date = None
    if current_ms is not None and current_ms > 0:
        quote_date = (
            datetime.datetime.fromtimestamp(current_ms / 1000.0, tz=datetime.timezone.utc)
            .astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            .date()
            .isoformat()
        )
    return {
        "date": _first_present(quote_date, MarketCalendar.now("T").date().isoformat()),
        "close": close_price,
        "open": _first_positive(detail.get("openPrice"), close_price),
        "high": _first_positive(detail.get("highPrice"), close_price),
        "low": _first_positive(detail.get("lowPrice"), close_price),
        "volume": _first_float(detail.get("volume"), 0.0),
        "previous_close": to_float(detail.get("previousPrice")),
        "currency": "JPY",
        "source": "yj_finance_page",
        "quote_quality": "free_delayed",
    }


def _parse_jp_preloaded_page(page_text: str) -> dict[str, Any] | None:
    prefix = "__PRELOADED_STATE__ = "
    start = page_text.find(prefix)
    if start < 0:
        return None
    end = page_text.find("</script>", start)
    if end < 0:
        return None
    payload = json.loads(page_text[start + len(prefix) : end].strip())
    return _jp_preloaded_quote(payload)


def _parse_jp_indicator_page(page_text: str) -> dict[str, Any] | None:
    close_price = extract_jp_page_price(page_text)
    if close_price is None or close_price <= 0:
        return None
    previous_close, _ = extract_jp_indicator_value(page_text, "previousPrice")
    open_price, open_date = extract_jp_indicator_value(page_text, "openPrice")
    high_price, high_date = extract_jp_indicator_value(page_text, "highPrice")
    low_price, low_date = extract_jp_indicator_value(page_text, "lowPrice")
    volume, volume_date = extract_jp_indicator_value(page_text, "volume")
    quote_date = latest_normalized_date(open_date, high_date, low_date, volume_date)
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


def parse_jp_realtime_page(page_text: str) -> dict[str, Any] | None:
    return _parse_jp_preloaded_page(page_text) or _parse_jp_indicator_page(page_text)


def fetch_jp_realtime_quote(code: str, http_session: Any) -> dict[str, Any] | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None
    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    response = asian_market_get(url, session=http_session, headers=DEFAULT_HTTP_HEADERS, timeout=HTTP_TIMEOUT_SEC)
    page_text = response.text
    quote = parse_jp_realtime_page(page_text)
    if quote:
        return quote
    if response_status_code(response) < 400 and "現在表示できません" not in page_text:
        return None
    retry_response = asian_market_get(
        url,
        headers=asian_market_headers(
            {"Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6", "Referer": "https://finance.yahoo.co.jp/"}
        ),
        timeout=HTTP_TIMEOUT_SEC,
    )
    return parse_jp_realtime_page(retry_response.text) if is_http_success(retry_response) else None


def fetch_yfinance_realtime_quote(
    code: str,
    yf_session: Any,
    *,
    yf_module: Any = yf,
) -> dict[str, Any] | None:
    ticker = yf_module.Ticker(code, session=yf_session)
    fast_info = ticker.fast_info
    frame = ticker.history(period="5d", interval="1d", timeout=15)
    if frame.empty:
        return None
    close_price = _first_positive(fast_info.get("lastPrice"), frame.iloc[-1]["Close"])
    if close_price is None:
        return None
    previous_close = resolve_previous_close(
        realtime_quote={"source": "yfinance"},
        fast_info=fast_info,
        frame=frame,
    )
    if previous_close is None:
        history_index = -2 if len(frame) >= 2 else -1
        previous_close = float(frame.iloc[history_index]["Close"])
    return {
        "date": resolve_quote_date(None, frame),
        "close": close_price,
        "open": _first_positive(fast_info.get("open"), frame.iloc[-1]["Open"]),
        "high": _first_positive(fast_info.get("dayHigh"), frame.iloc[-1]["High"]),
        "low": _first_positive(fast_info.get("dayLow"), frame.iloc[-1]["Low"]),
        "volume": _first_float(fast_info.get("lastVolume"), frame.iloc[-1].get("Volume", 0), 0.0),
        "previous_close": previous_close,
        "currency": fast_info.get("currency", "USD"),
        "source": "yfinance",
        "quote_quality": "last",
        "df_today": frame,
    }


def _find_twse_pe(
    rows: Sequence[Sequence[Any]],
    *,
    code_index: int,
    pe_index: int,
    base_code: str,
) -> tuple[float | None, str]:
    required_index = max(code_index, pe_index)
    for row in rows:
        if len(row) <= required_index:
            continue
        if str(row[code_index]).strip() == base_code:
            return pe_result(row[pe_index], "twse_per")
    return None, ""


def fetch_twse_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    base_code = _ticker_base(code)
    if not base_code:
        return None, ""
    response = asian_market_get(
        "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&selectType=ALL",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://www.twse.com.tw/"},
        timeout=15,
    )
    data = response.json()
    fields = list(data.get("fields") or [])
    rows = data.get("data") or []
    if not fields or not rows:
        return None, ""
    try:
        code_index = fields.index("證券代號")
        pe_index = fields.index("本益比")
    except ValueError:
        return None, ""
    return _find_twse_pe(rows, code_index=code_index, pe_index=pe_index, base_code=base_code)


def fetch_tpex_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    response = asian_market_get(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://www.tpex.org.tw/"},
        timeout=15,
    )
    rows = response.json()
    if not isinstance(rows, list):
        return None, ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("SecuritiesCompanyCode") or "").strip() == base_code:
            return pe_result(row.get("PriceEarningRatio"), "tpex_per")
    return None, ""


def fetch_kr_naver_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    response = asian_market_get(
        f"https://finance.naver.com/item/main.naver?code={base_code}",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    matched = re.search(r"<em\s+id=[\"']_per[\"'][^>]*>(.*?)</em>", response.text, re.IGNORECASE | re.DOTALL)
    return pe_result(strip_html_text(matched.group(1)), "naver_per") if matched else (None, "")


def parse_jp_yahoo_pe_from_html(page_text: str) -> tuple[float | None, str]:
    json_match = re.search(
        r'"per"\s*:\s*\{[^{}]*"value"\s*:\s*"([^"]+)"',
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    if json_match:
        pe_value = normalize_pe_value(json_match.group(1))
        if pe_value is not None:
            return pe_value, "yahoo_jp_per"
    item_match = re.search(
        r"<span[^>]*>\s*PER\s*</span>.*?</dt>\s*<dd[^>]*>(.*?)</dd>",
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    return pe_result(strip_html_text(item_match.group(1)), "yahoo_jp_per") if item_match else (None, "")


def fetch_jp_kabutan_pe(base_code: str) -> tuple[float | None, str]:
    response = asian_market_get(
        f"https://kabutan.jp/stock/?code={base_code}",
        headers=asian_market_headers(
            {"Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6", "Referer": "https://kabutan.jp/"}
        ),
        timeout=HTTP_TIMEOUT_SEC,
    )
    if not is_http_success(response):
        return None, ""
    matched = re.search(
        r"<th[^>]*>\s*PER\s*</th>\s*<td[^>]*>(.*?)</td>",
        response.text,
        re.IGNORECASE | re.DOTALL,
    )
    return pe_result(strip_html_text(matched.group(1)), "kabutan_per") if matched else (None, "")


def fetch_jp_yahoo_pe(
    code: str,
    http_session: Any,
    *,
    kabutan_fetcher: Callable[[str], tuple[float | None, str]] = fetch_jp_kabutan_pe,
) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    headers = asian_market_headers(
        {"Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6", "Referer": "https://finance.yahoo.co.jp/"}
    )
    response = asian_market_get(url, session=http_session, headers=headers, timeout=HTTP_TIMEOUT_SEC)
    page_text = response.text
    pe_value, source = parse_jp_yahoo_pe_from_html(page_text)
    if pe_value is not None:
        return pe_value, source
    error_page = "ご覧になろうとしているページは現在表示できません" in page_text
    if response_status_code(response) < 400 and not error_page:
        return kabutan_fetcher(base_code)
    retry_response = asian_market_get(url, headers=headers, timeout=HTTP_TIMEOUT_SEC)
    if is_http_success(retry_response):
        pe_value, source = parse_jp_yahoo_pe_from_html(retry_response.text)
        if pe_value is not None:
            return pe_value, source
    return kabutan_fetcher(base_code)


def _dispatch_asian_pe_fallback(
    normalized_code: str,
    http_session: Any,
    *,
    fetchers: Mapping[str, Callable[..., tuple[float | None, str]]],
    rate_limit_status: Callable[[], Mapping[str, Any]],
) -> tuple[float | None, str]:
    suffix = normalized_code.split(".")[-1]
    if suffix in {"TW", "TWO", "KS"}:
        return fetchers[suffix](normalized_code, http_session)
    if suffix != "T":
        return None, ""
    base_code = normalized_code.split(".")[0]
    if rate_limit_status()["active"]:
        return fetchers["T_KABUTAN"](base_code)
    return fetchers["T"](normalized_code, http_session)


def fetch_asian_pe_fallback(
    code: str,
    http_session: Any,
    *,
    rate_limit_status: Callable[[], Mapping[str, Any]] = get_yf_rate_limit_status,
    twse_fetcher: Callable[..., tuple[float | None, str]] = fetch_twse_pe,
    tpex_fetcher: Callable[..., tuple[float | None, str]] = fetch_tpex_pe,
    kr_fetcher: Callable[..., tuple[float | None, str]] = fetch_kr_naver_pe,
    jp_yahoo_fetcher: Callable[..., tuple[float | None, str]] = fetch_jp_yahoo_pe,
    jp_kabutan_fetcher: Callable[..., tuple[float | None, str]] = fetch_jp_kabutan_pe,
) -> tuple[float | None, str]:
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code or http_session is None:
        return None, ""
    fetchers = {
        "TW": twse_fetcher,
        "TWO": tpex_fetcher,
        "KS": kr_fetcher,
        "T": jp_yahoo_fetcher,
        "T_KABUTAN": jp_kabutan_fetcher,
    }
    try:
        return _dispatch_asian_pe_fallback(
            normalized_code,
            http_session,
            fetchers=fetchers,
            rate_limit_status=rate_limit_status,
        )
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, RequestException, json.JSONDecodeError) as exc:
        log.debug("[AsianProvider] PE fallback failed %s: %s", normalized_code, exc)
        return None, ""


def _direct_quote(
    normalized_code: str,
    session: Any,
    *,
    tw_fetcher: QuoteFetcher,
    hk_fetcher: QuoteFetcher,
    kr_fetcher: QuoteFetcher,
    jp_fetcher: QuoteFetcher,
) -> QuotePayload | None:
    suffix = normalized_code.split(".")[-1]
    fetcher = {
        "TW": tw_fetcher,
        "TWO": tw_fetcher,
        "HK": hk_fetcher,
        "KS": kr_fetcher,
        "T": jp_fetcher,
    }.get(suffix)
    return fetcher(normalized_code, session) if fetcher is not None else None


def _safe_direct_quote(
    normalized_code: str,
    session: Any,
    **fetchers: QuoteFetcher,
) -> QuotePayload | None:
    try:
        return _direct_quote(normalized_code, session, **fetchers)
    except AsianRealtimePayloadError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log.debug("[AsianProvider] direct realtime source failed %s: %s", normalized_code, exc)
        return None


def _safe_yfinance_quote(
    normalized_code: str,
    session: Any,
    *,
    yf_module: Any,
    yf_fetcher: QuoteFetcher,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
) -> QuotePayload | None:
    try:
        return _call_yfinance(lambda: yf_fetcher(normalized_code, session, yf_module=yf_module))
    except YFinanceOperationError as wrapped:
        exc = wrapped.cause
        if rate_limit_error(exc):
            remaining_sec = mark_rate_limited(exc)
            log.warning(
                "[AsianProvider] yfinance realtime rate limited %s: %s | cooldown %s",
                normalized_code,
                exc,
                format_cooldown_eta(remaining_sec),
            )
            return None
        if isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
            log.debug("[AsianProvider] yfinance realtime fallback failed %s: %s", normalized_code, exc)
            return None
        raise


def fetch_asian_realtime_quote(
    code: str,
    *,
    yf_session: Any = None,
    allow_yfinance_fallback: bool = True,
    raise_on_source_payload_error: bool = False,
    yf_module: Any = yf,
    rate_limit_status: RateLimitStatusGetter = get_yf_rate_limit_status,
    rate_limit_error: RateLimitPredicate = is_yf_rate_limit_error,
    mark_rate_limited: RateLimitMarker = mark_yf_rate_limited,
    tw_fetcher: QuoteFetcher = fetch_tw_realtime_quote,
    hk_fetcher: QuoteFetcher = fetch_hk_realtime_quote,
    kr_fetcher: QuoteFetcher = fetch_kr_realtime_quote,
    jp_fetcher: QuoteFetcher = fetch_jp_realtime_quote,
    yf_fetcher: QuoteFetcher = fetch_yfinance_realtime_quote,
) -> QuotePayload | None:
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code:
        return None
    session = yf_session or build_yf_session()
    try:
        quote = _safe_direct_quote(
            normalized_code,
            session,
            tw_fetcher=tw_fetcher,
            hk_fetcher=hk_fetcher,
            kr_fetcher=kr_fetcher,
            jp_fetcher=jp_fetcher,
        )
    except AsianRealtimePayloadError as exc:
        if raise_on_source_payload_error:
            raise
        log.debug("[AsianProvider] unusable direct payload %s: %s", normalized_code, exc)
        return None
    if quote or not allow_yfinance_fallback:
        return quote
    status = rate_limit_status()
    if status["active"]:
        log.debug("[AsianProvider] skip yfinance %s: cooldown %s", normalized_code, status["remaining_sec"])
        return None
    return _safe_yfinance_quote(
        normalized_code,
        session,
        yf_module=yf_module,
        yf_fetcher=yf_fetcher,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
    )


def handle_optional_yahoo_error(
    code: str,
    exc: Exception,
    context: str,
    *,
    rate_limit_error: RateLimitPredicate = is_yf_rate_limit_error,
    mark_rate_limited: RateLimitMarker = mark_yf_rate_limited,
) -> bool:
    if rate_limit_error(exc):
        remaining_sec = mark_rate_limited(exc)
        log.warning(
            "[AsianProvider] %s rate limited %s: %s | cooldown %s",
            context,
            code,
            exc,
            format_cooldown_eta(remaining_sec),
        )
        return True
    if isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
        log.debug("[AsianProvider] %s failed %s: %s", context, code, exc)
        return False
    raise exc


def fetch_yahoo_enrichment(
    code: str,
    yf_session: Any,
    *,
    allow_network: bool = True,
    yf_module: Any = yf,
    rate_limit_status: RateLimitStatusGetter = get_yf_rate_limit_status,
    error_handler: YahooErrorHandler = handle_optional_yahoo_error,
) -> tuple[dict[str, Any], Any, Any]:
    if not allow_network or rate_limit_status()["active"]:
        return {}, None, None
    ticker = yf_module.Ticker(code, session=yf_session)
    fast_info = {}
    frame = None
    try:
        fast_info = _call_yfinance(lambda: dict(ticker.fast_info or {}))
    except YFinanceOperationError as wrapped:
        if error_handler(code, wrapped.cause, "Yahoo fast_info"):
            return fast_info, frame, ticker
    if rate_limit_status()["active"]:
        return fast_info, frame, ticker
    try:
        history_frame = _call_yfinance(lambda: ticker.history(period="2mo", interval="1d", timeout=15))
        if not getattr(history_frame, "empty", True):
            frame = history_frame
    except YFinanceOperationError as wrapped:
        error_handler(code, wrapped.cause, "Yahoo history enrichment")
    return fast_info, frame, ticker


def _yahoo_pe(
    code: str,
    ticker: Any,
    info_session: Any,
    *,
    yf_module: Any,
    error_handler: YahooErrorHandler,
) -> tuple[float | None, str, bool]:
    try:
        info_ticker = ticker if ticker is not None else yf_module.Ticker(code, session=info_session)
        info = _call_yfinance(lambda: info_ticker.info)
        trailing_pe = normalize_pe_value(info.get("trailingPE"))
        if trailing_pe is not None:
            return trailing_pe, "trailing", True
        forward_pe = normalize_pe_value(info.get("forwardPE"))
        if forward_pe is not None:
            return forward_pe, "forward", True
        return None, "", True
    except YFinanceOperationError as wrapped:
        error_handler(code, wrapped.cause, "PE fetch")
        return None, "", False


def refresh_pe_if_needed(
    code: str,
    *,
    ticker: Any,
    info_session: Any,
    pe_value: Any,
    pe_source: str,
    pe_updated_at: float,
    allow_optional_network: bool = True,
    yf_module: Any = yf,
    rate_limit_status: RateLimitStatusGetter = get_yf_rate_limit_status,
    quote_refresh_time: Callable[[str], bool] | None = None,
    fallback_fetcher: PeFetcher = fetch_asian_pe_fallback,
    error_handler: YahooErrorHandler = handle_optional_yahoo_error,
    now: Callable[[], float] = time.time,
) -> tuple[Any, str, float]:
    now_ts = now()
    if (now_ts - pe_updated_at) < PE_REFRESH_INTERVAL_SEC:
        return pe_value, pe_source, pe_updated_at
    market = MarketCalendar.normalize_market(MarketCalendar.infer_market(code))
    is_quote_time = quote_refresh_time or MarketCalendar.is_quote_refresh_time
    if is_quote_time(market) or not allow_optional_network:
        return pe_value, pe_source, pe_updated_at
    yahoo_allowed = not rate_limit_status()["active"]
    if yahoo_allowed:
        yahoo_pe, yahoo_source, attempted = _yahoo_pe(
            code,
            ticker,
            info_session,
            yf_module=yf_module,
            error_handler=error_handler,
        )
        if yahoo_pe is not None:
            return yahoo_pe, yahoo_source, now()
        if attempted:
            pe_value, pe_source, pe_updated_at = None, "", now()
    fallback_pe, fallback_source = fallback_fetcher(code, info_session)
    if fallback_pe is not None:
        return fallback_pe, fallback_source, now()
    return pe_value, pe_source, now()


def _fetch_quote_sources(
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
    fast_info = {}
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


def _daily_ohlc(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
) -> tuple[float, float, float, float] | None:
    close_price = resolve_daily_field(
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        quote_key="close",
        fast_info_key="lastPrice",
        history_column="Close",
    )
    if close_price is None or close_price <= 0:
        return None
    open_price = resolve_daily_field(
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
    high_price = resolve_daily_field(
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
    low_price = resolve_daily_field(
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


def _price_snapshot(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
) -> QuotePayload | None:
    daily_ohlc = _daily_ohlc(realtime_quote, fast_info, frame)
    if daily_ohlc is None:
        return None
    close_price, open_price, high_price, low_price = daily_ohlc
    previous_close = resolve_previous_close_value(
        close_price=close_price,
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        cached_payload=cached_payload,
    )
    return {
        "date": resolve_quote_date(realtime_quote, frame),
        "close": close_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "previous_close": previous_close,
        "pct": ((close_price / previous_close) - 1.0) * 100.0 if previous_close > 0 else 0.0,
    }


def _complete_payload(
    snapshot: Mapping[str, Any],
    *,
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
    pe_value: Any,
    pe_source: str,
    pe_updated_at: float,
) -> QuotePayload:
    close_price = snapshot["close"]
    return {
        **snapshot,
        "volume": to_float((realtime_quote or {}).get("volume")) or 0.0,
        "pct_5": past_pct_from_history(close_price, frame, cached_payload, 5),
        "pct_10": past_pct_from_history(close_price, frame, cached_payload, 10),
        "pct_20": past_pct_from_history(close_price, frame, cached_payload, 20),
        "currency": (realtime_quote or {}).get("currency") or fast_info.get("currency", "USD"),
        "pe": pe_value,
        "pe_source": pe_source,
        "pe_updated_at": pe_updated_at,
        "source": (realtime_quote or {}).get("source", "yfinance"),
        "quote_quality": (realtime_quote or {}).get("quote_quality", ""),
        "df_today": frame,
    }


def fetch_normalized_asian_quote(
    code: str,
    *,
    yf_session: Any,
    info_session: Any,
    cached_payload: Mapping[str, Any],
    allow_optional_network: bool,
    realtime_fetcher: QuoteFetcher = fetch_asian_realtime_quote,
    enrichment_fetcher: Callable[..., tuple[dict[str, Any], Any, Any]] = fetch_yahoo_enrichment,
    pe_refresher: Callable[..., tuple[Any, str, float]] = refresh_pe_if_needed,
    error_handler: YahooErrorHandler = handle_optional_yahoo_error,
) -> QuotePayload | None:
    realtime_quote, fast_info, frame, ticker = _fetch_quote_sources(
        code,
        yf_session,
        allow_optional_network=allow_optional_network,
        realtime_fetcher=realtime_fetcher,
        enrichment_fetcher=enrichment_fetcher,
        error_handler=error_handler,
    )
    snapshot = _price_snapshot(realtime_quote, fast_info, frame, cached_payload)
    if snapshot is None:
        return None
    pe_value = cached_payload.get("pe")
    pe_source = cached_payload.get("pe_source", "")
    pe_updated_at = float(cached_payload.get("pe_updated_at", 0.0) or 0.0)
    pe_value, pe_source, pe_updated_at = pe_refresher(
        code,
        ticker=ticker,
        info_session=info_session,
        pe_value=pe_value,
        pe_source=pe_source,
        pe_updated_at=pe_updated_at,
        allow_optional_network=allow_optional_network,
    )
    return _complete_payload(
        snapshot,
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        cached_payload=cached_payload,
        pe_value=pe_value,
        pe_source=pe_source,
        pe_updated_at=pe_updated_at,
    )


__all__ = [
    "AsianRealtimePayloadError",
    "build_yf_session",
    "fetch_asian_pe_fallback",
    "fetch_asian_realtime_quote",
    "fetch_hk_realtime_quote",
    "fetch_jp_kabutan_pe",
    "fetch_jp_realtime_quote",
    "fetch_jp_yahoo_pe",
    "fetch_kr_naver_pe",
    "fetch_kr_realtime_quote",
    "fetch_normalized_asian_quote",
    "fetch_tpex_pe",
    "fetch_tw_realtime_quote",
    "fetch_twse_pe",
    "fetch_yahoo_enrichment",
    "format_cooldown_eta",
    "get_yf_rate_limit_status",
    "handle_optional_yahoo_error",
    "is_yf_rate_limit_error",
    "mark_yf_rate_limited",
    "normalize_pe_value",
    "parse_jp_realtime_page",
    "parse_jp_yahoo_pe_from_html",
    "refresh_pe_if_needed",
    "requests_module",
    "resolve_daily_field",
    "resolve_previous_close",
    "round_pct",
    "to_float",
    "yf",
]
