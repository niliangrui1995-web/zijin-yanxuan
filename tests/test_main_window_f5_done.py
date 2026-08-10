# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QWidget

from core.event_bus import event_bus
from ui.main_window_qt import MainWindowQT


class _DummyLabel:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


def _capture_runtime_timers(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "ui.main_window_runtime.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    return scheduled


def _run_post_f5_quote_timer(scheduled):
    delay, callback = scheduled.pop(0)
    assert delay == 16
    callback()


def test_main_window_ui_stall_context_marks_f5_background_on_system_log(monkeypatch):
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    dummy_window = SimpleNamespace(
        _current_workspace_tab_key=lambda: "system_log",
        _f5_precompute_ui_grace_until=105.0,
        tabs=SimpleNamespace(currentWidget=lambda: widget),
    )
    monkeypatch.setattr("ui.main_window_qt.time.perf_counter", lambda: 100.0)

    try:
        context = MainWindowQT._ui_stall_context(dummy_window)

        assert app is not None
        assert context["tab"] == "system_log"
        assert context["background"] == "f5_precompute"
        assert context["widget"] == "QWidget"
    finally:
        widget.deleteLater()


def test_main_window_f5_done_refreshes_snapshot_and_emits_cache_reload_completed(monkeypatch):
    app = QApplication.instance() or QApplication([])
    cache_spy = QSignalSpy(event_bus.sig_cache_reload_completed)
    calls = []

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        lbl_status=_DummyLabel(),
        lbl_code_count=_DummyLabel(),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
    )

    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time"]
    _run_post_f5_quote_timer(scheduled)
    assert calls == ["update_last_f5_time", "refresh_after_cache_reload"]
    assert dummy_window.lbl_status.text
    assert dummy_window.lbl_code_count.text
    assert len(cache_spy) == 1
    assert not scheduled


def test_scan_progress_completion_does_not_schedule_full_gc(monkeypatch):
    scheduled_delays = []
    dummy_window = SimpleNamespace(progress_bar=SimpleNamespace(setValue=lambda value: None), lbl_status=_DummyLabel())
    monkeypatch.setattr(
        "ui.main_window_qt.QTimer.singleShot",
        lambda delay, callback: scheduled_delays.append(delay),
    )

    MainWindowQT._on_task_progress(dummy_window, "scan", 100, "done")

    assert scheduled_delays == []


def test_main_window_f5_done_triggers_fund_holdings_auto_sync(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        lbl_status=_DummyLabel(),
        lbl_code_count=_DummyLabel(),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
        _workspace=SimpleNamespace(
            run_fund_holdings_auto_sync_after_f5=lambda: calls.append("fund_holdings_auto_sync")
        ),
    )

    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time", "fund_holdings_auto_sync"]
    _run_post_f5_quote_timer(scheduled)
    assert calls == ["update_last_f5_time", "fund_holdings_auto_sync", "refresh_after_cache_reload"]


def test_main_window_f5_done_prefers_information_source_refresh(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        lbl_status=_DummyLabel(),
        lbl_code_count=_DummyLabel(),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
        _workspace=SimpleNamespace(
            refresh_information_sources_after_f5=lambda: calls.append("info_sources_after_f5"),
            run_fund_holdings_auto_sync_after_f5=lambda: calls.append("legacy_fund_sync"),
        ),
    )

    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time", "info_sources_after_f5"]
    _run_post_f5_quote_timer(scheduled)
    assert calls == ["update_last_f5_time", "info_sources_after_f5", "refresh_after_cache_reload"]


def test_main_window_f5_done_prefers_scheduled_information_source_refresh(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        lbl_status=_DummyLabel(),
        lbl_code_count=_DummyLabel(),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
        _workspace=SimpleNamespace(
            refresh_information_sources_after_f5_scheduled=(
                lambda interval_ms=0: calls.append(("scheduled_info_sources_after_f5", interval_ms)) or True
            ),
            refresh_information_sources_after_f5=lambda: calls.append("info_sources_after_f5"),
        ),
    )

    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time", ("scheduled_info_sources_after_f5", 2500)]
    _run_post_f5_quote_timer(scheduled)
    assert calls == [
        "update_last_f5_time",
        ("scheduled_info_sources_after_f5", 2500),
        "refresh_after_cache_reload",
    ]


def test_main_window_f5_done_refreshes_all_workspace_tabs_after_f5(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        lbl_status=_DummyLabel(),
        lbl_code_count=_DummyLabel(),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
        _workspace=SimpleNamespace(
            refresh_all_tabs_after_f5=lambda: calls.append("refresh_all_tabs_after_f5"),
        ),
    )

    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time", "refresh_all_tabs_after_f5"]
    _run_post_f5_quote_timer(scheduled)
    assert calls == ["update_last_f5_time", "refresh_all_tabs_after_f5", "refresh_after_cache_reload"]


def test_main_window_f5_done_waits_for_scheduled_tab_refresh_before_quotes(monkeypatch):
    calls = []
    captured = {}

    def _schedule_tabs(**kwargs):
        captured.update(kwargs)
        calls.append("schedule_tabs")
        return True

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload")
        ),
        _workspace=SimpleNamespace(refresh_all_tabs_after_f5_scheduled=_schedule_tabs),
    )
    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 0, 0)

    assert captured["interval_ms"] == 16
    assert captured["skip_cache_reload_tabs"] is True
    assert callable(captured["on_finished"])
    assert calls == ["update_last_f5_time", "schedule_tabs"]
    assert scheduled == []

    captured["on_finished"]()
    assert len(scheduled) == 1
    _run_post_f5_quote_timer(scheduled)
    assert calls == ["update_last_f5_time", "schedule_tabs", "refresh_after_cache_reload"]


def test_main_window_f5_done_replays_all_loaded_quotes_after_information_sources_finish(monkeypatch):
    calls = []
    captured = {}

    def _schedule_information_sources(*, interval_ms, on_finished):
        calls.append(("schedule_information_sources", interval_ms))
        captured["on_finished"] = on_finished
        return True

    def _refresh_information_quotes(codes, *, on_completed):
        calls.append(("refresh_information_quotes", set(codes)))
        on_completed()
        return True

    dummy_window = SimpleNamespace(
        _update_last_f5_time=lambda: calls.append("update_last_f5_time"),
        central_quotes_svc=SimpleNamespace(
            refresh_after_cache_reload=lambda: calls.append("refresh_after_cache_reload"),
            refresh_after_f5_information_sources=_refresh_information_quotes,
        ),
        _workspace=SimpleNamespace(
            refresh_information_sources_after_f5_scheduled=_schedule_information_sources,
            get_f5_off_market_quote_codes=lambda: {"300001"},
            replay_all_loaded_quote_snapshots_after_f5_scheduled=(
                lambda *, interval_ms: calls.append(("replay_quotes", interval_ms)) or True
            ),
        ),
    )
    scheduled = _capture_runtime_timers(monkeypatch)

    MainWindowQT._on_f5_done(dummy_window, 0, 0)

    assert calls == ["update_last_f5_time", ("schedule_information_sources", 2500)]
    captured["on_finished"]()

    _run_post_f5_quote_timer(scheduled)
    _run_post_f5_quote_timer(scheduled)
    _run_post_f5_quote_timer(scheduled)

    assert calls == [
        "update_last_f5_time",
        ("schedule_information_sources", 2500),
        "refresh_after_cache_reload",
        ("refresh_information_quotes", {"300001"}),
        ("replay_quotes", 16),
    ]
