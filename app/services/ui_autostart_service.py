# -*- coding: utf-8 -*-
"""UI-facing launch-at-login service."""

from __future__ import annotations

from pathlib import Path

from infra.navigation.windows_autostart import AutoStartError, WindowsAutoStartManager


def windows_autostart_manager(repo_root: str | Path | None = None) -> WindowsAutoStartManager:
    return WindowsAutoStartManager(repo_root)


def is_launch_at_login_supported(repo_root: str | Path | None = None) -> bool:
    return windows_autostart_manager(repo_root).is_supported


def is_launch_at_login_enabled(repo_root: str | Path | None = None) -> bool:
    return windows_autostart_manager(repo_root).is_enabled()


def set_launch_at_login_enabled(enabled: bool, repo_root: str | Path | None = None) -> None:
    windows_autostart_manager(repo_root).set_enabled(enabled)


__all__ = [
    "AutoStartError",
    "WindowsAutoStartManager",
    "is_launch_at_login_enabled",
    "is_launch_at_login_supported",
    "set_launch_at_login_enabled",
    "windows_autostart_manager",
]
