from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from infra.diagnostics.runtime_health import (
    collect_runtime_health as _collect_runtime_health,
)
from infra.diagnostics.runtime_health import (
    export_runtime_health_report as _export_runtime_health_report,
)


def collect_runtime_health(main_window=None, *, kline_manager_instance: object | None = None) -> dict[str, Any]:
    return _collect_runtime_health(main_window, kline_manager_instance=kline_manager_instance)


def export_runtime_health_report(
    main_window=None,
    *,
    project_root: str | Path | None = None,
    report: dict[str, Any] | None = None,
    now: datetime | None = None,
    kline_manager_instance: object | None = None,
) -> Path:
    return _export_runtime_health_report(
        main_window,
        project_root=project_root,
        report=report,
        now=now,
        kline_manager_instance=kline_manager_instance,
    )

__all__ = ["collect_runtime_health", "export_runtime_health_report"]
