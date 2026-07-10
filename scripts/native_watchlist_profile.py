"""Native Windows Qt profiler for startup and the first Watchlist render.

The probe uses QApplication.exec() plus QAbstractEventDispatcher awake/aboutToBlock
signals. It deliberately rejects offscreen/minimal Qt plugins so dispatcher sleep
is not confused with real event-handling work.
"""

from __future__ import annotations

import argparse
import atexit
import cProfile
import json
import os
import platform
import pstats
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NON_NATIVE_QT_PLATFORMS = frozenset({"offscreen", "minimal", "minimalegl", "vnc", "webgl"})


def _native_platform_error(*, requested: str, actual: str, system: str | None = None) -> str:
    system_name = str(system or sys.platform).strip().lower()
    requested_name = str(requested or "").strip().lower()
    actual_name = str(actual or "").strip().lower()
    if system_name != "win32":
        return f"native Watchlist profile requires Windows, current platform={system_name or 'unknown'}"
    if requested_name in NON_NATIVE_QT_PLATFORMS:
        return f"QT_QPA_PLATFORM={requested_name} is not a native desktop platform"
    if actual_name != "windows":
        return f"Qt platform plugin must be windows, actual={actual_name or 'unknown'}"
    return ""


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, float(percentile)))
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_durations(values: list[float]) -> dict:
    clean = [max(0.0, float(value)) for value in values]
    return {
        "count": len(clean),
        "total_ms": round(sum(clean), 3),
        "max_ms": round(max(clean, default=0.0), 3),
        "p50_ms": round(_percentile(clean, 0.50), 3),
        "p95_ms": round(_percentile(clean, 0.95), 3),
        "p99_ms": round(_percentile(clean, 0.99), 3),
        "over_50ms": sum(value >= 50.0 for value in clean),
        "over_100ms": sum(value >= 100.0 for value in clean),
    }


def _prepare_profile_database(source: Path, target: Path) -> dict:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"profile source database not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
    _register_profile_database_cleanup(target)
    return {
        "mode": "sqlite_backup_copy",
        "source": str(source),
        "target": str(target),
        "source_size_bytes": source.stat().st_size,
        "target_size_bytes": target.stat().st_size,
        "cleanup_on_process_exit": True,
    }


def _register_profile_database_cleanup(target: Path) -> None:
    target_text = str(target.resolve())

    def _cleanup() -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(target_text + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    atexit.register(_cleanup)


def _profile_top_functions(profile_path: Path, *, limit: int = 30) -> list[dict]:
    stats = pstats.Stats(str(profile_path))
    project_root = str(PROJECT_ROOT.resolve()).lower()
    rows: list[dict] = []
    raw_stats = getattr(stats, "stats", {})
    for (filename, line, function), values in raw_stats.items():
        if str(filename).startswith(("<", "~")):
            continue
        normalized = str(Path(filename).resolve()).lower() if not str(filename).startswith("~") else ""
        if normalized and not normalized.startswith(project_root):
            continue
        primitive_calls, total_calls, total_time, cumulative_time, _callers = values
        display_path = filename
        if normalized.startswith(project_root):
            display_path = str(Path(filename).resolve().relative_to(PROJECT_ROOT.resolve()))
        rows.append(
            {
                "function": f"{display_path}:{line}({function})",
                "primitive_calls": primitive_calls,
                "total_calls": total_calls,
                "self_ms": round(total_time * 1000.0, 3),
                "cumulative_ms": round(cumulative_time * 1000.0, 3),
            }
        )
    rows.sort(key=lambda row: (row["cumulative_ms"], row["self_ms"]), reverse=True)
    return rows[: max(1, int(limit))]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile the first Watchlist render with the native Windows Qt event dispatcher."
    )
    parser.add_argument("--source-db", type=Path, default=PROJECT_ROOT / "data" / "vcp_hunter.db")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--warmup-ms", type=int, default=500)
    parser.add_argument("--settle-ms", type=int, default=3500)
    parser.add_argument("--load-timeout-ms", type=int, default=8000)
    parser.add_argument("--heartbeat-ms", type=int, default=25)
    parser.add_argument("--top-functions", type=int, default=30)
    parser.add_argument("--no-cprofile", action="store_true")
    return parser.parse_args(argv)


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return PROJECT_ROOT / "tmp" / "native_watchlist_profile" / stamp


