from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, QRect

from scripts import native_watchlist_profile
from scripts.native_watchlist_profile import (
    _background_prewarm_acceptance,
    _build_synthetic_quote_payload,
    _event_dispatcher_summary,
    _FirstPaintProbe,
    _foreground_watchlist_hold_acceptance,
    _foreground_watchlist_hold_remaining_ms,
    _native_platform_error,
    _NativeProfileController,
    _parse_args,
    _quote_repaint_acceptance,
    _reset_to_first_paint_delays,
    _residual_repaint_acceptance,
    _shell_nav_repaint_acceptance,
    _summarize_membership_reconcile_metrics,
    _summarize_model_signal_events,
    _summarize_paint_delay_metrics,
    _summarize_residual_repaint_metrics,
    _summarize_shell_nav_guard_metrics,
    _summarize_shell_nav_paint_metrics,
    _watchlist_reveal_acceptance,
    summarize_durations,
)


def test_native_watchlist_profile_rejects_non_native_qt_plugins():
    assert "not a native desktop platform" in _native_platform_error(
        requested="offscreen",
        actual="offscreen",
        system="win32",
    )
    assert _native_platform_error(requested="", actual="windows", system="win32") == ""


def test_native_watchlist_profile_separates_dispatch_work_from_sleep():
    summary = _event_dispatcher_summary(
        [
            {"kind": "active_dispatch", "phase": "watchlist_activation", "elapsed_ms": 144.0},
            {"kind": "blocked_wait", "phase": "watchlist_activation", "elapsed_ms": 612.0},
            {"kind": "active_dispatch", "phase": "watchlist_settle", "elapsed_ms": 12.0},
        ]
    )

    activation = summary["phases"]["watchlist_activation"]
    assert activation["active_dispatch"]["max_ms"] == 144.0
    assert activation["blocked_wait"]["max_ms"] == 612.0
    assert summary["largest_active_dispatch_segments"][0]["elapsed_ms"] == 144.0


def test_native_watchlist_profile_duration_summary_reports_tail_thresholds():
    summary = summarize_durations([1.0, 50.0, 100.0, 200.0])

    assert summary["count"] == 4
    assert summary["max_ms"] == 200.0
    assert summary["over_50ms"] == 3
    assert summary["over_100ms"] == 2


def test_native_watchlist_profile_cli_has_bounded_default_sampling_window():
    args = _parse_args([])

    assert args.warmup_ms == 500
    assert args.settle_ms == 3500
    assert args.load_timeout_ms == 8000
    assert args.heartbeat_ms == 25
    assert args.background_prewarm is False
    assert args.restore_last_tab is False
    assert args.prewarm_timeout_ms == 60_000
    assert args.quote_cycles == 0
    assert args.quote_cycle_ms == 1000
    assert args.quote_target_count == 6
    assert args.shell_nav_cycles == 0
    assert args.shell_nav_settle_ms == 1200
    assert args.shell_nav_source_tab == ""
    assert args.shell_nav_only is False
    assert args.membership_delta_probe is False
    assert args.disable_market_pulse is False
    assert args.question_dialog_ms == 0
    assert args.residual_repaint_cycles == 0
    assert args.legacy_quote_repaint is False
    assert args.no_cprofile is False


def test_native_watchlist_profile_cli_accepts_shell_nav_sampling_options():
    args = _parse_args(
        [
            "--shell-nav-cycles",
            "2",
            "--shell-nav-settle-ms",
            "1600",
            "--shell-nav-source-tab",
            "stock_candidates",
            "--shell-nav-only",
            "--membership-delta-probe",
            "--disable-market-pulse",
        ]
    )

    assert args.shell_nav_cycles == 2
    assert args.shell_nav_settle_ms == 1600
    assert args.shell_nav_source_tab == "stock_candidates"
    assert args.shell_nav_only is True
    assert args.membership_delta_probe is True
    assert args.disable_market_pulse is True


def test_native_watchlist_profile_cli_accepts_restore_last_tab_reveal_probe():
    args = _parse_args(["--background-prewarm", "--restore-last-tab"])

    assert args.background_prewarm is True
    assert args.restore_last_tab is True


@pytest.mark.parametrize(
    ("restore_last_tab", "expected_activation", "expected_restore"),
    [(False, [(0, "user")], []), (True, [], [(0, 0)])],
)
def test_native_watchlist_profile_activation_uses_requested_workspace_path(
    monkeypatch, restore_last_tab, expected_activation, expected_restore
):
    import infra.diagnostics.ui_stall_probe as stall_probe_module

    monkeypatch.setattr(stall_probe_module, "get_ui_stall_probe", lambda: None)
    activation_calls = []
    restore_calls = []
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "watchlist"}],
        activate_tab=lambda index, *, reason: activation_calls.append((index, reason)) or True,
        schedule_restore_last_tab=lambda index, *, delay_ms: restore_calls.append((index, delay_ms)),
    )
    controller = object.__new__(_NativeProfileController)
    controller._done = False
    controller.window = SimpleNamespace(_workspace=workspace)
    controller.args = SimpleNamespace(
        background_prewarm=False,
        restore_last_tab=restore_last_tab,
    )
    controller.cprofile_enabled = False
    controller.report = {"timings": {}}
    controller.paint_probe = SimpleNamespace(mark_activation=lambda _started: None)
    controller._set_phase = lambda _phase: None
    controller._fail = pytest.fail
    controller._load_poll = SimpleNamespace(start=lambda: None)

    _NativeProfileController._activate_watchlist(controller)

    assert activation_calls == expected_activation
    assert restore_calls == expected_restore


def test_native_watchlist_profile_shell_nav_only_requires_a_shell_nav_cycle():
    with pytest.raises(SystemExit):
        _parse_args(["--shell-nav-only"])


def test_native_watchlist_profile_resolves_same_group_source_with_production_index_activation():
    activation_calls = []
    workspace = SimpleNamespace(
        tab_specs=lambda: [
            {"key": "watchlist"},
            {"key": "stock_candidates"},
            {"key": "lhb"},
        ],
        tab_indices_by_group=lambda: {"主工作台": [0, 1, 2]},
    )
    nav = SimpleNamespace(
        _group_to_indices={"主工作台": [0, 1, 2]},
        _activate_workspace_index=lambda index, *, reason: activation_calls.append((index, reason)),
    )
    controller = object.__new__(_NativeProfileController)
    controller.window = SimpleNamespace(_workspace=workspace, _shell_navigation_widget=nav)
    controller.args = SimpleNamespace(shell_nav_source_tab="stock_candidates")

    targets = controller._resolve_shell_nav_targets()

    assert targets is not None
    assert targets["activation_path"] == "ShellNavigationWidget._activate_workspace_index"
    assert targets["outbound_index"] == 1
    assert targets["watchlist_index"] == 0
    assert targets["source_tab"] == "stock_candidates"
    assert targets["activate_workspace_index"] is nav._activate_workspace_index
    assert activation_calls == []


