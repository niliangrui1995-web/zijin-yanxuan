from PyQt6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton

from ui.theme_tokens import build_ui_tokens


class ToggleSwitch(QAbstractButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = 1.0 if self.isChecked() else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value)
        self.toggled.connect(self._animate_to_state)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self.text()) if self.text() else 0
        return QSize(42 + (9 + text_width if text_width else 0), max(30, fm.height() + 8))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _on_animation_value(self, value) -> None:
        self._position = float(value)
        self.update()

    def _animate_to_state(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setDuration(int(build_ui_tokens()["motion"].get("fast", 120)))
        self._animation.setStartValue(float(self._position))
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API naming
        tokens = build_ui_tokens()
        theme = tokens["theme"]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_rect = QRectF(1.0, (self.height() - 20.0) / 2.0, 38.0, 20.0)

        active = QColor(theme.get("COLOR_SUCCESS", theme["COLOR_INFO"]))
        inactive = QColor(theme["TEXT_MUTED"])
        track = QColor(active if self.isChecked() else inactive)
        track.setAlpha(190 if self.isChecked() and self.isEnabled() else 62)
        border = QColor(active if self.isChecked() else inactive)
        border.setAlpha(155 if self.isEnabled() else 70)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(track))
        painter.drawRoundedRect(track_rect, 10.0, 10.0)

        thumb_x = track_rect.left() + 2.0 + 18.0 * float(self._position)
        thumb_rect = QRectF(thumb_x, track_rect.center().y() - 8.0, 16.0, 16.0)
        thumb = QColor(theme["TEXT_BRIGHT"] if tokens["is_dark"] else theme["BG_CARD"])
        thumb.setAlpha(255 if self.isEnabled() else 150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(thumb))
        painter.drawEllipse(thumb_rect)

        if self.text():
            color = QColor(theme["TEXT_PRIMARY"] if self.isEnabled() else theme["TEXT_DISABLED"])
            painter.setPen(QPen(color))
            painter.setFont(self.font())
            text_rect = QRectF(track_rect.right() + 9.0, 0.0, self.width() - track_rect.right() - 9.0, self.height())
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
