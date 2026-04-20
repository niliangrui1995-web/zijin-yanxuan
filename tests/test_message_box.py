# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.components.message_box import ThemedQuestionDialog, show_themed_question
from ui.styles.global_qss import generate_global_qss


def test_show_themed_question_builds_frameless_theme_dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = {}

    def fake_exec(self):
        captured["object_name"] = self.objectName()
        captured["window_title"] = self.windowTitle()
        captured["is_modal"] = self.isModal()
        captured["frameless"] = bool(self.windowFlags() & Qt.WindowType.FramelessWindowHint)
        captured["translucent"] = self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        captured["title_bar_name"] = self._title_bar.objectName()
        captured["container_name"] = self._container.objectName()
        captured["message_text"] = self._message_label.text()
        captured["message_text_format"] = self._message_label.textFormat()
        captured["yes_is_default"] = self._yes_button.isDefault()
        captured["no_is_default"] = self._no_button.isDefault()
        captured["yes_text"] = self._yes_button.text()
        captured["yes_object_name"] = self._yes_button.objectName()
        captured["yes_min_width"] = self._yes_button.minimumWidth()
        captured["no_text"] = self._no_button.text()
        captured["no_class"] = self._no_button.property("class")
        captured["no_min_width"] = self._no_button.minimumWidth()
        self.reject()
        return int(self.DialogCode.Rejected)

    monkeypatch.setattr(ThemedQuestionDialog, "exec", fake_exec)

    result = show_themed_question(
        None,
        "盘后一键预计算",
        "是否继续执行？",
        yes_text="执行",
        no_text="取消",
    )
    app.processEvents()

    assert result == QMessageBox.StandardButton.No
    assert captured["object_name"] == "confirmDialog"
    assert captured["window_title"] == "盘后一键预计算"
    assert captured["is_modal"] is True
    assert captured["frameless"] is True
    assert captured["translucent"] is True
    assert captured["title_bar_name"] == "dialogTitleBar"
    assert captured["container_name"] == "dialogContainer"
    assert captured["message_text"] == "是否继续执行？"
    assert captured["message_text_format"] == Qt.TextFormat.PlainText
    assert captured["yes_is_default"] is False
    assert captured["no_is_default"] is True
    assert captured["yes_text"] == "执行"
    assert captured["yes_object_name"] == "primaryButton"
    assert captured["yes_min_width"] >= 96
    assert captured["no_text"] == "取消"
    assert captured["no_class"] == "secondary"
    assert captured["no_min_width"] >= 88


def test_global_qss_contains_confirm_dialog_theme_rules():
    qss = generate_global_qss()

    assert "QDialog#confirmDialog {" in qss
    assert "QLabel#confirmDialogIcon {" in qss
    assert "QLabel#confirmDialogMessage {" in qss
