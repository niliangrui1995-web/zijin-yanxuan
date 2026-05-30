from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
from collections.abc import Sequence

CREATE_NO_WINDOW = 0x08000000
PROCESS_DEVNULL = subprocess.DEVNULL
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
    return run_process(
        build_python_module_command(
            module_name,
            module_args,
            python_executable=python_executable,
        ),
        **kwargs,
    )
