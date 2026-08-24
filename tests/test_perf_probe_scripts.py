import json
from types import SimpleNamespace

from scripts import perf_memory_probe, perf_round4_probe, perf_round5_probe
from scripts import runtime_health_stability_suite as runtime_suite
from scripts.perf_round5_probe import (
    _effective_probe_tabs,
    _loaded_info_source_keys,
    disable_information_source_refresh_after_f5,
    summarize_background_tasks,
    summarize_quote_calls,
)
from scripts.runtime_health_stability_suite import (
    _apply_mode_defaults,
    _build_budget_trend,
    _build_startup_lazy_budget,
    _cycle_tabs,
    _measurement_phase,
    _parse_args,
    _wait_for_background_preload,
    _wait_for_background_tasks_idle,
)
from scripts.soak_leak_probe import _trend
from ui.workspaces.tab_registry import (
    INFO_SOURCE_GROUP,
    TAB_DEFINITIONS,
    health_probe_tab_keys,
    startup_tab_keys,
)


def _sample(rss: float, private: float, threads: int = 20, label: str = "") -> dict:
    return {
        "label": label,
        "main": {
            "rss_mb": rss,
            "private_mb": private,
            "thread_count": threads,
        },
    }


def test_round4_and_round5_default_tabs_follow_registry():
    expected = health_probe_tab_keys()

    assert len(expected) == 11
    assert perf_round4_probe.DEFAULT_TABS == expected
    assert perf_round5_probe.DEFAULT_TABS == expected
    assert tuple(perf_round4_probe._parse_args([]).tabs) == expected
    assert tuple(perf_round5_probe._parse_args([]).tabs) == expected
    assert perf_round5_probe.INFO_SOURCE_TAB_KEYS == frozenset(
        definition.key for definition in TAB_DEFINITIONS if definition.group == INFO_SOURCE_GROUP
    )


def test_runtime_health_tab_orders_keep_probe_and_preload_registry_semantics_separate():
    assert runtime_suite.DEFAULT_TABS == health_probe_tab_keys()
    assert runtime_suite.BACKGROUND_PRELOAD_ORDER == startup_tab_keys()


def test_runtime_health_measurement_phase_names_background_preload_explicitly():
    assert _measurement_phase("after_background_preload") == "background_preload"


