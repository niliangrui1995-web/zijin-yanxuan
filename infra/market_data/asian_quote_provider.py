# -*- coding: utf-8 -*-
"""Stable orchestration API for Asian-market realtime quote providers."""

from __future__ import annotations

import json as json
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, TypeVar

from domains.market_calendar import MarketCalendar as MarketCalendar
from infra.market_data.asian_market_http import (
    ASIAN_MARKET_HTTP_HEADERS,
    ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    asian_market_get,
    requests_module,
)
from infra.market_data.normalize import quote_normalizer as _normalizer
from infra.market_data.policies import fallback_policy as _fallback
from infra.market_data.providers import asian_http_provider as _asian_http
from infra.market_data.providers import yfinance_provider as _yfinance
from infra.market_data.yfinance_session import (
    build_yf_session,
    get_yf_rate_limit_status,
    is_yf_rate_limit_error,
    mark_yf_rate_limited,
)
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded

PE_REFRESH_INTERVAL_SEC = _fallback.PE_REFRESH_INTERVAL_SEC
EMPTY_NUMERIC_MARKERS = _normalizer.EMPTY_NUMERIC_MARKERS
NUMERIC_TOKEN_RE = _normalizer.NUMERIC_TOKEN_RE
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

LEGACY_PRIVATE_API_DEPRECATED_SINCE = "1.8.8"
LEGACY_PRIVATE_API_REMOVAL_VERSION = "2.0.0"
LEGACY_PRIVATE_API_CONTRACT: Mapping[str, str] = MappingProxyType(
    {
        "_asian_http": "infra.market_data.providers.asian_http_provider",
        "_yfinance": "infra.market_data.providers.yfinance_provider",
        "_normalizer": "infra.market_data.normalize.quote_normalizer",
        "_fallback": "infra.market_data.policies.fallback_policy",
        "_call_yfinance": "infra.market_data.providers.yfinance_provider",
        "_text": "infra.market_data.normalize.quote_normalizer.text",
        "_ticker_base": "infra.market_data.normalize.quote_normalizer.ticker_base",
        "_ticker_suffix": "infra.market_data.normalize.quote_normalizer.ticker_suffix",
        "_currency_for_ticker": "infra.market_data.normalize.quote_normalizer.currency_for_ticker",
        "_first_present": "infra.market_data.normalize.quote_normalizer.first_present",
        "_first_float": "infra.market_data.normalize.quote_normalizer.first_float",
        "_positive_float": "infra.market_data.normalize.quote_normalizer.positive_float",
        "_first_positive": "infra.market_data.normalize.quote_normalizer.first_positive",
        "_first_mapping_item": "infra.market_data.normalize.quote_normalizer.first_mapping_item",
        "_mapping": "infra.market_data.normalize.quote_normalizer.as_mapping",
        "_kr_previous_close": "infra.market_data.providers.asian_http_provider.kr_previous_close",
        "_decode_hk_response": "infra.market_data.providers.asian_http_provider.decode_hk_response",
        "_jp_preloaded_quote": "infra.market_data.providers.asian_http_provider.jp_preloaded_quote",
        "_parse_jp_preloaded_page": "infra.market_data.providers.asian_http_provider.parse_jp_preloaded_page",
        "_parse_jp_indicator_page": "infra.market_data.providers.asian_http_provider.parse_jp_indicator_page",
        "_find_twse_pe": "infra.market_data.providers.asian_http_provider.find_twse_pe",
        "_dispatch_asian_pe_fallback": "infra.market_data.policies.fallback_policy.dispatch_asian_pe_fallback",
        "_direct_quote": "infra.market_data.policies.fallback_policy.direct_quote",
        "_safe_direct_quote": "infra.market_data.policies.fallback_policy.safe_direct_quote",
        "_safe_yfinance_quote": "infra.market_data.policies.fallback_policy.safe_yfinance_quote",
        "_yahoo_pe": "infra.market_data.providers.yfinance_provider.yahoo_pe",
        "_fetch_quote_sources": "infra.market_data.normalize.quote_normalizer.fetch_quote_sources",
        "_daily_ohlc": "infra.market_data.normalize.quote_normalizer.daily_ohlc",
        "_price_snapshot": "infra.market_data.normalize.quote_normalizer.price_snapshot",
        "_complete_payload": "infra.market_data.normalize.quote_normalizer.complete_payload",
    }
)


