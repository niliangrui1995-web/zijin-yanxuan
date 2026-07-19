from pathlib import Path

import pytest

from infra.tasks import process_runner


class _FakeStartupInfo:
    def __init__(self):
        self.dwFlags = 0
        self.wShowWindow = None


def _patch_windows_startup_info(monkeypatch):
    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "STARTF_USESHOWWINDOW", 4, raising=False)
    monkeypatch.setattr(process_runner.subprocess, "SW_HIDE", 7, raising=False)


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


def test_process_runner_rejects_blank_executable():
    with pytest.raises(ValueError):
        process_runner.run_process([" "])


def test_process_runner_rejects_shell_true():
    with pytest.raises(ValueError):
        process_runner.spawn_process(["python", "-V"], shell=True)


def test_windows_no_window_kwargs_uses_creationflags_and_startupinfo(monkeypatch):
    _patch_windows_startup_info(monkeypatch)

    kwargs = process_runner.windows_no_window_kwargs()

    assert kwargs["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & 4
    assert kwargs["startupinfo"].wShowWindow == 7


def test_windows_no_window_helpers_cover_non_windows_and_startupinfo_failure(monkeypatch):
    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    assert process_runner.windows_no_window_creationflags() == process_runner.CREATE_NO_WINDOW

    monkeypatch.setattr(process_runner.os, "name", "posix", raising=False)
    assert process_runner.windows_no_window_creationflags() == 0
    assert process_runner.windows_hidden_startupinfo() is None

    kwargs = {}
    process_runner.apply_windows_no_window_kwargs(kwargs)
    assert kwargs == {}

    monkeypatch.setattr(process_runner.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        process_runner.subprocess,
        "STARTUPINFO",
        lambda: (_ for _ in ()).throw(TypeError("bad startupinfo")),
        raising=False,
    )
    assert process_runner.windows_hidden_startupinfo() is None


def test_build_domestic_process_env_strips_proxy_and_applies_extra(monkeypatch):
    for key in process_runner.PROXY_ENV_KEYS:
        monkeypatch.setenv(key, "http://proxy.invalid")

    env = process_runner.build_domestic_process_env(extra={"CUSTOM": "1"})

    for key in process_runner.PROXY_ENV_KEYS:
        assert key not in env
    assert env["NO_PROXY"] == "*"
    assert env["no_proxy"] == "*"
    assert env["CUSTOM"] == "1"


def test_spawn_process_normalizes_pathlike_command(monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "process"

    monkeypatch.setattr(process_runner.subprocess, "Popen", fake_popen)

    assert process_runner.spawn_process([Path("python"), "-V"], cwd="D:/tmp") == "process"
    assert captured == {"command": ["python", "-V"], "kwargs": {"cwd": "D:/tmp"}}


def test_spawn_detached_process_delegates_to_silent_process(monkeypatch):
    captured = {}

    def fake_spawn_silent_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "detached"

    monkeypatch.setattr(process_runner, "spawn_silent_process", fake_spawn_silent_process)

    assert process_runner.spawn_detached_process(["python", "-V"], cwd="D:/tmp") == "detached"
    assert captured == {"command": ["python", "-V"], "kwargs": {"cwd": "D:/tmp"}}


def test_build_python_module_command_validates_and_filters_args():
    with pytest.raises(ValueError):
        process_runner.build_python_module_command(" ")

    assert process_runner.build_python_module_command(
        " pkg.tool ",
        ["", " --flag ", None, "value"],
        python_executable="py",
    ) == ["py", "-m", "pkg.tool", "--flag", "value"]


def test_python_executable_for_no_window_branches(monkeypatch):
    assert process_runner._sibling_console_python_for_no_window(None) is None
    assert process_runner._sibling_console_python_for_no_window("python.exe") is None

    monkeypatch.setattr(process_runner.os.path, "exists", lambda path: True)
    expected = process_runner.os.path.join("C:/Python314", "python.exe")
    assert process_runner._sibling_console_python_for_no_window("C:/Python314/pythonw.exe") == expected

    assert process_runner._python_executable_for_no_window("custom-python") == "custom-python"

    monkeypatch.setattr(process_runner.os, "name", "posix", raising=False)
    assert process_runner._python_executable_for_no_window(None) is None


def test_run_python_module_no_window_preserves_existing_creationflags(monkeypatch):
    captured = {}

    def fake_run_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    _patch_windows_startup_info(monkeypatch)
    monkeypatch.setattr(process_runner, "run_process", fake_run_process)

    result = process_runner.run_python_module("pkg.tool", no_window=True, creationflags=0x20)

    assert result == "ok"
    assert captured["command"] == [process_runner.sys.executable, "-m", "pkg.tool"]
    assert captured["kwargs"]["creationflags"] & 0x20
    assert captured["kwargs"]["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert captured["kwargs"]["startupinfo"].wShowWindow == 7


def test_run_python_module_no_window_uses_console_python_for_pythonw(monkeypatch):
    captured = {}

    def fake_run_process(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    _patch_windows_startup_info(monkeypatch)
    monkeypatch.setattr(process_runner.sys, "executable", r"C:\Python314\pythonw.exe")
    monkeypatch.setattr(process_runner.os.path, "exists", lambda path: path == r"C:\Python314\python.exe")
    monkeypatch.setattr(process_runner, "run_process", fake_run_process)

    result = process_runner.run_python_module("pkg.tool", no_window=True)

    assert result == "ok"
    assert captured["command"] == [r"C:\Python314\python.exe", "-m", "pkg.tool"]
    assert captured["kwargs"]["creationflags"] & process_runner.CREATE_NO_WINDOW
    assert captured["kwargs"]["startupinfo"].wShowWindow == 7


def test_run_python_module_cancellable_preserves_token_timeout_and_no_window(monkeypatch):
    captured = {}
    token = object()

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return "ok"

    _patch_windows_startup_info(monkeypatch)
    monkeypatch.setattr(process_runner, "run_cancellable_process", fake_run)

    result = process_runner.run_python_module_cancellable(
        "pkg.tool",
        ["--flag"],
        cancellation_token=token,
        timeout=3,
        no_window=True,
        capture_output=True,
    )

    assert result == "ok"
    assert captured["command"] == [process_runner.sys.executable, "-m", "pkg.tool", "--flag"]
    assert captured["kwargs"]["cancellation_token"] is token
    assert captured["kwargs"]["timeout"] == 3
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["creationflags"] & process_runner.CREATE_NO_WINDOW


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


class _NeverCancelledToken:
    def raise_if_cancelled(self):
        return None


class _CancellationSignal(Exception):
    pass


class _CancelledToken:
    def raise_if_cancelled(self):
        raise _CancellationSignal


class _FakeCancellableProcess:
    def __init__(self, *, stubborn=False):
        self.returncode = None
        self.stubborn = stubborn
        self.communicate_calls = 0
        self.terminated = False
        self.killed = False
        self.waited = False

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self.stubborn and not self.killed:
            raise process_runner.ProcessTimeoutError(["python"], timeout)
        if self.communicate_calls == 1 and not self.killed:
            raise process_runner.ProcessTimeoutError(["python"], timeout)
        self.returncode = -9 if self.killed else 0
        return "stdout", "stderr"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


def test_run_cancellable_process_polls_and_reaps_completed_child(monkeypatch):
    process = _FakeCancellableProcess()
    monkeypatch.setattr(process_runner, "spawn_process", lambda *_args, **_kwargs: process)

    result = process_runner.run_cancellable_process(
        ["python", "-V"],
        cancellation_token=_NeverCancelledToken(),
        timeout=3,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "stdout"
    assert process.communicate_calls == 2
    assert process.waited is True
    assert process.terminated is False


def test_run_cancellable_process_timeout_kills_and_reaps_stubborn_child(monkeypatch):
    process = _FakeCancellableProcess(stubborn=True)
    monotonic = iter((10.0, 11.0))
    monkeypatch.setattr(process_runner, "spawn_process", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(process_runner.time, "monotonic", lambda: next(monotonic))

    with pytest.raises(process_runner.ProcessTimeoutError):
        process_runner.run_cancellable_process(
            ["python", "-V"],
            cancellation_token=_NeverCancelledToken(),
            timeout=0.1,
            capture_output=True,
        )

    assert process.terminated is True
    assert process.killed is True
    assert process.waited is True


def test_run_cancellable_process_cancellation_kills_and_reaps_child(monkeypatch):
    process = _FakeCancellableProcess(stubborn=True)
    monkeypatch.setattr(process_runner, "spawn_process", lambda *_args, **_kwargs: process)

    with pytest.raises(_CancellationSignal):
        process_runner.run_cancellable_process(
            ["python", "-V"],
            cancellation_token=_CancelledToken(),
            timeout=3,
            capture_output=True,
        )

    assert process.terminated is True
    assert process.killed is True
    assert process.waited is True
