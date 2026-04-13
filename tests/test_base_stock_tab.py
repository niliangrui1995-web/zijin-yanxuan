# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QLabel, QLineEdit, QToolButton, QWidget

from ui.tabs.base_stock_tab import BaseStockTab


def test_base_stock_toolbar_applies_shell_object_names_and_toolbutton_style():
    tab = BaseStockTab()
    subtitle = QLabel("已连接")
    filter_input = QLineEdit()
    action_btn = QToolButton()

    toolbar = tab.build_tab_toolbar("示例", subtitle, [filter_input], [action_btn])
    try:
        assert toolbar.objectName() == "tabToolbar"
        assert subtitle.objectName() == "tabStatusLabel"
        assert action_btn.property("class") == "toolbarGhost"
        assert toolbar.findChild(QWidget, "tabToolbarFilters") is not None
        assert toolbar.findChild(QWidget, "tabToolbarActions") is not None
    finally:
        toolbar.deleteLater()
        tab.deleteLater()
