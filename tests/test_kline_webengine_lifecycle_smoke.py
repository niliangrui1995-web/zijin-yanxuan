from types import SimpleNamespace

import pandas as pd

import scripts.kline_webengine_lifecycle_smoke as lifecycle_smoke
import scripts.perf_budget_check as perf_budget_check
from scripts.kline_webengine_lifecycle_smoke import (
    MINIMUM_LIFECYCLE_CYCLES,
    _parse_args,
    _summarize_cycles,
    evaluate_lifecycle,
)
from scripts.perf_budget_check import check_kline_lifecycle_budget


def _sample(label: str, children: int) -> dict:
    return {
        "label": label,
        "rss_mb": 100.0,
        "thread_count": 10,
        "background_task_count": 0,
        "active_timer_count": 2,
        "total_timer_count": 3,
        "event_receiver_count": 4,
        "webengine_available": True,
        "webengine_child_count": children,
    }


def _stall_snapshot(**overrides) -> dict:
    snapshot = {
        "installed": True,
        "total_count": 1,
        "critical_count": 0,
        "event_loop_count": 1,
        "event_loop_critical_count": 0,
        "method_count": 0,
        "method_critical_count": 0,
        "max_elapsed_ms": 75.0,
        "warn_threshold_ms": 50.0,
        "critical_threshold_ms": 100.0,
    }
    snapshot.update(overrides)
    return snapshot


def _cached_switch_sample(elapsed_ms: float = 100.0) -> dict:
    return {
        "status": "ok",
        "measurement": "real_cached_stock_switch_commit",
        "cache_verified": True,
        "commit_verified": True,
        "provider_cache_hits_delta": 1,
        "elapsed_ms": elapsed_ms,
    }


def _complete_stage_diagnostics() -> dict:
    stages = list(lifecycle_smoke.KLINE_OPEN_STAGE_ORDER)
    return {
        "required_stages": stages,
        "completed_stages": stages,
        "pending_stages": [],
        "timings_ms": {stage: float(index + 1) * 100.0 for index, stage in enumerate(stages)},
        "complete": True,
    }


def test_kline_lifecycle_evaluation_accepts_reclaimed_webengine_child():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 2),
            _sample("after_close", 0),
        ],
        browser_ready=True,
        chart_ready=True,
        blocked=False,
        closed=True,
        load_events=[True],
    )

    assert summary["status"] == "ok"
    assert summary["webengine_child_seen"] is True
    assert summary["webengine_child_reclaimed"] is True


def test_open_cycle_waits_for_pending_keeper_request_to_resume(monkeypatch):
    chart = SimpleNamespace(code="000001", _closing=False)
    manager = SimpleNamespace(_charts=[])
    manager.open_chart = lambda *_args, **_kwargs: None
    provider = SimpleNamespace(prime_codes=lambda _codes: None)
    window = SimpleNamespace(data_provider=provider)
    args = SimpleNamespace(
        code="000001",
        name="平安银行",
        switch_code="000002",
        switch_name="万科A",
        open_timeout_ms=100,
    )

    def _resume_pending(_app, predicate, **_kwargs):
        manager._charts.append(chart)
        return predicate()

    monkeypatch.setattr(lifecycle_smoke, "_wait_until", _resume_pending)

    assert lifecycle_smoke._open_cycle_chart(object(), manager, window, args, {}) is chart


def test_close_cycle_waits_for_async_pool_return_to_settle(monkeypatch):
    state = {"closed": False}
    chart = object()
    manager = SimpleNamespace(
        active_chart_view_count=0,
        managed_webengine_keeper_count=1,
        managed_webengine_keeper_ready=False,
    )

    def _settle_return(_app, predicate, **_kwargs):
        state["closed"] = True
        manager.managed_webengine_keeper_ready = True
        return predicate()

    monkeypatch.setattr(lifecycle_smoke, "_close_kline_charts", lambda _app: 0)
    monkeypatch.setattr(lifecycle_smoke, "_kline_chart_closed", lambda _chart: state["closed"])
    monkeypatch.setattr(lifecycle_smoke, "_wait_until", _settle_return)
    monkeypatch.setattr(lifecycle_smoke, "_sample", lambda *_args, **_kwargs: {})
    cycle = {"samples": []}

    closed = lifecycle_smoke._close_and_record_cycle(
        object(),
        object(),
        manager,
        cycle,
        chart,
        (1, True),
        close_timeout_ms=100,
    )

    assert closed is True
    assert cycle["closed_count"] == 1
    assert cycle["final_managed_webengine_keeper_ready"] is True


