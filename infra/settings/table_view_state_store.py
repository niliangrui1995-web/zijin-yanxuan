# -*- coding: utf-8 -*-
"""Infrastructure store for table header state persistence."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer


class TableViewStateStore:
    def __init__(self, settings, *, settings_key: str = "header_state"):
        self._settings = settings
        self._settings_key = str(settings_key or "header_state").strip() or "header_state"
        self._sort_column_key = f"{self._settings_key}/sort_column"
        self._sort_order_key = f"{self._settings_key}/sort_order"

    def bind(self, owner, table, header_state_savers: list) -> bool:
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        restored_sort = False
        sort_column = -1
        sort_order = Qt.SortOrder.AscendingOrder

        if self._settings.contains(self._settings_key):
            try:
                header.restoreState(self._settings.value(self._settings_key))
                header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logging.getLogger(__name__).warning(f"恢复列表头配置异常 {self._settings_key}: {exc}")

        if self._settings.contains(self._sort_column_key):
            try:
                sort_column = int(self._settings.value(self._sort_column_key, -1) or -1)
                sort_order_value = self._settings.value(
                    self._sort_order_key,
                    Qt.SortOrder.AscendingOrder.value,
                )
                sort_order = Qt.SortOrder(int(sort_order_value))
                restored_sort = True
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logging.getLogger(__name__).warning(f"恢复排序配置异常 {self._settings_key}: {exc}")
                sort_column = -1
                sort_order = Qt.SortOrder.AscendingOrder
                restored_sort = False

        if not hasattr(owner, "_header_save_timers"):
            owner._header_save_timers = []

        throttle_timer = QTimer(owner)
        throttle_timer.setSingleShot(True)
        throttle_timer.setInterval(800)
        owner._header_save_timers.append(throttle_timer)

        def _save_state():
            try:
                sorted_column_getter = getattr(table, "sorted_column", None)
                current_sort_column = -1
                if callable(sorted_column_getter):
                    current_sort_column = int(sorted_column_getter())
                current_sort_order = header.sortIndicatorOrder()
                self._settings.setValue(self._settings_key, header.saveState())
                self._settings.setValue(self._sort_column_key, current_sort_column)
                self._settings.setValue(self._sort_order_key, int(current_sort_order.value))
                self._settings.sync()
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logging.getLogger(__name__).debug(f"列表头配置保存失败: {exc}")

        throttle_timer.timeout.connect(_save_state)
        header_state_savers.append(_save_state)

        header.sectionResized.connect(lambda: throttle_timer.start())
        header.sectionMoved.connect(lambda: throttle_timer.start())
        header.sortIndicatorChanged.connect(lambda *_args: throttle_timer.start())

        if restored_sort:

            def _restore_sort_state():
                try:
                    table.sortByColumn(sort_column, sort_order)
                except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    logging.getLogger(__name__).warning(f"恢复排序状态异常 {self._settings_key}: {exc}")

            QTimer.singleShot(0, _restore_sort_state)

        return restored_sort

