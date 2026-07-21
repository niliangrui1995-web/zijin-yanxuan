# -*- coding: utf-8 -*-
"""Direct Asian-market HTTP quote and valuation providers."""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from domains.market_calendar import MarketCalendar
from infra.market_data.asian_market_http import (
    ASIAN_MARKET_HTTP_HEADERS,
    ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    asian_market_get,
    asian_market_headers,
    is_http_success,
    response_status_code,
)
from infra.market_data.normalize.quote_normalizer import (
    as_mapping,
    first_float,
    first_mapping_item,
    first_positive,
    first_present,
    load_realtime_json,
    normalize_pe_value,
    normalize_trade_date,
    pe_result,
    pick_twse_price,
    positive_float,
    strip_html_text,
    text,
    ticker_base,
    ticker_suffix,
    to_float,
)
from infra.tasks.lifecycle import raise_if_cancelled

DEFAULT_HTTP_HEADERS = ASIAN_MARKET_HTTP_HEADERS
HTTP_TIMEOUT_SEC = ASIAN_MARKET_HTTP_TIMEOUT_SEC
HttpGetter = Callable[..., Any]


def fetch_tw_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
    http_get: HttpGetter = asian_market_get,
) -> dict[str, Any] | None:
    suffix = ticker_suffix(code)
    base_code = ticker_base(code)
    if not base_code:
        return None
    market_prefix = "otc" if suffix == "TWO" else "tse"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={market_prefix}_{base_code}.tw&json=1&delay=0"
    response = http_get(
        url,
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://mis.twse.com.tw/"},
        timeout=15,
        cancellation_token=cancellation_token,
    )
    data = load_realtime_json(response, source="twse_mis")
    info = first_mapping_item(data.get("msgArray"))
    if not info:
        return None
    previous_close = to_float(info.get("y"))
    close_price, quote_quality = pick_twse_price(info, previous_close)
    if close_price is None:
        return None
    open_price = first_positive(info.get("o"), previous_close, close_price)
    if open_price is None:
        return None
    result = {
        "date": normalize_trade_date(first_present(info.get("d"), info.get("^"))),
        "close": close_price,
        "open": open_price,
        "high": first_positive(info.get("h"), max(open_price, close_price)),
        "low": first_positive(info.get("l"), min(open_price, close_price)),
        "volume": first_float(info.get("v"), 0.0),
        "previous_close": previous_close,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": quote_quality,
    }
    raise_if_cancelled(cancellation_token)
    return result


def kr_previous_close(info: Mapping[str, Any], close_price: float) -> float | None:
    ratio = first_float(info.get("fluctuationsRatioRaw"), info.get("fluctuationsRatio"))
    difference = first_float(
        info.get("compareToPreviousClosePriceRaw"),
        info.get("compareToPreviousClosePrice"),
    )
    direction = as_mapping(info.get("compareToPreviousPrice"))
    direction_code = text(direction.get("code")).strip()
    if difference is not None:
        if ratio is not None and ratio != 0:
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


def fetch_kr_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
    http_get: HttpGetter = asian_market_get,
) -> dict[str, Any] | None:
    base_code = ticker_base(code)
    if not base_code:
        return None
    response = http_get(
        f"https://polling.finance.naver.com/api/realtime/domestic/stock/{base_code}",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
        cancellation_token=cancellation_token,
    )
    data = load_realtime_json(response, source="naver_realtime")
    info = first_mapping_item(data.get("datas"))
    if not info:
        return None
    close_price = first_positive(info.get("closePriceRaw"), info.get("closePrice"))
    if close_price is None:
        return None
    currency = as_mapping(info.get("currencyType"))
    result = {
        "date": normalize_trade_date(info.get("localTradedAt")),
        "close": close_price,
        "open": first_positive(info.get("openPriceRaw"), info.get("openPrice"), close_price),
        "high": first_positive(info.get("highPriceRaw"), info.get("highPrice"), close_price),
        "low": first_positive(info.get("lowPriceRaw"), info.get("lowPrice"), close_price),
        "volume": first_float(
            info.get("accumulatedTradingVolumeRaw"),
            info.get("accumulatedTradingVolume"),
            0.0,
        ),
        "previous_close": kr_previous_close(info, close_price),
        "currency": first_present(currency.get("code"), "KRW"),
        "source": "naver_realtime",
        "quote_quality": "last",
    }
    raise_if_cancelled(cancellation_token)
    return result


def decode_hk_response(response: Any) -> str:
    raw_content = getattr(response, "content", None)
    if raw_content is not None:
        return raw_content.decode("gb18030", errors="replace")
    return str(getattr(response, "text", "") or "")


def fetch_hk_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
    http_get: HttpGetter = asian_market_get,
) -> dict[str, Any] | None:
    base_code = ticker_base(code)
    if not base_code:
        return None
    quote_code = base_code.zfill(5) if base_code.isdigit() else base_code
    response = http_get(
        f"https://qt.gtimg.cn/q=hk{quote_code}",
        session=http_session,
        headers={**DEFAULT_HTTP_HEADERS, "Referer": "https://stockapp.finance.qq.com/"},
        timeout=15,
        cancellation_token=cancellation_token,
    )
    matched = re.search(r'v_hk\d+="([^"]*)"', decode_hk_response(response))
    if not matched:
        return None
    fields = matched.group(1).split("~")
    field = lambda index: fields[index] if index < len(fields) else ""  # noqa: E731
    close_price = first_positive(field(3), field(35))
    if close_price is None:
        return None
    previous_close = positive_float(field(4))
    open_price = first_positive(field(5), previous_close, close_price)
    if open_price is None:
        return None
    result = {
        "date": normalize_trade_date(field(30)),
        "close": close_price,
        "open": open_price,
        "high": first_positive(field(33), max(open_price, close_price)),
        "low": first_positive(field(34), min(open_price, close_price)),
        "volume": first_float(field(6), field(36), 0.0),
        "previous_close": previous_close,
        "currency": "HKD",
        "source": "tencent_hk",
        "quote_quality": "free_delayed",
    }
    raise_if_cancelled(cancellation_token)
    return result


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


