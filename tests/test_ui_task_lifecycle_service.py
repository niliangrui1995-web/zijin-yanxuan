from __future__ import annotations

import logging
import threading

import pytest

from app.services.ui_task_lifecycle_service import (
    TaskLifecycleGroup,
    TaskSubmissionStatus,
    invoke_with_cancellation,
    task_unsettled_status,
)
from infra.tasks.lifecycle import (
    CancellationToken,
)
from infra.tasks.lifecycle import (
    task_unsettled_status as infra_task_unsettled_status,
)


def test_ui_task_lifecycle_facade_reexports_physical_task_status_probe():
    assert task_unsettled_status is infra_task_unsettled_status



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

    @staticmethod
    def is_task_unsettled(task_id):
        del task_id
        return False


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
    wait_calls = [call for call in runner.calls if call[0] == "wait_for_tasks"]
    assert len(wait_calls) == 1
    assert wait_calls[0][1] == ("fund-load-task",)
    assert 0 <= wait_calls[0][2] <= 37
    assert lifecycle.active_names == ()


def test_lifecycle_group_exposes_current_task_ids_for_named_cancellation():
    runner = _QueuedRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "dynamic-preload",
        lambda _token: "unused",
        task_id="runtime-generated-preload",
        timeout_sec=30.0,
    )

    assert lifecycle.task_ids_for(("missing", "dynamic-preload")) == (
        "runtime-generated-preload",
    )

    lifecycle.cancel("dynamic-preload", reason="step_timeout")
    assert lifecycle.task_ids_for(("dynamic-preload",)) == ()


def test_lifecycle_group_tracks_runner_generated_task_id():
    class _GeneratedIdRunner(_QueuedRunner):
        def run_in_background(self, fn, **kwargs):
            self.calls.append((fn, kwargs))
            return "runner-generated-task"

    runner = _GeneratedIdRunner()
    lifecycle = TaskLifecycleGroup(runner)

    lifecycle.run_background(
        "dynamic-preload",
        lambda _token: "unused",
        task_id=None,
        timeout_sec=30.0,
    )

    assert lifecycle.task_ids_for(("dynamic-preload",)) == ("runner-generated-task",)


def test_lifecycle_group_ignores_legacy_boolean_submit_result():
    class _LegacyRunner(_QueuedRunner):
        def run_in_background(self, fn, **kwargs):
            self.calls.append((fn, kwargs))
            return True

    lifecycle = TaskLifecycleGroup(_LegacyRunner())
    lifecycle.run_background(
        "legacy-preload",
        lambda _token: "unused",
        task_id="caller-supplied-task",
        timeout_sec=30.0,
    )

    assert lifecycle.task_ids_for(("legacy-preload",)) == ("caller-supplied-task",)


def test_submit_background_reports_unknown_for_none_without_identity_probe():
    class _NoneRunner(_QueuedRunner):
        is_task_unsettled = None

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, dict(kwargs)))
            return None

    lifecycle = TaskLifecycleGroup(_NoneRunner())

    receipt = lifecycle.submit_background(
        "unconfirmed",
        lambda _token: "unused",
        task_id="unconfirmed-task",
        timeout_sec=30.0,
    )

    assert receipt.status is TaskSubmissionStatus.UNKNOWN
    assert receipt.task_id == "unconfirmed-task"
    assert receipt.token.cancelled is False
    assert lifecycle.is_current("unconfirmed", receipt.token) is True


def test_submit_background_uses_token_identity_probe_for_explicit_status():
    class _ProbeRunner(_QueuedRunner):
        def __init__(self, accepted):
            super().__init__()
            self.accepted = accepted
            self.token = None

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, dict(kwargs)))
            self.token = kwargs["cancellation_token"]
            return None

        def is_task_token_active(self, task_id, token):
            del task_id
            return self.accepted and token is self.token

    accepted_lifecycle = TaskLifecycleGroup(_ProbeRunner(True))
    accepted = accepted_lifecycle.submit_background(
        "accepted",
        lambda _token: "unused",
        task_id="accepted-task",
        timeout_sec=30.0,
    )
    assert accepted.status is TaskSubmissionStatus.ACCEPTED

    rejected_lifecycle = TaskLifecycleGroup(_ProbeRunner(False))
    rejected = rejected_lifecycle.submit_background(
        "rejected",
        lambda _token: "unused",
        task_id="rejected-task",
        timeout_sec=30.0,
    )
    assert rejected.status is TaskSubmissionStatus.REJECTED
    assert rejected.token.cancelled is True
    assert rejected.token.reason == "submission_rejected"


