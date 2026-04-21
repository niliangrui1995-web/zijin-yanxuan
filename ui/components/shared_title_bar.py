# -*- coding: utf-8 -*-
"""Shared draggable title-bar primitive for shell windows and dialogs."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


class DraggableTitleBar(QWidget):
    """空白区域可拖拽的共享标题栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                ratio = event.position().x() / win.width()
                win.showNormal()
                new_x = int(event.globalPosition().x() - win.width() * ratio)
                new_y = int(event.globalPosition().y() - self.height() // 2)
                win.move(new_x, new_y)
                self._drag_pos = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
            else:
                win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
