from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ERROR_ALREADY_EXISTS = 183
APP_SINGLE_INSTANCE_MUTEX = "VCPHunterQuantTerminal_SingleInstance"


@dataclass
class SingleInstanceLock:
    already_running: bool = False
    _handle: Any | None = None
    _close_handle: Callable[[Any], Any] | None = None

    def release(self) -> None:
        if self._handle is None or self._close_handle is None:
            return
        try:
            self._close_handle(self._handle)
        finally:
            self._handle = None
            self._close_handle = None


def acquire_single_instance_lock(
    name: str = APP_SINGLE_INSTANCE_MUTEX,
    *,
    os_name: str | None = None,
    kernel32: Any | None = None,
    get_last_error: Callable[[], int] | None = None,
) -> SingleInstanceLock:
    if (os_name or os.name) != "nt":
        return SingleInstanceLock()

    import ctypes

    kernel32_api = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    last_error = get_last_error or ctypes.get_last_error

    if get_last_error is None:
        ctypes.set_last_error(0)
    handle = kernel32_api.CreateMutexW(None, True, name)
    if not handle:
        return SingleInstanceLock()

    if last_error() == ERROR_ALREADY_EXISTS:
        kernel32_api.CloseHandle(handle)
        return SingleInstanceLock(already_running=True)

    return SingleInstanceLock(_handle=handle, _close_handle=kernel32_api.CloseHandle)


def is_single_instance_running(
    name: str = APP_SINGLE_INSTANCE_MUTEX,
    *,
    os_name: str | None = None,
    kernel32: Any | None = None,
    get_last_error: Callable[[], int] | None = None,
) -> bool:
    if (os_name or os.name) != "nt":
        return False

    import ctypes

    kernel32_api = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    last_error = get_last_error or ctypes.get_last_error

    if get_last_error is None:
        ctypes.set_last_error(0)
    handle = kernel32_api.CreateMutexW(None, False, name)
    if not handle:
        return False

    try:
        return last_error() == ERROR_ALREADY_EXISTS
    finally:
        kernel32_api.CloseHandle(handle)


def _normalize_script_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path or "").strip().strip('"')))


def _windows_process_has_visible_window(pid: int) -> bool:
    if os.name != "nt":
        return True

    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, OSError):
        return False

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    visible = False

    def callback(hwnd, _lparam):
        nonlocal visible
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) == int(pid) and user32.IsWindowVisible(hwnd):
            visible = True
            return False
        return True

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
    user32.EnumWindows(enum_proc, 0)
    return visible


def is_entry_script_process_running(
    script_path: str,
    *,
    current_pid: int | None = None,
    process_iter: Callable[[list[str]], Any] | None = None,
    process_has_visible_window: Callable[[int], bool] | None = None,
) -> bool:
    target_script = _normalize_script_path(script_path)
    if not target_script:
        return False

    try:
        import psutil
    except ImportError:
        return False

    pid = os.getpid() if current_pid is None else current_pid
    iterator = process_iter or psutil.process_iter
    has_visible_window = process_has_visible_window or _windows_process_has_visible_window
    for process in iterator(["pid", "cmdline"]):
        try:
            info = getattr(process, "info", {}) or {}
            other_pid = int(info.get("pid") or 0)
            if other_pid == pid:
                continue
            cmdline = info.get("cmdline") or []
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, psutil.Error):
            continue

        for arg in cmdline:
            if _normalize_script_path(str(arg)) == target_script and has_visible_window(other_pid):
                return True

    return False
