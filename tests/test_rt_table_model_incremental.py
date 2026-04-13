from ui.models.table_models import RtTableModel


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


def test_rt_table_model_incremental_update_falls_back_to_reset_when_order_changes():
    model = RtTableModel()
    resets = []

    model.modelReset.connect(lambda: resets.append(True))
    model.update_data([
        _row("000001", "10.00", "+1.00%"),
        _row("000002", "20.00", "+2.00%"),
    ])
    resets.clear()

    reused = model.update_rows_incremental([
        _row("000002", "20.00", "+2.00%"),
        _row("000001", "10.00", "+1.00%"),
    ])

    assert reused is False
    assert resets == [True]
