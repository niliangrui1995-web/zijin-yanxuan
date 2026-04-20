# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.components.message_box import show_themed_question
from ui.styles.global_qss import generate_global_qss


def test_show_themed_question_builds_themed_qt_dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = {}

    def fake_exec(self):
        yes_button = self.button(QMessageBox.StandardButton.Yes)
        no_button = self.button(QMessageBox.StandardButton.No)
        test_option = getattr(self, "testOption", None)

        captured["object_name"] = self.objectName()
        captured["window_title"] = self.windowTitle()
        captured["text"] = self.text()
        captured["text_format"] = self.textFormat()
        captured["default_button_text"] = self.defaultButton().text()
        captured["yes_text"] = yes_button.text()
        captured["yes_object_name"] = yes_button.objectName()
        captured["yes_min_width"] = yes_button.minimumWidth()
        captured["no_text"] = no_button.text()
        captured["no_class"] = no_button.property("class")
        captured["no_min_width"] = no_button.minimumWidth()
        captured["uses_qt_dialog"] = (
            test_option(QMessageBox.Option.DontUseNativeDialog)
            if callable(test_option) and hasattr(QMessageBox, "Option")
            else True
        )
        return int(QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    result = show_themed_question(
        None,
        "盘后一键预计算",
        "是否继续执行？",
        yes_text="执行",
        no_text="取消",
    )
    app.processEvents()

    assert result == QMessageBox.StandardButton.No
    assert captured["object_name"] == "themedMessageBox"
    assert captured["window_title"] == "盘后一键预计算"
    assert captured["text"] == "是否继续执行？"
    assert captured["text_format"] == Qt.TextFormat.PlainText
    assert captured["default_button_text"] == "取消"
    assert captured["yes_text"] == "执行"
    assert captured["yes_object_name"] == "primaryButton"
    assert captured["yes_min_width"] >= 96
    assert captured["no_text"] == "取消"
    assert captured["no_class"] == "secondary"
    assert captured["no_min_width"] >= 88
    assert captured["uses_qt_dialog"] is True


def test_global_qss_contains_message_box_theme_rules():
    qss = generate_global_qss()

    assert "QMessageBox {" in qss
    assert "QMessageBox QLabel#qt_msgbox_label {" in qss
    assert "QMessageBox QPushButton#primaryButton {" in qss
