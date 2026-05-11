# -*- coding: utf-8 -*-
import time

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy

from core.global_store import global_store
from ui.models.table_models import RtSortFilterProxyModel, StockTableModel


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

    price_col = model.headers.index("现价")
    cap_col = model.headers.index("市值")
    assert model.data(model.index(0, price_col), Qt.ItemDataRole.UserRole + 1)["diff"] > 0
    assert model.data(model.index(0, cap_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0


def test_stock_table_model_sort_value_cache_invalidates_on_cell_update():
    model = StockTableModel(["代码", "名称", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "市值": "105亿"},
    ])

    cap_col = model.headers.index("市值")
    index = model.index(0, cap_col)

    assert model.data(index, Qt.ItemDataRole.UserRole) == 10_500_000_000
    assert (0, cap_col) in model._sort_value_cache

    model.set_cell_value(0, "市值", "210亿", emit_signal=False)

    assert model.data(index, Qt.ItemDataRole.UserRole) == 21_000_000_000


def test_stock_table_model_sort_cache_invalidates_on_external_data_changed():
    model = StockTableModel(["代码", "名称", "涨幅%"])
    model.update_data([
        {"代码": "A", "名称": "A", "涨幅%": 1.0},
        {"代码": "B", "名称": "B", "涨幅%": 2.0},
    ])
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)
    code_col = model.headers.index("代码")
    pct_col = model.headers.index("涨幅%")

    proxy.sort(pct_col, Qt.SortOrder.DescendingOrder)
    assert [proxy.data(proxy.index(row, code_col), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == ["B", "A"]

    model.row_data[0]["涨幅%"] = 5.0
    model.dataChanged.emit(model.index(0, 0), model.index(0, len(model.headers) - 1))
    proxy.sort(pct_col, Qt.SortOrder.DescendingOrder)

    assert [proxy.data(proxy.index(row, code_col), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == ["A", "B"]


def test_stock_table_model_incremental_update_marks_status_and_time_flash():
    model = StockTableModel(["代码", "名称", "状态", "最近时间"])
    model.update_data([
        {"代码": "000001", "名称": "A", "状态": "观察", "最近时间": "09:30"},
    ])

    model.update_data([
        {"代码": "000001", "名称": "A", "状态": "触发", "最近时间": "09:35"},
    ])

    status_col = model.headers.index("状态")
    time_col = model.headers.index("最近时间")
    assert model.data(model.index(0, status_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0
    assert model.data(model.index(0, time_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0


def test_stock_table_model_prunes_expired_flash_records_on_update():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 1_000_000_000},
    ])
    model._flash_records = {
        99: {1: {"time": time.time() - 10, "diff": 1.0}},
    }

    model.update_quotes({"000001": {"close": 10.5, "last_close": 10.0}})

    assert 99 not in model._flash_records


def test_stock_table_model_update_data_hydrates_latest_global_quotes(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--", "_zongguben": 1_000_000_000},
    ])

    assert model.row_data[0]["现价"] == "10.50"
    assert model.row_data[0]["涨幅%"] == pytest.approx(5.0)
    assert model.row_data[0]["市值"] == "105亿"


def test_stock_table_model_update_data_can_skip_latest_global_quotes(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [{"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--", "_zongguben": 1_000_000_000}],
        hydrate_latest_quotes=False,
    )

    assert model.row_data[0]["现价"] == "--"
    assert model.row_data[0]["涨幅%"] == "--"
    assert model.row_data[0]["市值"] == "--"


def test_stock_table_model_supports_shijia_header(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    model = StockTableModel(["代码", "名称", "市价", "涨幅%", "市值"])
    model.update_data([
        {"代码": "000001", "名称": "A", "市价": "--", "涨幅%": "--", "市值": "--", "_zongguben": 1_000_000_000},
    ])

    assert model.row_data[0]["市价"] == "10.50"
    assert model.row_data[0]["涨幅%"] == pytest.approx(5.0)
    assert model.row_data[0]["市值"] == "105亿"
