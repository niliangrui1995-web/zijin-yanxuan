from types import SimpleNamespace

from scripts import native_watchlist_profile
from scripts.native_watchlist_profile import (
    _background_prewarm_acceptance,
    _build_synthetic_quote_payload,
    _event_dispatcher_summary,
    _FirstPaintProbe,
    _native_platform_error,
    _NativeProfileController,
    _parse_args,
    _quote_repaint_acceptance,
    _residual_repaint_acceptance,
    _summarize_residual_repaint_metrics,
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
    assert args.prewarm_timeout_ms == 60_000
    assert args.quote_cycles == 0
    assert args.quote_cycle_ms == 1000
    assert args.quote_target_count == 6
    assert args.question_dialog_ms == 0
    assert args.residual_repaint_cycles == 0
    assert args.legacy_quote_repaint is False
    assert args.no_cprofile is False


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
    assert summary["paint"]["after_first"]["full_viewport_count"] == 1
    assert summary["snapshot"]["capture_count"] == 0
    assert summary["snapshot"]["skipped_count"] == 1


def test_native_watchlist_profile_background_prewarm_acceptance_requires_zero_hidden_full_paints():
    planned_order = ["watchlist", *[f"tab-{index}" for index in range(10)]]
    handoff_keys = planned_order[1:]
    status = {
        "finished": True,
        "planned_order": planned_order,
        "planned_count": 11,
        "start_order": ["watchlist"],
        "completion_order": ["watchlist"],
        "completion_scope": "visible_watchlist_ready",
        "startup_lazy_handoff_keys": handoff_keys,
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
        staged_keys=[],
        lazy_keys=handoff_keys,
    )["status"] == "pass"

    topology_failure = _background_prewarm_acceptance(
        status,
        paint_region,
        tab_count=10,
        mounted_keys=["watchlist", "tab-0"],
        staged_keys=["tab-0"],
        lazy_keys=handoff_keys[1:],
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
        completion_scope="all_planned",
        startup_lazy_handoff_keys=handoff_keys[1:],
    )
    contract_failure = _background_prewarm_acceptance(invalid_status, paint_region)
    assert contract_failure["status"] == "fail"
    assert any(item.startswith("start_order=") for item in contract_failure["violations"])
    assert any(item.startswith("completion_order=") for item in contract_failure["violations"])
    assert any(item.startswith("completion_scope=") for item in contract_failure["violations"])
    assert any(
        item.startswith("startup_lazy_handoff_keys=")
        for item in contract_failure["violations"]
    )

    paint_region["full_viewport_count"] = 2
    acceptance = _background_prewarm_acceptance(status, paint_region)
    assert acceptance["status"] == "fail"
    assert "watchlist_full_viewport_during_hidden_prewarm=2" in acceptance["violations"]


def test_native_watchlist_profile_accepts_finished_lazy_handoff_without_hidden_step():
    planned_order = ["watchlist", *[f"tab-{index}" for index in range(10)]]
    handoff_keys = planned_order[1:]
    status = {
        "finished": True,
        "planned_order": planned_order,
        "planned_count": len(planned_order),
        "start_order": ["watchlist"],
        "completion_order": ["watchlist"],
        "completion_scope": "visible_watchlist_ready",
        "startup_lazy_handoff_keys": handoff_keys,
        "failures": {},
    }
    specs = [
        {"key": key, "loaded": key == "watchlist", "mounted": key == "watchlist"}
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
    controller._background_prewarm_first_hidden_key = ""
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
    assert prewarm_report["first_hidden_key"] == ""
    assert prewarm_report["tab_count"] == 11
    assert prewarm_report["mounted_keys"] == ["watchlist"]
    assert prewarm_report["staged_keys"] == []
    assert prewarm_report["lazy_keys"] == handoff_keys
    assert prewarm_report["completion_scope"] == "visible_watchlist_ready"
    assert prewarm_report["paint_region"]["full_viewport_count"] == 0
    assert prewarm_report["acceptance"] == {"status": "pass", "violations": []}


def test_native_watchlist_profile_reveal_acceptance_allows_only_first_full_paint():
    paint_region = {
        "count": 2,
        "full_viewport_count": 1,
        "first": {"delivered_full_viewport": True},
        "after_first": {"count": 1, "full_viewport_count": 0},
    }

    assert _watchlist_reveal_acceptance(paint_region) == {
        "status": "pass",
        "violations": [],
    }

    paint_region["full_viewport_count"] = 2
    paint_region["after_first"]["full_viewport_count"] = 1
    assert _watchlist_reveal_acceptance(paint_region) == {
        "status": "fail",
        "violations": ["watchlist_full_viewport_after_reveal=1"],
    }
    assert _watchlist_reveal_acceptance({"count": 0, "after_first": {}}) == {
        "status": "fail",
        "violations": ["watchlist_reveal_paint_missing"],
    }
    assert _watchlist_reveal_acceptance(
        {
            "count": 1,
            "first": {"delivered_full_viewport": False},
            "after_first": {"full_viewport_count": 0},
        }
    ) == {
        "status": "fail",
        "violations": ["watchlist_first_reveal_not_full_viewport"],
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


def test_native_watchlist_profile_residual_acceptance_rejects_visible_return_burst():
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
    assert "cycle=1 action=lhb_to_watchlist full_viewport_paint_budget" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist full_viewport_region_budget" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist first_paint_reason" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist heartbeat_stall" in acceptance["violations"]
    assert "cycle=1 action=lhb_to_watchlist ui_critical_stall_recorded" in acceptance["violations"]


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
