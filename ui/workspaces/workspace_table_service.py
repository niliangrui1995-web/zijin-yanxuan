# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import QObject

from core.logger import get_logger
from ui.components.frame_task_scheduler import FrameTaskScheduler
from ui.workspaces.tab_capabilities import SnapshotRefreshCapability, TableCollectionCapability

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
        return [
            tab
            for tab in self._iter_tabs()
            if isinstance(tab, SnapshotRefreshCapability)
        ]

    def _current_tab_widget(self):
        tabs = getattr(self._workspace, "tabs", None)
        current_widget = getattr(tabs, "currentWidget", None)
        if callable(current_widget):
            try:
                return current_widget()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return None
        return None

    def _ordered_refreshable_tabs(self) -> list:
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
        ]

    def refresh_all_tabs_after_f5(self) -> None:
        for tab in self._ordered_refreshable_tabs():
            try:
                tab.refresh_table_from_latest_snapshot()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} 表格快照回灌失败: {exc}")

    def refresh_all_tabs_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 0,
        frame_budget_ms: int = 6,
    ) -> bool:
        tabs = self._ordered_refreshable_tabs()
        if not tabs:
            if callable(on_finished):
                from PyQt6.QtCore import QTimer

                QTimer.singleShot(0, on_finished)
            return False

        existing = getattr(self._workspace, "_f5_refresh_scheduler", None)
        if existing is not None and getattr(existing, "is_running", lambda: False)():
            existing.cancel()

        tasks = [
            (
                tab.__class__.__name__,
                lambda tab=tab: tab.refresh_table_from_latest_snapshot(),
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
        scheduler.taskFailed.connect(
            lambda label, message: log.warning(f"[F5] {label} 分帧刷新失败: {message}")
        )

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
