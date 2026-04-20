from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget


def show_themed_question(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes_text: str | None = None,
    no_text: str | None = None,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.No,
) -> QMessageBox.StandardButton:
    box = QMessageBox(parent)
    option_enum = getattr(QMessageBox, "Option", None)
    if option_enum is not None and hasattr(option_enum, "DontUseNativeDialog"):
        box.setOption(option_enum.DontUseNativeDialog, True)

    box.setObjectName("themedMessageBox")
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.setDefaultButton(default_button)
    box.setEscapeButton(QMessageBox.StandardButton.No)

    yes_button = box.button(QMessageBox.StandardButton.Yes)
    if yes_button is not None:
        if yes_text:
            yes_button.setText(yes_text)
        yes_button.setObjectName("primaryButton")
        yes_button.setMinimumWidth(96)

    no_button = box.button(QMessageBox.StandardButton.No)
    if no_button is not None:
        if no_text:
            no_button.setText(no_text)
        no_button.setProperty("class", "secondary")
        no_button.setMinimumWidth(88)

    return QMessageBox.StandardButton(box.exec())