def test_submit_background_treats_non_boolean_identity_probe_as_unknown():
    class _InvalidProbeRunner(_QueuedRunner):
        @staticmethod
        def is_task_token_active(task_id, token):
            del task_id, token
            return "accepted"

    lifecycle = TaskLifecycleGroup(_InvalidProbeRunner())

    receipt = lifecycle.submit_background(
        "invalid-probe",
        lambda _token: "unused",
        task_id="invalid-probe-task",
        timeout_sec=30.0,
    )

    assert receipt.status is TaskSubmissionStatus.UNKNOWN
    assert receipt.token.cancelled is False


def test_submit_background_treats_synchronous_completion_as_accepted():
    class _SynchronousRunner(_QueuedRunner):
        is_task_unsettled = None

        @staticmethod
        def run_in_background(fn, **kwargs):
            result = fn()
            kwargs["on_success"](result)
            return None

    lifecycle = TaskLifecycleGroup(_SynchronousRunner())
    delivered = []

    receipt = lifecycle.submit_background(
        "synchronous",
        lambda _token: "done",
        task_id="synchronous-task",
        timeout_sec=30.0,
        on_success=delivered.append,
    )

    assert receipt.status is TaskSubmissionStatus.ACCEPTED
    assert delivered == ["done"]
    assert lifecycle.active_names == ()


def test_submit_background_after_shutdown_is_rejected_and_terminates_once():
    lifecycle = TaskLifecycleGroup(_QueuedRunner())
    assert lifecycle.shutdown(timeout_ms=1) is True
    terminated = []

    receipt = lifecycle.submit_background(
        "closed-owner",
        lambda _token: "unused",
        task_id="closed-owner-task",
        timeout_sec=30.0,
        on_terminated=lambda: terminated.append(True),
    )

    assert receipt.status is TaskSubmissionStatus.REJECTED
    assert receipt.token.cancelled is True
    assert receipt.token.reason == "owner_shutdown"
    assert terminated == [True]
    assert lifecycle.active_names == ()
    assert lifecycle.submissions_settled_for(("closed-owner",)) is True


def test_lifecycle_group_retains_task_when_physical_probe_is_unknown():
    class _ActiveOnlyRunner(_QueuedRunner):
        is_task_unsettled = None
        wait_for_tasks = None

        @staticmethod
        def is_active_task(task_id):
            del task_id
            return False

    runner = _ActiveOnlyRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "legacy-physical-proof",
        lambda _token: "unused",
        task_id="legacy-physical-task",
        timeout_sec=30.0,
    )

    assert lifecycle.cancel("legacy-physical-proof", reason="shutdown") is True
    assert lifecycle.task_ids_for(("legacy-physical-proof",)) == (
        "legacy-physical-task",
    )


def test_lifecycle_accepts_explicit_wait_receipt_from_legacy_runner():
    class _WaitableLegacyRunner(_QueuedRunner):
        is_task_unsettled = None

        def __init__(self):
            super().__init__()
            self.settled = False

        @staticmethod
        def is_active_task(task_id):
            del task_id
            return False

        def wait_for_tasks(self, task_ids, *, timeout_ms):
            self.calls.append(("wait_for_tasks", tuple(task_ids), timeout_ms))
            return self.settled

    runner = _WaitableLegacyRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "legacy-wait",
        lambda _token: "unused",
        task_id="legacy-wait-task",
        timeout_sec=30.0,
    )

    assert lifecycle.shutdown(timeout_ms=1) is False
    assert lifecycle.task_ids_for(("legacy-wait",)) == ("legacy-wait-task",)

    runner.settled = True
    assert lifecycle.shutdown(timeout_ms=1) is True
    assert lifecycle.task_ids_for(("legacy-wait",)) == ()


def test_lifecycle_rejects_truthy_non_boolean_wait_receipt():
    class _TruthyWaitRunner(_QueuedRunner):
        @staticmethod
        def is_task_unsettled(task_id):
            del task_id
            return True

        def wait_for_tasks(self, task_ids, *, timeout_ms):
            self.calls.append(("wait_for_tasks", tuple(task_ids), timeout_ms))
            return "settled"

    lifecycle = TaskLifecycleGroup(_TruthyWaitRunner())
    lifecycle.run_background(
        "truthy-wait",
        lambda _token: "unused",
        task_id="truthy-wait-task",
        timeout_sec=30.0,
    )

    assert lifecycle.shutdown(timeout_ms=1) is False
    assert lifecycle.task_ids_for(("truthy-wait",)) == ("truthy-wait-task",)