def test_observe_cycle_subscribes_after_browser_ready_before_chart_ready(monkeypatch):
    calls: list[str] = []
    required_stages = list(lifecycle_smoke.KLINE_OPEN_STAGE_ORDER)
    recorded_stages = {"browser_ready"}

    def stage_diagnostics():
        completed = [stage for stage in required_stages if stage in recorded_stages]
        return {
            "required_stages": required_stages,
            "completed_stages": completed,
            "pending_stages": [stage for stage in required_stages if stage not in recorded_stages],
            "timings_ms": {stage: float(index) for index, stage in enumerate(completed)},
            "complete": completed == required_stages,
        }

    chart = SimpleNamespace(
        _open_stages=SimpleNamespace(
            recorded_stages=recorded_stages,
            stage_diagnostics=stage_diagnostics,
        ),
        _browser_epoch=1,
        _last_shell_load_epoch=1,
        _last_shell_load_ok=True,
    )
    cycle = {"cycle_index": 1, "samples": [], "load_status": {}}
    args = SimpleNamespace(open_timeout_ms=100)
    load_events: list[bool] = []
    wait_count = 0

    def fake_wait_until(_app, predicate, **_kwargs):
        nonlocal wait_count
        wait_count += 1
        if wait_count == 1:
            assert predicate() is True
            return True
        if wait_count == 2:
            load_events.append(True)
            chart._open_stages.recorded_stages.update(required_stages[:-1])
        else:
            chart._open_stages.recorded_stages.add("first_interaction")
        assert predicate() is True
        return True

    monkeypatch.setattr(
        lifecycle_smoke,
        "_kline_browser_ready",
        lambda _chart: calls.append("browser_ready") or True,
    )
    monkeypatch.setattr(lifecycle_smoke, "_wait_until", fake_wait_until)
    monkeypatch.setattr(
        lifecycle_smoke,
        "_connect_chart_load_signal",
        lambda _chart, _events: (calls.append("subscribed") or object(), object()),
    )
    monkeypatch.setattr(lifecycle_smoke, "_sample", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(lifecycle_smoke, "_chart_status_text", lambda _chart: "ready")
    monkeypatch.setattr(
        lifecycle_smoke,
        "_trigger_kline_first_interaction",
        lambda _chart: calls.append("first_interaction") or True,
    )

    observed = lifecycle_smoke._observe_cycle_chart(
        object(),
        object(),
        args,
        cycle,
        chart,
        load_events,
    )

    assert observed["browser_ready"] is True
    assert observed["chart_ready"] is True
    assert observed["first_interaction_ready"] is True
    assert cycle["stage_diagnostics"]["complete"] is True
    assert calls == ["browser_ready", "subscribed", "first_interaction"]


def test_observe_cold_warmup_records_prewarmed_page_identity_after_browser_ready(monkeypatch):
    calls: list[str] = []
    prewarmed_page = object()
    browser = SimpleNamespace(
        page=lambda: calls.append("page_identity") or prewarmed_page,
    )
    chart = SimpleNamespace(
        browser=browser,
        _browser_epoch=1,
        _last_shell_load_epoch=1,
        _last_shell_load_ok=True,
        _open_stages=SimpleNamespace(
            recorded_stages={"chart_ready"},
            stage_diagnostics=lambda: {},
        ),
    )
    cycle = {
        "measurement_role": "cold_warmup",
        "samples": [],
        "load_status": {},
    }
    monkeypatch.setattr(
        lifecycle_smoke,
        "_kline_browser_ready",
        lambda _chart: calls.append("browser_ready") or True,
    )
    monkeypatch.setattr(
        lifecycle_smoke,
        "_wait_until",
        lambda _app, predicate, **_kwargs: bool(predicate()),
    )
    monkeypatch.setattr(lifecycle_smoke, "_finish_ui_stall_scope", lambda *_args: _stall_snapshot())
    monkeypatch.setattr(lifecycle_smoke, "_wait_for_first_interaction", lambda *_args, **_kwargs: (True, True))
    monkeypatch.setattr(lifecycle_smoke, "_sample", lambda *_args, **_kwargs: {})

    observed = lifecycle_smoke._observe_cycle_chart(
        object(),
        object(),
        SimpleNamespace(open_timeout_ms=100),
        cycle,
        chart,
        [],
        prewarmed_page=prewarmed_page,
    )

    assert observed["browser_ready"] is True
    assert cycle["prewarmed_page_reused"] is True
    assert calls[:2] == ["browser_ready", "page_identity"]


def test_cycle_ui_stall_scope_is_reset_and_captured_before_resource_sampling(monkeypatch):
    calls: list[str] = []

    class _Probe:
        def reset_stall_snapshot(self):
            calls.append("reset")

        def stall_snapshot(self):
            calls.append("snapshot")
            return _stall_snapshot()

    monkeypatch.setattr(lifecycle_smoke, "get_ui_stall_probe", lambda: _Probe())
    monkeypatch.setattr(
        lifecycle_smoke,
        "_process_events",
        lambda *_args, **_kwargs: calls.append("settle"),
    )

    scope = lifecycle_smoke._begin_ui_stall_scope(object())
    captured = lifecycle_smoke._finish_ui_stall_scope(object(), scope)

    assert calls == ["settle", "reset", "settle", "snapshot"]
    assert captured["installed"] is True
    assert captured["scope"] == "kline_open_to_chart_ready"
    assert captured["critical_count"] == 0
    assert captured["event_loop_critical_count"] == 0
    assert captured["max_elapsed_ms"] == 75.0


def test_cycle_ui_stall_scope_fails_closed_when_probe_reset_or_snapshot_is_invalid(monkeypatch):
    class _BrokenResetProbe:
        def reset_stall_snapshot(self):
            raise RuntimeError("reset failed")

    monkeypatch.setattr(lifecycle_smoke, "_process_events", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle_smoke, "get_ui_stall_probe", lambda: _BrokenResetProbe())

    reset_failure = lifecycle_smoke._finish_ui_stall_scope(
        object(),
        lifecycle_smoke._begin_ui_stall_scope(object()),
    )

    assert reset_failure["installed"] is False
    assert reset_failure["error"] == "stall_snapshot_reset_failed"

    class _IncompleteProbe:
        def reset_stall_snapshot(self):
            return None

        def stall_snapshot(self):
            return {"installed": True, "critical_count": 0}

    monkeypatch.setattr(lifecycle_smoke, "get_ui_stall_probe", lambda: _IncompleteProbe())
    incomplete = lifecycle_smoke._finish_ui_stall_scope(
        object(),
        lifecycle_smoke._begin_ui_stall_scope(object()),
    )

    assert incomplete["installed"] is False
    assert incomplete["error"] == "stall_snapshot_invalid"


def test_chart_ready_uses_persisted_current_page_load_when_signal_was_missed():
    chart = SimpleNamespace(
        _browser_epoch=7,
        _last_shell_load_epoch=7,
        _last_shell_load_ok=True,
        _open_stages=SimpleNamespace(recorded_stages={"browser_ready", "chart_ready"}),
    )
    events: list[bool] = []

    assert lifecycle_smoke._chart_render_ready(chart, events) is True
    assert events == [True]

    chart._last_shell_load_epoch = 6
    assert lifecycle_smoke._chart_render_ready(chart, []) is False


def test_kline_lifecycle_evaluation_rejects_child_process_retention():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 2),
            _sample("after_close", 1),
        ],
        browser_ready=True,
        chart_ready=True,
        blocked=False,
        closed=True,
        load_events=[True],
    )

    assert summary["status"] == "fail"
    assert summary["webengine_child_reclaimed"] is False


