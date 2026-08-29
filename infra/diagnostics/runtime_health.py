from __future__ import annotations

import json
import os
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from core.f5_activation_gate import f5_snapshot_read_locked
from core.process_watchdog import collect_process_snapshot
from domains.runtime.fault_tolerance import provider_fault_tolerance
from infra.diagnostics.ui_stall_probe import get_ui_stall_probe
from infra.market_data.provider_ports import ProviderHealthPort, ProviderHealthSnapshot
from infra.runtime_monitor import runtime_health_report

try:  # pragma: no cover - psutil is optional outside the packaged runtime.
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

EVENT_SIGNAL_NAMES = (
    "sig_system_log",
    "sig_network_status_changed",
    "sig_app_closing",
    "sig_rt_quotes",
    "sig_vcp_watchlist_ready",
    "sig_cache_bootstrap_ready",
    "sig_cache_reload_completed",
    "sig_earnings_updated",
    "sig_asian_klines_ready",
    "sig_na_daily_updated",
    "sig_ai_industry_chain_updated",
    "sig_block_trade_updated",
    "sig_lhb_pool_updated",
    "sig_scan_updated",
    "sig_fund_holdings_updated",
    "sig_stock_context_snapshot_updated",
    "sig_watchlist_changed",
)

@lru_cache(maxsize=1)
def _default_lineage_snapshot() -> tuple[dict[str, dict[str, Any]], tuple[str, ...], dict[str, dict[str, Any]], frozenset[str]]:
    """Load UI-owned tab metadata only when a health report needs lineage."""
    from ui.workspaces.tab_registry import (
        STATIC_LINEAGE_FIELDS,
        lineage_exclusion_tab_definitions,
        lineage_tab_definitions,
    )

    key_view_lineage = {
        definition.key: definition.lineage.as_runtime_defaults(definition.key)
        for definition in lineage_tab_definitions()
        if definition.lineage is not None
    }
    excluded_tabs = {
        definition.key: definition.lineage_exclusion.as_runtime_defaults()
        for definition in lineage_exclusion_tab_definitions()
        if definition.lineage_exclusion is not None
    }
    return key_view_lineage, tuple(key_view_lineage), excluded_tabs, STATIC_LINEAGE_FIELDS


def _key_view_lineage() -> dict[str, dict[str, Any]]:
    return _default_lineage_snapshot()[0]


def _data_lineage_covered_tabs() -> tuple[str, ...]:
    return _default_lineage_snapshot()[1]


def _data_lineage_excluded_tabs() -> dict[str, dict[str, Any]]:
    return _default_lineage_snapshot()[2]


def _static_lineage_fields() -> frozenset[str]:
    return _default_lineage_snapshot()[3]


def __getattr__(name: str):
    values = {
        "KEY_VIEW_LINEAGE": _key_view_lineage,
        "DATA_LINEAGE_COVERED_TABS": _data_lineage_covered_tabs,
        "DATA_LINEAGE_EXCLUDED_TABS": _data_lineage_excluded_tabs,
        "STATIC_LINEAGE_FIELDS": _static_lineage_fields,
    }
    getter = values.get(name)
    if getter is None:
        raise AttributeError(name)
    return getter()


def _iso_from_timestamp(value: float | int | None) -> str:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _safe_len(value) -> int | None:
    try:
        return len(value)
    except TypeError:
        return None


def _safe_row_count(model) -> int | None:
    if model is None:
        return None
    try:
        return int(model.rowCount())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        rows = getattr(model, "row_data", None)
        return _safe_len(rows)


def _tab_row_count(tab) -> int | None:
    if tab is None:
        return None
    for attr_name in ("model", "source_model", "proxy_model"):
        count = _safe_row_count(getattr(tab, attr_name, None))
        if count is not None:
            return count
    table_getter = getattr(tab, "get_primary_table", None)
    table = cast(Any, table_getter() if callable(table_getter) else None)
    if table is not None:
        try:
            return _safe_row_count(table.model())
        except (AttributeError, RuntimeError, TypeError):
            return None
    return None


