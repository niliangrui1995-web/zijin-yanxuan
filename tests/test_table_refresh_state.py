# -*- coding: utf-8 -*-

from PyQt6.QtCore import Qt

from ui.components import VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockTableModel


def _rows(count: int):
    return [
        {"代码": f"{idx:06d}", "名称": f"N{idx}", "现价": f"{10 + idx:.2f}"}
        for idx in range(count)
    ]


def _process_events(app, rounds: int = 4):
    for _ in range(rounds):
        app.processEvents()


def test_vcp_table_view_restores_current_row_selection_after_model_reset(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    table.resize(420, 260)
    table.show()

    try:
        source_model.update_data(_rows(120))
        _process_events(qt_application)
        original_scroll = min(180, table.verticalScrollBar().maximum())
        table.setCurrentIndex(proxy_model.index(70, 2))
        table.selectRow(70)
        table.verticalScrollBar().setValue(original_scroll)

        source_model.update_data(
            [{"代码": "999999", "名称": "插入行", "现价": "1.00"}]
            + [
                {"代码": row["代码"], "名称": row["名称"], "现价": "88.88"}
                for row in _rows(120)
            ]
        )
        _process_events(qt_application)

        current = table.currentIndex()
        selected_rows = [index.row() for index in table.selectionModel().selectedRows()]

        assert current.isValid()
        assert table.verticalScrollBar().value() == original_scroll
        assert proxy_model.data(proxy_model.index(current.row(), 1), Qt.ItemDataRole.DisplayRole) == "000070"
        assert selected_rows == [71]
    finally:
        table.deleteLater()
