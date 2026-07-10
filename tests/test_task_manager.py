import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication

import infra.tasks.task_scheduler as task_scheduler_module
from core.task_manager import UserFacingTaskError, task_manager
from infra.tasks.task_scheduler import BackgroundWorker, _task_thread_pool_max_count


def _pump_events_until(predicate, timeout=3.0):
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.time() + timeout
    while time.time() < deadline:
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

    def slow_task():
        with calls_lock:
            calls.append(time.time())
        time.sleep(0.1)
        return "ok"

    task_manager.run_in_background(slow_task, task_id="dup_test")
    task_manager.run_in_background(slow_task, task_id="dup_test")

    assert task_manager.active_count == 1
    assert _pump_events_until(lambda: task_manager.active_count == 0)
    assert len(calls) == 1


def test_task_manager_rejects_new_tasks_during_shutdown():
    task_manager.cancel_all()
    task_manager._shutting_down = True
    assert task_manager.is_shutting_down is True

    calls = []

    def should_not_run():
        calls.append("ran")
        return "unexpected"

    task_manager.run_in_background(should_not_run, task_id="shutdown_test")
    time.sleep(0.05)

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

    generated_id = task_manager.submit_task(BackgroundWorker(lambda: None))

    assert starts
    assert task_manager.is_active_task(generated_id) is True
    assert task_manager.active_count == 1
    assert task_manager.abandon_task("missing") is False

    class BadCancel:
        def cancel(self):
            raise RuntimeError("cancel failed")

    task_manager.active_workers["bad_cancel"] = BadCancel()
    assert task_manager.abandon_task("bad_cancel") is True
    assert task_manager.active_count == 1
    task_manager.cancel_all()


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