def jp_preloaded_quote(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    stock_board = as_mapping(payload.get("mainStocksPriceBoard"))
    board = as_mapping(stock_board.get("priceBoard"))
    stock_detail = as_mapping(payload.get("mainStocksDetail"))
    detail = as_mapping(stock_detail.get("detail"))
    page_info = as_mapping(payload.get("pageInfo"))
    close_price = positive_float(board.get("price"))
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
        "date": first_present(quote_date, MarketCalendar.now("T").date().isoformat()),
        "close": close_price,
        "open": first_positive(detail.get("openPrice"), close_price),
        "high": first_positive(detail.get("highPrice"), close_price),
        "low": first_positive(detail.get("lowPrice"), close_price),
        "volume": first_float(detail.get("volume"), 0.0),
        "previous_close": to_float(detail.get("previousPrice")),
        "currency": "JPY",
        "source": "yj_finance_page",
        "quote_quality": "free_delayed",
    }


def parse_jp_preloaded_page(page_text: str) -> dict[str, Any] | None:
    prefix = "__PRELOADED_STATE__ = "
    start = page_text.find(prefix)
    if start < 0:
        return None
    end = page_text.find("</script>", start)
    if end < 0:
        return None
    payload = json.loads(page_text[start + len(prefix) : end].strip())
    return jp_preloaded_quote(payload)


def parse_jp_indicator_page(page_text: str) -> dict[str, Any] | None:
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
    return parse_jp_preloaded_page(page_text) or parse_jp_indicator_page(page_text)


def fetch_jp_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
    http_get: HttpGetter = asian_market_get,
) -> dict[str, Any] | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None
    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    response = http_get(
        url,
        session=http_session,
        headers=DEFAULT_HTTP_HEADERS,
        timeout=HTTP_TIMEOUT_SEC,
        cancellation_token=cancellation_token,
    )
    page_text = response.text
    quote = parse_jp_realtime_page(page_text)
    if quote:
        return quote
    if response_status_code(response) < 400 and "現在表示できません" not in page_text:
        return None
    retry_response = http_get(
        url,
        headers=asian_market_headers(
            {"Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6", "Referer": "https://finance.yahoo.co.jp/"}
        ),
        timeout=HTTP_TIMEOUT_SEC,
        cancellation_token=cancellation_token,
    )
    result = parse_jp_realtime_page(retry_response.text) if is_http_success(retry_response) else None
    raise_if_cancelled(cancellation_token)
    return result


def find_twse_pe(
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


def fetch_twse_pe(
    code: str,
    http_session: Any,
    *,
    http_get: HttpGetter = asian_market_get,
) -> tuple[float | None, str]:
    base_code = ticker_base(code)
    if not base_code:
        return None, ""
    response = http_get(
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
    return find_twse_pe(rows, code_index=code_index, pe_index=pe_index, base_code=base_code)


def fetch_tpex_pe(
    code: str,
    http_session: Any,
    *,
    http_get: HttpGetter = asian_market_get,
) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    response = http_get(
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


def fetch_kr_naver_pe(
    code: str,
    http_session: Any,
    *,
    http_get: HttpGetter = asian_market_get,
) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    response = http_get(
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


def fetch_jp_kabutan_pe(
    base_code: str,
    *,
    http_get: HttpGetter = asian_market_get,
) -> tuple[float | None, str]:
    response = http_get(
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
    http_get: HttpGetter = asian_market_get,
) -> tuple[float | None, str]:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None, ""
    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    headers = asian_market_headers(
        {"Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6", "Referer": "https://finance.yahoo.co.jp/"}
    )
    response = http_get(url, session=http_session, headers=headers, timeout=HTTP_TIMEOUT_SEC)
    page_text = response.text
    pe_value, source = parse_jp_yahoo_pe_from_html(page_text)
    if pe_value is not None:
        return pe_value, source
    error_page = "ご覧になろうとしているページは現在表示できません" in page_text
    if response_status_code(response) < 400 and not error_page:
        return kabutan_fetcher(base_code)
    retry_response = http_get(url, headers=headers, timeout=HTTP_TIMEOUT_SEC)
    if is_http_success(retry_response):
        pe_value, source = parse_jp_yahoo_pe_from_html(retry_response.text)
        if pe_value is not None:
            return pe_value, source
    return kabutan_fetcher(base_code)


__all__ = [
    "decode_hk_response",
    "extract_jp_indicator_value",
    "extract_jp_page_price",
    "fetch_hk_realtime_quote",
    "fetch_jp_kabutan_pe",
    "fetch_jp_realtime_quote",
    "fetch_jp_yahoo_pe",
    "fetch_kr_naver_pe",
    "fetch_kr_realtime_quote",
    "fetch_tpex_pe",
    "fetch_tw_realtime_quote",
    "fetch_twse_pe",
    "find_twse_pe",
    "jp_preloaded_quote",
    "kr_previous_close",
    "latest_normalized_date",
    "parse_jp_indicator_page",
    "parse_jp_preloaded_page",
    "parse_jp_realtime_page",
    "parse_jp_yahoo_pe_from_html",
]
