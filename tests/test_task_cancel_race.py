# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from functools import partial
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication

from infra.tasks.lifecycle import CancellationToken, accepts_cancellation_token, invoke_with_cancellation
from infra.tasks.owner_lifecycle import TaskLifecycleGroup
from infra.tasks.task_scheduler import BackgroundWorker, task_manager
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks


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


def test_plain_variadic_wrapper_keeps_legacy_no_token_call() -> None:
    token = CancellationToken()

    def downstream() -> str:
        return "legacy"

    def forwarding_wrapper(**kwargs) -> str:
        return downstream(**kwargs)

    assert invoke_with_cancellation(forwarding_wrapper, token) == "legacy"


def test_marked_variadic_callable_explicitly_receives_token() -> None:
    token = CancellationToken()
    received: list[CancellationToken] = []

    @accepts_cancellation_token
    def variadic_task(**kwargs) -> str:
        received.append(kwargs["cancellation_token"])
        return "marked"

    assert invoke_with_cancellation(variadic_task, token) == "marked"
    assert received == [token]


def test_partial_prebound_cancellation_token_is_not_overwritten() -> None:
    outer_token = CancellationToken()
    prebound_token = CancellationToken()

    def task(*, cancellation_token: CancellationToken) -> CancellationToken:
        return cancellation_token

    assert invoke_with_cancellation(
        partial(task, cancellation_token=prebound_token),
        outer_token,
    ) is prebound_token


def test_marked_bound_and_uninspectable_legacy_callables_remain_compatible() -> None:
    token = CancellationToken()

    class MarkedCallable:
        @accepts_cancellation_token
        def __call__(self, **kwargs) -> CancellationToken:
            return kwargs["cancellation_token"]

    class UninspectableCallable:
        @property
        def __signature__(self):
            raise ValueError("legacy callable has no signature")

        def __call__(self) -> str:
            return "legacy"

    assert invoke_with_cancellation(MarkedCallable(), token) is token
    assert invoke_with_cancellation(UninspectableCallable(), token) == "legacy"


def test_marked_partial_callable_and_uninspectable_variadic_callable_receive_token() -> None:
    token = CancellationToken()

    class MarkedCallable:
        @accepts_cancellation_token
        def __call__(self, *args, **kwargs) -> tuple[tuple, CancellationToken]:
            return args, kwargs["cancellation_token"]

    class MarkedUninspectableCallable:
        @property
        def __signature__(self):
            raise ValueError("explicit variadic contract")

        @accepts_cancellation_token
        def __call__(self, **kwargs) -> CancellationToken:
            return kwargs["cancellation_token"]

    args, received = invoke_with_cancellation(partial(MarkedCallable(), "bound"), token)
    assert args == ("bound",)
    assert received is token
    assert invoke_with_cancellation(MarkedUninspectableCallable(), token) is token


def test_cancellation_receipt_waits_for_real_worker_physical_termination(qt_application) -> None:
    task_id = "cancel-receipt-physical-termination"
    started = threading.Event()
    release = threading.Event()
    owner = SimpleNamespace(_task_lifecycle=TaskLifecycleGroup(task_manager))
    task_manager.cancel_all()
    task_manager._shutting_down = False

    def slow_exit(_token: CancellationToken) -> str:
        started.set()
        release.wait(1.0)
        return "released"

    try:
        owner._task_lifecycle.run_background(
            "physical-exit",
            slow_exit,
            task_id=task_id,
            timeout_sec=5.0,
            runner=task_manager,
        )
        assert started.wait(1.0)

        receipt = cancel_background_preload_tasks(
            owner,
            lifecycle_names=("physical-exit",),
            task_ids=(),
            reason="step_timeout",
            reset_state=lambda: None,
            runner=task_manager,
        )

        assert receipt.accepted is True
        assert receipt.is_settled() is False
        assert receipt.active_task_ids() == (task_id,)

        release.set()
        assert _pump_events_until(receipt.is_settled)
        assert receipt.active_task_ids() == ()
    finally:
        release.set()
        _pump_events_until(lambda: not task_manager.is_active_task(task_id))
        task_manager.abandon_task(task_id)