def _configure_isolated_runtime(output_dir: Path, source_db: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_info = _prepare_profile_database(source_db, output_dir / "profile.db")
    os.environ["VCP_HUNTER_DB_PATH"] = database_info["target"]
    os.environ["VCP_HUNTER_LOG_DIR"] = str((output_dir / "logs").resolve())
    identity = f"NativeWatchlistProfile_{os.getpid()}_{time.time_ns()}"
    os.environ["VCP_HUNTER_SETTINGS_ORGANIZATION"] = "VCPHunterDiagnostics"
    os.environ["VCP_HUNTER_SETTINGS_APPLICATION"] = identity
    return database_info


def _event_dispatcher_summary(segments: list[dict]) -> dict:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for segment in segments:
        grouped[str(segment["phase"])][str(segment["kind"])].append(float(segment["elapsed_ms"]))
    phases = {
        phase: {
            "active_dispatch": summarize_durations(kinds.get("active_dispatch", [])),
            "blocked_wait": summarize_durations(kinds.get("blocked_wait", [])),
        }
        for phase, kinds in sorted(grouped.items())
    }
    active = sorted(
        (segment for segment in segments if segment["kind"] == "active_dispatch"),
        key=lambda segment: float(segment["elapsed_ms"]),
        reverse=True,
    )
    blocked = sorted(
        (segment for segment in segments if segment["kind"] == "blocked_wait"),
        key=lambda segment: float(segment["elapsed_ms"]),
        reverse=True,
    )
    return {
        "interpretation": {
            "active_dispatch": "time between dispatcher awake and aboutToBlock; long spans indicate event handling work",
            "blocked_wait": "time spent in the native dispatcher wait; this is sleep/wake time, not UI work",
        },
        "phases": phases,
        "largest_active_dispatch_segments": active[:12],
        "largest_blocked_wait_segments": blocked[:12],
    }


def _qt_types():
    from PyQt6.QtCore import QAbstractEventDispatcher, QCoreApplication, QEvent, QObject, Qt, QTimer
    from PyQt6.QtWidgets import QApplication

    return QAbstractEventDispatcher, QCoreApplication, QEvent, QObject, Qt, QTimer, QApplication


class _DispatcherPhaseProbe:
    def __init__(self, dispatcher, *, clock=time.perf_counter):
        self._dispatcher = dispatcher
        self._clock = clock
        self._phase = "bootstrap"
        self._state = ""
        self._state_started = 0.0
        self._state_phase = "bootstrap"
        self._origin = clock()
        self.segments: list[dict] = []
        dispatcher.awake.connect(self._on_awake)
        dispatcher.aboutToBlock.connect(self._on_about_to_block)

    def start(self, phase: str) -> None:
        self._phase = str(phase or "unknown")
        self._state = "active_dispatch"
        self._state_started = self._clock()
        self._state_phase = self._phase

    def set_phase(self, phase: str) -> None:
        now = self._clock()
        self._record_current(now)
        self._phase = str(phase or "unknown")
        if self._state:
            self._state_started = now
            self._state_phase = self._phase

    def finish(self) -> dict:
        self._record_current(self._clock())
        self._state = ""
        try:
            self._dispatcher.awake.disconnect(self._on_awake)
            self._dispatcher.aboutToBlock.disconnect(self._on_about_to_block)
        except (RuntimeError, TypeError):
            pass
        return _event_dispatcher_summary(self.segments)

    def _record_current(self, now: float) -> None:
        if not self._state or self._state_started <= 0:
            return
        elapsed_ms = max(0.0, (now - self._state_started) * 1000.0)
        self.segments.append(
            {
                "kind": self._state,
                "phase": self._state_phase,
                "elapsed_ms": round(elapsed_ms, 3),
                "ended_at_ms": round((now - self._origin) * 1000.0, 3),
            }
        )

    def _transition(self, state: str) -> None:
        now = self._clock()
        self._record_current(now)
        self._state = state
        self._state_started = now
        self._state_phase = self._phase

    def _on_awake(self) -> None:
        self._transition("active_dispatch")

    def _on_about_to_block(self) -> None:
        self._transition("blocked_wait")


class _FirstPaintProbe(QObject):
    def __init__(self, app, window, qevent_type, *, origin: float):
        super().__init__()
        self._app = app
        self._window = window
        self._qevent_type = qevent_type
        self._origin = origin
        self._activation_started = 0.0
        self.events: dict[str, float] = {}
        app.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API naming
        event_type = event.type()
        if watched is self._window:
            self._record_widget_event("window", event_type)
        elif watched.__class__.__name__ == "WatchlistTab":
            self._record_widget_event("watchlist", event_type)
        return False

    def mark_activation(self, started: float) -> None:
        self._activation_started = started

    def close(self) -> None:
        try:
            self._app.removeEventFilter(self)
        except RuntimeError:
            pass

    def report(self) -> dict:
        result = {key: round(value, 3) for key, value in sorted(self.events.items())}
        if self._activation_started > 0:
            activation_ms = (self._activation_started - self._origin) * 1000.0
            for key in ("watchlist_show_at_ms", "watchlist_first_paint_at_ms"):
                if key in result:
                    result[key.replace("_at_ms", "_after_activation_ms")] = round(result[key] - activation_ms, 3)
        return result

    def _record_widget_event(self, prefix: str, event_type) -> None:
        if event_type == self._qevent_type.Show:
            key = f"{prefix}_show_at_ms"
        elif event_type == self._qevent_type.Paint:
            key = f"{prefix}_first_paint_at_ms"
        else:
            return
        self.events.setdefault(key, (time.perf_counter() - self._origin) * 1000.0)


class _NativeProfileController:
    def __init__(
        self,
        *,
        app,
        window,
        qtimer_type,
        qt_timer_type,
        dispatcher_probe: _DispatcherPhaseProbe,
        paint_probe: _FirstPaintProbe,
        args: argparse.Namespace,
        activation_profile_path: Path,
        settle_profile_path: Path,
        report: dict,
        origin: float,
        cprofile_enabled: bool,
    ):
        self.app = app
        self.window = window
        self.QTimer = qtimer_type
        self.dispatcher_probe = dispatcher_probe
        self.paint_probe = paint_probe
        self.args = args
        self.activation_profile_path = activation_profile_path
        self.settle_profile_path = settle_profile_path
        self.report = report
        self.origin = origin
        self.cprofile_enabled = bool(cprofile_enabled)
        self.activation_profiler = cProfile.Profile()
        self.settle_profiler = cProfile.Profile()
        self._active_profiler: cProfile.Profile | None = None
        self._active_profile_path: Path | None = None
        self._activation_started = 0.0
        self._heartbeat_last = 0.0
        self._heartbeat_by_phase: dict[str, list[float]] = defaultdict(list)
        self._phase = "startup_idle"
        self._done = False

        self._heartbeat = qtimer_type()
        self._heartbeat.setTimerType(qt_timer_type.PreciseTimer)
        self._heartbeat.setInterval(max(5, int(args.heartbeat_ms)))
        self._heartbeat.timeout.connect(self._on_heartbeat)

        self._load_poll = qtimer_type()
        self._load_poll.setTimerType(qt_timer_type.PreciseTimer)
        self._load_poll.setInterval(10)
        self._load_poll.timeout.connect(self._poll_watchlist_loaded)

    def start(self) -> None:
        self.dispatcher_probe.start("startup_idle")
        self._heartbeat_last = time.perf_counter()
        self._heartbeat.start()
        self.QTimer.singleShot(max(0, int(self.args.warmup_ms)), self._activate_watchlist)
        total_timeout = max(1000, int(self.args.warmup_ms) + int(self.args.load_timeout_ms))
        self.QTimer.singleShot(total_timeout, self._abort_on_timeout)

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self.dispatcher_probe.set_phase(phase)

    def _activate_watchlist(self) -> None:
        if self._done:
            return
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        stall_probe = get_ui_stall_probe()
        if stall_probe is not None:
            stall_probe.reset_stall_snapshot()
        self._set_phase("watchlist_activation")
        self._activation_started = time.perf_counter()
        self.paint_probe.mark_activation(self._activation_started)
        if self.cprofile_enabled:
            self.activation_profiler.enable()
            self._active_profiler = self.activation_profiler
            self._active_profile_path = self.activation_profile_path

        workspace = getattr(self.window, "_workspace", None)
        if workspace is None:
            self._fail("workspace unavailable")
            return
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        index = next((i for i, spec in enumerate(specs) if spec.get("key") == "watchlist"), -1)
        if index < 0:
            self._fail("watchlist tab unavailable")
            return

        call_started = time.perf_counter()
        activated = bool(workspace.activate_tab(index, reason="user"))
        self.report["timings"]["watchlist_activate_call_ms"] = round(
            (time.perf_counter() - call_started) * 1000.0, 3
        )
        if not activated:
            self._fail("watchlist activation rejected")
            return
        self._load_poll.start()

    def _poll_watchlist_loaded(self) -> None:
        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        if tab is None:
            return
        self._load_poll.stop()
        loaded_ms = (time.perf_counter() - self._activation_started) * 1000.0
        model = getattr(tab, "model", None)
        try:
            row_count = int(model.rowCount()) if model is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            row_count = None
        self.report["timings"]["watchlist_loaded_ms"] = round(loaded_ms, 3)
        self.report["watchlist"] = {
            "row_count": row_count,
            "visible": bool(tab.isVisible()),
            "workspace_load_reason": str(getattr(tab, "_workspace_load_reason", "")),
        }
        if self.cprofile_enabled:
            self._stop_active_profiler()
            self.settle_profiler.enable()
            self._active_profiler = self.settle_profiler
            self._active_profile_path = self.settle_profile_path
        self._set_phase("watchlist_settle")
        self.QTimer.singleShot(max(0, int(self.args.settle_ms)), self._finish)

    def _on_heartbeat(self) -> None:
        now = time.perf_counter()
        if self._heartbeat_last <= 0:
            self._heartbeat_last = now
            return
        interval_ms = max(5, int(self.args.heartbeat_ms))
        late_ms = max(0.0, (now - self._heartbeat_last) * 1000.0 - interval_ms)
        self._heartbeat_last = now
        self._heartbeat_by_phase[self._phase].append(late_ms)

    def _abort_on_timeout(self) -> None:
        if not self._done and "watchlist_loaded_ms" not in self.report["timings"]:
            self._fail("watchlist load timeout")

    def _fail(self, message: str) -> None:
        self.report["errors"].append(str(message))
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._heartbeat.stop()
        self._load_poll.stop()
        self._stop_active_profiler()

        from app.services.ui_task_service import background_job_runner
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        self.report["heartbeat_lateness"] = {
            phase: summarize_durations(values) for phase, values in sorted(self._heartbeat_by_phase.items())
        }
        self.report["background_tasks_at_finish"] = int(getattr(background_job_runner, "active_count", 0) or 0)
        stall_probe = get_ui_stall_probe()
        self.report["ui_stall_snapshot"] = stall_probe.stall_snapshot() if stall_probe is not None else {"installed": False}
        self._set_phase("profile_finalize")
        self.report["dispatcher"] = self.dispatcher_probe.finish()
        self.report["paint_events"] = self.paint_probe.report()
        self.paint_probe.close()
        self.report["timings"]["profile_elapsed_ms"] = round((time.perf_counter() - self.origin) * 1000.0, 3)
        try:
            self.window.close()
        finally:
            self.QTimer.singleShot(0, self.app.quit)

    def _stop_active_profiler(self) -> None:
        profiler = self._active_profiler
        profile_path = self._active_profile_path
        if profiler is None or profile_path is None:
            return
        profiler.disable()
        profiler.dump_stats(str(profile_path))
        self._active_profiler = None
        self._active_profile_path = None


def _build_environment_report(app) -> dict:
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

    screen = app.primaryScreen()
    geometry = screen.availableGeometry() if screen is not None else None
    return {
        "system": platform.platform(),
        "python": sys.version,
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "pid": os.getpid(),
        "session_name": os.environ.get("SESSIONNAME", ""),
        "requested_qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "actual_qt_platform": app.platformName(),
        "primary_screen": (
            {
                "name": screen.name(),
                "available_width": geometry.width(),
                "available_height": geometry.height(),
                "device_pixel_ratio": screen.devicePixelRatio(),
            }
            if screen is not None and geometry is not None
            else None
        ),
    }


def run_profile(args: argparse.Namespace) -> tuple[dict, Path]:
    output_dir = (args.output_dir or _default_output_dir()).resolve()
    database_info = _configure_isolated_runtime(output_dir, args.source_db)

    from core.runtime_env import configure_qt_webengine_runtime

    configure_qt_webengine_runtime()
    QAbstractEventDispatcher, QCoreApplication, QEvent, _QObject, Qt, QTimer, QApplication = _qt_types()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    environment = _build_environment_report(app)
    platform_error = _native_platform_error(
        requested=environment["requested_qt_platform"],
        actual=environment["actual_qt_platform"],
    )
    if platform_error:
        raise RuntimeError(platform_error)

    report_path = output_dir / "native_watchlist_profile.json"
    startup_profile_path = output_dir / "startup.prof"
    watchlist_activation_profile_path = output_dir / "watchlist_activation.prof"
    watchlist_settle_profile_path = output_dir / "watchlist_settle.prof"
    report = {
        "schema_version": 1,
        "report_type": "native_watchlist_profile",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "running",
        "environment": environment,
        "isolation": {
            "database": database_info,
            "settings_application": os.environ["VCP_HUNTER_SETTINGS_APPLICATION"],
            "log_dir": os.environ["VCP_HUNTER_LOG_DIR"],
            "startup_orchestrator_suppressed": True,
            "auto_refresh_suppressed": True,
            "central_quotes_suppressed": True,
            "background_prewarm_suppressed": True,
        },
        "configuration": {
            "warmup_ms": int(args.warmup_ms),
            "settle_ms": int(args.settle_ms),
            "load_timeout_ms": int(args.load_timeout_ms),
            "heartbeat_ms": int(args.heartbeat_ms),
            "cprofile_enabled": not bool(args.no_cprofile),
        },
        "timings": {},
        "profiles": {
            "startup": str(startup_profile_path) if not args.no_cprofile else None,
            "watchlist_activation": str(watchlist_activation_profile_path) if not args.no_cprofile else None,
            "watchlist_settle": str(watchlist_settle_profile_path) if not args.no_cprofile else None,
        },
        "errors": [],
    }

    origin = time.perf_counter()
    startup_profiler = cProfile.Profile()
    if not args.no_cprofile:
        startup_profiler.enable()
    import_started = time.perf_counter()
    import ui.main_window_qt as main_window_module

    report["timings"]["main_window_import_ms"] = round((time.perf_counter() - import_started) * 1000.0, 3)
    construct_started = time.perf_counter()
    window = main_window_module.MainWindowQT(
        startup_enabled=True,
        auto_refresh_enabled=False,
        background_prewarm=False,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
        controlled_startup_probe_guard=False,
    )
    window.startup_orchestrator.schedule_startup = lambda: None
    report["timings"]["window_construct_ms"] = round((time.perf_counter() - construct_started) * 1000.0, 3)
    paint_probe = _FirstPaintProbe(app, window, QEvent.Type, origin=origin)
    show_started = time.perf_counter()
    window.show()
    window.raise_()
    window.activateWindow()
    report["timings"]["window_show_call_ms"] = round((time.perf_counter() - show_started) * 1000.0, 3)
    if not args.no_cprofile:
        startup_profiler.disable()
        startup_profiler.dump_stats(str(startup_profile_path))

    dispatcher = QAbstractEventDispatcher.instance()
    if dispatcher is None:
        raise RuntimeError("native Qt event dispatcher unavailable")
    dispatcher_probe = _DispatcherPhaseProbe(dispatcher)
    controller = _NativeProfileController(
        app=app,
        window=window,
        qtimer_type=QTimer,
        qt_timer_type=Qt.TimerType,
        dispatcher_probe=dispatcher_probe,
        paint_probe=paint_probe,
        args=args,
        activation_profile_path=watchlist_activation_profile_path,
        settle_profile_path=watchlist_settle_profile_path,
        report=report,
        origin=origin,
        cprofile_enabled=not args.no_cprofile,
    )
    controller.start()
    exit_code = app.exec()
    report["qt_exit_code"] = int(exit_code)
    if not args.no_cprofile:
        report["profiles"]["startup_top_cumulative"] = _profile_top_functions(
            startup_profile_path, limit=args.top_functions
        )
        report["profiles"]["watchlist_activation_top_cumulative"] = _profile_top_functions(
            watchlist_activation_profile_path, limit=args.top_functions
        )
        report["profiles"]["watchlist_settle_top_cumulative"] = _profile_top_functions(
            watchlist_settle_profile_path, limit=args.top_functions
        )
    report["status"] = "ok" if not report["errors"] and "watchlist_loaded_ms" in report["timings"] else "error"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        report, report_path = run_profile(args)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(report_path),
                "watchlist_loaded_ms": report["timings"].get("watchlist_loaded_ms"),
                "watchlist_first_paint_ms": report.get("paint_events", {}).get(
                    "watchlist_first_paint_after_activation_ms"
                ),
                "max_active_dispatch_ms": max(
                    (
                        float(item["elapsed_ms"])
                        for item in report.get("dispatcher", {}).get("largest_active_dispatch_segments", [])
                    ),
                    default=0.0,
                ),
                "max_heartbeat_late_ms": max(
                    (float(item.get("max_ms", 0.0)) for item in report.get("heartbeat_lateness", {}).values()),
                    default=0.0,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