def test_soak_trend_treats_post_warmup_plateau_as_ok():
    samples = [
        _sample(170.0, 550.0, 18),
        _sample(188.0, 564.0, 20),
        _sample(292.0, 650.0, 100),
        _sample(301.0, 720.0, 110),
        _sample(304.0, 724.0, 110),
        _sample(302.0, 722.0, 110),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["rss"]["status"] == "ok"
    assert result["private"]["status"] == "ok"
    assert result["threads"]["status"] == "ok"


def test_soak_trend_flags_sustained_growth():
    samples = [
        _sample(170.0, 550.0, 18),
        _sample(190.0, 580.0, 20),
        _sample(230.0, 620.0, 30),
        _sample(270.0, 670.0, 40),
        _sample(315.0, 725.0, 50),
        _sample(355.0, 780.0, 60),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["rss"]["status"] == "warn"
    assert result["private"]["status"] == "warn"


def test_soak_trend_uses_close_samples_for_open_close_cycles():
    samples = [
        _sample(188.0, 564.0, 20, "after_window"),
        _sample(306.0, 728.0, 111, "kline_cycle_1_open"),
        _sample(299.0, 718.0, 111, "kline_cycle_1_close"),
        _sample(307.0, 729.0, 111, "kline_cycle_2_open"),
        _sample(301.0, 721.0, 111, "kline_cycle_2_close"),
        _sample(307.0, 728.0, 111, "kline_cycle_3_open"),
        _sample(302.0, 722.0, 110, "kline_cycle_3_close"),
        _sample(302.0, 722.0, 110, "after_window_close"),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["growth_basis"] == "stable_close_samples"
    assert result["private"]["status"] == "ok"


def test_round5_quote_summary_counts_repeated_post_f5_batches():
    calls = [
        {
            "phase": "during_f5",
            "codes": ["000001"],
            "signature": "000001",
            "duplicate_in_batch": 0,
        },
        {
            "phase": "post_f5",
            "codes": ["000001", "600519"],
            "signature": "000001|600519",
            "duplicate_in_batch": 0,
            "is_cache_only_source": False,
        },
        {
            "phase": "post_f5",
            "codes": ["600519", "000001", "000001"],
            "signature": "000001|600519",
            "duplicate_in_batch": 1,
            "is_cache_only_source": True,
        },
    ]

    summary = summarize_quote_calls(calls)

    assert summary["batch_count"] == 2
    assert summary["repeated_batch_signature_count"] == 1
    assert summary["duplicate_quote_code_count"] == 4
    assert summary["cache_only_quote_request_count"] == 1


def test_round5_background_summary_flags_info_source_tail():
    tasks = [
        {"phase": "post_f5", "source": "foreign_block", "task_id": "foreign_block_trade"},
        {"phase": "post_f5", "source": "watchlist", "task_id": "watchlist_vcp_refresh"},
    ]
    final_sample = {
        "active_background_task_ids": ["baseline", "foreign_block_trade"],
        "active_earnings_workers": ["routine"],
        "active_earnings_worker_count": 1,
    }

    summary = summarize_background_tasks(tasks, final_sample, {"baseline"})

    assert summary["scheduled_task_count"] == 2
    assert summary["information_source_task_count"] == 1
    assert summary["new_active_task_ids_final"] == ["foreign_block_trade"]
    assert summary["active_earnings_worker_count_final"] == 1


def test_round5_probe_filters_info_source_tabs_when_isolated():
    tabs = ("stock_candidates", "scan", "fund_holdings", "watchlist", "earnings")

    assert _effective_probe_tabs(tabs, isolate_info_source_refresh=True) == (
        "stock_candidates",
        "watchlist",
    )
    assert _effective_probe_tabs(tabs, isolate_info_source_refresh=False) == tabs


def test_round5_probe_can_disable_information_source_refresh_after_f5():
    class _Workspace:
        def refresh_information_sources_after_f5(self):
            return {"fund_holdings": True}

    workspace = _Workspace()

    result = disable_information_source_refresh_after_f5(workspace)

    assert result == {"disabled": True, "reason": "probe_isolation"}
    assert workspace.refresh_information_sources_after_f5() == {}


def test_round5_report_lists_only_loaded_info_source_tabs():
    class _Workspace:
        @staticmethod
        def tab_specs():
            return [
                {"key": "scan", "group": "情报源", "loaded": False},
                {"key": "earnings", "group": "情报源", "loaded": True},
                {"key": "watchlist", "group": "core", "loaded": True},
            ]

    assert _loaded_info_source_keys(_Workspace()) == {"earnings"}


def test_round5_probe_parse_args_exposes_auto_refresh_enabled_flag():
    args = perf_round5_probe._parse_args(["--no-auto-refresh-enabled"])

    assert args.auto_refresh_enabled is False


def test_runtime_health_suite_supports_explicit_soak_minutes():
    args = _apply_mode_defaults(
        _parse_args(
            [
                "--mode",
                "soak60",
                "--idle-minutes",
                "0.5",
                "--sample-output-dir",
                "tmp/runtime_health_samples",
            ]
        )
    )

    assert args.idle_seconds == 30
    assert args.kline_cycles == 1
    assert args.post_tab_idle_timeout_ms == runtime_suite.POST_TAB_IDLE_TIMEOUT_MS
    assert str(args.sample_output_dir).endswith("runtime_health_samples")


def test_runtime_health_suite_allows_post_tab_idle_timeout_override():
    args = _parse_args(["--mode", "short", "--post-tab-idle-timeout-ms", "1200", "--show-window"])

    assert args.post_tab_idle_timeout_ms == 1200
    assert args.show_window is True


def test_runtime_health_suite_emits_production_full_validation_profile():
    args = _apply_mode_defaults(
        _parse_args(
            [
                "--native-qt",
                "--show-window",
                "--startup-enabled",
                "--background-prewarm",
                "--kline-prewarm-enabled",
                "--central-quotes-enabled",
                "--real-f5",
                "--kline-cycles",
                "1",
            ]
        )
    )
    tabs = tuple(runtime_suite.DEFAULT_TABS)

    report = runtime_suite._new_suite_report(args, tabs, [], 0.0, True)

    assert report["validation_profile"] == "production_full"


def test_runtime_health_native_probe_can_show_window(monkeypatch):
    calls = []
    window = type("Window", (), {"show": lambda self: calls.append("show")})()
    app = type("App", (), {"processEvents": lambda self: calls.append("event")})()
    args = _parse_args(["--show-window", "--startup-settle-ms", "12"])
    monkeypatch.setattr(runtime_suite, "_settle", lambda _app, timeout: calls.append(("settle", timeout)))

    runtime_suite._prepare_probe_window(window, app, args)

    assert calls == ["show", "event", "event", ("settle", 12)]


def test_runtime_health_idle_stops_when_required_window_becomes_invisible(monkeypatch, tmp_path):
    visibility = iter((True, False))
    window = SimpleNamespace(isVisible=lambda: next(visibility))
    args = _apply_mode_defaults(
        _parse_args(
            [
                "--show-window",
                "--idle-seconds",
                "3",
                "--sample-every-seconds",
                "1",
                "--tab-cycles",
                "0",
                "--f5-cycles",
                "0",
                "--quote-cycles",
                "0",
                "--kline-cycles",
                "0",
            ]
        )
    )
    report = runtime_suite._new_suite_report(args, (), [], 0.0, True)
    evidence = runtime_suite._SuiteEvidence(tmp_path / "runtime_health.json")
    evidence.bind(report, (), 0, [])
    evidence.phase_start("startup_idle")
    settle_calls = []
    monkeypatch.setattr(
        runtime_suite,
        "_prepare_probe_window",
        lambda *_args, **_kwargs: {
            "first_paint_ms": 1.0,
            "initial_tab_ready_ms": None,
            "initial_tab_loaded": False,
            "initial_tab_ready": False,
            "initial_tab_status": "missing_key",
        },
    )
    monkeypatch.setattr(
        runtime_suite,
        "_build_startup_timing",
        lambda *_args, **_kwargs: {
            "script_module_inclusive": {"first_paint_ms": 1.0, "initial_tab_ready_ms": None},
            "application_initialization_inclusive": {
                "first_paint_ms": 1.0,
                "initial_tab_ready_ms": None,
            },
        },
    )
    monkeypatch.setattr(runtime_suite, "_take_suite_sample", lambda *_args: None)
    monkeypatch.setattr(runtime_suite, "_record_stall_phase", lambda *_args: None)
    monkeypatch.setattr(runtime_suite, "_settle", lambda _app, timeout: settle_calls.append(timeout))
    monkeypatch.setattr(
        runtime_suite,
        "_wait_for_startup_tasks_idle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must stop before task settle")),
    )

    completed = runtime_suite._run_startup_and_idle(
        window,
        object(),
        args,
        report,
        [],
        [],
        1.0,
        1.0,
        1.0,
        evidence,
    )

    assert completed is False
    assert settle_calls == [1000]
    assert report["window_visibility"] == {
        "required": True,
        "status": "not_visible",
        "planned_observation_seconds": 3,
        "actual_observation_seconds": 1,
        "first_invisible_at_seconds": 1,
        "first_invisible_reason": "window_not_visible",
    }
    assert report["startup_task_settle"] == {
        "status": "skipped",
        "reason": "window_visibility_failed",
    }
    checkpoint = json.loads(
        (tmp_path / "runtime_health.checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["event"] == "window_visibility_failed"
    assert checkpoint["current_phase"] == "startup_idle"
    assert checkpoint["visibility"] == report["window_visibility"]


def test_runtime_health_idle_checkpoint_flushes_each_exported_sample(monkeypatch, tmp_path):
    window = SimpleNamespace(isVisible=lambda: True)
    args = _apply_mode_defaults(
        _parse_args(
            [
                "--show-window",
                "--idle-seconds",
                "2",
                "--sample-every-seconds",
                "1",
            ]
        )
    )
    exported_paths = []
    report = runtime_suite._new_suite_report(args, (), exported_paths, 0.0, True)
    evidence = runtime_suite._SuiteEvidence(tmp_path / "runtime_health.json")
    evidence.bind(report, (), 0, exported_paths)
    evidence.phase_start("startup_idle")

    def _record_sample(_window, _args, label, _samples, paths, _evidence=None):
        paths.append(str(tmp_path / f"{label.replace(':', '_')}.json"))
        _evidence.sample_recorded(label)

    monkeypatch.setattr(runtime_suite, "_take_suite_sample", _record_sample)
    monkeypatch.setattr(runtime_suite, "_record_stall_phase", lambda *_args: None)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args: None)

    assert runtime_suite._run_idle_soak(
        window,
        object(),
        args,
        report,
        [],
        exported_paths,
        evidence,
    )

    checkpoint = json.loads(
        (tmp_path / "runtime_health.checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["event"] == "sample_recorded"
    assert checkpoint["last_sample_label"] == "idle:2s"
    assert checkpoint["visibility"]["actual_observation_seconds"] == 2
    assert checkpoint["sample_paths"] == exported_paths


def test_runtime_health_checkpoint_tracks_final_phase_sample(monkeypatch, tmp_path):
    args = _apply_mode_defaults(_parse_args(["--show-window"]))
    exported_paths = []
    report = runtime_suite._new_suite_report(args, (), exported_paths, 0.0, True)
    evidence = runtime_suite._SuiteEvidence(tmp_path / "runtime_health.json")
    evidence.bind(report, (), 0, exported_paths)

    def _sample(_window, *, label, exported_paths, **_kwargs):
        exported_paths.append(str(tmp_path / f"{label}.json"))

    monkeypatch.setattr(runtime_suite, "_sample", _sample)
    runtime_suite._take_suite_sample(object(), args, "final", [], exported_paths, evidence)

    checkpoint = json.loads(
        (tmp_path / "runtime_health.checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["event"] == "sample_recorded"
    assert checkpoint["last_sample_label"] == "final"
    assert checkpoint["sample_paths"] == exported_paths


def test_runtime_health_first_paint_budget_excludes_initial_tab_settle(monkeypatch):
    window = type(
        "Window",
        (),
        {
            "_first_paint_recorded": True,
            "_first_paint_elapsed_ms": 123.0,
            "_workspace": None,
            "show": lambda self: None,
        },
    )()
    args = _parse_args(["--show-window", "--startup-settle-ms", "300"])
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)

    phases = runtime_suite._prepare_probe_window(window, object(), args, suite_started_at=1.0)

    assert phases["first_paint_ms"] == 123.0
    assert phases["first_paint_recorded"] is True
    assert phases["initial_tab_key"] == ""


def test_runtime_health_startup_timing_keeps_window_and_inclusive_scopes_distinct():
    workspace = type("Workspace", (), {"_initial_tab_ready_elapsed_ms": 400.0})()
    window = type(
        "Window",
        (),
        {
            "_launch_started_at": 12.0,
            "_workspace": workspace,
        },
    )()
    phases = {
        "first_paint_ms": 100.0,
        "first_paint_recorded": True,
        "initial_tab_ready": True,
        "initial_tab_ready_ms": 400.0,
    }

    timing = runtime_suite._build_startup_timing(
        window,
        phases,
        window_probe_started_at=12.0,
        application_initialization_started_at=10.0,
        script_module_started_at=8.0,
    )

    assert timing["window_only"] == {"first_paint_ms": 100.0, "initial_tab_ready_ms": 400.0}
    assert timing["application_initialization_inclusive"] == {
        "first_paint_ms": 2100.0,
        "initial_tab_ready_ms": 2400.0,
    }
    assert timing["script_module_inclusive"] == {
        "first_paint_ms": 4100.0,
        "initial_tab_ready_ms": 4400.0,
    }
    assert timing["scope"]["includes_python_interpreter_startup"] is False
    assert timing["scope"]["includes_qapplication_initialization"] is True
    assert timing["scope"]["includes_native_dataframe_runtime_initialization"] is True
    assert timing["scope"]["includes_search_filter_runtime_initialization"] is True


def test_runtime_health_suite_preheats_search_runtime_before_window_creation(qt_application, monkeypatch):
    calls = []
    window = object()

    monkeypatch.setattr(
        runtime_suite,
        "_initialize_native_runtime_for_probe",
        lambda: calls.append("native") or (12.0, True),
    )
    monkeypatch.setattr(
        runtime_suite,
        "_initialize_search_runtime_for_probe",
        lambda: calls.append("search") or (280.0, True),
    )
    monkeypatch.setattr(
        runtime_suite,
        "_create_probe_window",
        lambda _args: calls.append("window") or window,
    )
    monkeypatch.setattr(
        runtime_suite,
        "_run_startup_and_idle",
        lambda *_args: calls.append("startup") or True,
    )
    monkeypatch.setattr(runtime_suite, "_run_tab_phase", lambda *_args: calls.append("tabs"))
    monkeypatch.setattr(runtime_suite, "_run_refresh_phases", lambda *_args: calls.append("refresh"))
    monkeypatch.setattr(runtime_suite, "_run_kline_phase", lambda *_args: calls.append("kline"))
    monkeypatch.setattr(runtime_suite, "_finalize_probe_window", lambda *_args: calls.append("shutdown") or {})
    monkeypatch.setattr(runtime_suite, "_finish_suite_report", lambda report, *_args: report)

    report = runtime_suite.run_suite(
        _parse_args(
            [
                "--idle-seconds",
                "0",
                "--tab-cycles",
                "0",
                "--f5-cycles",
                "0",
                "--quote-cycles",
                "0",
                "--kline-cycles",
                "0",
            ]
        )
    )

    assert calls == ["native", "search", "window", "startup", "tabs", "refresh", "kline", "shutdown"]
    assert report["search_filter_runtime"] == {
        "ready": True,
        "initialization_ms": 280.0,
        "excluded_from_window_only_startup_timing": True,
        "included_in_script_module_inclusive_startup_timing": True,
        "included_in_application_initialization_startup_timing": True,
    }


def test_runtime_health_suite_skips_workload_after_idle_visibility_failure(qt_application, monkeypatch):
    calls = []
    window = object()
    monkeypatch.setattr(runtime_suite, "_initialize_native_runtime_for_probe", lambda: (0.0, True))
    monkeypatch.setattr(runtime_suite, "_initialize_search_runtime_for_probe", lambda: (0.0, True))
    monkeypatch.setattr(runtime_suite, "_create_probe_window", lambda _args: window)
    monkeypatch.setattr(
        runtime_suite,
        "_run_startup_and_idle",
        lambda *_args: calls.append("startup") or False,
    )
    monkeypatch.setattr(runtime_suite, "_run_tab_phase", lambda *_args: calls.append("tabs"))
    monkeypatch.setattr(runtime_suite, "_run_refresh_phases", lambda *_args: calls.append("refresh"))
    monkeypatch.setattr(runtime_suite, "_run_kline_phase", lambda *_args: calls.append("kline"))
    monkeypatch.setattr(runtime_suite, "_finalize_probe_window", lambda *_args: calls.append("shutdown") or {})
    monkeypatch.setattr(runtime_suite, "_finish_suite_report", lambda report, *_args: report)

    report = runtime_suite.run_suite(
        _parse_args(
            [
                "--show-window",
                "--idle-seconds",
                "5",
                "--tab-cycles",
                "1",
                "--f5-cycles",
                "1",
                "--quote-cycles",
                "1",
                "--kline-cycles",
                "1",
            ]
        )
    )

    assert calls == ["startup", "shutdown"]
    assert report["aborted"] == {"phase": "idle", "reason": "window_visibility_failed"}


def test_runtime_health_initial_tab_waits_for_async_business_readiness(monkeypatch):
    class _Tab:
        _initial_data_loading = True

    tab = _Tab()

    class _Tabs:
        @staticmethod
        def currentIndex():
            return 0

    class _Workspace:
        tabs = _Tabs()

        @staticmethod
        def tab_specs():
            return [{"key": "watchlist"}]

        @staticmethod
        def get_loaded_tab(_key):
            return tab

    def _wait_until(_app, predicate, **kwargs):
        assert kwargs["timeout_ms"] == runtime_suite.INITIAL_TAB_READY_TIMEOUT_MS
        assert predicate() is False
        tab._initial_data_loading = False
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _wait_until)
    result = runtime_suite._probe_initial_tab(
        type("Window", (), {"_workspace": _Workspace()})(),
        object(),
        runtime_suite.time.perf_counter(),
    )

    assert result["initial_tab_loaded"] is True
    assert result["initial_tab_ready"] is True
    assert result["initial_tab_status"] == "ok"
    assert result["initial_tab_ready_ms"] is not None


def test_runtime_health_waits_for_background_tasks_to_idle(monkeypatch):
    class _App:
        def __init__(self):
            self.process_events_count = 0

        def processEvents(self):
            self.process_events_count += 1

    class _TaskManager:
        def __init__(self):
            self.values = [2, 1, 0, 0]

        @property
        def active_count(self):
            if len(self.values) > 1:
                return self.values.pop(0)
            return self.values[0]

    app = _App()
    monkeypatch.setattr(runtime_suite, "task_manager", _TaskManager())
    monkeypatch.setattr(runtime_suite.time, "sleep", lambda _seconds: None)

    result = _wait_for_background_tasks_idle(app, timeout_ms=100, step_ms=1)

    assert result == {"status": "ok", "timeout_ms": 100, "active_before": 2, "active_after": 0}
    assert app.process_events_count > 0


def test_runtime_health_background_task_diagnostic_failure_is_unavailable(monkeypatch):
    class _BrokenTaskManager:
        @property
        def active_count(self):
            raise RuntimeError("diagnostic failed")

    monkeypatch.setattr(runtime_suite, "task_manager", _BrokenTaskManager())

    assert runtime_suite._active_background_task_count() is None
    assert _wait_for_background_tasks_idle(object(), timeout_ms=100) == {
        "status": "unavailable",
        "timeout_ms": 100,
        "active_before": None,
        "active_after": None,
    }


def test_runtime_health_startup_settle_covers_delayed_asian_sync(monkeypatch):
    smart_id = runtime_suite.STARTUP_SMART.task_id
    asian_id = runtime_suite.STARTUP_ASIAN_DATA_SYNC.task_id
    active_snapshots = iter(((smart_id,), (), (asian_id,), ()))
    clocks = iter((0.005, 0.011, 0.012))
    monkeypatch.setattr(runtime_suite, "ASIAN_DATA_SYNC_START_DELAY_MS", 10)
    monkeypatch.setattr(runtime_suite, "_active_known_task_ids", lambda: next(active_snapshots))
    monkeypatch.setattr(runtime_suite.time, "perf_counter", lambda: next(clocks))

    def _wait_until(_app, predicate, **_kwargs):
        assert predicate() is False
        assert predicate() is False
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _wait_until)

    result = runtime_suite._wait_for_startup_tasks_idle(
        object(), suite_started_at=0.0, await_delayed_asian=True, timeout_ms=100
    )

    assert result["status"] == "ok"
    assert result["contaminated"] is False
    assert result["delay_horizon_ms"] == 10
    assert result["observed_task_ids"] == [asian_id, smart_id]
    assert result["remaining_task_ids"] == []


def test_runtime_health_post_tab_idle_waits_only_for_phase_owned_tasks(monkeypatch):
    asian_id = runtime_suite.STARTUP_ASIAN_DATA_SYNC.task_id
    baseline = (asian_id, "preexisting")
    active_snapshots = [baseline + ("tab_job",), baseline, baseline]

    def _active_ids():
        if len(active_snapshots) > 1:
            return active_snapshots.pop(0)
        return active_snapshots[0]

    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", _active_ids)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    observed = set(baseline) | {"tab_job"}

    result = _wait_for_background_tasks_idle(
        object(),
        timeout_ms=100,
        step_ms=1,
        baseline_task_ids=baseline,
        observed_task_ids=observed,
    )

    assert result["status"] == "ok"
    assert result["active_after"] == 2
    assert result["baseline_task_ids"] == ["asian_data_sync_bg", "preexisting"]
    assert result["started_task_ids"] == ["tab_job"]
    assert result["remaining_task_ids"] == []
    assert result["concurrent_startup_task_ids"] == [asian_id]


def test_runtime_health_collects_structured_post_close_state(monkeypatch):
    kline_shutdown = {
        "active_close_clean": True,
        "active_windows": 0,
        "managed_keepers": 0,
        "pending_open": False,
        "prewarm_main_window_retained": False,
        "clean": True,
    }

    class _ThreadPool:
        def activeThreadCount(self):
            return 3

    class _QThreadPool:
        @staticmethod
        def globalInstance():
            return _ThreadPool()

    preload_shutdown = {
        "active_key": "",
        "cancelling_key": "",
        "remaining_keys": [],
        "active_step_count": 0,
        "timer_active": False,
        "cancellation_blocked": False,
        "shutdown_cancel_receipts": [],
        "shutdown_cancellation_settled": True,
    }
    workspace = SimpleNamespace(background_preload_status=lambda: preload_shutdown)
    window = type(
        "Window",
        (),
        {
            "_process_watchdog": type("Watchdog", (), {"running": True})(),
            "_workspace": workspace,
        },
    )()
    monkeypatch.setattr(runtime_suite, "QThreadPool", _QThreadPool)
    monkeypatch.setattr(runtime_suite, "task_manager", type("TaskManager", (), {"active_count": 2})())
    monkeypatch.setattr(runtime_suite, "pending_thread_count", lambda: 4)
    monkeypatch.setattr(
        runtime_suite,
        "kline_manager",
        type("KlineManager", (), {"shutdown_diagnostics": kline_shutdown})(),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_suite,
        "collect_runtime_health",
        lambda _window, **_kwargs: {
            "webengine": {"available": True, "count": 1},
            "f5_refresh": {
                "job_controller_present": True,
                "job_controller_diagnostics_available": True,
                "job_controller_running": False,
            },
        },
    )
    f5_runtime_artifacts = {
        "clean": True,
        "active_snapshot_id": "a" * 32,
        "active_snapshot_complete": True,
    }
    monkeypatch.setattr(
        runtime_suite,
        "_f5_runtime_artifact_receipt",
        lambda _window: (f5_runtime_artifacts, True, ""),
    )

    assert runtime_suite._collect_post_close_state(window) == {
        "task_manager_diagnostics_available": True,
        "task_manager_active_count": 2,
        "qthread_pool_diagnostics_available": True,
        "qthread_pool_active_count": 3,
        "pending_qthread_diagnostics_available": True,
        "pending_qthread_count": 4,
        "watchdog_diagnostics_available": True,
        "watchdog_running": True,
        "workspace_background_preload_diagnostics_available": True,
        "workspace_background_preload": preload_shutdown,
        "f5_controller_present": True,
        "f5_controller_diagnostics_available": True,
        "f5_controller_running": False,
        "f5_runtime_artifacts_diagnostics_available": True,
        "f5_runtime_artifacts_diagnostics_error": "",
        "f5_runtime_artifacts": f5_runtime_artifacts,
        "webengine_available": True,
        "webengine_child_count": 1,
        "kline_manager_shutdown_diagnostics_available": True,
        "kline_manager_shutdown_diagnostics": kline_shutdown,
    }


def test_runtime_health_post_close_diagnostics_fail_closed(monkeypatch):
    class _Broken:
        def __getattr__(self, _name):
            raise RuntimeError("diagnostic failed")

    monkeypatch.setattr(runtime_suite, "task_manager", _Broken())
    monkeypatch.setattr(runtime_suite, "QThreadPool", _Broken())
    monkeypatch.setattr(runtime_suite, "pending_thread_count", lambda: "0")
    monkeypatch.setattr(runtime_suite, "collect_runtime_health", lambda _window, **_kwargs: {})
    monkeypatch.setattr(runtime_suite, "kline_manager", _Broken(), raising=False)

    state = runtime_suite._collect_post_close_state(
        type("Window", (), {"_process_watchdog": _Broken()})()
    )

    assert state["task_manager_diagnostics_available"] is False
    assert state["task_manager_active_count"] is None
    assert state["qthread_pool_diagnostics_available"] is False
    assert state["qthread_pool_active_count"] is None
    assert state["pending_qthread_diagnostics_available"] is False
    assert state["pending_qthread_count"] is None
    assert state["watchdog_diagnostics_available"] is False
    assert state["watchdog_running"] is None
    assert state["workspace_background_preload_diagnostics_available"] is False
    assert state["workspace_background_preload"] is None
    assert state["f5_controller_diagnostics_available"] is None
    assert state["f5_controller_running"] is None
    assert state["kline_manager_shutdown_diagnostics_available"] is False
    assert state["kline_manager_shutdown_diagnostics"] is None


def test_runtime_health_finalizer_samples_after_close_before_delete(monkeypatch):
    calls = []

    class _Window:
        def close(self):
            calls.append("close")

        def deleteLater(self):
            calls.append("deleteLater")

    times = iter((5.0, 5.025))
    monkeypatch.setattr(runtime_suite.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(runtime_suite, "_settle", lambda _app, timeout: calls.append(("settle", timeout)))
    monkeypatch.setattr(
        runtime_suite,
        "_process_events",
        lambda _app, **kwargs: calls.append(("events", kwargs)),
    )
    monkeypatch.setattr(
        runtime_suite,
        "_collect_post_close_state",
        lambda _window: calls.append("post_close")
        or {
            "task_manager_diagnostics_available": True,
            "task_manager_active_count": 0,
            "qthread_pool_diagnostics_available": True,
            "qthread_pool_active_count": 0,
            "pending_qthread_diagnostics_available": True,
            "pending_qthread_count": 0,
            "watchdog_diagnostics_available": True,
            "watchdog_running": False,
            "f5_controller_present": False,
            "f5_controller_diagnostics_available": True,
            "f5_controller_running": False,
            "webengine_available": True,
            "webengine_child_count": 0,
            "kline_manager_shutdown_diagnostics_available": True,
            "kline_manager_shutdown_diagnostics": {
                "active_close_clean": True,
                "active_windows": 0,
                "managed_keepers": 0,
                "pending_open": False,
                "prewarm_main_window_retained": False,
                "clean": True,
            },
        },
    )
    shutdown = runtime_suite._finalize_probe_window(_Window(), object())

    assert shutdown == {
        "close_elapsed_ms": 25.0,
        "pending_qthread_settle_ok": True,
        "post_close": {
            "task_manager_diagnostics_available": True,
            "task_manager_active_count": 0,
            "qthread_pool_diagnostics_available": True,
            "qthread_pool_active_count": 0,
            "pending_qthread_diagnostics_available": True,
            "pending_qthread_count": 0,
            "watchdog_diagnostics_available": True,
            "watchdog_running": False,
            "f5_controller_present": False,
            "f5_controller_diagnostics_available": True,
            "f5_controller_running": False,
            "webengine_available": True,
            "webengine_child_count": 0,
            "kline_manager_shutdown_diagnostics_available": True,
            "kline_manager_shutdown_diagnostics": {
                "active_close_clean": True,
                "active_windows": 0,
                "managed_keepers": 0,
                "pending_open": False,
                "prewarm_main_window_retained": False,
                "clean": True,
            },
        },
    }
    assert calls == [
        "close",
        ("settle", 200),
        ("events", {"flush_deferred_deletes": True}),
        "post_close",
        "deleteLater",
        ("settle", 100),
        ("events", {"flush_deferred_deletes": True}),
    ]


def test_runtime_health_suite_does_not_force_full_gc_on_gui_thread():
    source = (runtime_suite.PROJECT_ROOT / "scripts" / "runtime_health_stability_suite.py").read_text(
        encoding="utf-8"
    )

    assert "gc.collect(" not in source


def test_runtime_health_process_events_can_flush_deferred_deletes(monkeypatch):
    calls = []

    class _App:
        @staticmethod
        def processEvents():
            calls.append("process")

    class _CoreApplication:
        @staticmethod
        def sendPostedEvents(receiver, event_type):
            calls.append(("deferred", receiver, event_type))

    monkeypatch.setattr(runtime_suite, "QCoreApplication", _CoreApplication)

    runtime_suite._process_events(_App(), flush_deferred_deletes=True)

    assert calls == [
        "process",
        ("deferred", None, runtime_suite.QEvent.Type.DeferredDelete),
        "process",
    ]


def test_runtime_health_phase_boundary_settles_before_stall_reset(monkeypatch):
    calls = []
    times = iter((2.0, 2.125))
    monkeypatch.setattr(runtime_suite.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        runtime_suite,
        "_settle",
        lambda _app, settle_ms: calls.append(("settle", settle_ms)),
    )
    monkeypatch.setattr(
        runtime_suite,
        "_reset_ui_stall_snapshot",
        lambda: calls.append("reset") or True,
    )

    boundary = runtime_suite._begin_stall_phase(object(), phase="quote_cycle", settle_ms=120)

    assert calls == [("settle", 120), "reset"]
    assert boundary == {
        "phase": "quote_cycle",
        "settle_ms": 120,
        "elapsed_ms": 125.0,
        "stall_snapshot_reset": True,
    }


def test_runtime_health_kline_open_stall_capture_flushes_tail_tick(monkeypatch):
    calls = []
    probe = SimpleNamespace(
        timer_interval_ms=25,
        stall_snapshot=lambda: {
            "installed": True,
            "critical_count": 0,
            "event_loop_critical_count": 0,
            "max_elapsed_ms": 80.0,
        },
    )
    monkeypatch.setattr(runtime_suite, "get_ui_stall_probe", lambda: probe)
    monkeypatch.setattr(
        runtime_suite,
        "_settle",
        lambda _app, settle_ms: calls.append(("settle", settle_ms)),
    )

    snapshot = runtime_suite._capture_kline_open_ui_stalls(object(), reset_succeeded=True)

    assert calls == [("settle", 50)]
    assert snapshot["scope"] == "kline_open_to_chart_ready"
    assert snapshot["reset_succeeded"] is True
    assert snapshot["max_elapsed_ms"] == 80.0


def test_runtime_health_tab_cycle_and_async_tail_use_independent_stall_phases(monkeypatch):
    calls = []
    args = SimpleNamespace(tab_cycles=1, cycle_settle_ms=120, post_tab_idle_timeout_ms=5000)
    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ("baseline",))
    monkeypatch.setattr(
        runtime_suite,
        "_record_stall_phase",
        lambda _report, _app, _args, phase: calls.append(("phase", phase)),
    )

    def _cycle(*_args, observed_task_ids, **_kwargs):
        observed_task_ids.add("tab_job")
        calls.append(("cycle", tuple(sorted(observed_task_ids))))
        return {"status": "ok", "tabs": []}

    monkeypatch.setattr(runtime_suite, "_cycle_tabs", _cycle)
    monkeypatch.setattr(
        runtime_suite,
        "_take_suite_sample",
        lambda _window, _args, label, *_rest: calls.append(("sample", label)),
    )

    def _wait(*_args, **kwargs):
        calls.append(("wait", tuple(sorted(kwargs["observed_task_ids"]))))
        return {"status": "ok"}

    monkeypatch.setattr(runtime_suite, "_wait_for_background_tasks_idle", _wait)

    runtime_suite._run_tab_phase(object(), object(), args, ("watchlist",), {}, [], [])

    assert calls == [
        ("phase", "tab_cycle"),
        ("cycle", ("baseline", "tab_job")),
        ("sample", "after_tab_cycle"),
        ("phase", "tab_async_tail"),
        ("wait", ("baseline", "tab_job")),
        ("sample", "after_tab_async_tail"),
    ]


def test_runtime_health_wait_until_keeps_event_loop_alive(monkeypatch):
    state = {"settles": 0}

    def _settle(_app, _step_ms):
        state["settles"] += 1

    monkeypatch.setattr(runtime_suite, "_settle", _settle)
    monkeypatch.setattr(
        runtime_suite.time,
        "sleep",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GUI wait must not sleep")),
    )

    ready = runtime_suite._wait_until(
        object(),
        lambda: state["settles"] >= 2,
        timeout_ms=100,
        step_ms=10,
    )

    assert ready is True
    assert state["settles"] == 2


def test_runtime_health_sample_records_collect_and_write_cost(monkeypatch, tmp_path):
    samples = []
    exported_paths = []
    times = iter((4.0, 4.006, 4.018))
    monkeypatch.setattr(runtime_suite.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        runtime_suite,
        "collect_runtime_health",
        lambda _window, **_kwargs: {"ui_stalls": {"critical_count": 3}},
    )

    sample = runtime_suite._sample(
        object(),
        label="after_tab_cycle",
        samples=samples,
        exported_paths=exported_paths,
        export_each_sample=True,
        sample_output_dir=tmp_path,
    )

    assert sample["sample_collect_elapsed_ms"] == 6.0
    assert sample["sample_elapsed_ms"] == 18.0
    assert sample["sample_export_elapsed_ms"] == 12.0
    assert sample["ui_stalls"]["critical_count"] == 3
    assert samples == [sample]
    assert len(exported_paths) == 1
    sample_path = tmp_path / "runtime_health_sample_0001_after_tab_cycle.json"
    assert sample_path.exists()
    exported = json.loads(sample_path.read_text(encoding="utf-8"))
    assert exported["sample_collect_elapsed_ms"] == 6.0
    assert exported["sample_elapsed_ms"] == 18.0
    assert exported["sample_export_elapsed_ms"] == 12.0


def test_runtime_health_final_output_uses_atomic_json_repository(monkeypatch, tmp_path):
    calls = []
    output = tmp_path / "runtime_health.json"
    report = {"status": "ok", "runtime_health_samples": [{"label": "final"}]}
    monkeypatch.setattr(
        runtime_suite,
        "save_json_file",
        lambda path, payload: calls.append((path, payload)),
    )

    runtime_suite._save_final_report(output, report)

    assert calls == [(str(output), report)]


def test_runtime_health_faulthandler_uses_output_derived_file_and_keeps_handle_alive(
    monkeypatch, tmp_path
):
    calls = []
    evidence = runtime_suite._SuiteEvidence(tmp_path / "runtime_health.json")
    monkeypatch.setattr(
        runtime_suite.faulthandler,
        "enable",
        lambda *, file, all_threads: calls.append(("enable", file, all_threads)),
    )
    monkeypatch.setattr(runtime_suite.faulthandler, "disable", lambda: calls.append(("disable",)))

    file_obj = runtime_suite._enable_suite_faulthandler(evidence)

    assert evidence.faulthandler_path == tmp_path / "runtime_health.faulthandler.log"
    assert file_obj is not None and file_obj.closed is False
    assert calls == [("enable", file_obj, True)]
    runtime_suite._close_suite_faulthandler(file_obj)
    assert file_obj.closed is True
    assert calls[-1] == ("disable",)


def test_runtime_health_f5_timeout_propagates_to_top_level(monkeypatch):
    monkeypatch.setattr(runtime_suite, "finish_f5_reload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    window = type("Window", (), {"_workspace": object()})()

    result = runtime_suite._cycle_f5(window, object(), cycles=1, settle_ms=0)

    assert result["status"] == "timeout"
    assert result["cycles"] == 1
    assert result["cycle_timings"][0]["status"] == "timeout"


def test_runtime_health_finished_f5_failure_propagates_to_top_level(monkeypatch):
    monkeypatch.setattr(
        runtime_suite,
        "_cycle_real_f5_once",
        lambda *_args, **_kwargs: {"status": "failed", "finished": True, "elapsed_ms": 12.0},
    )

    result = runtime_suite._cycle_f5(
        object(),
        object(),
        cycles=1,
        settle_ms=0,
        real=True,
    )

    assert result["status"] == "failed"
    assert result["cycle_timings"][0]["finished"] is True


def test_runtime_health_real_f5_cycle_records_subprocess_and_snapshot_receipt(monkeypatch):
    result = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        run_id="a" * 32,
        requested_date="20260716",
        effective_trade_date="20260715",
        symbol_count=5122,
        rps_valid_count=5121,
        sector_count=377,
        elapsed_seconds=12.5,
        artifacts=SimpleNamespace(snapshot_id="a" * 32),
        error_code="",
        error_message="",
    )
    controller = SimpleNamespace(
        is_running=False,
        last_request=SimpleNamespace(job_dir="unused"),
        last_result=result,
        last_worker_pid=222,
        cancel=lambda _reason: None,
    )
    scheduler = SimpleNamespace(is_running=lambda: False)
    window = SimpleNamespace(
        _workspace=SimpleNamespace(_f5_refresh_scheduler=scheduler),
    )

    monkeypatch.setattr(runtime_suite, "start_f5_precompute", lambda target: setattr(target, "_f5_job_controller", controller))
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda _app, predicate, **_kwargs: predicate())
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_suite,
        "_f5_event_phases",
        lambda _controller: (["prepare", "market_sync", "market_stage", "rps", "sector_rps", "validate"], 7),
    )
    monkeypatch.setattr(runtime_suite.os, "getpid", lambda: 111)

    receipt = runtime_suite._cycle_f5(
        window,
        object(),
        cycles=1,
        settle_ms=0,
        real=True,
        timeout_ms=1000,
    )

    assert receipt["status"] == "ok"
    assert receipt["probe_mode"] == "real_process"
    timing = receipt["cycle_timings"][0]
    assert timing["worker_pid"] == 222
    assert timing["parent_pid"] == 111
    assert timing["snapshot_id"] == timing["run_id"]
    assert timing["event_count"] == 7


def test_runtime_health_real_f5_cli_contract():
    args = _parse_args(["--real-f5", "--real-f5-timeout-seconds", "42"])

    assert args.real_f5 is True
    assert args.real_f5_timeout_seconds == 42


def test_runtime_health_real_f5_timeout_requests_cooperative_cancel(monkeypatch):
    cancellations = []
    controller = SimpleNamespace(
        is_running=True,
        last_request=SimpleNamespace(job_dir="unused"),
        last_result=None,
        last_worker_pid=222,
        cancel=cancellations.append,
    )
    window = SimpleNamespace(_workspace=SimpleNamespace())
    waits = iter((True, False))

    monkeypatch.setattr(runtime_suite, "start_f5_precompute", lambda target: setattr(target, "_f5_job_controller", controller))
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: next(waits))
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_suite, "_f5_event_phases", lambda _controller: ([], 0))

    receipt = runtime_suite._cycle_f5(
        window,
        object(),
        cycles=1,
        settle_ms=0,
        real=True,
        timeout_ms=1000,
    )

    assert receipt["status"] == "timeout"
    assert receipt["cycle_timings"][0]["finished"] is False
    assert cancellations == ["runtime_probe_timeout"]


