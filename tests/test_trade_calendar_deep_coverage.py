# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QDate, QRect
from PyQt6.QtGui import QPainter, QPixmap

from domains.global_earnings_calendar.service import EarningsCalendarEvent
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded
from ui.components import trade_calendar as calendar_module

_FIXED_TODAY = dt.date(2026, 8, 25)


class _FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(_FIXED_TODAY.year, _FIXED_TODAY.month, _FIXED_TODAY.day)


def _freeze_calendar_today(monkeypatch):
    monkeypatch.setattr(calendar_module._dt, "date", _FixedDate)


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        if slot in self.slots:
            self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Thread:
    def __init__(self, *, running=False):
        self.running = running
        self.finished = _Signal()
        self.sig_result = _Signal()
        self.sig_error = _Signal()
        self.calls = []

    def isRunning(self):
        return self.running

    def cancel(self, reason):
        self.calls.append(("cancel", reason))

    def deleteLater(self):
        self.calls.append(("delete",))

    def setParent(self, parent):
        self.calls.append(("parent", parent))

    def start(self):
        self.running = True
        self.calls.append(("start",))


def _event(
    ticker="NVDA",
    *,
    report_date=None,
    priority="normal",
    status="estimated",
    source="",
    time_label="",
    beijing_time="",
    source_type="",
):
    return EarningsCalendarEvent(
        company=f"{ticker} Corp",
        ticker=ticker,
        sector="AI",
        report_date=report_date or _FIXED_TODAY.isoformat(),
        fiscal_period="Q2",
        time_label=time_label,
        beijing_time=beijing_time,
        status=status,
        source=source,
        priority=priority,
        market="US",
        call_time_source_type=source_type,
    )


def test_trade_calendar_helpers_and_refresh_worker_shutdown(monkeypatch):
    monkeypatch.setattr(calendar_module.theme_manager, "get", lambda token: f"#{len(token):06x}"[-7:])
    assert calendar_module._c("TEXT")
    assert calendar_module._priority_tone("super_giant") == "super_giant"
    assert calendar_module._priority_tone("unknown") == "normal"
    styles = calendar_module._priority_marker_styles({})
    assert set(styles) == {"super_giant", "strategic_giant", "normal"}

    worker = _Thread(running=False)
    calendar_module._remember_detached_refresh_worker(worker)
    calendar_module._remember_detached_refresh_worker(worker)
    assert calendar_module._DETACHED_EARNINGS_REFRESH_WORKERS.count(worker) == 1
    worker.finished.emit()
    assert worker not in calendar_module._DETACHED_EARNINGS_REFRESH_WORKERS

    calendar_module._shutdown_refresh_worker(worker)
    assert worker.calls == [("delete",)]

    running = _Thread(running=True)
    shutdowns = []
    monkeypatch.setattr(
        calendar_module,
        "request_thread_shutdown",
        lambda item, **kwargs: shutdowns.append((item, kwargs)) or kwargs["stop"](),
    )
    calendar_module._shutdown_refresh_worker(running)
    assert running in calendar_module._DETACHED_EARNINGS_REFRESH_WORKERS
    assert running.calls == [("cancel", "panel_disposed")]
    running.finished.emit()


def test_trade_calendar_paint_cell_all_visual_states(monkeypatch, qt_application):
    today = QDate.currentDate()
    selected = today.addDays(1)
    widget = calendar_module.TradeCalendarWidget(
        earnings_events={
            today.toString("yyyy-MM-dd"): [
                _event("A", priority="super_giant"),
                _event("B", priority="strategic_giant"),
                _event("C", priority="weird"),
                _event("D"),
            ]
        }
    )
    try:
        widget.setCurrentPage(today.year(), today.month())
        widget.setSelectedDate(selected)
        monkeypatch.setattr(
            calendar_module.MarketCalendar,
            "is_trade_day",
            lambda value, _market: value.day % 2 == 0,
        )
        pixmap = QPixmap(400, 300)
        pixmap.fill()
        painter = QPainter(pixmap)
        try:
            widget.paintCell(painter, QRect(0, 0, 80, 52), selected)
            widget.paintCell(painter, QRect(80, 0, 80, 52), today)
            widget.paintCell(painter, QRect(160, 0, 80, 52), today.addDays(2))
            widget.paintCell(painter, QRect(240, 0, 80, 52), today.addMonths(-1))
            widget.paintCell(painter, QRect(320, 0, 10, 10), today.addDays(3))
            widget._closing = True
            widget.paintCell(painter, QRect(0, 60, 80, 52), today)
            widget._closing = False
        finally:
            painter.end()
        assert not pixmap.isNull()
    finally:
        widget.close()
        widget.deleteLater()


