# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer

from core.logger import get_logger
from ui.components.frame_task_scheduler import FrameTaskScheduler
from ui.workspaces.quote_universe_service import INFO_SOURCE_GROUP, QuoteUniverseService
from ui.workspaces.stock_context_service import StockContextService
from ui.workspaces.stock_signal import StockSignal
from ui.workspaces.tab_capabilities import (
    PostF5DataRefreshCapability,
    ScanResultsCapability,
)
from ui.workspaces.workspace_navigation_service import WorkspaceNavigationService
from ui.workspaces.workspace_table_service import WorkspaceTableService

log = get_logger(__name__)

_POST_F5_INFO_REFRESH_COOLDOWN_SECONDS = 5.0


def _shutdown_stock_context_facade(facade, *, timeout_ms: int = 750) -> bool:
    return facade._stock_context_service.shutdown(timeout_ms=timeout_ms)


class WorkspaceFacade:
    """ClassicWorkspace 的跨 Tab 聚合与编排门面。"""

    shutdown = _shutdown_stock_context_facade

    def __init__(self, workspace):
        self._workspace = workspace
        self._workspace_navigation_service = WorkspaceNavigationService(workspace)
        self._workspace_table_service = WorkspaceTableService(workspace)
        self._quote_universe_service = QuoteUniverseService(workspace)
        self._stock_context_service = StockContextService(workspace)

    def _get_tab(self, key: str):
        get_tab = getattr(self._workspace, "get_tab", None)
        if not callable(get_tab):
            return None
        return get_tab(key)

    def _get_loaded_tab(self, key: str):
        get_loaded_tab = getattr(self._workspace, "get_loaded_tab", None)
        if callable(get_loaded_tab):
            return get_loaded_tab(key)
        return self._get_tab(key)

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
        tab = self._get_loaded_tab("scan")
        if not isinstance(tab, ScanResultsCapability):
            return []
        return list(tab.get_scan_results() or [])

    def iter_tables(self) -> list:
        return self._workspace_table_service.iter_tables()

    def iter_refreshable_tabs(self) -> list:
        return self._workspace_table_service.iter_refreshable_tabs()

    def refresh_all_tabs_after_f5(self, *, skip_cache_reload_tabs: bool = False) -> None:
        self._stock_context_service.prepare_post_f5_refresh()
        self._workspace_table_service.refresh_all_tabs_after_f5(skip_cache_reload_tabs=skip_cache_reload_tabs)

    def refresh_all_tabs_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 0,
        skip_cache_reload_tabs: bool = False,
    ) -> bool:
        self._stock_context_service.prepare_post_f5_refresh()
        return self._workspace_table_service.refresh_all_tabs_after_f5_scheduled(
            on_finished=on_finished,
            interval_ms=interval_ms,
            skip_cache_reload_tabs=skip_cache_reload_tabs,
        )

    def refresh_tabs_after_ai_industry_chain_update(self) -> dict[str, bool]:
        return self._workspace_table_service.refresh_tabs_after_ai_industry_chain_update()

    def _iter_post_f5_information_source_tabs(self) -> list[tuple[str, PostF5DataRefreshCapability]]:
        tab_specs = getattr(self._workspace, "tab_specs", None)
        specs = list(tab_specs() or []) if callable(tab_specs) else []
        tabs: list[tuple[str, PostF5DataRefreshCapability]] = []
        for spec in specs:
            if str(spec.get("group", "")).strip() != INFO_SOURCE_GROUP:
                continue
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            tab = self._get_loaded_tab(key)
            if key != "scan" and self._is_noninteractive_loaded_tab(tab):
                continue
            if not isinstance(tab, PostF5DataRefreshCapability):
                continue
            tabs.append((key, tab))
        return tabs

    @staticmethod
    def _refresh_information_source_after_f5(key: str, tab: PostF5DataRefreshCapability) -> bool:
        try:
            return bool(tab.refresh_data_after_f5())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[F5] {key} information source refresh failed: {exc}")
            return False

    @staticmethod
    def _prepare_information_source_after_f5(key: str, tab: PostF5DataRefreshCapability) -> None:
        callback = getattr(tab, "prepare_post_f5_refresh", None)
        if not callable(callback):
            return
        try:
            callback()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[F5] {key} information source prepare failed: {exc}")

    def _is_post_f5_information_refresh_cooling_down(self) -> bool:
        last_started = getattr(self._workspace, "_f5_information_source_last_started_at", 0.0)
        try:
            return time.monotonic() - float(last_started or 0.0) < _POST_F5_INFO_REFRESH_COOLDOWN_SECONDS
        except (TypeError, ValueError):
            return False

    def _mark_post_f5_information_refresh_started(self) -> None:
        setattr(self._workspace, "_f5_information_source_last_started_at", time.monotonic())

    def refresh_information_sources_after_f5(self) -> dict[str, bool]:
        """F5 完成后触发情报源自身的数据刷新，不只回灌行情快照。"""
        if self._is_post_f5_information_refresh_cooling_down():
            return {}
        tabs = self._iter_post_f5_information_source_tabs()
        for key, tab in tabs:
            self._prepare_information_source_after_f5(key, tab)
        self._mark_post_f5_information_refresh_started()
        results: dict[str, bool] = {}
        for key, tab in tabs:
            results[key] = self._refresh_information_source_after_f5(key, tab)
        return results

    def refresh_information_sources_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 2500,
        frame_budget_ms: int = 4,
    ) -> bool:
        tabs = self._iter_post_f5_information_source_tabs()
        if not tabs:
            if callable(on_finished):
                QTimer.singleShot(0, on_finished)
            return False

        existing = getattr(self._workspace, "_f5_information_source_scheduler", None)
        if existing is not None and getattr(existing, "is_running", lambda: False)():
            return True
        if self._is_post_f5_information_refresh_cooling_down():
            if callable(on_finished):
                QTimer.singleShot(0, on_finished)
            return False

        tasks = [
            (
                key,
                lambda key=key, tab=tab: self._refresh_information_source_after_f5(key, tab),
            )
            for key, tab in tabs
        ]
        for key, tab in tabs:
            self._prepare_information_source_after_f5(key, tab)
        parent = self._workspace if isinstance(self._workspace, QObject) else None
        scheduler = FrameTaskScheduler(
            parent,
            interval_ms=interval_ms,
            frame_budget_ms=frame_budget_ms,
            max_tasks_per_frame=1,
        )
        scheduler.taskFailed.connect(
            lambda label, message: log.warning(f"[F5] {label} scheduled information source refresh failed: {message}")
        )

        def _cleanup():
            if getattr(self._workspace, "_f5_information_source_scheduler", None) is scheduler:
                setattr(self._workspace, "_f5_information_source_scheduler", None)
            try:
                if callable(on_finished):
                    on_finished()
            finally:
                scheduler.deleteLater()

        scheduler.finished.connect(_cleanup)
        setattr(self._workspace, "_f5_information_source_scheduler", scheduler)
        self._mark_post_f5_information_refresh_started()
        scheduler.start(tasks)
        return True

    @staticmethod
    def _is_noninteractive_loaded_tab(tab) -> bool:
        if tab is None:
            return False
        if bool(getattr(tab, "_workspace_noninteractive_loaded", False)):
            return True
        reason = str(getattr(tab, "_workspace_load_reason", "") or "").strip()
        return bool(reason and reason not in {"placeholder_action", "tab_switch", "user"})

    def select_scan_row(self, index: int) -> bool:
        return self._workspace_navigation_service.select_scan_row(index)

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
        return self._stock_context_service.refresh_watchlist_names(code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        self._stock_context_service.prime_watchlist_state()

    def run_post_online_refresh(self, task_manager) -> None:
        for key in ("na_daily",):
            self._call_bool(self._get_loaded_tab(key), "run_post_online_refresh")

        self.schedule_watchlist_special_quotes(task_manager)

    def collect_watchlist_radar_data(
        self,
        *,
        include_cache_fallback: bool = False,
        include_source_cache_fallback: bool | None = None,
        target_codes=None,
        allow_lhb_cache_compute: bool = False,
    ) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return self._stock_context_service.collect_watchlist_radar_data(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            target_codes=target_codes,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
        )

    def collect_stock_signals(self) -> list[StockSignal]:
        return self._stock_context_service.iter_stock_signals()

    def collect_stock_context(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
    ) -> dict[str, list[StockSignal]]:
        return self._stock_context_service.collect_signals_by_code(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
        )

    def prime_stock_context_snapshots(
        self,
        *,
        force: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> bool:
        return bool(
            self._stock_context_service.refresh_async_snapshots(
                force=force,
                include_fund=include_fund,
                include_lhb=include_lhb,
            )
        )
