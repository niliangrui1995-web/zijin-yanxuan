# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt
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
                "group": "情报源",
                "group_order": 20,
                "attr": "tab_lhb",
                "widget": LhbTab(self.data_provider, self, autoload_pool=False),
            },
            {
                "key": "foreign_block",
                "title": "大宗交易",
                "group": "情报源",
                "group_order": 30,
                "attr": "tab_foreign_block",
                "widget": ForeignBlockTradeTab(self.data_provider, self),
            },
            {
                "key": "earnings",
                "title": "业绩异动",
                "group": "情报源",
                "group_order": 40,
                "attr": "tab_earnings",
                "widget": EarningsTab(self.data_provider, self),
            },
            {
                "key": "fund_holdings",
                "title": "基金持仓",
                "group": "情报源",
                "group_order": 50,
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
        groups: list[str] = []
        for spec in self._tab_specs:
            group = str(spec.get("group", "")).strip()
            if group and group not in groups:
                groups.append(group)
        return groups

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for index, spec in enumerate(self._tab_specs):
            group = str(spec.get("group", "")).strip()
            result.setdefault(group, []).append(index)
        for group, indices in result.items():
            result[group] = sorted(
                indices,
                key=lambda idx: (
                    int(self._tab_specs[idx].get("group_order", idx) or idx),
                    idx,
                ),
            )
        return result

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
        scan_tab = self.get_tab("scan")
        get_scan_results = getattr(scan_tab, "get_scan_results", None)
        if not callable(get_scan_results):
            return []
        return list(get_scan_results() or [])

    def get_rt_table(self):
        return getattr(self.get_tab("rt_monitor"), "table_rt", None)

    def iter_tables(self) -> list:
        tables = []
        for tab in self.iter_tabs():
            tables.extend(self._iter_tab_tables(tab))
        return tables

    def iter_refreshable_tabs(self) -> list:
        return [
            tab
            for tab in self.iter_tabs()
            if tab is not None and hasattr(tab, "refresh_table_from_latest_snapshot")
        ]

    def refresh_all_tabs_after_f5(self) -> None:
        for tab in self.iter_refreshable_tabs():
            try:
                tab.refresh_table_from_latest_snapshot()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} 表格快照回灌失败: {exc}")

    def select_scan_row(self, index: int) -> bool:
        table = getattr(self.get_tab("scan"), "table_scan", None)
        if table is None or index < 0:
            return False
        try:
            table.selectRow(index)
        except (AttributeError, RuntimeError, TypeError):
            return False
        return True

    def is_rt_monitor_running(self) -> bool:
        rt_tab = self.get_tab("rt_monitor")
        is_running = getattr(rt_tab, "is_rt_running", None)
        if not callable(is_running):
            return False
        return bool(is_running())

    def toggle_rt_monitor(self) -> bool:
        rt_tab = self.get_tab("rt_monitor")
        toggle_monitor = getattr(rt_tab, "toggle_rt_monitor", None)
        if not callable(toggle_monitor):
            return False
        toggle_monitor()
        return True

    def run_incremental_scan(self) -> bool:
        scan_tab = self.get_tab("scan")
        run_incremental_scan = getattr(scan_tab, "run_incremental_scan", None)
        if not callable(run_incremental_scan):
            return False
        return bool(run_incremental_scan())

    def open_scan_settings(self) -> bool:
        scan_tab = self.get_tab("scan")
        open_scan_settings = getattr(scan_tab, "open_scan_settings", None)
        if not callable(open_scan_settings):
            return False
        return bool(open_scan_settings())

    def refresh_lhb_history(self) -> bool:
        lhb_tab = self.get_tab("lhb")
        refresh_history = getattr(lhb_tab, "refresh_history", None)
        if not callable(refresh_history):
            return False
        return bool(refresh_history())

    def run_fund_holdings_sync(self) -> bool:
        fund_holdings_tab = self.get_tab("fund_holdings")
        run_full_sync = getattr(fund_holdings_tab, "run_full_sync", None)
        if not callable(run_full_sync):
            return False
        return bool(run_full_sync())

    @staticmethod
    def _iter_tab_tables(tab) -> list:
        tables = []
        for attr_name in ("table_sp", "table_scan", "table_rt", "na_daily_table", "asian_table", "table"):
            table = getattr(tab, attr_name, None)
            if table is not None and hasattr(table, "model") and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _find_code_column(model) -> int:
        if model is None:
            return -1
        try:
            column_count = int(model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1

        for column in range(column_count):
            try:
                header_text = str(
                    model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
                ).strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                header_text = ""
            if header_text == "代码":
                return column
        return -1

    @classmethod
    def _select_code_in_tab(cls, tab, code: str) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        for table in cls._iter_tab_tables(tab):
            model = table.model()
            code_column = cls._find_code_column(model)
            if code_column < 0:
                continue

            try:
                row_count = int(model.rowCount())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

            for row in range(row_count):
                try:
                    index = model.index(row, code_column)
                    row_code = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    continue
                if row_code != code_text:
                    continue

                try:
                    table.clearSelection()
                    table.setCurrentIndex(index)
                    table.selectRow(row)
                    table.scrollTo(index)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    return False
                return True

        return False

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        tab_widget = getattr(self, "tabs", None)
        if tab_widget is None:
            return False

        current_index = tab_widget.currentIndex()
        candidate_indices: list[int] = []

        if 0 <= current_index < tab_widget.count():
            candidate_indices.append(current_index)

        if isinstance(preferred_tab_index, int) and 0 <= preferred_tab_index < tab_widget.count():
            if preferred_tab_index not in candidate_indices:
                candidate_indices.append(preferred_tab_index)

        for tab_index in range(tab_widget.count()):
            if tab_index not in candidate_indices:
                candidate_indices.append(tab_index)

        for tab_index in candidate_indices:
            tab = tab_widget.widget(tab_index)
            if tab is None:
                continue
            if self._select_code_in_tab(tab, code_text):
                if tab_index != current_index:
                    tab_widget.setCurrentIndex(tab_index)
                return True

        return False

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
