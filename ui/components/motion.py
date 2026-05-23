# -*- coding: utf-8 -*-
"""Shared low-cost motion helpers for local UI feedback."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QObject
from PyQt6.QtWidgets import QGraphicsOpacityEffect

from ui.theme_tokens import build_ui_tokens


def motion_duration(name: str = "base", fallback: int = 180) -> int:
    try:
        return int(build_ui_tokens()["motion"][name])
    except (KeyError, TypeError, ValueError):
        return int(fallback)


def fade_in(widget, *, duration: int | None = None, start: float = 0.0, end: float = 1.0) -> QPropertyAnimation | None:
    if widget is None:
        return None
    if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
        return None

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(float(start))
    widget.setGraphicsEffect(effect)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(motion_duration("fast") if duration is None else int(duration))
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup() -> None:
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    animation.finished.connect(_cleanup)
    animation.start()
    return animation


class ButtonFeedbackFilter(QObject):
    """Tiny opacity pulse for clicked buttons without changing layout."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API naming
        if event.type() == QEvent.Type.MouseButtonPress and watched.isEnabled():
            fade_in(watched, duration=motion_duration("fast"), start=0.76, end=1.0)
        return super().eventFilter(watched, event)


def install_button_feedback(button) -> None:
    if button is None or getattr(button, "_motion_feedback_filter", None) is not None:
        return
    event_filter = ButtonFeedbackFilter(button)
    button.installEventFilter(event_filter)
    button._motion_feedback_filter = event_filter
