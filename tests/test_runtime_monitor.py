from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

from infra.runtime_monitor import (
    RuntimeHealthHistoryStore,
    RuntimeHealthMonitor,
    RuntimeHealthThresholds,
    runtime_health_report,
)
from infra.runtime_monitor import monitor as runtime_monitor_module
from infra.tasks.task_scheduler import task_manager
from ui.components.kline_window_manager import KLineWindowManager


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[Callable[..., object]] = []

    def connect(self, callback, **_kwargs) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


def test_task_runtime_health_snapshot_is_atomic_and_counts_failures():
    task_id = "runtime-health-failure"
    worker = SimpleNamespace(
        cancellation_token=SimpleNamespace(cancelled=False),
        signals=SimpleNamespace(finished=_Signal(), error=_Signal(), terminated=_Signal()),
    )
    errors: list[str] = []
    with task_manager._lock:
        baseline = task_manager._failed_count
        task_manager.active_workers[task_id] = cast(Any, worker)
    task_manager._connect_worker_callbacks(cast(Any, worker), task_id, None, errors.append, None)
    try:
        worker.signals.error.emit("expected failure")
        snapshot = task_manager.runtime_health_snapshot()

        assert snapshot["failed_count"] == baseline + 1
        assert task_id in snapshot["task_ids"]
        assert errors == ["expected failure"]
        with pytest.raises(TypeError):
            cast(dict[str, Any], snapshot)["failed_count"] = 0
    finally:
        worker.signals.terminated.emit()
        with task_manager._lock:
            task_manager._failed_count = baseline
    assert task_manager.is_active_task(task_id) is False


def test_kline_manager_runtime_health_snapshot_counts_unique_browsers_and_pages():
    first_page = object()
    second_page = object()
    prewarm_page = object()
    first_browser = SimpleNamespace(page=lambda: first_page)
    second_browser = SimpleNamespace(page=lambda: second_page)
    first_chart = SimpleNamespace(browser=first_browser)
    second_chart = SimpleNamespace(browser=second_browser)

    manager = object.__new__(KLineWindowManager)
    manager._charts = [first_chart]
    manager._idle_chart = second_chart
    manager._reclaiming_chart = second_chart
    manager._prewarm_window = first_chart
    manager._prewarm_view = prewarm_page

    snapshot = manager.runtime_health_snapshot()

    assert snapshot == {
        "browser_count": 2,
        "page_count": 3,
        "active_window_count": 1,
        "keeper_count": 3,
    }
    with pytest.raises(TypeError):
        snapshot["browser_count"] = 0


def test_runtime_health_report_aggregates_required_metrics():
    task = SimpleNamespace(
        runtime_health_snapshot=lambda: MappingProxyType(
            {
                "active_count": 2,
                "failed_count": 5,
            }
        )
    )
    kline = SimpleNamespace(
        runtime_health_snapshot=lambda: MappingProxyType(
            {
                "browser_count": 3,
                "page_count": 4,
            }
        )
    )

    report = runtime_health_report(
        task_manager_instance=task,
        kline_manager_instance=kline,
        process_snapshot={"rss_mb": 12.5, "source": "unit-test"},
        monitor=RuntimeHealthMonitor(capacity=2),
    )

    assert report["task"] == {
        "available": True,
        "active_count": 2,
        "failed_count": 5,
    }
    assert report["kline"] == {
        "available": True,
        "browser_count": 3,
        "page_count": 4,
    }
    assert report["memory"]["process_rss"] == int(12.5 * 1024 * 1024)
    assert report["memory"]["process_rss_unit"] == "bytes"
    assert report["memory"]["process_rss_mb"] == 12.5
    assert report["memory"]["process_pid"] is None
    assert report["memory"]["source"] == "unit-test"