def _active_task_snapshot() -> dict[str, Any]:
    try:
        from core.background_job_runner import background_job_runner

        manager = background_job_runner._resolve_manager()
        health_getter = getattr(manager, "runtime_health_snapshot", None)
        if callable(health_getter):
            health = health_getter()
            if not isinstance(health, Mapping):
                raise TypeError("runtime health snapshot must be a mapping")
            task_ids = [str(task_id) for task_id in health.get("task_ids", ())]
            workers = [dict(worker) for worker in health.get("workers", ())]
            active_count = int(health.get("active_count", len(task_ids)))
            failed_count = int(health.get("failed_count", 0))
        else:
            active_workers = getattr(manager, "active_workers")
            if not isinstance(active_workers, dict):
                raise TypeError("active_workers must be a dictionary")
            task_ids = sorted(str(task_id) for task_id in active_workers)
            workers = [
                {
                    "task_id": str(task_id),
                    "worker_class": worker.__class__.__name__,
                    "cancelled": bool(
                        getattr(getattr(worker, "cancellation_token", None), "cancelled", False)
                    ),
                }
                for task_id, worker in sorted(active_workers.items(), key=lambda item: str(item[0]))
            ]
            active_count = len(task_ids)
            failed_count = int(getattr(manager, "failed_count", 0) or 0)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "count": None,
            "failed_count": None,
            "ids": [],
            "workers": [],
            "diagnostic_error": exc.__class__.__name__,
        }

    return {
        "available": True,
        "count": active_count,
        "failed_count": failed_count,
        "ids": task_ids,
        "workers": workers,
    }


def _timer_snapshot(root) -> dict[str, Any]:
    timers = []
    if root is not None:
        try:
            all_timers = list(root.findChildren(QTimer))
        except (AttributeError, RuntimeError, TypeError):
            all_timers = []
    else:
        all_timers = []

    for timer in all_timers:
        try:
            parent = timer.parent()
            timers.append(
                {
                    "object_name": str(timer.objectName() or ""),
                    "owner": parent.__class__.__name__ if parent is not None else "",
                    "active": bool(timer.isActive()),
                    "interval_ms": int(timer.interval()),
                    "single_shot": bool(timer.isSingleShot()),
                }
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue

    active = [timer for timer in timers if timer.get("active")]
    interval_counts = Counter(int(timer.get("interval_ms") or 0) for timer in active)
    key_intervals = [
        timer
        for timer in active
        if int(timer.get("interval_ms") or 0) in {0, 250, 500, 1000, 5000, 10000, 30000, 60000, 300000}
        or str(timer.get("object_name") or "").strip()
    ]
    return {
        "total": len(timers),
        "active": len(active),
        "active_interval_counts": {str(key): value for key, value in sorted(interval_counts.items())},
        "key_intervals": key_intervals[:40],
        "timers": timers[:160],
    }


def _event_bus_snapshot() -> dict[str, Any]:
    domain_events: Any
    try:
        from domains.runtime import domain_events
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        domain_events = None

    signals = {}
    total = 0
    if domain_events is not None:
        for name in EVENT_SIGNAL_NAMES:
            signal = getattr(domain_events, name, None)
            if signal is None:
                continue
            try:
                count = int(domain_events.receivers(signal))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                count = None
            signals[name] = count
            if count is not None:
                total += count
    return {
        "total_receivers": total,
        "signals": signals,
    }


def _mb(value: int | float | None) -> float:
    return round(float(value or 0) / 1024.0 / 1024.0, 1)


def _process_info(process, *, include_thread_count: bool = True) -> dict[str, Any] | None:
    if psutil is None:
        return None
    try:
        oneshot_factory = getattr(process, "oneshot", None)
        process_context = cast(Any, oneshot_factory() if callable(oneshot_factory) else nullcontext())
        with process_context:
            memory = process.memory_info()
            item = {
                "pid": process.pid,
                "name": process.name(),
                "rss_mb": _mb(getattr(memory, "rss", 0)),
                "vms_mb": _mb(getattr(memory, "vms", 0)),
            }
            if include_thread_count:
                item["thread_count"] = process.num_threads()
            private_value = getattr(memory, "private", None)
            if private_value is not None:
                item["private_mb"] = _mb(private_value)
            working_set = getattr(memory, "wset", None)
            if working_set is not None:
                item["working_set_mb"] = _mb(working_set)
            return item
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, psutil.Error):
        return None


