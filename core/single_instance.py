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

    handle = kernel32_api.CreateMutexW(None, True, name)
    if not handle:
        return SingleInstanceLock()

    if last_error() == ERROR_ALREADY_EXISTS:
        kernel32_api.CloseHandle(handle)
        return SingleInstanceLock(already_running=True)

    return SingleInstanceLock(_handle=handle, _close_handle=kernel32_api.CloseHandle)