def test_runtime_health_quote_timeout_propagates_to_top_level(monkeypatch):
    fetch_calls = []
    central = type("Central", (), {"_trigger_fetch": lambda self: fetch_calls.append("fetch")})()
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    window = type("Window", (), {"central_quotes_svc": central})()

    result = runtime_suite._cycle_quotes(window, object(), cycles=1, settle_ms=0)

    assert fetch_calls == ["fetch"]
    assert result["status"] == "timeout"
    assert result["cycles"] == 1
    assert result["cycle_timings"][0]["status"] == "timeout"


def test_runtime_health_quote_cycle_ignores_unrelated_background_tasks(monkeypatch):
    fetch_calls = []
    central = SimpleNamespace(
        _trigger_fetch=lambda: fetch_calls.append("fetch"),
        _is_fetching=False,
        _off_market_snapshot_fetching=False,
        _pending_fetch_reason="",
        _task_lifecycle=SimpleNamespace(active_names=()),
    )
    monkeypatch.setattr(runtime_suite, "task_manager", SimpleNamespace(active_count=9))
    monkeypatch.setattr(
        runtime_suite,
        "_wait_until",
        lambda _app, predicate, **_kwargs: predicate(),
    )
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)

    result = runtime_suite._cycle_quotes(
        SimpleNamespace(central_quotes_svc=central),
        object(),
        cycles=1,
        settle_ms=0,
    )

    assert fetch_calls == ["fetch"]
    assert result["status"] == "ok"


