# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from core.logger import get_logger
from ui.tabs.asian_market_tab import AsianMarketTab
from ui.tabs.earnings_tab import EarningsTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.fund_holdings_tab import FundHoldingsTab
from ui.tabs.lhb_tab import LhbTab
from ui.tabs.log_tab import LogTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.scan_tab import ScanTab
from ui.tabs.watchlist_tab import WatchlistTab
from ui.workspaces.workspace_facade import WorkspaceFacade

log = get_logger(__name__)


def _resolve_workspace_facade(workspace) -> WorkspaceFacade:
    facade = getattr(workspace, "_workspace_facade", None)
    if facade is None:
        facade = WorkspaceFacade(workspace)
        setattr(workspace, "_workspace_facade", facade)
    return facade


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

        self._tab_specs = [
            {
                "key": "watchlist",
                "title": "关注池",
                "group": "主工作台",
                "group_order": 10,
                "attr": "tab_watchlist",
                "widget": WatchlistTab(self.data_provider, self),
            },
            {
                "key": "asian_market",
                "title": "亚洲寡头",
                "group": "主工作台",
                "group_order": 20,
                "attr": "tab_asian_market",
                "widget": AsianMarketTab(self.data_provider, self),
            },
            {
                "key": "na_daily",
                "title": "北美战报",
                "group": "主工作台",
                "group_order": 30,
                "attr": "tab_na_daily",
                "widget": NADailyTab(self.data_provider, self),
            },
            {
                "key": "rt_monitor",
                "title": "盘中监控",
                "group": "主工作台",
                "group_order": 40,
                "attr": "tab_rt",
                "widget": RtMonitorTab(self.data_provider, self.engine, self),
            },
            {
                "key": "scan",
                "title": "VCP扫描",
                "group": "情报源",
                "group_order": 10,
                "attr": "tab_scan",
                "widget": ScanTab(self.data_provider, self.engine, self),
            },
            {
                "key": "lhb",
                "title": "龙虎榜",
                "group": "主工作台",
                "group_order": 50,
                "attr": "tab_lhb",
                "widget": LhbTab(self.data_provider, self, autoload_pool=False),
            },
            {
                "key": "foreign_block",
                "title": "大宗交易",
                "group": "情报源",
                "group_order": 20,
                "attr": "tab_foreign_block",
                "widget": ForeignBlockTradeTab(self.data_provider, self),
            },
            {
                "key": "earnings",
                "title": "业绩异动",
                "group": "情报源",
                "group_order": 30,
                "attr": "tab_earnings",
                "widget": EarningsTab(self.data_provider, self),
            },
            {
                "key": "fund_holdings",
                "title": "基金持仓",
                "group": "情报源",
                "group_order": 40,
                "attr": "tab_fund_holdings",
                "widget": FundHoldingsTab(self.data_provider, self, autoload=False),
            },
            {
                "key": "system_log",
                "title": "系统日志",
                "group": "系统",
                "group_order": 10,
                "attr": "tab_log",
                "widget": LogTab(self),
            },
        ]

        for spec in self._tab_specs:
            setattr(self, spec["attr"], spec["widget"])
            self.tabs.addTab(spec["widget"], spec["title"])

        self._tabs_by_key = {str(spec["key"]): spec["widget"] for spec in self._tab_specs}
        self._workspace_facade = WorkspaceFacade(self)

    def tab_specs(self) -> list[dict]:
        return list(self._tab_specs)

    def nav_groups(self) -> list[str]:
        return _resolve_workspace_facade(self).nav_groups()

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return _resolve_workspace_facade(self).tab_indices_by_group()

    def restore_last_tab(self, index: int):
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def current_tab_index(self) -> int:
        return self.tabs.currentIndex()

    def get_tab(self, key: str):
        return self._tabs_by_key.get(str(key or "").strip())

    def iter_tabs(self) -> list:
        return [spec["widget"] for spec in self._tab_specs if spec.get("widget") is not None]

    def get_realtime_quote_codes(self) -> set[str]:
        return _resolve_workspace_facade(self).get_realtime_quote_codes()

    def get_scan_results(self) -> list[dict]:
        return _resolve_workspace_facade(self).get_scan_results()

    def get_rt_table(self):
        return _resolve_workspace_facade(self).get_rt_table()

    def iter_tables(self) -> list:
        return _resolve_workspace_facade(self).iter_tables()

    def iter_refreshable_tabs(self) -> list:
        return _resolve_workspace_facade(self).iter_refreshable_tabs()

    def refresh_all_tabs_after_f5(self) -> None:
        _resolve_workspace_facade(self).refresh_all_tabs_after_f5()

    def select_scan_row(self, index: int) -> bool:
        return _resolve_workspace_facade(self).select_scan_row(index)

    def is_rt_monitor_running(self) -> bool:
        return _resolve_workspace_facade(self).is_rt_monitor_running()

    def toggle_rt_monitor(self) -> bool:
        return _resolve_workspace_facade(self).toggle_rt_monitor()

    def run_incremental_scan(self) -> bool:
        return _resolve_workspace_facade(self).run_incremental_scan()

    def open_scan_settings(self) -> bool:
        return _resolve_workspace_facade(self).open_scan_settings()

    def refresh_lhb_history(self) -> bool:
        return _resolve_workspace_facade(self).refresh_lhb_history()

    def run_fund_holdings_sync(self) -> bool:
        return _resolve_workspace_facade(self).run_fund_holdings_sync()

    def run_fund_holdings_auto_sync_after_f5(self) -> bool:
        return _resolve_workspace_facade(self).run_fund_holdings_auto_sync_after_f5()

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        return _resolve_workspace_facade(self).select_code_row(code, preferred_tab_index)

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        return _resolve_workspace_facade(self).refresh_watchlist_names(code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        _resolve_workspace_facade(self).schedule_watchlist_special_quotes(task_manager)

    def run_post_online_refresh(self, task_manager) -> None:
        _resolve_workspace_facade(self).run_post_online_refresh(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        return _resolve_workspace_facade(self).auto_start_rt_monitor()

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return _resolve_workspace_facade(self).collect_watchlist_radar_data()

    def open_security_detail(self, code: str, context=None):
        return None

    def shutdown(self):
        for tab in self.iter_tabs():
            shutdown = getattr(tab, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] {tab.__class__.__name__} shutdown failed: {exc}")
