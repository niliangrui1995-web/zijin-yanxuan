# -*- coding: utf-8 -*-
"""UI navigation and progress signal channels."""

from PyQt6.QtCore import QObject, pyqtSignal


class UISignalBus(QObject):
    """UI 专用信号总线。"""

    _instance = None

    sig_task_progress = pyqtSignal(str, int, str)
    sig_show_kline = pyqtSignal(str)
    sig_show_kline_with_list = pyqtSignal(str, object, int)

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance


ui_signals = UISignalBus()
