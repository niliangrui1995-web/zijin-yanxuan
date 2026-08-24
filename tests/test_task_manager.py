import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication

import infra.tasks.task_scheduler as task_scheduler_module
from core.task_manager import UserFacingTaskError, task_manager
from infra.tasks.task_scheduler import BackgroundWorker, _task_thread_pool_max_count


def _pump_events_until(predicate, timeout=3.0):
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_task_thread_pool_max_count_defaults_and_accepts_override():
    assert _task_thread_pool_max_count({}) == 12
    assert _task_thread_pool_max_count({"VCP_TASK_THREAD_POOL_MAX_THREADS": "5"}) == 5
    assert _task_thread_pool_max_count({"VCP_TASK_THREAD_POOL_MAX_THREADS": "bad"}) == 12


def test_task_manager_dedupes_same_task_id():
    task_manager.cancel_all()
    task_manager._shutting_down = False

    calls = []
    calls_lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def slow_task():
        with calls_lock:
            calls.append(time.time())
        started.set()
        assert release.wait(timeout=3)
        return "ok"

    task_manager.run_in_background(slow_task, task_id="dup_test")
    assert started.wait(1)
    task_manager.run_in_background(slow_task, task_id="dup_test")

    assert task_manager.active_count == 1
    release.set()
    assert _pump_events_until(lambda: task_manager.active_count == 0)
    assert len(calls) == 1


def test_task_manager_start_failure_releases_slot_and_allows_same_id_retry(monkeypatch):
    class _FlakyPool:
        def __init__(self):
            self.starts = 0

        def start(self, worker, *args):
            del worker, args
            self.starts += 1
            if self.starts == 1:
                raise RuntimeError("thread pool start failed")

        @staticmethod
        def clear():
            return None

    pool = _FlakyPool()
    monkeypatch.setattr(task_manager, "thread_pool", pool)
    task_manager.active_workers.clear()
    task_manager._shutting_down = False
    first = BackgroundWorker(lambda: "first")

    with pytest.raises(RuntimeError, match="thread pool start failed"):
        task_manager.submit_task(first, task_id="start-failure-retry")

    assert task_manager.is_active_task("start-failure-retry") is False
    assert task_manager.is_task_unsettled("start-failure-retry") is False
    assert first.cancellation_token.cancelled is True
    assert first.cancellation_token.reason == "submission_failed"
    assert first.terminated_event.is_set() is True

    second = BackgroundWorker(lambda: "second")
    try:
        assert task_manager.submit_task(second, task_id="start-failure-retry") == "start-failure-retry"
        assert task_manager.active_workers["start-failure-retry"] is second
    finally:
        task_manager.cancel_all()
        second.run()
        _pump_events_until(lambda: not task_manager.is_task_unsettled("start-failure-retry"))


def test_task_manager_start_interrupt_cleans_slot_without_swallowing_control_signal(monkeypatch):
    class _InterruptingPool:
        @staticmethod
        def start(worker, *args):
            del worker, args
            raise KeyboardInterrupt("stop submission")

    monkeypatch.setattr(task_manager, "thread_pool", _InterruptingPool())
    task_manager.active_workers.clear()
    task_manager._retired_workers.clear()
    task_manager._shutting_down = False
    worker = BackgroundWorker(lambda: "unused")

    with pytest.raises(KeyboardInterrupt, match="stop submission"):
        task_manager.submit_task(worker, task_id="interrupting-start")

    assert task_manager.is_active_task("interrupting-start") is False
    assert worker.cancellation_token.cancelled is True
    assert worker.cancellation_token.reason == "submission_failed"
    assert worker.terminated_event.is_set() is True


def test_cancel_all_without_try_take_keeps_worker_unsettled_until_terminated(monkeypatch):
    class _LegacyPool:
        def __init__(self):
            self.workers = []
            self.clear_calls = 0

        def start(self, worker, *args):
            del args
            self.workers.append(worker)

        def clear(self):
            self.clear_calls += 1

    pool = _LegacyPool()
    monkeypatch.setattr(task_manager, "thread_pool", pool)
    task_manager.active_workers.clear()
    task_manager._retired_workers.clear()
    task_manager._shutting_down = False
    task_id = "legacy-pool-cancel"

    task_manager.run_in_background(lambda: "never-delivered", task_id=task_id)
    worker = task_manager.active_workers[task_id]
    task_manager.cancel_all(reason="legacy_pool_cancel")

    assert pool.clear_calls == 0
    assert task_manager.is_task_unsettled(task_id) is True
    assert worker.terminated_event.is_set() is False

    worker.run()
    assert worker.terminated_event.is_set() is True
    assert _pump_events_until(lambda: not task_manager.is_task_unsettled(task_id))


