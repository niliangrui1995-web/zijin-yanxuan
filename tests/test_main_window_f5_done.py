# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication

from core.event_bus import event_bus
from ui.main_window_qt import MainWindowQT


class _DummyLabel:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


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

    monkeypatch.setattr("ui.main_window_qt.QTimer.singleShot", lambda delay, callback: None)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == ["update_last_f5_time", "refresh_after_cache_reload"]
    assert dummy_window.lbl_status.text
    assert dummy_window.lbl_code_count.text
    assert len(cache_spy) == 1


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
            tab_fund_holdings=SimpleNamespace(
                run_auto_sync_after_f5=lambda: calls.append("fund_holdings_auto_sync")
            )
        ),
    )

    monkeypatch.setattr("ui.main_window_qt.QTimer.singleShot", lambda delay, callback: None)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == [
        "update_last_f5_time",
        "refresh_after_cache_reload",
        "fund_holdings_auto_sync",
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

    monkeypatch.setattr("ui.main_window_qt.QTimer.singleShot", lambda delay, callback: None)

    MainWindowQT._on_f5_done(dummy_window, 321, 4.5)
    app.processEvents()

    assert calls == [
        "update_last_f5_time",
        "refresh_after_cache_reload",
        "refresh_all_tabs_after_f5",
    ]