@pytest.mark.parametrize("rss_mb", [None, "", -1, float("nan"), float("inf"), float("-inf"), True])
def test_runtime_health_report_rejects_invalid_rss_values(rss_mb):
    report = runtime_health_report(
        task_manager_instance=SimpleNamespace(
            runtime_health_snapshot=lambda: {"active_count": 0, "failed_count": 0}
        ),
        kline_manager_instance=SimpleNamespace(
            runtime_health_snapshot=lambda: {"browser_count": 0, "page_count": 0}
        ),
        process_snapshot={"rss_mb": rss_mb},
        monitor=RuntimeHealthMonitor(capacity=2),
    )

    assert report["memory"]["available"] is False
    assert report["status"] == "degraded"
    assert {alert["code"] for alert in report["alerts"]} == {"memory_collector_unavailable"}


def test_runtime_health_report_rejects_non_finite_and_negative_task_counts():
    for active_count in (float("inf"), float("nan"), -1, True):
        report = runtime_health_report(
            task_manager_instance=SimpleNamespace(
                runtime_health_snapshot=lambda active_count=active_count: {
                    "active_count": active_count,
                    "failed_count": 0,
                }
            ),
            kline_manager_instance=SimpleNamespace(
                runtime_health_snapshot=lambda: {"browser_count": 0, "page_count": 0}
            ),
            process_snapshot={"rss_mb": 1.0},
            monitor=RuntimeHealthMonitor(capacity=2),
        )
        assert report["task"]["available"] is False
        assert report["status"] == "degraded"