def test_trade_calendar_events_theme_and_disposal_guards(monkeypatch, qt_application):
    widget = calendar_module.TradeCalendarWidget()
    try:
        event = _event(report_date="2026-07-15")
        widget.set_earnings_events([event])
        assert widget.earnings_events_for_date(QDate(2026, 7, 15)) == [event]
        assert widget.earnings_events_for_date("2026-07-15T01:00") == [event]
        widget._apply_theme_stylesheet()
        widget._closing = True
        widget.set_earnings_events({})
        widget._apply_theme_stylesheet()
        assert widget.earnings_events_for_date("2026-07-15") == [event]
        widget._closing = False
        widget._dispose()
        assert widget._closing
    finally:
        widget.deleteLater()


@pytest.mark.parametrize(
    ("service", "expected_result", "expected_error"),
    [
        (SimpleNamespace(refresh_events=lambda: ["plain"]), ["plain"], None),
        (
            SimpleNamespace(refresh_events=lambda **kwargs: [kwargs["cancellation_token"]]),
            "token",
            None,
        ),
        (
            SimpleNamespace(refresh_events=lambda: (_ for _ in ()).throw(TaskCancelledError("stop"))),
            None,
            None,
        ),
        (
            SimpleNamespace(refresh_events=lambda: (_ for _ in ()).throw(TaskDeadlineExceeded("late"))),
            None,
            "late",
        ),
        (
            SimpleNamespace(refresh_events=lambda: (_ for _ in ()).throw(RuntimeError("failed"))),
            None,
            "failed",
        ),
    ],
)
def test_earnings_refresh_worker_run_result_and_error_paths(service, expected_result, expected_error, qt_application):
    worker = calendar_module.EarningsCalendarRefreshWorker(service, timeout_seconds=10)
    results = []
    errors = []
    worker.sig_result.connect(results.append)
    worker.sig_error.connect(errors.append)
    worker.run()
    if expected_result == "token":
        assert results == [[worker.cancellation_token]]
    elif expected_result is not None:
        assert results == [expected_result]
    else:
        assert results == []
    assert errors == ([] if expected_error is None else [expected_error])


def test_earnings_refresh_worker_cancel_and_signature_fallback(monkeypatch, qt_application):
    calls = []
    worker = calendar_module.EarningsCalendarRefreshWorker(
        SimpleNamespace(refresh_events=lambda: calls.append("refresh") or [])
    )
    monkeypatch.setattr(calendar_module.inspect, "signature", lambda _fn: (_ for _ in ()).throw(ValueError("opaque")))
    assert worker._refresh_events() == []
    worker.cancel("closed")
    assert worker.cancellation_token.cancelled
    assert calls == ["refresh"]


