from __future__ import annotations

import builtins
import ctypes
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from core import process_watchdog as module


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(("info", message % args if args else message))

    def warning(self, message, *args):
        self.messages.append(("warning", message % args if args else message))


def test_bytes_and_psutil_snapshot_edges(monkeypatch):
    assert module._bytes_to_mb(1024 * 1024) == 1
    assert module._bytes_to_mb("bad") == 0

    class Process:
        pid = 12

        @staticmethod
        def memory_info():
            return SimpleNamespace(rss=2 * 1024**2, vms=3 * 1024**2, private=4 * 1024**2, wset=5 * 1024**2)

        @staticmethod
        def num_threads():
            return 7

    fake = ModuleType("psutil")
    fake.Process = Process
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert module._snapshot_via_psutil() == {
        "pid": 12,
        "rss_mb": 2,
        "vms_mb": 3,
        "thread_count": 7,
        "source": "psutil",
        "private_mb": 4,
        "working_set_mb": 5,
    }

    fake.Process = lambda: (_ for _ in ()).throw(OSError("gone"))
    assert module._snapshot_via_psutil() is None


def test_psutil_dependency_missing(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert module._snapshot_via_psutil() is None


def test_windows_ctypes_snapshot_success_failure_and_non_windows(monkeypatch):
    monkeypatch.setattr(module.os, "name", "nt")

    class Kernel:
        @staticmethod
        def GetCurrentProcess():
            return 99

        @staticmethod
        def GetProcessHandleCount(_handle, pointer):
            pointer._obj.value = 17
            return 1

    class Psapi:
        result = 1

        @classmethod
        def GetProcessMemoryInfo(cls, _handle, pointer, _size):
            pointer._obj.WorkingSetSize = 6 * 1024**2
            pointer._obj.PrivateUsage = 7 * 1024**2
            pointer._obj.PagefileUsage = 8 * 1024**2
            return cls.result

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel(), psapi=Psapi()), raising=False)
    snapshot = module._snapshot_via_windows_ctypes()
    assert snapshot["rss_mb"] == 6
    assert snapshot["private_mb"] == 7
    assert snapshot["handle_count"] == 17

    Psapi.result = 0
    assert module._snapshot_via_windows_ctypes() is None
    monkeypatch.setattr(module.os, "name", "posix")
    assert module._snapshot_via_windows_ctypes() is None


def test_resource_snapshot_and_dependency_edges(monkeypatch):
    fake = ModuleType("resource")
    fake.RUSAGE_SELF = 1
    fake.getrusage = lambda _which: SimpleNamespace(ru_maxrss=2048)
    monkeypatch.setitem(sys.modules, "resource", fake)
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert module._snapshot_via_resource()["rss_mb"] == 2
    monkeypatch.setattr(module.sys, "platform", "darwin")
    assert module._snapshot_via_resource()["rss_mb"] == 2048
    fake.getrusage = lambda _which: (_ for _ in ()).throw(OSError("bad"))
    assert module._snapshot_via_resource() is None

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "resource":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert module._snapshot_via_resource() is None


def test_collect_snapshot_fallback_and_line_format(monkeypatch):
    monkeypatch.setattr(module, "_snapshot_via_psutil", lambda: None)
    monkeypatch.setattr(module, "_snapshot_via_windows_ctypes", lambda: None)
    monkeypatch.setattr(module, "_snapshot_via_resource", lambda: None)
    fallback = module.collect_process_snapshot()
    assert fallback["source"] == "fallback"

    monkeypatch.setattr(module, "_snapshot_via_psutil", lambda: {"pid": 1, "rss_mb": 2})
    snapshot = module.collect_process_snapshot()
    assert snapshot["thread_count"] >= 1
    line = module._snapshot_to_line(
        {
            "pid": 1,
            "rss_mb": 2,
            "private_mb": 3,
            "working_set_mb": 4,
            "vms_mb": 5,
            "thread_count": 6,
            "handle_count": 7,
            "source": "test",
        }
    )
    assert "private=3MB" in line and "handles=7" in line and "source=test" in line


