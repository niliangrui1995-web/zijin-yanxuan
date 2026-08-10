# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import QObject

from app.services.ui_diagnostics_service import ui_stall_span
from core.logger import get_logger
from ui.components.frame_task_scheduler import FrameTaskScheduler
from ui.workspaces.tab_capabilities import (
    AIIndustryChainUpdateCapability,
    PostF5DataRefreshCapability,
    SnapshotRefreshCapability,
    TableCollectionCapability,
)
from ui.workspaces.tab_registry import TabF5SnapshotPolicy, get_tab_definition

log = get_logger(__name__)


class WorkspaceTableService:
    """Collects workspace table widgets and refreshable tabs."""

    def __init__(self, workspace):
        self._workspace = workspace

    def _iter_tabs(self) -> list:
        iter_tabs = getattr(self._workspace, "iter_tabs", None)
        return list(iter_tabs() or []) if callable(iter_tabs) else []

    @staticmethod
    def _iter_tab_tables(tab) -> list:
        if isinstance(tab, TableCollectionCapability):
            return list(tab.iter_tables() or [])
        return []

    def iter_tables(self) -> list:
        tables = []
        for tab in self._iter_tabs():
            tables.extend(self._iter_tab_tables(tab))
        return tables

    def iter_refreshable_tabs(self) -> list:
        return [tab for tab in self._iter_tabs() if isinstance(tab, SnapshotRefreshCapability)]

    def _current_tab_widget(self):
        tabs = getattr(self._workspace, "tabs", None)
        current_widget = getattr(tabs, "currentWidget", None)
        if callable(current_widget):
            try:
                return current_widget()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _explicit_f5_snapshot_policy(tab) -> TabF5SnapshotPolicy | None:
        definition = get_tab_definition(getattr(tab, "workspace_key", ""))
        return definition.f5_snapshot_policy if definition is not None else None

    @staticmethod
    def _uses_independent_f5_refresh(tab) -> bool:
        if isinstance(tab, PostF5DataRefreshCapability):
            return True
        return any(
            callable(getattr(tab, method_name, None))
            for method_name in (
                "_on_cache_reload_completed",
                "_schedule_context_refresh",
            )
        )

    @classmethod
    def _should_refresh_snapshot(cls, tab, *, skip_cache_reload_tabs: bool) -> bool:
        explicit_policy = cls._explicit_f5_snapshot_policy(tab)
        if explicit_policy is not None:
            if explicit_policy is TabF5SnapshotPolicy.NONE:
                return False
            return not skip_cache_reload_tabs or explicit_policy is TabF5SnapshotPolicy.SNAPSHOT
        if not skip_cache_reload_tabs:
            return True
        # Compatibility for lightweight/legacy workspace test doubles that do
        # not carry a registry key.
        return not cls._uses_independent_f5_refresh(tab)

    def _ordered_refreshable_tabs(self, *, skip_cache_reload_tabs: bool = False) -> list:
        current_widget = self._current_tab_widget()
        tabs = self.iter_refreshable_tabs()
        return [
            tab
            for _, tab in sorted(
                enumerate(tabs),
                key=lambda item: (
                    0 if item[1] is current_widget else 1,
                    0 if getattr(item[1], "isVisible", lambda: False)() else 1,
                    item[0],
                ),
            )
            if self._should_refresh_snapshot(tab, skip_cache_reload_tabs=skip_cache_reload_tabs)
        ]

    @staticmethod
    def _refresh_latest_snapshot_for_f5(tab) -> None:
        with ui_stall_span(
            "WorkspaceTableService._refresh_latest_snapshot_for_f5",
            tab=tab.__class__.__name__,
            signal="F5",
        ):
            try:
                setattr(tab, "_f5_cache_snapshot_apply", True)
                tab.refresh_table_from_latest_snapshot(async_local=True)
            except TypeError:
                tab.refresh_table_from_latest_snapshot()
            finally:
                try:
                    delattr(tab, "_f5_cache_snapshot_apply")
                except (AttributeError, RuntimeError, TypeError):
                    pass

    @staticmethod
    def _prepare_tab_for_f5(tab) -> None:
        callback = getattr(tab, "prepare_post_f5_refresh", None)
        if callable(callback):
            callback()

    def refresh_all_tabs_after_f5(self, *, skip_cache_reload_tabs: bool = False) -> None:
        tabs = self._ordered_refreshable_tabs(skip_cache_reload_tabs=skip_cache_reload_tabs)
        for tab in tabs:
            try:
                self._prepare_tab_for_f5(tab)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} prepare refresh failed: {exc}")
        for tab in tabs:
            try:
                self._refresh_latest_snapshot_for_f5(tab)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} 表格快照回灌失败: {exc}")

    def refresh_all_tabs_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 0,
        frame_budget_ms: int = 6,
        skip_cache_reload_tabs: bool = False,
    ) -> bool:
        tabs = self._ordered_refreshable_tabs(skip_cache_reload_tabs=skip_cache_reload_tabs)
        if not tabs:
            if callable(on_finished):
                from PyQt6.QtCore import QTimer

                QTimer.singleShot(0, on_finished)
            return False
        for tab in tabs:
            try:
                self._prepare_tab_for_f5(tab)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} prepare refresh failed: {exc}")

        existing = getattr(self._workspace, "_f5_refresh_scheduler", None)
        if existing is not None and getattr(existing, "is_running", lambda: False)():
            existing.cancel()

        tasks = [
            (
                tab.__class__.__name__,
                lambda tab=tab: self._refresh_latest_snapshot_for_f5(tab),
            )
            for tab in tabs
        ]
        parent = self._workspace if isinstance(self._workspace, QObject) else None
        scheduler = FrameTaskScheduler(
            parent,
            interval_ms=interval_ms,
            frame_budget_ms=frame_budget_ms,
            max_tasks_per_frame=1,
        )
        scheduler.taskFailed.connect(lambda label, message: log.warning(f"[F5] {label} 分帧刷新失败: {message}"))

        def _cleanup():
            if getattr(self._workspace, "_f5_refresh_scheduler", None) is scheduler:
                setattr(self._workspace, "_f5_refresh_scheduler", None)
            if callable(on_finished):
                on_finished()
            scheduler.deleteLater()

        scheduler.finished.connect(_cleanup)
        setattr(self._workspace, "_f5_refresh_scheduler", scheduler)
        scheduler.start(tasks)
        return True

    def replay_all_loaded_quote_snapshots_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 0,
        frame_budget_ms: int = 6,
    ) -> bool:
        """Replay the latest quote snapshot without reloading a tab's business data."""
        tabs = self._ordered_refreshable_tabs(skip_cache_reload_tabs=False)
        if not tabs:
            if callable(on_finished):
                from PyQt6.QtCore import QTimer

                QTimer.singleShot(0, on_finished)
            return False

        existing = getattr(self._workspace, "_f5_quote_replay_scheduler", None)
        if existing is not None and getattr(existing, "is_running", lambda: False)():
            existing.cancel()

        tasks = [
            (
                tab.__class__.__name__,
                lambda tab=tab: self._refresh_latest_snapshot_for_f5(tab),
            )
            for tab in tabs
        ]
        parent = self._workspace if isinstance(self._workspace, QObject) else None
        scheduler = FrameTaskScheduler(
            parent,
            interval_ms=interval_ms,
            frame_budget_ms=frame_budget_ms,
            max_tasks_per_frame=1,
        )
        scheduler.taskFailed.connect(lambda label, message: log.warning(f"[F5] {label} quote replay failed: {message}"))

        def _cleanup():
            if getattr(self._workspace, "_f5_quote_replay_scheduler", None) is scheduler:
                setattr(self._workspace, "_f5_quote_replay_scheduler", None)
            if callable(on_finished):
                on_finished()
            scheduler.deleteLater()

        scheduler.finished.connect(_cleanup)
        setattr(self._workspace, "_f5_quote_replay_scheduler", scheduler)
        scheduler.start(tasks)
        return True

    def refresh_tabs_after_ai_industry_chain_update(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for index, tab in enumerate(self._iter_tabs()):
            if not isinstance(tab, AIIndustryChainUpdateCapability):
                continue

            label = str(getattr(tab, "workspace_key", "") or tab.__class__.__name__)
            if label in results:
                label = f"{label}:{index}"
            try:
                results[label] = bool(tab.refresh_data_after_ai_industry_chain_update())
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                results[label] = False
                log.warning(f"[AI chain] {label} dependent refresh failed: {exc}")
        return results
