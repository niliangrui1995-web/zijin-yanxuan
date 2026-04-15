# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ui.tabs.asian_market_tab import AsianMarketTab
from ui.tabs.earnings_tab import EarningsTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.lhb_tab import LhbTab
from ui.tabs.log_tab import LogTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.scan_tab import ScanTab
from ui.tabs.watchlist_tab import WatchlistTab


class ClassicWorkspace(QWidget):
    mode = "classic"

    def __init__(self, data_provider, engine, host=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self.engine = engine
        self.host = host

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)

        self.tab_watchlist = WatchlistTab(self.data_provider, self)
        self.tab_lhb = LhbTab(self.data_provider, self)
        self.tab_na_daily = NADailyTab(self.data_provider, self)
        self.tab_asian_market = AsianMarketTab(self.data_provider, self)
        self.tab_rt = RtMonitorTab(self.data_provider, self.engine, self)
        self.tab_foreign_block = ForeignBlockTradeTab(self.data_provider, self)
        self.tab_earnings = EarningsTab(self.data_provider, self)
        self.tab_scan = ScanTab(self.data_provider, self.engine, self)
        self.tab_log = LogTab(self)

        self.tabs.addTab(self.tab_watchlist, "关注池")
        self.tabs.addTab(self.tab_lhb, "龙虎榜")
        self.tabs.addTab(self.tab_na_daily, "美股日报")
        self.tabs.addTab(self.tab_asian_market, "亚洲寡头")
        self.tabs.addTab(self.tab_rt, "盘中监控")
        self.tabs.addTab(self.tab_foreign_block, "大宗交易")
        self.tabs.addTab(self.tab_earnings, "业绩异动")
        self.tabs.addTab(self.tab_scan, "VCP扫描")
        self.tabs.addTab(self.tab_log, "系统日志")

    def restore_last_tab(self, index: int):
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def current_tab_index(self) -> int:
        return self.tabs.currentIndex()

    def open_security_detail(self, code: str, context=None):
        return None

    def shutdown(self):
        rt_worker = getattr(self.tab_rt, "rt_worker", None)
        if rt_worker is not None and rt_worker.isRunning():
            self.tab_rt._manual_stop_requested = False
            self.tab_rt._rt_stop_requested = True
            self.tab_rt._toggle_rt_monitor(auto=True)
            rt_worker.wait(2000)

        scan_worker = getattr(self.tab_scan, "worker", None)
        if scan_worker is not None and scan_worker.isRunning():
            self.tab_scan.cancel_scan()
            scan_worker.wait(2000)

        asian_auto_timer = getattr(self.tab_asian_market, "auto_cache_timer", None)
        if asian_auto_timer is not None:
            asian_auto_timer.stop()

        asian_cache_thread = getattr(self.tab_asian_market, "cache_thread", None)
        if asian_cache_thread is not None and asian_cache_thread.isRunning():
            asian_cache_thread.wait(2000)

        asian_worker = getattr(self.tab_asian_market, "worker", None)
        if asian_worker is not None and asian_worker.isRunning():
            asian_worker.stop()
            asian_worker.wait(2000)

        auto_timer = getattr(self.tab_rt, "_auto_timer", None)
        if auto_timer is not None:
            auto_timer.stop()

        log_flush_timer = getattr(self.tab_log, "_log_flush_timer", None)
        if log_flush_timer is not None:
            log_flush_timer.stop()