def test_runtime_health_kline_close_only_counts_confirmed_closed_windows(monkeypatch):
    from ui.components.kline_window_manager import kline_manager

    class _RejectedChart:
        browser = object()

        @staticmethod
        def isVisible():
            return True

        @staticmethod
        def close():
            return False

    class _AcceptedChart:
        def __init__(self):
            self.browser = object()
            self.visible = True

        def isVisible(self):
            return self.visible

        def close(self):
            self.visible = False
            self.browser = None
            return True

    rejected = _RejectedChart()
    accepted = _AcceptedChart()
    kline_manager._charts = [rejected, accepted]
    monkeypatch.setattr(runtime_suite, "_process_events", lambda *_args, **_kwargs: None)

    try:
        assert runtime_suite._close_kline_charts(object()) == 1
        assert kline_manager._charts == [rejected]
        assert runtime_suite._kline_chart_ready(rejected) is False
        assert runtime_suite._kline_chart_closed(rejected) is False
        assert runtime_suite._kline_chart_closed(accepted) is True
    finally:
        kline_manager._charts = []


def test_runtime_health_kline_ready_requires_real_chart_stage():
    incomplete = SimpleNamespace(
        browser=object(),
        isVisible=lambda: True,
        _open_stages=SimpleNamespace(recorded_stages={"shell_ready", "browser_ready"}),
    )
    ready = SimpleNamespace(
        browser=object(),
        isVisible=lambda: True,
        _open_stages=SimpleNamespace(recorded_stages={"shell_ready", "browser_ready", "chart_ready"}),
    )

    assert runtime_suite._kline_chart_ready(incomplete) is False
    assert runtime_suite._kline_chart_ready(ready) is True


