# -*- coding: utf-8 -*-
import time

import pytest
from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtTest import QSignalSpy

from core.global_store import global_store
from core.observability import clear_metric_history, metric_history
from ui.models.table_model_helpers import STOCK_CELL_RENDER_ROLE, _flash_decay_alpha
from ui.models.table_models import RtSortFilterProxyModel, StockTableModel


def test_stock_table_model_update_quotes_batches_changed_rows():
    clear_metric_history()
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 1_000_000_000},
            {"代码": "000002", "名称": "B", "现价": "20.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 2_000_000_000},
            {"代码": "000003", "名称": "C", "现价": "30.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 3_000_000_000},
        ]
    )

    spy = QSignalSpy(model.dataChanged)

    changed_rows = model.update_quotes(
        {
            "000001": {"close": 10.5, "last_close": 10.0},
            "000002": {"close": 21.0, "last_close": 20.0},
        }
    )

    assert changed_rows == 2
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
    samples = metric_history("stock_table_update_quotes_ms")
    assert samples
    assert samples[-1].tags["payload_codes"] == "2"
    assert samples[-1].tags["scanned_rows"] == "2"
    assert samples[-1].tags["changed_rows"] == "2"


def test_stock_table_model_updates_quote_metadata_and_price_tooltip_when_price_is_unchanged():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [{"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 0.0, "市值": "--"}],
        hydrate_latest_quotes=False,
    )
    spy = QSignalSpy(model.dataChanged)

    changed_rows = model.update_quotes(
        {
            "000001": {
                "close": 10.0,
                "last_close": 10.0,
                "source": "eastmoney",
                "quote_time": "2026-07-22T10:24:06+08:00",
                "quote_received_at": 1_784_688_246.0,
                "quote_freshness": "network",
            }
        }
    )

    assert changed_rows == 1
    assert len(spy) == 1
    assert model.row_data[0]["_quote_time"] == "2026-07-22T10:24:06+08:00"
    assert model.row_data[0]["_quote_freshness"] == "network"
    price_col = model.headers.index("现价")
    tooltip = model.data(model.index(0, price_col), Qt.ItemDataRole.ToolTipRole)
    assert "报价时间：2026-07-22 10:24:06" in tooltip
    assert "新鲜度：network（eastmoney）" in tooltip

    changed_rows = model.update_quotes(
        {
            "000001": {
                "close": 10.0,
                "last_close": 10.0,
                "source": "eastmoney",
                "quote_time": "2026-07-22T10:24:36+08:00",
                "quote_received_at": 1_784_688_276.0,
                "quote_freshness": "cache",
            }
        }
    )
    assert changed_rows == 1
    assert model.row_data[0]["_quote_freshness"] == "cache"


def test_stock_table_model_coalesces_sparse_non_flash_updates():
    model = StockTableModel(["代码", "名称", "现价", "RPS强度"])
    model.set_sparse_update_coalescing(True)
    model.update_data(
        [
            {"代码": f"00000{idx}", "名称": str(idx), "现价": "10.00", "RPS强度": ""}
            for idx in range(1, 6)
        ],
        hydrate_latest_quotes=False,
    )
    spy = QSignalSpy(model.dataChanged)
    updated = [dict(row) for row in model.row_data]
    for row_idx in (0, 2, 4):
        updated[row_idx]["RPS强度"] = "95"

    model.update_data(updated, hydrate_latest_quotes=False)

    assert len(spy) == 1
    assert spy[0][0].row() == 0
    assert spy[0][1].row() == 4
    roles = {int(getattr(role, "value", role)) for role in spy[0][2]}
    assert int(Qt.ItemDataRole.UserRole) + 1 not in roles
    assert model._flash_records == {}


def test_stock_table_model_incremental_update_emits_only_changed_visible_columns():
    model = StockTableModel(["代码", "名称", "现价", "RPS强度", "摘要"])
    model.update_data(
        [{"代码": "000001", "名称": "A", "现价": "10.00", "RPS强度": "80", "摘要": "旧"}],
        hydrate_latest_quotes=False,
    )
    spy = QSignalSpy(model.dataChanged)
    updated = [dict(model.row_data[0])]
    updated[0]["RPS强度"] = "95"

    model.update_data(updated, hydrate_latest_quotes=False)

    assert len(spy) == 1
    changed_col = model.headers.index("RPS强度")
    assert spy[0][0].column() == changed_col
    assert spy[0][1].column() == changed_col
    roles = {int(getattr(role, "value", role)) for role in spy[0][2]}
    assert STOCK_CELL_RENDER_ROLE in roles


def test_stock_table_model_incremental_snapshot_respects_disabled_flash():
    model = StockTableModel(["代码", "现价"])
    model.update_data([{"代码": "000001", "现价": "10.00"}], hydrate_latest_quotes=False)
    changes = QSignalSpy(model.dataChanged)

    model.update_data(
        [{"代码": "000001", "现价": "11.00"}],
        hydrate_latest_quotes=False,
        record_flash=False,
    )

    assert model.row_data[0]["现价"] == "11.00"
    assert model._flash_records == {}
    assert len(changes) == 1
    assert int(Qt.ItemDataRole.UserRole) + 1 not in changes[0][2]


def test_stock_table_model_silent_snapshot_clears_flash_for_overwritten_value():
    model = StockTableModel(["代码", "现价"])
    model.update_data([{"代码": "000001", "现价": "10.00"}], hydrate_latest_quotes=False)
    model.update_quotes({"000001": {"close": 11.0, "last_close": 10.0}})
    price_column = model.headers.index("现价")
    assert model._flash_records[0][price_column]["diff"] == 1.0

    model.update_data(
        [{"代码": "000001", "现价": "9.00"}],
        hydrate_latest_quotes=False,
        record_flash=False,
    )

    assert model.data(model.index(0, price_column), Qt.ItemDataRole.DisplayRole) == "9.00"
    assert model.data(model.index(0, price_column), Qt.ItemDataRole.UserRole + 1) is None


@pytest.mark.parametrize("record_flash", [True, False])
def test_stock_table_model_reorder_clears_overwritten_flash_and_keeps_unchanged_value(record_flash):
    model = StockTableModel(["代码", "现价"])
    model.update_data(
        [{"代码": "000001", "现价": "10.00"}, {"代码": "000002", "现价": "20.00"}],
        hydrate_latest_quotes=False,
    )
    model.update_quotes(
        {"000001": {"close": 11.0, "last_close": 10.0}, "000002": {"close": 21.0, "last_close": 20.0}}
    )
    price_column = model.headers.index("现价")
    unchanged_flash = dict(model._flash_records[1][price_column])
    assert model._flash_records[0][price_column]["diff"] == 1.0

    model.update_data(
        [{"代码": "000002", "现价": "21.00"}, {"代码": "000001", "现价": "9.00"}],
        hydrate_latest_quotes=False,
        record_flash=record_flash,
    )

    assert model.data(model.index(1, price_column), Qt.ItemDataRole.DisplayRole) == "9.00"
    assert model.data(model.index(1, price_column), Qt.ItemDataRole.UserRole + 1) is None
    assert model.data(model.index(0, price_column), Qt.ItemDataRole.UserRole + 1) == unchanged_flash


def test_stock_table_model_reorder_moves_existing_flash_with_stock_and_persistent_index():
    model = StockTableModel(["代码", "现价"])
    model.update_data(
        [{"代码": "000001", "现价": "10.00"}, {"代码": "000002", "现价": "20.00"}],
        hydrate_latest_quotes=False,
    )
    price_column = model.headers.index("现价")
    persistent = QPersistentModelIndex(model.index(0, price_column))
    model.update_quotes({"000001": {"close": 11.0, "last_close": 10.0}})
    flash = dict(model._flash_records[0][price_column])
    source_rows = [dict(row) for row in reversed(model.row_data)]
    layouts = QSignalSpy(model.layoutChanged)
    resets = QSignalSpy(model.modelReset)

    model.update_data(source_rows, hydrate_latest_quotes=False, record_flash=False)

    assert len(layouts) == 1
    assert len(resets) == 0
    assert persistent.isValid() and persistent.row() == 1
    assert persistent.data(Qt.ItemDataRole.DisplayRole) == "11.00"
    assert model._flash_records == {1: {price_column: flash}}
    assert model.data(model.index(1, price_column), Qt.ItemDataRole.UserRole + 1) == flash
    assert model.data(model.index(0, price_column), Qt.ItemDataRole.UserRole + 1) is None


def test_stock_table_model_reorder_tracks_persistent_index_created_by_layout_listener():
    model = StockTableModel(["代码", "名称"])
    model.update_data(
        [{"代码": "000001", "名称": "甲"}, {"代码": "000002", "名称": "乙"}],
        hydrate_latest_quotes=False,
    )
    indexes = []
    code_column = model.headers.index("代码")
    model.layoutAboutToBeChanged.connect(
        lambda: indexes.append(QPersistentModelIndex(model.index(0, code_column)))
    )

    model.update_data([dict(row) for row in reversed(model.row_data)], hydrate_latest_quotes=False)

    assert len(indexes) == 1
    assert indexes[0].isValid() and indexes[0].row() == 1
    assert indexes[0].data(Qt.ItemDataRole.DisplayRole) == "000001"


def test_stock_table_model_hidden_row_style_change_notifies_accent_rail_column():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data(
        [{"代码": "000001", "名称": "A", "现价": "10.00", "_row_style": ""}],
        hydrate_latest_quotes=False,
    )
    spy = QSignalSpy(model.dataChanged)
    updated = [dict(model.row_data[0])]
    updated[0]["_row_style"] = "warning"

    model.update_data(updated, hydrate_latest_quotes=False)

    assert len(spy) == 1
    assert spy[0][0].column() == 0
    assert spy[0][1].column() == 0
    roles = {int(getattr(role, "value", role)) for role in spy[0][2]}
    assert int(Qt.ItemDataRole.UserRole) + 4 in roles
    assert STOCK_CELL_RENDER_ROLE in roles


def test_stock_table_model_pct_change_notifies_dependent_price_style():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%"])
    model.update_data(
        [{"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": -1.0}],
        hydrate_latest_quotes=False,
    )
    price_col = model.headers.index("现价")
    pct_col = model.headers.index("涨幅%")
    old_color = model.data(model.index(0, price_col), Qt.ItemDataRole.ForegroundRole)
    spy = QSignalSpy(model.dataChanged)
    updated = [dict(model.row_data[0])]
    updated[0]["涨幅%"] = 1.0

    model.update_data(updated, hydrate_latest_quotes=False)

    changed_spans = [(entry[0].column(), entry[1].column()) for entry in spy]
    assert any(start <= price_col <= end for start, end in changed_spans)
    assert any(start <= pct_col <= end for start, end in changed_spans)
    new_color = model.data(model.index(0, price_col), Qt.ItemDataRole.ForegroundRole)
    assert new_color != old_color


def test_stock_table_model_sort_value_cache_invalidates_on_cell_update():
    model = StockTableModel(["代码", "名称", "市值"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "市值": "105亿"},
        ]
    )

    cap_col = model.headers.index("市值")
    index = model.index(0, cap_col)

    assert model.data(index, Qt.ItemDataRole.UserRole) == 10_500_000_000
    assert (0, cap_col) in model._sort_value_cache

    model.set_cell_value(0, "市值", "210亿", emit_signal=False)

    assert model.data(index, Qt.ItemDataRole.UserRole) == 21_000_000_000


