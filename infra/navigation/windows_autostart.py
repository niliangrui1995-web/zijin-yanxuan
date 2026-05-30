# -*- coding: utf-8 -*-
"""Windows current-user launch-at-login integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "ZijinResearchVCPHunter"
APP_DISPLAY_NAME = "".join(chr(code) for code in (0x7D2B, 0x91D1, 0x7814, 0x9009))


class AutoStartError(RuntimeError):
    """Raised when launch-at-login cannot be changed."""


def _quote_command_part(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace('"', r"\"") + '"'


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LaunchCommand:
    command: str
    source: str


class WindowsAutoStartManager:
    def __init__(
        self,
        repo_root: str | Path | None = None,
        *,
        os_name: str | None = None,
        registry: Any | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
        self._os_name = os_name
        self._registry = registry
        self._environ = environ if environ is not None else os.environ

    @property
    def is_supported(self) -> bool:
        return (self._os_name or os.name) == "nt"

    def current_command(self) -> str | None:
        if not self.is_supported:
            return None
        registry = self._registry_module()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_READ) as key:
                value, _value_type = registry.QueryValueEx(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AutoStartError(str(exc)) from exc
        return str(value or "").strip() or None

    def is_enabled(self) -> bool:
        return self.current_command() is not None

    def set_enabled(self, enabled: bool) -> None:
        if not self.is_supported:
            raise AutoStartError("launch-at-login is only available on Windows")
        if enabled:
            self._write_command(self.resolve_launch_command().command)
        else:
            self._delete_command()

    def resolve_launch_command(self) -> LaunchCommand:
        packaged_exe = self.repo_root / "dist" / APP_DISPLAY_NAME / f"{APP_DISPLAY_NAME}.exe"
        if packaged_exe.is_file():
            return LaunchCommand(_quote_command_part(packaged_exe), "packaged-exe")

        launcher = self._silent_launcher_path()
        if launcher is not None and launcher.is_file():
            return LaunchCommand(
                f"{_quote_command_part(launcher)} {_quote_command_part(self.repo_root)}",
                "silent-launcher",
            )

        pythonw = self.repo_root / ".venv" / "Scripts" / "pythonw.exe"
        entry_script = self.repo_root / "vcp_hunter_qt.pyw"
        if pythonw.is_file() and entry_script.is_file():
            return LaunchCommand(
                f"{_quote_command_part(pythonw)} {_quote_command_part(entry_script)}",
                "project-venv",
            )

        raise AutoStartError("no packaged exe, silent launcher, or project pythonw launcher was found")

    def _silent_launcher_path(self) -> Path | None:
        local_app_data = str(self._environ.get("LOCALAPPDATA", "") or "").strip()
        if not local_app_data:
            return None
        return Path(local_app_data) / "ZijinResearch" / "Launcher" / "ZijinResearchLauncher.exe"

    def _registry_module(self):
        if self._registry is not None:
            return self._registry
        import winreg

        return winreg

    def _write_command(self, command: str) -> None:
        registry = self._registry_module()
        try:
            with registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
                registry.SetValueEx(key, RUN_VALUE_NAME, 0, registry.REG_SZ, command)
        except OSError as exc:
            raise AutoStartError(str(exc)) from exc

    def _delete_command(self) -> None:
        registry = self._registry_module()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                registry.DeleteValue(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AutoStartError(str(exc)) from exc