def test_append_and_log_snapshot_cover_errors_levels_and_extra(monkeypatch, tmp_path):
    path = module._append_watchdog_line(str(tmp_path), "line1\r\nline2")
    assert path and "line1\\r\\nline2" in Path(path).read_text(encoding="utf-8")

    monkeypatch.setattr(module.os, "makedirs", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")))
    assert module._append_watchdog_line(str(tmp_path / "bad"), "x") == ""

    monkeypatch.setattr(module, "collect_process_snapshot", lambda: {"pid": 1, "rss_mb": 2, "source": "fake"})
    logger = _Logger()
    result = module.log_process_snapshot(
        "sample", logger=logger, level="missing", extra={"keep": 1, "none": None, "empty": ""}
    )
    assert result["source"] == "fake"
    assert logger.messages and logger.messages[0][0] == "info"
    assert "keep=1" in logger.messages[0][1]
    assert "none=" not in logger.messages[0][1]


def test_dump_main_thread_stack_unavailable(monkeypatch):
    monkeypatch.setattr(module.sys, "_current_frames", lambda: {})
    assert module.dump_main_thread_stack() == "<main thread frame unavailable>"


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _Timer:
    def __init__(self, host):
        self.host = host
        self.timeout = _Signal()
        self.interval = None
        self.started = False
        self.stopped = False

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _Thread:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.joined = None

    def start(self):
        self.started = True

    def is_alive(self):
        return True

    def join(self, timeout):
        self.joined = timeout


def test_watchdog_start_stop_and_recovery(monkeypatch, tmp_path):
    qtcore = ModuleType("PyQt6.QtCore")
    qtcore.QTimer = _Timer
    monkeypatch.setitem(sys.modules, "PyQt6.QtCore", qtcore)
    monkeypatch.setattr(module.threading, "Thread", _Thread)
    snapshots = []
    monkeypatch.setattr(module, "log_process_snapshot", lambda label, **kwargs: snapshots.append((label, kwargs)))
    times = iter([10.0, 20.0, 30.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    logger = _Logger()
    watchdog = module.ProcessWatchdog(
        project_root=str(tmp_path),
        logger=logger,
        heartbeat_interval_ms=1,
        poll_interval_sec=0.1,
        hang_threshold_sec=0.1,
        high_memory_mb=1,
        high_memory_step_mb=1,
    )
    assert watchdog._heartbeat_interval_ms == 250
    assert watchdog._poll_interval_sec == 0.5
    assert watchdog._hang_threshold_sec == 2
    assert watchdog._high_memory_mb == 256
    assert watchdog._high_memory_step_mb == 64

    watchdog.start(object())
    watchdog.start(object())
    assert watchdog.running is True
    timer = watchdog._heartbeat_timer
    assert timer.started and timer.interval == 250 and timer.timeout.callback == watchdog.pulse
    watchdog._stall_active = True
    watchdog._last_pulse_monotonic = 15.0
    watchdog.pulse("manual")
    assert snapshots[-1][0] == "watchdog.recovered"

    thread = watchdog._thread
    watchdog.stop()
    watchdog.stop()
    assert watchdog.running is False
    assert timer.stopped is True
    assert thread.joined == 1.0
    assert snapshots[-1][0] == "watchdog.stopped"


def test_watch_loop_reports_memory_and_stall(monkeypatch, tmp_path):
    logger = _Logger()
    watchdog = module.ProcessWatchdog(
        project_root=str(tmp_path), logger=logger, poll_interval_sec=0.5, hang_threshold_sec=2, high_memory_mb=256
    )

    waits = iter([False, True])
    watchdog._stop_event = SimpleNamespace(wait=lambda _timeout: next(waits))
    watchdog._last_pulse_monotonic = 1.0
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(module, "collect_process_snapshot", lambda: {"pid": 1, "rss_mb": 300, "source": "fake"})
    monkeypatch.setattr(module, "dump_main_thread_stack", lambda: "stack")
    reports = []
    monkeypatch.setattr(watchdog, "_write_hang_report", lambda *args: reports.append(args))
    emitted = []
    monkeypatch.setattr(module, "log_process_snapshot", lambda label, **kwargs: emitted.append((label, kwargs)))

    watchdog._watch_loop()

    assert emitted[0][0] == "memory.high"
    assert reports and reports[0][0] == 9
    assert watchdog._stall_active is True
    assert logger.messages[0][0] == "warning"


def test_write_hang_report_handles_stack_newline_and_faulthandler_error(monkeypatch, tmp_path):
    watchdog = module.ProcessWatchdog(project_root=str(tmp_path))
    monkeypatch.setattr(
        module.faulthandler,
        "dump_traceback",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    watchdog._write_hang_report(3.5, {"pid": 1, "rss_mb": 2, "source": "test"}, "stack without newline")
    watchdog._write_hang_report(4.5, {"pid": 1, "rss_mb": 2, "source": "test"}, "stack with newline\n")
    report = next((tmp_path / "data" / "logs").glob("watchdog_*.log")).read_text(encoding="utf-8")
    assert "lag=3.5s" in report
    assert "<faulthandler dump unavailable>" in report
