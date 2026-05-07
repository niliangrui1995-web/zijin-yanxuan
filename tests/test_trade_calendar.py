# -*- coding: utf-8 -*-
import datetime as dt

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableView

from domains.global_earnings_calendar.service import EarningsCalendarEvent
from ui.components.trade_calendar import OligarchEarningsCalendarPanel, TradeCalendarWidget


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


def test_trade_calendar_accepts_earnings_events_by_date():
    widget = TradeCalendarWidget()
    try:
        widget.set_earnings_events({
            "2026-05-07": [
                EarningsCalendarEvent(
                    "NVIDIA",
                    "NVDA",
                    "AI加速芯片与定制ASIC",
                    "2026-05-07",
                    priority="super_giant",
                )
            ]
        })

        events = widget.earnings_events_for_date("2026-05-07")

        assert len(events) == 1
        assert events[0].ticker == "NVDA"
    finally:
        widget.deleteLater()


def test_trade_calendar_marks_earnings_on_beijing_calendar_date():
    widget = TradeCalendarWidget()
    try:
        widget.set_earnings_events([
            EarningsCalendarEvent(
                "Lumentum",
                "LITE",
                "光芯片与硅光",
                "2026-05-05",
                time_label="盘后",
                beijing_time="05-06 05:00",
                market="US",
            )
        ])

        assert widget.earnings_events_for_date("2026-05-05") == []
        assert [event.ticker for event in widget.earnings_events_for_date("2026-05-06")] == ["LITE"]
    finally:
        widget.deleteLater()


def test_trade_calendar_earnings_marker_policy_uses_dots_without_count_text():
    events = [
        EarningsCalendarEvent(
            "NVIDIA",
            "NVDA",
            "AI加速芯片与定制ASIC",
            "2026-05-07",
            priority="super_giant",
        ),
        EarningsCalendarEvent(
            "AMD",
            "AMD",
            "AI加速芯片与定制ASIC",
            "2026-05-07",
            priority="strategic_giant",
        ),
        EarningsCalendarEvent(
            "Lumentum",
            "LITE",
            "光芯片与光引擎",
            "2026-05-07",
        ),
        EarningsCalendarEvent(
            "Arista",
            "ANET",
            "数据中心网络",
            "2026-05-07",
        ),
    ]

    marker = TradeCalendarWidget.earnings_marker_policy(events)

    assert marker["count_text"] == ""
    assert marker["dot_tones"] == ["super_giant", "strategic_giant", "normal"]


def test_oligarch_earnings_panel_filters_events_by_search_and_segment():
    events = [
        EarningsCalendarEvent(
            "NVIDIA",
            "NVDA",
            "AI加速芯片与定制ASIC",
            "2026-05-07",
            priority="super_giant",
        ),
        EarningsCalendarEvent(
            "Applied Materials",
            "AMAT",
            "前道晶圆设备与量测",
            "2026-05-13",
            priority="strategic_giant",
        ),
    ]
    panel = OligarchEarningsCalendarPanel(events=events)
    try:
        panel.search_box.setText("NVDA")
        assert [event.ticker for event in panel.filtered_events()] == ["NVDA"]

        panel.search_box.clear()
        panel.set_filter_mode("super_giant")
        assert [event.ticker for event in panel.filtered_events()] == ["NVDA"]

        panel.set_filter_mode("strategic_giant")
        assert [event.ticker for event in panel.filtered_events()] == ["AMAT"]
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_formats_strategic_priority_label():
    event = EarningsCalendarEvent(
        "Applied Materials",
        "AMAT",
        "前道晶圆设备与量测",
        "2026-05-13",
        priority="strategic_giant",
        market="US",
    )

    assert OligarchEarningsCalendarPanel._format_event_line(event).startswith("战略核心 |")


