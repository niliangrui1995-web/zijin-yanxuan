from __future__ import annotations

import time

# Earliest clock available inside this script. It intentionally excludes Python
# process/interpreter startup and the import of ``time`` itself.
_SCRIPT_MODULE_ENTRY_STARTED_AT = time.perf_counter()

import argparse
import faulthandler
import json
import math
import os
import sqlite3
import sys
from contextlib import suppress
from itertools import pairwise
from pathlib import Path

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import QApplication

from app.bootstrap.startup_orchestrator import ASIAN_DATA_SYNC_START_DELAY_MS, ASIAN_DATA_SYNC_TIMEOUT_SEC
from app.services.f5_retention_service import inspect_f5_runtime
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_service import background_job_runner as task_manager
from core.runtime_paths import CACHE_DIR
from infra.diagnostics.runtime_health import (
    build_runtime_health_trend,
    collect_runtime_health,
    collect_runtime_health_summary,
    export_runtime_health_report,
)
from infra.diagnostics.ui_exception_boundary import install_ui_exception_hook
from infra.diagnostics.ui_stall_probe import get_ui_stall_probe
from infra.storage.json_cache_repository import save_json_file
from infra.tasks import (
    STARTUP_ASIAN_DATA_SYNC,
    STARTUP_DATA_PROVIDER,
    STARTUP_DEFERRED_LOAD,
    STARTUP_F5_RETENTION,
    STARTUP_SMART,
    task_registry,
)
from scripts.perf_budget_check import check_runtime_health_budget
from ui.components.kline_window_manager import kline_manager
from ui.components.thread_shutdown import pending_thread_count
from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER
from ui.main_window_qt import MainWindowQT
from ui.main_window_runtime import finish_f5_reload, start_f5_precompute
from ui.workspaces.tab_registry import health_probe_tab_keys, startup_tab_keys

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

DEFAULT_TABS = health_probe_tab_keys()
BACKGROUND_PRELOAD_ORDER = startup_tab_keys()

SOAK_MODE_MINUTES = {
    "short": 0,
    "long": 30,
    "soak30": 30,
    "soak60": 60,
}
POST_TAB_IDLE_TIMEOUT_MS = 5000
STARTUP_TASK_IDLE_TIMEOUT_MS = (
    ASIAN_DATA_SYNC_START_DELAY_MS + ASIAN_DATA_SYNC_TIMEOUT_SEC * 1000 + 5000
)
INITIAL_TAB_READY_TIMEOUT_MS = 3000
BACKGROUND_PRELOAD_TIMEOUT_MS = 10 * 60 * 1000
KLINE_PREWARM_STABLE_TIMEOUT_MS = 12_000
REAL_F5_TIMEOUT_SECONDS = 30 * 60
STARTUP_TIMING_MODULE_ORIGIN = "script_module_after_time_import"
STARTUP_TIMING_APP_ORIGIN = "run_suite_entry_before_qapplication"
STABLE_TAIL_SAMPLE_LABELS = frozenset(
    {
        "after_tab_cycle",
        "after_tab_async_tail",
        "after_f5_cycle",
        "after_quote_cycle",
        "after_kline_prewarm",
        "final",
    }
)
KLINE_OPEN_UI_STALL_SCOPE = "kline_open_to_chart_ready"
AUTO_REFRESH_ASIAN_RUNTIME = task_registry.workspace(
    "auto_refresh_asian_market_runtime",
    description="Visible Asian-market runtime refresh",
)
AUTO_REFRESH_EARNINGS_STARTUP_GAP_FILL = task_registry.workspace(
    "auto_refresh_earnings_startup_gap_fill",
    description="Automatic earnings startup gap fill",
)
STARTUP_TASK_KEYS = (
    STARTUP_DEFERRED_LOAD,
    STARTUP_ASIAN_DATA_SYNC,
    STARTUP_SMART,
    STARTUP_F5_RETENTION,
    STARTUP_DATA_PROVIDER,
    AUTO_REFRESH_ASIAN_RUNTIME,
    AUTO_REFRESH_EARNINGS_STARTUP_GAP_FILL,
)
STARTUP_TASK_IDS = frozenset(task.task_id for task in STARTUP_TASK_KEYS)
BACKGROUND_PRELOAD_TASK_OBSERVER_INTERVAL_MS = 25
BACKGROUND_PRELOAD_FORBIDDEN_TASK_ID = "cn_trade_calendar_refresh"
BACKGROUND_PRELOAD_FORBIDDEN_TASK_CATEGORIES = frozenset({"startup", "network"})


def _derived_output_artifact_path(output: Path | None, suffix: str) -> Path | None:
    if output is None:
        return None
    return Path(output).with_suffix(suffix)


class _SuiteEvidence:
    """Persist the last known suite boundary before native code can terminate Python."""

    def __init__(self, output: Path | None):
        self.output_path = Path(output) if output is not None else None
        self.checkpoint_path = _derived_output_artifact_path(output, ".checkpoint.json")
        self.faulthandler_path = _derived_output_artifact_path(output, ".faulthandler.log")
        self._visibility: dict = {}
        self._sample_paths: list[str] = []
        self._state = {
            "schema_version": 1,
            "report_type": "runtime_health_stability_checkpoint",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "",
            "status": "initialized",
            "event": "initialized",
            "output_path": str(self.output_path or ""),
            "faulthandler_path": str(self.faulthandler_path or ""),
            "visibility": {},
            "last_completed_phase": "",
            "current_phase": "",
            "current_tab": None,
            "tab_progress": {
                "completed": 0,
                "total": 0,
                "cycles": 0,
                "last_completed": None,
                "last_good": None,
                "last_status": "",
                "tabs": [],
            },
            "sample_paths": [],
            "last_sample_label": "",
            "unhandled_ui_exceptions": [],
        }

    @property
    def enabled(self) -> bool:
        return self.checkpoint_path is not None

    def bind(self, report: dict, tabs: tuple[str, ...], cycles: int, sample_paths: list[str]) -> None:
        self._visibility = report.get("window_visibility") or {}
        self._sample_paths = sample_paths
        progress = self._state["tab_progress"]
        progress["cycles"] = max(0, int(cycles))
        progress["total"] = len(tabs) * progress["cycles"]
        self._write("started", status="running")

    def _write(self, event: str, *, status: str | None = None) -> None:
        if not self.enabled:
            return
        if status is not None:
            self._state["status"] = status
        self._state["event"] = event
        self._state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._state["visibility"] = dict(self._visibility)
        self._state["sample_paths"] = list(self._sample_paths)
        save_json_file(str(self.checkpoint_path), self._state)

    def idle_complete(self) -> None:
        self._state["last_completed_phase"] = "idle"
        self._state["current_phase"] = ""
        self._write("idle_complete", status="running")

    def phase_start(self, phase: str) -> None:
        self._state["current_phase"] = str(phase)
        self._write("phase_start", status="running")

    def phase_end(self, phase: str) -> None:
        self._state["last_completed_phase"] = str(phase)
        self._state["current_phase"] = ""
        self._write("phase_end", status="running")

    def tab_start(self, key: str, cycle: int) -> None:
        progress = self._state["tab_progress"]
        progress["current_cycle"] = int(cycle)
        progress["current_index"] = int(progress["completed"]) + 1
        self._state["current_tab"] = str(key)
        self._write("tab_start", status="running")

    def tab_end(self, key: str, cycle: int, timing: dict) -> None:
        progress = self._state["tab_progress"]
        status = str(timing.get("status") or "")
        progress["completed"] = int(progress["completed"]) + 1
        progress["last_completed"] = str(key)
        progress["last_status"] = status
        if status in {"ok", "skipped_controlled_probe"}:
            progress["last_good"] = str(key)
        progress["tabs"].append(
            {
                "cycle": int(cycle),
                "key": str(key),
                "status": status,
                "elapsed_ms": timing.get("elapsed_ms"),
            }
        )
        progress.pop("current_cycle", None)
        progress.pop("current_index", None)
        self._state["current_tab"] = None
        self._write("tab_end", status="running")

    def error(self, exc: BaseException) -> None:
        self._state["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "phase": self._state.get("current_phase") or "",
            "current_tab": self._state.get("current_tab"),
        }
        self._write("error", status="error")

    def ui_exception(self, record: dict) -> None:
        exceptions = self._state["unhandled_ui_exceptions"]
        exceptions.append(dict(record))
        self._write("ui_exception", status="running")

    def sample_recorded(self, label: str) -> None:
        self._state["last_sample_label"] = str(label)
        self._write("sample_recorded", status="running")

    def window_visibility_failed(self) -> None:
        self._write("window_visibility_failed", status="running")

    def complete(self, report: dict) -> None:
        self._state["current_phase"] = ""
        self._state["current_tab"] = None
        self._state["report_status"] = str(report.get("status") or "")
        self._write("complete", status="complete")


def _enable_suite_faulthandler(evidence: _SuiteEvidence):
    path = evidence.faulthandler_path
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = path.open("a", encoding="utf-8")
    file_obj.write(
        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"runtime_health_stability_suite pid={os.getpid()}\n"
    )
    file_obj.flush()
    os.fsync(file_obj.fileno())
    faulthandler.enable(file=file_obj, all_threads=True)
    return file_obj


def _close_suite_faulthandler(file_obj) -> None:
    if file_obj is None:
        return
    with suppress(RuntimeError):
        faulthandler.disable()
    with suppress(OSError):
        file_obj.flush()
        os.fsync(file_obj.fileno())
    file_obj.close()


def _save_final_report(output: Path, report: dict) -> None:
    save_json_file(str(output), report)


def _record_evidence(evidence: _SuiteEvidence | None, method_name: str, *args) -> None:
    if evidence is not None:
        getattr(evidence, method_name)(*args)


def _process_events(
    app: QApplication,
    rounds: int = 1,
    sleep_ms: int = 0,
    *,
    flush_deferred_deletes: bool = False,
) -> None:
    for _ in range(max(0, int(rounds))):
        app.processEvents()
        if flush_deferred_deletes:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
    if rounds > 0 and sleep_ms > 0:
        app.processEvents()


def _settle(app: QApplication, settle_ms: int) -> None:
    if settle_ms <= 0:
        _process_events(app, rounds=3)
        return
    loop = QEventLoop()
    QTimer.singleShot(max(1, int(settle_ms)), loop.quit)
    loop.exec()
    app.processEvents()


def _probe_display_mode(args: argparse.Namespace) -> dict:
    return {"native_qt": bool(args.native_qt), "show_window": bool(args.show_window)}


def _window_visibility_evidence(args: argparse.Namespace) -> dict:
    required = bool(args.show_window)
    return {
        "required": required,
        "status": "pending" if required else "not_required",
        "planned_observation_seconds": max(0, int(args.idle_seconds or 0)),
        "actual_observation_seconds": 0,
        "first_invisible_at_seconds": None,
        "first_invisible_reason": None,
    }


def _probe_first_paint(window: MainWindowQT, app: QApplication, args: argparse.Namespace, suite_started_at) -> dict:
    if args.show_window:
        window.show()
        if hasattr(window, "_first_paint_recorded"):
            _wait_until(app, lambda: bool(window._first_paint_recorded), timeout_ms=5000)
        else:
            _process_events(app, rounds=2)
    first_paint_ms = _as_float(getattr(window, "_first_paint_elapsed_ms", None))
    if first_paint_ms is None and suite_started_at is not None:
        first_paint_ms = max(0.0, (time.perf_counter() - suite_started_at) * 1000.0)
    return {
        "first_paint_ms": round(first_paint_ms, 3) if first_paint_ms is not None else None,
        "first_paint_recorded": bool(getattr(window, "_first_paint_recorded", False)),
    }


def _initial_tab_key(workspace) -> str:
    tab_widget = getattr(workspace, "tabs", None)
    specs = _tab_specs(workspace) if tab_widget is not None else []
    try:
        index = int(tab_widget.currentIndex()) if tab_widget is not None else -1
    except (AttributeError, RuntimeError, TypeError, ValueError):
        index = -1
    return str(specs[index].get("key") or "").strip() if 0 <= index < len(specs) else ""


def _initial_tab_probe_status(initial_key: str, *, loaded: bool, ready: bool) -> str:
    if not initial_key:
        return "missing_key"
    if ready:
        return "ok"
    return "data_timeout" if loaded else "load_timeout"


