# -*- coding: utf-8 -*-
"""Domain layer exports with lazy loading to avoid import cycles."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "BreakoutMonitorService",
    "EarningsEngine",
    "EarningsScheduler",
    "FundHoldingsStore",
    "FundHoldingsSyncService",
    "IndicatorService",
    "MarketCalendar",
    "RpsService",
    "TaskCategory",
    "VcpScannerService",
    "WatchlistViewModel",
    "fund_holdings_store",
    "fund_holdings_sync_service",
    "publish_rt_quotes",
    "watchlist_vm",
]

_EXPORTS = {
    "BreakoutMonitorService": ("domains.scan", "BreakoutMonitorService"),
    "IndicatorService": ("domains.scan", "IndicatorService"),
    "RpsService": ("domains.scan", "RpsService"),
    "VcpScannerService": ("domains.scan", "VcpScannerService"),
    "EarningsEngine": ("domains.earnings", "EarningsEngine"),
    "EarningsScheduler": ("domains.earnings", "EarningsScheduler"),
    "FundHoldingsStore": ("domains.fund_holdings", "FundHoldingsStore"),
    "FundHoldingsSyncService": ("domains.fund_holdings", "FundHoldingsSyncService"),
    "fund_holdings_store": ("domains.fund_holdings", "fund_holdings_store"),
    "fund_holdings_sync_service": ("domains.fund_holdings", "fund_holdings_sync_service"),
    "MarketCalendar": ("domains.market_calendar", "MarketCalendar"),
    "publish_rt_quotes": ("domains.quotes", "publish_rt_quotes"),
    "TaskCategory": ("domains.runtime", "TaskCategory"),
    "WatchlistViewModel": ("domains.watchlist", "WatchlistViewModel"),
    "watchlist_vm": ("domains.watchlist", "watchlist_vm"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
