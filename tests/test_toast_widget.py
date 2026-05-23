# -*- coding: utf-8 -*-
import pytest
from PyQt6.QtWidgets import QWidget


@pytest.fixture
def toast_dependencies(qt_application):
    from ui.components.toast_widget import Toast
    from ui.theme import theme_manager

    return Toast, theme_manager


def test_warning_toast_keeps_icon_visible(toast_dependencies):
    Toast, _theme_manager = toast_dependencies
    toast = Toast(duration=10)
    try:
        toast.show_toast("代码不在当前 A 股股票列表中", "warning")

        assert toast.lbl_icon.text() == "!"
        assert toast.lbl_icon.isHidden() is False
    finally:
        toast.hide_toast()
        toast.close()


def test_success_toast_keeps_icon_visible(toast_dependencies):
    Toast, _theme_manager = toast_dependencies
    toast = Toast(duration=10)
    try:
        toast.show_toast("已加入关注池", "success")

        assert toast.lbl_icon.text() == "OK"
        assert toast.lbl_icon.isHidden() is False
    finally:
        toast.hide_toast()
        toast.close()


def test_toast_text_uses_primary_theme_color(toast_dependencies):
    Toast, theme_manager = toast_dependencies
    toast = Toast(duration=10)
    try:
        toast.show_toast("同步完成", "success")

        assert "color:" in toast.lbl_text.styleSheet()
        assert theme_manager.current_theme["TEXT_PRIMARY"] in toast.lbl_text.styleSheet()
    finally:
        toast.hide_toast()
        toast.close()


def test_global_toast_suppresses_hidden_parent(qt_application):
    from ui.components import toast_widget

    parent = QWidget()
    try:
        toast_widget.clear_active_toast()

        assert toast_widget.show_toast("hidden", "success", parent=parent, duration=10) is None
        assert toast_widget._active_toast is None

        parent.show()
        qt_application.processEvents()
        toast = toast_widget.show_toast("visible", "success", parent=parent, duration=10)
        assert toast is not None
        assert toast_widget._active_toast is toast
    finally:
        toast_widget.clear_active_toast()
        parent.close()
        parent.deleteLater()