def test_cancel_all_try_take_exception_does_not_clear_or_drop_worker(monkeypatch):
    class _BrokenTakePool:
        def __init__(self):
            self.workers = []

        def start(self, worker, *args):
            del args
            self.workers.append(worker)

        @staticmethod
        def tryTake(worker):
            del worker
            raise OSError("take unavailable")

        @staticmethod
        def clear():
            raise AssertionError("cancel_all must not use identity-free clear")

    pool = _BrokenTakePool()
    monkeypatch.setattr(task_manager, "thread_pool", pool)
    task_manager.active_workers.clear()
    task_manager._retired_workers.clear()
    task_manager._shutting_down = False
    task_id = "broken-try-take"

    task_manager.run_in_background(lambda: "cancelled", task_id=task_id)
    worker = task_manager.active_workers[task_id]
    task_manager.cancel_all(reason="broken_take")

    assert task_manager.is_task_unsettled(task_id) is True
    worker.run()
    assert _pump_events_until(lambda: not task_manager.is_task_unsettled(task_id))


def test_cancel_all_requires_exact_true_from_try_take(monkeypatch):
    class _InvalidTakePool:
        def __init__(self):
            self.workers = []

        def start(self, worker, *args):
            del args
            self.workers.append(worker)

        @staticmethod
        def tryTake(worker):
            del worker
            return "taken"

    pool = _InvalidTakePool()
    monkeypatch.setattr(task_manager, "thread_pool", pool)
    task_manager.active_workers.clear()
    task_manager._retired_workers.clear()
    task_manager._shutting_down = False
    task_id = "invalid-try-take-receipt"

    task_manager.run_in_background(lambda: "cancelled", task_id=task_id)
    worker = task_manager.active_workers[task_id]
    task_manager.cancel_all(reason="invalid_take_receipt")

    assert task_manager.is_task_unsettled(task_id) is True
    assert worker.terminated_event.is_set() is False
    worker.run()
    assert _pump_events_until(lambda: not task_manager.is_task_unsettled(task_id))


def test_task_manager_rejects_new_tasks_during_shutdown():
    task_manager.cancel_all()
    task_manager._shutting_down = True
    assert task_manager.is_shutting_down is True

    calls = []

    def should_not_run():
        calls.append("ran")
        return "unexpected"

    task_manager.run_in_background(should_not_run, task_id="shutdown_test")

    assert calls == []
    assert task_manager.active_count == 0

    task_manager._shutting_down = False
    assert task_manager.is_shutting_down is False


def test_task_manager_user_facing_error_uses_clean_message():
    task_manager.cancel_all()
    task_manager._shutting_down = False

    errors = []

    def expected_failure():
        raise UserFacingTaskError(
            "抓取超时：网络较慢，请稍后重试。",
            "测试任务：模拟可恢复超时",
        )

    task_manager.run_in_background(
        expected_failure,
        on_error=lambda message: errors.append(message),
        task_id="user_facing_error_test",
    )

    assert _pump_events_until(lambda: bool(errors))
    assert errors == ["抓取超时：网络较慢，请稍后重试。"]
    assert task_manager.active_count == 0


def test_task_manager_unexpected_exception_reaches_terminal_state():
    task_manager.cancel_all()
    task_manager._shutting_down = False
    errors = []

    def unexpected_failure():
        raise IndexError("unexpected index")

    task_manager.run_in_background(
        unexpected_failure,
        on_error=lambda message: errors.append(message),
        task_id="unexpected_error_test",
    )

    assert _pump_events_until(lambda: task_manager.active_count == 0)
    assert errors == ["unexpected index"]