def test_kline_lifecycle_evaluation_rejects_load_failure():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 1),
            _sample("after_close", 0),
        ],
        browser_ready=True,
        chart_ready=False,
        blocked=False,
        closed=True,
        load_events=[False],
    )

    assert summary["status"] == "fail"
    assert summary["load_failed"] is True


def test_kline_lifecycle_evaluation_rejects_missing_load_event_and_unavailable_diagnostics():
    unavailable = _sample("after_close", 0)
    unavailable["webengine_available"] = False
    unavailable["webengine_child_count"] = None

    summary = evaluate_lifecycle(
        [_sample("baseline", 0), _sample("after_open", 1), unavailable],
        browser_ready=True,
        chart_ready=False,
        blocked=False,
        closed=True,
        load_events=[],
    )

    assert summary["status"] == "fail"
    assert summary["load_succeeded"] is False
    assert summary["webengine_diagnostics_available"] is False


def test_kline_lifecycle_evaluation_rejects_unconfirmed_close():
    summary = evaluate_lifecycle(
        [_sample("baseline", 0), _sample("after_open", 1), _sample("after_close", 0)],
        browser_ready=True,
        chart_ready=True,
        blocked=False,
        closed=False,
        load_events=[True],
    )

    assert summary["status"] == "fail"
    assert summary["closed"] is False


def test_kline_lifecycle_summary_supports_multiple_cycles():
    samples = [
        _sample("cycle_1:before_open", 0),
        _sample("cycle_1:after_open", 2),
        _sample("cycle_1:after_close", 0),
        _sample("cycle_2:before_open", 0),
        _sample("cycle_2:after_open", 1),
        _sample("cycle_2:after_close", 0),
    ]
    cycles = [
        {"cycle_index": 1, "summary": {"status": "ok"}},
        {"cycle_index": 2, "summary": {"status": "ok"}},
    ]

    summary = _summarize_cycles(cycles, samples, minimum_cycles=2)

    assert summary["status"] == "ok"
    assert summary["cycles"] == 2
    assert summary["ok_cycles"] == 2
    assert summary["failed_cycles"] == []
    assert summary["final_webengine_child_count"] == 0


def test_lifecycle_smoke_defaults_to_ten_cycle_gate_with_explicit_override():
    default_args = _parse_args([])
    overridden = _parse_args(
        [
            "--cycles",
            "12",
            "--minimum-cycles",
            "12",
            "--provider-mode",
            "production-local",
        ]
    )

    assert MINIMUM_LIFECYCLE_CYCLES == 10
    assert default_args.cycles == 10
    assert default_args.minimum_cycles == 10
    assert default_args.provider_mode == "offline-synthetic"
    assert overridden.cycles == 12
    assert overridden.minimum_cycles == 12
    assert overridden.provider_mode == "production-local"


def _production_local_provider(*, source_layer: str = "parquet_sqlite_warehouse"):
    dates = pd.bdate_range("2025-01-01", periods=260)
    frame = pd.DataFrame(
        {
            "open": [10.0] * 260,
            "high": [11.0] * 260,
            "low": [9.0] * 260,
            "close": [10.5] * 260,
            "volume": [100_000] * 260,
        },
        index=dates,
    )

    class TdxDataProvider:
        def __init__(self):
            self._offline = True
            self.server_pool = []
            self._last_market_data_source_status = {}
            self.calls: list[str] = []

        def get_data(self, code):
            self.calls.append(code)
            self._last_market_data_source_status = {
                "ok": True,
                "active_layer": source_layer,
                "data_status": "ok",
                "trade_date": "2025-12-31",
            }
            return frame

    TdxDataProvider.__module__ = "infra.market_data.tdx_data_provider"
    return TdxDataProvider()


def test_production_local_provider_primes_real_bars_once_then_freezes_reads():
    source = _production_local_provider()
    provider = lifecycle_smoke._ProductionLocalSmokeDataProvider(source)

    provider.prime_codes(["000001", "000002"])
    provider.prime_codes(["000001", "000002"])
    assert source.calls == ["000001", "000002"]
    assert len(provider.get_data_fresh_for_chart("000002", force_sync=True)) == 260
    assert source.calls == ["000001", "000002"]

    evidence = provider.evidence()
    assert evidence["status"] == "ok"
    assert evidence["provider_class"] == "infra.market_data.tdx_data_provider.TdxDataProvider"
    assert evidence["network_guard_active"] is True
    assert evidence["network_access_enabled"] is False
    assert evidence["network_request_count"] == 0
    assert evidence["codes"]["000001"]["source_layer"] == "parquet_sqlite_warehouse"
    assert evidence["codes"]["000002"]["row_count"] == 260

    failures: list[dict] = []
    perf_budget_check._check_kline_lifecycle_production_local_provider(
        failures,
        {
            "data_provider": evidence,
            "cached_switch": {
                "provider_mode": "production-local",
                "cache_source": "production_local_frozen_cache",
            },
            "cycles": [
                {
                    "cached_switch": {
                        "provider_mode": "production-local",
                        "cache_source": "production_local_frozen_cache",
                    }
                }
            ],
        },
        {
            "provider_mode": "production-local",
            "code": "000001",
            "switch_code": "000002",
        },
    )
    assert failures == []


