# -*- coding: utf-8 -*-
"""Application-service exports loaded on first use.

Keep this package initializer side-effect free: UI modules import many narrow
``app.services.*`` modules during cold startup, and an eager barrel here used
to pull market-data providers into every such import.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.asian_market_service import (
        build_yf_session,
        fetch_single_kline,
        filter_asian_tickers,
        find_asian_track,
        get_yf_rate_limit_status,
        is_yf_rate_limit_error,
        mark_yf_rate_limited,
        sync_asian_kline_cache,
    )
    from app.services.kline_open_service import build_kline_open_request
    from app.services.runtime_constants import APP_VERSION, CACHE_DIR, FINANCE_CACHE_FILE, RPS_CACHE_FILE
    from app.services.runtime_services import (
        create_data_provider,
        create_startup_orchestrator,
        load_local_tdx_capital_snapshot,
    )
    from app.services.scan_runtime_service import (
        batch_check_market_cap,
        batch_get_finance_info,
        build_rps_matrix,
        calculate_scan_indicators,
        create_scan_engine,
        precompute_ready_pool,
        quick_check_breakout,
    )
    from app.services.sector_runtime_service import get_sector_manager
    from vcp.models import VCPParams

__all__ = [
    "APP_VERSION",
    "CACHE_DIR",
    "FINANCE_CACHE_FILE",
    "RPS_CACHE_FILE",
    "VCPParams",
    "batch_check_market_cap",
    "batch_get_finance_info",
    "build_kline_open_request",
    "build_rps_matrix",
    "build_yf_session",
    "calculate_scan_indicators",
    "create_data_provider",
    "create_scan_engine",
    "create_startup_orchestrator",
    "fetch_single_kline",
    "filter_asian_tickers",
    "find_asian_track",
    "get_sector_manager",
    "get_yf_rate_limit_status",
    "is_yf_rate_limit_error",
    "load_local_tdx_capital_snapshot",
    "mark_yf_rate_limited",
    "precompute_ready_pool",
    "quick_check_breakout",
    "sync_asian_kline_cache",
]

_EXPORTS = {
    "APP_VERSION": ("app.services.runtime_constants", "APP_VERSION"),
    "CACHE_DIR": ("app.services.runtime_constants", "CACHE_DIR"),
    "FINANCE_CACHE_FILE": ("app.services.runtime_constants", "FINANCE_CACHE_FILE"),
    "RPS_CACHE_FILE": ("app.services.runtime_constants", "RPS_CACHE_FILE"),
    "VCPParams": ("vcp.models", "VCPParams"),
    "batch_check_market_cap": ("app.services.scan_runtime_service", "batch_check_market_cap"),
    "batch_get_finance_info": ("app.services.scan_runtime_service", "batch_get_finance_info"),
    "build_kline_open_request": ("app.services.kline_open_service", "build_kline_open_request"),
    "build_rps_matrix": ("app.services.scan_runtime_service", "build_rps_matrix"),
    "build_yf_session": ("app.services.asian_market_service", "build_yf_session"),
    "calculate_scan_indicators": ("app.services.scan_runtime_service", "calculate_scan_indicators"),
    "create_data_provider": ("app.services.runtime_services", "create_data_provider"),
    "create_scan_engine": ("app.services.scan_runtime_service", "create_scan_engine"),
    "create_startup_orchestrator": ("app.services.runtime_services", "create_startup_orchestrator"),
    "fetch_single_kline": ("app.services.asian_market_service", "fetch_single_kline"),
    "filter_asian_tickers": ("app.services.asian_market_service", "filter_asian_tickers"),
    "find_asian_track": ("app.services.asian_market_service", "find_asian_track"),
    "get_sector_manager": ("app.services.sector_runtime_service", "get_sector_manager"),
    "get_yf_rate_limit_status": ("app.services.asian_market_service", "get_yf_rate_limit_status"),
    "is_yf_rate_limit_error": ("app.services.asian_market_service", "is_yf_rate_limit_error"),
    "load_local_tdx_capital_snapshot": ("app.services.runtime_services", "load_local_tdx_capital_snapshot"),
    "mark_yf_rate_limited": ("app.services.asian_market_service", "mark_yf_rate_limited"),
    "precompute_ready_pool": ("app.services.scan_runtime_service", "precompute_ready_pool"),
    "quick_check_breakout": ("app.services.scan_runtime_service", "quick_check_breakout"),
    "sync_asian_kline_cache": ("app.services.asian_market_service", "sync_asian_kline_cache"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
