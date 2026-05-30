# -*- coding: utf-8 -*-
"""UI-facing external navigation entrypoints."""

from __future__ import annotations

import os
from pathlib import Path

from infra.navigation import ExternalTerminalNavigator
from infra.tasks.process_runner import spawn_silent_process

CODEX_LOCAL_LAUNCHER = Path.home() / ".codex" / "local-tools" / "open-codex-project.ps1"


def _powershell_executable() -> str:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(powershell if powershell.is_file() else "powershell.exe")


def open_codex_desktop_thread(thread_url: str, *, launcher: str | Path | None = None) -> bool:
    launcher_path = Path(launcher) if launcher is not None else CODEX_LOCAL_LAUNCHER
    if not launcher_path.is_file():
        return False

    try:
        spawn_silent_process(
            [
                _powershell_executable(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher_path),
                thread_url,
            ],
        )
    except OSError:
        return False
    return True


__all__ = ["CODEX_LOCAL_LAUNCHER", "ExternalTerminalNavigator", "open_codex_desktop_thread"]
