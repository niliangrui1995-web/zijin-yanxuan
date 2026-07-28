from types import SimpleNamespace

from scripts import native_watchlist_profile
from scripts.native_watchlist_profile import (
    _build_synthetic_quote_payload,
    _event_dispatcher_summary,
    _native_platform_error,
    _parse_args,
    _quote_repaint_acceptance,
    _residual_repaint_acceptance,
    _summarize_residual_repaint_metrics,
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
                SimpleNamespace(value=8.0, tags={"dirty_bounding_area_ratio": "0.1250"}),
                SimpleNamespace(value=80.0, tags={"dirty_bounding_area_ratio": "1.0000"}),
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