def test_runtime_health_kline_cycle_reports_six_stage_contract():
    from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER

    diagnostics = {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "completed_stages": list(KLINE_OPEN_STAGE_ORDER),
        "pending_stages": [],
        "timings_ms": {stage: float(index + 1) for index, stage in enumerate(KLINE_OPEN_STAGE_ORDER)},
        "observed_timings_ms": {stage: float(index + 1) for index, stage in enumerate(KLINE_OPEN_STAGE_ORDER)},
        "complete": True,
    }
    manager = SimpleNamespace(active_chart_view_count=0)
    result = runtime_suite._build_kline_cycle_result(
        1,
        {"requested": False, "status": "ok"},
        [
            {
                "cycle_index": 1,
                "opened": 1,
                "closed": 1,
                "blocked": 0,
                "samples": [],
                "stage_diagnostics": diagnostics,
                "ui_stalls": {
                    "installed": True,
                    "scope": "kline_open_to_chart_ready",
                    "reset_succeeded": True,
                    "critical_count": 0,
                    "event_loop_critical_count": 0,
                    "max_elapsed_ms": 80.0,
                },
            }
        ],
        manager,
    )

    assert result["status"] == "ok"
    assert result["open_success_criterion"] == "chart_ready"
    assert result["stage_contract"]["complete"] is True
    assert result["stage_contract"]["required_stages"] == list(KLINE_OPEN_STAGE_ORDER)
    assert result["open_ui_stalls"] == [
        {
            "cycle_index": 1,
            "ui_stalls": {
                "installed": True,
                "scope": "kline_open_to_chart_ready",
                "reset_succeeded": True,
                "critical_count": 0,
                "event_loop_critical_count": 0,
                "max_elapsed_ms": 80.0,
            },
        }
    ]


