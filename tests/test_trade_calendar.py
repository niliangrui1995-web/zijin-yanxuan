# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableView

from ui.components.trade_calendar import TradeCalendarWidget


def test_trade_calendar_uses_compact_weekday_labels():
    widget = TradeCalendarWidget()
    try:
        assert (
            widget.horizontalHeaderFormat()
            == widget.HorizontalHeaderFormat.SingleLetterDayNames
        )

        view = widget.findChild(QTableView, "qt_calendar_calendarview")
        assert view is not None

        model = view.model()
        labels = [
            model.data(model.index(0, column), Qt.ItemDataRole.DisplayRole)
            for column in range(model.columnCount())
        ]

        assert len(labels) == 7
        assert all(isinstance(label, str) and label for label in labels)
        assert all(len(label) == 1 for label in labels)
    finally:
        widget.deleteLater()
