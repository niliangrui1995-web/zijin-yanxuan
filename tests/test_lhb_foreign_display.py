from PyQt6.QtCore import Qt

from ui.models.table_models import StockTableModel
from ui.workers.lhb_worker import _build_foreign_display


def test_build_foreign_display_generates_compact_summary_and_tooltip():
    display, tooltip = _build_foreign_display({
        "深股通": 8200.0,
        "高盛": -1200.0,
        "摩根士丹利": 600.0,
    })

    assert display.startswith("净买")
    assert "深股通+" in display
    assert "等3席" in display
    assert "外资合计：" in tooltip
    assert "高盛：净卖" in tooltip


def test_stock_table_model_sorts_foreign_column_by_numeric_net():
    model = StockTableModel(["代码", "名称", "外资净买入"])
    model.update_data([
        {"代码": "000001", "名称": "A", "外资净买入": "净卖800万", "外资净买(万)": -800},
        {"代码": "000002", "名称": "B", "外资净买入": "净买1200万", "外资净买(万)": 1200, "_外资净买入_tooltip": "外资合计：净买1200万"},
    ])

    foreign_col = model.headers.index("外资净买入")
    assert model.data(model.index(1, foreign_col), Qt.ItemDataRole.UserRole) == 1200.0
    assert model.data(model.index(1, foreign_col), Qt.ItemDataRole.ToolTipRole) == "外资合计：净买1200万"