def _probe_initial_tab(window: MainWindowQT, app: QApplication, suite_started_at) -> dict:
    workspace = getattr(window, "_workspace", None)
    initial_key = _initial_tab_key(workspace)
    wait_started_at = time.perf_counter()
    initial_ready = bool(initial_key and _tab_runtime_probe_ready(_loaded_tab(workspace, initial_key)))
    if initial_key and not initial_ready:
        initial_ready = _wait_until(
            app,
            lambda: _tab_runtime_probe_ready(_loaded_tab(workspace, initial_key)),
            timeout_ms=INITIAL_TAB_READY_TIMEOUT_MS,
        )
    initial_loaded = bool(initial_key and _loaded_tab(workspace, initial_key) is not None)
    recorded_ready_ms = getattr(workspace, "_initial_tab_ready_elapsed_ms", None)
    try:
        recorded_ready_ms = float(recorded_ready_ms)
    except (TypeError, ValueError):
        recorded_ready_ms = 0.0
    ready_ms = None
    if initial_ready:
        if recorded_ready_ms > 0:
            ready_ms = recorded_ready_ms
        elif suite_started_at is not None:
            ready_ms = (time.perf_counter() - suite_started_at) * 1000.0
    return {
        "initial_tab_key": initial_key,
        "initial_tab_loaded": initial_loaded,
        "initial_tab_ready": initial_ready,
        "initial_tab_status": _initial_tab_probe_status(
            initial_key,
            loaded=initial_loaded,
            ready=initial_ready,
        ),
        "initial_tab_timeout_ms": INITIAL_TAB_READY_TIMEOUT_MS,
        "initial_tab_wait_elapsed_ms": round((time.perf_counter() - wait_started_at) * 1000.0, 3),
        "initial_tab_ready_ms": round(ready_ms, 3) if ready_ms is not None else None,
    }


def _prepare_probe_window(
    window: MainWindowQT,
    app: QApplication,
    args: argparse.Namespace,
    *,
    suite_started_at: float | None = None,
) -> dict:
    phases = _probe_first_paint(window, app, args, suite_started_at)
    _settle(app, args.startup_settle_ms)
    phases.update(_probe_initial_tab(window, app, suite_started_at))
    return phases


def _wait_until(app: QApplication, predicate, *, timeout_ms: int, step_ms: int = 25) -> bool:
    deadline = time.perf_counter() + max(1, int(timeout_ms)) / 1000.0
    while time.perf_counter() < deadline:
        if predicate():
            return True
        remaining_ms = max(1, int((deadline - time.perf_counter()) * 1000.0))
        _settle(app, min(max(1, int(step_ms)), remaining_ms))
    _process_events(app, rounds=2)
    return bool(predicate())


def _read_nonnegative_int_diagnostic(reader) -> tuple[int | None, bool]:
    try:
        value = reader()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None, False
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, False
    return value, True


def _read_bool_diagnostic(reader) -> tuple[bool | None, bool]:
    try:
        value = reader()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None, False
    return (value, True) if isinstance(value, bool) else (None, False)


def _observe_required_window_visibility(window, evidence: dict, observed_seconds: int) -> bool:
    if evidence.get("required") is not True:
        return True
    observed = max(0, int(observed_seconds))
    evidence["actual_observation_seconds"] = observed
    visible, available = _read_bool_diagnostic(lambda: window.isVisible())
    if not available:
        evidence.update(
            {
                "status": "diagnostic_unavailable",
                "first_invisible_at_seconds": observed,
                "first_invisible_reason": "visibility_diagnostic_unavailable",
            }
        )
        return False
    if visible is not True:
        evidence.update(
            {
                "status": "not_visible",
                "first_invisible_at_seconds": observed,
                "first_invisible_reason": "window_not_visible",
            }
        )
        return False
    planned = int(evidence.get("planned_observation_seconds") or 0)
    evidence["status"] = "ok" if observed >= planned else "observing"
    return True


def _active_background_task_count() -> int | None:
    count, _available = _read_nonnegative_int_diagnostic(lambda: task_manager.active_count)
    return count


def _manager_active_worker_ids(manager) -> tuple[str, ...] | None:
    active_workers = getattr(manager, "active_workers", None)
    if not isinstance(active_workers, dict):
        return None
    lock = getattr(manager, "_lock", None)
    if lock is None:
        return tuple(sorted(str(task_id) for task_id in active_workers))
    with lock:
        return tuple(sorted(str(task_id) for task_id in active_workers))


def _active_background_task_ids() -> tuple[str, ...] | None:
    resolver = getattr(task_manager, "_resolve_manager", None)
    try:
        manager = resolver() if callable(resolver) else task_manager
        return _manager_active_worker_ids(manager)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _active_known_task_ids(task_keys=STARTUP_TASK_KEYS) -> tuple[str, ...] | None:
    is_active = getattr(task_manager, "is_active_task", None)
    if not callable(is_active):
        return None
    try:
        return tuple(sorted(task.task_id for task in task_keys if bool(is_active(task))))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _wait_for_startup_tasks_idle(
    app: QApplication,
    *,
    suite_started_at: float,
    await_delayed_asian: bool,
    timeout_ms: int = STARTUP_TASK_IDLE_TIMEOUT_MS,
) -> dict:
    active_before = _active_known_task_ids()
    if active_before is None:
        return {
            "status": "unavailable",
            "task_ids": sorted(STARTUP_TASK_IDS),
            "remaining_task_ids": [],
            "contaminated": True,
        }
    observed = set(active_before)
    latest = {"ids": active_before}
    delay_horizon_ms = ASIAN_DATA_SYNC_START_DELAY_MS if await_delayed_asian else 0

    def _settled() -> bool:
        current = _active_known_task_ids()
        if current is None:
            return False
        latest["ids"] = current
        observed.update(current)
        elapsed_ms = (time.perf_counter() - suite_started_at) * 1000.0
        return elapsed_ms >= delay_horizon_ms and not current

    idle = _wait_until(app, _settled, timeout_ms=timeout_ms)
    active_after = latest["ids"]
    return {
        "status": "ok" if idle else "timeout",
        "task_id": STARTUP_SMART.task_id,
        "task_ids": sorted(STARTUP_TASK_IDS),
        "active_before": bool(active_before),
        "active_before_ids": list(active_before),
        "active_after": bool(active_after),
        "observed_task_ids": sorted(observed),
        "remaining_task_ids": list(active_after),
        "delayed_task_ids": [STARTUP_ASIAN_DATA_SYNC.task_id] if await_delayed_asian else [],
        "delay_horizon_ms": int(delay_horizon_ms),
        "timeout_ms": int(timeout_ms),
        "contaminated": not idle,
    }


def _wait_for_global_background_tasks_idle(app: QApplication, *, timeout_ms: int, step_ms: int) -> dict:
    before = _active_background_task_count()
    if before is None:
        return {"status": "unavailable", "timeout_ms": int(timeout_ms), "active_before": None, "active_after": None}
    if int(timeout_ms) <= 0:
        return {"status": "skipped", "timeout_ms": int(timeout_ms), "active_before": before, "active_after": before}
    idle = _wait_until(app, lambda: _active_background_task_count() == 0, timeout_ms=timeout_ms, step_ms=step_ms)
    after = _active_background_task_count()
    return {
        "status": "unavailable" if after is None else ("ok" if idle else "timeout"),
        "timeout_ms": int(timeout_ms),
        "active_before": before,
        "active_after": after,
    }


def _phase_task_result(status, timeout_ms, baseline, observed, active_before, active_after) -> dict:
    started = set(observed) - set(baseline)
    concurrent_startup = set(observed) & STARTUP_TASK_IDS
    owned = started - concurrent_startup
    remaining = set(active_after) & owned
    return {
        "status": status,
        "timeout_ms": int(timeout_ms),
        "ownership": "phase_started_task_ids",
        "task_id_diagnostics_available": True,
        "active_before": len(active_before),
        "active_after": len(active_after),
        "baseline_task_ids": sorted(baseline),
        "started_task_ids": sorted(started),
        "remaining_task_ids": sorted(remaining),
        "concurrent_startup_task_ids": sorted(concurrent_startup),
        "active_before_ids": sorted(active_before),
        "active_after_ids": sorted(active_after),
    }


def _wait_for_phase_background_tasks_idle(app, *, timeout_ms, step_ms, baseline, observed) -> dict:
    active_before = _active_background_task_ids()
    if active_before is None:
        return {
            "status": "unavailable",
            "timeout_ms": int(timeout_ms),
            "ownership": "phase_started_task_ids",
            "task_id_diagnostics_available": False,
            "baseline_task_ids": sorted(baseline),
            "started_task_ids": sorted(set(observed) - set(baseline)),
            "remaining_task_ids": [],
            "concurrent_startup_task_ids": [],
        }
    if int(timeout_ms) <= 0:
        return _phase_task_result("skipped", timeout_ms, baseline, observed, active_before, active_before)
    deadline = time.perf_counter() + int(timeout_ms) / 1000.0
    quiet_reads = 0
    active_after = active_before
    while time.perf_counter() < deadline:
        observed.update(active_after)
        owned = (set(observed) - set(baseline)) - STARTUP_TASK_IDS
        quiet_reads = quiet_reads + 1 if not (set(active_after) & owned) else 0
        if quiet_reads >= 2:
            return _phase_task_result("ok", timeout_ms, baseline, observed, active_before, active_after)
        remaining_ms = max(1, int((deadline - time.perf_counter()) * 1000.0))
        _settle(app, min(max(1, int(step_ms)), remaining_ms))
        active_after = _active_background_task_ids()
        if active_after is None:
            break
    active_after = active_after or ()
    return _phase_task_result("timeout", timeout_ms, baseline, observed, active_before, active_after)


def _wait_for_background_tasks_idle(
    app: QApplication,
    *,
    timeout_ms: int,
    step_ms: int = 50,
    baseline_task_ids: tuple[str, ...] | None = None,
    observed_task_ids: set[str] | None = None,
) -> dict:
    if baseline_task_ids is None or observed_task_ids is None:
        return _wait_for_global_background_tasks_idle(app, timeout_ms=timeout_ms, step_ms=step_ms)
    return _wait_for_phase_background_tasks_idle(
        app,
        timeout_ms=timeout_ms,
        step_ms=step_ms,
        baseline=set(baseline_task_ids),
        observed=observed_task_ids,
    )


def _shutdown_runtime_health(window: MainWindowQT) -> dict:
    try:
        runtime_health = collect_runtime_health(window)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}
    return runtime_health if isinstance(runtime_health, dict) else {}


def _dict_section(payload: dict, key: str) -> dict:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _kline_manager_shutdown_receipt() -> tuple[dict | None, bool]:
    try:
        diagnostics = kline_manager.shutdown_diagnostics
    except (AttributeError, RuntimeError, TypeError):
        return None, False
    if not isinstance(diagnostics, dict):
        return None, False
    return diagnostics, bool(diagnostics)


def _workspace_background_preload_shutdown_receipt(window: MainWindowQT) -> tuple[dict | None, bool]:
    workspace = getattr(window, "_workspace", None)
    reader = getattr(workspace, "background_preload_status", None)
    if not callable(reader):
        return None, False
    try:
        receipt = reader()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None, False
    return (dict(receipt), True) if isinstance(receipt, dict) else (None, False)


