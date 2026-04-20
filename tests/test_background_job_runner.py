# -*- coding: utf-8 -*-

from core.background_job_runner import BackgroundJobRunner


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
