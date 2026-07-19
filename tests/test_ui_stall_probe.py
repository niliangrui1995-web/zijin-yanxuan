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
    assert "method" not in probe.current_context()

    probe.record_span(1, {"method": "Fast.method", "tab": "watchlist"})
    assert "method" not in probe.current_context()
    assert recorded
    probe.deleteLater()


def test_ui_stall_probe_consumes_slow_span_context_once(monkeypatch, qt_application):
    records = []
    probe = UiStallProbe(
        timer_interval_ms=5,
        thresholds=StallThresholds(warn_ms=1, critical_ms=10),
        context_provider=lambda: {"tab": "system_log"},
        auto_start=False,
    )
    monkeypatch.setattr(probe, "_record_stall", lambda *args, **kwargs: records.append((args, kwargs)))

    probe.record_span(
        125,
        {"method": "ClassicWorkspace.ensure_tab_loaded", "tab": "scan", "signal": "f5_auto_scan"},
    )
    assert "method" not in probe.current_context()

    probe._last_tick = probe_module.time.perf_counter() - 0.2
    probe._last_event_loop_record_at = 0
    probe._poll_event_loop()

    assert records[-1][1]["context"]["method"] == "ClassicWorkspace.ensure_tab_loaded"
    assert records[-1][1]["context"]["signal"] == "f5_auto_scan"

    probe._last_tick = probe_module.time.perf_counter() - 0.2
    probe._last_event_loop_record_at = 0
    probe._poll_event_loop()

    assert "method" not in records[-1][1]["context"]
    assert records[-1][1]["context"]["tab"] == "system_log"
    probe.deleteLater()


def test_ui_stall_probe_exposes_cumulative_stall_snapshot(monkeypatch, qt_application):
    monkeypatch.setattr(probe_module, "emit_structured_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe_module, "record_metric", lambda *args, **kwargs: None)
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        auto_start=False,
    )

    probe.record_span(75, {"method": "Warn.method", "tab": "watchlist"})
    probe.record_span(125, {"method": "Critical.method", "tab": "watchlist"})

    snapshot = probe.stall_snapshot()
    assert snapshot["installed"] is True
    assert snapshot["total_count"] == 2
    assert snapshot["method_count"] == 2
    assert snapshot["critical_count"] == 1
    assert snapshot["method_critical_count"] == 1
    assert snapshot["max_elapsed_ms"] == 125.0
    probe._last_tick = 1.0
    probe._last_span_context = {"method": "stale"}
    probe._last_event_loop_record_at = 2.0
    monkeypatch.setattr(probe_module.time, "perf_counter", lambda: 10.0)
    probe.reset_stall_snapshot()
    reset_snapshot = probe.stall_snapshot()
    assert reset_snapshot["total_count"] == 0
    assert reset_snapshot["critical_count"] == 0
    assert reset_snapshot["max_elapsed_ms"] == 0.0
    assert probe._last_tick == 10.0
    assert probe._last_span_context == {}
    assert probe._last_event_loop_record_at == 0.0
    probe.deleteLater()


def test_ui_stall_probe_demotes_f5_background_system_log_event_loop(monkeypatch, qt_application):
    logs = []
    metrics = []
    monkeypatch.setattr(probe_module, "emit_structured_log", lambda event, **kwargs: logs.append((event, kwargs)))
    monkeypatch.setattr(
        probe_module,
        "record_metric",
        lambda _metric, _value, **kwargs: metrics.append(kwargs),
    )
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        auto_start=False,
    )

    probe._record_stall(
        "ui.stall.event_loop",
        250,
        context={"tab": "system_log", "background": "f5_precompute"},
        metric_name="ui_event_loop_stall_ms",
        extra={"event_loop_gap_ms": 275},
    )

    assert logs[0][0] == "ui.stall.event_loop"
    assert logs[0][1]["level"] == "info"
    assert logs[0][1]["severity"] == "warn"
    assert logs[0][1]["demoted_from_severity"] == "critical"
    assert metrics[0]["tags"]["severity"] == "warn"
    snapshot = probe.stall_snapshot()
    assert snapshot["event_loop_count"] == 1
    assert snapshot["event_loop_critical_count"] == 0
    assert snapshot["critical_count"] == 0
    probe.deleteLater()


def test_ui_stall_probe_keeps_foreground_system_log_event_loop_critical(monkeypatch, qt_application):
    logs = []
    monkeypatch.setattr(probe_module, "emit_structured_log", lambda event, **kwargs: logs.append((event, kwargs)))
    monkeypatch.setattr(probe_module, "record_metric", lambda *args, **kwargs: None)
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        auto_start=False,
    )

    probe._record_stall(
        "ui.stall.event_loop",
        250,
        context={"tab": "system_log"},
        metric_name="ui_event_loop_stall_ms",
    )

    assert logs[0][1]["level"] == "warning"
    assert logs[0][1]["severity"] == "critical"
    assert "demoted_from_severity" not in logs[0][1]
    assert probe.stall_snapshot()["event_loop_critical_count"] == 1
    probe.deleteLater()


def test_ui_stall_probe_stop_and_context_provider_error(qt_application):
    probe = UiStallProbe(
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        context_provider=lambda: (_ for _ in ()).throw(RuntimeError("bad context")),
        auto_start=False,
    )

    probe.start()
    probe.stop()

    assert probe.current_context()["context_error"] == "RuntimeError"
    probe.deleteLater()


def test_ui_stall_probe_throttles_event_loop_records(monkeypatch, qt_application):
    records = []
    probe = UiStallProbe(
        timer_interval_ms=5,
        thresholds=StallThresholds(warn_ms=1, critical_ms=10),
        auto_start=False,
    )
    now = probe_module.time.perf_counter()
    probe._last_tick = now - 0.1
    probe._last_event_loop_record_at = now
    monkeypatch.setattr(probe, "_record_stall", lambda *args, **kwargs: records.append((args, kwargs)))

    probe._poll_event_loop()

    assert records == []
    probe.deleteLater()


def test_ui_stall_probe_ignores_fast_event_loop_poll(monkeypatch, qt_application):
    records = []
    probe = UiStallProbe(
        timer_interval_ms=25,
        thresholds=StallThresholds(warn_ms=50, critical_ms=100),
        auto_start=False,
    )
    probe._last_tick = probe_module.time.perf_counter()
    monkeypatch.setattr(probe, "_record_stall", lambda *args, **kwargs: records.append((args, kwargs)))

    probe._poll_event_loop()

    assert records == []
    probe.deleteLater()


def test_install_ui_stall_probe_handles_missing_and_stale_application(monkeypatch, qt_application):
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", None)
    monkeypatch.setattr(probe_module.QApplication, "instance", staticmethod(lambda: None))

    assert probe_module.install_ui_stall_probe() is None

    class StaleProbe:
        def objectName(self):
            raise RuntimeError("deleted")

    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", StaleProbe())

    probe = probe_module.install_ui_stall_probe(app=qt_application, timer_interval_ms=5)

    assert probe is probe_module.get_ui_stall_probe()
    probe.stop()
    probe.deleteLater()
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", None)


def test_install_ui_stall_probe_reuses_live_probe(monkeypatch, qt_application):
    class LiveProbe:
        def objectName(self):
            return "live"

    live_probe = LiveProbe()
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", live_probe)

    assert probe_module.install_ui_stall_probe(app=qt_application) is live_probe
    monkeypatch.setattr(probe_module, "_ACTIVE_PROBE", None)