def _f5_runtime_artifact_receipt(window: MainWindowQT) -> tuple[dict | None, bool, str]:
    controller = getattr(window, "_f5_job_controller", None)
    installer = getattr(controller, "installer", None)
    repository = getattr(installer, "repository", None)
    cache_dir = getattr(installer, "cache_dir", CACHE_DIR)
    try:
        receipt = inspect_f5_runtime(cache_dir, repository=repository)
    except (AttributeError, OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
        return None, False, str(exc)
    return (receipt, True, "") if isinstance(receipt, dict) and receipt else (None, False, "invalid receipt")


def _collect_post_close_state(window: MainWindowQT) -> dict:
    runtime_health = _shutdown_runtime_health(window)
    webengine = _dict_section(runtime_health, "webengine")
    f5_refresh = _dict_section(runtime_health, "f5_refresh")
    task_count, task_available = _read_nonnegative_int_diagnostic(lambda: task_manager.active_count)
    pool_count, pool_available = _read_nonnegative_int_diagnostic(
        lambda: QThreadPool.globalInstance().activeThreadCount()
    )
    pending_count, pending_available = _read_nonnegative_int_diagnostic(pending_thread_count)
    watchdog_running, watchdog_available = _read_bool_diagnostic(
        lambda: getattr(window, "_process_watchdog").running
    )
    preload_shutdown, preload_shutdown_available = _workspace_background_preload_shutdown_receipt(window)
    kline_shutdown, kline_shutdown_available = _kline_manager_shutdown_receipt()
    f5_artifacts, f5_artifacts_available, f5_artifacts_error = _f5_runtime_artifact_receipt(window)
    return {
        "task_manager_diagnostics_available": task_available,
        "task_manager_active_count": task_count,
        "qthread_pool_diagnostics_available": pool_available,
        "qthread_pool_active_count": pool_count,
        "pending_qthread_diagnostics_available": pending_available,
        "pending_qthread_count": pending_count,
        "watchdog_diagnostics_available": watchdog_available,
        "watchdog_running": watchdog_running,
        "workspace_background_preload_diagnostics_available": preload_shutdown_available,
        "workspace_background_preload": preload_shutdown,
        "f5_controller_present": f5_refresh.get("job_controller_present"),
        "f5_controller_diagnostics_available": f5_refresh.get("job_controller_diagnostics_available"),
        "f5_controller_running": f5_refresh.get("job_controller_running"),
        "f5_runtime_artifacts_diagnostics_available": f5_artifacts_available,
        "f5_runtime_artifacts_diagnostics_error": f5_artifacts_error,
        "f5_runtime_artifacts": f5_artifacts,
        "webengine_available": webengine.get("available") is True,
        "webengine_child_count": webengine.get("count"),
        "kline_manager_shutdown_diagnostics_available": kline_shutdown_available,
        "kline_manager_shutdown_diagnostics": kline_shutdown,
    }


def _finalize_probe_window(window: MainWindowQT, app: QApplication) -> dict:
    close_started = time.perf_counter()
    try:
        window.close()
    except RuntimeError as exc:
        return {
            "close_elapsed_ms": round((time.perf_counter() - close_started) * 1000.0, 3),
            "error": str(exc),
        }

    shutdown = {
        "close_elapsed_ms": round((time.perf_counter() - close_started) * 1000.0, 3),
    }
    _settle(app, 200)
    _process_events(app, flush_deferred_deletes=True)
    pending_zero = pending_thread_count() == 0
    if not pending_zero:
        pending_zero = _wait_until(app, lambda: pending_thread_count() == 0, timeout_ms=5000)
    shutdown["pending_qthread_settle_ok"] = bool(pending_zero)
    shutdown["post_close"] = _collect_post_close_state(window)
    try:
        window.deleteLater()
        _settle(app, 100)
        _process_events(app, flush_deferred_deletes=True)
    except RuntimeError as exc:
        shutdown["cleanup_error"] = str(exc)
    return shutdown


def _reset_ui_stall_snapshot() -> bool:
    stall_probe = get_ui_stall_probe()
    if stall_probe is None:
        return False
    try:
        stall_probe.reset_stall_snapshot()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return True


def _capture_kline_open_ui_stalls(app: QApplication, *, reset_succeeded: bool) -> dict:
    """Flush one probe tick and freeze the actual-open-only stall receipt."""

    stall_probe = get_ui_stall_probe()
    if stall_probe is None:
        return {
            "installed": False,
            "scope": KLINE_OPEN_UI_STALL_SCOPE,
            "reset_succeeded": bool(reset_succeeded),
            "error": "stall_probe_not_installed",
        }
    try:
        settle_ms = max(50, int(getattr(stall_probe, "timer_interval_ms", 25) or 25) * 2)
        _settle(app, settle_ms)
        snapshot = stall_probe.stall_snapshot()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "installed": False,
            "scope": KLINE_OPEN_UI_STALL_SCOPE,
            "reset_succeeded": bool(reset_succeeded),
            "error": "stall_snapshot_capture_failed",
            "exception": str(exc),
        }
    return {
        **dict(snapshot),
        "scope": KLINE_OPEN_UI_STALL_SCOPE,
        "reset_succeeded": bool(reset_succeeded),
    }


def _begin_stall_phase(app: QApplication, *, phase: str, settle_ms: int) -> dict:
    """Flush prior probe work, then start a fresh stall-counting phase."""

    started = time.perf_counter()
    effective_settle_ms = max(0, int(settle_ms))
    _settle(app, effective_settle_ms)
    reset = _reset_ui_stall_snapshot()
    return {
        "phase": str(phase or "").strip(),
        "settle_ms": effective_settle_ms,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "stall_snapshot_reset": reset,
    }


def _tab_specs(workspace) -> list[dict]:
    tab_specs = getattr(workspace, "tab_specs", None)
    return list(tab_specs() or []) if callable(tab_specs) else []


def _read_background_preload_status(workspace) -> dict | None:
    reader = getattr(workspace, "background_preload_status", None)
    if not callable(reader):
        return None
    try:
        status = reader()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return dict(status) if isinstance(status, dict) else None


def _background_preload_is_active(status) -> bool:
    return isinstance(status, dict) and all(
        (
            status.get("enabled") is True,
            status.get("started") is True,
            status.get("finished") is not True,
        )
    )


def _record_preload_task_categories(observer, normalized: set[str]) -> None:
    observer._background_task_ids.update(normalized)
    observer._auto_refresh_task_ids.update(
        task_id for task_id in normalized if task_id.startswith("auto_refresh_")
    )
    observer._startup_task_ids.update(normalized & STARTUP_TASK_IDS)
    for task_id in normalized - observer._baseline_task_ids:
        try:
            category = task_registry.category_for(task_id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            observer._diagnostics_available = False
            continue
        category_text = str(getattr(category, "value", category) or "").strip().lower()
        if task_id == BACKGROUND_PRELOAD_FORBIDDEN_TASK_ID or (
            category_text in BACKGROUND_PRELOAD_FORBIDDEN_TASK_CATEGORIES
        ):
            observer._startup_network_task_ids.add(task_id)
            observer._startup_network_task_categories[task_id] = (
                category_text or "explicit_forbidden"
            )


class _BackgroundPreloadTaskObserver:
    """Observe global task overlap for the whole staged-preload window."""

    def __init__(self, workspace) -> None:
        self._workspace = workspace
        self._timer = QTimer()
        self._timer.setInterval(BACKGROUND_PRELOAD_TASK_OBSERVER_INTERVAL_MS)
        self._timer.timeout.connect(self.poll)
        active_before = _active_background_task_ids()
        initial_status = _read_background_preload_status(workspace)
        preload_already_active = _background_preload_is_active(initial_status)
        self._baseline_task_ids = set() if preload_already_active else set(active_before or ())
        self._diagnostics_available = active_before is not None
        self._preload_window_observed = False
        self._background_task_ids: set[str] = set()
        self._auto_refresh_task_ids: set[str] = set()
        self._startup_task_ids: set[str] = set()
        self._startup_network_task_ids: set[str] = set()
        self._startup_network_task_categories: dict[str, str] = {}

    def start(self) -> None:
        self.poll()
        self._timer.start()

    def stop(self) -> None:
        self.poll()
        self._timer.stop()

    def poll(self) -> None:
        status = _read_background_preload_status(self._workspace)
        if not _background_preload_is_active(status):
            return
        self._preload_window_observed = True
        active_task_ids = _active_background_task_ids()
        if active_task_ids is None:
            self._diagnostics_available = False
            return
        normalized = {str(task_id) for task_id in active_task_ids if str(task_id)}
        _record_preload_task_categories(self, normalized)

    def receipt(self) -> dict:
        return {
            "preload_task_window_observed": self._preload_window_observed,
            "auto_refresh_task_diagnostics_available": self._diagnostics_available,
            "auto_refresh_task_ids_observed": sorted(self._auto_refresh_task_ids),
            "startup_task_ids_observed": sorted(self._startup_task_ids),
            "background_task_ids_observed": sorted(self._background_task_ids),
            "preload_baseline_task_ids": sorted(self._baseline_task_ids),
            "startup_network_task_diagnostics_available": self._diagnostics_available,
            "startup_network_task_ids_observed": sorted(self._startup_network_task_ids),
            "startup_network_task_categories": dict(sorted(self._startup_network_task_categories.items())),
        }


def _preload_status_list(status: dict, field: str) -> list:
    value = status.get(field)
    return list(value) if isinstance(value, (list, tuple)) else []


def _preload_status_dict(status: dict, field: str) -> dict:
    value = status.get(field)
    return dict(value) if isinstance(value, dict) else {}


def _background_preload_contract(status: dict) -> dict[str, bool]:
    expected = list(BACKGROUND_PRELOAD_ORDER)
    planned = _preload_status_list(status, "planned_order")
    started = _preload_status_list(status, "start_order")
    completed = _preload_status_list(status, "completion_order")
    ready = _preload_status_list(status, "ready_keys")
    loaded = _preload_status_list(status, "loaded_keys")
    failures = _preload_status_dict(status, "failures")
    dependency_failures = _preload_status_dict(status, "dependency_failures")
    timeouts = _preload_status_list(status, "timeouts")
    cancellation_timeouts = _preload_status_dict(status, "cancellation_timeouts")
    cancellation_timeout_keys = _preload_status_list(status, "cancellation_timeout_keys")
    remaining = _preload_status_list(status, "remaining_keys")
    return {
        "enabled": status.get("enabled") is True,
        "started": status.get("started") is True,
        "finished": status.get("finished") is True,
        "planned_order_matches_registry": planned == expected,
        "start_order_matches_plan": started == expected,
        "completion_order_matches_plan": completed == expected,
        "all_tabs_ready": ready == expected,
        "all_tabs_loaded": all((len(loaded) == len(expected), set(loaded) == set(expected))),
        "planned_count_matches": status.get("planned_count") == len(expected),
        "loaded_count_matches": status.get("loaded_count") == len(expected),
        "single_step": status.get("max_concurrent_steps") == 1,
        "no_active_step_count": status.get("active_step_count") == 0,
        "no_active_step": not str(status.get("active_key", "")).strip(),
        "queue_empty": not remaining,
        "no_failures": not failures,
        "no_dependency_failures": not dependency_failures,
        "no_timeouts": not timeouts,
        "no_cancellation_timeouts": not any((cancellation_timeouts, cancellation_timeout_keys)),
        "cancellation_unblocked": status.get("cancellation_blocked") is False,
        "preload_timer_idle": status.get("timer_active") is False,
    }


def _background_preload_task_contract(receipt: dict) -> dict[str, bool]:
    return {
        "no_auto_refresh_tasks": bool(
            receipt.get("auto_refresh_task_diagnostics_available") is True
            and not receipt.get("auto_refresh_task_ids_observed")
        ),
        "no_global_startup_tasks": not receipt.get("startup_task_ids_observed"),
        "no_new_startup_or_network_tasks": bool(
            receipt.get("startup_network_task_diagnostics_available") is True
            and not receipt.get("startup_network_task_ids_observed")
        ),
    }


def _skipped_background_preload_receipt(expected: list[str]) -> dict:
    return {
        "status": "skipped",
        "reason": "background_prewarm_disabled",
        "expected_order": expected,
        "timeout_ms": 0,
        "elapsed_ms": 0.0,
        "auto_refresh_task_diagnostics_available": True,
        "auto_refresh_task_ids_observed": [],
        "preload_task_window_observed": False,
        "startup_task_ids_observed": [],
        "background_task_ids_observed": [],
        "preload_baseline_task_ids": [],
        "startup_network_task_diagnostics_available": True,
        "startup_network_task_ids_observed": [],
        "startup_network_task_categories": {},
    }


def _unavailable_background_preload_receipt(
    expected: list[str],
    timeout_ms: int,
    task_receipt: dict,
) -> dict:
    return {
        "status": "unavailable",
        "reason": "background_preload_diagnostics_unavailable",
        "expected_order": expected,
        "timeout_ms": timeout_ms,
        "elapsed_ms": 0.0,
        **task_receipt,
    }


def _completed_background_preload_receipt(
    final_status: dict,
    *,
    expected: list[str],
    timeout_ms: int,
    elapsed_ms: float,
    finished: bool,
    task_receipt: dict,
) -> dict:
    contract = _background_preload_contract(final_status)
    contract.update(_background_preload_task_contract(task_receipt))
    contract_ok = all(contract.values())
    status = "ok" if finished and contract_ok else ("failed" if finished else "timeout")
    return {
        **final_status,
        "status": status,
        "expected_order": expected,
        "timeout_ms": timeout_ms,
        "elapsed_ms": round(elapsed_ms, 3),
        **task_receipt,
        "contract": contract,
        "contract_ok": contract_ok,
    }


def _wait_for_background_preload(
    window,
    app: QApplication,
    *,
    enabled: bool,
    timeout_ms: int,
    task_observer: _BackgroundPreloadTaskObserver | None = None,
) -> dict:
    expected = list(BACKGROUND_PRELOAD_ORDER)
    effective_timeout_ms = max(1, int(timeout_ms))
    if not enabled:
        return _skipped_background_preload_receipt(expected)

    workspace = getattr(window, "_workspace", None)
    observer = task_observer or _BackgroundPreloadTaskObserver(workspace)
    observer.poll()
    initial = _read_background_preload_status(workspace)
    if initial is None:
        return _unavailable_background_preload_receipt(
            expected,
            effective_timeout_ms,
            observer.receipt(),
        )

    latest = {"value": initial}
    wait_started_at = time.perf_counter()

    def _finished() -> bool:
        observer.poll()
        current = _read_background_preload_status(workspace)
        if current is not None:
            latest["value"] = current
        return latest["value"].get("finished") is True

    finished = _wait_until(app, _finished, timeout_ms=effective_timeout_ms, step_ms=50)
    observer.poll()
    return _completed_background_preload_receipt(
        latest["value"],
        expected=expected,
        timeout_ms=effective_timeout_ms,
        elapsed_ms=(time.perf_counter() - wait_started_at) * 1000.0,
        finished=finished,
        task_receipt=observer.receipt(),
    )


def _tab_index(workspace, key: str) -> int:
    key_text = str(key or "").strip()
    for index, spec in enumerate(_tab_specs(workspace)):
        if str(spec.get("key") or "").strip() == key_text:
            return index
    return -1


def _loaded_tab(workspace, key: str):
    getter = getattr(workspace, "get_loaded_tab", None)
    return getter(key) if callable(getter) else None


def _should_defer_probe_tab_load(workspace, key: str, *, reason: str = "perf_memory_probe") -> bool:
    should_defer = getattr(workspace, "should_defer_probe_tab_load", None)
    if not callable(should_defer):
        return False
    try:
        return bool(should_defer(key, reason=reason))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _measurement_phase(label: str) -> str:
    normalized = str(label or "").strip()
    if normalized.startswith("idle"):
        return "idle"
    return {
        "startup": "startup",
        "after_background_preload": "background_preload",
        "after_tab_cycle": "tab_cycle",
        "after_tab_async_tail": "tab_async_tail",
        "after_f5_cycle": "f5_cycle",
        "after_quote_cycle": "quote_cycle",
        "final": "kline_cycle",
    }.get(normalized, normalized)


def _sample(
    window: MainWindowQT,
    *,
    label: str,
    samples: list[dict],
    exported_paths: list[str],
    export_each_sample: bool,
    sample_output_dir: Path | None = None,
) -> dict:
    sample_started = time.perf_counter()
    report = collect_runtime_health(window)
    report["label"] = label
    report["measurement_phase"] = _measurement_phase(label)
    report["sample_collect_elapsed_ms"] = round((time.perf_counter() - sample_started) * 1000.0, 3)
    samples.append(report)
    path = None
    if export_each_sample:
        if sample_output_dir is not None:
            sample_output_dir.mkdir(parents=True, exist_ok=True)
            safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label)
            path = sample_output_dir / f"runtime_health_sample_{len(samples):04d}_{safe_label}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path = export_runtime_health_report(
                window,
                project_root=PROJECT_ROOT,
                report=report,
            )
    report["sample_elapsed_ms"] = round((time.perf_counter() - sample_started) * 1000.0, 3)
    report["sample_export_elapsed_ms"] = round(
        max(0.0, report["sample_elapsed_ms"] - report["sample_collect_elapsed_ms"]),
        3,
    )
    if path is not None:
        Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        exported_paths.append(str(path))
    return report


