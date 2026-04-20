# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer


def bind_table_view_state(owner, table, settings, header_state_savers: list, settings_key: str = "header_state") -> bool:
    """绑定表格列宽、列顺序和排序状态的自动恢复与防抖保存。"""

    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
    sort_column_key = f"{settings_key}/sort_column"
    sort_order_key = f"{settings_key}/sort_order"
    restored_sort = False
    sort_column = -1
    sort_order = Qt.SortOrder.AscendingOrder

    if settings.contains(settings_key):
        try:
            header.restoreState(settings.value(settings_key))
            header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logging.getLogger(__name__).warning(f"恢复列宽配置异常 {settings_key}: {exc}")

    if settings.contains(sort_column_key):
        try:
            sort_column = int(settings.value(sort_column_key, -1) or -1)
            sort_order_value = settings.value(sort_order_key, Qt.SortOrder.AscendingOrder.value)
            sort_order = Qt.SortOrder(int(sort_order_value))
            restored_sort = True
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logging.getLogger(__name__).warning(f"恢复排序配置异常 {settings_key}: {exc}")
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
            settings.setValue(settings_key, header.saveState())
            settings.setValue(sort_column_key, current_sort_column)
            settings.setValue(sort_order_key, int(current_sort_order.value))
            settings.sync()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logging.getLogger(__name__).debug(f"列宽配置保存失败: {exc}")

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
                logging.getLogger(__name__).warning(f"恢复排序状态异常 {settings_key}: {exc}")

        QTimer.singleShot(0, _restore_sort_state)

    return restored_sort