def test_native_watchlist_profile_builds_sparse_changed_quote_payload():
    rows = [{"代码": f"0000{index:02d}", "现价": str(10 + index)} for index in range(41)]

    payload = _build_synthetic_quote_payload(rows, cycle=1, target_count=6)

    assert len(payload) == 6
    assert list(payload) == ["000000", "000008", "000016", "000024", "000032", "000040"]
    assert all(quote["close"] > quote["pre_close"] for quote in payload.values())


def test_native_watchlist_profile_summarizes_residual_repaint_structure():
    summary = _summarize_residual_repaint_metrics(
        {
            "watchlist_model_update_ms": [
                SimpleNamespace(
                    value=2.5,
                    tags={"changed_headers": "名称", "mode": "direct", "reason": "name_refresh"},
                )
            ],
            "watchlist_table_paint_ms": [
                SimpleNamespace(
                    value=8.0,
                    tags={
                        "dirty_bounding_area_ratio": "0.1250",
                        "delivered_full_viewport": "false",
                    },
                ),
                SimpleNamespace(
                    value=80.0,
                    tags={
                        "dirty_bounding_area_ratio": "1.0000",
                        "delivered_full_viewport": "true",
                    },
                ),
            ],
            "tab_transition_snapshot_ms": [],
            "tab_transition_snapshot_skipped": [
                SimpleNamespace(value=1.0, tags={"source": "watchlist", "target": "lhb"})
            ],
        }
    )

    assert summary["model_updates"][0]["mode"] == "direct"
    assert summary["paint"]["full_viewport_count"] == 1
    assert summary["paint"]["max_dirty_bounding_area_ratio"] == 1.0
    assert summary["paint"]["first"]["dirty_bounding_area_ratio"] == 0.125
    assert summary["paint"]["first"]["delivered_full_viewport"] is False
    assert summary["paint"]["after_first"]["full_viewport_count"] == 1
    assert summary["snapshot"]["capture_count"] == 0
    assert summary["snapshot"]["skipped_count"] == 1


def test_native_watchlist_profile_background_prewarm_acceptance_requires_all_hidden_staged_tabs_and_zero_hidden_full_paints():
    planned_order = ["watchlist", *[f"tab-{index}" for index in range(10)]]
    staged_keys = planned_order[1:]
    status = {
        "finished": True,
        "planned_order": planned_order,
        "planned_count": 11,
        "start_order": planned_order,
        "completion_order": planned_order,
        "completion_scope": "all_planned",
        "visible_watchlist_state": "ready",
        "visible_watchlist_at": 1.0,
        "visible_watchlist_detail": "",
        "startup_lazy_handoff_keys": [],
        "ready_keys": planned_order,
        "failures": {},
    }
    paint_region = {
        "full_viewport_count": 0,
        "after_first": {"full_viewport_count": 0},
    }

    assert _background_prewarm_acceptance(status, paint_region) == {
        "status": "pass",
        "violations": [],
    }
    assert _background_prewarm_acceptance(
        status,
        paint_region,
        tab_count=11,
        mounted_keys=["watchlist"],
        staged_keys=staged_keys,
        lazy_keys=[],
    )["status"] == "pass"
    assert _background_prewarm_acceptance(
        status,
        paint_region,
        tab_count=11,
        mounted_keys=["watchlist", "tab-0"],
        staged_keys=staged_keys[1:],
        lazy_keys=[],
        foreground_mounted_key="tab-0",
    )["status"] == "pass"
    priority_status = dict(status)
    priority_order = ["watchlist", "tab-1", "tab-0", *staged_keys[2:]]
    priority_status.update(start_order=priority_order, completion_order=priority_order)
    assert _background_prewarm_acceptance(
        priority_status,
        paint_region,
        tab_count=11,
        mounted_keys=["watchlist", "tab-1"],
        staged_keys=["tab-0", *staged_keys[2:]],
        lazy_keys=[],
        foreground_mounted_key="tab-1",
    )["status"] == "pass"

    topology_failure = _background_prewarm_acceptance(
        status,
        paint_region,
        tab_count=10,
        mounted_keys=["watchlist", "tab-0"],
        staged_keys=staged_keys[1:],
        lazy_keys=["tab-0"],
    )
    assert topology_failure["status"] == "fail"
    assert any(item.startswith("tab_count=") for item in topology_failure["violations"])
    assert any(item.startswith("mounted_keys=") for item in topology_failure["violations"])
    assert any(item.startswith("staged_keys=") for item in topology_failure["violations"])
    assert any(item.startswith("lazy_keys=") for item in topology_failure["violations"])

    invalid_status = dict(status)
    invalid_status.update(
        start_order=["watchlist", "tab-0"],
        completion_order=["watchlist", "tab-0"],
        completion_scope="visible_watchlist_ready",
        visible_watchlist_state="pending",
        startup_lazy_handoff_keys=["tab-0"],
        ready_keys=planned_order[:-1],
    )
    contract_failure = _background_prewarm_acceptance(invalid_status, paint_region)
    assert contract_failure["status"] == "fail"
    assert any(item.startswith("start_order=") for item in contract_failure["violations"])
    assert any(item.startswith("completion_order=") for item in contract_failure["violations"])
    assert any(item.startswith("completion_scope=") for item in contract_failure["violations"])
    assert any(item.startswith("visible_watchlist_state=") for item in contract_failure["violations"])
    assert any(
        item.startswith("startup_lazy_handoff_keys=")
        for item in contract_failure["violations"]
    )
    assert any(item.startswith("ready_keys=") for item in contract_failure["violations"])

    paint_region["full_viewport_count"] = 2
    acceptance = _background_prewarm_acceptance(status, paint_region)
    assert acceptance["status"] == "fail"
    assert "watchlist_full_viewport_during_hidden_prewarm=2" in acceptance["violations"]


