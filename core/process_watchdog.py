# -*- coding: utf-8 -*-
"""Main-thread watchdog and process memory snapshot helpers."""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from core.logger import get_logger

_log = get_logger(__name__)


def _bytes_to_mb(value: int | float | None) -> float:
    try:
        return float(value or 0) / 1024.0 / 1024.0
    except (TypeError, ValueError):
        return 0.0


def _snapshot_via_psutil() -> dict[str, Any] | None:
    try:
        import psutil
    except ImportError:
        return None

    try:
        process = psutil.Process()
        oneshot_factory = getattr(process, "oneshot", None)
        with oneshot_factory() if callable(oneshot_factory) else nullcontext():
            memory = process.memory_info()
            snapshot = {
                "pid": process.pid,
                "rss_mb": _bytes_to_mb(getattr(memory, "rss", 0)),
                "vms_mb": _bytes_to_mb(getattr(memory, "vms", 0)),
                "thread_count": process.num_threads(),
                "source": "psutil",
            }
            private_value = getattr(memory, "private", None)
            if private_value is not None:
                snapshot["private_mb"] = _bytes_to_mb(private_value)
            working_set = getattr(memory, "wset", None)
            if working_set is not None:
                snapshot["working_set_mb"] = _bytes_to_mb(working_set)
            return snapshot
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _snapshot_via_windows_ctypes() -> dict[str, Any] | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        process_handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            process_handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            return None

        handle_count = wintypes.DWORD()
        ctypes.windll.kernel32.GetProcessHandleCount(
            process_handle,
            ctypes.byref(handle_count),
        )

        return {
            "pid": os.getpid(),
            "rss_mb": _bytes_to_mb(counters.WorkingSetSize),
            "working_set_mb": _bytes_to_mb(counters.WorkingSetSize),
            "private_mb": _bytes_to_mb(counters.PrivateUsage),
            "vms_mb": _bytes_to_mb(counters.PagefileUsage),
            "thread_count": len(threading.enumerate()),
            "handle_count": int(handle_count.value),
            "source": "win32",
        }
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _snapshot_via_resource() -> dict[str, Any] | None:
    try:
        import resource
    except ImportError:
        return None

    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_mb = float(usage.ru_maxrss)
        if sys.platform != "darwin":
            rss_mb /= 1024.0
        return {
            "pid": os.getpid(),
            "rss_mb": rss_mb,
            "thread_count": len(threading.enumerate()),
            "source": "resource",
        }
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def collect_process_snapshot() -> dict[str, Any]:
    snapshot = _snapshot_via_psutil() or _snapshot_via_windows_ctypes() or _snapshot_via_resource()
    if snapshot is None:
        snapshot = {
            "pid": os.getpid(),
            "rss_mb": 0.0,
            "thread_count": len(threading.enumerate()),
            "source": "fallback",
        }
    snapshot.setdefault("thread_count", len(threading.enumerate()))
    return snapshot


def _snapshot_to_line(snapshot: dict[str, Any]) -> str:
    parts = [
        f"pid={snapshot.get('pid')}",
        f"rss={snapshot.get('rss_mb', 0.0):.0f}MB",
    ]
    if snapshot.get("private_mb") is not None:
        parts.append(f"private={snapshot.get('private_mb', 0.0):.0f}MB")
    if snapshot.get("working_set_mb") is not None:
        parts.append(f"wset={snapshot.get('working_set_mb', 0.0):.0f}MB")
    if snapshot.get("vms_mb") is not None:
        parts.append(f"vms={snapshot.get('vms_mb', 0.0):.0f}MB")
    parts.append(f"threads={snapshot.get('thread_count', 0)}")
    if snapshot.get("handle_count") is not None:
        parts.append(f"handles={snapshot.get('handle_count')}")
    parts.append(f"source={snapshot.get('source', 'unknown')}")
    return " | ".join(parts)


