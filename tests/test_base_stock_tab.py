# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QWidget

from ui.tabs.base_stock_tab import BaseStockTab


def test_base_stock_toolbar_applies_shell_object_names_and_toolbutton_style():
    tab = BaseStockTab()
    subtitle = QLabel("已连接")
    filter_label = QLabel("筛选")
    filter_input = QLineEdit()
    action_btn = QToolButton()

    toolbar = tab.build_tab_toolbar("示例", subtitle, [filter_label, filter_input], [action_btn])
    try:
        assert toolbar.objectName() == "tabToolbar"
        assert subtitle.objectName() == "tabStatusLabel"
        assert subtitle.property("toolbarRole") == "status"
        assert filter_label.property("toolbarRole") == "meta"
        assert action_btn.property("class") == "toolbarGhost"
        assert toolbar.findChild(QWidget, "tabToolbarFilters") is not None
        assert toolbar.findChild(QWidget, "tabToolbarActions") is not None
    finally:
        toolbar.deleteLater()
        tab.deleteLater()


def test_base_stock_status_summary_skips_empty_segments():
    assert BaseStockTab.format_status_summary("状态 已启动", "", None, "下一步 拉取报价") == "状态 已启动 | 下一步 拉取报价"


def test_base_stock_tab_defers_quote_refresh_until_visible(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class DummyModel:
        def __init__(self):
            self.calls = []

        def update_quotes(self, quotes):
            self.calls.append(dict(quotes))

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.model = DummyModel()

    from core.global_store import global_store

    snapshot = {"000001": {"close": 10.5}}
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: snapshot)

    tab = DummyTab()
    try:
        assert not tab.isVisible()

        tab.subscribe_global_quotes()

        assert tab.model.calls == []
        assert tab._deferred_quote_refresh is True

        tab.show()
        app.processEvents()

        assert tab.model.calls == [snapshot]
        assert tab._deferred_quote_refresh is False
    finally:
        tab.close()
        tab.deleteLater()
