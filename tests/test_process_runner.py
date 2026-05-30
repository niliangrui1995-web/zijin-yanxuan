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


def test_windows_no_window_kwargs_uses_creationflags_and_startupinfo(monkeypatch):
    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTF_USESHOWWINDOW", 4, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "SW_HIDE", 7, raising=False)

    kwargs = process_runner.windows_no_window_kwargs()

    assert kwargs["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & 4
    assert kwargs["startupinfo"].wShowWindow == 7


def test_run_python_module_no_window_preserves_existing_creationflags(monkeypatch):
    captured = {}

    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    def fake_run_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTF_USESHOWWINDOW", 4, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "SW_HIDE", 7, raising=False)
    monkeypatch.setattr(process_runner, "run_process", fake_run_process)

    result = process_runner.run_python_module("pkg.tool", no_window=True, creationflags=0x20)

    assert result == "ok"
    assert captured["command"] == [process_runner.sys.executable, "-m", "pkg.tool"]
    assert captured["kwargs"]["creationflags"] & 0x20
    assert captured["kwargs"]["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert captured["kwargs"]["startupinfo"].wShowWindow == 7


def test_run_python_module_no_window_uses_console_python_for_pythonw(monkeypatch):
    captured = {}

    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    def fake_run_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    monkeypatch.setattr(process_runner.sys, "executable", r"C:\Python314\pythonw.exe")
    monkeypatch.setattr(process_runner.os.path, "exists", lambda path: path == r"C:\Python314\python.exe")
    monkeypatch.setattr(process_runner.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTF_USESHOWWINDOW", 4, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "SW_HIDE", 7, raising=False)
    monkeypatch.setattr(process_runner, "run_process", fake_run_process)

    result = process_runner.run_python_module("pkg.tool", no_window=True)

    assert result == "ok"
    assert captured["command"] == [r"C:\Python314\python.exe", "-m", "pkg.tool"]
    assert captured["kwargs"]["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert captured["kwargs"]["startupinfo"].wShowWindow == 7


def test_spawn_silent_process_redirects_standard_streams(monkeypatch):
    captured = {}

    def fake_spawn_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "process"

    monkeypatch.setattr(process_runner, "spawn_process", fake_spawn_process)
    monkeypatch.setattr(process_runner, "apply_windows_no_window_kwargs", lambda kwargs: kwargs.update(hidden=True))

    result = process_runner.spawn_silent_process(["python", "-V"])

    assert result == "process"
    assert captured["command"] == ["python", "-V"]
    assert captured["kwargs"]["stdin"] is process_runner.PROCESS_DEVNULL
    assert captured["kwargs"]["stdout"] is process_runner.PROCESS_DEVNULL
    assert captured["kwargs"]["stderr"] is process_runner.PROCESS_DEVNULL
    assert captured["kwargs"]["hidden"] is True
