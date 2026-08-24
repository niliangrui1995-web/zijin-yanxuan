# -*- coding: utf-8 -*-

import importlib

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
    scan_spy = QSignalSpy(event_bus.sig_scan_updated)
    fund_spy = QSignalSpy(event_bus.sig_fund_holdings_updated)
    ui_spy = QSignalSpy(event_bus.sig_task_progress)

    domain_events.sig_watchlist_changed.emit("add", "600519")
    domain_events.sig_scan_updated.emit()
    domain_events.sig_fund_holdings_updated.emit()
    ui_signals.sig_task_progress.emit("scan", 25, "running")

    assert len(domain_spy) == 1
    assert len(scan_spy) == 1
    assert len(fund_spy) == 1
    assert len(ui_spy) == 1


def test_core_domain_events_module_preserves_the_domain_event_contract():
    legacy_module = importlib.import_module("core.domain_events")
    target_module = importlib.import_module("domains.runtime.domain_events")

    assert legacy_module is not target_module
    assert legacy_module.domain_events is target_module.domain_events
    assert legacy_module.get_domain_events is target_module.get_domain_events
    assert legacy_module.DomainEventBus is target_module.DomainEventBus


def test_core_ui_signals_module_preserves_the_ui_signal_contract():
    legacy_module = importlib.import_module("core.ui_signals")
    target_module = importlib.import_module("ui.signals.ui_signal_bus")

    assert legacy_module is not target_module
    assert legacy_module.UISignalBus is target_module.UISignalBus
    assert legacy_module.ui_signals is target_module.ui_signals