def _webengine_snapshot(*, detailed: bool = True) -> dict[str, Any]:
    if psutil is None:
        return {
            "available": False,
            "count": None,
            "rss_mb": None,
            "private_mb": None,
            "processes": [],
        }
    try:
        process = psutil.Process(os.getpid())
        children = [
            _process_info(child, include_thread_count=detailed)
            for child in process.children(recursive=False)
        ]
    except (OSError, RuntimeError, TypeError, ValueError, psutil.Error):
        return {
            "available": False,
            "count": None,
            "rss_mb": None,
            "private_mb": None,
            "processes": [],
        }
    webengine_children = [
        item
        for item in children
        if item
        and (
            "qtwebengine" in str(item.get("name", "")).lower()
            or "chrome" in str(item.get("name", "")).lower()
            or "chromium" in str(item.get("name", "")).lower()
        )
    ]
    return {
        "available": True,
        "count": len(webengine_children),
        "rss_mb": round(sum(float(item.get("rss_mb") or 0.0) for item in webengine_children), 1),
        "private_mb": round(sum(float(item.get("private_mb") or 0.0) for item in webengine_children), 1),
        "processes": webengine_children,
    }


def _read_provider_health(provider: object | None) -> ProviderHealthSnapshot:
    health = ProviderHealthSnapshot.empty()
    if isinstance(provider, ProviderHealthPort):
        try:
            health = provider.read_provider_health()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            health = ProviderHealthSnapshot.empty()
    return health


def _central_quote_coverage_snapshot(central: object | None) -> dict[str, Any]:
    """Read the optional universe breakdown without letting diagnostics break the UI."""
    try:
        coverage_getter = getattr(central, "get_quote_coverage_snapshot", None)
        coverage = coverage_getter() if callable(coverage_getter) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {
            "available": False,
            "degraded_reasons": ["coverage_snapshot_unavailable"],
        }
    if coverage is None:
        return {}
    if not isinstance(coverage, Mapping):
        return {
            "available": False,
            "degraded_reasons": ["coverage_snapshot_invalid"],
        }
    try:
        return dict(coverage)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {
            "available": False,
            "degraded_reasons": ["coverage_snapshot_invalid"],
        }


def _central_quotes_snapshot(central: object | None) -> dict[str, Any]:
    runtime_state_getter = getattr(central, "runtime_state_snapshot", None)
    try:
        central_state = runtime_state_getter() if callable(runtime_state_getter) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        central_state = None
    return {
        "enabled": central is not None,
        "fetching": bool(getattr(central_state, "fetching", getattr(central, "_is_fetching", False))),
        "generation": int(getattr(central_state, "generation", getattr(central, "_fetch_generation", 0)) or 0),
        "started_at": float(getattr(central_state, "started_at", 0.0) or 0.0),
        "failure_count": int(getattr(central_state, "failure_count", 0) or 0),
        "current_source": str(getattr(central_state, "current_source", "") or ""),
        "pending_reason": str(
            getattr(central_state, "pending_reason", getattr(central, "_pending_fetch_reason", "")) or ""
        ),
        "circuit_breaker_cooldown_ticks": int(getattr(central, "_circuit_breaker_cooldown", 0) or 0),
        "post_cache_reload_quiet_until": _iso_from_timestamp(
            getattr(central, "_post_cache_reload_quiet_until", 0.0)
        ),
        "post_cache_reload_codes": len(getattr(central, "_post_cache_reload_signature", ()) or ()),
        "quote_coverage": _central_quote_coverage_snapshot(central),
    }