def test_stock_table_model_sort_cache_invalidates_on_external_data_changed():
    model = StockTableModel(["代码", "名称", "涨幅%"])
    model.update_data(
        [
            {"代码": "A", "名称": "A", "涨幅%": 1.0},
            {"代码": "B", "名称": "B", "涨幅%": 2.0},
        ]
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)
    code_col = model.headers.index("代码")
    pct_col = model.headers.index("涨幅%")

    proxy.sort(pct_col, Qt.SortOrder.DescendingOrder)
    assert [proxy.data(proxy.index(row, code_col), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == [
        "B",
        "A",
    ]

    model.row_data[0]["涨幅%"] = 5.0
    model.dataChanged.emit(model.index(0, 0), model.index(0, len(model.headers) - 1))
    proxy.sort(pct_col, Qt.SortOrder.DescendingOrder)

    assert [proxy.data(proxy.index(row, code_col), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == [
        "A",
        "B",
    ]


def test_stock_table_model_percent_display_falls_back_to_text():
    model = StockTableModel(["代码", "名称", "涨幅%"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "涨幅%": "停牌"},
        ]
    )

    pct_col = model.headers.index("涨幅%")
    assert model.data(model.index(0, pct_col), Qt.ItemDataRole.DisplayRole) == "停牌"


def test_stock_table_model_incremental_update_marks_status_and_time_flash():
    model = StockTableModel(["代码", "名称", "状态", "最近时间"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "状态": "观察", "最近时间": "09:30"},
        ]
    )

    model.update_data(
        [
            {"代码": "000001", "名称": "A", "状态": "触发", "最近时间": "09:35"},
        ]
    )

    status_col = model.headers.index("状态")
    time_col = model.headers.index("最近时间")
    assert model.data(model.index(0, status_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0
    assert model.data(model.index(0, time_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0


def test_stock_table_model_prunes_expired_flash_records_on_update():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 0.0, "市值": "--", "_zongguben": 1_000_000_000},
        ]
    )
    model._flash_records = {
        99: {1: {"time": time.time() - 10, "diff": 1.0}},
    }

    model.update_quotes({"000001": {"close": 10.5, "last_close": 10.0}})

    assert 99 not in model._flash_records


def test_flash_decay_alpha_is_monotonic_and_soft_lands():
    samples = [_flash_decay_alpha(elapsed, 0.5) for elapsed in (0.0, 0.08, 0.18, 0.32, 0.5)]

    assert samples[0] == 1.0
    assert samples[-1] == 0.0
    assert samples == sorted(samples, reverse=True)
    assert samples[1] > samples[2] > samples[3]


def test_stock_table_model_uses_bold_mono_font_for_large_market_moves():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.00", "涨幅%": 9.5},
        ],
        hydrate_latest_quotes=False,
    )

    price_col = model.headers.index("现价")
    pct_col = model.headers.index("涨幅%")
    name_col = model.headers.index("名称")
    price_font = model.data(model.index(0, price_col), Qt.ItemDataRole.FontRole)
    pct_font = model.data(model.index(0, pct_col), Qt.ItemDataRole.FontRole)
    name_font = model.data(model.index(0, name_col), Qt.ItemDataRole.FontRole)

    assert price_font.bold()
    assert pct_font.bold()
    assert price_font.fixedPitch()
    assert pct_font.pointSize() == 12
    assert "JetBrains Mono" in pct_font.families()
    assert not name_font.bold()


def test_stock_table_model_update_data_hydrates_latest_global_quotes(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--", "_zongguben": 1_000_000_000},
        ]
    )

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


