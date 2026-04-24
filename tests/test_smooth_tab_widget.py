# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QWidget

from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.models.table_models import StockTableModel


def test_smooth_tab_widget_keeps_qtabwidget_contract_when_hidden():
    tabs = SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")

        tabs.setCurrentIndex(1)

        assert tabs.currentIndex() == 1
        assert tabs.count() == 2
        assert tabs._pending_transition is None
    finally:
        tabs.deleteLater()


def test_stock_table_model_same_code_update_avoids_model_reset():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.00"},
        {"代码": "000002", "名称": "B", "现价": "20.00"},
    ])

    reset_spy = QSignalSpy(model.modelReset)
    change_spy = QSignalSpy(model.dataChanged)

    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.10"},
        {"代码": "000002", "名称": "B", "现价": "20.00"},
    ])

    assert len(reset_spy) == 0
    assert len(change_spy) == 1
    assert model.row_data[0]["现价"] == "10.10"
