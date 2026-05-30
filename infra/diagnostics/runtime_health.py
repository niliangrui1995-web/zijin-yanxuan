from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from core.process_watchdog import collect_process_snapshot
from domains.runtime.fault_tolerance import provider_fault_tolerance

try:  # pragma: no cover - psutil is optional outside the packaged runtime.
    import psutil
except Exception:  # pragma: no cover
    psutil = None

EVENT_SIGNAL_NAMES = (
    "sig_system_log",
    "sig_network_status_changed",
    "sig_app_closing",
    "sig_rt_quotes",
    "sig_rt_quotes_refreshed",
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

KEY_VIEW_LINEAGE = {
    "stock_candidates": {
        "view": "stock_candidates",
        "source": "workspace_stock_context",
        "provider": "workspace_stock_context",
        "cache_refs": ["global_store.quotes", "workspace.collect_stock_context"],
        "triggered_network": False,
        "fallback_or_degraded": False,
        "updated_at": "",
        "errors": [],
        "warnings": [],
    },
    "asian_market": {
        "view": "asian_market",
        "source": "asian_market local cache + realtime quote cache",
        "provider": "AsianMarketWorker/yfinance/cache",
        "cache_refs": [
            "data/Cache/asian_klines_latest.json",
            "data/Cache/asian_rt_latest.json",
            "global_store.quotes",
        ],
        "triggered_network": True,
        "fallback_or_degraded": None,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
    "na_daily": {
        "view": "na_daily",
        "source": "daily report markdown/json + global_store.quotes",
        "provider": "NADailyTab local report reader",
        "cache_refs": [
            "daily report output:report json/markdown",
            "global_store.quotes",
        ],
        "triggered_network": True,
        "fallback_or_degraded": None,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
    "ai_industry_chain": {
        "view": "ai_industry_chain",
        "source": "AI industry chain workbook + local market data",
        "provider": "AIIndustryChainTab workbook reader",
        "cache_refs": [
            "AI industry chain workbook",
            "local market data provider",
            "global_store.quotes",
        ],
        "triggered_network": True,
        "fallback_or_degraded": None,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
    "scan": {
        "view": "scan",
        "source": "DataStore.scan_cache",
        "provider": "scan_runtime_service",
        "cache_refs": ["data/vcp_hunter.db:kv_store.scan_cache", "data/scan_cache.json.migrated"],
        "triggered_network": False,
        "fallback_or_degraded": False,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
    "lhb": {
        "view": "lhb",
        "source": "LhbPoolManager cache + local_quote_snapshot",
        "provider": "LhbPoolManager",
        "cache_refs": ["data/Cache/lhb_pool_30d.json", "global_store.quotes", "local_tdx_cache"],
        "triggered_network": False,
        "fallback_or_degraded": False,
    },
    "foreign_block": {
        "view": "foreign_block",
        "source": "foreign_block_trade_latest.json",
        "cache_refs": ["data/Cache/foreign_block_trade_latest.json"],
        "triggered_network": False,
        "fallback_or_degraded": False,
    },
    "earnings": {
        "view": "earnings",
        "source": "earnings_state / local display window",
        "cache_refs": ["data/vcp_hunter.db:earnings_state", "global_store.quotes"],
        "triggered_network": False,
        "fallback_or_degraded": False,
    },
    "fund_holdings": {
        "view": "fund_holdings",
        "source": "fund_holdings_store",
        "cache_refs": ["data/vcp_hunter.db:fund holdings tables", "global_store.quotes"],
        "triggered_network": False,
        "fallback_or_degraded": False,
    },
    "rt_monitor": {
        "view": "rt_monitor",
        "source": "data_provider.cache_data + global_store.quotes",
        "provider": "rt_scan_worker",
        "cache_refs": ["data/Cache/vcp_rps_precomputed.json", "global_store.quotes"],
        "triggered_network": True,
        "fallback_or_degraded": None,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
    "watchlist": {
        "view": "watchlist",
        "source": "watchlist_vm + global_store.quotes",
        "provider": "watchlist_vm/global_store",
        "cache_refs": ["watchlist store", "global_store.quotes"],
        "triggered_network": True,
        "fallback_or_degraded": None,
        "updated_at": "",
        "errors": [],
        "warnings": [],
        "provider_fault_tolerance": {},
    },
}

DATA_LINEAGE_COVERED_TABS = tuple(KEY_VIEW_LINEAGE.keys())
DATA_LINEAGE_EXCLUDED_TABS = {
    "system_log": {
        "reason": "non_data_tab",
        "description": "system_log is an operational log surface, not a data table or upstream data source.",
    },
}


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
    table = table_getter() if callable(table_getter) else None
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
        active_workers = getattr(manager, "active_workers", {}) or {}
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        active_workers = {}

    task_ids = sorted(str(task_id) for task_id in active_workers.keys())
    workers = []
    for task_id, worker in sorted(active_workers.items(), key=lambda item: str(item[0])):
        workers.append(
            {
                "task_id": str(task_id),
                "worker_class": worker.__class__.__name__,
                "cancelled": bool(getattr(worker, "_is_cancelled", False)),
            }
        )
    return {
        "count": len(task_ids),
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


def _process_info(process) -> dict[str, Any] | None:
    if psutil is None:
        return None
    try:
        memory = process.memory_info()
        item = {
            "pid": process.pid,
            "name": process.name(),
            "rss_mb": _mb(getattr(memory, "rss", 0)),
            "vms_mb": _mb(getattr(memory, "vms", 0)),
            "thread_count": process.num_threads(),
        }
        private_value = getattr(memory, "private", None)
        if private_value is not None:
            item["private_mb"] = _mb(private_value)
        working_set = getattr(memory, "wset", None)
        if working_set is not None:
            item["working_set_mb"] = _mb(working_set)
        return item
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, psutil.Error):
        return None


def _webengine_snapshot() -> dict[str, Any]:
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
        children = [_process_info(child) for child in process.children(recursive=True)]
    except (OSError, RuntimeError, TypeError, ValueError, psutil.Error):
        children = []
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


def _quote_snapshot(main_window) -> dict[str, Any]:
    provider = getattr(main_window, "data_provider", None)
    central = getattr(main_window, "central_quotes_svc", None)
    request_stats = {}
    provider_runtime = {}

    getter = getattr(provider, "get_quote_request_stats", None)
    if callable(getter):
        try:
            request_stats = getter() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            request_stats = {}

    runtime_getter = getattr(provider, "get_realtime_runtime_stats", None)
    if callable(runtime_getter):
        try:
            provider_runtime = runtime_getter() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            provider_runtime = {}

    eastmoney_cooldown_until = float(getattr(provider, "_rt_eastmoney_cooldown_until", 0.0) or 0.0)
    fault_tolerance = provider_fault_tolerance(
        {
            "request_stats": request_stats,
            "provider_runtime": provider_runtime,
            "eastmoney_cooldown_until": eastmoney_cooldown_until,
            "eastmoney_last_error": getattr(provider, "_rt_eastmoney_last_error", ""),
        }
    )
    provider_degraded = bool(fault_tolerance.get("provider_degraded"))
    fallback_or_degraded = bool(fault_tolerance.get("fallback_or_degraded"))
    last_network_error = str(fault_tolerance.get("last_network_error") or "")
    recent_status = str(fault_tolerance.get("recent_status") or "")
    source_layers = list(fault_tolerance.get("recent_source_layers") or [])
    return {
        "request_stats": request_stats,
        "central_quotes": {
            "enabled": central is not None,
            "fetching": bool(getattr(central, "_is_fetching", False)),
            "generation": int(getattr(central, "_fetch_generation", 0) or 0),
            "circuit_breaker_cooldown_ticks": int(getattr(central, "_circuit_breaker_cooldown", 0) or 0),
            "post_cache_reload_quiet_until": _iso_from_timestamp(
                getattr(central, "_post_cache_reload_quiet_until", 0.0)
            ),
            "post_cache_reload_codes": len(getattr(central, "_post_cache_reload_signature", ()) or ()),
        },
        "provider_runtime": provider_runtime,
        "provider_degraded": provider_degraded,
        "fallback_or_degraded": fallback_or_degraded,
        "last_network_error": last_network_error,
        "cooldown_seconds_left": fault_tolerance["cooldown_seconds_left"],
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
    return {
        "workspace_available": workspace is not None,
        "scheduler_active": bool(scheduler is not None and getattr(scheduler, "is_running", lambda: False)()),
        "pending_tasks": pending_count,
        "scheduler_interval_ms": int(getattr(scheduler, "_interval_ms", 0) or 0) if scheduler is not None else 0,
        "frame_budget_ms": int(getattr(scheduler, "_frame_budget_ms", 0) or 0) if scheduler is not None else 0,
        "max_tasks_per_frame": int(getattr(scheduler, "_max_tasks_per_frame", 0) or 0) if scheduler is not None else 0,
    }


def _f5_cache_snapshot() -> dict[str, Any]:
    try:
        from core.runtime_paths import APP_VERSION, RPS_CACHE_FILE
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        APP_VERSION = ""
        RPS_CACHE_FILE = ""

    path = Path(RPS_CACHE_FILE) if RPS_CACHE_FILE else None
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


def _workspace_lineage(main_window) -> list[dict[str, Any]]:
    workspace = getattr(main_window, "_workspace", None)
    if workspace is None:
        return []

    tab_specs = getattr(workspace, "tab_specs", None)
    specs = list(tab_specs() or []) if callable(tab_specs) else []
    get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
    lineage = []

    for key, defaults in KEY_VIEW_LINEAGE.items():
        spec = next((item for item in specs if str(item.get("key") or "").strip() == key), {})
        tab = get_loaded_tab(key) if callable(get_loaded_tab) else None
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
            }
        )
        custom_getter = getattr(tab, "get_data_lineage", None)
        if callable(custom_getter):
            try:
                custom = custom_getter() or {}
                if isinstance(custom, dict):
                    entry.update(custom)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                entry["lineage_error"] = True
        status_label = getattr(tab, "lbl_status", None)
        status_text = ""
        if status_label is not None:
            try:
                status_text = str(status_label.text() or "").strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                status_text = ""
        if status_text:
            entry["status_text"] = status_text
        lineage.append(entry)

    return lineage


def _workspace_lineage_exclusions(main_window) -> list[dict[str, Any]]:
    workspace = getattr(main_window, "_workspace", None)
    tab_specs = getattr(workspace, "tab_specs", None)
    specs = list(tab_specs() or []) if callable(tab_specs) else []
    specs_by_key = {
        str(item.get("key") or "").strip(): item
        for item in specs
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
    exclusions = []
    for key, defaults in DATA_LINEAGE_EXCLUDED_TABS.items():
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
        "covered": list(DATA_LINEAGE_COVERED_TABS),
        "excluded": list(DATA_LINEAGE_EXCLUDED_TABS),
    }


def collect_runtime_health(main_window=None) -> dict[str, Any]:
    app = QApplication.instance()
    root = main_window
    if root is None and app is not None:
        active = app.activeWindow()
        root = active if active is not None else (app.topLevelWidgets()[0] if app.topLevelWidgets() else None)

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
        "background_tasks": _active_task_snapshot(),
        "timers": _timer_snapshot(root),
        "event_bus": _event_bus_snapshot(),
        "webengine": _webengine_snapshot(),
        "quotes": _quote_snapshot(root) if root is not None else {},
        "market_data": _market_data_snapshot(root) if root is not None else {},
        "f5_refresh": _f5_scheduler_snapshot(root) if root is not None else {},
        "f5_cache": _f5_cache_snapshot(),
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
) -> Path:
    current = now or datetime.now()
    payload = report if report is not None else collect_runtime_health(main_window)
    output_dir = runtime_health_output_dir(project_root, now=current)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"runtime_health_{current.strftime('%H%M%S_%f')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