def _quote_snapshot(main_window) -> dict[str, Any]:
    provider = getattr(main_window, "data_provider", None)
    central = getattr(main_window, "central_quotes_svc", None)
    health = _read_provider_health(provider)
    health_payload = health.as_dict()
    request_stats = health_payload["request_stats"]
    provider_runtime = health_payload["runtime_stats"]
    fault_tolerance = provider_fault_tolerance(
        {
            "request_stats": request_stats,
            "provider_runtime": provider_runtime,
            "hithink_cooldown_until": health.hithink_cooldown_until,
            "hithink_last_error": health.hithink_last_error,
            "eastmoney_cooldown_until": health.eastmoney_cooldown_until,
            "eastmoney_last_error": health.eastmoney_last_error,
            "quote_cooldown_until": health.quote_cooldown_until,
            "quote_last_error": health.quote_last_error,
        }
    )
    provider_degraded = bool(fault_tolerance.get("provider_degraded"))
    fallback_or_degraded = bool(fault_tolerance.get("fallback_or_degraded"))
    last_network_error = str(fault_tolerance.get("last_network_error") or "")
    recent_status = str(fault_tolerance.get("recent_status") or "")
    source_layers = list(fault_tolerance.get("recent_source_layers") or [])
    return {
        "request_stats": request_stats,
        "central_quotes": _central_quotes_snapshot(central),
        "provider_runtime": provider_runtime,
        "provider_degraded": provider_degraded,
        "fallback_or_degraded": fallback_or_degraded,
        "last_network_error": last_network_error,
        "cooldown_seconds_left": fault_tolerance["cooldown_seconds_left"],
        "hithink_cooldown_until": health.hithink_cooldown_until,
        "hithink_last_error": health.hithink_last_error,
        "eastmoney_cooldown_seconds_left": fault_tolerance["eastmoney_cooldown_seconds_left"],
        "recent_triggered_network": fault_tolerance["recent_triggered_network"],
        "recent_cache_hit_count": fault_tolerance["recent_cache_hit_count"],
        "recent_pending_count": fault_tolerance["recent_pending_count"],
        "recent_status": recent_status,
        "recent_source_layers": source_layers,
        "fault_tolerance": fault_tolerance,
    }


def _market_data_snapshot(main_window) -> dict[str, Any]:
    provider = getattr(main_window, "data_provider", None)
    if provider is None:
        return {
            "ok": False,
            "active_layer": "unavailable",
            "data_status": "provider_missing",
            "fallback_or_degraded": True,
        }

    getter = getattr(provider, "get_market_data_source_status", None)
    if callable(getter):
        try:
            payload = getter() or {}
            if isinstance(payload, dict):
                return payload
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "active_layer": "status_error",
                "data_status": "status_error",
                "error": str(exc),
                "fallback_or_degraded": True,
            }

    cache_data = getattr(provider, "cache_data", {}) or {}
    try:
        symbol_count = len(cache_data)
    except TypeError:
        symbol_count = 0
    return {
        "ok": bool(symbol_count),
        "active_layer": "memory_cache" if symbol_count else "unknown",
        "data_status": "ok" if symbol_count else "unknown",
        "memory_symbol_count": symbol_count,
        "fallback_or_degraded": not bool(symbol_count),
    }


