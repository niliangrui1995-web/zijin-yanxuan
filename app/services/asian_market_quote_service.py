"""Application facade for normalized Asian-market realtime quotes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from infra.market_data import asian_quote_provider as _provider

AsianRealtimePayloadError = _provider.AsianRealtimePayloadError
build_yf_session = _provider.build_yf_session
fetch_hk_realtime_quote = _provider.fetch_hk_realtime_quote
fetch_jp_kabutan_pe = _provider.fetch_jp_kabutan_pe
fetch_jp_realtime_quote = _provider.fetch_jp_realtime_quote
fetch_jp_yahoo_pe = _provider.fetch_jp_yahoo_pe
fetch_kr_naver_pe = _provider.fetch_kr_naver_pe
fetch_kr_realtime_quote = _provider.fetch_kr_realtime_quote
fetch_tpex_pe = _provider.fetch_tpex_pe
fetch_tw_realtime_quote = _provider.fetch_tw_realtime_quote
fetch_twse_pe = _provider.fetch_twse_pe
fetch_yfinance_realtime_quote = _provider.fetch_yfinance_realtime_quote
format_cooldown_eta = _provider.format_cooldown_eta
get_yf_rate_limit_status = _provider.get_yf_rate_limit_status
is_yf_rate_limit_error = _provider.is_yf_rate_limit_error
mark_yf_rate_limited = _provider.mark_yf_rate_limited
normalize_pe_value = _provider.normalize_pe_value
parse_jp_realtime_page = _provider.parse_jp_realtime_page
parse_jp_yahoo_pe_from_html = _provider.parse_jp_yahoo_pe_from_html
requests_module = _provider.requests_module
resolve_daily_field = _provider.resolve_daily_field
resolve_previous_close = _provider.resolve_previous_close
round_pct = _provider.round_pct
to_float = _provider.to_float
yf = _provider.yf
QuotePayload = dict[str, Any]
QuoteFetcher = Callable[..., QuotePayload | None]
PeFetcher = Callable[..., tuple[float | None, str]]
RateLimitStatusGetter = Callable[[], Mapping[str, Any]]
RateLimitPredicate = Callable[[BaseException | None], bool]
RateLimitMarker = Callable[..., float]
YahooErrorHandler = Callable[[str, Exception, str], bool]


class AsianRealtimeQuotePort(Protocol):
    def __call__(
        self,
        code: str,
        *,
        yf_session: Any = None,
        allow_yfinance_fallback: bool = True,
        raise_on_source_payload_error: bool = False,
        cancellation_token: Any = None,
    ) -> dict | None: ...


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
    yf_fetcher: QuoteFetcher = _provider.fetch_yfinance_realtime_quote,
) -> QuotePayload | None:
    return _provider.fetch_asian_realtime_quote(
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
    )


def fetch_asian_pe_fallback(
    code: str,
    http_session: Any,
    *,
    rate_limit_status: Callable[[], Mapping[str, Any]] = get_yf_rate_limit_status,
    twse_fetcher: PeFetcher = fetch_twse_pe,
    tpex_fetcher: PeFetcher = fetch_tpex_pe,
    kr_fetcher: PeFetcher = fetch_kr_naver_pe,
    jp_yahoo_fetcher: PeFetcher = fetch_jp_yahoo_pe,
    jp_kabutan_fetcher: PeFetcher = fetch_jp_kabutan_pe,
) -> tuple[float | None, str]:
    return _provider.fetch_asian_pe_fallback(
        code,
        http_session,
        rate_limit_status=rate_limit_status,
        twse_fetcher=twse_fetcher,
        tpex_fetcher=tpex_fetcher,
        kr_fetcher=kr_fetcher,
        jp_yahoo_fetcher=jp_yahoo_fetcher,
        jp_kabutan_fetcher=jp_kabutan_fetcher,
    )


def fetch_yahoo_enrichment(
    code: str,
    yf_session: Any,
    *,
    allow_network: bool = True,
    yf_module: Any = yf,
    rate_limit_status: RateLimitStatusGetter = get_yf_rate_limit_status,
    error_handler: YahooErrorHandler = _provider.handle_optional_yahoo_error,
) -> tuple[dict[str, Any], Any, Any]:
    return _provider.fetch_yahoo_enrichment(
        code,
        yf_session,
        allow_network=allow_network,
        yf_module=yf_module,
        rate_limit_status=rate_limit_status,
        error_handler=error_handler,
    )


def refresh_pe_if_needed(code: str, **kwargs: Any) -> tuple[Any, str, float]:
    return _provider.refresh_pe_if_needed(code, **kwargs)


def fetch_normalized_asian_quote(code: str, **kwargs: Any) -> QuotePayload | None:
    return _provider.fetch_normalized_asian_quote(code, **kwargs)


__all__ = [
    "AsianRealtimePayloadError",
    "AsianRealtimeQuotePort",
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