def _runtime_health_sample_summary(label: str, window: MainWindowQT) -> dict:
    report = collect_runtime_health_summary(window)
    process = report.get("process") or {}
    webengine = report.get("webengine") or {}
    background_tasks = report.get("background_tasks") or {}
    timers = report.get("timers") or {}
    event_bus = report.get("event_bus") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "private_mb": process.get("private_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_available": webengine.get("available") is True,
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
        "event_receiver_count": event_bus.get("total_receivers"),
    }


def _trend_one(values: list[float]) -> dict:
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


def _tail_range(values: list[float], tail_count: int = 3) -> float:
    tail = values[-max(1, int(tail_count)) :]
    return round(max(tail) - min(tail), 3) if tail else 0.0


def _as_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_finite_float(value) -> float | None:
    parsed = _as_float(value)
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _startup_event_completion_times(
    window: MainWindowQT,
    phases: dict,
    *,
    window_probe_started_at: float,
) -> tuple[float | None, float | None]:
    launch_started_at = _nonnegative_finite_float(getattr(window, "_launch_started_at", None))
    first_paint_ms = _nonnegative_finite_float(phases.get("first_paint_ms"))
    first_paint_origin = (
        launch_started_at
        if phases.get("first_paint_recorded") is True and launch_started_at is not None
        else window_probe_started_at
    )
    first_paint_completed_at = (
        first_paint_origin + first_paint_ms / 1000.0 if first_paint_ms is not None else None
    )

    initial_tab_completed_at = None
    initial_tab_ready_ms = _nonnegative_finite_float(phases.get("initial_tab_ready_ms"))
    if phases.get("initial_tab_ready") is True and initial_tab_ready_ms is not None:
        workspace = getattr(window, "_workspace", None)
        recorded_ready_ms = _nonnegative_finite_float(
            getattr(workspace, "_initial_tab_ready_elapsed_ms", None)
        )
        if recorded_ready_ms is not None and recorded_ready_ms > 0 and launch_started_at is not None:
            initial_tab_completed_at = launch_started_at + recorded_ready_ms / 1000.0
        else:
            initial_tab_completed_at = window_probe_started_at + initial_tab_ready_ms / 1000.0
    return first_paint_completed_at, initial_tab_completed_at


def _elapsed_ms(started_at: float, completed_at: float | None) -> float | None:
    started = _nonnegative_finite_float(started_at)
    completed = _nonnegative_finite_float(completed_at)
    if started is None or completed is None or completed < started:
        return None
    return round((completed - started) * 1000.0, 3)


def _build_startup_timing(
    window: MainWindowQT,
    phases: dict,
    *,
    window_probe_started_at: float,
    application_initialization_started_at: float,
    script_module_started_at: float,
) -> dict:
    first_paint_completed_at, initial_tab_completed_at = _startup_event_completion_times(
        window,
        phases,
        window_probe_started_at=window_probe_started_at,
    )
    return {
        "scope": {
            "clock": "time.perf_counter",
            "script_module_origin": STARTUP_TIMING_MODULE_ORIGIN,
            "application_origin": STARTUP_TIMING_APP_ORIGIN,
            "includes_python_interpreter_startup": False,
            "includes_process_creation": False,
            "includes_qt_runtime_configuration": True,
            "includes_qapplication_initialization": True,
            "includes_native_dataframe_runtime_initialization": True,
            "includes_search_filter_runtime_initialization": True,
            "window_only_metrics_preserved": True,
            "excluded_before_module_marker": [
                "process_creation",
                "python_interpreter_startup",
                "time_module_import",
            ],
        },
        "script_module_inclusive": {
            "first_paint_ms": _elapsed_ms(script_module_started_at, first_paint_completed_at),
            "initial_tab_ready_ms": _elapsed_ms(script_module_started_at, initial_tab_completed_at),
        },
        "application_initialization_inclusive": {
            "first_paint_ms": _elapsed_ms(application_initialization_started_at, first_paint_completed_at),
            "initial_tab_ready_ms": _elapsed_ms(
                application_initialization_started_at,
                initial_tab_completed_at,
            ),
        },
        "window_only": {
            "first_paint_ms": phases.get("first_paint_ms"),
            "initial_tab_ready_ms": phases.get("initial_tab_ready_ms"),
        },
    }


def _runtime_summary_from_health(sample: dict, label: str) -> dict:
    process = sample.get("process") or {}
    timers = sample.get("timers") or {}
    event_bus = sample.get("event_bus") or {}
    webengine = sample.get("webengine") or {}
    background_tasks = sample.get("background_tasks") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "private_mb": process.get("private_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_available": webengine.get("available") is True,
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
        "event_receiver_count": event_bus.get("total_receivers"),
    }


def _build_budget_trend(samples: list[dict], kline_cycle: dict | None) -> dict:
    trend = build_runtime_health_trend(samples)
    cycle_samples = list((kline_cycle or {}).get("cycle_samples") or [])
    close_samples = [sample for sample in cycle_samples if str(sample.get("label") or "").endswith(":after_close")]
    if close_samples and samples:
        return _overlay_budget_trend(
            trend,
            close_samples + [_runtime_summary_from_health(samples[-1], "final_runtime_health")],
            basis="post_kline_close_samples",
        )

    tail_samples = _post_workload_tail_summaries(samples)
    if len(tail_samples) < 2:
        return trend
    return _overlay_budget_trend(trend, tail_samples, basis="tail_runtime_health_samples")