def test_production_local_provider_and_budget_fail_closed_without_local_source():
    provider = lifecycle_smoke._ProductionLocalSmokeDataProvider(
        _production_local_provider(source_layer="network_history")
    )

    try:
        provider.prime_codes(["000001", "000002"])
    except RuntimeError as exc:
        assert "production-local A-share history unavailable" in str(exc)
    else:
        raise AssertionError("non-local source must fail closed")

    failures: list[dict] = []
    perf_budget_check._check_kline_lifecycle_production_local_provider(
        failures,
        {
            "mode": {
                "provider_mode": "production-local",
                "code": "000001",
                "switch_code": "000002",
            },
            "data_provider": provider.evidence(),
            "cycles": [],
            "cached_switch": {},
        },
        {
            "provider_mode": "production-local",
            "code": "000001",
            "switch_code": "000002",
        },
    )

    assert [failure["check"] for failure in failures] == [
        "kline_lifecycle.provider.production_local"
    ]


def test_lifecycle_summary_rejects_successful_run_below_minimum_cycle_gate():
    samples = [_sample("before", 0), _sample("after", 0)]
    cycles = [
        {
            "cycle_index": index,
            "summary": {"status": "ok"},
            "samples": [_sample("before", 0), _sample("after", 0)],
        }
        for index in range(1, 3)
    ]

    summary = _summarize_cycles(
        cycles,
        samples,
        expected_cycles=2,
        minimum_cycles=10,
    )

    assert summary["status"] == "fail"
    assert summary["minimum_cycle_gate"] is False
    assert summary["minimum_cycles"] == 10


def test_lifecycle_summary_rejects_positive_resource_count_growth():
    baseline = {
        **_sample("before", 0),
        "rss_mb": 100.0,
        "thread_count": 10,
        "background_task_count": 0,
        "active_timer_count": 2,
        "total_timer_count": 3,
        "event_receiver_count": 4,
    }
    final = {
        **_sample("after", 0),
        "rss_mb": 110.0,
        "thread_count": 11,
        "background_task_count": 0,
        "active_timer_count": 2,
        "total_timer_count": 3,
        "event_receiver_count": 4,
    }
    cycles = [{"cycle_index": 1, "summary": {"status": "ok"}, "samples": [baseline, final]}]

    summary = _summarize_cycles(
        cycles,
        [baseline, final],
        expected_cycles=1,
        minimum_cycles=1,
    )

    assert summary["status"] == "fail"
    assert summary["resource_net_growth"]["thread_count"]["delta"] == 1
    assert summary["resource_net_growth"]["status"] == "fail"


def test_resource_growth_uses_warmup_after_close_as_steady_state_baseline():
    cold = {**_sample("warmup:before_open", 1), "rss_mb": 100.0, "thread_count": 9}
    warmed = {**_sample("warmup:after_close", 1), "rss_mb": 120.0, "thread_count": 10}
    measured_first = {**_sample("cycle_1:after_close", 1), "rss_mb": 121.0, "thread_count": 10}
    measured_last = {**_sample("cycle_10:after_close", 1), "rss_mb": 128.0, "thread_count": 10}
    warmup = {"samples": [cold, warmed]}
    cycles = [
        {"cycle_index": 1, "samples": [measured_first]},
        {"cycle_index": 10, "samples": [measured_last]},
    ]

    evidence = lifecycle_smoke._resource_growth_evidence(warmup, cycles)

    assert evidence["status"] == "ok"
    assert evidence["basis"] == "warmup_after_close_to_last_measured_after_close"
    assert evidence["cold_first_use_retained_mb"] == 20.0
    assert evidence["steady_state_lifecycle_net_growth_mb"] == 8.0
    assert evidence["resource_net_growth"]["rss_mb"] == {
        "available": True,
        "baseline": 120.0,
        "final": 128.0,
        "delta": 8.0,
        "budget": 24.0,
        "status": "ok",
    }


def test_resource_growth_fails_closed_without_exact_warmup_and_measured_close_samples():
    evidence = lifecycle_smoke._resource_growth_evidence(
        {"samples": [_sample("warmup:before_open", 1)]},
        [{"cycle_index": 1, "samples": [_sample("cycle_1:before_open", 1)]}],
    )

    assert evidence["status"] == "fail"
    assert evidence["cold_first_use_retained_mb"] is None
    assert evidence["steady_state_lifecycle_net_growth_mb"] is None
    assert evidence["resource_net_growth"]["diagnostics_available"] is False


