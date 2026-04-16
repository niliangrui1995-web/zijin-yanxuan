# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QAbstractItemView, QApplication

from ui.components.toast_widget import show_toast


def _build_copy_handler(current_table, original_handler):
    def new_handler(event):
        if event.matches(QKeySequence.StandardKey.Copy):
            selection_model = current_table.selectionModel()
            if selection_model:
                indexes = selection_model.selectedIndexes()
                if indexes:
                    current_idx = selection_model.currentIndex()
                    unique_rows = {item.row() for item in indexes}
                    if len(unique_rows) == 1 and current_idx.isValid():
                        indexes = [current_idx]

                    rows_dict = defaultdict(dict)
                    for item in indexes:
                        display_val = current_table.model().data(
                            item,
                            Qt.ItemDataRole.DisplayRole,
                        )
                        rows_dict[item.row()][item.column()] = (
                            str(display_val) if display_val is not None else ""
                        )

                    lines: list[str] = []
                    for row_key in sorted(rows_dict.keys()):
                        cols = rows_dict[row_key]
                        lines.append(
                            "\t".join(cols.get(col, "") for col in sorted(cols.keys()))
                        )

                    QApplication.clipboard().setText("\n".join(lines))
                    show_toast(
                        "已复制单元格内容，可直接粘贴到 Excel。",
                        "success",
                        current_table.window(),
                        duration=1500,
                    )

            event.accept()
            return

        original_handler(event)

    return new_handler


def install_table_copy_hooks(tables) -> None:
    for table in tables:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if getattr(table, "_copy_hook_installed", False):
            continue

        table.keyPressEvent = _build_copy_handler(table, table.keyPressEvent)
        table._copy_hook_installed = True
