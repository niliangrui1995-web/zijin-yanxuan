from PyQt6.QtCore import Qt

from ui.components.table_view_helpers import bounded_model_row, find_header_column, model_header_text


class _SimpleModel:
    def __init__(self, headers, rows):
        self._headers = list(headers)
        self._rows = int(rows)

    def columnCount(self):  # noqa: N802 - Qt model compatibility
        return len(self._headers)

    def rowCount(self):  # noqa: N802 - Qt model compatibility
        return self._rows

    def headerData(self, column, orientation, role):  # noqa: N802 - Qt model compatibility
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[column]
        return None


def test_table_view_helpers_find_columns_and_bound_rows():
    model = _SimpleModel(["名称", "代码", "市值"], 3)

    assert model_header_text(model, 1) == "代码"
    assert find_header_column(model, "代码") == 1
    assert find_header_column(model, "缺失") == -1
    assert bounded_model_row(model, -5) == 0
    assert bounded_model_row(model, 99) == 2
    assert bounded_model_row(None, 1) == -1
