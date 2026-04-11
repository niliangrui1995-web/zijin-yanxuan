from PyQt6.QtCore import Qt

from ui.models.table_models import RtSortFilterProxyModel, StockTableModel


def test_stock_table_model_prepends_serial_header():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "现价": "10.00"}])

    assert model.headers[0] == "序号"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "1"
    assert model.get_row_data(0)["序号"] == 1


def test_proxy_serial_column_stays_continuous_after_sort():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data([
        {"代码": "000001", "名称": "A", "现价": "10.00"},
        {"代码": "000002", "名称": "B", "现价": "30.00"},
        {"代码": "000003", "名称": "C", "现价": "20.00"},
    ])
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.sort(model.headers.index("现价"), Qt.SortOrder.DescendingOrder)

    assert [proxy.data(proxy.index(row, 0), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == ["1", "2", "3"]
    assert proxy.data(proxy.index(0, model.headers.index("代码")), Qt.ItemDataRole.DisplayRole) == "000002"


def test_hot_sector_display_keeps_full_text():
    model = StockTableModel(["代码", "名称", "热点板块"])
    full_text = "光通信(15d=100) | CPO概念(15d=96) | 铜连接(20d=93)"
    model.update_data([{"代码": "300308", "名称": "中际旭创", "热点板块": full_text}])

    idx = model.index(0, model.headers.index("热点板块"))
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == full_text
    assert model.data(idx, Qt.ItemDataRole.ToolTipRole) is not None
