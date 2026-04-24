# -*- coding: utf-8 -*-
"""Canonical domain/application event channels."""

from PyQt6.QtCore import QObject, pyqtSignal


class DomainEventBus(QObject):
    """领域/应用事件总线。

    承载状态变更、缓存同步、日志广播等跨层事件，不承载 UI 导航请求。
    """

    _instance = None

    sig_system_log = pyqtSignal(str, str)
    sig_network_status_changed = pyqtSignal(bool, str)
    sig_app_closing = pyqtSignal()

    sig_rt_quotes = pyqtSignal(object)
    sig_rt_quotes_refreshed = pyqtSignal(object)
    sig_vcp_watchlist_ready = pyqtSignal(object)

    sig_cache_bootstrap_ready = pyqtSignal()
    sig_cache_reload_completed = pyqtSignal()
    sig_earnings_updated = pyqtSignal()
    sig_asian_klines_ready = pyqtSignal()
    sig_na_daily_updated = pyqtSignal()
    sig_ai_industry_chain_updated = pyqtSignal()
    sig_block_trade_updated = pyqtSignal()
    sig_lhb_pool_updated = pyqtSignal()
    sig_scan_updated = pyqtSignal()
    sig_fund_holdings_updated = pyqtSignal()

    sig_watchlist_changed = pyqtSignal(str, str)

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance


domain_events = DomainEventBus()
