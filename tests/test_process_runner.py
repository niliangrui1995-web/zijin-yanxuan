import pytest

from infra.tasks import process_runner


def test_run_process_normalizes_sequence_commands(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(process_runner.subprocess, "run", fake_run)

    assert process_runner.run_process(("python", "-V"), check=True) == "ok"
    assert captured == {"command": ["python", "-V"], "kwargs": {"check": True}}


def test_process_runner_rejects_string_command():
    with pytest.raises(TypeError):
        process_runner.run_process("python -V")


def test_process_runner_rejects_shell_true():
    with pytest.raises(ValueError):
        process_runner.spawn_process(["python", "-V"], shell=True)