def legacy_private_api_contract() -> Mapping[str, Any]:
    """Describe the retained 1.x private hooks and their supported replacements."""

    return MappingProxyType(
        {
            "deprecated_since": LEGACY_PRIVATE_API_DEPRECATED_SINCE,
            "removal_version": LEGACY_PRIVATE_API_REMOVAL_VERSION,
            "replacements": LEGACY_PRIVATE_API_CONTRACT,
        }
    )

_LazyYFinanceModule = _yfinance.LazyYFinanceModule
AsianRealtimePayloadError = _normalizer.AsianRealtimePayloadError
YFinanceOperationError = _yfinance.YFinanceOperationError
yf = _LazyYFinanceModule()


def _call_yfinance(operation: Callable[[], _T]) -> _T:
    """Isolate yfinance's intentionally open third-party exception surface."""

    try:
        return operation()
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except Exception as exc:
        raise YFinanceOperationError(exc) from exc


# Pure normalization aliases remain available for legacy imports and tests.
to_float = _normalizer.to_float
_text = _normalizer.text
_ticker_base = _normalizer.ticker_base
_ticker_suffix = _normalizer.ticker_suffix
_currency_for_ticker = _normalizer.currency_for_ticker
_first_present = _normalizer.first_present
_first_float = _normalizer.first_float
_positive_float = _normalizer.positive_float
_first_positive = _normalizer.first_positive
_first_mapping_item = _normalizer.first_mapping_item
_mapping = _normalizer.as_mapping
normalize_pe_value = _normalizer.normalize_pe_value
pe_result = _normalizer.pe_result
round_pct = _normalizer.round_pct
strip_html_text = _normalizer.strip_html_text
first_book_price = _normalizer.first_book_price
normalize_trade_date = _normalizer.normalize_trade_date
has_history_rows = _normalizer.has_history_rows
history_previous_close = _normalizer.history_previous_close
resolve_previous_close = _normalizer.resolve_previous_close
resolve_daily_field = _normalizer.resolve_daily_field
past_pct_from_history = _normalizer.past_pct_from_history
resolve_quote_date = _normalizer.resolve_quote_date
response_body_is_blank = _normalizer.response_body_is_blank
load_realtime_json = _normalizer.load_realtime_json
pick_twse_price = _normalizer.pick_twse_price


def resolve_previous_close_value(
    *,
    close_price: float,
    realtime_quote: dict[str, Any] | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
) -> float:
    return _normalizer.resolve_previous_close_value(
        close_price=close_price,
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        cached_payload=cached_payload,
        resolver=resolve_previous_close,
    )


def format_cooldown_eta(seconds: float) -> str:
    return _fallback.format_cooldown_eta(seconds)


def fetch_tw_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
) -> dict[str, Any] | None:
    return _asian_http.fetch_tw_realtime_quote(
        code,
        http_session,
        cancellation_token=cancellation_token,
        http_get=asian_market_get,
    )


_kr_previous_close = _asian_http.kr_previous_close


def fetch_kr_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
) -> dict[str, Any] | None:
    return _asian_http.fetch_kr_realtime_quote(
        code,
        http_session,
        cancellation_token=cancellation_token,
        http_get=asian_market_get,
    )


_decode_hk_response = _asian_http.decode_hk_response


def fetch_hk_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
) -> dict[str, Any] | None:
    return _asian_http.fetch_hk_realtime_quote(
        code,
        http_session,
        cancellation_token=cancellation_token,
        http_get=asian_market_get,
    )


extract_jp_page_price = _asian_http.extract_jp_page_price
extract_jp_indicator_value = _asian_http.extract_jp_indicator_value
latest_normalized_date = _asian_http.latest_normalized_date
_jp_preloaded_quote = _asian_http.jp_preloaded_quote
_parse_jp_preloaded_page = _asian_http.parse_jp_preloaded_page
_parse_jp_indicator_page = _asian_http.parse_jp_indicator_page
parse_jp_realtime_page = _asian_http.parse_jp_realtime_page


def fetch_jp_realtime_quote(
    code: str,
    http_session: Any,
    *,
    cancellation_token: Any = None,
) -> dict[str, Any] | None:
    return _asian_http.fetch_jp_realtime_quote(
        code,
        http_session,
        cancellation_token=cancellation_token,
        http_get=asian_market_get,
    )


def fetch_yfinance_realtime_quote(
    code: str,
    yf_session: Any,
    *,
    yf_module: Any = yf,
    cancellation_token: Any = None,
) -> dict[str, Any] | None:
    return _yfinance.fetch_yfinance_realtime_quote(
        code,
        yf_session,
        yf_module=yf_module,
        cancellation_token=cancellation_token,
        previous_close_resolver=resolve_previous_close,
        quote_date_resolver=resolve_quote_date,
    )


