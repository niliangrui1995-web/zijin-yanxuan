# -*- coding: utf-8 -*-
"""Low-overhead animated tab container for the workspace."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QTabWidget, QWidget

from ui.theme_tokens import build_ui_tokens


class SmoothTabWidget(QTabWidget):
    """QTabWidget with a snapshot-based transition between pages.

    The old page is captured before the index switch, then a lightweight pixmap
    overlay fades and slides away after the new page is shown. This avoids
    animating large live table widgets directly.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transition_enabled = True
        self._transition_distance = 18
        self._pending_transition: tuple[QWidget, QPixmap, int] | None = None
        self._transition_overlay: QLabel | None = None
        self._transition_group: QParallelAnimationGroup | None = None
        self.currentChanged.connect(self._run_pending_transition)

    def setTransitionEnabled(self, enabled: bool) -> None:
        self._transition_enabled = bool(enabled)

    def setTransitionDistance(self, distance: int) -> None:
        self._transition_distance = max(0, int(distance or 0))

    def addTab(self, widget, *args):  # noqa: N802 - Qt API naming
        index = super().addTab(widget, *args)
        QTimer.singleShot(0, widget.ensurePolished)
        return index

    def insertTab(self, index, widget, *args):  # noqa: N802 - Qt API naming
        inserted = super().insertTab(index, widget, *args)
        QTimer.singleShot(0, widget.ensurePolished)
        return inserted

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt API naming
        self._prepare_transition(int(index))
        super().setCurrentIndex(index)

    def prewarm_pages(self) -> None:
        for idx in range(self.count()):
            widget = self.widget(idx)
            if widget is not None:
                QTimer.singleShot(0, widget.ensurePolished)

    def _motion_duration(self) -> int:
        try:
            return int(build_ui_tokens()["motion"]["base"])
        except (KeyError, TypeError, ValueError):
            return 180

    def _prepare_transition(self, target_index: int) -> None:
        self._pending_transition = None
        if not self._transition_enabled or not self.isVisible():
            return

        old_index = self.currentIndex()
        if target_index == old_index or target_index < 0 or target_index >= self.count():
            return

        old_widget = self.currentWidget()
        if old_widget is None or old_widget.width() <= 0 or old_widget.height() <= 0:
            return

        stack_host = old_widget.parentWidget()
        if stack_host is None:
            return

        pixmap = old_widget.grab()
        if pixmap.isNull():
            return

        direction = 1 if target_index > old_index else -1
        self._pending_transition = (stack_host, pixmap, direction)

    def _run_pending_transition(self, _index: int) -> None:
        pending = self._pending_transition
        self._pending_transition = None
        if pending is None or not self._transition_enabled or not self.isVisible():
            return

        stack_host, pixmap, direction = pending
        if stack_host.width() <= 0 or stack_host.height() <= 0:
            return

        self._clear_transition()

        overlay = QLabel(stack_host)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setPixmap(pixmap)
        overlay.setScaledContents(True)
        overlay.setGeometry(stack_host.rect())
        overlay.raise_()
        overlay.show()

        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(1.0)
        overlay.setGraphicsEffect(effect)

        duration = self._motion_duration()
        fade = QPropertyAnimation(effect, b"opacity", overlay)
        fade.setDuration(duration)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        start_rect = QRect(stack_host.rect())
        end_rect = QRect(start_rect)
        end_rect.translate(-direction * self._transition_distance, 0)
        slide = QPropertyAnimation(overlay, b"geometry", overlay)
        slide.setDuration(duration)
        slide.setStartValue(start_rect)
        slide.setEndValue(end_rect)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self._clear_transition)

        self._transition_overlay = overlay
        self._transition_group = group
        group.start()

    def _clear_transition(self) -> None:
        group = self._transition_group
        self._transition_group = None
        if group is not None:
            try:
                group.stop()
            except RuntimeError:
                pass
            group.deleteLater()

        overlay = self._transition_overlay
        self._transition_overlay = None
        if overlay is not None:
            try:
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                pass