def test_runtime_health_kline_cycle_fails_closed_on_invalid_stage_timings():
    from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER

    timings = {stage: float(index + 1) for index, stage in enumerate(KLINE_OPEN_STAGE_ORDER)}
    timings["data_ready"] = 0.0
    diagnostics = {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "completed_stages": list(KLINE_OPEN_STAGE_ORDER),
        "pending_stages": [],
        "timings_ms": timings,
        "complete": True,
    }
    result = runtime_suite._build_kline_cycle_result(
        1,
        {"requested": False, "status": "ok"},
        [
            {
                "cycle_index": 1,
                "opened": 1,
                "closed": 1,
                "blocked": 0,
                "samples": [],
                "stage_diagnostics": diagnostics,
                "ui_stalls": {
                    "installed": True,
                    "scope": "kline_open_to_chart_ready",
                    "reset_succeeded": True,
                    "critical_count": 0,
                    "event_loop_critical_count": 0,
                    "max_elapsed_ms": 80.0,
                },
            }
        ],
        SimpleNamespace(active_chart_view_count=0),
    )

    assert result["status"] == "fail"
    assert result["stage_contract"]["complete"] is False


def test_runtime_health_suite_soak60_defaults_to_one_hour():
    args = _apply_mode_defaults(_parse_args(["--mode", "soak60"]))

    assert args.idle_seconds == 3600
    assert args.tab_cycles == 2
    assert args.f5_cycles == 2
    assert args.quote_cycles == 0
    assert args.kline_cycles == 1

    central_args = _apply_mode_defaults(_parse_args(["--mode", "soak60", "--central-quotes-enabled"]))
    assert central_args.quote_cycles == 2


def test_runtime_health_mode_defaults_preserve_explicit_cycle_counts():
    args = _apply_mode_defaults(
        _parse_args(
            [
                "--mode",
                "soak60",
                "--idle-seconds",
                "17",
                "--tab-cycles",
                "3",
                "--f5-cycles",
                "4",
                "--quote-cycles",
                "5",
                "--kline-cycles",
                "6",
            ]
        )
    )

    assert args.idle_seconds == 17
    assert args.tab_cycles == 3
    assert args.f5_cycles == 4
    assert args.quote_cycles == 5
    assert args.kline_cycles == 6


def _runtime_health_sample(label: str, *, receivers: int, timers: int = 5, threads: int = 20) -> dict:
    return {
        "label": label,
        "background_tasks": {"count": 0},
        "timers": {"active": timers, "total": timers + 4},
        "event_bus": {"total_receivers": receivers},
        "process": {"thread_count": threads, "rss_mb": 200.0},
        "webengine": {"count": 0, "rss_mb": 0.0, "private_mb": 0.0},
    }


def test_runtime_health_suite_budget_trend_uses_post_workload_tail():
    trend = _build_budget_trend(
        [
            _runtime_health_sample("startup", receivers=10, timers=4),
            _runtime_health_sample("idle:1s", receivers=10, timers=4),
            _runtime_health_sample("after_tab_cycle", receivers=22, timers=6),
            _runtime_health_sample("after_f5_cycle", receivers=22, timers=6),
            _runtime_health_sample("final", receivers=22, timers=6),
        ],
        None,
    )

    assert trend["event_receivers"]["net_delta"] == 0
    assert trend["event_receivers"]["basis"] == "tail_runtime_health_samples"
    assert trend["active_timers"]["net_delta"] == 0
    assert trend["rss_mb"]["range"] == 0.0
    assert trend["rss_mb"]["tail_range"] == 0.0
    assert trend["rss_mb"]["basis"] == "tail_runtime_health_samples"


def test_runtime_health_suite_fails_closed_on_unhandled_qt_callback(monkeypatch):
    monkeypatch.setattr(runtime_suite, "check_runtime_health_budget", lambda _report: [])
    monkeypatch.setattr(runtime_suite, "build_runtime_health_trend", lambda _samples: {})
    monkeypatch.setattr(runtime_suite, "_build_budget_trend", lambda *_args: {})
    monkeypatch.setattr(runtime_suite, "_build_startup_lazy_budget", lambda *_args: {})
    report = {
        "unhandled_ui_exceptions": [
            {
                "type": "TypeError",
                "message": "slot contract mismatch",
                "traceback": "TypeError: slot contract mismatch",
            }
        ]
    }

    result = runtime_suite._finish_suite_report(report, [], runtime_suite.time.perf_counter())

    assert result["status"] == "fail"
    assert result["budget"]["failures"] == [
        {
            "check": "runtime_health.unhandled_ui_exceptions",
            "detail": "Python exception escaped a Qt callback boundary",
            "actual": 1,
            "budget": 0,
        }
    ]


def test_runtime_health_suite_budget_trend_starts_after_intentional_kline_prewarm():
    trend = _build_budget_trend(
        [
            _runtime_health_sample("after_quote_cycle", receivers=22, timers=6, threads=20),
            _runtime_health_sample("after_kline_prewarm", receivers=22, timers=7, threads=96),
            _runtime_health_sample("final", receivers=22, timers=7, threads=96),
        ],
        {"cycles": 0},
    )

    assert trend["threads"]["net_delta"] == 0
    assert trend["active_timers"]["net_delta"] == 0
    assert trend["threads"]["basis"] == "tail_runtime_health_samples"


def test_runtime_health_suite_startup_lazy_budget_summarizes_key_timings():
    report = {
        "startup_ready_ms": 420.0,
        "initial_tab_ready_ms": 780.0,
        "startup_inclusive_first_paint_ms": 1420.0,
        "startup_inclusive_initial_tab_ready_ms": 1780.0,
        "startup_app_init_first_paint_ms": 1020.0,
        "startup_app_init_initial_tab_ready_ms": 1380.0,
        "startup_timing": {"scope": {"script_module_origin": "script_module_after_time_import"}},
        "mode": {
            "startup_settle_ms": 300,
            "startup_enabled": False,
            "background_prewarm": False,
            "cycle_settle_ms": 120,
        },
        "tab_cycle": {
            "tabs": [
                {"key": "scan", "status": "ok", "elapsed_ms": 150.0},
                {"key": "watchlist", "status": "ok", "elapsed_ms": 80.0},
                {"key": "scan", "status": "ok", "elapsed_ms": 40.0},
            ]
        },
        "f5_cycle": {
            "total_elapsed_ms": 220.0,
            "cycle_timings": [
                {"cycle": 1, "status": "ok", "elapsed_ms": 220.0},
            ],
        },
    }
    samples = [
        {
            "label": "final",
            "background_tasks": {"count": 0},
            "process": {"thread_count": 21},
            "timers": {"active": 6},
        }
    ]

    budget = _build_startup_lazy_budget(report, samples)

    assert budget["startup"]["main_window_ready_ms"] == 420.0
    assert budget["startup"]["inclusive_first_paint_ms"] == 1420.0
    assert budget["startup"]["inclusive_initial_tab_ready_ms"] == 1780.0
    assert budget["startup"]["app_init_first_paint_ms"] == 1020.0
    assert budget["startup"]["timing_scope"]["script_module_origin"] == "script_module_after_time_import"
    assert budget["tab_first_open"]["max_elapsed_ms"] == 150.0
    assert [item["key"] for item in budget["tab_first_open"]["tabs"]] == ["scan", "watchlist"]
    assert budget["f5_quiet"]["max_cycle_elapsed_ms"] == 220.0
    assert budget["background_settle"]["final_background_task_count"] == 0


def _completed_preload_fail_closed_fields(expected: list[str]) -> dict:
    return {
        "ready_keys": list(expected),
        "dependency_failures": {},
        "pending_priority_keys": [],
        "cancelling_key": "",
        "cancel_receipt": {},
        "cancellation_settlement_timeout_ms": 5_000,
        "cancellation_blocked_poll_interval_ms": 500,
        "cancellation_timeouts": {},
        "cancellation_timeout_keys": [],
        "cancellation_blocked": False,
        "blocked_reason": "",
        "active_step_count": 0,
        "timer_active": False,
        "shutdown_cancel_receipts": [],
        "shutdown_cancellation_settled": True,
    }


def test_runtime_health_waits_for_dependency_ordered_background_preload(monkeypatch):
    expected = list(startup_tab_keys())
    status = {
        "enabled": True,
        "started": True,
        "finished": False,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": expected,
        "completion_order": expected,
        "loaded_keys": list(reversed(expected)),
        "loaded_count": len(expected),
        "active_key": "watchlist",
        "remaining_keys": [],
        "failures": {},
        "timeouts": [],
        "max_concurrent_steps": 1,
    }
    status.update(_completed_preload_fail_closed_fields(expected))
    workspace = SimpleNamespace(background_preload_status=lambda: status)
    window = SimpleNamespace(_workspace=workspace)
    def _finish(_app, predicate, **_kwargs):
        status["finished"] = True
        status["active_key"] = ""
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _finish)
    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ())

    result = _wait_for_background_preload(window, object(), enabled=True, timeout_ms=1234)

    assert result["status"] == "ok"
    assert result["contract_ok"] is True
    assert result["expected_order"] == expected
    assert result["auto_refresh_task_diagnostics_available"] is True
    assert result["auto_refresh_task_ids_observed"] == []
    assert all(result["contract"].values())


