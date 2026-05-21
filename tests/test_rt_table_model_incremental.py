import time

from PyQt6.QtCore import Qt

from ui.models.table_models import RtSortFilterProxyModel, RtTableModel


def _row(code, price, pct, status="跟踪"):
    return {
        "代码": code,
        "名称": f"N{code}",
        "现价": price,
        "涨幅%": pct,
        "市值": "10亿",
        "时间": "09:30",
        "评分": "80",
        "RPS强度": "90/95",
        "突破状态": status,
        "区间振幅": "3.2%",
        "热点板块": "AI",
    }


def test_rt_table_model_incremental_update_emits_data_changed_without_reset():
    model = RtTableModel()
    resets = []
    changes = []

    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda *_args: changes.append(True))

    model.update_data([_row("000001", "10.00", "+1.00%")])
    resets.clear()
    changes.clear()

    reused = model.update_rows_incremental([_row("000001", "10.50", "+5.00%", "突破")])

    assert reused is True
    assert resets == []
    assert changes
    assert model.get_row_data(0)["现价"] == "10.50"
    assert model.get_row_data(0)["突破状态"] == "突破"
    price_col = model.headers.index("现价")
    status_col = model.headers.index("突破状态")
    assert model.data(model.index(0, price_col), Qt.ItemDataRole.UserRole + 1)["diff"] > 0
    assert model.data(model.index(0, status_col), Qt.ItemDataRole.UserRole + 1)["diff"] == 0


def test_rt_table_model_prunes_expired_flash_records_on_update():
    model = RtTableModel()
    model.update_data([_row("000001", "10.00", "+1.00%")])
    model._flash_records = {
        99: {1: {"time": time.time() - 10, "diff": 1.0}},
    }

    model.update_quotes({"000001": {"close": 10.5, "last_close": 10.0}})

    assert 99 not in model._flash_records


def test_rt_table_model_incremental_update_uses_layout_change_when_order_changes():
    model = RtTableModel()
    resets = []
    layouts = []

    model.modelReset.connect(lambda: resets.append(True))
    model.layoutChanged.connect(lambda: layouts.append(True))
    model.update_data(
        [
            _row("000001", "10.00", "+1.00%"),
            _row("000002", "20.00", "+2.00%"),
        ]
    )
    resets.clear()
    layouts.clear()

    reused = model.update_rows_incremental(
        [
            _row("000002", "20.00", "+2.00%"),
            _row("000001", "10.00", "+1.00%"),
        ]
    )

    assert reused is True
    assert resets == []
    assert layouts == [True]


def test_rt_table_model_pct_sort_uses_numeric_values():
    model = RtTableModel()
    model.update_data(
        [
            _row("000001", "10.00", "+2.00%"),
            _row("000002", "20.00", "+10.00%"),
            _row("000003", "30.00", "-1.00%"),
        ]
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)
    code_col = model.headers.index("代码")
    pct_col = model.headers.index("涨幅%")

    proxy.sort(pct_col, Qt.SortOrder.DescendingOrder)

    assert [proxy.data(proxy.index(row, code_col), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == [
        "000002",
        "000001",
        "000003",
    ]