def test_earnings_panel_cache_filter_group_and_format_edge_paths(monkeypatch, qt_application):
    _freeze_calendar_today(monkeypatch)
    class _BadStatus:
        def load_cache_status(self):
            raise OSError("bad")

    panel = calendar_module.OligarchEarningsCalendarPanel(events=[], service=_BadStatus())
    try:
        assert panel._cache_status == {}
        invalid = _event("BAD", report_date="not-a-date")
        empty_day = _event("EMPTY", report_date="")
        normal = _event("NORMAL", report_date=(_FIXED_TODAY + dt.timedelta(days=2)).isoformat())
        panel.set_events([invalid, empty_day, normal])
        panel.set_filter_mode("unknown")
        assert panel._filter_mode == "30d"
        assert [item.ticker for item in panel.filtered_events()] == ["EMPTY", "NORMAL"]
        panel.set_selected_date("not-a-date")
        assert [item.ticker for item in panel.filtered_events()] == ["BAD"]
        panel.set_filter_mode("all")
        groups = panel.grouped_events()
        assert any(day == "not-a-date" for day, _events in groups)

        assert panel._format_group_title("bad") == "bad"
        assert panel._format_event_line(_event("A", priority="", report_date="2026-07-15"))
        assert panel._format_time_line(_event(time_label="")) == "仅日期｜时间待确认"
        assert panel._format_time_line(_event(time_label="盘前")).startswith("盘前")
        assert panel._event_status_text(_event(status="confirmed")) == "确认"
        assert panel._event_status_text(_event(source="示例")) == "示例"
        assert panel._event_status_text(_event(beijing_time="07-15 08:00")) == "精确"
        assert panel._event_status_text(_event(time_label="盘后")) == "盘后"
        assert panel._event_badge_object_name(_event(status="confirmed")) == "earningsEventBadgeConfirmed"
        assert panel._event_badge_object_name(_event(time_label="盘后")) == "earningsEventBadgeBroad"
        assert panel._format_source_text(_event(source="", source_type="custom")) == "未知来源｜custom"
        assert panel._time_precision_counts([_event(beijing_time="x"), _event(time_label="盘前"), _event()]) == (
            1,
            1,
            1,
        )
    finally:
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        ({"status": "ok"}, ""),
        ({"status": "degraded", "providers": "bad", "failed_tickers": ["a", "b"]}, "A等2个Ticker"),
        ({"status": "degraded", "failed_days": ["2026-07-15"]}, "2026-07-15"),
        ({"status": "degraded", "reused_event_count": "bad"}, "保留可用旧快照"),
    ],
)
def test_earnings_panel_cache_status_shapes(status, fragment):
    assert fragment in calendar_module.OligarchEarningsCalendarPanel._format_cache_status(status)


def test_earnings_panel_refresh_reload_callbacks_and_dispose(monkeypatch, qt_application):
    class _Service:
        universe = {"A": 1}

        def load_cache_status(self):
            return {"status": "ok"}

        def load_events(self, **_kwargs):
            return [_event("CACHE")]

        def refresh_events(self):
            return [_event("NEW")]

    panel = calendar_module.OligarchEarningsCalendarPanel(events=[], service=None)
    try:
        panel.refresh_from_service()
        assert "暂无" in panel.status_label.text()
        panel.reload_from_service_cache()

        panel._service = _Service()
        made = []
        monkeypatch.setattr(
            calendar_module,
            "EarningsCalendarRefreshWorker",
            lambda service: made.append(_Thread()) or made[-1],
        )
        panel.refresh_from_service()
        worker = made[0]
        assert worker.calls == [("start",)]
        assert not panel.btn_refresh.isEnabled()
        panel.refresh_from_service()
        assert len(made) == 1

        worker.sig_result.emit([_event("NEW")])
        assert [event.ticker for event in panel._events] == ["NEW"]
        worker.sig_error.emit("bad")
        assert panel.status_label.text() == "刷新失败: bad"
        worker.running = False
        worker.finished.emit()
        assert panel.btn_refresh.isEnabled()
        assert panel._refresh_worker is None

        panel.reload_from_service_cache()
        assert [event.ticker for event in panel._events] == ["CACHE"]
        panel._service.load_events = lambda **_kwargs: (_ for _ in ()).throw(OSError("bad"))
        panel.reload_from_service_cache()

        worker = _Thread(running=False)
        panel._refresh_worker = worker
        shutdowns = []
        monkeypatch.setattr(calendar_module, "_shutdown_refresh_worker", lambda item: shutdowns.append(item))
        panel._dispose()
        assert panel._closing
        assert shutdowns == [worker]
        assert ("parent", None) in worker.calls

        panel._apply_theme()
        panel.set_events([])
        panel.set_selected_date("")
        panel.set_filter_mode("all")
        panel._clear_cards()
        panel._rebuild_cards()
        panel.refresh_from_service()
        panel._on_refresh_result([])
        panel._on_refresh_error("ignored")
        panel._on_refresh_finished()
        panel.reload_from_service_cache()
        panel._dispose()
    finally:
        panel.deleteLater()


def test_trade_date_edit_applies_calendar_date_and_width(qt_application):
    edit = calendar_module.TradeDateEdit(
        display_format="yyyy/MM/dd",
        date=QDate(2026, 7, 15),
        fixed_width=180,
    )
    try:
        assert edit.date() == QDate(2026, 7, 15)
        assert edit.displayFormat() == "yyyy/MM/dd"
        assert edit.width() == 180
        assert isinstance(edit.calendarWidget(), calendar_module.TradeCalendarWidget)
    finally:
        edit.close()
        edit.deleteLater()
