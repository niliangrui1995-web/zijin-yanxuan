from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from ui.theme import theme_manager
from ui.theme_tokens import get_state_tone


class Toast(QWidget):
    """A non-blocking Toast notification widget for PyQt6."""

    _ICON_MAP = {
        "info": "i",
        "success": "OK",
        "warning": "!",
        "error": "x",
    }

    def __init__(self, parent=None, duration=3000):
        super().__init__(parent)
        self.duration = duration
        self._init_ui()

    def _set_background_style(self, background: str, border: str):
        self.bg_widget.setStyleSheet(
            f"""
            QWidget#ToastBackground {{
                background-color: {background};
                border-radius: 8px;
                border: 1px solid {border};
            }}
            """
        )

    def _apply_level_style(self, level: str):
        theme = theme_manager.current_theme
        normalized_level = level if level in self._ICON_MAP else "info"
        tone = get_state_tone(normalized_level, theme)

        self.lbl_icon.setText(self._ICON_MAP[normalized_level])
        self.lbl_icon.setVisible(True)
        self._set_background_style(tone["bg"], tone["border"])
        self.lbl_icon.setStyleSheet(
            f"font-size: 16px; margin-right: 8px; background: transparent; color: {tone['fg']}; font-weight: 700;"
        )
        self.lbl_text.setStyleSheet(
            f"color: {theme['TEXT_PRIMARY']}; font-size: 14px; font-weight: 600; background: transparent;"
        )

    def _init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.bg_widget = QWidget(self)
        self.bg_widget.setObjectName("ToastBackground")

        theme = theme_manager.current_theme
        self._set_background_style(theme["BG_GLASS"], theme["BORDER_DEFAULT"])

        bg_layout = QHBoxLayout(self.bg_widget)
        bg_layout.setContentsMargins(16, 12, 16, 12)

        self.lbl_icon = QLabel()
        self.lbl_icon.setStyleSheet("font-size: 16px; margin-right: 8px; background: transparent;")
        bg_layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel()
        self.lbl_text.setStyleSheet(
            f"color: {theme['TEXT_PRIMARY']}; font-size: 14px; font-weight: 500; background: transparent;"
        )
        self.lbl_text.setWordWrap(True)
        bg_layout.addWidget(self.lbl_text)

        layout.addWidget(self.bg_widget)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.anim_in = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_in.setDuration(300)
        self.anim_in.setStartValue(0.0)
        self.anim_in.setEndValue(1.0)
        self.anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.anim_out = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_out.setDuration(400)
        self.anim_out.setStartValue(1.0)
        self.anim_out.setEndValue(0.0)
        self.anim_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim_out.finished.connect(self.close)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_toast)

    def show_toast(self, message: str, level: str = "info"):
        """Display a toast using theme-derived state colors."""
        self.lbl_text.setText(message)
        self._apply_level_style(level)

        self.adjustSize()
        if self.parent():
            parent_geometry = self.parent().geometry()
            x = parent_geometry.x() + (parent_geometry.width() - self.width()) // 2
            y = parent_geometry.y() + 60
            self.move(x, y)
        else:
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - self.width()) // 2
            y = 60
            self.move(x, y)

        self.show()
        self.anim_in.start()
        self.timer.start(self.duration)

    def hide_toast(self):
        self.anim_out.start()


_active_toast = None


def clear_active_toast():
    global _active_toast
    if _active_toast is not None:
        try:
            _active_toast.timer.stop()
            _active_toast.close()
        except RuntimeError:
            pass
        _active_toast = None


def show_toast(message: str, level: str = "info", parent=None, duration=3000):
    global _active_toast
    clear_active_toast()

    _active_toast = Toast(parent, duration)
    _active_toast.show_toast(message, level)
