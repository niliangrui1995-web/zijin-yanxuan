# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer

from app.services.stock_context_model_service import (
    StockContextReadPolicy,
    StockContextSignalIndex,
    StockContextSnapshot,
    StockSignal,
)
from app.services.stock_context_query_service import (
    GENERAL_STOCK_CONTEXT_SOURCE_KEYS,
    RADAR_SOURCE_KEYS,
    StockContextQueryService,
)
from core.logger import get_logger
from ui.components.frame_task_scheduler import FrameTaskScheduler
from ui.workspaces.quote_universe_service import QuoteUniverseService
from ui.workspaces.stock_context_service import (
    StockContextService,
    capture_stock_context_snapshot,
    collect_watchlist_radar_snapshot,
)
from ui.workspaces.tab_capabilities import (
    PostF5DataRefreshCapability,
    ScanResultsCapability,
)
from ui.workspaces.tab_registry import INFO_SOURCE_GROUP, TabPostF5Policy, is_interactive_tab_load_reason
from ui.workspaces.workspace_navigation_service import WorkspaceNavigationService
from ui.workspaces.workspace_table_service import WorkspaceTableService

log = get_logger(__name__)

_POST_F5_INFO_REFRESH_COOLDOWN_SECONDS = 5.0


def _uses_post_f5_data_refresh(spec: dict) -> bool:
    if "post_f5_policy" in spec:
        return str(spec.get("post_f5_policy") or "").strip() == TabPostF5Policy.DATA_REFRESH.value
    return str(spec.get("group", "")).strip() == INFO_SOURCE_GROUP