def _f5_scheduler_snapshot(main_window) -> dict[str, Any]:
    workspace = getattr(main_window, "_workspace", None)
    scheduler = getattr(workspace, "_f5_refresh_scheduler", None)
    pending_tasks = getattr(scheduler, "_tasks", None)
    pending_count = None
    if pending_tasks is not None:
        try:
            pending_count = len(pending_tasks)
        except TypeError:
            pending_count = None
    snapshot = {
        "workspace_available": workspace is not None,
        "scheduler_active": bool(scheduler is not None and getattr(scheduler, "is_running", lambda: False)()),
        "pending_tasks": pending_count,
        "scheduler_interval_ms": int(getattr(scheduler, "_interval_ms", 0) or 0) if scheduler is not None else 0,
        "frame_budget_ms": int(getattr(scheduler, "_frame_budget_ms", 0) or 0) if scheduler is not None else 0,
        "max_tasks_per_frame": int(getattr(scheduler, "_max_tasks_per_frame", 0) or 0) if scheduler is not None else 0,
    }
    snapshot.update(_f5_job_controller_snapshot(main_window))
    return snapshot


def _f5_job_controller_snapshot(main_window) -> dict[str, Any]:
    controller = None
    try:
        controller = getattr(main_window, "_f5_job_controller", None)
        if controller is None:
            return {
                "job_controller_present": False,
                "job_controller_diagnostics_available": True,
                "job_controller_running": False,
            }
        running = getattr(controller, "is_running")
        if not isinstance(running, bool):
            raise TypeError("F5 controller is_running must be a boolean")
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "job_controller_present": controller is not None,
            "job_controller_diagnostics_available": False,
            "job_controller_running": None,
            "job_controller_diagnostic_error": exc.__class__.__name__,
        }
    return {
        "job_controller_present": True,
        "job_controller_diagnostics_available": True,
        "job_controller_running": running,
    }


def _active_f5_rps_path(fallback: str) -> str:
    try:
        from infra.storage.f5_snapshot_repository import resolve_active_rps_path

        return resolve_active_rps_path(fallback)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return fallback


@f5_snapshot_read_locked
def _f5_cache_snapshot() -> dict[str, Any]:
    try:
        from core.runtime_paths import APP_VERSION, RPS_CACHE_FILE
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        APP_VERSION = ""
        RPS_CACHE_FILE = ""

    path = Path(_active_f5_rps_path(RPS_CACHE_FILE)) if RPS_CACHE_FILE else None
    snapshot = {
        "app_version": APP_VERSION,
        "path": str(path) if path else "",
        "exists": bool(path and path.exists()),
        "cache_version": None,
        "trade_date": "",
        "updated_at": "",
        "size_bytes": 0,
    }
    if not path or not path.exists():
        return snapshot

    try:
        stat = path.stat()
        snapshot["size_bytes"] = int(stat.st_size)
        snapshot["updated_at"] = _iso_from_timestamp(stat.st_mtime)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            snapshot["cache_version"] = payload.get("version") or payload.get("cache_version")
            snapshot["trade_date"] = str(
                payload.get("trade_date")
                or payload.get("date")
                or ((payload.get("meta") or {}).get("trade_date") if isinstance(payload.get("meta"), dict) else "")
                or ""
            )
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError):
        snapshot["read_error"] = True
    return snapshot


def _workspace_lineage_specs(workspace) -> dict[str, dict[str, Any]]:
    tab_specs = getattr(workspace, "tab_specs", None)
    raw_specs = tab_specs() if callable(tab_specs) else []
    specs = list(cast(Iterable[Any], raw_specs or []))
    return {str(item.get("key") or "").strip(): item for item in specs}


def _new_lineage_entry(key: str, defaults: dict[str, Any], spec: dict, tab) -> dict[str, Any]:
    entry = dict(defaults)
    entry.update(
        {
            "key": key,
            "title": str(spec.get("title") or ""),
            "group": str(spec.get("group") or ""),
            "loaded": tab is not None,
            "class_name": tab.__class__.__name__ if tab is not None else "",
            "row_count": _tab_row_count(tab),
            "last_updated": "",
            "trade_date": "",
            "network_capable": bool(defaults.get("network_capable", False)),
            "triggered_network": False if tab is None else defaults.get("triggered_network"),
        }
    )
    return entry


