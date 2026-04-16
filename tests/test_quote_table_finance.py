# -*- coding: utf-8 -*-
from ui.models.table_models import StockTableModel


def test_stock_table_model_update_quotes_injects_zongguben_from_quote_payload():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--"},
    ])

    model.update_quotes({
        "000001": {
            "close": 10.5,
            "last_close": 10.0,
            "zongguben": 1_000_000_000,
        }
    })

    assert model.row_data[0]["_zongguben"] == 1_000_000_000
    assert model.row_data[0]["现价"] == "10.50"
    assert model.row_data[0]["市值"] == "105亿"


def test_stock_table_model_finance_only_payload_does_not_clear_existing_price():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.50", "涨幅%": 5.0, "市值": "--"},
    ])

    model.update_quotes({
        "000001": {
            "zongguben": 1_000_000_000,
            "market_cap": 10_500_000_000,
        }
    })

    assert model.row_data[0]["现价"] == "10.50"
    assert model.row_data[0]["_zongguben"] == 1_000_000_000
    assert model.row_data[0]["市值"] == "105亿"