def test_smoke_runner_keeps_two_explicit_warmups_outside_measured_cycles(monkeypatch):
    import ui.components.kline_window_manager as manager_module

    calls: list[tuple[int, bool, str, object | None]] = []
    monkeypatch.setattr(manager_module, "kline_manager", SimpleNamespace(_prewarm_view=None))
    monkeypatch.setattr(
        lifecycle_smoke,
        "_prepare_smoke_runtime",
        lambda *_args, **_kwargs: {"status": "ok"},
    )

    def fake_cycle(
        _app,
        _window,
        _args,
        cycle_index,
        *,
        measure_cached_switch,
        measurement_role,
        prewarmed_page=None,
    ):
        calls.append((cycle_index, measure_cached_switch, measurement_role, prewarmed_page))
        return {
            "cycle_index": 0 if measurement_role != "measured" else cycle_index + 1,
            "measurement_role": measurement_role,
            "samples": [],
            "summary": {"status": "ok"},
        }

    monkeypatch.setattr(lifecycle_smoke, "_run_one_cycle", fake_cycle)
    report = {"cycles": [], "samples": []}

    lifecycle_smoke._run_smoke_cycles(object(), object(), SimpleNamespace(open_timeout_ms=1), report, 10)

    assert calls[:2] == [(-2, True, "cold_warmup", None), (-1, True, "warmup", None)]
    assert calls[2:] == [(index, True, "measured", None) for index in range(10)]
    assert report["cold_warmup_cycle"]["measurement_role"] == "cold_warmup"
    assert report["warmup_cycle"]["measurement_role"] == "warmup"
    assert len(report["cycles"]) == 10


def test_smoke_runner_passes_prewarmed_page_only_to_cold_warmup(monkeypatch):
    import ui.components.kline_window_manager as manager_module

    prewarmed_page = object()
    manager = SimpleNamespace(_prewarm_view=prewarmed_page)
    calls: list[tuple[str, object | None]] = []
    monkeypatch.setattr(manager_module, "kline_manager", manager)
    monkeypatch.setattr(
        lifecycle_smoke,
        "_prepare_smoke_runtime",
        lambda *_args, **_kwargs: {"status": "ok"},
    )

    def fake_cycle(
        _app,
        _window,
        _args,
        _cycle_index,
        *,
        measure_cached_switch,
        measurement_role,
        prewarmed_page=None,
    ):
        assert measure_cached_switch is True
        calls.append((measurement_role, prewarmed_page))
        return {
            "cycle_index": 0,
            "measurement_role": measurement_role,
            "samples": [],
            "summary": {"status": "ok"},
        }

    monkeypatch.setattr(lifecycle_smoke, "_run_one_cycle", fake_cycle)
    report = {"cycles": [], "samples": []}

    lifecycle_smoke._run_smoke_cycles(object(), object(), SimpleNamespace(open_timeout_ms=1), report, 1)

    assert calls == [
        ("cold_warmup", prewarmed_page),
        ("warmup", None),
        ("measured", None),
    ]


def test_cold_warmup_status_requires_prewarmed_page_reuse_receipt():
    report = {
        "warmup_cycle": {"summary": {"status": "ok"}},
        "cold_warmup_cycle": {
            "measurement_role": "cold_warmup",
            "prewarmed_page_reused": False,
            "summary": {"status": "ok"},
        },
    }

    assert lifecycle_smoke._warmup_statuses(report) == ("ok", "fail")


def test_smoke_setup_report_exposes_preflight_failure_and_pending_state():
    manager = SimpleNamespace(
        managed_webengine_keeper_count=0,
        managed_webengine_keeper_ready=False,
        _webengine_available=False,
        _webengine_preflight_started=False,
        _webengine_failure="timeout>15s",
        _webengine_preflight_diagnostics={
            "attempt_count": 2,
            "attempts": [{"attempt": 1}, {"attempt": 2}],
        },
        _prewarm_started=False,
        _prewarm_failure="",
    )
    report = lifecycle_smoke._smoke_setup_report(
        SimpleNamespace(_first_paint_recorded=True, data_provider=object()),
        manager,
        ready=False,
        scheduled=True,
        timeout_ms=lifecycle_smoke.SMOKE_SETUP_TIMEOUT_MS,
    )

    assert report["status"] == "fail"
    assert report["preflight_pending"] is False
    assert report["preflight_failure"] == "timeout>15s"
    assert report["preflight_attempt_count"] == 2
    assert report["preflight_attempts"] == [{"attempt": 1}, {"attempt": 2}]
    assert report["timeout_ms"] == 40_000


def test_smoke_setup_uses_page_only_keeper_and_reports_keeper_shape(monkeypatch):
    import ui.components.kline_window_manager as manager_module

    calls = []
    manager = SimpleNamespace(
        managed_webengine_keeper_count=1,
        managed_webengine_keeper_ready=True,
        _webengine_available=True,
        _webengine_preflight_started=False,
        _webengine_failure="",
        _webengine_preflight_diagnostics={},
        _prewarm_started=False,
        _prewarm_failure="",
        _prewarm_main_window=None,
        runtime_health_snapshot=lambda: {"browser_count": 0, "page_count": 1},
    )
    manager.prewarm = lambda **kwargs: calls.append(kwargs) or True
    window = SimpleNamespace(
        _first_paint_recorded=True,
        data_provider=object(),
        show=lambda: None,
    )
    monkeypatch.setattr(manager_module, "kline_manager", manager)
    monkeypatch.setattr(
        lifecycle_smoke,
        "_wait_until",
        lambda _app, predicate, **_kwargs: bool(predicate()),
    )

    report = lifecycle_smoke._prepare_smoke_runtime(object(), window, timeout_ms=321)

    assert calls == [{"delay_ms": 0, "hidden_view": True}]
    assert report["status"] == "ok"
    assert report["keeper_shape"] == {
        "mode": "page_only",
        "browser_count": 0,
        "page_count": 1,
        "main_window_retained": False,
        "verified": True,
    }


