from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

CREATE_NO_WINDOW = 0x08000000
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


def windows_no_window_creationflags() -> int:
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def build_domestic_process_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    if extra:
        env.update(extra)
    return env


def run_process(command: Sequence[str], **kwargs):
    return subprocess.run(command, **kwargs)


def spawn_process(command: Sequence[str], **kwargs):
    return subprocess.Popen(command, **kwargs)


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
        kwargs.setdefault("creationflags", windows_no_window_creationflags())
    return run_process(
        build_python_module_command(
            module_name,
            module_args,
            python_executable=python_executable,
        ),
        **kwargs,
    )
