# -*- coding: utf-8 -*-
"""Bootstrap helpers for wiring the main window shell to workspace services."""

from __future__ import annotations

import time
from typing import Any

from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from infra.features import service_toggle_registry

log = get_logger(__name__)


class ApplicationBootstrap:
    def __init__(self, main_window: Any) -> None:
        self._window = main_window

    def _call_host(self, method_name: str, *args, **kwargs) -> Any:
        callback = getattr(self._window, method_name, None)
        if not callable(callback):
            raise AttributeError(f"Main window is missing required bootstrap hook: {method_name}")
        return callback(*args, **kwargs)

    def workspace_tables(self) -> list[Any]:
        iter_workspace_tables = getattr(self._window, "iter_workspace_tables", None)
        if callable(iter_workspace_tables):
            return list(iter_workspace_tables() or [])
        current_workspace = getattr(self._window, "current_workspace", None)
        workspace = current_workspace() if callable(current_workspace) else None
        if workspace is None:
            return []
        iter_tables = getattr(workspace, "iter_tables", None)
        return list(iter_tables() or []) if callable(iter_tables) else []

    def mount_workspace(self) -> Any:
        started_at = time.perf_counter()
        workspace = self._call_host(
            "create_workspace",
            parent=getattr(self._window, "tabs_wrapper", None),
        )
        self._call_host("replace_workspace", workspace)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        record_metric(
            "workspace_mount_ms",
            elapsed_ms,
            unit="ms",
            tags={"workspace_mode": str(getattr(workspace, "mode", "unknown"))},
        )
        tab_specs = getattr(workspace, "tab_specs", None)
        tab_count = len(tab_specs()) if callable(tab_specs) else 0
        emit_structured_log(
            "workspace.mounted",
            workspace_mode=str(getattr(workspace, "mode", "unknown")),
            tab_count=tab_count,
            elapsed_ms=round(elapsed_ms, 3),
        )
        return workspace

    def install_central_quotes(self) -> Any:
        if not service_toggle_registry.is_enabled("central_quotes_service"):
            log.info("[UI] central_quotes_service disabled by toggle")
            self._window.central_quotes_svc = None
            return None

        code_supplier = getattr(self._window, "get_realtime_quote_codes", None)
        if not callable(code_supplier):
            code_supplier = None
        self._window.central_quotes_svc = self._call_host(
            "create_central_quotes_service",
            code_supplier=code_supplier,
        )
        return self._window.central_quotes_svc
