# -*- coding: utf-8 -*-
"""Versioned compatibility facade for the stable Asian quote provider.

Public symbols remain source-compatible. Private injection hooks are retained
through version 1.x, emit ``DeprecationWarning`` when accessed, and are
scheduled for removal in 2.0.0. The immutable compatibility contract names the
supported replacement for each retained private symbol.
"""

from __future__ import annotations

import sys
import warnings
from types import ModuleType
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
from infra.market_data.normalize import quote_normalizer as _normalizer
from infra.market_data.providers import asian_http_provider as _asian_http
from infra.market_data.providers import yfinance_provider as _yfinance

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

_LEGACY_PRIVATE_PATCH_TARGETS: dict[str, tuple[tuple[ModuleType, str], ...]] = {
    "_text": ((_normalizer, "text"), (_asian_http, "text")),
    "_ticker_base": ((_normalizer, "ticker_base"), (_asian_http, "ticker_base")),
    "_ticker_suffix": ((_normalizer, "ticker_suffix"), (_asian_http, "ticker_suffix")),
    "_currency_for_ticker": (
        (_normalizer, "currency_for_ticker"),
        (_yfinance, "currency_for_ticker"),
    ),
    "_first_present": ((_normalizer, "first_present"), (_asian_http, "first_present")),
    "_first_float": (
        (_normalizer, "first_float"),
        (_asian_http, "first_float"),
        (_yfinance, "first_float"),
    ),
    "_positive_float": ((_normalizer, "positive_float"), (_asian_http, "positive_float")),
    "_first_positive": (
        (_normalizer, "first_positive"),
        (_asian_http, "first_positive"),
        (_yfinance, "first_positive"),
    ),
    "_first_mapping_item": (
        (_normalizer, "first_mapping_item"),
        (_asian_http, "first_mapping_item"),
    ),
    "_mapping": ((_normalizer, "as_mapping"), (_asian_http, "as_mapping")),
    "_kr_previous_close": ((_asian_http, "kr_previous_close"),),
    "_decode_hk_response": ((_asian_http, "decode_hk_response"),),
    "_jp_preloaded_quote": ((_asian_http, "jp_preloaded_quote"),),
    "_parse_jp_preloaded_page": ((_asian_http, "parse_jp_preloaded_page"),),
    "_parse_jp_indicator_page": ((_asian_http, "parse_jp_indicator_page"),),
    "_find_twse_pe": ((_asian_http, "find_twse_pe"),),
}


def _warn_private(name: str) -> None:
    if name not in _implementation.LEGACY_PRIVATE_API_CONTRACT:
        return
    warnings.warn(
        f"{__name__}.{name} is deprecated since {__deprecated_since__}; "
        f"use {_implementation.LEGACY_PRIVATE_API_CONTRACT[name]} before {__removal_version__}",
        DeprecationWarning,
        stacklevel=3,
    )


class _LegacyAsianQuoteFacade(ModuleType):
    """Delegate reads and writes so 1.x monkeypatch injection remains intact."""

    def __getattribute__(self, name: str) -> Any:
        if name in __all__ or name in _implementation.LEGACY_PRIVATE_API_CONTRACT:
            _warn_private(name)
            return getattr(_implementation, name)
        return super().__getattribute__(name)

    def __getattr__(self, name: str) -> Any:
        _warn_private(name)
        return getattr(_implementation, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("__") or name in {
            "_implementation",
            "_warn_private",
            "_LEGACY_PRIVATE_PATCH_TARGETS",
        }:
            super().__setattr__(name, value)
            return
        _warn_private(name)
        setattr(_implementation, name, value)
        for target_module, target_name in _LEGACY_PRIVATE_PATCH_TARGETS.get(name, ()):
            setattr(target_module, target_name, value)

    def __delattr__(self, name: str) -> None:
        if name.startswith("__") or name in {
            "_implementation",
            "_warn_private",
            "_LEGACY_PRIVATE_PATCH_TARGETS",
        }:
            super().__delattr__(name)
            return
        _warn_private(name)
        delattr(_implementation, name)
        for target_module, target_name in _LEGACY_PRIVATE_PATCH_TARGETS.get(name, ()):
            delattr(target_module, target_name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_implementation)))


sys.modules[__name__].__class__ = _LegacyAsianQuoteFacade
