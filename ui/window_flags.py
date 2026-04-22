# -*- coding: utf-8 -*-
"""Window flag helpers for custom-frameless Qt windows."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt

GWL_STYLE = -16
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000


def build_frameless_main_window_flags() -> Qt.WindowType:
    """Keep the main window fully frameless at the Qt level."""

    return (
        Qt.WindowType.Window
        | Qt.WindowType.FramelessWindowHint
    )


def apply_windows_frameless_taskbar_fix(window, *, user32=None) -> bool:
    """Restore native minimize/taskbar behavior without re-enabling the title bar."""

    if os.name != "nt":
        return False

    hwnd = int(window.winId())
    if hwnd <= 0:
        return False

    if user32 is None:
        import ctypes

        user32 = ctypes.windll.user32

    get_window_long = getattr(user32, "GetWindowLongPtrW", None) or getattr(user32, "GetWindowLongW")
    set_window_long = getattr(user32, "SetWindowLongPtrW", None) or getattr(user32, "SetWindowLongW")
    set_window_pos = getattr(user32, "SetWindowPos")

    current_style = int(get_window_long(hwnd, GWL_STYLE))
    target_style = (current_style | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX) & ~WS_CAPTION

    if target_style == current_style:
        return False

    set_window_long(hwnd, GWL_STYLE, target_style)
    set_window_pos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
    return True