def test_cancellation_receipt_waits_for_generated_id_submission_handshake() -> None:
    class _BlockingGeneratedRunner:
        def __init__(self):
            self.submit_started = threading.Event()
            self.release_submit = threading.Event()
            self.active = set()
            self.submitted_token = None
            self.cancel_calls = []

        def run_in_background(self, fn, **kwargs):
            del fn
            self.submitted_token = kwargs.get("cancellation_token")
            self.submit_started.set()
            assert self.release_submit.wait(1.0)
            self.active.add("receipt-generated")
            return "receipt-generated"

        def is_task_token_active(self, task_id, token):
            return task_id in self.active and token is self.submitted_token

        def is_task_unsettled(self, task_id):
            return task_id in self.active

        def cancel_task(self, task_id, *, reason="cancelled"):
            self.cancel_calls.append((task_id, reason))
            return True

    runner = _BlockingGeneratedRunner()
    owner = SimpleNamespace(_task_lifecycle=TaskLifecycleGroup(runner))

    thread = threading.Thread(
        target=lambda: owner._task_lifecycle.run_background(
            "generated-receipt",
            lambda _token: "unused",
            task_id=None,
            timeout_sec=5.0,
        )
    )
    thread.start()
    assert runner.submit_started.wait(1.0)

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("generated-receipt",),
        task_ids=(),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert receipt.accepted is True
    assert receipt.is_settled() is False
    assert receipt.status()["task_ids"] == []

    runner.release_submit.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert receipt.is_settled() is False
    assert receipt.status()["task_ids"] == ["receipt-generated"]
    assert runner.cancel_calls == [("receipt-generated", "cancelled_during_submit")]

    runner.active.clear()
    assert receipt.is_settled() is True


def test_same_id_replacement_receipt_waits_for_retired_and_current_workers(qt_application) -> None:
    task_id = "same-id-physical-replacement"
    old_started = threading.Event()
    release_old = threading.Event()
    owner = SimpleNamespace(_task_lifecycle=TaskLifecycleGroup(task_manager))
    task_manager.cancel_all()
    task_manager._shutting_down = False

    def old_task(_token: CancellationToken) -> str:
        old_started.set()
        release_old.wait(1.0)
        return "old"

    try:
        owner._task_lifecycle.run_background(
            "same-name",
            old_task,
            task_id=task_id,
            timeout_sec=5.0,
            runner=task_manager,
        )
        assert old_started.wait(1.0)
        owner._task_lifecycle.run_background(
            "same-name",
            lambda _token: "new",
            task_id=task_id,
            timeout_sec=5.0,
            runner=task_manager,
        )

        receipt = cancel_background_preload_tasks(
            owner,
            lifecycle_names=("same-name",),
            task_ids=(),
            reason="step_timeout",
            reset_state=lambda: None,
            runner=task_manager,
        )

        assert _pump_events_until(lambda: task_manager.is_task_unsettled(task_id))
        assert receipt.is_settled() is False
        release_old.set()
        assert _pump_events_until(receipt.is_settled)
    finally:
        release_old.set()
        _pump_events_until(lambda: not task_manager.is_task_unsettled(task_id))
        task_manager.abandon_task(task_id)


def test_cancel_all_tracks_running_worker_and_settles_removed_queued_worker(qt_application) -> None:
    running_id = "cancel-all-running-receipt"
    queued_id = "cancel-all-queued-receipt"
    running_started = threading.Event()
    release_running = threading.Event()
    previous_max = task_manager.thread_pool.maxThreadCount()
    task_manager.cancel_all()
    task_manager._shutting_down = False
    task_manager.thread_pool.setMaxThreadCount(1)

    def running_task(cancellation_token: CancellationToken) -> str:
        del cancellation_token
        running_started.set()
        release_running.wait(1.0)
        return "released"

    try:
        task_manager.run_in_background(running_task, task_id=running_id)
        assert running_started.wait(1.0)
        task_manager.run_in_background(lambda: "queued", task_id=queued_id)
        assert task_manager.is_task_unsettled(queued_id) is True

        task_manager.cancel_all(reason="test_cancel_all")

        assert task_manager.is_task_unsettled(running_id) is True
        assert _pump_events_until(lambda: not task_manager.is_task_unsettled(queued_id))
        release_running.set()
        assert _pump_events_until(lambda: not task_manager.is_task_unsettled(running_id))
    finally:
        release_running.set()
        task_manager.thread_pool.setMaxThreadCount(previous_max)
        _pump_events_until(lambda: not task_manager.is_task_unsettled(running_id))
        task_manager.abandon_task(running_id)
        task_manager.abandon_task(queued_id)


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