@pytest.mark.parametrize("mode", ["raises", "unbounded-signature"])
def test_lifecycle_wait_failure_is_bounded_and_keeps_task_tracked(mode):
    class _BrokenWaitRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.wait_body_called = False

        @staticmethod
        def is_task_unsettled(task_id):
            del task_id
            return True

        if mode == "raises":
            def wait_for_tasks(self, task_ids, *, timeout_ms):
                del task_ids, timeout_ms
                self.wait_body_called = True
                raise RuntimeError("wait failed")
        else:
            def wait_for_tasks(self, task_ids):
                del task_ids
                self.wait_body_called = True
                return True

    runner = _BrokenWaitRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "broken-wait",
        lambda _token: "unused",
        task_id="broken-wait-task",
        timeout_sec=30.0,
    )

    assert lifecycle.shutdown(timeout_ms=1) is False
    assert lifecycle.task_ids_for(("broken-wait",)) == ("broken-wait-task",)
    assert runner.wait_body_called is (mode == "raises")


def test_lifecycle_multiple_runners_share_one_shutdown_deadline(monkeypatch):
    class _BudgetRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.wait_budgets = []

        @staticmethod
        def is_task_unsettled(task_id):
            del task_id
            return True

        def wait_for_tasks(self, task_ids, *, timeout_ms):
            del task_ids
            self.wait_budgets.append(timeout_ms)
            return False

    first = _BudgetRunner()
    second = _BudgetRunner()
    lifecycle = TaskLifecycleGroup(first)
    lifecycle.run_background(
        "first-runner",
        lambda _token: "unused",
        task_id="first-task",
        timeout_sec=30.0,
    )
    lifecycle.run_background(
        "second-runner",
        lambda _token: "unused",
        task_id="second-task",
        timeout_sec=30.0,
        runner=second,
    )
    moments = iter((100.0, 100.005, 100.018))
    monkeypatch.setattr("infra.tasks.owner_lifecycle.time.monotonic", lambda: next(moments))

    assert lifecycle.shutdown(timeout_ms=20) is False
    assert len(first.wait_budgets) == len(second.wait_budgets) == 1
    assert 0 <= second.wait_budgets[0] < first.wait_budgets[0] <= 20


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


def test_lifecycle_group_keeps_generated_id_until_physical_termination():
    class _UnsettledRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.unsettled = {"generated"}

        def is_task_unsettled(self, task_id):
            return task_id in self.unsettled

    runner = _UnsettledRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "generated-load",
        lambda _token: "done",
        task_id=None,
        timeout_sec=5.0,
    )

    _run_fn, kwargs = runner.jobs[-1]
    kwargs["on_success"]("done")

    assert lifecycle.active_names == ()
    assert lifecycle.task_ids_for(("generated-load",)) == ("generated",)
    runner.unsettled.clear()
    assert lifecycle.task_ids_for(("generated-load",)) == ()


def test_lifecycle_group_cancels_generated_task_returned_after_concurrent_cancel():
    class _BlockingRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.submit_started = threading.Event()
            self.release_submit = threading.Event()
            self.unsettled = set()
            self.submitted_token = None

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, dict(kwargs)))
            self.submitted_token = kwargs.get("cancellation_token")
            self.submit_started.set()
            assert self.release_submit.wait(1.0)
            self.unsettled.add("late-generated")
            return "late-generated"

        def is_task_token_active(self, task_id, token):
            return task_id in self.unsettled and token is self.submitted_token

        def is_task_unsettled(self, task_id):
            return task_id in self.unsettled

    runner = _BlockingRunner()
    lifecycle = TaskLifecycleGroup(runner)
    submitted = {}

    def _submit() -> None:
        submitted["token"] = lifecycle.run_background(
            "late-load",
            lambda _token: "unused",
            task_id=None,
            timeout_sec=5.0,
        )

    thread = threading.Thread(target=_submit)
    thread.start()
    assert runner.submit_started.wait(1.0)
    assert lifecycle.submissions_settled_for(("late-load",)) is False
    assert lifecycle.cancel("late-load", reason="step_timeout") is True

    runner.release_submit.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert submitted["token"].cancelled is True
    assert ("cancel_task", "late-generated", "cancelled_during_submit") in runner.calls
    assert lifecycle.submissions_settled_for(("late-load",)) is True
    assert lifecycle.task_ids_for(("late-load",)) == ("late-generated",)

    runner.unsettled.clear()
    assert lifecycle.task_ids_for(("late-load",)) == ()