def test_smoke_setup_rejects_a_full_window_keeper_shape():
    manager = SimpleNamespace(
        managed_webengine_keeper_count=1,
        managed_webengine_keeper_ready=True,
        _webengine_available=True,
        _webengine_preflight_started=False,
        _webengine_failure="",
        _webengine_preflight_diagnostics={},
        _prewarm_started=False,
        _prewarm_failure="",
        _prewarm_main_window=object(),
        runtime_health_snapshot=lambda: {"browser_count": 1, "page_count": 1},
    )
    window = SimpleNamespace(_first_paint_recorded=True, data_provider=object())

    assert lifecycle_smoke._smoke_runtime_ready(window, manager) is False
    report = lifecycle_smoke._smoke_setup_report(
        window,
        manager,
        ready=True,
        scheduled=True,
        timeout_ms=321,
    )

    assert report["status"] == "fail"
    assert report["keeper_shape"]["mode"] == "unexpected"
    assert report["keeper_shape"]["verified"] is False


def test_cached_switch_measurement_uses_real_navigation_and_frame_commit(monkeypatch):
    identity_before = SimpleNamespace(generation=1, code="000001")
    controller = SimpleNamespace(current_identity=identity_before, frame_owner=identity_before)
    provider = SimpleNamespace(
        is_cached_code=lambda code: code == "000002",
        cached_hit_count=lambda code: 1 if code == "000002" else 0,
    )
    chart = SimpleNamespace(
        code="000001",
        current_idx=0,
        code_list=[{"代码": "000001"}, {"代码": "000002"}],
        data_provider=provider,
        _load_controller=controller,
        _snapshot_inflight=None,
    )

    def switch_to_stock(index):
        assert index == 1
        chart.current_idx = index
        chart.code = "000002"
        committed = SimpleNamespace(generation=2, code="000002")
        controller.current_identity = committed
        controller.frame_owner = committed
        provider.cached_hit_count = lambda code: 2 if code == "000002" else 0

    chart._switch_to_stock = switch_to_stock
    times = iter((10.0, 10.123))
    monkeypatch.setattr(lifecycle_smoke.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        lifecycle_smoke,
        "_wait_until",
        lambda _app, predicate, **_kwargs: predicate(),
    )

    result = lifecycle_smoke._measure_cached_switch(
        object(),
        chart,
        SimpleNamespace(switch_timeout_ms=1000),
    )

    assert result["status"] == "ok"
    assert result["cache_verified"] is True
    assert result["commit_verified"] is True
    assert result["elapsed_ms"] == 123.0
    assert result["target_code"] == "000002"
    assert result["provider_cache_hits_delta"] == 1


def test_cached_switch_measurement_and_summary_fail_closed_without_real_cache_evidence():
    chart = SimpleNamespace(
        code="000001",
        current_idx=0,
        code_list=[{"代码": "000001"}, {"代码": "000002"}],
        data_provider=SimpleNamespace(is_cached_code=lambda _code: False),
    )

    failed = lifecycle_smoke._measure_cached_switch(
        object(),
        chart,
        SimpleNamespace(switch_timeout_ms=1000),
    )
    summary = lifecycle_smoke._cached_switch_summary(
        [
            {
                "cycle_index": index,
                "cached_switch": _cached_switch_sample(),
            }
            for index in range(1, 10)
        ]
        + [{"cycle_index": 10, "cached_switch": failed}]
    )

    assert failed["status"] == "fail"
    assert "elapsed_ms" not in failed
    assert summary["status"] == "fail"
    assert summary["samples_ms"] == [100.0] * 9
    assert summary["failed_cycles"] == [10]
    assert summary["failures"] == [
        {"cycle_index": 10, "error": "cached_switch_cache_evidence_unavailable"}
    ]


def test_cached_switch_summary_rejects_unverified_numeric_samples():
    summary = lifecycle_smoke._cached_switch_summary(
        [
            {
                "cycle_index": index,
                "cached_switch": {"status": "ok", "elapsed_ms": 1.0},
            }
            for index in range(1, 11)
        ]
    )

    assert summary["status"] == "fail"
    assert summary["samples_ms"] == []
    assert summary["failed_cycles"] == list(range(1, 11))


def _native_production_local_mode() -> dict:
    return {
        "native_qt": True,
        "allow_offscreen": False,
        "qt_platform": "windows",
        "provider_mode": lifecycle_smoke.PRODUCTION_LOCAL_PROVIDER_MODE,
    }


def _native_lifecycle_success() -> dict:
    same_stock = {
        "status": "ok",
        "first_open": {"chart_ready": True},
        "second_open": {"chart_ready": True},
        **{field: True for field in lifecycle_smoke._SAME_STOCK_REQUIRED_TRUE_FIELDS},
    }
    transition = {
        "status": "ok",
        "pause_observed": True,
        "runtime_reactivated": True,
        "chart_ready_after_resume": True,
    }
    visibility = {
        "status": "ok",
        "open": {"chart_ready": True},
        "hidden": transition,
        "minimized": transition,
        **{field: True for field in lifecycle_smoke._VISIBILITY_REQUIRED_TRUE_FIELDS},
    }
    recovery = {
        "status": "ok",
        "open": {"chart_ready": True},
        "recovery_attempts": 1,
        "recovery_limit": 1,
        "second_recovery_allowed": False,
        **{field: True for field in lifecycle_smoke._RECOVERY_REQUIRED_TRUE_FIELDS},
    }
    return {
        "required": True,
        "status": "ok",
        "network_guard": {
            "status": "ok",
            "network_guard_active": True,
            "network_access_enabled": False,
            "network_request_count_before": 0,
            "network_request_count_after": 0,
            "no_network_requests": True,
        },
        "same_stock_multi_window": same_stock,
        "visibility_pause_resume": visibility,
        "render_process_recovery": recovery,
    }


