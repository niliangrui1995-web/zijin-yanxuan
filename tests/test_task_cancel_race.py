# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from PyQt6.QtCore import QCoreApplication

from infra.tasks.lifecycle import CancellationToken
from infra.tasks.task_scheduler import BackgroundWorker, task_manager


def _pump_events_until(predicate, timeout: float = 2.0) -> bool:
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_submit_injects_cancellation_token_and_keeps_zero_arg_compatibility() -> None:
    captured: list[CancellationToken] = []
    results: list[str] = []
    task_manager.cancel_all()
    task_manager._shutting_down = False

    def cancellable_task(cancellation_token: CancellationToken) -> str:
        captured.append(cancellation_token)
        return "new"

    task_manager.run_in_background(cancellable_task, on_success=results.append, task_id="token-submit")
    assert _pump_events_until(lambda: results == ["new"])
    assert len(captured) == 1
    assert isinstance(captured[0], CancellationToken)

    task_manager.run_in_background(lambda: "legacy", on_success=results.append, task_id="legacy-submit")
    assert _pump_events_until(lambda: results == ["new", "legacy"])


def test_background_worker_supports_positional_and_keyword_only_tokens() -> None:
    received: list[CancellationToken] = []

    def positional(cancellation_token: CancellationToken, /) -> str:
        received.append(cancellation_token)
        return "positional"

    def keyword_only(*, cancellation_token: CancellationToken) -> str:
        received.append(cancellation_token)
        return "keyword"

    for task in (positional, keyword_only):
        worker = BackgroundWorker(task)
        worker.run()

    assert len(received) == 2
    assert all(isinstance(token, CancellationToken) for token in received)


def test_cancel_suppresses_a_success_callback_already_queued(qt_application) -> None:
    callbacks: list[str] = []
    task_id = "cancel-delayed-callback"
    task_manager.cancel_all()
    task_manager._shutting_down = False

    task_manager.run_in_background(lambda: "stale", on_success=callbacks.append, task_id=task_id)
    worker = task_manager.active_workers[task_id]
    assert worker.terminated_event.wait(1.0)
    assert task_manager.cancel_task(task_id, reason="cancel_after_finish") is True

    qt_application.processEvents()
    assert callbacks == []
    assert _pump_events_until(lambda: task_manager.active_count == 0)


def test_new_task_overwrite_rejects_old_delayed_finish_callback(qt_application) -> None:
    callbacks: list[str] = []
    task_id = "overwrite-delayed-callback"
    task_manager.cancel_all()
    task_manager._shutting_down = False

    task_manager.run_in_background(lambda: "old", on_success=callbacks.append, task_id=task_id)
    old_worker = task_manager.active_workers[task_id]
    assert old_worker.terminated_event.wait(1.0)
    assert task_manager.abandon_task(task_id) is True

    task_manager.run_in_background(lambda: "new", on_success=callbacks.append, task_id=task_id)
    new_worker = task_manager.active_workers[task_id]
    assert new_worker is not old_worker
    assert new_worker.terminated_event.wait(1.0)

    assert _pump_events_until(lambda: task_manager.active_count == 0)
    assert callbacks == ["new"]
