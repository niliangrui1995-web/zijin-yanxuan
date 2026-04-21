# -*- coding: utf-8 -*-
from __future__ import annotations

from core.logger import get_logger
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

    def refresh_all_tabs_after_f5(self) -> None:
        for tab in self.iter_refreshable_tabs():
            try:
                tab.refresh_table_from_latest_snapshot()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} 表格快照回灌失败: {exc}")