def test_native_production_local_lifecycle_gate_fails_closed_on_missing_scenario_fields():
    mode = _native_production_local_mode()
    args = _parse_args(["--native-qt", "--provider-mode", "production-local"])
    initialized = lifecycle_smoke._new_smoke_report(
        args,
        qt_platform="windows",
        cycles=10,
    )

    assert initialized["native_lifecycle"]["required"] is True
    assert initialized["native_lifecycle"]["status"] == "pending"
    assert lifecycle_smoke._native_lifecycle_evidence_failed({"mode": mode}) is True

    lifecycle = _native_lifecycle_success()
    assert lifecycle_smoke._native_lifecycle_evidence_failed(
        {"mode": mode, "native_lifecycle": lifecycle}
    ) is False
    lifecycle["render_process_recovery"] = {}
    assert lifecycle_smoke._native_lifecycle_evidence_failed(
        {"mode": mode, "native_lifecycle": lifecycle}
    ) is True


def test_same_stock_multi_window_smoke_keeps_second_window_ready_after_first_closes(monkeypatch):
    first = SimpleNamespace(browser=object(), _closing=False)
    second = SimpleNamespace(browser=object(), _closing=False)
    first_owner = {
        "window_id": "window-1",
        "generation": 1,
        "code": "000001",
        "task_id": "kline:window-1:1:history",
        "frame_owner_window_id": "window-1",
        "frame_owner_current": True,
    }
    second_owner = {
        "window_id": "window-2",
        "generation": 1,
        "code": "000001",
        "task_id": "kline:window-2:1:history",
        "frame_owner_window_id": "window-2",
        "frame_owner_current": True,
    }
    monkeypatch.setattr(
        lifecycle_smoke,
        "_open_same_stock_pair",
        lambda *_args: (
            first,
            second,
            {"chart_ready": True},
            {"chart_ready": True},
        ),
    )
    monkeypatch.setattr(
        lifecycle_smoke,
        "_chart_ownership_evidence",
        lambda chart: first_owner if chart is first else second_owner,
    )
    monkeypatch.setattr(lifecycle_smoke, "_chart_render_ready", lambda *_args: True)

    def close_chart(_app, chart, **_kwargs):
        if chart is first:
            first._closing = True
        return True

    monkeypatch.setattr(lifecycle_smoke, "_close_acceptance_chart", close_chart)

    report = lifecycle_smoke._run_same_stock_multi_window(
        object(),
        object(),
        SimpleNamespace(close_timeout_ms=100),
    )

    assert report["status"] == "ok"
    assert report["window_ids_distinct"] is True
    assert report["task_ids_distinct"] is True
    assert report["frame_owners_isolated"] is True
    assert report["second_survived_first_close"] is True


def test_visibility_smoke_pauses_and_reactivates_hidden_and_minimized_chart(monkeypatch):
    class Chart:
        def __init__(self):
            self._runtime_active = True
            self.visible = True
            self.hidden = False
            self.minimized = False

        def hide(self):
            self.visible = False
            self.hidden = True
            self._runtime_active = False

        def show(self):
            self.visible = True
            self.hidden = False
            self._runtime_active = True

        def showMinimized(self):
            self.visible = True
            self.minimized = True
            self._runtime_active = False

        def showNormal(self):
            self.visible = True
            self.hidden = False
            self.minimized = False
            self._runtime_active = True

        def raise_(self):
            return None

        def activateWindow(self):
            return None

        def isVisible(self):
            return self.visible

        def isHidden(self):
            return self.hidden

        def isMinimized(self):
            return self.minimized

    chart = Chart()
    monkeypatch.setattr(
        lifecycle_smoke,
        "_wait_until",
        lambda _app, predicate, **_kwargs: predicate(),
    )
    monkeypatch.setattr(lifecycle_smoke, "_chart_render_ready", lambda *_args: True)
    args = SimpleNamespace(open_timeout_ms=100)

    hidden = lifecycle_smoke._run_visibility_transition(
        object(), chart, args, minimized=False
    )
    minimized = lifecycle_smoke._run_visibility_transition(
        object(), chart, args, minimized=True
    )

    assert hidden == {
        "status": "ok",
        "mode": "hidden",
        "pause_observed": True,
        "runtime_reactivated": True,
        "chart_ready_after_resume": True,
    }
    assert minimized["status"] == "ok"
    assert minimized["pause_observed"] is True
    assert minimized["runtime_reactivated"] is True


def test_render_recovery_smoke_requires_one_recovery_and_replayed_snapshot(monkeypatch):
    old_browser = object()
    new_browser = object()
    lifecycle = SimpleNamespace(_recovery_used=False)
    chart = SimpleNamespace(
        browser=old_browser,
        _browser_epoch=1,
        _runtime_lifecycle=lifecycle,
    )
    ownership = {
        "window_id": "window-1",
        "generation": 3,
        "code": "000001",
        "frame_owner_current": True,
        "latest_snapshot": {
            "window_id": "window-1",
            "generation": 3,
            "code": "000001",
            "points": 260,
            "version": 7,
        },
    }
    snapshot = SimpleNamespace()
    monkeypatch.setattr(
        lifecycle_smoke,
        "_open_acceptance_chart",
        lambda *_args: (chart, {"chart_ready": True}),
    )
    monkeypatch.setattr(
        lifecycle_smoke,
        "_recovery_baseline",
        lambda _chart: (ownership, snapshot, old_browser, 1, True),
    )

    def emit(_chart):
        chart.browser = new_browser
        chart._browser_epoch = 2
        lifecycle._recovery_used = True
        return True

    monkeypatch.setattr(lifecycle_smoke, "_emit_controlled_renderer_termination", emit)
    monkeypatch.setattr(lifecycle_smoke, "_wait_for_recovery_structure", lambda *_args: True)
    monkeypatch.setattr(
        lifecycle_smoke,
        "_post_recovery_probes",
        lambda *_args: (
            {"ack_received": True, "last_snapshot_replayed": True},
            False,
        ),
    )
    monkeypatch.setattr(lifecycle_smoke, "_chart_ownership_evidence", lambda _chart: ownership)
    monkeypatch.setattr(lifecycle_smoke, "_chart_render_ready", lambda *_args: True)
    monkeypatch.setattr(lifecycle_smoke, "_close_acceptance_chart", lambda *_args, **_kwargs: True)

    report = lifecycle_smoke._run_render_process_recovery(
        object(),
        object(),
        SimpleNamespace(open_timeout_ms=100, close_timeout_ms=100),
    )

    assert report["status"] == "ok"
    assert report["recovery_attempts"] == 1
    assert report["recovery_limit"] == 1
    assert report["second_recovery_allowed"] is False
    assert report["last_snapshot_replayed"] is True


