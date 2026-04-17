# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QToolButton, QWidget

from ui.components.scan_dialogs import VCPScanRangeDialog


def test_scan_range_dialog_uses_custom_themed_shell():
    dialog = VCPScanRangeDialog()
    try:
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.findChild(QFrame, "dialogContainer") is not None
        assert dialog.findChild(QWidget, "dialogTitleBar") is not None
        assert dialog.findChild(QToolButton, "dialogCloseButton") is not None
    finally:
        dialog.deleteLater()
