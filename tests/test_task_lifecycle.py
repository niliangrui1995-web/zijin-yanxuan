from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from infra.tasks.lifecycle import CancellationToken, TaskCancelledError, TaskDeadlineExceeded, raise_if_cancelled
from infra.tasks.task_scheduler import BackgroundWorker, task_manager


def test_cancellation_token_supports_manual_cancel_and_deadline():
    token = CancellationToken()
    assert token.cancelled is False
    assert token.cancel("window_closed") is True
    assert token.cancel("ignored") is False
    assert token.cancelled is True
    assert token.reason == "window_closed"
    assert token.wait(0.01) is True
    with pytest.raises(TaskCancelledError, match="window_closed"):
        token.raise_if_cancelled()

    expired = CancellationToken(deadline_monotonic=time.monotonic() - 0.01)
    assert expired.cancelled is True
    assert expired.remaining_seconds() == 0.0
    with pytest.raises(TaskDeadlineExceeded):
        expired.raise_if_cancelled()


def test_shared_cancellation_checkpoint_preserves_optional_token_behavior():
    from app.services.ui_task_lifecycle_service import raise_if_cancelled as ui_raise_if_cancelled

    assert ui_raise_if_cancelled is raise_if_cancelled
    assert raise_if_cancelled(None) is None

    token = CancellationToken()
    assert raise_if_cancelled(token) is None
    token.cancel("shared_checkpoint")
    with pytest.raises(TaskCancelledError, match="shared_checkpoint"):
        raise_if_cancelled(token)

    expired = CancellationToken(deadline_monotonic=time.monotonic() - 0.01)
    with pytest.raises(TaskDeadlineExceeded):
        raise_if_cancelled(expired)


def test_background_worker_uses_shared_cancellation_token():
    calls = []
    token = CancellationToken()
    worker = BackgroundWorker(lambda: calls.append("ran"), cancellation_token=token)
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: calls.append(("error", message))),
        terminated=SimpleNamespace(emit=lambda: calls.append("terminated")),
    )

    token.cancel("test_cancel")
    worker.run()

    assert calls == ["terminated"]


def test_background_worker_reports_deadline_as_timeout_without_success():
    calls = []
    token = CancellationToken(deadline_monotonic=time.monotonic() - 0.01)
    worker = BackgroundWorker(lambda: "late", cancellation_token=token)
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: calls.append(("error", message))),
        terminated=SimpleNamespace(emit=lambda: calls.append("terminated")),
    )

    worker.run()

    assert calls == [("error", "任务已超过截止时间"), "terminated"]


def test_background_worker_ignores_signals_deleted_during_shutdown():
    class _DeletedSignals:
        @property
        def finished(self):
            raise RuntimeError("wrapped C/C++ object has been deleted")

        @property
        def terminated(self):
            raise RuntimeError("wrapped C/C++ object has been deleted")

    worker = BackgroundWorker(lambda: "done")
    worker.signals = _DeletedSignals()

    worker.run()

    assert worker.terminated_event.is_set() is True


@pytest.mark.parametrize("wait_result", [False, "done"])
def test_task_manager_shutdown_wait_is_bounded_and_observable(monkeypatch, wait_result):
    calls = []

    class _Pool:
        def clear(self):
            calls.append("clear")

        def waitForDone(self, timeout_ms):
            calls.append(("waitForDone", timeout_ms))
            return wait_result

    monkeypatch.setattr(task_manager, "thread_pool", _Pool())
    task_manager.active_workers.clear()
    task_manager._shutting_down = False
    try:
        assert task_manager.shutdown(wait_timeout_ms=17) is False
        assert calls == [("waitForDone", 17)]
        assert task_manager.is_shutting_down is True
    finally:
        task_manager._shutting_down = False


def test_run_in_background_builds_deadline_token(monkeypatch):
    captured = []

    def _capture(worker, tid, *, priority=None):
        captured.append((worker, tid, priority))
        return tid

    monkeypatch.setattr(task_manager, "submit_task", _capture)
    task_manager._shutting_down = False

    task_manager.run_in_background(lambda: "ok", task_id="deadline", timeout_sec=0.25)

    worker = captured[0][0]
    assert isinstance(worker.cancellation_token, CancellationToken)
    remaining = worker.cancellation_token.remaining_seconds()
    assert remaining is not None
    assert 0.0 < remaining <= 0.25


def test_task_manager_targeted_cancel_stops_running_cooperative_task(qt_application):
    started = threading.Event()
    token = CancellationToken.with_timeout(2.0)
    task_id = "targeted_cancel_integration"
    task_manager._shutting_down = False
    task_manager.abandon_task(task_id)

    def _run():
        started.set()
        while True:
            token.raise_if_cancelled()
            token.wait(0.01)

    task_manager.run_in_background(
        _run,
        task_id=task_id,
        cancellation_token=token,
        timeout_sec=2.0,
    )
    try:
        assert started.wait(1.0) is True
        assert task_manager.cancel_task(task_id, reason="integration_cancel") is True
        assert task_manager.wait_for_tasks((task_id,), timeout_ms=1000) is True
        assert token.cancelled is True
        assert token.reason == "integration_cancel"
    finally:
        task_manager.abandon_task(task_id)