def test_stock_table_model_append_rows_hydrates_existing_quote_snapshot_for_duplicate_codes(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000002": {"close": 10.5, "last_close": 10.0, "total_shares": 1_000_000_000}},
    )

    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    model.update_data(
        [{"代码": "000001", "名称": "首批", "现价": "--", "涨幅%": "--", "市值": "--"}],
        hydrate_latest_quotes=False,
    )

    appended = model.append_rows(
        [
            {"代码": "000002", "名称": "追加甲", "现价": "--", "涨幅%": "--", "市值": "--"},
            {"代码": "000002", "名称": "追加乙", "现价": "--", "涨幅%": "--", "市值": "--"},
        ]
    )

    assert appended == 2
    assert [row["现价"] for row in model.row_data[1:]] == ["10.50", "10.50"]
    assert all(row["涨幅%"] == pytest.approx(5.0) for row in model.row_data[1:])
    assert [row["市值"] for row in model.row_data[1:]] == ["105亿", "105亿"]


def test_stock_table_model_supports_shijia_header(monkeypatch):
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    model = StockTableModel(["代码", "名称", "市价", "涨幅%", "市值"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "市价": "--", "涨幅%": "--", "市值": "--", "_zongguben": 1_000_000_000},
        ]
    )

    assert model.row_data[0]["市价"] == "10.50"
    assert model.row_data[0]["涨幅%"] == pytest.approx(5.0)
    assert model.row_data[0]["市值"] == "105亿"
