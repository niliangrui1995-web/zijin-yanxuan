from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Sequence

CREATE_NO_WINDOW = 0x08000000
PROCESS_DEVNULL = subprocess.DEVNULL
PROCESS_PIPE = subprocess.PIPE
PROCESS_CANCEL_POLL_SECONDS = 0.05
PROCESS_TERMINATE_GRACE_SECONDS = 0.5
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

ProcessExecutionError = subprocess.CalledProcessError
ProcessSubprocessError = subprocess.SubprocessError
ProcessTimeoutError = subprocess.TimeoutExpired


def _validate_process_kwargs(kwargs: dict) -> None:
    if kwargs.get("shell"):
        raise ValueError("shell=True is not allowed")


def _normalize_command(command: Sequence[str | os.PathLike[str]]) -> list[str]:
    if isinstance(command, (str, bytes, os.PathLike)):
        raise TypeError("command must be a sequence of arguments, not a string")
    normalized = [os.fspath(part) for part in command]
    if not normalized or not str(normalized[0]).strip():
        raise ValueError("command must not be empty")
    return [str(part) for part in normalized]


def windows_no_window_creationflags() -> int:
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def windows_hidden_startupinfo():
    if os.name != "nt":
        return None
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo
    except (AttributeError, TypeError, ValueError):
        return None


def apply_windows_no_window_kwargs(kwargs: dict) -> None:
    if os.name != "nt":
        return
    kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
    startupinfo = windows_hidden_startupinfo()
    if startupinfo is not None:
        kwargs.setdefault("startupinfo", startupinfo)


def windows_no_window_kwargs() -> dict:
    kwargs = {}
    apply_windows_no_window_kwargs(kwargs)
    return kwargs


def build_domestic_process_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    if extra:
        env.update(extra)
    return env


def run_process(command: Sequence[str | os.PathLike[str]], **kwargs):
    _validate_process_kwargs(kwargs)
    # Arguments are validated and shell=True is blocked above.
    return subprocess.run(_normalize_command(command), **kwargs)  # nosec


def _stop_and_reap_process(process, *, grace_seconds: float = PROCESS_TERMINATE_GRACE_SECONDS) -> bool:
    """Terminate, then kill, a child process within a bounded cleanup window."""
    try:
        if process.poll() is not None:
            process.wait()
            return True
        process.terminate()
        try:
            process.communicate(timeout=max(0.0, float(grace_seconds)))
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        process.wait()
        return process.poll() is not None
    except (OSError, subprocess.SubprocessError):
        return process.poll() is not None


def run_cancellable_process(
    command: Sequence[str | os.PathLike[str]],
    *,
    cancellation_token,
    timeout: float,
    poll_interval_seconds: float = PROCESS_CANCEL_POLL_SECONDS,
    terminate_grace_seconds: float = PROCESS_TERMINATE_GRACE_SECONDS,
    check: bool = False,
    capture_output: bool = False,
    **kwargs,
):
    """Run an argv-only child with cooperative cancellation and bounded reaping."""
    normalized_command = _normalize_command(command)
    _validate_process_kwargs(kwargs)
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs["stdout"] = PROCESS_PIPE
        kwargs["stderr"] = PROCESS_PIPE
    process = spawn_process(normalized_command, **kwargs)
    normalized_timeout = max(0.1, float(timeout))
    deadline = time.monotonic() + normalized_timeout
    try:
        while True:
            cancellation_token.raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessTimeoutError(normalized_command, normalized_timeout)
            try:
                stdout, stderr = process.communicate(
                    timeout=min(max(0.01, float(poll_interval_seconds)), remaining),
                )
                break
            except ProcessTimeoutError:
                continue
        cancellation_token.raise_if_cancelled()
        completed = subprocess.CompletedProcess(normalized_command, process.returncode, stdout, stderr)
        if check and process.returncode:
            raise ProcessExecutionError(
                process.returncode,
                normalized_command,
                output=stdout,
                stderr=stderr,
            )
        return completed
    finally:
        _stop_and_reap_process(process, grace_seconds=terminate_grace_seconds)


def spawn_process(command: Sequence[str | os.PathLike[str]], **kwargs):
    _validate_process_kwargs(kwargs)
    # Arguments are validated and shell=True is blocked above.
    return subprocess.Popen(_normalize_command(command), **kwargs)  # nosec


def spawn_silent_process(command: Sequence[str | os.PathLike[str]], **kwargs):
    kwargs.setdefault("stdin", PROCESS_DEVNULL)
    kwargs.setdefault("stdout", PROCESS_DEVNULL)
    kwargs.setdefault("stderr", PROCESS_DEVNULL)
    apply_windows_no_window_kwargs(kwargs)
    return spawn_process(command, **kwargs)


def spawn_detached_process(command: Sequence[str | os.PathLike[str]], **kwargs):
    return spawn_silent_process(command, **kwargs)


def build_python_module_command(
    module_name: str,
    module_args: Sequence[str] | None = None,
    *,
    python_executable: str | None = None,
) -> list[str]:
    normalized_module = str(module_name or "").strip()
    if not normalized_module:
        raise ValueError("module_name must not be blank")

    command = [python_executable or sys.executable, "-m", normalized_module]
    for arg in module_args or ():
        text = str(arg or "").strip()
        if text:
            command.append(text)
    return command


def _sibling_console_python_for_no_window(executable: str | None) -> str | None:
    text = str(executable or "").strip()
    if not text:
        return None
    normalized = text.replace("\\", "/").lower()
    if not (normalized == "pythonw.exe" or normalized.endswith("/pythonw.exe")):
        return None

    if "\\" in text:
        directory = text.rsplit("\\", 1)[0]
        candidate = f"{directory}\\python.exe" if directory else "python.exe"
    else:
        directory = os.path.dirname(text)
        candidate = os.path.join(directory, "python.exe") if directory else "python.exe"

    return candidate if os.path.exists(candidate) else None


def _python_executable_for_no_window(python_executable: str | None) -> str | None:
    if python_executable:
        return python_executable
    if os.name != "nt":
        return None
    return _sibling_console_python_for_no_window(sys.executable)


def run_python_module(
    module_name: str,
    module_args: Sequence[str] | None = None,
    *,
    python_executable: str | None = None,
    no_window: bool = False,
    **kwargs,
):
    if no_window:
        apply_windows_no_window_kwargs(kwargs)
        python_executable = _python_executable_for_no_window(python_executable)
    return run_process(
        build_python_module_command(
            module_name,
            module_args,
            python_executable=python_executable,
        ),
        **kwargs,
    )


def run_python_module_cancellable(
    module_name: str,
    module_args: Sequence[str] | None = None,
    *,
    cancellation_token,
    timeout: float,
    python_executable: str | None = None,
    no_window: bool = False,
    **kwargs,
):
    if no_window:
        apply_windows_no_window_kwargs(kwargs)
        python_executable = _python_executable_for_no_window(python_executable)
    return run_cancellable_process(
        build_python_module_command(
            module_name,
            module_args,
            python_executable=python_executable,
        ),
        cancellation_token=cancellation_token,
        timeout=timeout,
        **kwargs,
    )
