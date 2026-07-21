# -*- coding: utf-8 -*-
"""Source selection, cooldown, and fallback policy for Asian quotes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from core.logger import get_logger
from domains.market_calendar import MarketCalendar
from infra.market_data.asian_market_http import RequestException
from infra.market_data.normalize.quote_normalizer import AsianRealtimePayloadError
from infra.market_data.providers.yfinance_provider import YFinanceOperationError
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded, raise_if_cancelled
from infra.tasks.owner_lifecycle import invoke_with_cancellation

log = get_logger(__name__)

PE_REFRESH_INTERVAL_SEC = 12 * 60 * 60
QuotePayload = dict[str, Any]
QuoteFetcher = Callable[..., QuotePayload | None]
PeFetcher = Callable[..., tuple[float | None, str]]
RateLimitStatusGetter = Callable[[], Mapping[str, Any]]
RateLimitPredicate = Callable[[BaseException | None], bool]
RateLimitMarker = Callable[..., float]
YahooErrorHandler = Callable[[str, Exception, str], bool]


def format_cooldown_eta(seconds: float) -> str:
    remaining = max(1, int(round(float(seconds or 0.0))))
    if remaining >= 60:
        return f"{(remaining + 59) // 60} 分钟"
    return f"{remaining} 秒"


def dispatch_asian_pe_fallback(
    normalized_code: str,
    http_session: Any,
    *,
    fetchers: Mapping[str, Callable[..., tuple[float | None, str]]],
    rate_limit_status: RateLimitStatusGetter,
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
    rate_limit_status: RateLimitStatusGetter,
    twse_fetcher: PeFetcher,
    tpex_fetcher: PeFetcher,
    kr_fetcher: PeFetcher,
    jp_yahoo_fetcher: PeFetcher,
    jp_kabutan_fetcher: PeFetcher,
    dispatcher: Callable[..., tuple[float | None, str]] = dispatch_asian_pe_fallback,
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
        return dispatcher(
            normalized_code,
            http_session,
            fetchers=fetchers,
            rate_limit_status=rate_limit_status,
        )
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        RequestException,
        json.JSONDecodeError,
    ) as exc:
        log.debug("[AsianProvider] PE fallback failed %s: %s", normalized_code, exc)
        return None, ""


def direct_quote(
    normalized_code: str,
    session: Any,
    *,
    tw_fetcher: QuoteFetcher,
    hk_fetcher: QuoteFetcher,
    kr_fetcher: QuoteFetcher,
    jp_fetcher: QuoteFetcher,
    cancellation_token: Any = None,
) -> QuotePayload | None:
    suffix = normalized_code.split(".")[-1]
    fetcher = {
        "TW": tw_fetcher,
        "TWO": tw_fetcher,
        "HK": hk_fetcher,
        "KS": kr_fetcher,
        "T": jp_fetcher,
    }.get(suffix)
    if fetcher is None:
        return None
    return invoke_with_cancellation(fetcher, cancellation_token, normalized_code, session)


def safe_direct_quote(
    normalized_code: str,
    session: Any,
    cancellation_token: Any = None,
    *,
    direct_fetcher: Callable[..., QuotePayload | None] = direct_quote,
    **fetchers: QuoteFetcher,
) -> QuotePayload | None:
    try:
        return direct_fetcher(
            normalized_code,
            session,
            cancellation_token=cancellation_token,
            **fetchers,
        )
    except AsianRealtimePayloadError:
        raise
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log.debug("[AsianProvider] direct realtime source failed %s: %s", normalized_code, exc)
        return None


def safe_yfinance_quote(
    normalized_code: str,
    session: Any,
    *,
    yf_module: Any,
    yf_fetcher: QuoteFetcher,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
    call_yfinance: Callable[[Callable[[], Any]], Any],
    cancellation_token: Any = None,
) -> QuotePayload | None:
    try:
        return call_yfinance(
            lambda: invoke_with_cancellation(
                yf_fetcher,
                cancellation_token,
                normalized_code,
                session,
                yf_module=yf_module,
            )
        )
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


def _fetch_direct_candidate(
    normalized_code: str,
    session: Any,
    *,
    raise_on_source_payload_error: bool,
    cancellation_token: Any,
    safe_direct_fetcher: Callable[..., QuotePayload | None],
    **fetchers: QuoteFetcher,
) -> tuple[QuotePayload | None, bool]:
    try:
        return (
            safe_direct_fetcher(
                normalized_code,
                session,
                cancellation_token=cancellation_token,
                **fetchers,
            ),
            False,
        )
    except AsianRealtimePayloadError as exc:
        if raise_on_source_payload_error:
            raise
        log.debug("[AsianProvider] unusable direct payload %s: %s", normalized_code, exc)
        return None, True


def _select_quote_or_yfinance(
    normalized_code: str,
    session: Any,
    quote: QuotePayload | None,
    *,
    allow_yfinance_fallback: bool,
    cancellation_token: Any,
    yf_module: Any,
    rate_limit_status: RateLimitStatusGetter,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
    yf_fetcher: QuoteFetcher,
    safe_yfinance_fetcher: Callable[..., QuotePayload | None],
) -> QuotePayload | None:
    if quote or not allow_yfinance_fallback:
        raise_if_cancelled(cancellation_token)
        return quote
    status = rate_limit_status()
    if status["active"]:
        log.debug("[AsianProvider] skip yfinance %s: cooldown %s", normalized_code, status["remaining_sec"])
        return None
    return safe_yfinance_fetcher(
        normalized_code,
        session,
        yf_module=yf_module,
        yf_fetcher=yf_fetcher,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
        cancellation_token=cancellation_token,
    )


def fetch_asian_realtime_quote(
    code: str,
    *, yf_session: Any,
    allow_yfinance_fallback: bool,
    raise_on_source_payload_error: bool,
    cancellation_token: Any,
    yf_module: Any,
    rate_limit_status: RateLimitStatusGetter,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
    tw_fetcher: QuoteFetcher,
    hk_fetcher: QuoteFetcher,
    kr_fetcher: QuoteFetcher,
    jp_fetcher: QuoteFetcher,
    yf_fetcher: QuoteFetcher,
    session_factory: Callable[[], Any],
    safe_direct_fetcher: Callable[..., QuotePayload | None],
    safe_yfinance_fetcher: Callable[..., QuotePayload | None],
) -> QuotePayload | None:
    raise_if_cancelled(cancellation_token)
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code:
        return None
    session = yf_session or session_factory()
    quote, direct_payload_failed = _fetch_direct_candidate(
        normalized_code,
        session,
        raise_on_source_payload_error=raise_on_source_payload_error,
        cancellation_token=cancellation_token,
        safe_direct_fetcher=safe_direct_fetcher,
        tw_fetcher=tw_fetcher,
        hk_fetcher=hk_fetcher,
        kr_fetcher=kr_fetcher,
        jp_fetcher=jp_fetcher,
    )
    if direct_payload_failed:
        return None
    return _select_quote_or_yfinance(
        normalized_code,
        session,
        quote,
        allow_yfinance_fallback=allow_yfinance_fallback,
        cancellation_token=cancellation_token,
        yf_module=yf_module,
        rate_limit_status=rate_limit_status,
        rate_limit_error=rate_limit_error,
        mark_rate_limited=mark_rate_limited,
        yf_fetcher=yf_fetcher,
        safe_yfinance_fetcher=safe_yfinance_fetcher,
    )


def handle_optional_yahoo_error(
    code: str,
    exc: Exception,
    context: str,
    *,
    rate_limit_error: RateLimitPredicate,
    mark_rate_limited: RateLimitMarker,
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


def refresh_pe_if_needed(
    code: str,
    *,
    ticker: Any,
    info_session: Any,
    pe_value: Any,
    pe_source: str,
    pe_updated_at: float,
    allow_optional_network: bool,
    yf_module: Any,
    rate_limit_status: RateLimitStatusGetter,
    quote_refresh_time: Callable[[str], bool] | None,
    fallback_fetcher: PeFetcher,
    error_handler: YahooErrorHandler,
    yahoo_pe_fetcher: Callable[..., tuple[float | None, str, bool]],
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
        yahoo_pe, yahoo_source, attempted = yahoo_pe_fetcher(
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


__all__ = [
    "PE_REFRESH_INTERVAL_SEC",
    "direct_quote",
    "dispatch_asian_pe_fallback",
    "fetch_asian_pe_fallback",
    "fetch_asian_realtime_quote",
    "format_cooldown_eta",
    "handle_optional_yahoo_error",
    "refresh_pe_if_needed",
    "safe_direct_quote",
    "safe_yfinance_quote",
]