def test_lifecycle_group_submission_exception_cleans_placeholder_and_terminates_once():
    class _RaisingRunner(_QueuedRunner):
        def run_in_background(self, fn, **kwargs):
            del fn, kwargs
            raise RuntimeError("submit failed")

    lifecycle = TaskLifecycleGroup(_RaisingRunner())
    terminated = []

    with pytest.raises(RuntimeError, match="submit failed"):
        lifecycle.run_background(
            "raising-load",
            lambda _token: "unused",
            task_id="raising-task",
            timeout_sec=5.0,
            on_terminated=lambda: terminated.append(True),
        )

    assert lifecycle.active_names == ()
    assert lifecycle.task_ids_for(("raising-load",)) == ()
    assert lifecycle.submissions_settled_for(("raising-load",)) is True
    assert terminated == [True]


def test_lifecycle_group_control_signal_cleans_placeholder_and_terminates_once():
    class _InterruptingRunner(_QueuedRunner):
        def run_in_background(self, fn, **kwargs):
            del fn, kwargs
            raise KeyboardInterrupt("stop submission")

    lifecycle = TaskLifecycleGroup(_InterruptingRunner())
    terminated = []

    with pytest.raises(KeyboardInterrupt, match="stop submission"):
        lifecycle.run_background(
            "interrupting-load",
            lambda _token: "unused",
            task_id="interrupting-task",
            timeout_sec=5.0,
            on_terminated=lambda: terminated.append(True),
        )

    assert lifecycle.active_names == ()
    assert lifecycle.task_ids_for(("interrupting-load",)) == ()
    assert lifecycle.submissions_settled_for(("interrupting-load",)) is True
    assert terminated == [True]


def test_lifecycle_group_rejected_submission_delivers_not_started_terminal_callback():
    class _RejectingRunner(_QueuedRunner):
        def is_task_token_active(self, task_id, token):
            del task_id, token
            return False

    lifecycle = TaskLifecycleGroup(_RejectingRunner())
    terminated = []

    token = lifecycle.run_background(
        "workspace-background-snapshot",
        lambda _token: "unused",
        task_id="rejected-snapshot",
        timeout_sec=5.0,
        on_terminated=lambda: terminated.append(True),
    )

    assert token.cancelled is True
    assert token.reason == "submission_rejected"
    assert terminated == [True]
    assert lifecycle.active_names == ()


def test_lifecycle_group_probe_exception_keeps_generated_task_tracked():
    class _ProbeErrorRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.unsettled = {"probe-generated"}

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, dict(kwargs)))
            return "probe-generated"

        def is_task_token_active(self, task_id, token):
            del task_id, token
            raise OSError("probe unavailable")

        def is_task_unsettled(self, task_id):
            return task_id in self.unsettled

    runner = _ProbeErrorRunner()
    lifecycle = TaskLifecycleGroup(runner)
    receipt = lifecycle.submit_background(
        "probe-load",
        lambda _token: "unused",
        task_id=None,
        timeout_sec=5.0,
    )

    assert receipt.status is TaskSubmissionStatus.UNKNOWN
    assert receipt.token.cancelled is False
    assert lifecycle.task_ids_for(("probe-load",)) == ("probe-generated",)
    assert lifecycle.cancel("probe-load", reason="step_timeout") is True
    assert ("cancel_task", "probe-generated", "step_timeout") in runner.calls


def test_lifecycle_group_shutdown_timeout_retains_ids_for_retry():
    class _TimeoutRunner(_QueuedRunner):
        def __init__(self):
            super().__init__()
            self.unsettled = {"slow-shutdown"}

        def is_task_unsettled(self, task_id):
            return task_id in self.unsettled

        def wait_for_tasks(self, task_ids, *, timeout_ms):
            self.calls.append(("wait_for_tasks", tuple(task_ids), timeout_ms))
            return not any(task_id in self.unsettled for task_id in task_ids)

    runner = _TimeoutRunner()
    lifecycle = TaskLifecycleGroup(runner)
    lifecycle.run_background(
        "slow-load",
        lambda _token: "unused",
        task_id="slow-shutdown",
        timeout_sec=5.0,
    )

    assert lifecycle.shutdown(timeout_ms=1) is False
    assert lifecycle.task_ids_for(("slow-load",)) == ("slow-shutdown",)
    assert lifecycle.shutdown(timeout_ms=1) is False

    runner.unsettled.clear()
    assert lifecycle.shutdown(timeout_ms=1) is True
    assert lifecycle.task_ids_for(("slow-load",)) == ()