def test_task_manager_delivers_on_terminated_after_worker_really_stops():
    task_manager.cancel_all()
    task_manager._shutting_down = False
    release = threading.Event()
    terminated = []

    task_manager.run_in_background(
        lambda: release.wait(0.5),
        on_terminated=lambda: terminated.append(True),
        task_id="terminated_callback_test",
    )

    assert terminated == []
    release.set()
    assert _pump_events_until(lambda: bool(terminated))
    assert terminated == [True]
    assert task_manager.active_count == 0


def test_task_manager_releases_dedupe_slot_only_on_terminated():
    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback, **_kwargs):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in tuple(self.callbacks):
                callback(*args)

    worker = SimpleNamespace(
        signals=SimpleNamespace(
            finished=_Signal(),
            error=_Signal(),
            terminated=_Signal(),
        )
    )
    task_manager.active_workers["terminal-only-cleanup"] = worker
    task_manager._connect_worker_callbacks(
        worker,
        "terminal-only-cleanup",
        None,
        lambda _message: None,
        None,
    )

    worker.signals.finished.emit("ok")
    assert task_manager.is_active_task("terminal-only-cleanup") is True

    worker.signals.terminated.emit()
    assert task_manager.is_active_task("terminal-only-cleanup") is False


def test_task_manager_releases_exact_worker_before_terminal_callback_resubmits(monkeypatch):
    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback, **_kwargs):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in tuple(self.callbacks):
                callback(*args)

    old_worker = SimpleNamespace(
        signals=SimpleNamespace(finished=_Signal(), error=_Signal(), terminated=_Signal())
    )
    replacement = SimpleNamespace()
    started = []
    monkeypatch.setattr(
        task_manager,
        "thread_pool",
        SimpleNamespace(start=lambda worker, *args: started.append((worker, args))),
    )
    task_manager.active_workers["terminal-resubmit"] = old_worker
    task_manager._connect_worker_callbacks(
        old_worker,
        "terminal-resubmit",
        None,
        lambda _message: None,
        lambda: task_manager.submit_task(replacement, "terminal-resubmit"),
    )

    old_worker.signals.terminated.emit()

    assert task_manager.active_workers["terminal-resubmit"] is replacement
    assert started == [(replacement, ())]
    task_manager.active_workers.pop("terminal-resubmit", None)


def test_background_worker_cancelled_before_run_emits_terminal_signal():
    terminal = []
    worker = BackgroundWorker(lambda: "unreachable")
    worker.signals.terminated.connect(lambda: terminal.append(True))

    worker.cancel()
    worker.run()

    assert terminal == [True]


def test_task_manager_abandon_task_releases_same_id_slot():
    task_manager.cancel_all()
    task_manager._shutting_down = False

    started = threading.Event()
    release = threading.Event()
    rerun_calls = []

    def blocked_task():
        started.set()
        release.wait(0.3)
        return "blocked-done"

    def fast_task():
        rerun_calls.append("ran")
        return "fast-done"

    task_manager.run_in_background(blocked_task, task_id="abandon_test")
    assert started.wait(0.2)
    assert task_manager.is_active_task("abandon_test")
    assert task_manager.abandon_task("abandon_test") is True
    assert not task_manager.is_active_task("abandon_test")

    task_manager.run_in_background(fast_task, task_id="abandon_test")
    assert _pump_events_until(lambda: bool(rerun_calls))

    release.set()
    assert _pump_events_until(lambda: task_manager.active_count == 0)


def test_background_worker_direct_run_covers_cancel_and_emit_failures():
    calls = []

    worker = BackgroundWorker(lambda: calls.append("ran"))
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: calls.append(("error", message))),
    )
    worker.cancel()
    worker.run()
    assert calls == []

    worker = BackgroundWorker(lambda: "ok")
    worker.task_id = "finish_fail"
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: (_ for _ in ()).throw(RuntimeError("deleted"))),
        error=SimpleNamespace(emit=lambda message: calls.append(("error", message))),
    )
    worker.run()

    worker = BackgroundWorker(lambda: (_ for _ in ()).throw(UserFacingTaskError("clean", "log detail")))
    worker.task_id = "user_error"
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: (_ for _ in ()).throw(RuntimeError("deleted"))),
    )
    worker.run()

    worker = BackgroundWorker(lambda: (_ for _ in ()).throw(TimeoutError("slow")))
    worker.task_id = "timeout_error"
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: calls.append(("timeout", message))),
    )
    worker.run()

    worker = BackgroundWorker(lambda: (_ for _ in ()).throw(ValueError("bad")))
    worker.task_id = "generic_error"
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: (_ for _ in ()).throw(RuntimeError("deleted"))),
    )
    worker.run()

    assert ("timeout", "slow") in calls