def test_native_watchlist_profile_foreground_watchlist_hold_requires_zero_full_frames():
    clear_region = {"full_viewport_count": 0}
    clear_metrics = {"paint": {"full_viewport_count": 0}}

    assert _foreground_watchlist_hold_acceptance(
        observed=True,
        paint_region=clear_region,
        metrics=clear_metrics,
    ) == {"status": "pass", "violations": []}

    rejected = _foreground_watchlist_hold_acceptance(
        observed=False,
        paint_region={"full_viewport_count": 1},
        metrics={"paint": {"full_viewport_count": 1}},
    )

    assert rejected == {
        "status": "fail",
        "violations": [
            "watchlist_foreground_hold_not_observed",
            "watchlist_foreground_hold_full_viewport_input=1",
            "watchlist_foreground_hold_full_viewport_delivered=1",
        ],
    }


def test_native_watchlist_profile_foreground_hold_waits_a_full_policy_interval():
    assert _foreground_watchlist_hold_remaining_ms(0.0) == 1100
    assert _foreground_watchlist_hold_remaining_ms(1_099.1) == 1
    assert _foreground_watchlist_hold_remaining_ms(1_100.0) == 0
    assert _foreground_watchlist_hold_remaining_ms(1_101.0) == 0


def test_native_watchlist_profile_releases_foreground_hold_through_real_user_navigation():
    activation_calls = []
    scheduled = []
    failures = []
    phases = []
    specs = [{"key": "watchlist"}, {"key": "system_log"}]

    workspace = SimpleNamespace(
        tab_specs=lambda: specs,
        activate_tab=lambda index, *, reason: activation_calls.append((index, reason)) or True,
    )
    workspace._background_preload_coordinator = SimpleNamespace(
        advance=lambda: scheduled.append("advance")
    )
    controller = object.__new__(_NativeProfileController)
    controller.window = SimpleNamespace(_workspace=workspace)
    controller._foreground_watchlist_release_requested = False
    controller._set_phase = phases.append
    controller._fail = failures.append
    controller.QTimer = SimpleNamespace(
        singleShot=lambda delay, callback: scheduled.append((delay, callback))
    )

    assert _NativeProfileController._release_foreground_watchlist_prewarm_hold(controller) is True
    assert activation_calls == [(1, "user")]
    assert phases == ["background_prewarm_released"]
    assert failures == []
    assert scheduled[0][0] == 0
    assert scheduled[0][1]() is None
    assert scheduled == [(0, scheduled[0][1]), "advance"]


def test_native_watchlist_profile_accepts_finished_full_hidden_staging_without_lazy_handoff():
    planned_order = ["watchlist", *[f"tab-{index}" for index in range(10)]]
    staged_keys = planned_order[1:]
    status = {
        "finished": True,
        "planned_order": planned_order,
        "planned_count": len(planned_order),
        "start_order": planned_order,
        "completion_order": planned_order,
        "completion_scope": "all_planned",
        "visible_watchlist_state": "ready",
        "visible_watchlist_at": 1.0,
        "visible_watchlist_detail": "",
        "startup_lazy_handoff_keys": [],
        "ready_keys": planned_order,
        "failures": {},
    }
    specs = [
        {"key": key, "loaded": True, "mounted": key == "watchlist"}
        for key in planned_order
    ]
    workspace = SimpleNamespace(
        background_preload_status=lambda: status,
        tab_specs=lambda: specs,
        tabs=SimpleNamespace(count=lambda: len(specs)),
    )
    controller = object.__new__(_NativeProfileController)
    controller._done = False
    controller.window = SimpleNamespace(_workspace=workspace)
    controller.args = SimpleNamespace(prewarm_timeout_ms=1)
    controller._background_prewarm_started_at = 0.0
    controller._background_prewarm_offsets = None
    controller._background_prewarm_first_hidden_key = staged_keys[0]
    controller._watchlist_reveal_started_at = 1.0
    controller._activation_started = 1.0
    controller._heartbeat_by_phase = {}
    controller.report = {"errors": []}
    controller.paint_probe = SimpleNamespace(
        paint_region_summary=lambda phase: {
            "count": 0,
            "full_viewport_count": 0,
            "after_first": {"full_viewport_count": 0},
        }
    )
    reveal_calls = []
    phases = []
    continuation_calls = []
    timer_calls = []
    controller._record_watchlist_reveal = reveal_calls.append
    controller._metric_offsets = lambda: {}
    controller._metrics_since = lambda _offsets: {}
    controller._reset_stall_probe = lambda: None
    controller._stall_snapshot = lambda: {"installed": True, "total_count": 0}
    controller._set_phase = phases.append
    controller._continue_after_background_prewarm = lambda: continuation_calls.append(True)
    controller.QTimer = SimpleNamespace(
        singleShot=lambda *_args: timer_calls.append(_args)
    )

    _NativeProfileController._poll_background_prewarm_finished(controller)

    assert len(reveal_calls) == 1
    assert phases == ["background_prewarm"]
    assert timer_calls == []
    assert continuation_calls == [True]
    prewarm_report = controller.report["background_prewarm"]
    assert prewarm_report["first_hidden_key"] == staged_keys[0]
    assert prewarm_report["tab_count"] == 11
    assert prewarm_report["mounted_keys"] == ["watchlist"]
    assert prewarm_report["staged_keys"] == staged_keys
    assert prewarm_report["lazy_keys"] == []
    assert prewarm_report["ready_keys"] == planned_order
    assert prewarm_report["startup_lazy_handoff_keys"] == []
    assert prewarm_report["completion_scope"] == "all_planned"
    assert prewarm_report["visible_watchlist_state"] == "ready"
    assert prewarm_report["visible_watchlist_at"] == 1.0
    assert prewarm_report["visible_watchlist_detail"] == ""
    assert prewarm_report["paint_region"]["full_viewport_count"] == 0
    assert prewarm_report["acceptance"] == {"status": "pass", "violations": []}


