# -*- coding: utf-8 -*-
"""Window flag helpers for custom-frameless Qt windows."""

from __future__ import annotations

from PyQt6.QtCore import Qt


def build_frameless_main_window_flags() -> Qt.WindowType:
    """Keep frameless chrome while preserving native main-window behaviors."""

    return (
        Qt.WindowType.Window
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.CustomizeWindowHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinimizeButtonHint
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowCloseButtonHint
    )
