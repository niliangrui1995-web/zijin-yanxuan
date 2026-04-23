# -*- coding: utf-8 -*-
from __future__ import annotations

from core.logger import get_logger
from ui.workspaces.quote_universe_service import INFO_SOURCE_GROUP, QuoteUniverseService
from ui.workspaces.tab_capabilities import (
    PostF5DataRefreshCapability,
    RtMonitorControlCapability,
    ScanResultsCapability,
    TableCollectionCapability,
)
from ui.workspaces.watchlist_radar_service import WatchlistRadarService
from ui.workspaces.workspace_navigation_service import WorkspaceNavigationService
from ui.workspaces.workspace_table_service import WorkspaceTableService

log = get_logger(__name__)


class WorkspaceFacade:
    """ClassicWorkspace 的跨 Tab 聚合与编排门面。"""

    def __init__(self, workspace):
        self._workspace = workspace
        self._workspace_navigation_service = WorkspaceNavigationService(workspace)
        self._workspace_table_service = WorkspaceTableService(workspace)
        self._quote_universe_service = QuoteUniverseService(workspace)
        self._watchlist_radar_service = WatchlistRadarService(workspace)

    def _get_tab(self, key: str):
        get_tab = getattr(self._workspace, "get_tab", None)
        if not callable(get_tab):
            return None
        return get_tab(key)

    @staticmethod
    def _call_bool(tab, method_name: str, *args, **kwargs) -> bool:
        callback = getattr(tab, method_name, None)
        if not callable(callback):
            return False
        return bool(callback(*args, **kwargs))

    def nav_groups(self) -> list[str]:
        return self._workspace_navigation_service.nav_groups()

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return self._workspace_navigation_service.tab_indices_by_group()

    def get_scan_results(self) -> list[dict]:
        tab = self._get_tab("scan")
        if not isinstance(tab, ScanResultsCapability):
            return []
        return list(tab.get_scan_results() or [])

    def get_rt_table(self):
        tab = self._get_tab("rt_monitor")
        if isinstance(tab, TableCollectionCapability):
            tables = list(tab.iter_tables() or [])
            return tables[0] if tables else None
        return None

    def iter_tables(self) -> list:
        return self._workspace_table_service.iter_tables()

    def iter_refreshable_tabs(self) -> list:
        return self._workspace_table_service.iter_refreshable_tabs()

    def refresh_all_tabs_after_f5(self) -> None:
        self._workspace_table_service.refresh_all_tabs_after_f5()

    def refresh_information_sources_after_f5(self) -> dict[str, bool]:
        """F5 完成后触发情报源自身的数据刷新，不只回灌行情快照。"""
        tab_specs = getattr(self._workspace, "tab_specs", None)
        specs = list(tab_specs() or []) if callable(tab_specs) else []
        results: dict[str, bool] = {}
        for spec in specs:
            if str(spec.get("group", "")).strip() != INFO_SOURCE_GROUP:
                continue
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            tab = self._get_tab(key)
            if not isinstance(tab, PostF5DataRefreshCapability):
                continue
            try:
                results[key] = bool(tab.refresh_data_after_f5())
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                results[key] = False
                log.warning(f"[F5] {key} 情报源数据刷新失败: {exc}")
        return results

    def select_scan_row(self, index: int) -> bool:
        return self._workspace_navigation_service.select_scan_row(index)

    def is_rt_monitor_running(self) -> bool:
        tab = self._get_tab("rt_monitor")
        if not isinstance(tab, RtMonitorControlCapability):
            return False
        return bool(tab.is_rt_running())

    def toggle_rt_monitor(self) -> bool:
        tab = self._get_tab("rt_monitor")
        if not isinstance(tab, RtMonitorControlCapability):
            return False
        return bool(tab.toggle_rt_monitor())

    def run_incremental_scan(self) -> bool:
        return self._call_bool(self._get_tab("scan"), "run_incremental_scan")

    def open_scan_settings(self) -> bool:
        return self._call_bool(self._get_tab("scan"), "open_scan_settings")

    def refresh_lhb_history(self) -> bool:
        return self._call_bool(self._get_tab("lhb"), "refresh_history")

    def run_fund_holdings_sync(self) -> bool:
        return self._call_bool(self._get_tab("fund_holdings"), "run_full_sync")

    def run_fund_holdings_auto_sync_after_f5(self) -> bool:
        return self._call_bool(self._get_tab("fund_holdings"), "run_auto_sync_after_f5")

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        return self._workspace_navigation_service.select_code_row(code, preferred_tab_index)

    def get_realtime_quote_codes(self) -> set[str]:
        return self._quote_universe_service.collect_realtime_quote_codes()

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        return self._watchlist_radar_service.refresh_watchlist_names(code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        self._watchlist_radar_service.prime_watchlist_state()

    def run_post_online_refresh(self, task_manager) -> None:
        for key in ("na_daily", "foreign_block"):
            self._call_bool(self._get_tab(key), "run_post_online_refresh")

        self.schedule_watchlist_special_quotes(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        tab = self._get_tab("rt_monitor")
        if not isinstance(tab, RtMonitorControlCapability):
            return False
        if tab.is_rt_running():
            return False
        return bool(tab.toggle_rt_monitor(auto=True))

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return self._watchlist_radar_service.collect_radar_data()
