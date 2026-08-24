"""Bounded runtime-health history, persistence, alerts, and trend analysis."""

from __future__ import annotations

import importlib
import json
import math
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from core.runtime_paths import CACHE_DIR

_HISTORY_PATH_LOCKS_LOCK = threading.Lock()
_HISTORY_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _history_path_lock(path: Path) -> threading.RLock:
    resolved_path = path.resolve()
    with _HISTORY_PATH_LOCKS_LOCK:
        return _HISTORY_PATH_LOCKS.setdefault(resolved_path, threading.RLock())


@dataclass(frozen=True, slots=True)
class RuntimeHealthThresholds:
    """Warning limits for the lightweight runtime monitor.

    A ``None`` value disables the corresponding limit. Counts are triggered
    only when they exceed the configured maximum.
    """

    task_active_count: int | None = 32
    task_failed_delta: int | None = 0
    kline_browser_count: int | None = 4
    kline_page_count: int | None = 8
    process_rss_mb: float | None = 2048.0
    process_rss_growth_mb: float | None = 256.0
    min_trend_samples: int = 3

    def __post_init__(self) -> None:
        limits = (
            self.task_active_count,
            self.task_failed_delta,
            self.kline_browser_count,
            self.kline_page_count,
            self.process_rss_mb,
            self.process_rss_growth_mb,
        )
        if any(value is not None and value < 0 for value in limits):
            raise ValueError("runtime-health thresholds must be non-negative")
        if self.min_trend_samples < 2:
            raise ValueError("min_trend_samples must be at least 2")


class RuntimeHealthHistoryStore:
    """Append-only JSONL storage with bounded, atomic compaction."""

    def __init__(self, path: str | Path, *, max_records: int = 4096) -> None:
        if max_records < 2:
            raise ValueError("max_records must be at least 2")
        self.path = Path(path)
        self.max_records = max_records
        self._lock = threading.RLock()

    def load(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        with _history_path_lock(self.path):
            with self._lock:
                with _interprocess_file_lock(self.path):
                    records = self._read_records()
        return records[-limit:] if limit is not None else records

    def append(self, sample: Mapping[str, Any]) -> None:
        record = dict(sample)
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with _history_path_lock(self.path):
            with self._lock:
                with _interprocess_file_lock(self.path):
                    with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload + "\n")
                        handle.flush()
                    records = self._read_records()
                    if len(records) > self.max_records:
                        self._compact(records)

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw_line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(record, dict) and isinstance(record.get("timestamp"), (int, float)):
                records.append(record)
        return records

    def _compact(self, records: Sequence[Mapping[str, Any]]) -> None:
        retained = records[-self.max_records :]
        fd, raw_temp_path = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temp_path = Path(raw_temp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                for record in retained:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _interprocess_file_lock(history_path: Path) -> Iterator[None]:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = history_path.with_name(f".{history_path.name}.lock")
    with lock_path.open("a+b") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _number(section: Mapping[str, Any] | None, key: str) -> float | int | None:
    if not isinstance(section, Mapping):
        return None
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _compact_sample(
    report: Mapping[str, Any], timestamp: float, process_session_id: str
) -> dict[str, Any]:
    task = report.get("task")
    kline = report.get("kline")
    memory = report.get("memory")
    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "process_session_id": process_session_id,
        "task_active_count": _number(task, "active_count"),
        "task_failed_count": _number(task, "failed_count"),
        "kline_browser_count": _number(kline, "browser_count"),
        "kline_page_count": _number(kline, "page_count"),
        "process_pid": _number(memory, "process_pid"),
        "process_rss_mb": _number(memory, "process_rss_mb"),
    }