def _stable_tail_summaries(samples: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for sample in samples:
        label = str(sample.get("label") or "")
        if label in STABLE_TAIL_SAMPLE_LABELS:
            selected.append(_runtime_summary_from_health(sample, label))
    return selected


def _tail_after_kline_prewarm(selected: list[dict]) -> list[dict]:
    for index, sample in enumerate(selected):
        if sample.get("label") != "after_kline_prewarm":
            continue
        tail = selected[index:]
        return tail[-3:] if len(tail) >= 2 else []
    return []


def _post_workload_tail_summaries(samples: list[dict]) -> list[dict]:
    selected = _stable_tail_summaries(samples)
    prewarm_tail = _tail_after_kline_prewarm(selected)
    if prewarm_tail:
        return prewarm_tail
    if len(selected) >= 2:
        return selected[-3:]
    return [
        _runtime_summary_from_health(sample, str(sample.get("label") or "runtime_health")) for sample in samples[-3:]
    ]


def _overlay_budget_trend(trend: dict, summary_samples: list[dict], *, basis: str) -> dict:
    def _values(key: str) -> list[float]:
        values: list[float] = []
        for sample in summary_samples:
            value = _as_float(sample.get(key))
            if value is not None:
                values.append(value)
        return values

    trend = dict(trend)
    for output_key, sample_key in (
        ("rss_mb", "rss_mb"),
        ("private_mb", "private_mb"),
        ("background_tasks", "background_task_count"),
        ("threads", "thread_count"),
        ("active_timers", "active_timer_count"),
        ("total_timers", "total_timer_count"),
        ("event_receivers", "event_receiver_count"),
        ("webengine_children", "webengine_child_count"),
        ("webengine_rss_mb", "webengine_rss_mb"),
        ("webengine_private_mb", "webengine_private_mb"),
    ):
        values = _values(sample_key)
        stats = _trend_one(values)
        if output_key in {"rss_mb", "private_mb"}:
            stats["tail_range"] = _tail_range(values)
        trend[output_key] = {
            **stats,
            "basis": basis,
        }
    return trend


def _tab_runtime_probe_ready(tab) -> bool:
    if tab is None:
        return False
    readiness_reader = getattr(tab, "is_runtime_probe_ready", None)
    if callable(readiness_reader):
        try:
            return bool(readiness_reader())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
    return getattr(tab, "_initial_data_loading", False) is not True


def _observe_active_task_ids(observed_task_ids: set[str]) -> bool:
    active_ids = _active_background_task_ids()
    if active_ids is None:
        return False
    observed_task_ids.update(active_ids)
    return True


def _cycle_one_tab(
    workspace, tab_widget, app, key: str, cycle: int, settle_ms: int, observed_task_ids: set[str]
) -> tuple[dict, bool]:
    index = _tab_index(workspace, key)
    if index < 0:
        return {"cycle": cycle, "key": key, "status": "missing", "elapsed_ms": 0.0}, False
    if _should_defer_probe_tab_load(workspace, key, reason="perf_memory_probe"):
        return {
            "cycle": cycle,
            "key": key,
            "status": "skipped_controlled_probe",
            "elapsed_ms": 0.0,
            "reason": "controlled_startup_probe_deferred",
        }, False

    started = time.perf_counter()
    _observe_active_task_ids(observed_task_ids)
    loaded_before = _loaded_tab(workspace, key) is not None
    activate_tab = getattr(workspace, "activate_tab", None)
    if callable(activate_tab):
        activate_tab(index, reason="shell_nav")
    else:
        tab_widget.setCurrentIndex(index)
    _observe_active_task_ids(observed_task_ids)
    activation_request_ms = (time.perf_counter() - started) * 1000.0
    ready = _wait_until(
        app,
        lambda: _tab_runtime_probe_ready(_loaded_tab(workspace, key)),
        timeout_ms=2000,
    )
    _observe_active_task_ids(observed_task_ids)
    loaded = _loaded_tab(workspace, key) is not None
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    _settle(app, settle_ms)
    _observe_active_task_ids(observed_task_ids)
    interaction_to_stable_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return {
        "cycle": cycle,
        "key": key,
        "status": "ok" if ready else ("data_timeout" if loaded else "timeout"),
        "elapsed_ms": elapsed_ms,
        "interaction_to_stable_ms": interaction_to_stable_ms,
        "activation_request_ms": round(activation_request_ms, 3),
        "load_wait_ms": round(max(0.0, elapsed_ms - activation_request_ms), 3),
        "settle_ms": int(settle_ms),
        "settle_elapsed_ms": round(max(0.0, interaction_to_stable_ms - elapsed_ms), 3),
        "loaded_before": loaded_before,
        "loaded_after": loaded,
        "runtime_probe_ready": ready,
    }, True


def _cycle_tab_with_evidence(
    workspace,
    tab_widget,
    app,
    key: str,
    cycle: int,
    settle_ms: int,
    observed_task_ids: set[str],
    evidence: _SuiteEvidence | None,
) -> tuple[dict, bool]:
    _record_evidence(evidence, "tab_start", key, cycle)
    try:
        timing, counted = _cycle_one_tab(
            workspace, tab_widget, app, key, cycle, settle_ms, observed_task_ids
        )
    except Exception as exc:
        _record_evidence(evidence, "error", exc)
        raise
    _record_evidence(evidence, "tab_end", key, cycle, timing)
    return timing, counted


def _cycle_tabs(
    window: MainWindowQT,
    app: QApplication,
    tabs: tuple[str, ...],
    *,
    cycles: int,
    settle_ms: int,
    observed_task_ids: set[str] | None = None,
    evidence: _SuiteEvidence | None = None,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    tab_widget = getattr(workspace, "tabs", None)
    if workspace is None or tab_widget is None:
        return {"status": "skipped", "reason": "workspace_unavailable", "visited": 0}

    timings: list[dict] = []
    visited = 0
    observed = observed_task_ids if observed_task_ids is not None else set()
    diagnostics_available = _observe_active_task_ids(observed)
    for cycle_index in range(max(0, int(cycles))):
        for key in tabs:
            cycle_number = cycle_index + 1
            timing, counted = _cycle_tab_with_evidence(
                workspace,
                tab_widget,
                app,
                key,
                cycle_number,
                settle_ms,
                observed,
                evidence,
            )
            timings.append(timing)
            visited += int(counted)
    accepted_statuses = {"ok", "skipped_controlled_probe"}
    status = "ok" if all(item.get("status") in accepted_statuses for item in timings) else "timeout"
    return {
        "status": status,
        "cycles": int(cycles),
        "visited": visited,
        "tabs": timings,
        "observed_task_ids": sorted(observed),
        "task_id_diagnostics_available": diagnostics_available,
    }


def _f5_event_phases(controller) -> tuple[list[str], int]:
    request = getattr(controller, "last_request", None)
    if request is None:
        return [], 0
    try:
        from infra.storage.f5_job_repository import F5JobRepository

        payloads, _offset = F5JobRepository(request.job_dir).read_events()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return [], 0
    phases = [str(payload.get("phase") or "").strip() for payload in payloads if isinstance(payload, dict)]
    return list(dict.fromkeys(phase for phase in phases if phase)), len(payloads)


def _real_f5_controller_started(window, previous_result) -> bool:
    controller = getattr(window, "_f5_job_controller", None)
    if controller is None:
        return False
    return bool(getattr(controller, "is_running", False)) or getattr(controller, "last_result", None) is not previous_result


def _real_f5_controller_finished(window, previous_result) -> bool:
    controller = getattr(window, "_f5_job_controller", None)
    if controller is None or bool(getattr(controller, "is_running", False)):
        return False
    result = getattr(controller, "last_result", None)
    return result is not None and result is not previous_result


def _wait_for_real_f5_completion(
    window,
    app: QApplication,
    previous_result,
    *,
    started_at: float,
    timeout_ms: int,
) -> tuple[bool, bool, object]:
    start_timeout_ms = min(max(1, int(timeout_ms)), 45_000)
    started = _wait_until(
        app,
        lambda: _real_f5_controller_started(window, previous_result),
        timeout_ms=start_timeout_ms,
        step_ms=50,
    )
    remaining_ms = max(1, int(timeout_ms - (time.perf_counter() - started_at) * 1000.0))
    finished = False
    if started:
        finished = _wait_until(
            app,
            lambda: _real_f5_controller_finished(window, previous_result),
            timeout_ms=remaining_ms,
            step_ms=100,
        )
    controller = getattr(window, "_f5_job_controller", None)
    return bool(started), bool(finished), controller


def _wait_for_real_f5_post_refresh(window, app: QApplication, *, finished: bool) -> bool:
    if not finished:
        return False
    return _wait_until(
        app,
        lambda: getattr(
            getattr(getattr(window, "_workspace", None), "_f5_refresh_scheduler", None),
            "is_running",
            lambda: False,
        )()
        is False,
        timeout_ms=6000,
    )


def _real_f5_succeeded(*, finished: bool, job_status: str, worker_pid, scheduler_settled: bool) -> bool:
    if not finished:
        return False
    if job_status != "succeeded":
        return False
    if not isinstance(worker_pid, int):
        return False
    if worker_pid <= 0:
        return False
    if worker_pid == os.getpid():
        return False
    return bool(scheduler_settled)


def _value_or_default(value, default):
    return value if value else default


def _build_real_f5_timing(
    result,
    *,
    cycle: int,
    started_at: float,
    started: bool,
    finished: bool,
    scheduler_settled: bool,
    worker_pid,
    event_phases: list[str],
    event_count: int,
    job_status: str,
    succeeded: bool,
) -> dict:
    artifacts = getattr(result, "artifacts", None)
    return {
        "cycle": cycle,
        "status": "ok" if succeeded else ("failed" if finished else "timeout"),
        "execution": "real_process",
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
        "started": bool(started),
        "finished": bool(finished),
        "post_refresh_settled": bool(scheduler_settled),
        "parent_pid": os.getpid(),
        "worker_pid": worker_pid,
        "run_id": str(_value_or_default(getattr(result, "run_id", ""), "")),
        "job_status": job_status,
        "requested_date": str(_value_or_default(getattr(result, "requested_date", ""), "")),
        "effective_trade_date": str(_value_or_default(getattr(result, "effective_trade_date", ""), "")),
        "snapshot_id": str(_value_or_default(getattr(artifacts, "snapshot_id", ""), "")),
        "symbol_count": int(_value_or_default(getattr(result, "symbol_count", 0), 0)),
        "rps_valid_count": int(_value_or_default(getattr(result, "rps_valid_count", 0), 0)),
        "sector_count": int(_value_or_default(getattr(result, "sector_count", 0), 0)),
        "job_elapsed_seconds": round(
            float(_value_or_default(getattr(result, "elapsed_seconds", 0.0), 0.0)), 3
        ),
        "event_count": event_count,
        "event_phases": event_phases,
        "error_code": str(_value_or_default(getattr(result, "error_code", ""), "")),
        "error_message": str(_value_or_default(getattr(result, "error_message", ""), "")),
    }


def _cycle_real_f5_once(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycle: int,
    settle_ms: int,
    timeout_ms: int,
) -> dict:
    started_at = time.perf_counter()
    previous_controller = getattr(window, "_f5_job_controller", None)
    previous_result = getattr(previous_controller, "last_result", None)
    start_f5_precompute(window)
    started, finished, controller = _wait_for_real_f5_completion(
        window,
        app,
        previous_result,
        started_at=started_at,
        timeout_ms=timeout_ms,
    )
    if not finished and controller is not None and bool(getattr(controller, "is_running", False)):
        controller.cancel("runtime_probe_timeout")
    result = getattr(controller, "last_result", None) if finished else None
    job_status = str(_value_or_default(getattr(getattr(result, "status", None), "value", ""), ""))
    worker_pid = getattr(controller, "last_worker_pid", None)
    event_phases, event_count = _f5_event_phases(controller)
    scheduler_settled = _wait_for_real_f5_post_refresh(window, app, finished=finished)
    _settle(app, settle_ms)
    succeeded = _real_f5_succeeded(
        finished=finished,
        job_status=job_status,
        worker_pid=worker_pid,
        scheduler_settled=scheduler_settled,
    )
    return _build_real_f5_timing(
        result,
        cycle=cycle,
        started_at=started_at,
        started=started,
        finished=finished,
        scheduler_settled=scheduler_settled,
        worker_pid=worker_pid,
        event_phases=event_phases,
        event_count=event_count,
        job_status=job_status,
        succeeded=succeeded,
    )


def _cycle_post_refresh_f5_once(workspace, window, app: QApplication, *, cycle: int, settle_ms: int) -> dict:
    started_at = time.perf_counter()
    finish_f5_reload(window, count=123, elapsed=1.23, event_bus=event_bus)
    settled = _wait_until(
        app,
        lambda: getattr(getattr(workspace, "_f5_refresh_scheduler", None), "is_running", lambda: False)() is False,
        timeout_ms=6000,
    )
    _settle(app, settle_ms)
    return {
        "cycle": cycle,
        "status": "ok" if settled else "timeout",
        "execution": "post_refresh_callback",
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
    }


def _f5_cycle_status(timings: list[dict]) -> str:
    if all(item.get("status") == "ok" for item in timings):
        return "ok"
    if any(item.get("status") == "timeout" for item in timings):
        return "timeout"
    return "failed"


def _cycle_f5(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycles: int,
    settle_ms: int,
    real: bool = False,
    timeout_ms: int = REAL_F5_TIMEOUT_SECONDS * 1000,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    timings: list[dict] = []
    for cycle in range(1, max(0, int(cycles)) + 1):
        if real:
            timing = _cycle_real_f5_once(
                window,
                app,
                cycle=cycle,
                settle_ms=settle_ms,
                timeout_ms=timeout_ms,
            )
        else:
            timing = _cycle_post_refresh_f5_once(
                workspace,
                window,
                app,
                cycle=cycle,
                settle_ms=settle_ms,
            )
        timings.append(timing)
    total_elapsed_ms = round(sum(float(item.get("elapsed_ms") or 0.0) for item in timings), 3)
    return {
        "status": _f5_cycle_status(timings),
        "probe_mode": "real_process" if real else "post_refresh_callback",
        "cycles": len(timings),
        "total_elapsed_ms": total_elapsed_ms,
        "cycle_timings": timings,
    }


def _central_quote_cycle_idle(central) -> bool:
    lifecycle = getattr(central, "_task_lifecycle", None)
    active_names = getattr(lifecycle, "active_names", ())
    return not any(
        (
            bool(getattr(central, "_is_fetching", False)),
            bool(getattr(central, "_off_market_snapshot_fetching", False)),
            bool(str(getattr(central, "_pending_fetch_reason", "") or "")),
            bool(active_names),
        )
    )


def _cycle_quotes(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int) -> dict:
    if int(cycles) <= 0:
        return {"status": "ok", "cycles": 0, "cycle_timings": []}
    central = getattr(window, "central_quotes_svc", None)
    trigger_fetch = getattr(central, "_trigger_fetch", None)
    if not callable(trigger_fetch):
        return {"status": "skipped", "reason": "central_quotes_unavailable", "cycles": 0}

    completed = 0
    timings: list[dict] = []
    for _ in range(max(0, int(cycles))):
        started = time.perf_counter()
        trigger_fetch()
        settled = _wait_until(
            app,
            lambda: _central_quote_cycle_idle(central),
            timeout_ms=5000,
        )
        _settle(app, settle_ms)
        completed += 1
        timings.append(
            {
                "cycle": completed,
                "status": "ok" if settled else "timeout",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        )
    status = "ok" if all(item.get("status") == "ok" for item in timings) else "timeout"
    return {"status": status, "cycles": completed, "cycle_timings": timings}


def _kline_browser_ready(chart) -> bool:
    try:
        return bool(chart.isVisible()) and getattr(chart, "browser", None) is not None
    except (AttributeError, RuntimeError, TypeError):
        return False


def _kline_stage_diagnostics(chart) -> dict:
    stages = getattr(chart, "_open_stages", None)
    diagnostics = getattr(stages, "stage_diagnostics", None)
    if not callable(diagnostics):
        return {}
    try:
        result = diagnostics()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _kline_chart_ready(chart) -> bool:
    if not _kline_browser_ready(chart):
        return False
    stages = getattr(chart, "_open_stages", None)
    recorded = set(getattr(stages, "recorded_stages", ()) or ())
    return "chart_ready" in recorded


def _kline_stage_contract_ready(chart) -> bool:
    diagnostics = _kline_stage_diagnostics(chart)
    return bool(
        diagnostics.get("complete") is True
        and diagnostics.get("required_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("completed_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("pending_stages") == []
    )


def _trigger_kline_first_interaction(chart) -> bool:
    browser = getattr(chart, "browser", None)
    if browser is None:
        return False
    try:
        from PyQt6.QtTest import QTest

        browser.setFocus()
        QTest.mouseClick(browser, Qt.MouseButton.LeftButton, pos=browser.rect().center())
        return True
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return False


def _kline_chart_closed(chart) -> bool:
    try:
        physically_released = getattr(chart, "browser", None) is None
        pooled_lease_closed = bool(
            getattr(chart, "_closing", False)
            and getattr(chart, "_pool_idle", False)
        )
        return not bool(chart.isVisible()) and (physically_released or pooled_lease_closed)
    except RuntimeError:
        return True
    except (AttributeError, TypeError):
        return False


def _kline_keeper_snapshot(kline_manager) -> dict:
    return {
        "keeper_count": int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0),
        "keeper_ready": bool(getattr(kline_manager, "managed_webengine_keeper_ready", False)),
        "prewarm_failure": str(getattr(kline_manager, "_prewarm_failure", "") or ""),
        "prewarm_started": bool(getattr(kline_manager, "_prewarm_started", False)),
        "prewarm_cancelled": bool(getattr(kline_manager, "_prewarm_cancelled", False)),
        "preflight_running": bool(getattr(kline_manager, "_webengine_preflight_started", False)),
        "preflight_available": getattr(kline_manager, "_webengine_available", None),
    }


def _stabilize_kline_prewarm(app: QApplication, kline_manager, *, requested: bool) -> dict:
    before = _kline_keeper_snapshot(kline_manager)
    ready = before["keeper_count"] == 1 and before["keeper_ready"] is True
    if requested and not ready:
        ready = _wait_until(
            app,
            lambda: (
                _kline_keeper_snapshot(kline_manager)["keeper_count"] == 1
                and _kline_keeper_snapshot(kline_manager)["keeper_ready"] is True
            ),
            timeout_ms=KLINE_PREWARM_STABLE_TIMEOUT_MS,
            step_ms=50,
        )
    after = _kline_keeper_snapshot(kline_manager)
    return {
        "requested": bool(requested),
        "status": "ok" if (not requested or ready) else "timeout",
        "timeout_ms": KLINE_PREWARM_STABLE_TIMEOUT_MS if requested else 0,
        "before": before,
        "after": after,
    }


def _valid_webengine_count(sample: dict) -> int | None:
    if sample.get("webengine_available") is not True:
        return None
    value = sample.get("webengine_child_count")
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _close_kline_charts(app: QApplication) -> int:
    from ui.components.kline_window_manager import kline_manager

    charts = list(getattr(kline_manager, "_charts", []) or [])
    closed = 0
    for chart in charts:
        try:
            chart.close()
        except RuntimeError:
            pass
    _process_events(app, rounds=12, sleep_ms=20, flush_deferred_deletes=True)
    closed = sum(_kline_chart_closed(chart) for chart in charts)
    with suppress(AttributeError, RuntimeError, TypeError):
        kline_manager._charts = [chart for chart in kline_manager._charts if not _kline_chart_closed(chart)]
    return closed


def _kline_cycle_sample(label: str, window: MainWindowQT, kline_manager) -> dict:
    sample = _runtime_health_sample_summary(label, window)
    sample["managed_webengine_keeper_count"] = int(
        getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0
    )
    return sample


def _disabled_kline_cycle(prewarm: dict, kline_manager) -> dict:
    keeper_count = int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0)
    return {
        "status": "skipped",
        "reason": "disabled",
        "cycles": 0,
        "prewarm": prewarm,
        "baseline_managed_webengine_keeper_count": keeper_count,
        "final_managed_webengine_keeper_count": keeper_count,
        "managed_webengine_keeper_count": keeper_count,
    }


def _open_kline_cycle_chart(
    window: MainWindowQT,
    app: QApplication,
    kline_manager,
    *,
    cycle_number: int,
    settle_ms: int,
    code: str,
    name: str,
) -> tuple[object, bool, bool, dict, dict]:
    stall_boundary = _begin_stall_phase(
        app,
        phase=f"kline_cycle_{cycle_number}:actual_open",
        settle_ms=settle_ms,
    )
    chart = kline_manager.open_chart(
        window, code, name,
        getattr(window, "data_provider", None),
        {"代码": code, "名称": name},
        [{"代码": code, "名称": name}],
        0,
    )
    browser_ready = bool(
        chart is not None
        and _wait_until(app, lambda: _kline_browser_ready(chart), timeout_ms=max(1000, settle_ms))
    )
    chart_ready = bool(
        browser_ready
        and _wait_until(app, lambda: _kline_chart_ready(chart), timeout_ms=max(1000, settle_ms))
    )
    ui_stalls = _capture_kline_open_ui_stalls(
        app,
        reset_succeeded=stall_boundary.get("stall_snapshot_reset") is True,
    )
    return chart, browser_ready, chart_ready, stall_boundary, ui_stalls


def _complete_kline_stage_contract(app: QApplication, chart, *, chart_ready: bool, settle_ms: int) -> bool:
    if chart_ready:
        _trigger_kline_first_interaction(chart)
    stage_contract_complete = bool(
        chart_ready
        and _wait_until(app, lambda: _kline_stage_contract_ready(chart), timeout_ms=max(1000, settle_ms))
    )
    if not chart_ready:
        _settle(app, settle_ms)
    return stage_contract_complete


def _run_single_kline_cycle(
    window: MainWindowQT,
    app: QApplication,
    kline_manager,
    *,
    cycle_number: int,
    settle_ms: int,
    code: str,
    name: str,
) -> dict:
    samples = [_kline_cycle_sample(f"kline_cycle_{cycle_number}:before_open", window, kline_manager)]
    chart, browser_ready, chart_ready, stall_boundary, ui_stalls = _open_kline_cycle_chart(
        window,
        app,
        kline_manager,
        cycle_number=cycle_number,
        settle_ms=settle_ms,
        code=code,
        name=name,
    )
    stage_contract_complete = _complete_kline_stage_contract(
        app,
        chart,
        chart_ready=chart_ready,
        settle_ms=settle_ms,
    )
    samples.append(_kline_cycle_sample(f"kline_cycle_{cycle_number}:after_open", window, kline_manager))
    stage_diagnostics = _kline_stage_diagnostics(chart) if chart is not None else {}
    context_diagnostics = dict(getattr(chart, "_context_diagnostics", None) or {}) if chart is not None else {}
    closed = _close_kline_charts(app)
    _settle(app, settle_ms)
    samples.append(_kline_cycle_sample(f"kline_cycle_{cycle_number}:after_close", window, kline_manager))
    return {
        "cycle_index": int(cycle_number),
        "opened": int(chart_ready),
        "closed": closed,
        "blocked": int(not chart_ready),
        "browser_ready": browser_ready,
        "chart_ready": chart_ready,
        "stage_contract_complete": stage_contract_complete,
        "stage_diagnostics": stage_diagnostics,
        "context_diagnostics": context_diagnostics,
        "ui_stall_boundary": stall_boundary,
        "ui_stalls": ui_stalls,
        "samples": samples,
    }


def _flatten_kline_cycle_samples(results: list[dict]) -> list[dict]:
    samples = []
    for result in results:
        samples.extend(result["samples"])
    return samples


def _sum_kline_cycle_field(results: list[dict], field: str) -> int:
    return sum(int(result[field]) for result in results)


def _kline_cycle_dicts(results: list[dict], field: str) -> list[dict]:
    return [result.get(field) or {} for result in results]


def _kline_open_stall_records(results: list[dict]) -> list[dict]:
    return [
        {
            "cycle_index": result.get("cycle_index"),
            "ui_stalls": result.get("ui_stalls") or {},
        }
        for result in results
    ]


def _combine_kline_cycle_results(results: list[dict]) -> dict:
    return {
        "samples": _flatten_kline_cycle_samples(results),
        "opened": _sum_kline_cycle_field(results, "opened"),
        "closed": _sum_kline_cycle_field(results, "closed"),
        "blocked": _sum_kline_cycle_field(results, "blocked"),
        "stage_diagnostics": _kline_cycle_dicts(results, "stage_diagnostics"),
        "context_diagnostics": _kline_cycle_dicts(results, "context_diagnostics"),
        "open_ui_stalls": _kline_open_stall_records(results),
    }


def _valid_kline_stage_timing(value) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _valid_kline_stage_timings(timings) -> bool:
    if not isinstance(timings, dict) or set(timings) != set(KLINE_OPEN_STAGE_ORDER):
        return False
    values = [timings.get(stage) for stage in KLINE_OPEN_STAGE_ORDER]
    return all(_valid_kline_stage_timing(value) for value in values) and all(
        float(left) <= float(right) for left, right in pairwise(values)
    )


def _valid_kline_stage_diagnostics(diagnostics: dict) -> bool:
    if not isinstance(diagnostics, dict):
        return False
    timings = diagnostics.get("timings_ms")
    return bool(
        diagnostics.get("required_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("completed_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("pending_stages") == []
        and diagnostics.get("complete") is True
        and _valid_kline_stage_timings(timings)
    )


def _kline_cycle_boundaries(cycle_samples: list[dict]) -> dict:
    baseline_sample = cycle_samples[0] if cycle_samples else {}
    final_sample = cycle_samples[-1] if cycle_samples else {}
    baseline_count = _valid_webengine_count(baseline_sample)
    final_count = _valid_webengine_count(final_sample)
    net_delta = final_count - baseline_count if baseline_count is not None and final_count is not None else None
    return {
        "baseline_sample": baseline_sample,
        "final_sample": final_sample,
        "baseline_count": baseline_count,
        "final_count": final_count,
        "net_delta": net_delta,
        "baseline_keeper_count": int(baseline_sample.get("managed_webengine_keeper_count") or 0),
        "final_keeper_count": int(final_sample.get("managed_webengine_keeper_count") or 0),
    }


def _build_kline_cycle_result(cycles: int, prewarm: dict, results: list[dict], kline_manager) -> dict:
    combined = _combine_kline_cycle_results(results)
    boundaries = _kline_cycle_boundaries(combined["samples"])
    stage_cycles = combined["stage_diagnostics"]
    stage_complete = len(stage_cycles) == int(cycles) and all(
        _valid_kline_stage_diagnostics(item) for item in stage_cycles
    )
    cycle_complete = (
        combined["opened"] == int(cycles)
        and combined["closed"] == int(cycles)
        and combined["blocked"] == 0
        and stage_complete
    )
    return {
        "status": "ok" if cycle_complete else "fail",
        "open_success_criterion": "chart_ready",
        "cycles": int(cycles),
        "opened": combined["opened"],
        "closed": combined["closed"],
        "blocked": combined["blocked"],
        "cycle_samples": combined["samples"],
        "stage_contract": {
            "required_stages": list(KLINE_OPEN_STAGE_ORDER),
            "complete": stage_complete,
            "cycles": stage_cycles,
        },
        "context_diagnostics": combined["context_diagnostics"],
        "open_ui_stalls": combined["open_ui_stalls"],
        "prewarm": prewarm,
        "baseline_webengine_available": boundaries["baseline_sample"].get("webengine_available") is True,
        "final_webengine_available": boundaries["final_sample"].get("webengine_available") is True,
        "baseline_webengine_child_count": boundaries["baseline_count"],
        "final_webengine_child_count": boundaries["final_count"],
        "webengine_child_count_net_delta": boundaries["net_delta"],
        "baseline_managed_webengine_keeper_count": boundaries["baseline_keeper_count"],
        "final_managed_webengine_keeper_count": boundaries["final_keeper_count"],
        "managed_webengine_keeper_count": boundaries["final_keeper_count"],
        "active_chart_view_count_after_close": int(
            getattr(kline_manager, "active_chart_view_count", 0) or 0
        ),
    }


def _cycle_kline(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycles: int,
    settle_ms: int,
    code: str,
    name: str,
    allow_offscreen: bool,
    prewarm_requested: bool = False,
) -> dict:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and not allow_offscreen:
        return {"status": "skipped", "reason": "offscreen_webengine_guard", "cycles": 0}

    from ui.components.kline_window_manager import kline_manager

    prewarm = _stabilize_kline_prewarm(app, kline_manager, requested=prewarm_requested)
    if cycles <= 0:
        return _disabled_kline_cycle(prewarm, kline_manager)
    code_text = str(code or "").strip() or "000001"
    name_text = str(name or "").strip() or code_text
    results = [
        _run_single_kline_cycle(
            window,
            app,
            kline_manager,
            cycle_number=cycle_index + 1,
            settle_ms=settle_ms,
            code=code_text,
            name=name_text,
        )
        for cycle_index in range(max(0, int(cycles)))
    ]
    return _build_kline_cycle_result(cycles, prewarm, results, kline_manager)


def _mode_idle_seconds(args: argparse.Namespace, *, long_mode: bool) -> int:
    if args.idle_minutes is not None:
        return int(max(0.0, float(args.idle_minutes)) * 60)
    if args.idle_seconds is not None:
        return args.idle_seconds
    return SOAK_MODE_MINUTES.get(args.mode, 0) * 60 if long_mode else 5


def _mode_cycle_count(
    current: int | None,
    *,
    long_mode: bool,
    long_default: int = 2,
    short_default: int = 1,
) -> int:
    if current is not None:
        return current
    return long_default if long_mode else short_default


def _mode_quote_cycles(args: argparse.Namespace, *, long_mode: bool) -> int:
    if args.quote_cycles is not None:
        return args.quote_cycles
    if not args.central_quotes_enabled:
        return 0
    return 2 if long_mode else 1


def _apply_mode_defaults(args: argparse.Namespace) -> argparse.Namespace:
    long_mode = args.mode in {"long", "soak30", "soak60"}
    args.idle_seconds = _mode_idle_seconds(args, long_mode=long_mode)
    args.tab_cycles = _mode_cycle_count(args.tab_cycles, long_mode=long_mode)
    args.f5_cycles = _mode_cycle_count(args.f5_cycles, long_mode=long_mode)
    args.quote_cycles = _mode_quote_cycles(args, long_mode=long_mode)
    args.kline_cycles = _mode_cycle_count(
        args.kline_cycles,
        long_mode=long_mode,
        long_default=1,
        short_default=0,
    )
    return args


def _first_open_timings(tab_timings: list[dict]) -> dict[str, dict]:
    first_tabs: dict[str, dict] = {}
    for item in tab_timings:
        key = str(item.get("key") or "").strip()
        if key and key not in first_tabs and item.get("loaded_before") is not True:
            first_tabs[key] = dict(item)
    return first_tabs


def _build_startup_lazy_budget(report: dict, samples: list[dict]) -> dict:
    mode = report.get("mode") or {}
    tab_cycle = report.get("tab_cycle") or {}
    f5_cycle = report.get("f5_cycle") or {}
    first_tabs = _first_open_timings(list(tab_cycle.get("tabs") or []))

    f5_timings = list(f5_cycle.get("cycle_timings") or [])
    final_sample = samples[-1] if samples else {}
    final_background = final_sample.get("background_tasks") or {}
    final_process = final_sample.get("process") or {}
    final_timers = final_sample.get("timers") or {}

    return {
        "startup": {
            "main_window_ready_ms": report.get("startup_ready_ms"),
            "initial_tab_ready_ms": report.get("initial_tab_ready_ms"),
            "inclusive_first_paint_ms": report.get("startup_inclusive_first_paint_ms"),
            "inclusive_initial_tab_ready_ms": report.get("startup_inclusive_initial_tab_ready_ms"),
            "app_init_first_paint_ms": report.get("startup_app_init_first_paint_ms"),
            "app_init_initial_tab_ready_ms": report.get("startup_app_init_initial_tab_ready_ms"),
            "timing_scope": (report.get("startup_timing") or {}).get("scope") or {},
            "phases": report.get("startup_phases") or {},
            "startup_settle_ms": mode.get("startup_settle_ms"),
            "startup_enabled": mode.get("startup_enabled"),
            "background_prewarm": mode.get("background_prewarm"),
        },
        "background_preload": report.get("background_preload") or {},
        "tab_first_open": {
            "tabs": list(first_tabs.values()),
            "max_elapsed_ms": max(
                [float(item.get("elapsed_ms") or 0.0) for item in first_tabs.values()],
                default=0.0,
            ),
        },
        "f5_quiet": {
            "cycles": f5_timings,
            "total_elapsed_ms": f5_cycle.get("total_elapsed_ms"),
            "max_cycle_elapsed_ms": max(
                [float(item.get("elapsed_ms") or 0.0) for item in f5_timings],
                default=0.0,
            ),
            "cycle_settle_ms": mode.get("cycle_settle_ms"),
        },
        "background_settle": {
            "final_background_task_count": final_background.get("count"),
            "final_thread_count": final_process.get("thread_count"),
            "final_active_timer_count": final_timers.get("active"),
            "final_sample_label": final_sample.get("label", ""),
        },
    }


def _initialize_native_runtime_for_probe() -> tuple[float, bool]:
    from app.services.runtime_services import (
        initialize_native_dataframe_runtime,
        is_native_dataframe_runtime_ready,
    )

    started_at = time.perf_counter()
    initialize_native_dataframe_runtime()
    return (time.perf_counter() - started_at) * 1000.0, is_native_dataframe_runtime_ready()


def _initialize_search_runtime_for_probe() -> tuple[float, bool]:
    from app.services.runtime_services import initialize_search_filter_runtime, is_search_filter_runtime_ready

    started_at = time.perf_counter()
    initialize_search_filter_runtime()
    return (time.perf_counter() - started_at) * 1000.0, is_search_filter_runtime_ready()


def _suite_mode(args: argparse.Namespace, tabs: tuple[str, ...]) -> dict:
    return {
        "mode": args.mode,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        **_probe_display_mode(args),
        "startup_enabled": bool(args.startup_enabled),
        "background_prewarm": bool(args.background_prewarm),
        "kline_prewarm_enabled": bool(args.kline_prewarm_enabled),
        "central_quotes_enabled": bool(args.central_quotes_enabled),
        "allow_controlled_probe_tab_loads": bool(args.allow_controlled_probe_tab_loads),
        "idle_seconds": int(args.idle_seconds),
        "idle_minutes": round(float(args.idle_seconds) / 60.0, 3),
        "minimum_soak_seconds": int(SOAK_MODE_MINUTES.get(args.mode, 0) * 60),
        "sample_every_seconds": int(args.sample_every_seconds),
        "startup_settle_ms": int(args.startup_settle_ms),
        "cycle_settle_ms": int(args.cycle_settle_ms),
        "post_tab_idle_timeout_ms": int(args.post_tab_idle_timeout_ms),
        "background_preload_timeout_ms": int(args.background_preload_timeout_ms),
        "tab_cycles": int(args.tab_cycles),
        "f5_cycles": int(args.f5_cycles),
        "f5_probe_mode": "real_process" if args.real_f5 else "post_refresh_callback",
        "real_f5_timeout_seconds": int(args.real_f5_timeout_seconds),
        "quote_cycles": int(args.quote_cycles),
        "kline_cycles": int(args.kline_cycles),
        "tabs": list(tabs),
        "sample_output_dir": str(args.sample_output_dir or ""),
    }


def _validation_profile(args: argparse.Namespace, tabs: tuple[str, ...]) -> str:
    required_flags = (
        args.native_qt,
        args.show_window,
        args.startup_enabled,
        args.background_prewarm,
        args.kline_prewarm_enabled,
        args.central_quotes_enabled,
        args.real_f5,
    )
    required_cycles = (args.tab_cycles, args.f5_cycles, args.quote_cycles, args.kline_cycles)
    if (
        all(bool(flag) for flag in required_flags)
        and tuple(tabs) == tuple(DEFAULT_TABS)
        and all(int(value or 0) > 0 for value in required_cycles)
    ):
        return "production_full"
    return "controlled"


def _new_suite_report(args, tabs, exported_paths, native_runtime_ms: float, native_runtime_ready: bool) -> dict:
    return {
        "schema_version": 1,
        "report_type": "runtime_health_stability_suite",
        "validation_profile": _validation_profile(args, tabs),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": _suite_mode(args, tabs),
        "window_visibility": _window_visibility_evidence(args),
        "individual_report_paths": exported_paths,
        "native_dataframe_runtime": {
            "ready": native_runtime_ready,
            "initialization_ms": round(native_runtime_ms, 3),
            "excluded_from_startup_timing": True,
            "excluded_from_startup_timing_scope": "window_only",
            "included_in_script_module_inclusive_startup_timing": True,
            "included_in_application_initialization_startup_timing": True,
        },
        "ui_stall_sampling": {
            "scope": "phase_local",
            "boundary_strategy": "qt_event_loop_settle_then_reset",
            "phase_boundaries": [],
        },
    }


def _create_probe_window(args: argparse.Namespace) -> MainWindowQT:
    return MainWindowQT(
        startup_enabled=bool(args.startup_enabled),
        background_prewarm=bool(args.background_prewarm),
        kline_prewarm_enabled=bool(args.kline_prewarm_enabled),
        central_quotes_enabled=bool(args.central_quotes_enabled),
        restore_last_tab_enabled=False,
        controlled_startup_probe_guard=False if args.allow_controlled_probe_tab_loads else None,
    )


def _record_stall_phase(report: dict, app: QApplication, args: argparse.Namespace, phase: str) -> None:
    boundary = _begin_stall_phase(app, phase=phase, settle_ms=args.cycle_settle_ms)
    report["ui_stall_sampling"]["phase_boundaries"].append(boundary)


def _take_suite_sample(window, args, label, samples, exported_paths, evidence: _SuiteEvidence | None = None) -> None:
    _sample(
        window,
        label=label,
        samples=samples,
        exported_paths=exported_paths,
        export_each_sample=not args.no_export_samples,
        sample_output_dir=args.sample_output_dir,
    )
    _record_evidence(evidence, "sample_recorded", label)


def _unavailable_post_tab_idle(timeout_ms: int, observed_task_ids: set[str]) -> dict:
    return {
        "status": "unavailable",
        "timeout_ms": int(timeout_ms),
        "ownership": "phase_started_task_ids",
        "task_id_diagnostics_available": False,
        "baseline_task_ids": [],
        "started_task_ids": sorted(observed_task_ids),
        "remaining_task_ids": [],
        "concurrent_startup_task_ids": sorted(observed_task_ids & STARTUP_TASK_IDS),
    }


def _stop_for_window_visibility_failure(report: dict) -> bool:
    report["startup_task_settle"] = {
        "status": "skipped",
        "reason": "window_visibility_failed",
    }
    return False


def _run_idle_soak(
    window,
    app,
    args,
    report,
    samples,
    exported_paths,
    evidence: _SuiteEvidence | None = None,
) -> bool:
    visibility = report["window_visibility"]
    if not _observe_required_window_visibility(window, visibility, 0):
        _record_evidence(evidence, "window_visibility_failed")
        return _stop_for_window_visibility_failure(report)
    _record_stall_phase(report, app, args, "idle")
    for second in range(max(0, int(args.idle_seconds))):
        _settle(app, 1000)
        observed_seconds = second + 1
        if not _observe_required_window_visibility(window, visibility, observed_seconds):
            _record_evidence(evidence, "window_visibility_failed")
            return _stop_for_window_visibility_failure(report)
        if observed_seconds % max(1, int(args.sample_every_seconds)) == 0:
            label = f"idle:{observed_seconds}s"
            _take_suite_sample(window, args, label, samples, exported_paths, evidence)
            _record_stall_phase(report, app, args, f"idle_after_{observed_seconds}s")
    return True


def _run_startup_and_idle(
    window,
    app,
    args,
    report,
    samples,
    exported_paths,
    window_probe_started_at,
    application_initialization_started_at,
    script_module_started_at,
    evidence: _SuiteEvidence | None = None,
) -> bool:
    report["startup_phases"] = _prepare_probe_window(
        window,
        app,
        args,
        suite_started_at=window_probe_started_at,
    )
    report["startup_ready_ms"] = report["startup_phases"].get("first_paint_ms")
    report["initial_tab_ready_ms"] = report["startup_phases"].get("initial_tab_ready_ms")
    report["initial_tab_loaded"] = report["startup_phases"].get("initial_tab_loaded")
    report["initial_tab_ready"] = report["startup_phases"].get("initial_tab_ready")
    report["initial_tab_status"] = report["startup_phases"].get("initial_tab_status")
    report["startup_timing"] = _build_startup_timing(
        window,
        report["startup_phases"],
        window_probe_started_at=window_probe_started_at,
        application_initialization_started_at=application_initialization_started_at,
        script_module_started_at=script_module_started_at,
    )
    script_inclusive = report["startup_timing"]["script_module_inclusive"]
    app_inclusive = report["startup_timing"]["application_initialization_inclusive"]
    report["startup_inclusive_first_paint_ms"] = script_inclusive["first_paint_ms"]
    report["startup_inclusive_initial_tab_ready_ms"] = script_inclusive["initial_tab_ready_ms"]
    report["startup_app_init_first_paint_ms"] = app_inclusive["first_paint_ms"]
    report["startup_app_init_initial_tab_ready_ms"] = app_inclusive["initial_tab_ready_ms"]
    _take_suite_sample(window, args, "startup", samples, exported_paths, evidence)
    if not _run_idle_soak(window, app, args, report, samples, exported_paths, evidence):
        return False
    report["startup_task_settle"] = _wait_for_startup_tasks_idle(
        app,
        suite_started_at=window_probe_started_at,
        await_delayed_asian=bool(args.startup_enabled),
    )
    return True


def _run_tab_phase(
    window, app, args, tabs, report, samples, exported_paths, evidence: _SuiteEvidence | None = None
) -> None:
    if evidence is not None:
        evidence.phase_start("tab_cycle")
    _record_stall_phase(report, app, args, "tab_cycle")
    baseline_task_ids = _active_background_task_ids()
    observed_task_ids = set(baseline_task_ids or ())
    report["tab_cycle"] = _cycle_tabs(
        window,
        app,
        tabs,
        cycles=args.tab_cycles,
        settle_ms=args.cycle_settle_ms,
        observed_task_ids=observed_task_ids,
        evidence=evidence,
    )
    _take_suite_sample(window, args, "after_tab_cycle", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("tab_cycle")
        evidence.phase_start("tab_async_tail")
    _record_stall_phase(report, app, args, "tab_async_tail")
    if baseline_task_ids is None:
        report["post_tab_idle"] = _unavailable_post_tab_idle(
            args.post_tab_idle_timeout_ms, observed_task_ids
        )
    else:
        report["post_tab_idle"] = _wait_for_background_tasks_idle(
            app,
            timeout_ms=args.post_tab_idle_timeout_ms,
            baseline_task_ids=baseline_task_ids,
            observed_task_ids=observed_task_ids,
        )
    _take_suite_sample(window, args, "after_tab_async_tail", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("tab_async_tail")


def _run_background_preload_phase(
    window,
    app,
    args,
    report,
    samples,
    exported_paths,
    evidence: _SuiteEvidence | None = None,
    task_observer: _BackgroundPreloadTaskObserver | None = None,
) -> None:
    if evidence is not None:
        evidence.phase_start("background_preload")
    _record_stall_phase(report, app, args, "background_preload")
    report["background_preload"] = _wait_for_background_preload(
        window,
        app,
        enabled=bool(args.background_prewarm),
        timeout_ms=args.background_preload_timeout_ms,
        task_observer=task_observer,
    )
    _take_suite_sample(window, args, "after_background_preload", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("background_preload")


def _run_refresh_phases(
    window, app, args, report, samples, exported_paths, evidence: _SuiteEvidence | None = None
) -> None:
    if evidence is not None:
        evidence.phase_start("f5_cycle")
    _record_stall_phase(report, app, args, "f5_cycle")
    report["f5_cycle"] = _cycle_f5(
        window,
        app,
        cycles=args.f5_cycles,
        settle_ms=args.cycle_settle_ms,
        real=bool(args.real_f5),
        timeout_ms=max(1, int(args.real_f5_timeout_seconds)) * 1000,
    )
    _take_suite_sample(window, args, "after_f5_cycle", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("f5_cycle")
        evidence.phase_start("quote_cycle")
    _record_stall_phase(report, app, args, "quote_cycle")
    report["quote_cycle"] = _cycle_quotes(window, app, cycles=args.quote_cycles, settle_ms=args.cycle_settle_ms)
    _take_suite_sample(window, args, "after_quote_cycle", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("quote_cycle")


def _run_kline_phase(
    window, app, args, report, samples, exported_paths, evidence: _SuiteEvidence | None = None
) -> None:
    if evidence is not None:
        evidence.phase_start("kline_cycle")
    _record_stall_phase(report, app, args, "kline_cycle")
    report["kline_cycle"] = _cycle_kline(
        window,
        app,
        cycles=args.kline_cycles,
        settle_ms=args.cycle_settle_ms,
        code=args.kline_code,
        name=args.kline_name,
        allow_offscreen=bool(args.allow_offscreen_kline),
        prewarm_requested=bool(args.kline_prewarm_enabled),
    )
    _settle(app, args.cycle_settle_ms)
    _take_suite_sample(window, args, "after_kline_prewarm", samples, exported_paths, evidence)
    _settle(app, args.cycle_settle_ms)
    _take_suite_sample(window, args, "final", samples, exported_paths, evidence)
    if evidence is not None:
        evidence.phase_end("kline_cycle")


def _finish_suite_report(report: dict, samples: list[dict], started: float) -> dict:
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    report["runtime_health_samples"] = samples
    report["trend"] = build_runtime_health_trend(samples)
    report["budget_trend"] = _build_budget_trend(samples, report.get("kline_cycle"))
    report["startup_lazy_budget"] = _build_startup_lazy_budget(report, samples)
    failures = check_runtime_health_budget(report)
    unhandled_ui_exceptions = list(report.get("unhandled_ui_exceptions") or [])
    if unhandled_ui_exceptions:
        failures.append(
            {
                "check": "runtime_health.unhandled_ui_exceptions",
                "detail": "Python exception escaped a Qt callback boundary",
                "actual": len(unhandled_ui_exceptions),
                "budget": 0,
            }
        )
    report["budget"] = {"status": "fail" if failures else "ok", "failures": failures}
    report["status"] = report["budget"]["status"]
    return report


def _run_suite_workload(
    window,
    app,
    args,
    tabs,
    report,
    samples,
    exported_paths,
    evidence,
    window_probe_started_at,
    application_initialization_started_at,
    preload_task_observer: _BackgroundPreloadTaskObserver | None = None,
) -> None:
    _record_evidence(evidence, "phase_start", "startup_idle")
    idle_completed = _run_startup_and_idle(
        window,
        app,
        args,
        report,
        samples,
        exported_paths,
        window_probe_started_at,
        application_initialization_started_at,
        _SCRIPT_MODULE_ENTRY_STARTED_AT,
        evidence,
    )
    if not idle_completed:
        report["aborted"] = {"phase": "idle", "reason": "window_visibility_failed"}
        return
    _record_evidence(evidence, "phase_end", "startup_idle")
    _record_evidence(evidence, "idle_complete")
    _run_background_preload_phase(
        window,
        app,
        args,
        report,
        samples,
        exported_paths,
        evidence,
        preload_task_observer,
    )
    _run_tab_phase(window, app, args, tabs, report, samples, exported_paths, evidence)
    _run_refresh_phases(window, app, args, report, samples, exported_paths, evidence)
    _run_kline_phase(window, app, args, report, samples, exported_paths, evidence)


def _finalize_suite_window(window, app, args, report, evidence, *, normal_completion: bool) -> None:
    if normal_completion:
        _record_evidence(evidence, "phase_start", "shutdown")
    _record_stall_phase(report, app, args, "shutdown")
    report["shutdown"] = _finalize_probe_window(window, app)
    if normal_completion:
        _record_evidence(evidence, "phase_end", "shutdown")


def run_suite(
    args: argparse.Namespace,
    *,
    evidence: _SuiteEvidence | None = None,
    unhandled_ui_exceptions: list[dict] | None = None,
) -> dict:
    application_initialization_started_at = time.perf_counter()
    args = _apply_mode_defaults(args)
    app = QApplication.instance() or QApplication(sys.argv)
    native_runtime_ms, native_runtime_ready = _initialize_native_runtime_for_probe()
    search_runtime_ms, search_runtime_ready = _initialize_search_runtime_for_probe()
    tabs = tuple(dict.fromkeys(args.tabs or DEFAULT_TABS))
    samples: list[dict] = []
    exported_paths: list[str] = []
    window_probe_started_at = time.perf_counter()
    report = _new_suite_report(args, tabs, exported_paths, native_runtime_ms, native_runtime_ready)
    report["unhandled_ui_exceptions"] = (
        unhandled_ui_exceptions if unhandled_ui_exceptions is not None else []
    )
    report["search_filter_runtime"] = {
        "ready": search_runtime_ready,
        "initialization_ms": round(search_runtime_ms, 3),
        "excluded_from_window_only_startup_timing": True,
        "included_in_script_module_inclusive_startup_timing": True,
        "included_in_application_initialization_startup_timing": True,
    }
    _record_evidence(evidence, "bind", report, tabs, args.tab_cycles, exported_paths)
    window = _create_probe_window(args)
    preload_task_observer = None
    if args.background_prewarm:
        preload_task_observer = _BackgroundPreloadTaskObserver(getattr(window, "_workspace", None))
        preload_task_observer.start()
    try:
        _run_suite_workload(
            window,
            app,
            args,
            tabs,
            report,
            samples,
            exported_paths,
            evidence,
            window_probe_started_at,
            application_initialization_started_at,
            preload_task_observer,
        )
    finally:
        if preload_task_observer is not None:
            preload_task_observer.stop()
        _finalize_suite_window(
            window,
            app,
            args,
            report,
            evidence,
            normal_completion=sys.exc_info()[0] is None,
        )
    return _finish_suite_report(report, samples, window_probe_started_at)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime health and long-running stability suite.")
    parser.add_argument("--mode", choices=("short", "long", "soak30", "soak60"), default="short")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--show-window", action="store_true")
    parser.add_argument("--startup-enabled", action="store_true")
    parser.add_argument("--background-prewarm", action="store_true")
    parser.add_argument("--kline-prewarm-enabled", action="store_true")
    parser.add_argument("--central-quotes-enabled", action="store_true")
    parser.add_argument("--allow-controlled-probe-tab-loads", action="store_true")
    parser.add_argument("--startup-settle-ms", type=int, default=300)
    parser.add_argument("--cycle-settle-ms", type=int, default=120)
    parser.add_argument("--post-tab-idle-timeout-ms", type=int, default=POST_TAB_IDLE_TIMEOUT_MS)
    parser.add_argument(
        "--background-preload-timeout-ms",
        type=int,
        default=BACKGROUND_PRELOAD_TIMEOUT_MS,
    )
    parser.add_argument("--sample-every-seconds", type=int, default=5)
    parser.add_argument("--idle-seconds", type=int, default=None)
    parser.add_argument("--idle-minutes", type=float, default=None)
    parser.add_argument("--tab-cycles", type=int, default=None)
    parser.add_argument("--f5-cycles", type=int, default=None)
    parser.add_argument("--real-f5", action="store_true")
    parser.add_argument("--real-f5-timeout-seconds", type=int, default=REAL_F5_TIMEOUT_SECONDS)
    parser.add_argument("--quote-cycles", type=int, default=None)
    parser.add_argument("--kline-cycles", type=int, default=None)
    parser.add_argument("--kline-code", default="000001")
    parser.add_argument("--kline-name", default="平安银行")
    parser.add_argument("--allow-offscreen-kline", action="store_true")
    parser.add_argument("--tabs", nargs="*", default=list(DEFAULT_TABS))
    parser.add_argument("--no-export-samples", action="store_true")
    parser.add_argument("--sample-output-dir", type=Path, default=None)
    parser.add_argument("--fail-on-budget", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    evidence = _SuiteEvidence(args.output)
    faulthandler_file = None
    exception_hook = None
    unhandled_ui_exceptions: list[dict] = []

    def _record_ui_exception(record: dict) -> None:
        unhandled_ui_exceptions.append(dict(record))
        evidence.ui_exception(record)

    try:
        faulthandler_file = _enable_suite_faulthandler(evidence)
        exception_hook = install_ui_exception_hook(
            log_file=faulthandler_file,
            on_exception=_record_ui_exception,
        )
        report = run_suite(
            args,
            evidence=evidence,
            unhandled_ui_exceptions=unhandled_ui_exceptions,
        )
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            _save_final_report(args.output, report)
        evidence.complete(report)
        print(text)
        if args.fail_on_budget and report.get("status") != "ok":
            return 1
        return 0
    except BaseException as exc:
        evidence.error(exc)
        raise
    finally:
        if exception_hook is not None:
            exception_hook.restore()
        _close_suite_faulthandler(faulthandler_file)


if __name__ == "__main__":
    raise SystemExit(main())