def test_runtime_health_background_preload_fails_closed_on_wrong_order(monkeypatch):
    expected = list(startup_tab_keys())
    wrong_order = list(reversed(expected))
    status = {
        "enabled": True,
        "started": True,
        "finished": True,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": wrong_order,
        "completion_order": wrong_order,
        "loaded_keys": expected,
        "loaded_count": len(expected),
        "active_key": "",
        "remaining_keys": [],
        "failures": {},
        "timeouts": [],
        "max_concurrent_steps": 1,
    }
    status.update(_completed_preload_fail_closed_fields(expected))
    workspace = SimpleNamespace(background_preload_status=lambda: status)
    window = SimpleNamespace(_workspace=workspace)
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda _app, predicate, **_kwargs: predicate())
    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ())

    result = _wait_for_background_preload(window, object(), enabled=True, timeout_ms=1234)

    assert result["status"] == "failed"
    assert result["contract_ok"] is False
    assert result["contract"]["start_order_matches_plan"] is False


def test_runtime_health_background_preload_records_auto_refresh_overlap(monkeypatch):
    expected = list(startup_tab_keys())
    status = {
        "enabled": True,
        "started": True,
        "finished": False,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": expected,
        "completion_order": expected,
        "loaded_keys": expected,
        "loaded_count": len(expected),
        "active_key": "watchlist",
        "remaining_keys": [],
        "failures": {},
        "timeouts": [],
        "max_concurrent_steps": 1,
    }
    status.update(_completed_preload_fail_closed_fields(expected))
    workspace = SimpleNamespace(background_preload_status=lambda: status)
    window = SimpleNamespace(_workspace=workspace)
    def _finish(_app, predicate, **_kwargs):
        status["finished"] = True
        status["active_key"] = ""
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _finish)
    monkeypatch.setattr(
        runtime_suite,
        "_active_background_task_ids",
        lambda: ("auto_refresh_earnings_startup_gap_fill", "tab_watchlist_vcp"),
    )

    result = _wait_for_background_preload(window, object(), enabled=True, timeout_ms=1234)

    assert result["status"] == "failed"
    assert result["contract_ok"] is False
    assert result["contract"]["no_auto_refresh_tasks"] is False
    assert result["auto_refresh_task_diagnostics_available"] is True
    assert result["auto_refresh_task_ids_observed"] == ["auto_refresh_earnings_startup_gap_fill"]


def test_runtime_health_background_preload_observer_keeps_early_task_overlap(monkeypatch):
    expected = list(startup_tab_keys())
    finished = [False]
    status = {
        "enabled": True,
        "started": True,
        "finished": False,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": expected,
        "completion_order": expected,
        "loaded_keys": expected,
        "loaded_count": len(expected),
        "active_key": "watchlist",
        "remaining_keys": [],
        "failures": {},
        "timeouts": [],
        "max_concurrent_steps": 1,
    }
    status.update(_completed_preload_fail_closed_fields(expected))

    def _status():
        status["finished"] = finished[0]
        status["active_key"] = "" if finished[0] else "watchlist"
        return status

    workspace = SimpleNamespace(background_preload_status=_status)
    window = SimpleNamespace(_workspace=workspace)
    active_task_ids = ["auto_refresh_earnings_routine_0830"]
    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: tuple(active_task_ids))
    observer = runtime_suite._BackgroundPreloadTaskObserver(workspace)

    observer.poll()
    active_task_ids.clear()
    finished[0] = True
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda _app, predicate, **_kwargs: predicate())

    result = _wait_for_background_preload(
        window,
        object(),
        enabled=True,
        timeout_ms=1234,
        task_observer=observer,
    )

    assert result["status"] == "failed"
    assert result["preload_task_window_observed"] is True
    assert result["auto_refresh_task_ids_observed"] == ["auto_refresh_earnings_routine_0830"]


def test_runtime_health_background_preload_observer_rejects_new_startup_and_network_tasks(monkeypatch):
    expected = list(startup_tab_keys())
    status = {
        "enabled": True,
        "started": True,
        "finished": False,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": expected,
        "completion_order": expected,
        "loaded_keys": expected,
        "loaded_count": len(expected),
        "active_key": "watchlist",
        "remaining_keys": [],
        "failures": {},
        "timeouts": [],
        "max_concurrent_steps": 1,
    }
    status.update(_completed_preload_fail_closed_fields(expected))
    workspace = SimpleNamespace(background_preload_status=lambda: status)
    startup_id = "test_hidden_preload_startup_task"
    network_id = "test_hidden_preload_network_task"
    runtime_suite.task_registry.startup("cn_trade_calendar_refresh")
    runtime_suite.task_registry.startup(startup_id)
    runtime_suite.task_registry.network(network_id)
    monkeypatch.setattr(
        runtime_suite,
        "_active_background_task_ids",
        lambda: ("cn_trade_calendar_refresh", startup_id, network_id),
    )

    def _finish(_app, predicate, **_kwargs):
        assert predicate() is False
        status["finished"] = True
        status["active_key"] = ""
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _finish)

    result = _wait_for_background_preload(
        SimpleNamespace(_workspace=workspace),
        object(),
        enabled=True,
        timeout_ms=1234,
    )

    assert result["status"] == "failed"
    assert result["contract"]["no_new_startup_or_network_tasks"] is False
    assert result["startup_network_task_ids_observed"] == [
        "cn_trade_calendar_refresh",
        network_id,
        startup_id,
    ]
    assert result["startup_network_task_categories"] == {
        "cn_trade_calendar_refresh": "startup",
        network_id: "network",
        startup_id: "startup",
    }


def test_runtime_health_tab_cycle_uses_production_shell_navigation_path():
    calls = []
    loaded = {}

    class _App:
        def processEvents(self):
            return None

    class _Tabs:
        def setCurrentIndex(self, index):
            calls.append(("setCurrentIndex", index))

    class _Workspace:
        tabs = _Tabs()

        def tab_specs(self):
            return [{"key": "foreign_block"}]

        def get_loaded_tab(self, key):
            return loaded.get(key)

        def activate_tab(self, index, reason="user"):
            key = self.tab_specs()[index]["key"]
            tab = type("Tab", (), {})()
            tab._workspace_load_reason = reason
            tab._workspace_noninteractive_loaded = reason not in {
                "placeholder_action",
                "shell_nav",
                "tab_switch",
                "user",
            }
            loaded[key] = tab
            calls.append(("activate_tab", index, reason))
            return True

    result = _cycle_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        _App(),
        ("foreign_block",),
        cycles=1,
        settle_ms=0,
    )

    assert result["status"] == "ok"
    assert calls == [("activate_tab", 0, "shell_nav")]
    assert loaded["foreign_block"]._workspace_noninteractive_loaded is False
    assert result["tabs"][0]["loaded_before"] is False
    assert result["tabs"][0]["loaded_after"] is True
    assert result["tabs"][0]["settle_ms"] == 0


def test_runtime_health_tab_checkpoint_keeps_last_good_when_next_tab_is_interrupted(
    monkeypatch, tmp_path
):
    output = tmp_path / "runtime_health.json"
    evidence = runtime_suite._SuiteEvidence(output)
    sample_paths = [str(tmp_path / "runtime_health_sample_0001_startup.json")]
    report = {"window_visibility": {"required": True, "status": "ok"}}
    evidence.bind(report, ("first", "second"), 1, sample_paths)
    evidence.phase_start("tab_cycle")
    loaded = {"first": object()}

    class _Workspace:
        tabs = type("Tabs", (), {"setCurrentIndex": lambda *_args: None})()

        @staticmethod
        def tab_specs():
            return [{"key": "first"}, {"key": "second"}]

        @staticmethod
        def get_loaded_tab(key):
            return loaded.get(key)

        @staticmethod
        def activate_tab(index, reason="user"):
            assert reason == "shell_nav"
            if index == 1:
                raise RuntimeError("second tab interrupted")
            return True

    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ())
    monkeypatch.setattr(
        runtime_suite,
        "_wait_until",
        lambda _app, predicate, **_kwargs: predicate(),
    )
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)

    try:
        _cycle_tabs(
            type("Window", (), {"_workspace": _Workspace()})(),
            object(),
            ("first", "second"),
            cycles=1,
            settle_ms=0,
            evidence=evidence,
        )
    except RuntimeError as exc:
        assert str(exc) == "second tab interrupted"
    else:
        raise AssertionError("second tab interruption must propagate")

    checkpoint_path = tmp_path / "runtime_health.checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["event"] == "error"
    assert checkpoint["status"] == "error"
    assert checkpoint["current_phase"] == "tab_cycle"
    assert checkpoint["current_tab"] == "second"
    assert checkpoint["tab_progress"]["completed"] == 1
    assert checkpoint["tab_progress"]["last_good"] == "first"
    assert checkpoint["visibility"] == report["window_visibility"]
    assert checkpoint["sample_paths"] == sample_paths


def test_runtime_health_tab_cycle_waits_for_explicit_async_readiness(monkeypatch):
    loaded = {}

    class _Tab:
        _initial_data_loading = True

    tab = _Tab()

    class _Workspace:
        tabs = type("Tabs", (), {"setCurrentIndex": lambda *_args: None})()

        @staticmethod
        def tab_specs():
            return [{"key": "watchlist"}]

        @staticmethod
        def get_loaded_tab(key):
            return loaded.get(key)

        @staticmethod
        def activate_tab(_index, reason="user"):
            assert reason == "shell_nav"
            loaded["watchlist"] = tab
            return True

    def _wait_until(_app, predicate, **_kwargs):
        assert predicate() is False
        tab._initial_data_loading = False
        return predicate()

    monkeypatch.setattr(runtime_suite, "_wait_until", _wait_until)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)

    result = _cycle_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        object(),
        ("watchlist",),
        cycles=1,
        settle_ms=0,
    )

    assert result["status"] == "ok"
    assert result["tabs"][0]["runtime_probe_ready"] is True