def test_default_runtime_health_monitor_is_initialized_lazily(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_monitor_module, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(runtime_monitor_module, "_DEFAULT_RUNTIME_HEALTH_MONITOR", None)
    history_path = runtime_monitor_module.default_runtime_health_store_path()

    assert history_path.parent.exists() is False
    monitor = runtime_monitor_module.default_runtime_health_monitor()

    assert monitor.store is not None
    assert monitor.store.path == history_path
    assert history_path.parent.is_dir()
    assert runtime_monitor_module.default_runtime_health_monitor() is monitor


def test_runtime_health_report_is_fail_open_for_unavailable_collectors():
    def _raise_runtime():
        raise RuntimeError("unavailable")

    monitor = RuntimeHealthMonitor(capacity=5)
    healthy_kline = SimpleNamespace(
        runtime_health_snapshot=lambda: {"browser_count": 0, "page_count": 0}
    )
    for failed_count in (0, 1, 2):
        runtime_health_report(
            task_manager_instance=SimpleNamespace(
                runtime_health_snapshot=lambda failed_count=failed_count: {
                    "active_count": 0,
                    "failed_count": failed_count,
                }
            ),
            kline_manager_instance=healthy_kline,
            process_snapshot={"rss_mb": 10.0},
            monitor=monitor,
        )
    report = runtime_health_report(
        task_manager_instance=SimpleNamespace(runtime_health_snapshot=_raise_runtime),
        kline_manager_instance=SimpleNamespace(runtime_health_snapshot=_raise_runtime),
        process_snapshot={"rss_mb": "invalid"},
        monitor=monitor,
    )

    assert report["task"]["available"] is False
    assert report["task"]["diagnostic_error"] == "RuntimeError"
    assert report["kline"]["available"] is False
    assert report["memory"]["available"] is False
    assert report["status"] == "degraded"
    assert {alert["code"] for alert in report["alerts"]} == {
        "task_collector_unavailable",
        "kline_collector_unavailable",
        "memory_collector_unavailable",
    }


def test_task_health_snapshot_handles_concurrent_reads():
    failures = []

    def _reader() -> None:
        for _ in range(100):
            snapshot = task_manager.runtime_health_snapshot()
            if snapshot["active_count"] != len(snapshot["task_ids"]):
                failures.append(snapshot)

    threads = [threading.Thread(target=_reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []


def test_runtime_health_monitor_persists_bounded_history_and_reloads(tmp_path):
    history_path = tmp_path / "runtime-health.jsonl"
    store = RuntimeHealthHistoryStore(history_path, max_records=3)
    monitor = RuntimeHealthMonitor(capacity=3, store=store, process_session_id="persist-test")
    task = SimpleNamespace(runtime_health_snapshot=lambda: {"active_count": 1, "failed_count": 0})
    kline = SimpleNamespace(runtime_health_snapshot=lambda: {"browser_count": 1, "page_count": 1})

    for timestamp in range(5):
        runtime_health_report(
            task_manager_instance=task,
            kline_manager_instance=kline,
            process_snapshot={"rss_mb": 100.0 + timestamp},
            monitor=monitor,
            timestamp=float(timestamp),
        )

    records = store.load()
    assert [record["timestamp"] for record in records] == [2.0, 3.0, 4.0]
    assert history_path.read_text(encoding="utf-8").count("\n") == 3

    reloaded = RuntimeHealthMonitor(
        capacity=3,
        store=RuntimeHealthHistoryStore(history_path, max_records=3),
        process_session_id="persist-test",
    )
    report = runtime_health_report(
        task_manager_instance=task,
        kline_manager_instance=kline,
        process_snapshot={"rss_mb": 105.0},
        monitor=reloaded,
        timestamp=5.0,
    )

    assert report["history"]["persistent"] is True
    assert report["history"]["sample_count"] == 3
    assert report["trend"]["memory"]["rss_delta_mb"] == 2.0


def test_runtime_health_monitor_applies_thresholds_and_classifies_trends(tmp_path):
    thresholds = RuntimeHealthThresholds(
        task_active_count=1,
        task_failed_delta=0,
        kline_browser_count=1,
        kline_page_count=2,
        process_rss_mb=120.0,
        process_rss_growth_mb=10.0,
        min_trend_samples=3,
    )
    monitor = RuntimeHealthMonitor(
        capacity=5,
        thresholds=thresholds,
        store=RuntimeHealthHistoryStore(tmp_path / "trend.jsonl", max_records=10),
    )
    samples = [
        (0.0, 1, 0, 1, 1, 100.0),
        (60.0, 1, 0, 1, 1, 108.0),
        (120.0, 2, 1, 2, 3, 125.0),
    ]
    report = {}
    for timestamp, active, failed, browsers, pages, rss_mb in samples:
        report = runtime_health_report(
            task_manager_instance=SimpleNamespace(
                runtime_health_snapshot=lambda active=active, failed=failed: {
                    "active_count": active,
                    "failed_count": failed,
                }
            ),
            kline_manager_instance=SimpleNamespace(
                runtime_health_snapshot=lambda browsers=browsers, pages=pages: {
                    "browser_count": browsers,
                    "page_count": pages,
                }
            ),
            process_snapshot={"rss_mb": rss_mb},
            monitor=monitor,
            timestamp=timestamp,
        )

    assert report["status"] == "degraded"
    assert {alert["code"] for alert in report["alerts"]} == {
        "task_active_limit",
        "task_failures_increased",
        "kline_browser_limit",
        "kline_page_limit",
        "process_rss_limit",
        "process_rss_growth",
    }
    assert report["trend"]["memory"] == {
        "rss_start_mb": 100.0,
        "rss_end_mb": 125.0,
        "rss_peak_mb": 125.0,
        "rss_delta_mb": 25.0,
        "rss_slope_mb_per_minute": 12.5,
        "direction": "rising",
    }
    assert report["trend"]["task"]["failed_delta"] == 1.0


def test_runtime_health_monitor_serializes_concurrent_persistence(tmp_path):
    store = RuntimeHealthHistoryStore(tmp_path / "concurrent.jsonl", max_records=40)
    monitor = RuntimeHealthMonitor(capacity=40, store=store)
    failures = []

    def _record(offset: int) -> None:
        try:
            for index in range(10):
                monitor.observe(
                    {
                        "task": {"active_count": 0, "failed_count": 0},
                        "kline": {"browser_count": 0, "page_count": 0},
                        "memory": {"process_rss_mb": float(offset + index)},
                    },
                    timestamp=float(offset + index),
                )
        except Exception as exc:  # pragma: no cover - assertion collector
            failures.append(exc)

    threads = [threading.Thread(target=_record, args=(index * 10,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(store.load()) == 40


def test_runtime_health_monitor_is_fail_open_when_history_cannot_be_written(tmp_path):
    class _BrokenStore(RuntimeHealthHistoryStore):
        def append(self, sample: Mapping[str, Any]) -> None:
            raise PermissionError("read only")

    store = _BrokenStore(tmp_path / "blocked" / "history.jsonl")
    monitor = RuntimeHealthMonitor(store=store)
    report = monitor.observe(
        {
            "task": {"active_count": 0, "failed_count": 0},
            "kline": {"browser_count": 0, "page_count": 0},
            "memory": {"process_rss_mb": 10.0},
        },
        timestamp=1.0,
    )

    assert report["status"] == "degraded"
    assert report["alerts"][-1]["code"] == "history_persistence_unavailable"


def test_runtime_health_trend_resets_when_process_pid_changes():
    monitor = RuntimeHealthMonitor(capacity=5)
    task = SimpleNamespace(runtime_health_snapshot=lambda: {"active_count": 0, "failed_count": 0})
    kline = SimpleNamespace(runtime_health_snapshot=lambda: {"browser_count": 0, "page_count": 0})
    for timestamp, pid, rss_mb in ((1.0, 101, 100.0), (2.0, 101, 110.0), (3.0, 202, 50.0)):
        report = runtime_health_report(
            task_manager_instance=task,
            kline_manager_instance=kline,
            process_snapshot={"pid": pid, "rss_mb": rss_mb},
            monitor=monitor,
            timestamp=timestamp,
        )

    assert report["history"]["sample_count"] == 3
    assert report["trend"]["sample_count"] == 1
    assert report["trend"]["memory"]["rss_delta_mb"] is None
    assert report["trend"]["memory"]["direction"] == "stable"


def test_runtime_health_history_ignores_semantically_corrupt_metrics(tmp_path):
    history_path = tmp_path / "corrupt.jsonl"
    history_path.write_text(
        '{"timestamp":1,"process_session_id":"same","process_rss_mb":"bad"}\n',
        encoding="utf-8",
    )
    monitor = RuntimeHealthMonitor(
        store=RuntimeHealthHistoryStore(history_path),
        process_session_id="same",
    )

    report = monitor.observe(
        {
            "task": {"active_count": 0, "failed_count": 0},
            "kline": {"browser_count": 0, "page_count": 0},
            "memory": {"process_rss_mb": 25.0},
        },
        timestamp=2.0,
    )

    assert report["status"] == "healthy"
    assert report["trend"]["sample_count"] == 2
    assert report["trend"]["memory"]["rss_delta_mb"] is None


def test_runtime_health_history_serializes_two_store_instances(tmp_path):
    history_path = tmp_path / "shared.jsonl"
    stores = [
        RuntimeHealthHistoryStore(history_path, max_records=80),
        RuntimeHealthHistoryStore(history_path, max_records=80),
    ]

    def _append(store: RuntimeHealthHistoryStore, offset: int) -> None:
        for index in range(40):
            store.append({"timestamp": float(offset + index), "process_rss_mb": 1.0})

    threads = [
        threading.Thread(target=_append, args=(stores[0], 0)),
        threading.Thread(target=_append, args=(stores[1], 100)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = stores[0].load()
    assert len(records) == 80
    assert len({record["timestamp"] for record in records}) == 80


def test_runtime_health_trend_does_not_join_reused_pid_across_sessions(tmp_path):
    history_path = tmp_path / "sessions.jsonl"
    first = RuntimeHealthMonitor(
        store=RuntimeHealthHistoryStore(history_path),
        process_session_id="first-start",
    )
    first.observe(
        {
            "task": {"active_count": 0, "failed_count": 0},
            "kline": {"browser_count": 0, "page_count": 0},
            "memory": {"process_pid": 88, "process_rss_mb": 500.0},
        },
        timestamp=1.0,
    )
    restarted = RuntimeHealthMonitor(
        store=RuntimeHealthHistoryStore(history_path),
        process_session_id="second-start",
    )

    report = restarted.observe(
        {
            "task": {"active_count": 0, "failed_count": 0},
            "kline": {"browser_count": 0, "page_count": 0},
            "memory": {"process_pid": 88, "process_rss_mb": 100.0},
        },
        timestamp=2.0,
    )

    assert report["history"]["sample_count"] == 2
    assert report["trend"]["sample_count"] == 1
    assert report["trend"]["memory"]["rss_delta_mb"] is None