def test_native_watchlist_profile_reveal_acceptance_allows_complete_later_frames():
    paint_metrics = {
        "durations": {"count": 2},
        "full_viewport_count": 1,
        "first": {"reason": "preload_reveal", "delivered_full_viewport": True},
        "after_first": {
            "count": 1,
            "full_viewport_count": 0,
            "other_full_viewport_count": 0,
        },
    }

    assert _watchlist_reveal_acceptance(paint_metrics) == {
        "status": "pass",
        "violations": [],
    }

    paint_metrics["full_viewport_count"] = 2
    paint_metrics["after_first"]["full_viewport_count"] = 1
    paint_metrics["after_first"]["other_full_viewport_count"] = 1
    assert _watchlist_reveal_acceptance(paint_metrics) == {
        "status": "pass",
        "violations": [],
    }
    assert _watchlist_reveal_acceptance({"durations": {"count": 0}, "after_first": {}}) == {
        "status": "fail",
        "violations": ["watchlist_reveal_paint_missing"],
    }
    assert _watchlist_reveal_acceptance(
        {
            "durations": {"count": 1},
            "first": {"reason": "preload_reveal", "delivered_full_viewport": False},
            "after_first": {"full_viewport_count": 0},
        }
    ) == {
        "status": "fail",
        "violations": ["watchlist_first_reveal_not_full_viewport"],
    }
    assert _watchlist_reveal_acceptance(
        {
            "durations": {"count": 1},
            "first": {"reason": "other", "delivered_full_viewport": True},
            "after_first": {"full_viewport_count": 0},
        }
    ) == {
        "status": "fail",
        "violations": ["watchlist_first_reveal_reason='other'"],
    }
    assert _watchlist_reveal_acceptance(
        paint_metrics={
            "durations": {"count": 1},
            "first": {"reason": "preload_reveal", "delivered_full_viewport": True},
            "after_first": {"full_viewport_count": 0},
        },
        viewport_background={
            "available": True,
            "auto_fill_background": False,
            "background_role": "Window",
        },
    ) == {
        "status": "fail",
        "violations": [
            "watchlist_viewport_base_background_disabled",
            "watchlist_viewport_background_role='Window'",
        ],
    }


def test_native_watchlist_profile_summarizes_backing_store_upstream_requests():
    summary = _FirstPaintProbe._summarize_viewport_update_requests(
        [
            {"event_type": "UpdateLater", "target": "viewport", "recorded_at_ms": 1.0},
            {
                "event_type": "UpdateLater",
                "target": "ancestor_5:ClassicWorkspace",
                "recorded_at_ms": 2.0,
            },
            {
                "event_type": "UpdateRequest",
                "target": "ancestor_8:MainWindowQT",
                "recorded_at_ms": 3.0,
            },
        ]
    )

    assert summary["event_type_counts"] == {"UpdateLater": 2, "UpdateRequest": 1}
    assert summary["target_counts"]["viewport"] == 1
    assert summary["upstream_target_counts"] == {
        "ancestor_5:ClassicWorkspace": 1,
        "ancestor_8:MainWindowQT": 1,
    }
    assert summary["backing_store_update_request_count"] == 1
    assert summary["upstream_update_later_count"] == 1


def test_native_watchlist_profile_summarizes_visible_watchlist_prewarm_spans():
    samples = [
        SimpleNamespace(
            value=110.4,
            tags={
                "method": "ClassicWorkspace.ensure_tab_loaded",
                "tab": "watchlist",
                "signal": "background_prewarm",
            },
        ),
        SimpleNamespace(
            value=111.2,
            tags={
                "method": "ClassicWorkspace._prewarm_next_tab",
                "tab": "",
                "signal": "background_prewarm",
            },
        ),
        SimpleNamespace(value=70.0, tags={"method": "unrelated"}),
    ]

    summary = native_watchlist_profile._summarize_named_runtime_spans(
        samples,
        names=(
            "ClassicWorkspace.ensure_tab_loaded",
            "ClassicWorkspace._prewarm_next_tab",
        ),
    )

    assert summary["count"] == 2
    assert summary["methods"]["ClassicWorkspace.ensure_tab_loaded"]["durations"]["max_ms"] == 110.4
    assert summary["methods"]["ClassicWorkspace._prewarm_next_tab"]["samples"] == [
        {"elapsed_ms": 111.2, "tab": "", "signal": "background_prewarm"}
    ]


def test_native_watchlist_profile_reveal_acceptance_rejects_dropped_native_full_frames():
    table_metric = SimpleNamespace(
        value=18.0,
        tags={
            "reason": "preload_reveal",
            "delivered_full_viewport": "true",
            "delivery_kind": "full_viewport",
            "dirty_bounding_area_ratio": "1.0000",
        },
    )
    controller = object.__new__(_NativeProfileController)
    controller.args = SimpleNamespace(shell_nav_only=False)
    controller.report = {"errors": []}
    controller._watchlist_reveal_offsets = {"watchlist_table_paint_ms": 0}
    controller._watchlist_reveal_started_at = 1.0
    controller.paint_probe = SimpleNamespace(
        paint_region_summary=lambda _phase: {
            "count": 3,
            "full_viewport_count": 3,
            "after_first": {"count": 2, "full_viewport_count": 2},
        },
        viewport_update_request_summary=lambda _phase: {
            "count": 2,
            "backing_store_update_request_count": 1,
            "samples": [],
        },
    )
    controller._metrics_since = lambda _offsets: {
        "watchlist_table_paint_ms": [table_metric],
        "ui_event_loop_stall_ms": [SimpleNamespace(value=24.0)],
    }
    controller._heartbeat_by_phase = {}
    controller._stall_snapshot = lambda: {"installed": True, "total_count": 0}

    _NativeProfileController._record_watchlist_reveal(controller, 1.2)

    reveal = controller.report["watchlist_reveal"]
    assert reveal["paint_region"]["full_viewport_count"] == 3
    assert reveal["metrics"]["paint"]["full_viewport_count"] == 1
    assert reveal["acceptance_metric_source"] == "watchlist_table_paint_ms"
    assert reveal["acceptance"] == {
        "status": "fail",
        "violations": ["watchlist_reveal full_viewport_paint_dropped=1/3"],
    }
    assert reveal["event_loop_stalls"]["max_ms"] == 24.0
    assert controller.report["errors"] == ["watchlist reveal repaint acceptance failed"]


