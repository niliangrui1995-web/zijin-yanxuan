from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget


class Toast(QWidget):
    """
    A non-blocking Toast notification widget for PyQt6.
    """
    def __init__(self, parent=None, duration=3000):
        super().__init__(parent)
        self.duration = duration
        self._init_ui()

    def _init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # UI Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.bg_widget = QWidget(self)
        self.bg_widget.setObjectName("ToastBackground")

        # 默认样式 (INFO)
        from ui.theme import theme_manager as _tm
        _t = _tm.current_theme
        self.bg_widget.setStyleSheet(f"""
            QWidget#ToastBackground {{
                background-color: {_t['BG_GLASS']};
                border-radius: 8px;
                border: 1px solid {_t['BORDER_DEFAULT']};
            }}
        """)

        bg_layout = QHBoxLayout(self.bg_widget)
        bg_layout.setContentsMargins(16, 12, 16, 12)

        self.lbl_icon = QLabel()
        self.lbl_icon.setStyleSheet("font-size: 16px; margin-right: 8px; background: transparent;")
        bg_layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel()
        self.lbl_text.setStyleSheet(f"color: {_tm.current_theme['TEXT_PRIMARY']}; font-size: 14px; font-weight: 500; background: transparent;")
        self.lbl_text.setWordWrap(True)
        bg_layout.addWidget(self.lbl_text)

        layout.addWidget(self.bg_widget)

        # Animation effects
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
        """
        显示 Toast
        level: 'info', 'success', 'warning', 'error'
        """
        self.lbl_text.setText(message)

        # 根据级别换图标和边框色
        styles = {
            "info": ("i", "rgba(59, 130, 246, 0.10)", "rgba(59, 130, 246, 0.42)", "#3B82F6"),
            "success": ("OK", "rgba(34, 197, 94, 0.12)", "rgba(34, 197, 94, 0.48)", "#22C55E"),
            "warning": ("", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.52)", "#F59E0B"),
            "error": ("x", "rgba(239, 68, 68, 0.14)", "rgba(239, 68, 68, 0.56)", "#EF4444")
        }
        icon, bg_color, border_color, text_color = styles.get(level, styles["info"])
        self.lbl_icon.setText(icon)
        self.lbl_icon.setVisible(bool(icon))
        from ui.theme import theme_manager as _tm
        self.bg_widget.setStyleSheet(f"""
            QWidget#ToastBackground {{
                background-color: {bg_color};
                border-radius: 8px;
                border: 1px solid {border_color};
            }}
        """)
        self.lbl_icon.setStyleSheet(
            f"font-size: 16px; margin-right: 8px; background: transparent; color: {text_color}; font-weight: 700;"
        )
        self.lbl_text.setStyleSheet(
            f"color: {text_color}; font-size: 14px; font-weight: 600; background: transparent;"
        )

        # 居中显示在父窗口或主屏幕顶部
        self.adjustSize()
        if self.parent():
            p_geom = self.parent().geometry()
            x = p_geom.x() + (p_geom.width() - self.width()) // 2
            y = p_geom.y() + 60
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

# Helper Factory function
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
