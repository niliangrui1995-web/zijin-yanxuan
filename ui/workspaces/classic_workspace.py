# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.logger import get_logger
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.tabs.ai_industry_chain_tab import AIIndustryChainTab
from ui.tabs.asian_market_tab import AsianMarketTab
from ui.tabs.earnings_tab import EarningsTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.fund_holdings_tab import FundHoldingsTab
from ui.tabs.lhb_tab import LhbTab
from ui.tabs.log_tab import LogTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.scan_tab import ScanTab
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.tabs.watchlist_tab import WatchlistTab
from ui.workspaces.stock_signal import StockSignal
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
        self._stock_detail_dialogs = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = SmoothTabWidget(self)
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
                "key": "stock_candidates",
                "title": "综合候选",
                "group": "主工作台",
                "group_order": 32,
                "attr": "tab_stock_candidates",
                "widget": StockCandidateTab(self.data_provider, self),
            },
            {
                "key": "ai_industry_chain",
                "title": "AI产业链",
                "group": "主工作台",
                "group_order": 35,
                "attr": "tab_ai_industry_chain",
                "widget": AIIndustryChainTab(self.data_provider, self),
            },
            {
                "key": "lhb",
                "title": "龙虎榜",
                "group": "主工作台",
                "group_order": 40,
                "attr": "tab_lhb",
                "widget": LhbTab(self.data_provider, self, autoload_pool=False),
            },
            {
                "key": "rt_monitor",
                "title": "盘中监控",
                "group": "主工作台",
                "group_order": 50,
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
        self.tabs.prewarm_pages()

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

    def refresh_information_sources_after_f5(self) -> dict[str, bool]:
        return _resolve_workspace_facade(self).refresh_information_sources_after_f5()

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

    def collect_stock_signals(self) -> list[StockSignal]:
        return _resolve_workspace_facade(self).collect_stock_signals()

    def collect_stock_context(self) -> dict[str, list[StockSignal]]:
        return _resolve_workspace_facade(self).collect_stock_context()

    def open_security_detail(self, code: str, context=None):
        code_text = str(code or "").strip()
        if not code_text:
            return False

        context = context if isinstance(context, dict) else {}
        name = str(context.get("name") or context.get("名称") or "").strip()
        if not name:
            code2name = getattr(self.data_provider, "code2name", {}) or {}
            name = str(code2name.get(code_text, "") or "").strip()

        tab_titles = {
            str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip()
            for spec in self.tab_specs()
        }
        signals = self.collect_stock_context().get(code_text, [])
        detail_context = context.get("vcp_data")
        detail_context = dict(detail_context) if isinstance(detail_context, dict) else {}

        from ui.components.stock_detail_dialog import StockDetailDialog

        detail_dialogs = getattr(self, "_stock_detail_dialogs", None)
        if detail_dialogs is None:
            detail_dialogs = {}
            setattr(self, "_stock_detail_dialogs", detail_dialogs)

        existing_dialog = detail_dialogs.get(code_text)
        if existing_dialog is not None:
            try:
                if existing_dialog.isVisible():
                    existing_dialog.raise_()
                    existing_dialog.activateWindow()
                    return True
            except RuntimeError:
                pass
            detail_dialogs.pop(code_text, None)

        dialog = StockDetailDialog(
            code_text,
            name,
            signals,
            tab_titles=tab_titles,
            activate_callback=self._activate_stock_signal_source,
            context=detail_context,
            parent=self.window(),
        )
        detail_dialogs[code_text] = dialog
        dialog.destroyed.connect(lambda _obj=None, key=code_text: detail_dialogs.pop(key, None))
        dialog.show()
        try:
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            pass
        return True

    def _tab_index_for_key(self, key: str) -> int:
        key_text = str(key or "").strip()
        if not key_text:
            return -1
        for index, spec in enumerate(self.tab_specs()):
            if str(spec.get("key") or "").strip() == key_text:
                return index
        return -1

    def _activate_stock_signal_source(self, signal: StockSignal) -> bool:
        code_text = signal.normalized_code()
        if not code_text:
            return False

        source_index = ClassicWorkspace._tab_index_for_key(self, signal.source_tab)
        if source_index >= 0:
            self.tabs.setCurrentIndex(source_index)
            tab = self.get_tab(signal.source_tab)
            select_code_row = getattr(tab, "select_code_row", None)
            if callable(select_code_row):
                return bool(select_code_row(code_text))

        return self.select_code_row(code_text, preferred_tab_index=source_index if source_index >= 0 else None)

    def shutdown(self):
        for tab in self.iter_tabs():
            shutdown = getattr(tab, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] {tab.__class__.__name__} shutdown failed: {exc}")