def _cancel_workspace_scheduler(workspace, attribute: str) -> None:
    scheduler = getattr(workspace, attribute, None)
    if scheduler is None:
        return
    setattr(workspace, attribute, None)
    cancel = getattr(scheduler, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    delete_later = getattr(scheduler, "deleteLater", None)
    if callable(delete_later):
        try:
            delete_later()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def _shutdown_stock_context_facade(facade, *, timeout_ms: int = 750) -> bool:
    if facade._shutdown_started:
        return bool(facade._shutdown_result)
    facade._shutdown_started = True
    for attribute in ("_f5_refresh_scheduler", "_f5_information_source_scheduler"):
        _cancel_workspace_scheduler(facade._workspace, attribute)
    facade._shutdown_result = bool(facade._stock_context_service.shutdown(timeout_ms=timeout_ms))
    return facade._shutdown_result


def _capture_facade_stock_context(
    facade,
    *,
    include_rps_bundle: bool = True,
    sources=None,
) -> StockContextSnapshot:
    if sources is None:
        return (
            capture_stock_context_snapshot(facade._stock_context_service)
            if include_rps_bundle
            else capture_stock_context_snapshot(
                facade._stock_context_service,
                include_rps_bundle=False,
            )
        )
    return capture_stock_context_snapshot(
        facade._stock_context_service,
        include_rps_bundle=include_rps_bundle,
        sources=sources,
    )


def _publish_facade_stock_context_index(facade, index: StockContextSignalIndex) -> int:
    return facade._stock_context_service.publish_kline_signal_index(index)


def _published_facade_stock_context_signals(facade, code: str):
    return facade._stock_context_service.published_kline_signals(code)


def _prime_facade_stock_context(
    facade,
    *,
    force: bool = False,
    include_fund: bool = True,
    include_lhb: bool = True,
) -> bool:
    return bool(
        facade._stock_context_service.refresh_async_snapshots(
            force=force,
            include_fund=include_fund,
            include_lhb=include_lhb,
        )
    )


def _facade_stock_context_snapshots_settled(facade) -> bool:
    return bool(facade._stock_context_service.async_snapshots_settled())


def _cancel_facade_stock_context_snapshots(facade, *, reason: str) -> bool:
    return bool(facade._stock_context_service.cancel_async_snapshots(reason=reason))


class WorkspaceFacade:
    """ClassicWorkspace 的跨 Tab 聚合与编排门面。"""

    shutdown = _shutdown_stock_context_facade
    capture_stock_context_snapshot = _capture_facade_stock_context
    publish_stock_context_signal_index = _publish_facade_stock_context_index
    get_published_stock_context_signals = _published_facade_stock_context_signals
    prime_stock_context_snapshots = _prime_facade_stock_context
    stock_context_snapshots_settled = _facade_stock_context_snapshots_settled
    cancel_stock_context_snapshots = _cancel_facade_stock_context_snapshots

    def __init__(self, workspace):
        self._workspace = workspace
        self._shutdown_started = False
        self._shutdown_result = False
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
        return get_loaded_tab(key) if callable(get_loaded_tab) else None

    @staticmethod
    def _call_bool(tab, method_name: str, *args, **kwargs) -> bool:
        callback = getattr(tab, method_name, None)
        if not callable(callback):
            return False
        return bool(callback(*args, **kwargs))

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return self._workspace_navigation_service.tab_indices_by_group()

    def get_scan_results(self) -> list[dict]:
        tab = self._get_loaded_tab("scan")
        if not isinstance(tab, ScanResultsCapability):
            return []
        return list(tab.get_scan_results() or [])

    def iter_tables(self) -> list:
        return self._workspace_table_service.iter_tables()

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
            if not _uses_post_f5_data_refresh(spec):
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
        if bool(getattr(tab, "_workspace_background_preload_ready", False)):
            return False
        if bool(getattr(tab, "_workspace_noninteractive_loaded", False)):
            return True
        reason = str(getattr(tab, "_workspace_load_reason", "") or "").strip()
        return bool(reason and not is_interactive_tab_load_reason(reason))

    def run_incremental_scan(self) -> bool:
        return self._call_bool(self._get_tab("scan"), "run_incremental_scan")

    def open_scan_settings(self) -> bool:
        return self._call_bool(self._get_tab("scan"), "open_scan_settings")

    def refresh_lhb_history(self) -> bool:
        return self._call_bool(self._get_tab("lhb"), "refresh_history")

    def run_fund_holdings_sync(self) -> bool:
        return self._call_bool(self._get_tab("fund_holdings"), "run_full_sync")

    def run_fund_holdings_auto_sync_after_f5(self) -> bool:
        return self._call_bool(self._get_loaded_tab("fund_holdings"), "run_auto_sync_after_f5")

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        return self._workspace_navigation_service.select_code_row(code, preferred_tab_index)

    def get_realtime_quote_codes(self) -> set[str]:
        return self._quote_universe_service.collect_realtime_quote_codes()

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        return self._call_bool(self._get_loaded_tab("watchlist"), "refresh_watchlist_names", code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        self._call_bool(self._get_loaded_tab("watchlist"), "prime_startup_state")

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
        target_policy = StockContextReadPolicy.build(target_codes=target_codes)
        if target_codes is not None and not target_policy.target_codes:
            return collect_watchlist_radar_snapshot(StockContextSnapshot(), target_codes=target_codes)
        snapshot = self.capture_stock_context_snapshot(
            sources=RADAR_SOURCE_KEYS,
        )
        if "lhb" not in snapshot.loading_sources:
            self._stock_context_service.refresh_async_snapshots(include_fund=False, include_lhb=True)
            snapshot = self.capture_stock_context_snapshot(
                sources=RADAR_SOURCE_KEYS,
            )
        source_cache_fallback = (
            bool(include_cache_fallback)
            if include_source_cache_fallback is None
            else bool(include_source_cache_fallback)
        )
        return collect_watchlist_radar_snapshot(
            snapshot,
            include_source_cache_fallback=source_cache_fallback,
            target_codes=target_codes,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
        )

    def _stock_context_query(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
        target_codes=None,
        sources=None,
    ) -> tuple[StockContextQueryService, StockContextReadPolicy]:
        query_sources = GENERAL_STOCK_CONTEXT_SOURCE_KEYS if sources is None else sources
        policy = StockContextReadPolicy.build(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_fund_store_query=False,
            target_codes=target_codes,
            sources=query_sources,
        )
        if target_codes is not None and not policy.target_codes:
            return StockContextQueryService(StockContextSnapshot()), policy
        snapshot = self.capture_stock_context_snapshot()
        if allow_async_snapshot_refresh:
            source_keys = policy.sources or GENERAL_STOCK_CONTEXT_SOURCE_KEYS
            self._stock_context_service.refresh_async_snapshots(
                include_fund=(
                    "fund_holdings" in source_keys
                    and "fund_holdings" not in snapshot.loading_sources
                ),
                include_lhb="lhb" in source_keys and "lhb" not in snapshot.loading_sources,
            )
            snapshot = self.capture_stock_context_snapshot()
        return StockContextQueryService(snapshot), policy

    def collect_stock_context(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
        capture_snapshot: bool = False,
        include_rps_bundle: bool = True,
        target_codes=None,
        sources=None,
    ) -> dict[str, list[StockSignal]] | StockContextSnapshot:
        if capture_snapshot:
            if sources is None:
                return self.capture_stock_context_snapshot(
                    include_rps_bundle=include_rps_bundle,
                )
            return self.capture_stock_context_snapshot(
                include_rps_bundle=include_rps_bundle,
                sources=sources,
            )
        query, policy = self._stock_context_query(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
            target_codes=target_codes,
            sources=sources,
        )
        return query.query_by_code(policy)
