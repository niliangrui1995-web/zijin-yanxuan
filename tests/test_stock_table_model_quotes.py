# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy

from ui.models.table_models import StockTableModel


def test_stock_table_model_update_quotes_batches_changed_rows():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 1_000_000_000},
        {"代码": "000002", "名称": "B", "现价": "20.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 2_000_000_000},
        {"代码": "000003", "名称": "C", "现价": "30.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 3_000_000_000},
    ])

    spy = QSignalSpy(model.dataChanged)

    model.update_quotes({
        "000001": {"close": 10.5, "last_close": 10.0},
        "000002": {"close": 21.0, "last_close": 20.0},
    })

    assert len(spy) == 1

    top_left = spy[0][0]
    bottom_right = spy[0][1]
    assert top_left.row() == 0
    assert bottom_right.row() == 1

    assert model.row_data[0]["现价"] == "10.50"
    assert model.row_data[1]["现价"] == "21.00"
    assert model.row_data[0]["市值"] == "105亿"
    assert model.row_data[1]["市值"] == "420亿"
