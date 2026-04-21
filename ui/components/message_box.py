from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.components.shared_title_bar import DraggableTitleBar
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


class ThemedQuestionDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        yes_text: str | None = None,
        no_text: str | None = None,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.No,
    ) -> None:
        super().__init__(parent)
        self._clicked_button = QMessageBox.StandardButton.No
        self._default_button = default_button

        self.setObjectName("confirmDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(432, 284)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(14, 14, 14, 14)
        outer_layout.setSpacing(0)

        self._container = QFrame(self)
        self._container.setObjectName("dialogContainer")
        outer_layout.addWidget(self._container)

        self._shadow = QGraphicsDropShadowEffect(self._container)
        self._container.setGraphicsEffect(self._shadow)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(1, 1, 1, 16)
        container_layout.setSpacing(0)

        self._title_bar = DraggableTitleBar(self)
        self._title_bar.setObjectName("dialogTitleBar")
        title_bar_layout = QHBoxLayout(self._title_bar)
        title_bar_layout.setContentsMargins(14, 0, 8, 0)
        title_bar_layout.setSpacing(0)

        self._window_title_label = QLabel(title)
        self._window_title_label.setObjectName("dialogWindowTitle")
        title_bar_layout.addWidget(self._window_title_label)
        title_bar_layout.addStretch(1)

        self._btn_close = QToolButton(self._title_bar)
        self._btn_close.setObjectName("dialogCloseButton")
        self._btn_close.setText("✕")
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self.reject)
        title_bar_layout.addWidget(self._btn_close)
        container_layout.addWidget(self._title_bar)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 18, 20, 0)
        content_layout.setSpacing(16)
        container_layout.addLayout(content_layout)

        message_row = QHBoxLayout()
        message_row.setContentsMargins(0, 0, 0, 0)
        message_row.setSpacing(16)

        self._icon_label = QLabel("?")
        self._icon_label.setObjectName("confirmDialogIcon")
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(38, 38)
        message_row.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)

        self._message_label = QLabel(text)
        self._message_label.setObjectName("confirmDialogMessage")
        self._message_label.setWordWrap(True)
        self._message_label.setTextFormat(Qt.TextFormat.PlainText)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        message_row.addWidget(self._message_label, 1)
        content_layout.addLayout(message_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        self._yes_button = QPushButton(yes_text or "是")
        self._yes_button.setObjectName("primaryButton")
        self._yes_button.setMinimumWidth(96)
        self._yes_button.clicked.connect(self.accept)
        footer.addWidget(self._yes_button)

        self._no_button = QPushButton(no_text or "否")
        self._no_button.setProperty("class", "secondary")
        self._no_button.setMinimumWidth(88)
        self._no_button.clicked.connect(self.reject)
        footer.addWidget(self._no_button)
        content_layout.addLayout(footer)

        self._apply_shell_metrics()
        self._apply_default_button()
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    @property
    def selected_button(self) -> QMessageBox.StandardButton:
        return self._clicked_button

    def _apply_shell_metrics(self) -> None:
        tokens = build_ui_tokens(theme_manager.current_theme)
        is_dark = tokens["is_dark"]
        titlebar_height = tokens["shell"]["titlebar_height"]

        self._title_bar.setFixedHeight(titlebar_height)
        self._btn_close.setFixedSize(36, titlebar_height)

        self._shadow.setBlurRadius(34 if is_dark else 28)
        self._shadow.setOffset(0, 12 if is_dark else 10)
        self._shadow.setColor(QColor(0, 0, 0, 108 if is_dark else 52))

    def _apply_default_button(self) -> None:
        buttons = {
            QMessageBox.StandardButton.Yes: self._yes_button,
            QMessageBox.StandardButton.No: self._no_button,
        }
        target = buttons.get(self._default_button, self._no_button)
        target.setDefault(True)
        target.setAutoDefault(True)
        target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _on_theme_changed(self, _theme_name: str) -> None:
        self._apply_shell_metrics()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def accept(self) -> None:
        self._clicked_button = QMessageBox.StandardButton.Yes
        super().accept()

    def reject(self) -> None:
        self._clicked_button = QMessageBox.StandardButton.No
        super().reject()


def show_themed_question(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes_text: str | None = None,
    no_text: str | None = None,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.No,
) -> QMessageBox.StandardButton:
    dialog = ThemedQuestionDialog(
        parent,
        title,
        text,
        yes_text=yes_text,
        no_text=no_text,
        default_button=default_button,
    )
    dialog.exec()
    return dialog.selected_button