def _merge_custom_lineage(entry: dict[str, Any], tab) -> None:
    custom_getter = getattr(tab, "get_data_lineage", None)
    if not callable(custom_getter):
        return
    try:
        custom = custom_getter() or {}
        if not isinstance(custom, dict):
            return
        static_lineage_fields = _static_lineage_fields()
        rejected_fields = sorted(static_lineage_fields.intersection(custom))
        if rejected_fields:
            entry["lineage_error"] = True
            entry["static_override_rejected"] = rejected_fields
        entry.update(
            {field: value for field, value in custom.items() if field not in static_lineage_fields}
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        entry["lineage_error"] = True


def _lineage_status_text(tab) -> str:
    status_label = getattr(tab, "lbl_status", None)
    if status_label is None:
        return ""
    try:
        return str(status_label.text() or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _workspace_lineage(main_window) -> list[dict[str, Any]]:
    workspace = getattr(main_window, "_workspace", None)
    if workspace is None:
        return []
    specs_by_key = _workspace_lineage_specs(workspace)
    get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
    lineage = []

    for key, defaults in _key_view_lineage().items():
        tab = get_loaded_tab(key) if callable(get_loaded_tab) else None
        entry = _new_lineage_entry(key, defaults, specs_by_key.get(key, {}), tab)
        _merge_custom_lineage(entry, tab)
        _merge_runtime_network_activity(entry, tab)
        status_text = _lineage_status_text(tab)
        if status_text:
            entry["status_text"] = status_text
        lineage.append(entry)

    return lineage


def _merge_runtime_network_activity(entry: dict[str, Any], tab) -> None:
    try:
        observed_network = getattr(tab, "_runtime_network_triggered", None)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        entry["lineage_error"] = True
        return
    if observed_network is None:
        return
    if type(observed_network) is not bool:
        entry["lineage_error"] = True
        return
    entry["triggered_network"] = bool(observed_network or entry.get("triggered_network") is True)


def _workspace_lineage_exclusions(main_window) -> list[dict[str, Any]]:
    workspace = getattr(main_window, "_workspace", None)
    tab_specs = getattr(workspace, "tab_specs", None)
    raw_specs = tab_specs() if callable(tab_specs) else []
    specs = list(cast(Iterable[Any], raw_specs or []))
    specs_by_key = {
        str(item.get("key") or "").strip(): item
        for item in specs
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
    exclusions = []
    for key, defaults in _data_lineage_excluded_tabs().items():
        spec = specs_by_key.get(key, {})
        tab = get_loaded_tab(key) if callable(get_loaded_tab) else None
        exclusions.append(
            {
                "key": key,
                "title": str(spec.get("title") or ""),
                "group": str(spec.get("group") or ""),
                "loaded": tab is not None,
                "class_name": tab.__class__.__name__ if tab is not None else "",
                **dict(defaults),
            }
        )
    return exclusions


def _lineage_coverage_snapshot() -> dict[str, Any]:
    return {
        "covered": list(_data_lineage_covered_tabs()),
        "excluded": list(_data_lineage_excluded_tabs()),
    }


def _ui_stall_snapshot() -> dict[str, Any]:
    probe = get_ui_stall_probe()
    if probe is None:
        return {"installed": False}
    try:
        return dict(probe.stall_snapshot())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {"installed": False, "error": "snapshot_failed"}


def _runtime_health_root(main_window=None):
    app = cast(Any, QApplication.instance())
    root = main_window
    if root is None and app is not None:
        active = app.activeWindow()
        root = active if active is not None else (app.topLevelWidgets()[0] if app.topLevelWidgets() else None)
    return root


def collect_runtime_health_summary(main_window=None, *, kline_manager_instance: object | None = None) -> dict[str, Any]:
    """Collect only the fields needed by frequent stability-cycle checkpoints."""
    root = _runtime_health_root(main_window)
    process = collect_process_snapshot()
    return {
        "process": process,
        "runtime_monitor": runtime_health_report(
            process_snapshot=process,
            kline_manager_instance=kline_manager_instance,
        ),
        "background_tasks": _active_task_snapshot(),
        "timers": _timer_snapshot(root),
        "event_bus": _event_bus_snapshot(),
        "webengine": _webengine_snapshot(detailed=False),
    }


def collect_runtime_health(main_window=None, *, kline_manager_instance: object | None = None) -> dict[str, Any]:
    root = _runtime_health_root(main_window)

    process = collect_process_snapshot()
    return {
        "schema_version": 1,
        "report_type": "runtime_health",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "threading": {
            "python_thread_count": len(threading.enumerate()),
            "threads": sorted(thread.name for thread in threading.enumerate()),
        },
        "process": process,
        "runtime_monitor": runtime_health_report(
            process_snapshot=process,
            kline_manager_instance=kline_manager_instance,
        ),
        "background_tasks": _active_task_snapshot(),
        "timers": _timer_snapshot(root),
        "event_bus": _event_bus_snapshot(),
        "webengine": _webengine_snapshot(),
        "quotes": _quote_snapshot(root) if root is not None else {},
        "market_data": _market_data_snapshot(root) if root is not None else {},
        "f5_refresh": _f5_scheduler_snapshot(root) if root is not None else {},
        "f5_cache": _f5_cache_snapshot(),
        "ui_stalls": _ui_stall_snapshot(),
        "data_lineage": _workspace_lineage(root) if root is not None else [],
        "data_lineage_coverage": _lineage_coverage_snapshot(),
        "data_lineage_exclusions": _workspace_lineage_exclusions(root) if root is not None else [],
    }


def _trend_one(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "first": None, "last": None, "net_delta": 0.0, "range": 0.0, "max": None}
    return {
        "count": len(values),
        "first": values[0],
        "last": values[-1],
        "net_delta": round(values[-1] - values[0], 3),
        "range": round(max(values) - min(values), 3),
        "max": max(values),
    }


def build_runtime_health_trend(samples: list[dict[str, Any]]) -> dict[str, Any]:
    def _values(getter) -> list[float]:
        values = []
        for sample in samples or []:
            try:
                value = getter(sample)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                value = None
            if value is not None:
                values.append(float(value))
        return values

    return {
        "background_tasks": _trend_one(_values(lambda item: (item.get("background_tasks") or {}).get("count"))),
        "active_timers": _trend_one(_values(lambda item: (item.get("timers") or {}).get("active"))),
        "total_timers": _trend_one(_values(lambda item: (item.get("timers") or {}).get("total"))),
        "event_receivers": _trend_one(_values(lambda item: (item.get("event_bus") or {}).get("total_receivers"))),
        "threads": _trend_one(_values(lambda item: (item.get("process") or {}).get("thread_count"))),
        "webengine_children": _trend_one(_values(lambda item: (item.get("webengine") or {}).get("count"))),
        "webengine_rss_mb": _trend_one(_values(lambda item: (item.get("webengine") or {}).get("rss_mb"))),
        "webengine_private_mb": _trend_one(_values(lambda item: (item.get("webengine") or {}).get("private_mb"))),
    }


def runtime_health_output_dir(project_root: str | Path | None = None, *, now: datetime | None = None) -> Path:
    root = Path(project_root) if project_root is not None else Path.cwd()
    stamp = (now or datetime.now()).strftime("%Y%m%d")
    return root / "tmp" / f"runtime_health_{stamp}"


def export_runtime_health_report(
    main_window=None,
    *,
    project_root: str | Path | None = None,
    report: dict[str, Any] | None = None,
    now: datetime | None = None,
    kline_manager_instance: object | None = None,
) -> Path:
    current = now or datetime.now()
    payload = (
        report
        if report is not None
        else collect_runtime_health(main_window, kline_manager_instance=kline_manager_instance)
    )
    output_dir = runtime_health_output_dir(project_root, now=current)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"runtime_health_{current.strftime('%H%M%S_%f')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
