# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt


class WorkspaceNavigationService:
    """Handles grouped tab navigation and cross-tab stock selection."""

    def __init__(self, workspace):
        self._workspace = workspace

    def _tab_specs(self) -> list[dict]:
        specs = getattr(self._workspace, "_tab_specs", None)
        if specs is not None:
            return list(specs)
        tab_specs = getattr(self._workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def nav_groups(self) -> list[str]:
        groups: list[str] = []
        for spec in self._tab_specs():
            group = str(spec.get("group", "")).strip()
            if group and group not in groups:
                groups.append(group)
        return groups

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        specs = self._tab_specs()
        for index, spec in enumerate(specs):
            group = str(spec.get("group", "")).strip()
            result.setdefault(group, []).append(index)
        for group, indices in result.items():
            result[group] = sorted(
                indices,
                key=lambda idx: (
                    int(specs[idx].get("group_order", idx) or idx),
                    idx,
                ),
            )
        return result

    def select_scan_row(self, index: int) -> bool:
        get_tab = getattr(self._workspace, "get_tab", None)
        table = getattr(get_tab("scan") if callable(get_tab) else None, "table_scan", None)
        if table is None or index < 0:
            return False
        try:
            table.selectRow(index)
        except (AttributeError, RuntimeError, TypeError):
            return False
        return True

    @staticmethod
    def _iter_tab_tables(tab) -> list:
        tables = []
        for attr_name in ("table_sp", "table_scan", "table_rt", "na_daily_table", "asian_table", "table"):
            table = getattr(tab, attr_name, None)
            if table is not None and hasattr(table, "model") and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _find_code_column(model) -> int:
        if model is None:
            return -1
        try:
            column_count = int(model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1

        for column in range(column_count):
            try:
                header_text = str(
                    model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
                ).strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                header_text = ""
            if header_text == "代码":
                return column
        return -1

    @classmethod
    def _select_code_in_tab(cls, tab, code: str) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        for table in cls._iter_tab_tables(tab):
            model = table.model()
            code_column = cls._find_code_column(model)
            if code_column < 0:
                continue

            try:
                row_count = int(model.rowCount())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

            for row in range(row_count):
                try:
                    index = model.index(row, code_column)
                    row_code = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    continue
                if row_code != code_text:
                    continue

                try:
                    table.clearSelection()
                    table.setCurrentIndex(index)
                    table.selectRow(row)
                    table.scrollTo(index)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    return False
                return True

        return False

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        tab_widget = getattr(self._workspace, "tabs", None)
        if tab_widget is None:
            return False

        current_index = tab_widget.currentIndex()
        candidate_indices: list[int] = []

        if 0 <= current_index < tab_widget.count():
            candidate_indices.append(current_index)

        if isinstance(preferred_tab_index, int) and 0 <= preferred_tab_index < tab_widget.count():
            if preferred_tab_index not in candidate_indices:
                candidate_indices.append(preferred_tab_index)

        for tab_index in range(tab_widget.count()):
            if tab_index not in candidate_indices:
                candidate_indices.append(tab_index)

        for tab_index in candidate_indices:
            tab = tab_widget.widget(tab_index)
            if tab is None:
                continue
            if self._select_code_in_tab(tab, code_text):
                if tab_index != current_index:
                    tab_widget.setCurrentIndex(tab_index)
                return True

        return False