def test_oligarch_earnings_panel_filters_to_selected_calendar_date():
    events = [
        EarningsCalendarEvent(
            "Lumentum",
            "LITE",
            "光芯片与光引擎",
            "2026-05-05",
            time_label="盘后",
            beijing_time="05-06 05:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "AMD",
            "AMD",
            "AI加速芯片与定制ASIC",
            "2026-05-05",
            time_label="盘后",
            market="US",
        ),
        EarningsCalendarEvent(
            "NVIDIA",
            "NVDA",
            "AI加速芯片与定制ASIC",
            "2026-05-07",
            priority="super_giant",
        ),
    ]
    panel = OligarchEarningsCalendarPanel(events=events)
    try:
        panel.set_selected_date("2026-05-06")

        assert [event.ticker for event in panel.filtered_events()] == ["LITE", "AMD"]

        panel.set_filter_mode("all")
        assert [event.ticker for event in panel.filtered_events()] == ["LITE", "AMD", "NVDA"]
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_reloads_cached_events_from_service():
    future_report_date = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    class _FakeService:
        def __init__(self):
            self.calls = []

        def load_events(self, **kwargs):
            self.calls.append(kwargs)
            return [
                EarningsCalendarEvent(
                    "Lumentum",
                    "LITE",
                    "光芯片与光引擎",
                    future_report_date,
                )
            ]

    service = _FakeService()
    panel = OligarchEarningsCalendarPanel(events=[], service=service)
    try:
        panel.reload_from_service_cache()

        assert service.calls == [{"allow_network": False}]
        assert [event.ticker for event in panel.filtered_events()] == ["LITE"]
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_groups_filtered_events_by_beijing_calendar_date():
    events = [
        EarningsCalendarEvent(
            company="Lumentum",
            ticker="LITE",
            sector="Optical components",
            report_date="2026-05-05",
            time_label="盘后",
            beijing_time="05-06 05:00",
            status="confirmed",
            source="Lumentum IR",
        ),
        EarningsCalendarEvent(
            company="AMD",
            ticker="AMD",
            sector="Accelerators",
            report_date="2026-05-05",
            time_label="盘后",
            market="US",
            source="Nasdaq",
        ),
        EarningsCalendarEvent(
            company="NVIDIA",
            ticker="NVDA",
            sector="Accelerators",
            report_date="2026-05-07",
            priority="super_giant",
            source="Nasdaq",
        ),
    ]
    panel = OligarchEarningsCalendarPanel(events=events)
    try:
        panel.set_filter_mode("all")

        groups = panel.grouped_events()

        assert [(day, [event.ticker for event in items]) for day, items in groups] == [
            ("2026-05-06", ["LITE", "AMD"]),
            ("2026-05-07", ["NVDA"]),
        ]
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_orders_same_day_events_by_beijing_time():
    events = [
        EarningsCalendarEvent(
            "ADTRAN",
            "ADTN",
            "光纤光缆与宽带接入",
            "2026-05-04",
            beijing_time="05-05 20:30",
            market="US",
        ),
        EarningsCalendarEvent(
            "Eaton",
            "ETN",
            "数据中心电力与配电",
            "2026-05-05",
            beijing_time="05-05 23:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "Fabrinet",
            "FN",
            "光模块与光引擎",
            "2026-05-04",
            beijing_time="05-05 05:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "GlobalFoundries",
            "GFS",
            "先进制程代工",
            "2026-05-05",
            beijing_time="05-05 20:30",
            market="US",
        ),
    ]
    panel = OligarchEarningsCalendarPanel(events=events)
    try:
        panel.set_filter_mode("all")

        groups = panel.grouped_events()

        assert [(day, [event.ticker for event in items]) for day, items in groups] == [
            ("2026-05-05", ["FN", "ADTN", "GFS", "ETN"]),
        ]
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_describes_time_precision():
    panel = OligarchEarningsCalendarPanel(events=[])
    try:
        exact = EarningsCalendarEvent(
            company="Lumentum",
            ticker="LITE",
            sector="Optical components",
            report_date="2026-05-05",
            time_label="盘后",
            beijing_time="05-06 05:00",
            status="confirmed",
            source="Lumentum IR",
        )
        broad = EarningsCalendarEvent(
            company="AMD",
            ticker="AMD",
            sector="Accelerators",
            report_date="2026-05-05",
            time_label="盘后",
            source="Nasdaq",
        )
        date_only = EarningsCalendarEvent(
            company="Hanmi Semi",
            ticker="042700.KS",
            sector="Packaging equipment",
            report_date="2026-05-06",
            time_label="待确认",
            source="Yahoo Finance",
        )

        assert panel._format_time_line(exact) == "北京 05-06 05:00"
        assert "具体时刻待确认" in panel._format_time_line(broad)
        assert "\u5b98\u65b9\u672a\u786e\u8ba4" in panel._format_time_line(date_only)
    finally:
        panel.deleteLater()


def test_oligarch_earnings_panel_labels_yfinance_estimates_as_unofficial():
    panel = OligarchEarningsCalendarPanel(events=[])
    try:
        estimate = EarningsCalendarEvent(
            company="Hanmi Semi",
            ticker="042700.KS",
            sector="Packaging equipment",
            report_date="2026-05-06",
            status="estimated_unverified",
            source="Yahoo Finance",
            market="KR",
        )
        conflict = EarningsCalendarEvent(
            company="Hanmi Semi",
            ticker="042700.KS",
            sector="Packaging equipment",
            report_date="2026-05-06",
            status="estimated_conflict",
            source="Yahoo Finance",
            market="KR",
        )

        assert panel._event_status_text(estimate) == "\u4f30\u7b97"
        assert "\u5b98\u65b9\u672a\u786e\u8ba4" in panel._format_time_line(estimate)
        assert panel._event_status_text(conflict) == "\u65e5\u671f\u51b2\u7a81"
        assert "\u4ee5\u5b98\u65b9\u62ab\u9732\u4e3a\u51c6" in panel._format_time_line(conflict)
    finally:
        panel.deleteLater()