def test_native_watchlist_profile_shell_nav_acceptance_requires_full_frame_delivery():
    paint_metrics = _summarize_shell_nav_paint_metrics(
        [
            SimpleNamespace(
                value=18.0,
                tags={
                    "reason": "other",
                    "delivered_full_viewport": "true",
                    "delivery_kind": "full_viewport",
                    "workspace_load_reason": "shell_nav",
                },
            ),
            SimpleNamespace(
                value=7.0,
                tags={
                    "reason": "model_data_changed",
                    "delivered_full_viewport": "false",
                    "delivery_kind": "partial_region",
                },
            ),
        ]
    )
    result = {
        "cycle": 1,
        "paint_region": {
            "count": 2,
            "full_viewport_count": 1,
            "after_first": {"full_viewport_count": 0},
        },
        "paint_metrics": paint_metrics,
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    assert paint_metrics["other_full_viewport_count"] == 1
    assert paint_metrics["other_full_viewport_after_first_count"] == 0
    assert paint_metrics["samples"][0]["workspace_load_reason"] == "shell_nav"
    assert _shell_nav_repaint_acceptance([result], expected_cycles=1) == {
        "status": "pass",
        "violations": [],
    }


def test_native_watchlist_profile_strict_warm_return_acceptance_requires_the_exact_single_frame_contract():
    result = {
        "cycle": 1,
        "paint_region": {
            "count": 1,
            "full_viewport_count": 1,
            "after_first": {"full_viewport_count": 0},
        },
        "paint_metrics": {
            "count": 1,
            "full_viewport_count": 1,
            "full_viewport_after_first_count": 0,
            "other_full_viewport_count": 1,
            "other_full_viewport_after_first_count": 0,
            "samples": [
                {
                    "source_tab": "stock_candidates",
                    "target_tab": "watchlist",
                    "transition_reason": "shell_nav",
                    "preload_state": "interactive_warm",
                    "mounted_before": "true",
                    "delivered_full_viewport": "true",
                    "native_window_signal": "",
                }
            ],
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }
    required_transition = {
        "source_tab": "stock_candidates",
        "target_tab": "watchlist",
        "transition_reason": "shell_nav",
        "preload_state": "interactive_warm",
        "mounted_before": "true",
    }

    assert _shell_nav_repaint_acceptance(
        [result],
        expected_cycles=1,
        required_transition=required_transition,
    ) == {"status": "pass", "violations": []}

    result["paint_region"]["after_first"]["full_viewport_count"] = 1
    result["paint_metrics"]["full_viewport_after_first_count"] = 1

    assert _shell_nav_repaint_acceptance(
        [result],
        expected_cycles=1,
        required_transition=required_transition,
    ) == {
        "status": "fail",
        "violations": ["cycle=1 warm_return_full_viewport_tail"],
    }


def test_native_watchlist_profile_shell_nav_guard_metrics_are_phase_local_and_diagnostic():
    summary = _summarize_shell_nav_guard_metrics(
        [
            SimpleNamespace(
                value=1,
                tags={
                    "decision": "first_full_allowed",
                    "workspace_load_reason": "shell_nav",
                    "age_ms": "3.100",
                    "remaining": "2",
                    "suppressed": "0",
                    "dirty_bounding_area_ratio": "1.0000",
                    "dirty_region_rects": "1",
                },
            ),
            SimpleNamespace(
                value=1,
                tags={
                    "decision": "suppress_redundant_full",
                    "workspace_load_reason": "shell_nav",
                    "age_ms": "18.200",
                    "remaining": "1",
                    "suppressed": "1",
                    "dirty_bounding_area_ratio": "1.0000",
                    "dirty_region_rects": "1",
                },
            ),
            SimpleNamespace(
                value=1,
                tags={
                    "decision": "allow_full_fallback",
                    "workspace_load_reason": "shell_nav",
                    "fallback_reason": "viewport_geometry",
                },
            ),
        ]
    )

    assert summary["count"] == 3
    assert summary["decision_counts"] == {
        "allow_full_fallback": 1,
        "first_full_allowed": 1,
        "suppress_redundant_full": 1,
    }
    assert summary["fallback_reason_counts"] == {"viewport_geometry": 1}
    assert summary["samples"][1] == {
        "decision": "suppress_redundant_full",
        "workspace_load_reason": "shell_nav",
        "age_ms": "18.200",
        "remaining": "1",
        "suppressed": "1",
        "dirty_bounding_area_ratio": "1.0000",
        "dirty_region_rects": "1",
        "fallback_reason": "",
    }


def test_native_watchlist_profile_shell_nav_acceptance_uses_actual_paint_not_incoming_event_filter():
    result = {
        "cycle": 1,
        # QApplication's event filter sees all three native full paints, but
        # the old guard delivers only the first to VCPTableView.paintEvent.
        "paint_region": {
            "count": 3,
            "full_viewport_count": 3,
            "after_first": {"full_viewport_count": 2},
        },
        "paint_metrics": {
            "count": 1,
            "full_viewport_count": 1,
            "full_viewport_after_first_count": 0,
            "other_full_viewport_count": 1,
            "other_full_viewport_after_first_count": 0,
        },
        "repaint_guard": {
            "decision_counts": {
                "first_full_allowed": 1,
                "suppress_redundant_full": 2,
            }
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    assert _shell_nav_repaint_acceptance([result], expected_cycles=1) == {
        "status": "fail",
        "violations": [
            "cycle=1 full_viewport_paint_dropped=1/3",
            "cycle=1 watchlist_full_viewport_paint_suppressed=2",
        ],
    }


def test_native_watchlist_profile_shell_nav_acceptance_allows_repeat_full_frames_when_delivered():
    result = {
        "cycle": 1,
        "paint_region": {
            "count": 3,
            "full_viewport_count": 3,
            "after_first": {"full_viewport_count": 2},
        },
        "paint_metrics": {
            "count": 3,
            "full_viewport_count": 3,
            "full_viewport_after_first_count": 2,
            "other_full_viewport_count": 2,
            "other_full_viewport_after_first_count": 1,
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    acceptance = _shell_nav_repaint_acceptance([result], expected_cycles=1)

    assert acceptance == {"status": "pass", "violations": []}


def test_native_watchlist_profile_shell_nav_acceptance_allows_late_other_full_metric_when_delivered():
    result = {
        "cycle": 1,
        "paint_region": {
            "count": 1,
            "full_viewport_count": 1,
            "after_first": {"full_viewport_count": 0},
        },
        "paint_metrics": {
            "count": 2,
            "full_viewport_count": 2,
            "full_viewport_after_first_count": 1,
            "other_full_viewport_count": 1,
            "other_full_viewport_after_first_count": 1,
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    acceptance = _shell_nav_repaint_acceptance([result], expected_cycles=1)

    assert acceptance == {"status": "pass", "violations": []}


def test_native_watchlist_profile_shell_nav_acceptance_preserves_tab_topology():
    result = {
        "cycle": 1,
        "tab_count": 10,
        "expected_tab_count": 11,
        "paint_region": {
            "count": 1,
            "full_viewport_count": 1,
            "after_first": {"full_viewport_count": 0},
        },
        "paint_metrics": {
            "count": 1,
            "full_viewport_count": 1,
            "other_full_viewport_count": 1,
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    acceptance = _shell_nav_repaint_acceptance([result], expected_cycles=1)

    assert acceptance["status"] == "fail"
    assert "cycle=1 tab_count=10 expected=11" in acceptance["violations"]


def test_native_watchlist_profile_shell_nav_acceptance_rejects_invalid_visual_evidence():
    result = {
        "cycle": 1,
        "paint_region": {
            "count": 1,
            "full_viewport_count": 1,
            "after_first": {"full_viewport_count": 0},
        },
        "paint_metrics": {
            "count": 1,
            "full_viewport_count": 1,
            "other_full_viewport_count": 1,
        },
        "visual_artifacts": {
            "main_window": {"saved": True},
            "watchlist_viewport": {"saved": False, "error": "screen_grab_too_small"},
        },
        "ui_stall_snapshot": {"installed": True, "event_loop_critical_count": 0},
    }

    assert _shell_nav_repaint_acceptance([result], expected_cycles=1) == {
        "status": "fail",
        "violations": [
            "cycle=1 visual_artifact=watchlist_viewport error=screen_grab_too_small"
        ],
    }


def test_native_watchlist_profile_region_summary_does_not_treat_sparse_span_as_full():
    summary = _FirstPaintProbe._summarize_paint_regions(
        [
            {
                "dirty_bounding_area_ratio": 1.0,
                "region_rect_count": 2,
                "delivered_full_viewport": False,
                "paint_event_spontaneous": True,
            }
        ]
    )

    assert summary["max_dirty_bounding_area_ratio"] == 1.0
    assert summary["full_viewport_count"] == 0
    assert summary["spontaneous_count"] == 1
    assert summary["samples"][0]["region_rect_count"] == 2


def test_native_watchlist_profile_model_signal_and_reset_to_paint_metrics_are_ordered():
    events = [
        {"model": "source", "signal": "model_reset", "recorded_at_ms": 100.0},
        {"model": "proxy", "signal": "model_reset", "recorded_at_ms": 101.0},
        {"model": "source", "signal": "rows_inserted", "recorded_at_ms": 102.0},
    ]
    paint_region = {
        "samples": [
            {"recorded_at_ms": 103.5, "delivered_full_viewport": True},
            {"recorded_at_ms": 105.0, "delivered_full_viewport": False},
        ]
    }

    assert _summarize_model_signal_events(events) == {
        "count": 3,
        "counts": {
            "proxy.model_reset": 1,
            "source.model_reset": 1,
            "source.rows_inserted": 1,
        },
        "events": events,
    }
    assert _reset_to_first_paint_delays(events, paint_region) == {
        "count": 2,
        "samples": [
            {
                "model": "source",
                "reset_at_ms": 100.0,
                "first_paint_at_ms": 103.5,
                "delay_ms": 3.5,
                "first_paint_full_viewport": True,
            },
            {
                "model": "proxy",
                "reset_at_ms": 101.0,
                "first_paint_at_ms": 103.5,
                "delay_ms": 2.5,
                "first_paint_full_viewport": True,
            },
        ],
    }


def test_native_watchlist_profile_summarizes_structural_delay_and_membership_metrics():
    delay_sample = SimpleNamespace(
        value=3346.63,
        tags={
            "reason": "model_reset",
            "structural_reason": "model_reset",
            "pending_reasons": "model_reset,quote_data_changed",
            "changed_rows": "1",
            "changed_indexes": "1",
            "model_rows": "46",
        },
    )
    reconcile_sample = SimpleNamespace(
        value=1,
        tags={"mode": "insert_one", "old_rows": "45", "new_rows": "46", "source": "initial_data"},
    )

    assert _summarize_paint_delay_metrics([delay_sample])["samples"] == [
        {
            "delay_ms": 3346.63,
            "reason": "model_reset",
            "structural_reason": "model_reset",
            "pending_reasons": "model_reset,quote_data_changed",
            "changed_rows": "1",
            "changed_indexes": "1",
            "model_rows": "46",
        }
    ]
    assert _summarize_membership_reconcile_metrics([reconcile_sample]) == {
        "count": 1,
        "modes": {"insert_one": 1},
        "samples": [
            {"mode": "insert_one", "old_rows": "45", "new_rows": "46", "source": "initial_data"}
        ],
    }


def test_native_watchlist_profile_metric_offsets_use_history_lengths(monkeypatch):
    from core import observability

    history = {
        "watchlist_table_paint_ms": [SimpleNamespace(value=11.0)],
        "watchlist_shell_nav_repaint_guard": [SimpleNamespace(value=1.0)],
        "ui_event_loop_stall_ms": [SimpleNamespace(value=417.0)],
    }
    monkeypatch.setattr(observability, "metric_history", lambda name: list(history[str(name)]))

    offsets = _NativeProfileController._metric_offsets(tuple(history))
    history["watchlist_table_paint_ms"].append(SimpleNamespace(value=19.0))
    history["watchlist_shell_nav_repaint_guard"].append(SimpleNamespace(value=1.0))
    history["ui_event_loop_stall_ms"].append(SimpleNamespace(value=23.0))

    observed = _NativeProfileController._metrics_since(offsets)

    assert offsets == {
        "watchlist_table_paint_ms": 1,
        "watchlist_shell_nav_repaint_guard": 1,
        "ui_event_loop_stall_ms": 1,
    }
    assert [sample.value for sample in observed["watchlist_table_paint_ms"]] == [19.0]
    assert [sample.value for sample in observed["watchlist_shell_nav_repaint_guard"]] == [1.0]
    assert [sample.value for sample in observed["ui_event_loop_stall_ms"]] == [23.0]


def test_native_watchlist_profile_shell_nav_finish_reports_actual_and_guard_phase_metrics():
    table_metric = SimpleNamespace(
        value=18.0,
        tags={
            "reason": "other",
            "delivered_full_viewport": "true",
            "workspace_load_reason": "shell_nav",
        },
    )
    guard_metric = SimpleNamespace(
        value=1,
        tags={
            "decision": "suppress_redundant_full",
            "workspace_load_reason": "shell_nav",
            "remaining": "1",
        },
    )
    tabs = SimpleNamespace(currentIndex=lambda: 0, count=lambda: 11)
    workspace = SimpleNamespace(
        tabs=tabs,
        tab_specs=lambda: [{"key": "watchlist"}, *({"key": "other"} for _ in range(10))],
    )
    controller = object.__new__(_NativeProfileController)
    controller._done = False
    controller.window = SimpleNamespace(_workspace=workspace)
    controller._shell_nav_phase = {
        "workspace": workspace,
        "cycle": 1,
        "watchlist_index": 0,
        "outbound_group": "market",
        "watchlist_group": "watchlist",
        "return_started_at": 0.0,
        "metric_offsets": {"phase": 2},
    }
    controller._shell_nav_results = []
    controller._shell_nav_cycle_index = 0
    controller._heartbeat_by_phase = {}
    controller.paint_probe = SimpleNamespace(
        paint_region_summary=lambda _phase: {
            "count": 3,
            "full_viewport_count": 3,
            "after_first": {"full_viewport_count": 2},
        }
    )
    offsets_seen = []
    controller._metrics_since = lambda offsets: offsets_seen.append(offsets) or {
        "watchlist_table_paint_ms": [table_metric],
        "watchlist_shell_nav_repaint_guard": [guard_metric],
        "ui_event_loop_stall_ms": [SimpleNamespace(value=417.0)],
    }
    controller._stall_snapshot = lambda: {"installed": True, "event_loop_critical_count": 0}
    controller._capture_shell_nav_visual_artifacts = lambda *_args: {
        "main_window": {"saved": True, "path": "main.png"},
        "watchlist_viewport": {"saved": True, "path": "viewport.png"},
    }
    continuation_calls = []
    controller._continue_after_background_prewarm = lambda: continuation_calls.append(True)
    timer_calls = []
    controller.QTimer = SimpleNamespace(singleShot=lambda *_args: timer_calls.append(_args))

    _NativeProfileController._finish_shell_nav_cycle(controller)

    assert offsets_seen == [{"phase": 2}]
    assert controller._shell_nav_cycle_index == 1
    assert controller._shell_nav_phase is None
    assert continuation_calls == []
    assert len(timer_calls) == 1
    result = controller._shell_nav_results[0]
    assert result["paint_region"]["full_viewport_count"] == 3
    assert result["paint_metrics"]["full_viewport_count"] == 1
    assert result["paint_metrics"]["full_viewport_after_first_count"] == 0
    assert result["repaint_guard"]["decision_counts"] == {"suppress_redundant_full": 1}
    assert result["event_loop_stalls"]["max_ms"] == 417.0
    assert result["visual_artifacts"]["watchlist_viewport"]["saved"] is True


@pytest.mark.parametrize("shell_nav_only", [False, True])
def test_native_watchlist_profile_shell_nav_only_scopes_reveal_enforcement(shell_nav_only):
    controller = object.__new__(_NativeProfileController)
    controller.args = SimpleNamespace(shell_nav_only=shell_nav_only)
    controller.report = {"errors": []}
    controller._watchlist_reveal_offsets = {}
    controller._watchlist_reveal_started_at = 1.0
    controller.paint_probe = SimpleNamespace(
        paint_region_summary=lambda _phase: {"count": 0, "after_first": {}},
        viewport_update_request_summary=lambda _phase: {
            "count": 1,
            "samples": [{"event_type": "UpdateRequest", "target": "ancestor_8:MainWindowQT"}],
        },
    )
    controller._metrics_since = lambda _offsets: {}
    controller._heartbeat_by_phase = {}
    controller._stall_snapshot = lambda: {"installed": True, "total_count": 0}

    _NativeProfileController._record_watchlist_reveal(controller, 1.2)

    reveal = controller.report["watchlist_reveal"]
    assert reveal["acceptance"]["status"] == "fail"
    assert reveal["acceptance_enforced"] is (not shell_nav_only)
    assert reveal["backing_store_update_requests"] == {
        "count": 1,
        "samples": [{"event_type": "UpdateRequest", "target": "ancestor_8:MainWindowQT"}],
    }
    assert controller.report["errors"] == (
        [] if shell_nav_only else ["watchlist reveal repaint acceptance failed"]
    )


def test_native_watchlist_profile_captures_shell_nav_visual_artifacts(tmp_path):
    class _Pixmap:
        def isNull(self):
            return False

        def save(self, path, _format):
            Path(path).write_bytes(b"fake-png")
            return True

        def devicePixelRatio(self):
            return 1.0

        def rect(self):
            return QRect(0, 0, 120, 80)

        def copy(self, _rect):
            return self

    class _Screen:
        def grabWindow(self, _win_id):
            return _Pixmap()

    class _Widget:
        def screen(self):
            return _Screen()

        def winId(self):
            return 1

    class _Viewport:
        def mapTo(self, _window, _point):
            return QPoint(0, 0)

        def width(self):
            return 20

        def height(self):
            return 10

    controller = object.__new__(_NativeProfileController)
    controller.window = _Widget()
    controller.activation_profile_path = tmp_path / "watchlist_activation.prof"
    table = SimpleNamespace(viewport=lambda: _Viewport())

    artifacts = controller._capture_shell_nav_visual_artifacts(2, table)

    assert artifacts["main_window"] == {
        "path": str(tmp_path / "shell_nav_cycle_2_main_window.png"),
        "saved": True,
    }
    assert artifacts["watchlist_viewport"] == {
        "path": str(tmp_path / "shell_nav_cycle_2_watchlist_viewport.png"),
        "saved": True,
    }


def test_native_watchlist_profile_rejects_tiny_shell_nav_screen_grab(tmp_path):
    class _TinyPixmap:
        def isNull(self):
            return False

        def width(self):
            return 1

        def height(self):
            return 1

        def save(self, path, _format):
            Path(path).write_bytes(b"tiny-png")
            return True

        def devicePixelRatio(self):
            return 1.0

        def rect(self):
            return QRect(0, 0, 1, 1)

        def copy(self, _rect):
            return self

    class _Screen:
        def grabWindow(self, _win_id):
            return _TinyPixmap()

    class _Widget:
        def screen(self):
            return _Screen()

        def winId(self):
            return 1

    controller = object.__new__(_NativeProfileController)
    controller.window = _Widget()
    controller.activation_profile_path = tmp_path / "watchlist_activation.prof"

    artifacts = controller._capture_shell_nav_visual_artifacts(2, table=None)

    assert artifacts["main_window"]["saved"] is False
    assert artifacts["main_window"]["error"] == "screen_grab_too_small"


def test_native_watchlist_profile_residual_acceptance_uses_structure_counts():
    results = [
        {
            "cycle": 1,
            "action": "name_refresh",
            "changed": True,
            "proxy_layout_changed_count": 0,
            "paint_region": {"count": 1, "full_viewport_count": 0},
            "heartbeat_lateness": {"count": 4, "max_ms": 1.0},
            "ui_stall_snapshot": {"installed": True, "total_count": 0},
            "metrics": {
                "model_updates": [{"mode": "direct"}],
                "paint": {"full_viewport_count": 0, "durations": {"count": 1}},
                "snapshot": {},
            },
        },
        {
            "cycle": 1,
            "action": "watchlist_to_lhb",
            "paint_region": {"count": 0, "full_viewport_count": 0},
            "heartbeat_lateness": {"count": 4, "max_ms": 1.0},
            "ui_stall_snapshot": {"installed": True, "total_count": 0},
            "metrics": {
                "model_updates": [],
                "paint": {"full_viewport_count": 0},
                "snapshot": {
                    "capture_count": 0,
                    "skipped_count": 1,
                    "skipped_pairs": [{"source": "watchlist", "target": "lhb"}],
                },
            },
        },
        {
            "cycle": 1,
            "action": "lhb_to_watchlist",
            "paint_region": {"count": 2, "full_viewport_count": 2},
            "heartbeat_lateness": {"count": 4, "max_ms": 80.0},
            "ui_stall_snapshot": {"installed": True, "total_count": 3, "critical_count": 0},
            "metrics": {
                "model_updates": [],
                "paint": {
                    "full_viewport_count": 2,
                    "first": {"reason": "native_profile_tab_return"},
                },
                "snapshot": {
                    "capture_count": 0,
                    "skipped_count": 1,
                    "skipped_pairs": [{"source": "lhb", "target": "watchlist"}],
                },
            },
        },
    ]

    assert _residual_repaint_acceptance(results, expected_cycles=1) == {
        "status": "pass",
        "violations": [],
    }
    results[1]["metrics"]["snapshot"]["capture_count"] = 1
    assert _residual_repaint_acceptance(results)["status"] == "fail"


def test_native_watchlist_profile_residual_acceptance_rejects_missing_cycles_and_stalls():
    assert _residual_repaint_acceptance([], expected_cycles=1) == {
        "status": "fail",
        "violations": ["residual_actions_missing"],
    }
    result = {
        "cycle": 1,
        "action": "name_refresh",
        "changed": True,
        "proxy_layout_changed_count": 0,
        "paint_region": {"count": 1, "full_viewport_count": 0},
        "heartbeat_lateness": {"count": 1, "max_ms": 493.0},
        "ui_stall_snapshot": {"installed": True, "total_count": 1},
        "metrics": {
            "model_updates": [{"mode": "direct"}],
            "paint": {"full_viewport_count": 0, "durations": {"count": 1}},
            "snapshot": {},
        },
    }

    acceptance = _residual_repaint_acceptance([result], expected_cycles=1)

    assert acceptance["status"] == "fail"
    assert "cycle=1 action=watchlist_to_lhb result_count=0" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist result_count=0" in acceptance["violations"]
    assert "cycle=1 action=name_refresh heartbeat_stall" in acceptance["violations"]
    assert "cycle=1 action=name_refresh ui_stall_recorded" in acceptance["violations"]


def test_native_watchlist_profile_residual_acceptance_keeps_stall_checks_without_rejecting_delivered_frames():
    result = {
        "cycle": 1,
        "action": "lhb_to_watchlist",
        "paint_region": {"count": 3, "full_viewport_count": 3},
        "heartbeat_lateness": {"count": 4, "max_ms": 120.0},
        "ui_stall_snapshot": {"installed": True, "total_count": 4, "critical_count": 1},
        "metrics": {
            "model_updates": [],
            "paint": {
                "full_viewport_count": 3,
                "first": {"reason": "other"},
            },
            "snapshot": {
                "capture_count": 0,
                "skipped_count": 1,
                "skipped_pairs": [{"source": "lhb", "target": "watchlist"}],
            },
        },
    }

    acceptance = _residual_repaint_acceptance([result])

    assert acceptance["status"] == "fail"
    assert "cycle=1 action=lhb_to_watchlist full_viewport_paint_budget" not in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist full_viewport_region_budget" not in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist first_paint_reason" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist heartbeat_stall" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist ui_critical_stall_recorded" in acceptance["violations"]


def test_native_watchlist_profile_residual_acceptance_rejects_dropped_visible_full_paint():
    result = {
        "cycle": 1,
        "action": "lhb_to_watchlist",
        "paint_region": {"count": 3, "full_viewport_count": 3},
        "heartbeat_lateness": {"count": 4, "max_ms": 1.0},
        "ui_stall_snapshot": {"installed": True, "total_count": 0, "critical_count": 0},
        "metrics": {
            "model_updates": [],
            "paint": {
                "full_viewport_count": 1,
                "first": {"reason": "native_profile_tab_return"},
            },
            "snapshot": {
                "capture_count": 0,
                "skipped_count": 1,
                "skipped_pairs": [{"source": "lhb", "target": "watchlist"}],
            },
        },
    }

    assert _residual_repaint_acceptance([result]) == {
        "status": "fail",
        "violations": [
            "cycle=1 action=lhb_to_watchlist full_viewport_paint_dropped=1/3",
        ],
    }


def test_native_watchlist_profile_quote_acceptance_enforces_local_direct_repaint():
    result = {
        "cycle": 1,
        "payload_size": 42,
        "proxy_layout_changed_count": 0,
        "paint_region": {"count": 2, "full_viewport_count": 0},
        "heartbeat_lateness": {"count": 20, "max_ms": 4.0},
        "ui_stall_snapshot": {"installed": True, "total_count": 0},
        "metrics": {
            "model_updates": [{"reason": "quote_snapshot", "mode": "direct"}],
            "paint": {
                "full_viewport_count": 0,
                "reasons": ["quote_data_changed", "flash_expiry"],
            },
        },
    }

    assert _quote_repaint_acceptance([result], expected_cycles=1) == {
        "status": "pass",
        "violations": [],
    }

    result["proxy_layout_changed_count"] = 1
    result["metrics"]["paint"]["full_viewport_count"] = 1
    acceptance = _quote_repaint_acceptance([result], expected_cycles=2)
    assert acceptance["status"] == "fail"
    assert "cycle=1 proxy_layout_changed" in acceptance["violations"]
    assert "cycle=1 full_viewport_paint" in acceptance["violations"]
    assert "cycle=2 result_count=0" in acceptance["violations"]


def test_native_watchlist_profile_cleans_isolated_database_on_exit(tmp_path, monkeypatch):
    callbacks = []
    database_path = tmp_path / "profile.db"
    database_path.write_bytes(b"db")
    (tmp_path / "profile.db-wal").write_bytes(b"wal")
    monkeypatch.setattr(native_watchlist_profile.atexit, "register", callbacks.append)

    native_watchlist_profile._register_profile_database_cleanup(database_path)
    callbacks[0]()

    assert not database_path.exists()
    assert not (tmp_path / "profile.db-wal").exists()
