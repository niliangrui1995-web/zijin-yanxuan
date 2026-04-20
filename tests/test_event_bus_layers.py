# -*- coding: utf-8 -*-

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QSignalSpy

from core.domain_events import domain_events
from core.event_bus import event_bus
from core.ui_signals import ui_signals


def _ensure_app():
    return QCoreApplication.instance() or QCoreApplication([])


def test_legacy_event_bus_forwards_domain_events():
    _ensure_app()
    spy = QSignalSpy(domain_events.sig_cache_bootstrap_ready)

    event_bus.sig_cache_bootstrap_ready.emit()

    assert len(spy) == 1


def test_legacy_event_bus_forwards_ui_signals():
    _ensure_app()
    spy = QSignalSpy(ui_signals.sig_show_kline_with_list)

    event_bus.sig_show_kline_with_list.emit("000001", [{"代码": "000001"}], 0)

    assert len(spy) == 1


def test_split_buses_can_be_observed_through_legacy_compatibility():
    _ensure_app()
    domain_spy = QSignalSpy(event_bus.sig_watchlist_changed)
    ui_spy = QSignalSpy(event_bus.sig_task_progress)

    domain_events.sig_watchlist_changed.emit("add", "600519")
    ui_signals.sig_task_progress.emit("scan", 25, "running")

    assert len(domain_spy) == 1
    assert len(ui_spy) == 1
