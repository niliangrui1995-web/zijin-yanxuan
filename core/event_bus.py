# -*- coding: utf-8 -*-
"""Legacy compatibility facade for the split event buses.

New code should import:
- ``core.domain_events.domain_events`` for application/domain events
- ``core.ui_signals.ui_signals`` for UI navigation/progress signals
"""

from core.domain_events import domain_events
from core.ui_signals import ui_signals


class GlobalEventBus:
    """兼容旧代码的聚合视图。"""

    def __init__(self):
        self.sig_system_log = domain_events.sig_system_log
        self.sig_network_status_changed = domain_events.sig_network_status_changed
        self.sig_app_closing = domain_events.sig_app_closing
        self.sig_rt_quotes = domain_events.sig_rt_quotes
        self.sig_rt_quotes_refreshed = domain_events.sig_rt_quotes_refreshed
        self.sig_vcp_watchlist_ready = domain_events.sig_vcp_watchlist_ready
        self.sig_cache_bootstrap_ready = domain_events.sig_cache_bootstrap_ready
        self.sig_cache_reload_completed = domain_events.sig_cache_reload_completed
        self.sig_earnings_updated = domain_events.sig_earnings_updated
        self.sig_asian_klines_ready = domain_events.sig_asian_klines_ready
        self.sig_na_daily_updated = domain_events.sig_na_daily_updated
        self.sig_block_trade_updated = domain_events.sig_block_trade_updated
        self.sig_lhb_pool_updated = domain_events.sig_lhb_pool_updated
        self.sig_watchlist_changed = domain_events.sig_watchlist_changed

        self.sig_task_progress = ui_signals.sig_task_progress
        self.sig_show_kline = ui_signals.sig_show_kline
        self.sig_show_kline_with_list = ui_signals.sig_show_kline_with_list


event_bus = GlobalEventBus()