def test_runtime_health_tab_interaction_to_stable_includes_actual_settle(monkeypatch):
    tab = object()

    class _Workspace:
        tabs = type("Tabs", (), {"setCurrentIndex": lambda *_args: None})()

        @staticmethod
        def tab_specs():
            return [{"key": "watchlist"}]

        @staticmethod
        def get_loaded_tab(_key):
            return tab

        @staticmethod
        def activate_tab(_index, reason="user"):
            assert reason == "shell_nav"
            return True

    clocks = iter((1.0, 1.01, 1.02, 1.15))
    monkeypatch.setattr(runtime_suite.time, "perf_counter", lambda: next(clocks))
    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ())
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_suite,
        "_begin_stall_phase",
        lambda _app, **kwargs: {"stall_snapshot_reset": True, **kwargs},
    )
    monkeypatch.setattr(
        runtime_suite,
        "_capture_scoped_ui_stalls",
        lambda _app, **kwargs: {"installed": False, **kwargs},
    )
    monkeypatch.setattr(runtime_suite, "metric_history", lambda: [])

    result = _cycle_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        object(),
        ("watchlist",),
        cycles=1,
        settle_ms=120,
    )

    timing = result["tabs"][0]
    assert timing["elapsed_ms"] == 20.0
    assert timing["interaction_to_stable_ms"] == 150.0
    assert timing["settle_elapsed_ms"] == 130.0


def test_runtime_health_tab_cycle_records_scored_transition_receipts(monkeypatch):
    calls = []
    loaded = {key: object() for key in ("watchlist", "asian_market", "na_daily")}
    metric_samples = []

    class _Tabs:
        def __init__(self):
            self.current_index = 0

        def currentIndex(self):
            return self.current_index

        def count(self):
            return 11

    class _Workspace:
        tabs = _Tabs()

        @staticmethod
        def tab_specs():
            return [
                {"key": "watchlist", "loaded": True, "mounted": True},
                {"key": "asian_market", "loaded": True, "mounted": True},
                {"key": "na_daily", "loaded": True, "mounted": True},
                *({"key": f"extra_{index}", "loaded": True, "mounted": True} for index in range(8)),
            ]

        @staticmethod
        def get_loaded_tab(key):
            return loaded.get(key)

        def activate_tab(self, index, reason="user"):
            assert reason == "shell_nav"
            self.tabs.current_index = index
            calls.append(index)
            if index == 1:
                metric_samples.extend(
                    [
                        SimpleNamespace(
                            name="tab_transition_snapshot_skipped",
                            value=1.0,
                            unit="count",
                            tags={"source": "watchlist", "target": "asian_market"},
                        ),
                        SimpleNamespace(
                            name="tab_transition_stage_ms",
                            value=3.0,
                            unit="ms",
                            tags={"stage": "reveal_or_mount", "target_tab": "asian_market"},
                        ),
                        SimpleNamespace(
                            name="ui_method_stall_ms",
                            value=120.0,
                            unit="ms",
                            tags={
                                "severity": "critical",
                                "source_tab": "watchlist",
                                "target_tab": "asian_market",
                                "reason": "shell_nav",
                                "transition_phase": "paint",
                            },
                        ),
                        SimpleNamespace(
                            name="ui_event_loop_stall_ms",
                            value=80.0,
                            unit="ms",
                            tags={"severity": "warn"},
                        ),
                    ]
                )
            return True

        @staticmethod
        def background_preload_status():
            return {"enabled": True, "started": True, "finished": True, "active_key": "", "active_step_count": 0}

    monkeypatch.setattr(runtime_suite, "_active_background_task_ids", lambda: ())
    monkeypatch.setattr(runtime_suite, "_wait_until", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime_suite, "_settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_suite,
        "_begin_stall_phase",
        lambda _app, **kwargs: {"stall_snapshot_reset": True, **kwargs},
    )
    monkeypatch.setattr(
        runtime_suite,
        "_capture_scoped_ui_stalls",
        lambda _app, **kwargs: {"installed": True, **kwargs},
    )
    monkeypatch.setattr(runtime_suite, "metric_history", lambda: list(metric_samples))

    result = _cycle_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        object(),
        ("watchlist", "asian_market", "na_daily"),
        cycles=1,
        settle_ms=0,
    )

    assert calls == [0, 1, 2]
    first, watch_to_asian, asian_to_na = result["tabs"]
    assert first["transition_receipt"]["scored"] is False
    assert watch_to_asian["transition_receipt"]["source_tab"] == "watchlist"
    assert watch_to_asian["transition_receipt"]["target_tab"] == "asian_market"
    assert watch_to_asian["transition_receipt"]["scored"] is True
    assert watch_to_asian["transition_receipt"]["snapshot_metrics"] == {
        "status": "observed",
        "captured": [],
        "skipped": [
            {
                "name": "tab_transition_snapshot_skipped",
                "value": 1.0,
                "unit": "count",
                "tags": {"source": "watchlist", "target": "asian_market"},
            }
        ],
    }
    assert watch_to_asian["transition_receipt"]["stage_metrics"] == [
        {
            "name": "tab_transition_stage_ms",
            "value": 3.0,
            "unit": "ms",
            "tags": {"stage": "reveal_or_mount", "target_tab": "asian_market"},
        }
    ]
    assert watch_to_asian["transition_receipt"]["attributed_stalls"] == {
        "count": 1,
        "critical_count": 1,
        "method_count": 1,
        "event_loop_count": 0,
        "max_elapsed_ms": 120.0,
        "samples": [
            {
                "name": "ui_method_stall_ms",
                "value": 120.0,
                "unit": "ms",
                "tags": {
                    "severity": "critical",
                    "source_tab": "watchlist",
                    "target_tab": "asian_market",
                    "reason": "shell_nav",
                    "transition_phase": "paint",
                },
            }
        ],
    }
    assert watch_to_asian["transition_receipt"]["unattributed_stalls"] == {
        "count": 1,
        "critical_count": 0,
        "method_count": 0,
        "event_loop_count": 1,
        "max_elapsed_ms": 80.0,
        "samples": [
            {
                "name": "ui_event_loop_stall_ms",
                "value": 80.0,
                "unit": "ms",
                "tags": {"severity": "warn"},
            }
        ],
    }
    assert asian_to_na["transition_receipt"]["snapshot_metrics"]["status"] == "not_observed"
    assert asian_to_na["transition_receipt"]["source_tab"] == "asian_market"
    assert asian_to_na["transition_receipt"]["target_tab"] == "na_daily"
    assert asian_to_na["transition_receipt"]["tab_count_before"] == 11
    assert asian_to_na["transition_receipt"]["tab_count_after"] == 11
    assert asian_to_na["transition_receipt"]["topology_unchanged"] is True


def test_transition_preload_snapshot_distinguishes_global_ready_from_cold_target():
    snapshot = runtime_suite._transition_preload_snapshot(
        {
            "enabled": True,
            "started": True,
            "finished": True,
            "active_key": "",
            "ready_keys": ["watchlist"],
            "loaded_keys": ["watchlist"],
            "pending_priority_keys": [],
            "active_step_count": 0,
        },
        target_key="asian_market",
        target_spec={"loaded": False, "mounted": False},
    )

    assert snapshot["target_preload_state"] == "cold"
    assert snapshot["target_in_ready_keys"] is False
    assert snapshot["target_in_loaded_keys"] is False


def test_runtime_health_tab_cycle_skips_controlled_probe_tabs():
    calls = []
    loaded = {}

    class _App:
        def processEvents(self):
            return None

    class _Tabs:
        def setCurrentIndex(self, index):
            calls.append(("setCurrentIndex", index))

    class _Workspace:
        tabs = _Tabs()

        def tab_specs(self):
            return [{"key": "fund_holdings"}, {"key": "watchlist"}]

        def get_loaded_tab(self, key):
            return loaded.get(key)

        def should_defer_probe_tab_load(self, key, *, reason="perf_memory_probe"):
            return key == "fund_holdings" and reason == "perf_memory_probe"

        def activate_tab(self, index, reason="user"):
            key = self.tab_specs()[index]["key"]
            loaded[key] = object()
            calls.append(("activate_tab", index, reason))
            return True

    result = _cycle_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        _App(),
        ("fund_holdings", "watchlist"),
        cycles=1,
        settle_ms=0,
    )

    assert result["tabs"][0]["status"] == "skipped_controlled_probe"
    assert result["tabs"][0]["key"] == "fund_holdings"
    assert ("activate_tab", 0, "shell_nav") not in calls
    assert ("setCurrentIndex", 0) not in calls
    assert ("activate_tab", 1, "shell_nav") in calls


def test_perf_memory_probe_load_tabs_skips_controlled_probe_tabs(monkeypatch):
    calls = []

    class _App:
        def processEvents(self):
            return None

    class _Workspace:
        def tab_specs(self):
            return [{"key": "watchlist"}, {"key": "fund_holdings"}]

        def ensure_tab_loaded(self, key, reason="user"):
            calls.append(("ensure_tab_loaded", key, reason))

        def should_defer_probe_tab_load(self, key, *, reason="perf_memory_probe"):
            return key == "fund_holdings" and reason == "perf_memory_probe"

    monkeypatch.setattr(
        perf_memory_probe,
        "collect_process_snapshot",
        lambda label="": {
            "label": label,
            "main": {"rss_mb": 100.0, "private_mb": 200.0, "vms_mb": 300.0},
        },
    )

    result = perf_memory_probe._load_workspace_tabs(
        type("Window", (), {"_workspace": _Workspace()})(),
        _App(),
    )

    assert calls == [("ensure_tab_loaded", "watchlist", "perf_memory_probe")]
    assert result[0]["result"] == {"key": "watchlist"}
    assert result[1]["status"] == "skipped_controlled_probe"
    assert result[1]["result"]["key"] == "fund_holdings"