_find_twse_pe = _asian_http.find_twse_pe


def fetch_twse_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    return _asian_http.fetch_twse_pe(code, http_session, http_get=asian_market_get)


def fetch_tpex_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    return _asian_http.fetch_tpex_pe(code, http_session, http_get=asian_market_get)


def fetch_kr_naver_pe(code: str, http_session: Any) -> tuple[float | None, str]:
    return _asian_http.fetch_kr_naver_pe(code, http_session, http_get=asian_market_get)


parse_jp_yahoo_pe_from_html = _asian_http.parse_jp_yahoo_pe_from_html


def fetch_jp_kabutan_pe(base_code: str) -> tuple[float | None, str]:
    return _asian_http.fetch_jp_kabutan_pe(base_code, http_get=asian_market_get)


def fetch_jp_yahoo_pe(
    code: str,
    http_session: Any,
    *,
    kabutan_fetcher: Callable[[str], tuple[float | None, str]] = fetch_jp_kabutan_pe,
) -> tuple[float | None, str]:
    return _asian_http.fetch_jp_yahoo_pe(
        code,
        http_session,
        kabutan_fetcher=kabutan_fetcher,
        http_get=asian_market_get,
    )


def _dispatch_asian_pe_fallback(
    normalized_code: str,
    http_session: Any,
    *,
    fetchers: Mapping[str, Callable[..., tuple[float | None, str]]],
    rate_limit_status: Callable[[], Mapping[str, Any]],
) -> tuple[float | None, str]:
    return _fallback.dispatch_asian_pe_fallback(
        normalized_code,
        http_session,
        fetchers=fetchers,
        rate_limit_status=rate_limit_status,
    )


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
    return _fallback.fetch_asian_pe_fallback(
        code,
        http_session,
        rate_limit_status=rate_limit_status,
        twse_fetcher=twse_fetcher,
        tpex_fetcher=tpex_fetcher,
        kr_fetcher=kr_fetcher,
        jp_yahoo_fetcher=jp_yahoo_fetcher,
        jp_kabutan_fetcher=jp_kabutan_fetcher,
        dispatcher=_dispatch_asian_pe_fallback,
    )


def _direct_quote(
    normalized_code: str,
    session: Any,
    *,
    tw_fetcher: QuoteFetcher,
    hk_fetcher: QuoteFetcher,
    kr_fetcher: QuoteFetcher,
    jp_fetcher: QuoteFetcher,
    cancellation_token: Any = None,
) -> QuotePayload | None:
    return _fallback.direct_quote(
        normalized_code,
        session,
        tw_fetcher=tw_fetcher,
        hk_fetcher=hk_fetcher,
        kr_fetcher=kr_fetcher,
        jp_fetcher=jp_fetcher,
        cancellation_token=cancellation_token,
    )


def _safe_direct_quote(
    normalized_code: str,
    session: Any,
    cancellation_token: Any = None,
    **fetchers: QuoteFetcher,
) -> QuotePayload | None:
    return _fallback.safe_direct_quote(
        normalized_code,
        session,
        cancellation_token,
        direct_fetcher=_direct_quote,
        **fetchers,
    )


def _safe_yfinance_quote(
    normalized_code: str,
    session: Any,
    *,
    yf_module: Any,
    yf_fetcher: QuoteFetcher,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
    cancellation_token: Any = None,
) -> QuotePayload | None:
    return _fallback.safe_yfinance_quote(
        normalized_code,
        session,
        yf_module=yf_module,
        yf_fetcher=yf_fetcher,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
        call_yfinance=_call_yfinance,
        cancellation_token=cancellation_token,
    )


