# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt


def model_header_text(model, column: int) -> str:
    try:
        return str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def find_header_column(model, header_text: str) -> int:
    if model is None:
        return -1
    expected = str(header_text or "").strip()
    if not expected:
        return -1
    try:
        column_count = int(model.columnCount())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return -1
    for column in range(column_count):
        if model_header_text(model, column) == expected:
            return column
    return -1


def bounded_model_row(model, row: int) -> int:
    if model is None:
        return -1
    try:
        row_count = int(model.rowCount())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return -1
    if row_count <= 0:
        return -1
    return max(0, min(row_count - 1, int(row)))
