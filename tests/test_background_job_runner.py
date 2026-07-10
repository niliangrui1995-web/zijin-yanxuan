# -*- coding: utf-8 -*-

from core.background_job_runner import BackgroundJobRunner
from domains.runtime import TaskCategory
from infra.tasks import task_registry
from infra.tasks.typed_task_registry import TypedTaskRegistry


class _FakeManager:
    def __init__(self):
        self.calls = []

    def run_in_background(self, fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        self.calls.append(("run_in_background", task_id, args, kwargs))
        if on_success is not None:
            on_success("ok")
        return task_id or "generated"

    def abandon_task(self, task_id):
        self.calls.append(("abandon_task", task_id))
        return True

    def is_active_task(self, task_id):
        self.calls.append(("is_active_task", task_id))
        return task_id == "busy"

    def cancel_all(self):
        self.calls.append(("cancel_all",))

    def shutdown(self):
        self.calls.append(("shutdown",))

    @property
    def active_count(self):
        self.calls.append(("active_count",))
        return 3


def test_background_job_runner_resolves_manager_lazily(monkeypatch):
    import core.task_manager as task_manager_module

    fake_manager = _FakeManager()
    runner = BackgroundJobRunner()
    success_messages = []

    monkeypatch.setattr(task_manager_module, "task_manager", fake_manager)

    assert runner.run("job-1", lambda: "ignored", on_success=success_messages.append) == "job-1"
    assert runner.run_in_background(lambda: "ignored", task_id="job-2") == "job-2"
    assert runner.abandon("job-3") is True
    assert runner.abandon_task("job-4") is True
    assert runner.is_active("busy") is True
    assert runner.is_active_task("idle") is False
    runner.cancel_all()
    runner.shutdown()

    assert runner.active_count == 3
    assert success_messages == ["ok"]
    assert fake_manager.calls == [
        ("run_in_background", "job-1", (), {}),
        ("run_in_background", "job-2", (), {}),
        ("abandon_task", "job-3"),
        ("abandon_task", "job-4"),
        ("is_active_task", "busy"),
        ("is_active_task", "idle"),
        ("cancel_all",),
        ("shutdown",),
        ("active_count",),
    ]


def test_background_job_runner_accepts_typed_task_key(monkeypatch):
    import core.task_manager as task_manager_module

    fake_manager = _FakeManager()
    runner = BackgroundJobRunner()
    task_key = task_registry.workspace("test_typed_task")

    monkeypatch.setattr(task_manager_module, "task_manager", fake_manager)

    assert runner.run(task_key, lambda: "ignored") == "test_typed_task"
    assert runner.run_in_background(lambda: "ignored", task_id=task_key) == "test_typed_task"
    assert runner.abandon(task_key) is True
    assert runner.is_active(task_key) is False

    assert fake_manager.calls == [
        ("run_in_background", "test_typed_task", (), {}),
        ("run_in_background", "test_typed_task", (), {}),
        ("abandon_task", "test_typed_task"),
        ("is_active_task", "test_typed_task"),
    ]


def test_typed_task_registry_validation_and_quote_refresh_helpers():
    registry = TypedTaskRegistry()

    try:
        registry.register(" ", category=TaskCategory.NETWORK)
    except ValueError:
        pass
    else:
        raise AssertionError("blank task id should fail")

    assert registry.resolve(None) is None

    first = registry.register("job", category=TaskCategory.NETWORK, description="First")
    assert registry.register("job", category=TaskCategory.WINDOW, description="Second") is first

    try:
        registry.quote_refresh(" ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank quote refresh scope should fail")

    assert registry.quote_refresh("watchlist").task_id == "watchlist_quotes"
    assert registry.quote_refresh("central_quotes").task_id == "central_quotes"
    assert registry.quotes("quote_job").category is TaskCategory.QUOTES

    transient = registry.transient_window("generation_1")
    assert transient.category is TaskCategory.WINDOW
    assert registry.window("generation_1") is not transient
    assert registry.transient_quotes("generation_2").category is TaskCategory.QUOTES
