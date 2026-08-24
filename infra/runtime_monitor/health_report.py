"""Small, dependency-tolerant runtime health report."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from core.process_watchdog import collect_process_snapshot
from infra.runtime_monitor.monitor import RuntimeHealthMonitor, default_runtime_health_monitor


def _safe_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _task_health(manager: object | None) -> dict[str, Any]:
    try:
        if manager is None:
            raise AttributeError("task manager is unavailable")
        getter = getattr(manager, "runtime_health_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if not isinstance(snapshot, Mapping):
                raise TypeError("task runtime health snapshot must be a mapping")
            active_count = _safe_non_negative_int(snapshot.get("active_count"))
            failed_count = _safe_non_negative_int(snapshot.get("failed_count"))
        else:
            active_count = _safe_non_negative_int(getattr(manager, "active_count", None))
            failed_count = _safe_non_negative_int(getattr(manager, "failed_count", 0))
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "active_count": None,
            "failed_count": None,
            "diagnostic_error": exc.__class__.__name__,
        }
    return {
        "available": active_count is not None and failed_count is not None,
        "active_count": active_count,
        "failed_count": failed_count,
    }


def _kline_health(manager: object | None) -> dict[str, Any]:
    try:
        if manager is None:
            raise AttributeError("KLine manager is unavailable")
        getter = getattr(manager, "runtime_health_snapshot", None)
        if not callable(getter):
            raise AttributeError("KLine manager does not expose runtime_health_snapshot")
        snapshot = getter()
        if not isinstance(snapshot, Mapping):
            raise TypeError("KLine runtime health snapshot must be a mapping")
        browser_count = _safe_non_negative_int(snapshot.get("browser_count"))
        page_count = _safe_non_negative_int(snapshot.get("page_count"))
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "browser_count": None,
            "page_count": None,
            "diagnostic_error": exc.__class__.__name__,
        }
    return {
        "available": browser_count is not None and page_count is not None,
        "browser_count": browser_count,
        "page_count": page_count,
    }


def _memory_health(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    try:
        if not isinstance(snapshot, Mapping) or "rss_mb" not in snapshot:
            raise KeyError("process RSS is unavailable")
        raw_rss = snapshot.get("rss_mb")
        if isinstance(raw_rss, bool) or raw_rss is None or raw_rss == "":
            raise ValueError("process RSS must be a finite non-negative number")
        rss_mb = float(raw_rss)
        if not math.isfinite(rss_mb) or rss_mb < 0.0:
            raise ValueError("process RSS must be a finite non-negative number")
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "process_rss": None,
            "process_rss_unit": "bytes",
            "process_rss_mb": None,
            "diagnostic_error": exc.__class__.__name__,
        }
    return {
        "available": True,
        "process_pid": _safe_non_negative_int(snapshot.get("pid")),
        "process_rss": int(round(rss_mb * 1024 * 1024)),
        "process_rss_unit": "bytes",
        "process_rss_mb": rss_mb,
        "source": str(snapshot.get("source") or "unknown"),
    }


def runtime_health_report(
    *,
    task_manager_instance: object | None = None,
    kline_manager_instance: object | None = None,
    process_snapshot: Mapping[str, Any] | None = None,
    monitor: RuntimeHealthMonitor | None = None,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Return current Task, KLine, and process-memory health metrics.

    Optional instances make the collector usable in diagnostics and deterministic
    tests without changing the application-wide singleton interfaces.
    """
    if task_manager_instance is None:
        try:
            from infra.tasks.task_scheduler import task_manager

            task_manager_instance = task_manager
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            task_manager_instance = None
    if process_snapshot is None:
        try:
            process_snapshot = collect_process_snapshot()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            process_snapshot = {}

    report = {
        "schema_version": 2,
        "task": _task_health(task_manager_instance),
        "kline": _kline_health(kline_manager_instance),
        "memory": _memory_health(process_snapshot),
    }
    runtime_monitor = monitor if monitor is not None else default_runtime_health_monitor()
    report.update(runtime_monitor.observe(report, timestamp=timestamp))
    return report
