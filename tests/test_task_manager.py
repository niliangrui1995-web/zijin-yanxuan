import threading
import time

from PyQt6.QtCore import QCoreApplication

from core.task_manager import UserFacingTaskError, task_manager


def _pump_events_until(predicate, timeout=3.0):
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


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

    calls = []

    def should_not_run():
        calls.append("ran")
        return "unexpected"

    task_manager.run_in_background(should_not_run, task_id="shutdown_test")
    time.sleep(0.05)

    assert calls == []
    assert task_manager.active_count == 0

    task_manager._shutting_down = False


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