def fetch_asian_realtime_quote(
    code: str,
    *,
    yf_session: Any = None,
    allow_yfinance_fallback: bool = True,
    raise_on_source_payload_error: bool = False,
    cancellation_token: Any = None,
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
    return _fallback.fetch_asian_realtime_quote(
        code,
        yf_session=yf_session,
        allow_yfinance_fallback=allow_yfinance_fallback,
        raise_on_source_payload_error=raise_on_source_payload_error,
        cancellation_token=cancellation_token,
        yf_module=yf_module,
        rate_limit_status=rate_limit_status,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
        tw_fetcher=tw_fetcher,
        hk_fetcher=hk_fetcher,
        kr_fetcher=kr_fetcher,
        jp_fetcher=jp_fetcher,
        yf_fetcher=yf_fetcher,
        session_factory=build_yf_session,
        safe_direct_fetcher=_safe_direct_quote,
        safe_yfinance_fetcher=_safe_yfinance_quote,
    )


def handle_optional_yahoo_error(
    code: str,
    exc: Exception,
    context: str,
    *,
    rate_limit_error: RateLimitPredicate = is_yf_rate_limit_error,
    mark_rate_limited: RateLimitMarker = mark_yf_rate_limited,
) -> bool:
    return _fallback.handle_optional_yahoo_error(
        code,
        exc,
        context,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
    )


def fetch_yahoo_enrichment(
    code: str,
    yf_session: Any,
    *,
    allow_network: bool = True,
    yf_module: Any = yf,
    rate_limit_status: RateLimitStatusGetter = get_yf_rate_limit_status,
    error_handler: YahooErrorHandler = handle_optional_yahoo_error,
) -> tuple[dict[str, Any], Any, Any]:
    return _yfinance.fetch_yahoo_enrichment(
        code,
        yf_session,
        allow_network=allow_network,
        yf_module=yf_module,
        rate_limit_status=rate_limit_status,
        error_handler=error_handler,
        call_yfinance=_call_yfinance,
    )


def _yahoo_pe(
    code: str,
    ticker: Any,
    info_session: Any,
    *,
    yf_module: Any,
    error_handler: YahooErrorHandler,
) -> tuple[float | None, str, bool]:
    return _yfinance.yahoo_pe(
        code,
        ticker,
        info_session,
        yf_module=yf_module,
        error_handler=error_handler,
        call_yfinance=_call_yfinance,
    )


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
    return _fallback.refresh_pe_if_needed(
        code,
        ticker=ticker,
        info_session=info_session,
        pe_value=pe_value,
        pe_source=pe_source,
        pe_updated_at=pe_updated_at,
        allow_optional_network=allow_optional_network,
        yf_module=yf_module,
        rate_limit_status=rate_limit_status,
        quote_refresh_time=quote_refresh_time,
        fallback_fetcher=fallback_fetcher,
        error_handler=error_handler,
        yahoo_pe_fetcher=_yahoo_pe,
        now=now,
    )


def _fetch_quote_sources(
    code: str,
    yf_session: Any,
    *,
    allow_optional_network: bool,
    realtime_fetcher: QuoteFetcher,
    enrichment_fetcher: Callable[..., tuple[dict[str, Any], Any, Any]],
    error_handler: YahooErrorHandler,
) -> tuple[QuotePayload | None, dict[str, Any], Any, Any]:
    return _normalizer.fetch_quote_sources(
        code,
        yf_session,
        allow_optional_network=allow_optional_network,
        realtime_fetcher=realtime_fetcher,
        enrichment_fetcher=enrichment_fetcher,
        error_handler=error_handler,
    )


def _daily_ohlc(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
) -> tuple[float, float, float, float] | None:
    return _normalizer.daily_ohlc(
        realtime_quote,
        fast_info,
        frame,
        field_resolver=resolve_daily_field,
    )


def _price_snapshot(
    realtime_quote: QuotePayload | None,
    fast_info: Mapping[str, Any],
    frame: Any,
    cached_payload: Mapping[str, Any],
) -> QuotePayload | None:
    return _normalizer.price_snapshot(
        realtime_quote,
        fast_info,
        frame,
        cached_payload,
        ohlc_builder=_daily_ohlc,
        previous_close_resolver=resolve_previous_close_value,
        quote_date_resolver=resolve_quote_date,
    )


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
    return _normalizer.complete_payload(
        snapshot,
        realtime_quote=realtime_quote,
        fast_info=fast_info,
        frame=frame,
        cached_payload=cached_payload,
        pe_value=pe_value,
        pe_source=pe_source,
        pe_updated_at=pe_updated_at,
        float_parser=to_float,
        history_pct=past_pct_from_history,
    )


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
    "LEGACY_PRIVATE_API_CONTRACT",
    "LEGACY_PRIVATE_API_DEPRECATED_SINCE",
    "LEGACY_PRIVATE_API_REMOVAL_VERSION",
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
    "fetch_yfinance_realtime_quote",
    "fetch_yahoo_enrichment",
    "format_cooldown_eta",
    "get_yf_rate_limit_status",
    "handle_optional_yahoo_error",
    "is_yf_rate_limit_error",
    "legacy_private_api_contract",
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
