# -*- coding: utf-8 -*-
"""Versioned compatibility facade for the stable Asian quote provider.

Public symbols remain source-compatible. Private compatibility reads are
retained through version 1.x, emit ``DeprecationWarning`` when accessed, and
are scheduled for removal in 2.0.0. Runtime injection must target the canonical
implementation modules named by the immutable compatibility contract.
"""

from __future__ import annotations

import warnings
from typing import Any

from core.logger import get_logger
from infra.market_data import asian_quote_provider as _implementation
from infra.market_data.asian_quote_provider import (
    LEGACY_PRIVATE_API_CONTRACT,
    LEGACY_PRIVATE_API_DEPRECATED_SINCE,
    LEGACY_PRIVATE_API_REMOVAL_VERSION,
    AsianRealtimePayloadError,
    build_yf_session,
    fetch_asian_pe_fallback,
    fetch_asian_realtime_quote,
    fetch_hk_realtime_quote,
    fetch_jp_kabutan_pe,
    fetch_jp_realtime_quote,
    fetch_jp_yahoo_pe,
    fetch_kr_naver_pe,
    fetch_kr_realtime_quote,
    fetch_normalized_asian_quote,
    fetch_tpex_pe,
    fetch_tw_realtime_quote,
    fetch_twse_pe,
    fetch_yahoo_enrichment,
    fetch_yfinance_realtime_quote,
    format_cooldown_eta,
    get_yf_rate_limit_status,
    handle_optional_yahoo_error,
    is_yf_rate_limit_error,
    legacy_private_api_contract,
    mark_yf_rate_limited,
    normalize_pe_value,
    parse_jp_realtime_page,
    parse_jp_yahoo_pe_from_html,
    refresh_pe_if_needed,
    requests_module,
    resolve_daily_field,
    resolve_previous_close,
    round_pct,
    to_float,
    yf,
)

log = get_logger(__name__)

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
__deprecated__ = True
__deprecated_since__ = _implementation.LEGACY_PRIVATE_API_DEPRECATED_SINCE
__removal_version__ = _implementation.LEGACY_PRIVATE_API_REMOVAL_VERSION


def _warn_private(name: str) -> None:
    if name not in _implementation.LEGACY_PRIVATE_API_CONTRACT:
        return
    warnings.warn(
        f"{__name__}.{name} is deprecated since {__deprecated_since__}; "
        f"use {_implementation.LEGACY_PRIVATE_API_CONTRACT[name]} before {__removal_version__}",
        DeprecationWarning,
        stacklevel=2,
    )


def __getattr__(name: str) -> Any:
    if name not in _implementation.LEGACY_PRIVATE_API_CONTRACT:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    _warn_private(name)
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__, *_implementation.LEGACY_PRIVATE_API_CONTRACT})
