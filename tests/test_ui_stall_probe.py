# -*- coding: utf-8 -*-

from infra.diagnostics import ui_stall_probe as probe_module
from infra.diagnostics.ui_stall_probe import StallThresholds, UiStallProbe, ui_stall_span


def test_ui_stall_span_records_method_context(monkeypatch, qt_application):
    recorded = []
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", None)

    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=0, critical_ms=100),
        context_provider=lambda: {"tab": "watchlist"},
        auto_start=False,
    )
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", probe)
    monkeypatch.setattr(probe, "_record_stall", lambda event, elapsed_ms, **kwargs: recorded.append((event, kwargs)))

    with ui_stall_span("Test.method", tab="fund_holdings", signal="unit"):
        pass

    assert recorded
    assert recorded[0][0] == "ui.stall.method"
    assert recorded[0][1]["context"]["method"] == "Test.method"
    assert recorded[0][1]["context"]["tab"] == "fund_holdings"
    assert recorded[0][1]["context"]["signal"] == "unit"
    probe.deleteLater()


def test_ui_stall_probe_merges_current_tab_context(monkeypatch, qt_application):
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", None)
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        context_provider=lambda: {"tab": "watchlist", "widget": "WatchlistTab"},
        auto_start=False,
    )
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", probe)

    assert probe.current_context()["tab"] == "watchlist"
    assert probe.current_context()["widget"] == "WatchlistTab"
    probe.deleteLater()


def test_ui_stall_probe_does_not_keep_fast_span_context(monkeypatch, qt_application):
    recorded = []
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        auto_start=False,
    )
    monkeypatch.setattr(probe, "_record_stall", lambda event, elapsed_ms, **kwargs: recorded.append((event, kwargs)))

    probe.record_span(75, {"method": "Slow.method", "tab": "watchlist"})
    assert probe.current_context()["method"] == "Slow.method"

    probe.record_span(1, {"method": "Fast.method", "tab": "watchlist"})
    assert "method" not in probe.current_context()
    assert recorded
    probe.deleteLater()