def test_rendered_snapshot_probe_uses_webengine_ack_identity(monkeypatch):
    snapshot = SimpleNamespace(
        window_id="window-1",
        generation=2,
        code="000001",
        points=260,
        version=5,
    )
    ack = {
        "ok": True,
        "rendered": True,
        "windowId": "window-1",
        "generation": 2,
        "code": "000001",
        "points": 260,
        "snapshotVersion": 5,
    }
    page = SimpleNamespace(runJavaScript=lambda _script, callback: callback(ack))
    chart = SimpleNamespace(browser=SimpleNamespace(page=lambda: page))

    def wait_for_ack(_app, predicate, **_kwargs):
        predicate()
        return predicate()

    monkeypatch.setattr(lifecycle_smoke, "_wait_until", wait_for_ack)

    report = lifecycle_smoke._probe_rendered_snapshot(
        object(), chart, snapshot, timeout_ms=100
    )

    assert report == {"ack_received": True, "last_snapshot_replayed": True}


def test_finalized_smoke_report_matches_exact_kline_performance_gate_contract():
    cold_before = {**_sample("cold_warmup:before_open", 1), "rss_mb": 100.0}
    cold_ready = {**_sample("cold_warmup:after_chart_ready", 2), "rss_mb": 105.0}
    cold_after = {**_sample("cold_warmup:after_close", 1), "rss_mb": 108.0}
    warmup_before = {**_sample("warmup:before_open", 1), "rss_mb": 108.0}
    warmup_ready = {**_sample("warmup:after_chart_ready", 2), "rss_mb": 112.0}
    warmup_after = {**_sample("warmup:after_close", 1), "rss_mb": 110.0}
    cycles = []
    for index in range(1, 11):
        before = {**_sample(f"cycle_{index}:before_open", 1), "rss_mb": 110.0}
        ready = {**_sample(f"cycle_{index}:after_chart_ready", 2), "rss_mb": 115.0}
        after = {**_sample(f"cycle_{index}:after_close", 1), "rss_mb": 112.0}
        cycles.append(
            {
                "cycle_index": index,
                "label": f"cycle_{index}",
                "measurement_role": "measured",
                "samples": [before, ready, after],
                "summary": {"status": "ok"},
                "stage_diagnostics": _complete_stage_diagnostics(),
                "first_interaction_triggered": True,
                "first_interaction_ready": True,
                "ui_stalls": _stall_snapshot(),
                "cached_switch": _cached_switch_sample(),
                "baseline_managed_webengine_keeper_count": 1,
                "final_managed_webengine_keeper_count": 1,
                "baseline_managed_webengine_keeper_ready": True,
                "final_managed_webengine_keeper_ready": True,
                "active_chart_view_count_after_close": 0,
            }
        )
    report = {
        "schema_version": 2,
        "report_type": "kline_webengine_lifecycle_smoke",
        "mode": {
            "native_qt": True,
            "allow_offscreen": False,
            "cycles": 10,
            "minimum_cycles": 10,
        },
        "cold_warmup_cycle": {
            "cycle_index": 0,
            "label": "cold_warmup",
            "measurement_role": "cold_warmup",
            "prewarmed_page_reused": True,
            "samples": [cold_before, cold_ready, cold_after],
            "summary": {"status": "ok"},
            "ui_stalls": _stall_snapshot(),
            "stage_diagnostics": _complete_stage_diagnostics(),
            "first_interaction_triggered": True,
            "first_interaction_ready": True,
        },
        "warmup_cycle": {
            "cycle_index": 0,
            "label": "warmup",
            "measurement_role": "warmup",
            "samples": [warmup_before, warmup_ready, warmup_after],
            "summary": {"status": "ok"},
            "ui_stalls": _stall_snapshot(),
            "stage_diagnostics": _complete_stage_diagnostics(),
            "first_interaction_triggered": True,
            "first_interaction_ready": True,
        },
        "cycles": cycles,
        "samples": [sample for cycle in cycles for sample in cycle["samples"]],
        "load_status": {},
        "shutdown": {
            "post_close": {**_sample("shutdown:post_close", 0), "rss_mb": 112.0},
            "included_in_lifecycle_resource_growth": False,
        },
    }

    finalized = lifecycle_smoke._finalize_smoke_report(report, 10, 10)

    assert finalized["summary"]["cold_first_use_retained_mb"] == 10.0
    assert finalized["summary"]["steady_state_lifecycle_net_growth_mb"] == 2.0
    assert finalized["cached_switch"]["samples_ms"] == [100.0] * 10
    assert finalized["budget"] == {"status": "ok", "failures": []}
    assert check_kline_lifecycle_budget(finalized) == []
