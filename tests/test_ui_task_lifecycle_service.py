from __future__ import annotations

import logging

from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from infra.tasks.lifecycle import CancellationToken


class _QueuedRunner:
    def __init__(self):
        self.calls = []
        self.jobs = []

    def run_in_background(self, fn, **kwargs):
        self.calls.append(("run_in_background", kwargs.get("task_id"), dict(kwargs)))
        self.jobs.append((fn, dict(kwargs)))
        return str(kwargs.get("task_id") or "generated")

    def abandon_task(self, task_id):
        self.calls.append(("abandon_task", task_id))
        return True

    def cancel_task(self, task_id, *, reason="cancelled"):
        self.calls.append(("cancel_task", task_id, reason))
        return True

    def wait_for_tasks(self, task_ids, *, timeout_ms):
        self.calls.append(("wait_for_tasks", tuple(task_ids), timeout_ms))
        return True


def test_lifecycle_group_replaces_named_task_and_passes_shared_deadline_token():
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    observed = []

    first_token = lifecycle.run_background(
        "snapshot",
        lambda token: observed.append(("first", token)) or "first",
        task_id="snapshot-task",
        timeout_sec=5.0,
    )
    second_token = lifecycle.run_background(
        "snapshot",
        lambda token: observed.append(("second", token)) or "second",
        task_id="snapshot-task",
        timeout_sec=2.0,
    )

    assert first_token.cancelled is True
    assert first_token.reason == "replaced"
    assert second_token.cancelled is False
    assert ("abandon_task", "snapshot-task") in runner.calls

    run_fn, kwargs = runner.jobs[-1]
    assert kwargs["cancellation_token"] is second_token
    assert kwargs["timeout_sec"] == 2.0
    assert run_fn() == "second"
    kwargs["on_success"]("second")
    assert observed == [("second", second_token)]
    assert lifecycle.active_names == ()


def test_lifecycle_group_shutdown_cancels_owned_tasks_and_waits_with_a_bound():
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    token = lifecycle.run_background(
        "fund-load",
        lambda _token: "unused",
        task_id="fund-load-task",
        timeout_sec=30.0,
    )

    assert lifecycle.shutdown(timeout_ms=37) is True

    assert token.cancelled is True
    assert token.reason == "owner_shutdown"
    assert ("cancel_task", "fund-load-task", "owner_shutdown") in runner.calls
    assert ("wait_for_tasks", ("fund-load-task",), 37) in runner.calls
    assert lifecycle.active_names == ()


def test_lifecycle_group_supports_qthread_token_without_background_submission():
    lifecycle = TaskLifecycleGroup(_QueuedRunner())

    token = lifecycle.begin("scan", timeout_sec=1.0)

    assert lifecycle.cancel("scan", reason="user_cancelled") is True
    assert token.cancelled is True
    assert token.reason == "user_cancelled"
    assert lifecycle.cancel("scan", reason="duplicate") is False


def test_invoke_with_cancellation_supports_new_and_legacy_callables():
    token = CancellationToken()
    observed = []

    assert invoke_with_cancellation(
        lambda *, cancellation_token=None: observed.append(cancellation_token) or "new",
        token,
    ) == "new"
    assert invoke_with_cancellation(lambda: "legacy", token) == "legacy"
    assert invoke_with_cancellation(lambda: "legacy", None) == "legacy"
    assert observed == [token]


def test_lifecycle_group_suppresses_replaced_task_callback_queued_before_new_submission():
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    observed = []

    lifecycle.run_background(
        "quote",
        lambda _token: "stale",
        task_id="quote-old",
        timeout_sec=5.0,
        on_success=lambda result: observed.append(result),
    )
    old_run, old_kwargs = runner.jobs[-1]
    assert old_run() == "stale"

    lifecycle.run_background(
        "quote",
        lambda _token: "current",
        task_id="quote-new",
        timeout_sec=5.0,
        on_success=lambda result: observed.append(result),
    )
    current_run, current_kwargs = runner.jobs[-1]

    old_kwargs["on_success"]("stale")
    assert observed == []

    assert current_run() == "current"
    current_kwargs["on_success"]("current")
    assert observed == ["current"]


def test_lifecycle_group_shutdown_suppresses_already_queued_owner_callback():
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    observed = []

    token = lifecycle.run_background(
        "load",
        lambda _token: "payload",
        task_id="owner-load",
        timeout_sec=5.0,
        on_success=lambda result: observed.append(result),
    )
    run_fn, kwargs = runner.jobs[-1]
    assert run_fn() == "payload"

    lifecycle.shutdown(timeout_ms=25)
    kwargs["on_success"]("payload")

    assert token.cancelled is True
    assert token.reason == "owner_shutdown"
    assert observed == []


def test_lifecycle_group_contains_exception_from_queued_owner_callback(caplog, monkeypatch):
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    reported = []
    monkeypatch.setattr(
        "infra.tasks.owner_lifecycle.sys.excepthook",
        lambda exc_type, exc, traceback: reported.append((exc_type, exc, traceback)),
    )

    def _raise_from_ui_callback(_):
        raise TypeError("callback contract mismatch")

    lifecycle.run_background(
        "period-return",
        lambda _token: "payload",
        task_id="period-return-task",
        timeout_sec=5.0,
        on_success=_raise_from_ui_callback,
    )
    _run_fn, kwargs = runner.jobs[-1]

    with caplog.at_level(logging.ERROR, logger="infra.tasks.owner_lifecycle"):
        kwargs["on_success"]("payload")

    assert lifecycle.active_names == ()
    assert "[任务生命周期][period-return] 主线程回调异常" in caplog.text
    assert "callback contract mismatch" in caplog.text
    assert reported[0][0] is TypeError
    assert str(reported[0][1]) == "callback contract mismatch"
    assert reported[0][2] is not None
