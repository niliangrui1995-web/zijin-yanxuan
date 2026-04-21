# -*- coding: utf-8 -*-
"""Bootstrap helpers for wiring the main window shell to workspace services."""

from __future__ import annotations

from core.app_config import app_config
from core.logger import get_logger
from ui.main_window_tables import install_table_copy_hooks
from ui.workspaces import ClassicWorkspace
from ui.workers.central_quotes_worker import CentralQuotesService

log = get_logger(__name__)


class ApplicationBootstrap:
    def __init__(self, main_window):
        self._window = main_window

    def workspace_tables(self):
        workspace = getattr(self._window, "_workspace", None)
        if workspace is None:
            return []
        iter_tables = getattr(workspace, "iter_tables", None)
        return iter_tables() if callable(iter_tables) else []

    def mount_workspace(self):
        workspace = ClassicWorkspace(
            self._window.data_provider,
            self._window.engine,
            host=self._window,
            parent=self._window.tabs_wrapper,
        )

        existing_workspace = getattr(self._window, "_workspace", None)
        if existing_workspace is not None:
            try:
                existing_workspace.shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError) as exc:
                log.error(f"[UI] 停止旧工作区失败: {exc}")
            self._window._tabs_wrapper_layout.removeWidget(existing_workspace)
            existing_workspace.deleteLater()

        self._window._workspace = workspace
        self._window.tabs = workspace.tabs
        self._window._tabs_wrapper_layout.addWidget(workspace, 1)
        workspace.restore_last_tab(app_config.last_active_tab)
        install_table_copy_hooks(self.workspace_tables())

        try:
            self._window.tabs.currentChanged.disconnect(self._window._remember_last_active_tab)
        except (TypeError, RuntimeError):
            pass
        self._window.tabs.currentChanged.connect(self._window._remember_last_active_tab)
        return workspace

    def install_central_quotes(self):
        code_supplier = getattr(getattr(self._window, "_workspace", None), "get_realtime_quote_codes", None)
        self._window.central_quotes_svc = CentralQuotesService(
            self._window,
            self._window.data_provider,
            code_supplier=code_supplier,
        )
        return self._window.central_quotes_svc

