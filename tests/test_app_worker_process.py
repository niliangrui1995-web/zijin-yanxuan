from __future__ import annotations

from infra.tasks import app_worker_process


def test_spawn_f5_worker_reserves_cpu_capacity_for_parent(monkeypatch, tmp_path):
    captured = {}
    sentinel = object()

    def _spawn(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(app_worker_process.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(app_worker_process, "spawn_silent_process", _spawn)

    result = app_worker_process.spawn_f5_worker(
        project_root=str(tmp_path),
        job_dir=str(tmp_path / "job"),
    )

    assert result is sentinel
    assert captured["kwargs"]["env"]["POLARS_MAX_THREADS"] == "2"
    for variable in app_worker_process._F5_SINGLE_THREAD_MATH_ENV:
        assert captured["kwargs"]["env"][variable] == "1"
    assert captured["kwargs"]["creationflags"] == int(
        getattr(app_worker_process.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0
    )


def test_spawn_f5_worker_never_overcommits_small_cpu_count(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(app_worker_process.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(
        app_worker_process,
        "spawn_silent_process",
        lambda _command, **kwargs: captured.update(kwargs) or object(),
    )

    app_worker_process.spawn_f5_worker(project_root=str(tmp_path), job_dir=str(tmp_path / "job"))

    assert captured["env"]["POLARS_MAX_THREADS"] == "1"