def _append_watchdog_line(project_root: str, message: str) -> str:
    root = os.path.abspath(project_root or "")
    if not root:
        return ""

    log_dir = os.path.join(root, "data", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        report_path = os.path.join(log_dir, f"watchdog_{datetime.now().strftime('%Y%m%d')}.log")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_message = str(message).replace("\r", "\\r").replace("\n", "\\n")
        with open(report_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"{timestamp} {safe_message}\n")
        return report_path
    except OSError:
        return ""


def log_process_snapshot(
    label: str,
    *,
    logger=None,
    level: str = "info",
    extra: dict[str, Any] | None = None,
    project_root: str | None = None,
    direct_watchdog: bool = False,
) -> dict[str, Any]:
    snapshot = collect_process_snapshot()
    parts = [f"[watchdog] {label}", _snapshot_to_line(snapshot)]
    for key, value in sorted((extra or {}).items()):
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")

    target_logger = logger or _log
    writer = getattr(target_logger, str(level or "info").lower(), None) or target_logger.info
    line = " | ".join(parts)
    writer(line)
    if direct_watchdog and project_root:
        _append_watchdog_line(project_root, line)
    return snapshot


def memory_bucket_index(rss_mb: float, *, threshold_mb: float, step_mb: float) -> int:
    if rss_mb < threshold_mb:
        return -1
    return int((rss_mb - threshold_mb) // step_mb)


def dump_main_thread_stack(limit: int = 64) -> str:
    main_thread = threading.main_thread()
    current_frames = sys._current_frames()
    frame = current_frames.get(main_thread.ident)
    if frame is None:
        return "<main thread frame unavailable>"
    return "".join(traceback.format_stack(frame, limit=limit))


class ProcessWatchdog:
    """Monitor Qt main-thread heartbeats and record hang evidence."""

    def __init__(
        self,
        *,
        project_root: str,
        logger=None,
        heartbeat_interval_ms: int = 1000,
        poll_interval_sec: float = 2.0,
        hang_threshold_sec: float = 10.0,
        high_memory_mb: float = 1400.0,
        high_memory_step_mb: float = 256.0,
    ) -> None:
        self._project_root = os.path.abspath(project_root)
        self._logger = logger or _log
        self._heartbeat_interval_ms = max(250, int(heartbeat_interval_ms))
        self._poll_interval_sec = max(0.5, float(poll_interval_sec))
        self._hang_threshold_sec = max(2.0, float(hang_threshold_sec))
        self._high_memory_mb = max(256.0, float(high_memory_mb))
        self._high_memory_step_mb = max(64.0, float(high_memory_step_mb))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat_timer = None
        self._last_pulse_monotonic = time.monotonic()
        self._last_pulse_reason = "init"
        self._stall_active = False
        self._last_high_memory_bucket = -1
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, host) -> None:
        if self._running:
            return

        from PyQt6.QtCore import QTimer

        self._heartbeat_timer = QTimer(host)
        self._heartbeat_timer.setInterval(self._heartbeat_interval_ms)
        self._heartbeat_timer.timeout.connect(self.pulse)
        self._heartbeat_timer.start()

        self._stop_event.clear()
        self._last_pulse_monotonic = time.monotonic()
        self._last_pulse_reason = "start"
        self._stall_active = False
        self._last_high_memory_bucket = -1
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="vcp-main-thread-watchdog",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        log_process_snapshot(
            "watchdog.started",
            logger=self._logger,
            project_root=self._project_root,
            direct_watchdog=True,
        )

    def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        self._stop_event.set()
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=min(2.0, self._poll_interval_sec + 0.5))
        self._thread = None
        log_process_snapshot(
            "watchdog.stopped",
            logger=self._logger,
            project_root=self._project_root,
            direct_watchdog=True,
        )

    def pulse(self, reason: str = "qtimer") -> None:
        now = time.monotonic()
        with self._lock:
            lag = now - self._last_pulse_monotonic
            was_stalled = self._stall_active
            self._last_pulse_monotonic = now
            self._last_pulse_reason = reason
            self._stall_active = False

        if was_stalled:
            log_process_snapshot(
                "watchdog.recovered",
                logger=self._logger,
                level="warning",
                extra={"lag_sec": f"{lag:.1f}", "reason": reason},
            )

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval_sec):
            snapshot = collect_process_snapshot()
            rss_mb = float(snapshot.get("rss_mb") or 0.0)
            bucket = memory_bucket_index(
                rss_mb,
                threshold_mb=self._high_memory_mb,
                step_mb=self._high_memory_step_mb,
            )
            if bucket > self._last_high_memory_bucket:
                self._last_high_memory_bucket = bucket
                if bucket >= 0:
                    log_process_snapshot(
                        "memory.high",
                        logger=self._logger,
                        level="warning",
                        extra={"bucket": bucket, "threshold_mb": int(self._high_memory_mb)},
                    )

            with self._lock:
                lag = time.monotonic() - self._last_pulse_monotonic
                if lag < self._hang_threshold_sec or self._stall_active:
                    continue
                self._stall_active = True
                last_reason = self._last_pulse_reason

            stack_text = dump_main_thread_stack()
            self._write_hang_report(lag, snapshot, stack_text)
            self._logger.warning(
                "[watchdog] main thread stall detected | lag=%.1fs | last_pulse=%s | %s",
                lag,
                last_reason,
                _snapshot_to_line(snapshot),
            )

    def _write_hang_report(
        self,
        lag_sec: float,
        snapshot: dict[str, Any],
        stack_text: str,
    ) -> None:
        log_dir = os.path.join(self._project_root, "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        report_path = os.path.join(log_dir, f"watchdog_{datetime.now().strftime('%Y%m%d')}.log")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(report_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"\n[{timestamp}] main thread stall detected\n")
            file_obj.write(f"lag={lag_sec:.1f}s | {_snapshot_to_line(snapshot)}\n")
            file_obj.write("main thread stack:\n")
            file_obj.write(stack_text)
            if not stack_text.endswith("\n"):
                file_obj.write("\n")
            file_obj.write("all threads:\n")
            try:
                faulthandler.dump_traceback(file=file_obj, all_threads=True)
            except (OSError, RuntimeError):
                file_obj.write("<faulthandler dump unavailable>\n")