def _series(samples: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        raw_value = sample.get(key)
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _delta(values: Sequence[float]) -> float | None:
    return values[-1] - values[0] if len(values) >= 2 else None


def _peak(values: Sequence[float]) -> float | None:
    return max(values) if values else None


def _task_trend(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    active = _series(samples, "task_active_count")
    failed = _series(samples, "task_failed_count")
    failed_delta = _delta(failed)
    return {
        "active_peak": _peak(active),
        "failed_delta": max(0.0, failed_delta) if failed_delta is not None else None,
    }


def _kline_trend(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    browsers = _series(samples, "kline_browser_count")
    pages = _series(samples, "kline_page_count")
    return {
        "browser_peak": _peak(browsers),
        "browser_delta": _delta(browsers),
        "page_peak": _peak(pages),
        "page_delta": _delta(pages),
    }


def _rss_direction(delta: float | None) -> str:
    if delta is None or abs(delta) < 1.0:
        return "stable"
    return "rising" if delta > 0.0 else "falling"


def _memory_trend(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rss = _series(samples, "process_rss_mb")
    timestamps = _series(samples, "timestamp")
    delta = _delta(rss)
    elapsed_minutes = max(0.0, (_delta(timestamps) or 0.0) / 60.0)
    slope = delta / elapsed_minutes if delta is not None and elapsed_minutes > 0.0 else None
    return {
        "rss_start_mb": rss[0] if rss else None,
        "rss_end_mb": rss[-1] if rss else None,
        "rss_peak_mb": _peak(rss),
        "rss_delta_mb": delta,
        "rss_slope_mb_per_minute": slope,
        "direction": _rss_direction(delta),
    }


def _trend(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    timestamps = _series(samples, "timestamp")
    return {
        "sample_count": len(samples),
        "window_seconds": max(0.0, _delta(timestamps) or 0.0),
        "task": _task_trend(samples),
        "kline": _kline_trend(samples),
        "memory": _memory_trend(samples),
    }


def _warning(code: str, metric: str, value: float, threshold: float) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "metric": metric,
        "value": value,
        "threshold": threshold,
    }


def _limit_alert(
    alerts: list[dict[str, Any]],
    *,
    code: str,
    metric: str,
    value: float | int | None,
    limit: float | int | None,
) -> None:
    if value is not None and limit is not None and value > limit:
        alerts.append(_warning(code, metric, float(value), float(limit)))


def _current_alerts(
    sample: Mapping[str, Any], thresholds: RuntimeHealthThresholds
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    specs = (
        ("task_active_limit", "task.active_count", "task_active_count", thresholds.task_active_count),
        (
            "kline_browser_limit",
            "kline.browser_count",
            "kline_browser_count",
            thresholds.kline_browser_count,
        ),
        ("kline_page_limit", "kline.page_count", "kline_page_count", thresholds.kline_page_count),
        ("process_rss_limit", "memory.process_rss_mb", "process_rss_mb", thresholds.process_rss_mb),
    )
    for code, metric, key, limit in specs:
        _limit_alert(
            alerts,
            code=code,
            metric=metric,
            value=sample.get(key),
            limit=limit,
        )
    return alerts


def _trend_alerts(
    sample: Mapping[str, Any],
    trend: Mapping[str, Any],
    thresholds: RuntimeHealthThresholds,
) -> list[dict[str, Any]]:
    if int(trend.get("sample_count", 0)) < thresholds.min_trend_samples:
        return []
    alerts: list[dict[str, Any]] = []
    specs = (
        (
            "task_failures_increased",
            "trend.task.failed_delta",
            sample.get("task_failed_count"),
            _number(trend.get("task"), "failed_delta"),
            thresholds.task_failed_delta,
        ),
        (
            "process_rss_growth",
            "trend.memory.rss_delta_mb",
            sample.get("process_rss_mb"),
            _number(trend.get("memory"), "rss_delta_mb"),
            thresholds.process_rss_growth_mb,
        ),
    )
    for code, metric, current_value, value, limit in specs:
        if current_value is None:
            continue
        _limit_alert(alerts, code=code, metric=metric, value=value, limit=limit)
    return alerts


def _alerts(
    sample: Mapping[str, Any],
    trend: Mapping[str, Any],
    thresholds: RuntimeHealthThresholds,
) -> list[dict[str, Any]]:
    return [*_current_alerts(sample, thresholds), *_trend_alerts(sample, trend, thresholds)]


def _current_process_samples(
    samples: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if not samples:
        return ()
    current_identity = (
        samples[-1].get("process_session_id"),
        samples[-1].get("process_pid"),
    )
    start = len(samples) - 1
    while start > 0:
        previous_identity = (
            samples[start - 1].get("process_session_id"),
            samples[start - 1].get("process_pid"),
        )
        if previous_identity != current_identity:
            break
        start -= 1
    return tuple(samples[start:])


def _availability_alerts(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for section_name in ("task", "kline", "memory"):
        section = report.get(section_name)
        if isinstance(section, Mapping) and section.get("available") is False:
            alerts.append(
                {
                    "code": f"{section_name}_collector_unavailable",
                    "severity": "warning",
                    "metric": f"{section_name}.available",
                    "diagnostic_error": section.get("diagnostic_error"),
                }
            )
    return alerts


class RuntimeHealthMonitor:
    """Thread-safe monitor that enriches snapshots with bounded trends and alerts."""

    def __init__(
        self,
        *,
        capacity: int = 120,
        thresholds: RuntimeHealthThresholds | None = None,
        store: RuntimeHealthHistoryStore | None = None,
        process_session_id: str | None = None,
    ) -> None:
        if capacity < 2:
            raise ValueError("capacity must be at least 2")
        self.capacity = capacity
        self.thresholds = thresholds or RuntimeHealthThresholds()
        self.store = store
        self.process_session_id = str(process_session_id or uuid.uuid4().hex)
        self._lock = threading.RLock()
        self._persistence_error: str | None = None
        try:
            existing = store.load(limit=capacity) if store is not None else []
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            existing = []
            self._persistence_error = exc.__class__.__name__
        self._samples: deque[dict[str, Any]] = deque(existing, maxlen=capacity)

    def observe(self, report: Mapping[str, Any], *, timestamp: float | None = None) -> dict[str, Any]:
        sample = _compact_sample(
            report,
            time.time() if timestamp is None else float(timestamp),
            self.process_session_id,
        )
        with self._lock:
            self._samples.append(sample)
            if self.store is not None:
                try:
                    self.store.append(sample)
                    self._persistence_error = None
                except (OSError, UnicodeError, TypeError, ValueError) as exc:
                    self._persistence_error = exc.__class__.__name__
            trend = _trend(_current_process_samples(tuple(self._samples)))
            alerts = _availability_alerts(report)
            alerts.extend(_alerts(sample, trend, self.thresholds))
            if self._persistence_error is not None:
                alerts.append(
                    {
                        "code": "history_persistence_unavailable",
                        "severity": "warning",
                        "metric": "history.persistence",
                        "diagnostic_error": self._persistence_error,
                    }
                )
            return {
                "observed_at_epoch": sample["timestamp"],
                "status": "degraded" if alerts else "healthy",
                "alerts": alerts,
                "trend": trend,
                "history": {
                    "sample_count": len(self._samples),
                    "capacity": self.capacity,
                    "persistent": self.store is not None,
                    "path": str(self.store.path) if self.store is not None else None,
                    "process_session_id": self.process_session_id,
                    "diagnostic_error": self._persistence_error,
                },
            }


def default_runtime_health_store_path() -> Path:
    return Path(CACHE_DIR) / "runtime_health_history.jsonl"


_DEFAULT_RUNTIME_HEALTH_MONITOR: RuntimeHealthMonitor | None = None
_DEFAULT_RUNTIME_HEALTH_MONITOR_LOCK = threading.Lock()


def default_runtime_health_monitor() -> RuntimeHealthMonitor:
    """Create the persistent process-wide monitor only on first observation."""
    global _DEFAULT_RUNTIME_HEALTH_MONITOR
    monitor = _DEFAULT_RUNTIME_HEALTH_MONITOR
    if monitor is not None:
        return monitor
    with _DEFAULT_RUNTIME_HEALTH_MONITOR_LOCK:
        monitor = _DEFAULT_RUNTIME_HEALTH_MONITOR
        if monitor is None:
            monitor = RuntimeHealthMonitor(
                store=RuntimeHealthHistoryStore(default_runtime_health_store_path())
            )
            _DEFAULT_RUNTIME_HEALTH_MONITOR = monitor
        return monitor


__all__ = [
    "RuntimeHealthHistoryStore",
    "RuntimeHealthMonitor",
    "RuntimeHealthThresholds",
    "default_runtime_health_monitor",
    "default_runtime_health_store_path",
]