def test_background_worker_temporarily_sets_thread_priority(monkeypatch):
    calls = []

    class _Thread:
        def __init__(self):
            self._priority = "normal"
            self.set_calls = []

        def priority(self):
            return self._priority

        def setPriority(self, priority):
            self.set_calls.append(priority)
            self._priority = priority

    fake_thread = _Thread()
    monkeypatch.setattr(
        task_scheduler_module,
        "QThread",
        SimpleNamespace(currentThread=lambda: fake_thread),
    )

    worker = BackgroundWorker(lambda: "ok", thread_priority="low")
    worker.signals = SimpleNamespace(
        finished=SimpleNamespace(emit=lambda result: calls.append(("finished", result))),
        error=SimpleNamespace(emit=lambda message: calls.append(("error", message))),
    )

    worker.run()

    assert calls == [("finished", "ok")]
    assert fake_thread.set_calls == ["low", "normal"]


def test_task_manager_direct_submit_status_and_abandon_branches(monkeypatch):
    task_manager.cancel_all()
    task_manager._shutting_down = True
    assert task_manager.submit_task(BackgroundWorker(lambda: None), task_id="blocked") == "blocked"

    task_manager._shutting_down = False
    existing = BackgroundWorker(lambda: None)
    task_manager.active_workers["dup_direct"] = existing
    assert task_manager.submit_task(BackgroundWorker(lambda: None), task_id="dup_direct") == "dup_direct"

    starts = []
    monkeypatch.setattr(task_manager, "thread_pool", SimpleNamespace(start=lambda worker: starts.append(worker), clear=lambda: None))
    task_manager.active_workers.clear()

    class BadCancel:
        def __init__(self):
            self.terminated_event = threading.Event()

        def cancel(self):
            raise RuntimeError("cancel failed")

    bad_cancel = BadCancel()
    generated_id = task_manager.submit_task(BackgroundWorker(lambda: None))
    try:
        assert starts
        assert task_manager.is_active_task(generated_id) is True
        assert task_manager.active_count == 1
        assert task_manager.abandon_task("missing") is False

        task_manager.active_workers["bad_cancel"] = bad_cancel
        assert task_manager.abandon_task("bad_cancel") is True
        assert task_manager.active_count == 1
    finally:
        task_manager.cancel_all()
        if starts:
            starts[0].run()
            _pump_events_until(lambda: not task_manager.is_task_unsettled(generated_id))
        bad_cancel.terminated_event.set()
        task_manager.is_task_unsettled("bad_cancel")


def test_task_manager_run_in_background_connects_callbacks_without_starting(monkeypatch):
    task_manager.cancel_all()
    task_manager._shutting_down = False
    captured = []

    def fake_submit(worker, tid):
        captured.append((worker, tid))
        return tid

    monkeypatch.setattr(task_manager, "submit_task", fake_submit)

    tid = task_manager.run_in_background(
        lambda: "ok",
        on_success=lambda result: result,
        on_error=lambda message: message,
        task_id="connect_only",
    )

    assert tid == "connect_only"
    assert captured[0][1] == "connect_only"


def test_task_manager_run_in_background_forwards_background_priority(monkeypatch):
    task_manager.cancel_all()
    task_manager._shutting_down = False
    captured = []

    def fake_submit(worker, tid, *, priority=None):
        captured.append((worker, tid, priority))
        return tid

    monkeypatch.setattr(task_manager, "submit_task", fake_submit)

    tid = task_manager.run_in_background(
        lambda: "ok",
        task_id="low_priority",
        task_priority=-1,
        thread_priority="low",
    )

    assert tid == "low_priority"
    assert captured[0][1:] == ("low_priority", -1)
    assert captured[0][0].thread_priority == "low"
