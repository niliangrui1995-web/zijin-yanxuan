# -*- coding: utf-8 -*-
"""Bootstrap helpers for wiring the main window shell to workspace services."""

from __future__ import annotations

from core.app_config import app_config
from core.logger import get_logger
from infra.features import service_toggle_registry

log = get_logger(__name__)


class ApplicationBootstrap:
    def __init__(self, main_window):
        self._window = main_window

    def _call_host(self, method_name: str, *args, **kwargs):
        callback = getattr(self._window, method_name, None)
        if not callable(callback):
            raise AttributeError(f"Main window is missing required bootstrap hook: {method_name}")
        return callback(*args, **kwargs)

    def workspace_tables(self):
        iter_workspace_tables = getattr(self._window, "iter_workspace_tables", None)
        if callable(iter_workspace_tables):
            return list(iter_workspace_tables() or [])
        workspace = getattr(self._window, "_workspace", None)
        if workspace is None:
            return []
        iter_tables = getattr(workspace, "iter_tables", None)
        return list(iter_tables() or []) if callable(iter_tables) else []

    def mount_workspace(self):
        workspace = self._call_host(
            "create_workspace",
            parent=getattr(self._window, "tabs_wrapper", None),
        )
        self._call_host("replace_workspace", workspace)
        return workspace

    def install_central_quotes(self):
        if not service_toggle_registry.is_enabled("central_quotes_service"):
            log.info("[UI] central_quotes_service disabled by toggle")
            self._window.central_quotes_svc = None
            return None

        code_supplier = getattr(getattr(self._window, "_workspace", None), "get_realtime_quote_codes", None)
        self._window.central_quotes_svc = self._call_host(
            "create_central_quotes_service",
            code_supplier=code_supplier,
        )
        return self._window.central_quotes_svc
