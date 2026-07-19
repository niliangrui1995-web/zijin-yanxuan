import pytest

from scripts import perf_budget_check
from scripts.perf_budget_check import (
    check_gbbq_budget,
    check_kline_budget,
    check_kline_lifecycle_budget,
    check_round4_budget,
    check_round5_budget,
    check_runtime_health_budget,
    check_soak_budget,
    check_tab_cycle_budget,
)
from ui.workspaces.tab_registry import (
    health_probe_tab_keys,
    lineage_exclusion_tab_definitions,
    lineage_tab_definitions,
    startup_tab_keys,
)


def test_perf_budget_accepts_expected_probe_reports():
    gbbq_report = {
        "gbbq_profile": {
            "samples": {
                "single_code": {
                    "elapsed_ms": 40.0,
                    "rss_delta_mb": 0.2,
                    "result": {"codes": 1, "full_loaded": False},
                },
                "full": {
                    "elapsed_ms": 3000.0,
                    "rss_delta_mb": 78.0,
                    "result": {"codes": 6000, "full_loaded": True},
                },
            }
        }
    }
    tab_report = {"samples": {"tab_cycles": {"rss_delta_mb": 0.1}}}
    kline_report = {
        "samples": {
            "kline_cycles": {
                "rss_delta_mb": 92.0,
                "result": {
                    "cycles": 3,
                    "opened": 3,
                    "closed": 3,
                    "blocked": 0,
                    "cycle_samples": [
                        {"label": "kline_cycle_1:after_close", "webengine_child_count": 0},
                        {"label": "kline_cycle_2:after_close", "webengine_child_count": 0},
                    ],
                },
            }
        },
        "snapshots": [{"webengine_child_count": 0}],
    }
    soak_report = {
        "trend": {
            "growth_basis": "stable_close_samples",
            "rss": {"status": "ok", "tail_range": 3.0},
            "private": {"status": "ok", "tail_range": 6.0},
        },
        "samples": [{"webengine_child_count": 0}],
    }

    assert check_gbbq_budget(gbbq_report) == []
    assert check_tab_cycle_budget(tab_report) == []
    assert check_kline_budget(kline_report) == []
    assert check_soak_budget(soak_report) == []


def test_perf_budget_rejects_lazy_gbbq_regression():
    report = {
        "gbbq_profile": {
            "samples": {
                "single_code": {
                    "elapsed_ms": 40.0,
                    "rss_delta_mb": 70.0,
                    "result": {"codes": 6183, "full_loaded": True},
                }
            }
        }
    }

    failures = check_gbbq_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "gbbq.single.lazy",
        "gbbq.single.codes",
        "gbbq.single.rss_delta",
    }


def test_perf_budget_rejects_kline_child_process_retention():
    report = {
        "samples": {
            "kline_cycles": {
                "rss_delta_mb": 90.0,
                "result": {
                    "cycles": 2,
                    "opened": 2,
                    "closed": 2,
                    "blocked": 0,
                    "cycle_samples": [
                        {"label": "kline_cycle_1:after_close", "webengine_child_count": 1},
                    ],
                },
            }
        },
        "snapshots": [{"webengine_child_count": 1}],
    }

    failures = check_kline_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "kline.webengine_children.after_close",
        "kline.webengine_children.final",
    }


def _resource_growth_item(baseline: float, final: float, *, budget: float = 0.0) -> dict:
    delta = final - baseline
    return {
        "available": True,
        "baseline": baseline,
        "final": final,
        "delta": delta,
        "budget": budget,
        "status": "ok" if delta <= budget else "fail",
    }


def _valid_kline_lifecycle_report(*, cycles: int = 10, after_close_children: int = 1) -> dict:
    def _sample(label: str, *, rss_mb: float = 100.0, children: int = after_close_children) -> dict:
        return {
            "label": label,
            "rss_mb": rss_mb,
            "thread_count": 8,
            "background_task_count": 0,
            "active_timer_count": 4,
            "total_timer_count": 6,
            "event_receiver_count": 12,
            "webengine_available": True,
            "webengine_child_count": children,
        }

    resource_net_growth = {
        "status": "ok",
        "diagnostics_available": True,
        "thread_count": _resource_growth_item(8.0, 8.0),
        "background_task_count": _resource_growth_item(0.0, 0.0),
        "active_timer_count": _resource_growth_item(4.0, 4.0),
        "total_timer_count": _resource_growth_item(6.0, 6.0),
        "event_receiver_count": _resource_growth_item(12.0, 12.0),
        "webengine_child_count": _resource_growth_item(
            float(after_close_children),
            float(after_close_children),
        ),
        "rss_mb": _resource_growth_item(100.0, 124.0, budget=24.0),
    }

    def _stage_diagnostics() -> dict:
        stages = list(perf_budget_check.KLINE_OPEN_STAGE_ORDER)
        timings = {
            "shell_ready": 120.0,
            "browser_ready": 500.0,
            "data_ready": 650.0,
            "js_ready": 700.0,
            "chart_ready": 800.0,
            "first_interaction": 820.0,
        }
        return {
            "required_stages": stages,
            "completed_stages": stages,
            "pending_stages": [],
            "timings_ms": timings,
            "complete": True,
        }

    def _warmup_cycle(label: str) -> dict:
        return {
            "cycle_index": 0,
            "label": label,
            "measurement_role": label,
            "summary": {"status": "ok"},
            "stage_diagnostics": _stage_diagnostics(),
            "first_interaction_triggered": True,
            "first_interaction_ready": True,
            "ui_stalls": {
                "installed": True,
                "scope": "kline_open_to_chart_ready",
                "reset_succeeded": True,
                "critical_count": 0,
                "event_loop_critical_count": 0,
                "max_elapsed_ms": 100.0,
            },
            "samples": [
                _sample(f"{label}:before_open"),
                _sample(f"{label}:after_chart_ready"),
                _sample(f"{label}:after_close"),
            ],
        }

    measured_cycles = [
        {
            "cycle_index": index,
            "label": f"cycle_{index}",
            "measurement_role": "measured",
            "summary": {"status": "ok"},
            "stage_diagnostics": _stage_diagnostics(),
            "first_interaction_triggered": True,
            "first_interaction_ready": True,
            "ui_stalls": {
                "installed": True,
                "critical_count": 0,
                "event_loop_critical_count": 0,
                "max_elapsed_ms": 100.0,
            },
            "baseline_managed_webengine_keeper_count": 1,
            "final_managed_webengine_keeper_count": 1,
            "baseline_managed_webengine_keeper_ready": True,
            "final_managed_webengine_keeper_ready": True,
            "active_chart_view_count_after_close": 0,
            "samples": [
                _sample(f"cycle_{index}:before_open", rss_mb=124.0),
                _sample(f"cycle_{index}:after_chart_ready", rss_mb=124.0),
                _sample(f"cycle_{index}:after_close", rss_mb=124.0),
            ],
        }
        for index in range(1, cycles + 1)
    ]
    report = {
        "report_type": "kline_webengine_lifecycle_smoke",
        "status": "ok",
        "mode": {
            "native_qt": True,
            "allow_offscreen": False,
            "cycles": cycles,
            "minimum_cycles": 10,
        },
        "summary": {
            "status": "ok",
            "cycles": cycles,
            "expected_cycles": cycles,
            "minimum_cycles": 10,
            "minimum_cycle_gate": True,
            "cycle_count_complete": True,
            "final_webengine_child_count": 0,
            "steady_state_final_webengine_child_count": after_close_children,
            "shutdown_webengine_diagnostics_available": True,
            "managed_webengine_keeper_count_during_cycles": 1,
            "managed_webengine_keeper_ready_during_cycles": True,
            "resource_net_growth": resource_net_growth,
        },
        "cold_warmup_cycle": _warmup_cycle("cold_warmup"),
        "warmup_cycle": _warmup_cycle("warmup"),
        "cycles": measured_cycles,
        "samples": [sample for cycle in measured_cycles for sample in cycle["samples"]],
        "resource_growth": {
            "status": "ok",
            "basis": "warmup_after_close_to_last_measured_after_close",
            "cold_baseline_label": "cold_warmup:before_open",
            "warm_baseline_label": "warmup:after_close",
            "measured_final_label": f"cycle_{cycles}:after_close",
            "resource_net_growth": resource_net_growth,
        },
        "shutdown": {
            "post_close": _sample("shutdown:post_close", rss_mb=124.0, children=0),
            "included_in_lifecycle_resource_growth": False,
        },
        "cached_switch": {"samples_ms": [300.0] * 10},
    }
    return report


def _valid_native_lifecycle_evidence() -> dict:
    transition = {
        "status": "ok",
        "pause_observed": True,
        "runtime_reactivated": True,
        "chart_ready_after_resume": True,
    }
    return {
        "required": True,
        "status": "ok",
        "provider_mode": "production-local",
        "network_guard": {
            "status": "ok",
            "network_guard_active": True,
            "network_access_enabled": False,
            "network_request_count_before": 0,
            "network_request_count_after": 0,
            "no_network_requests": True,
        },
        "same_stock_multi_window": {
            "status": "ok",
            "first_open": {"chart_ready": True},
            "second_open": {"chart_ready": True},
            "same_code": True,
            "window_ids_distinct": True,
            "task_ids_distinct": True,
            "frame_owners_isolated": True,
            "browser_instances_distinct": True,
            "first_closed": True,
            "second_survived_first_close": True,
            "second_closed": True,
        },
        "visibility_pause_resume": {
            "status": "ok",
            "open": {"chart_ready": True},
            "hidden": dict(transition),
            "minimized": dict(transition),
            "browser_preserved": True,
            "identity_preserved": True,
            "latest_snapshot_owned_after_resume": True,
            "frame_owner_current": True,
            "closed": True,
        },
        "render_process_recovery": {
            "status": "ok",
            "open": {"chart_ready": True},
            "guard_installed": True,
            "controlled_termination_emitted": True,
            "browser_replaced": True,
            "browser_epoch_advanced": True,
            "structure_ready": True,
            "chart_ready_after_recovery": True,
            "identity_preserved": True,
            "frame_owner_current": True,
            "latest_snapshot_identity_preserved": True,
            "ack_received": True,
            "last_snapshot_replayed": True,
            "at_most_one_recovery": True,
            "closed": True,
            "recovery_attempts": 1,
            "recovery_limit": 1,
            "second_recovery_allowed": False,
        },
    }


def test_kline_lifecycle_budget_requires_complete_native_production_local_scenarios():
    valid = _valid_kline_lifecycle_report()
    valid["mode"]["provider_mode"] = "production-local"
    valid["native_lifecycle"] = _valid_native_lifecycle_evidence()
    valid_checks = {item["check"] for item in check_kline_lifecycle_budget(valid)}
    assert "kline_lifecycle.native_lifecycle" not in valid_checks

    for section in (
        "network_guard",
        "same_stock_multi_window",
        "visibility_pause_resume",
        "render_process_recovery",
    ):
        missing = _valid_kline_lifecycle_report()
        missing["mode"]["provider_mode"] = "production-local"
        missing["native_lifecycle"] = _valid_native_lifecycle_evidence()
        missing["native_lifecycle"].pop(section)
        failures = {item["check"] for item in check_kline_lifecycle_budget(missing)}
        assert "kline_lifecycle.native_lifecycle" in failures


def test_perf_budget_accepts_kline_lifecycle_smoke_report():
    report = _valid_kline_lifecycle_report(after_close_children=0)

    assert check_kline_lifecycle_budget(report) == []


def test_perf_budget_accepts_ready_keeper_until_final_shutdown():
    report = _valid_kline_lifecycle_report(after_close_children=1)

    assert check_kline_lifecycle_budget(report) == []


def test_kline_lifecycle_budget_requires_ordered_six_stage_interaction_contract():
    incomplete = _valid_kline_lifecycle_report()
    incomplete["cycles"][0]["stage_diagnostics"]["completed_stages"].pop()
    incomplete["cycles"][0]["stage_diagnostics"]["timings_ms"].pop("first_interaction")
    incomplete["cycles"][0]["stage_diagnostics"]["complete"] = False
    missing_trigger = _valid_kline_lifecycle_report()
    missing_trigger["warmup_cycle"]["first_interaction_triggered"] = False

    incomplete_failures = {item["check"] for item in check_kline_lifecycle_budget(incomplete)}
    missing_trigger_failures = {item["check"] for item in check_kline_lifecycle_budget(missing_trigger)}

    assert "kline_lifecycle.stage_contract" in incomplete_failures
    assert "kline_lifecycle.stage_contract" in missing_trigger_failures


def test_perf_budget_rejects_unready_or_unbounded_lifecycle_keeper():
    report = {
        "status": "ok",
        "summary": {
            "status": "ok",
            "final_webengine_child_count": 0,
            "managed_webengine_keeper_count_during_cycles": 2,
            "managed_webengine_keeper_ready_during_cycles": False,
        },
        "cycles": [],
    }
    failures = check_kline_lifecycle_budget(report)
    assert "kline_lifecycle.webengine_keeper.count" in {failure["check"] for failure in failures}

    report["summary"]["managed_webengine_keeper_count_during_cycles"] = 1
    failures = check_kline_lifecycle_budget(report)
    assert "kline_lifecycle.webengine_keeper.ready" in {failure["check"] for failure in failures}


def test_perf_budget_rejects_kline_lifecycle_child_retention():
    report = {
        "report_type": "kline_webengine_lifecycle_smoke",
        "status": "fail",
        "summary": {
            "status": "fail",
            "cycles": 1,
            "failed_cycles": [1],
            "final_webengine_child_count": 1,
        },
        "cycles": [
            {
                "cycle_index": 1,
                "summary": {"status": "fail"},
                "samples": [{"label": "cycle_1:after_close", "webengine_child_count": 1}],
            },
        ],
    }

    failures = check_kline_lifecycle_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "kline_lifecycle.status",
        "kline_lifecycle.webengine_children.final",
        "kline_lifecycle.cycle.status",
        "kline_lifecycle.webengine_children.after_close",
    }


def test_kline_lifecycle_budget_requires_native_executed_ten_cycle_evidence():
    too_short = _valid_kline_lifecycle_report(cycles=9)
    skipped = _valid_kline_lifecycle_report()
    skipped["status"] = "skipped"
    skipped["skip_reason"] = "no native Qt"
    offscreen = _valid_kline_lifecycle_report()
    offscreen["mode"]["native_qt"] = False
    offscreen["mode"]["allow_offscreen"] = True

    too_short_failures = {item["check"] for item in check_kline_lifecycle_budget(too_short)}
    skipped_failures = {item["check"] for item in check_kline_lifecycle_budget(skipped)}
    offscreen_failures = {item["check"] for item in check_kline_lifecycle_budget(offscreen)}

    assert "kline_lifecycle.cycles.samples" in too_short_failures
    assert "kline_lifecycle.skipped" in skipped_failures
    assert "kline_lifecycle.native_mode" in offscreen_failures


def test_kline_lifecycle_budget_enforces_exact_stage_percentiles():
    shell = _valid_kline_lifecycle_report()
    browser = _valid_kline_lifecycle_report()
    chart_p50 = _valid_kline_lifecycle_report()
    chart_p95 = _valid_kline_lifecycle_report()
    for cycle in shell["cycles"]:
        cycle["stage_diagnostics"]["timings_ms"]["shell_ready"] = 120.1
    for cycle in browser["cycles"]:
        cycle["stage_diagnostics"]["timings_ms"]["browser_ready"] = 500.1
    for cycle in chart_p50["cycles"]:
        cycle["stage_diagnostics"]["timings_ms"]["chart_ready"] = 800.1
    for cycle in chart_p95["cycles"][-2:]:
        cycle["stage_diagnostics"]["timings_ms"]["chart_ready"] = 1500.1

    assert "kline_lifecycle.performance.shell_ready.p95" in {
        item["check"] for item in check_kline_lifecycle_budget(shell)
    }
    assert "kline_lifecycle.performance.browser_ready.p95" in {
        item["check"] for item in check_kline_lifecycle_budget(browser)
    }
    assert "kline_lifecycle.performance.chart_ready.p50" in {
        item["check"] for item in check_kline_lifecycle_budget(chart_p50)
    }
    assert "kline_lifecycle.performance.chart_ready.p95" in {
        item["check"] for item in check_kline_lifecycle_budget(chart_p95)
    }


def test_kline_lifecycle_budget_fails_closed_on_missing_stage_samples():
    report = _valid_kline_lifecycle_report()
    report["cycles"][0]["stage_diagnostics"]["timings_ms"].pop("browser_ready")

    failures = {item["check"] for item in check_kline_lifecycle_budget(report)}

    assert failures >= {
        "kline_lifecycle.performance.browser_ready.diagnostics",
        "kline_lifecycle.performance.browser_ready.samples",
    }


def test_kline_lifecycle_budget_enforces_cached_switch_samples_and_p95():
    missing = _valid_kline_lifecycle_report()
    missing.pop("cached_switch")
    too_short = _valid_kline_lifecycle_report()
    too_short["cached_switch"]["samples_ms"] = [100.0] * 9
    slow = _valid_kline_lifecycle_report()
    slow["cached_switch"]["samples_ms"] = [300.1] * 10

    missing_failures = {item["check"] for item in check_kline_lifecycle_budget(missing)}
    too_short_failures = {item["check"] for item in check_kline_lifecycle_budget(too_short)}
    slow_failures = {item["check"] for item in check_kline_lifecycle_budget(slow)}

    assert missing_failures >= {
        "kline_lifecycle.cached_switch.diagnostics",
        "kline_lifecycle.cached_switch.samples",
    }
    assert "kline_lifecycle.cached_switch.samples" in too_short_failures
    assert "kline_lifecycle.cached_switch.p95" in slow_failures


def test_kline_lifecycle_budget_enforces_each_open_ui_stall_budget():
    missing = _valid_kline_lifecycle_report()
    missing["cycles"][0].pop("ui_stalls")
    slow = _valid_kline_lifecycle_report()
    slow["cycles"][0]["ui_stalls"].update(
        {
            "critical_count": 1,
            "event_loop_critical_count": 1,
            "max_elapsed_ms": 100.1,
        }
    )

    missing_failures = {item["check"] for item in check_kline_lifecycle_budget(missing)}
    slow_failures = {item["check"] for item in check_kline_lifecycle_budget(slow)}

    assert "kline_lifecycle.ui_stall.diagnostics" in missing_failures
    assert slow_failures >= {
        "kline_lifecycle.ui_stall.critical_count",
        "kline_lifecycle.ui_stall.event_loop_critical_count",
        "kline_lifecycle.ui_stall.max_elapsed",
    }


def test_kline_lifecycle_budget_includes_cold_and_regular_warmup_stalls():
    missing_cold = _valid_kline_lifecycle_report()
    missing_cold.pop("cold_warmup_cycle")
    missing_warmup = _valid_kline_lifecycle_report()
    missing_warmup.pop("warmup_cycle")
    slow_cold = _valid_kline_lifecycle_report()
    slow_cold["cold_warmup_cycle"]["ui_stalls"].update(
        {
            "critical_count": 1,
            "event_loop_critical_count": 1,
            "max_elapsed_ms": 100.1,
        }
    )

    missing_cold_failures = {item["check"] for item in check_kline_lifecycle_budget(missing_cold)}
    missing_warmup_failures = {item["check"] for item in check_kline_lifecycle_budget(missing_warmup)}
    slow_cold_failures = {item["check"] for item in check_kline_lifecycle_budget(slow_cold)}

    assert "kline_lifecycle.ui_stall.cold_warmup_cycle.diagnostics" in missing_cold_failures
    assert "kline_lifecycle.ui_stall.warmup_cycle.diagnostics" in missing_warmup_failures
    assert slow_cold_failures >= {
        "kline_lifecycle.ui_stall.critical_count",
        "kline_lifecycle.ui_stall.event_loop_critical_count",
        "kline_lifecycle.ui_stall.max_elapsed",
    }


def test_kline_lifecycle_budget_lists_every_stall_violation_with_cycle_identity():
    report = _valid_kline_lifecycle_report()
    violating_cycles = (
        report["cold_warmup_cycle"],
        report["warmup_cycle"],
        report["cycles"][1],
    )
    for cycle in violating_cycles:
        cycle["ui_stalls"].update(
            {
                "critical_count": 1,
                "event_loop_critical_count": 1,
                "max_elapsed_ms": 100.1,
            }
        )

    failures = {
        item["check"]: item
        for item in check_kline_lifecycle_budget(report)
        if item["check"].startswith("kline_lifecycle.ui_stall.")
    }

    expected_identities = [
        (0, "cold_warmup", "cold_warmup"),
        (0, "warmup", "warmup"),
        (2, "cycle_2", "measured"),
    ]
    for check, stage in (
        ("kline_lifecycle.ui_stall.critical_count", "critical_count"),
        ("kline_lifecycle.ui_stall.event_loop_critical_count", "event_loop_critical_count"),
        ("kline_lifecycle.ui_stall.max_elapsed", "max_elapsed_ms"),
    ):
        failure = failures[check]
        assert failure["label"] == "cycle_2"
        assert failure["measurement_role"] == "measured"
        assert [
            (item["cycle"], item["label"], item["measurement_role"])
            for item in failure["violations"]
        ] == expected_identities
        assert {item["stage"] for item in failure["violations"]} == {stage}


def test_kline_lifecycle_budget_requires_all_zero_growth_resources_and_rss_under_24mb():
    missing = _valid_kline_lifecycle_report()
    missing["summary"]["resource_net_growth"].pop("event_receiver_count")
    leaked = _valid_kline_lifecycle_report()
    leaked["summary"]["resource_net_growth"]["active_timer_count"]["delta"] = 1.0
    rss = _valid_kline_lifecycle_report()
    rss["summary"]["resource_net_growth"]["rss_mb"]["delta"] = 24.1

    missing_failures = {item["check"] for item in check_kline_lifecycle_budget(missing)}
    leaked_failures = {item["check"] for item in check_kline_lifecycle_budget(leaked)}
    rss_failures = {item["check"] for item in check_kline_lifecycle_budget(rss)}

    assert "kline_lifecycle.resources.event_receiver_count.diagnostics" in missing_failures
    assert "kline_lifecycle.resources.active_timer_count.net_growth" in leaked_failures
    assert "kline_lifecycle.resources.rss_mb.net_growth" in rss_failures


def test_kline_lifecycle_budget_rejects_repeated_cycle_evidence_with_forged_count():
    report = _valid_kline_lifecycle_report()
    report["cycles"] = [report["cycles"][0] for _index in range(10)]
    report["samples"] = [sample for cycle in report["cycles"] for sample in cycle["samples"]]

    failures = {item["check"] for item in check_kline_lifecycle_budget(report)}

    assert "kline_lifecycle.cycles.identity" in failures


def test_kline_lifecycle_budget_rejects_non_finite_raw_resource_sample_even_when_summary_says_ok():
    report = _valid_kline_lifecycle_report()
    report["cycles"][-1]["samples"][-1]["rss_mb"] = float("nan")

    failures = {item["check"] for item in check_kline_lifecycle_budget(report)}

    assert failures >= {
        "kline_lifecycle.resources.sample_diagnostics",
        "kline_lifecycle.resources.rss_mb.recomputed",
    }


def test_kline_lifecycle_budget_recomputes_resource_summary_from_raw_samples():
    report = _valid_kline_lifecycle_report()
    report["summary"]["resource_net_growth"]["thread_count"]["delta"] = 0.0
    report["summary"]["resource_net_growth"]["thread_count"]["baseline"] = 999.0

    failures = {item["check"] for item in check_kline_lifecycle_budget(report)}

    assert "kline_lifecycle.resources.thread_count.recomputed" in failures


def test_kline_lifecycle_budget_requires_zero_child_shutdown_receipt():
    missing = _valid_kline_lifecycle_report()
    missing.pop("shutdown")
    retained = _valid_kline_lifecycle_report()
    retained["shutdown"]["post_close"]["webengine_child_count"] = 1

    missing_failures = {item["check"] for item in check_kline_lifecycle_budget(missing)}
    retained_failures = {item["check"] for item in check_kline_lifecycle_budget(retained)}

    assert "kline_lifecycle.shutdown.diagnostics" in missing_failures
    assert retained_failures >= {
        "kline_lifecycle.shutdown.contract",
        "kline_lifecycle.shutdown.summary",
    }


def test_perf_budget_rejects_soak_open_peak_basis():
    report = {
        "trend": {
            "growth_basis": "all_samples",
            "rss": {"status": "ok", "tail_range": 2.0},
            "private": {"status": "warn", "tail_range": 30.0},
        },
        "samples": [{"webengine_child_count": 0}],
    }

    failures = check_soak_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "soak.growth_basis",
        "soak.private.status",
        "soak.private.tail_range",
    }


def test_round4_budget_accepts_expected_report():
    report = {
        "startup": {"main_window_ready_ms": 1200.0},
        "tab_first_open": {
            "tabs": [
                {"key": "watchlist", "status": "ok", "elapsed_ms": 20.0},
                {"key": "fund_holdings", "status": "ok", "elapsed_ms": 850.0},
            ]
        },
        "f5_refresh": {
            "total_elapsed_ms": 900.0,
            "active_background_tasks_after": 0,
            "new_active_background_tasks_after": 0,
            "tab_timings": [{"label": "watchlist", "elapsed_ms": 12.0}],
            "quote_requests": {
                "duplicate_across_batches": 0,
                "duplicate_in_batch": 0,
                "duplicates_by_code": {},
            },
        },
        "stability": {
            "trend": {
                "active_tasks": {"last": 0},
                "active_timers": {"net_delta": 0},
                "threads": {"net_delta": 1},
            }
        },
    }

    assert check_round4_budget(report) == []


def test_round4_budget_rejects_duplicates_and_growth():
    report = {
        "startup": {"main_window_ready_ms": 7000.0},
        "tab_first_open": {"tabs": [{"key": "scan", "status": "timeout", "elapsed_ms": 7000.0}]},
        "f5_refresh": {
            "total_elapsed_ms": 7000.0,
            "active_background_tasks_after": 1,
            "new_active_background_tasks_after": 1,
            "tab_timings": [{"label": "scan", "elapsed_ms": 3000.0}],
            "quote_requests": {
                "duplicate_across_batches": 2,
                "duplicate_in_batch": 1,
                "duplicates_by_code": {"000001": 3},
            },
        },
        "stability": {
            "trend": {
                "active_tasks": {"last": 2},
                "active_timers": {"net_delta": 3},
                "threads": {"net_delta": 20},
            }
        },
    }

    failures = check_round4_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "round4.startup.main_window_ready",
        "round4.tabs.status",
        "round4.tabs.elapsed",
        "round4.f5.total_elapsed",
        "round4.f5.tab_elapsed",
        "round4.f5.quote_duplicates",
        "round4.f5.new_active_tasks_after",
        "round4.stability.active_tasks_final",
        "round4.stability.active_timer_growth",
        "round4.stability.thread_growth",
    }


def test_round5_budget_accepts_expected_post_f5_report():
    report = {
        "mode": {"isolate_info_source_refresh": False},
        "post_f5": {
            "quote_requests": {
                "batch_count": 1,
                "repeated_batch_signature_count": 0,
                "duplicate_quote_code_count": 0,
                "cache_only_quote_request_count": 0,
            },
            "cache_only_guard": {
                "cache_only_quote_request_count": 0,
                "information_source_background_task_count": 0,
            },
            "background_tasks": {
                "new_active_task_final": 0,
                "active_earnings_worker_count_final": 0,
            },
            "runtime_trend": {
                "active_timers": {"net_delta": 0},
                "threads": {"net_delta": 1},
            },
            "event_receiver_trend": {
                "sig_cache_reload_completed": {"net_delta": 0},
            },
        },
    }

    assert check_round5_budget(report) == []


def test_round5_budget_rejects_isolated_information_source_report():
    report = {
        "mode": {"isolate_info_source_refresh": True},
        "post_f5": {
            "quote_requests": {},
            "cache_only_guard": {},
            "background_tasks": {},
            "runtime_trend": {},
            "event_receiver_trend": {},
        },
    }

    failures = check_round5_budget(report)

    assert any(failure["check"] == "round5.mode.isolated_info_source_refresh" for failure in failures)


def test_round5_budget_rejects_post_f5_network_tail():
    report = {
        "post_f5": {
            "quote_requests": {
                "batch_count": 3,
                "repeated_batch_signature_count": 1,
                "duplicate_quote_code_count": 2,
                "duplicates_by_code": {"000001": 3},
                "repeated_batch_signatures": {"000001|600519": 2},
            },
            "cache_only_guard": {
                "cache_only_quote_request_count": 1,
                "information_source_background_task_count": 2,
            },
            "background_tasks": {
                "new_active_task_final": 1,
                "new_active_task_ids_final": ["foreign_block_trade"],
                "active_earnings_worker_count_final": 1,
                "active_earnings_workers_final": ["routine"],
            },
            "runtime_trend": {
                "active_timers": {"net_delta": 3},
                "threads": {"net_delta": 20},
            },
            "event_receiver_trend": {
                "sig_cache_reload_completed": {"net_delta": 1},
            },
        }
    }

    failures = check_round5_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "round5.quote.batch_count",
        "round5.quote.repeated_batch_signatures",
        "round5.quote.duplicate_codes",
        "round5.cache_only.quote_requests",
        "round5.cache_only.background_tasks",
        "round5.background.new_active_tasks_final",
        "round5.background.active_earnings_workers_final",
        "round5.runtime.active_timer_growth",
        "round5.runtime.thread_growth",
        "round5.events.receiver_growth",
    }


def _runtime_health_sample(**overrides):
    sample = {
        "background_tasks": {"available": True, "count": 0},
        "timers": {"active": 4, "total": 8},
        "event_bus": {"total_receivers": 12},
        "process": {"rss_mb": 500.0, "thread_count": 24},
        "webengine": {"available": True, "count": 0},
        "ui_stalls": {
            "installed": True,
            "critical_count": 0,
            "event_loop_critical_count": 0,
            "max_elapsed_ms": 0.0,
        },
        "market_data": {"ok": True},
        "f5_refresh": {"workspace_available": True},
        "quotes": {
            "request_stats": {"recent_batch_count": 1, "recent_codes_count": 20},
            "provider_degraded": False,
            "last_network_error": "",
        },
        "f5_cache": {
            "cache_version": 1,
            "trade_date": "2026-05-08",
            "updated_at": "2026-05-08T15:00:00",
        },
        "data_lineage": [
            {
                "key": "stock_candidates",
                "title": "综合候选",
                "source": "workspace_stock_context",
                "cache_refs": ["global_store.quotes"],
                "network_capable": False,
                "triggered_network": False,
                "fallback_or_degraded": False,
                "loaded": True,
            }
        ],
    }
    sample.update(overrides)
    return sample


def _resource_trend(samples: list[dict]) -> dict:
    value_getters = {
        "background_tasks": lambda sample: sample["background_tasks"]["count"],
        "active_timers": lambda sample: sample["timers"]["active"],
        "total_timers": lambda sample: sample["timers"]["total"],
        "event_receivers": lambda sample: sample["event_bus"]["total_receivers"],
        "threads": lambda sample: sample["process"]["thread_count"],
        "rss_mb": lambda sample: sample["process"]["rss_mb"],
    }
    return {
        key: perf_budget_check._runtime_health_trend_one([getter(sample) for sample in samples])
        for key, getter in value_getters.items()
    }


def _completed_startup_task_settle() -> dict:
    return {
        "status": "ok",
        "task_id": "smart_startup",
        "task_ids": ["smart_startup", "asian_data_sync_bg"],
        "active_before": False,
        "active_before_ids": [],
        "active_after": False,
        "observed_task_ids": [],
        "remaining_task_ids": [],
        "delayed_task_ids": ["asian_data_sync_bg"],
        "delay_horizon_ms": 500,
        "timeout_ms": 3000,
        "contaminated": False,
    }


def _completed_post_tab_idle() -> dict:
    return {
        "status": "ok",
        "timeout_ms": 5000,
        "ownership": "phase_started_task_ids",
        "task_id_diagnostics_available": True,
        "active_before": 0,
        "active_after": 0,
        "baseline_task_ids": [],
        "started_task_ids": [],
        "remaining_task_ids": [],
        "concurrent_startup_task_ids": [],
        "active_before_ids": [],
        "active_after_ids": [],
    }


def _completed_post_tab_idle_with_live_baseline() -> dict:
    receipt = _completed_post_tab_idle()
    baseline = ["asian_data_sync_bg", "preexisting"]
    receipt.update(
        active_before=3,
        active_after=2,
        baseline_task_ids=baseline,
        started_task_ids=["tab_job"],
        concurrent_startup_task_ids=["asian_data_sync_bg"],
        active_before_ids=[*baseline, "tab_job"],
        active_after_ids=baseline,
    )
    return receipt


def _completed_ui_stall_sampling() -> dict:
    phases = [
        "idle",
        "background_preload",
        "tab_cycle",
        "tab_async_tail",
        "f5_cycle",
        "quote_cycle",
        "kline_cycle",
        "shutdown",
    ]
    return {
        "scope": "phase_local",
        "boundary_strategy": "qt_event_loop_settle_then_reset",
        "phase_boundaries": [
            {
                "phase": phase,
                "settle_ms": 0,
                "elapsed_ms": 0.0,
                "stall_snapshot_reset": True,
            }
            for phase in phases
        ],
    }


def _workspace_background_preload_shutdown(**overrides) -> dict:
    receipt = {
        "active_key": "",
        "cancelling_key": "",
        "remaining_keys": [],
        "active_step_count": 0,
        "timer_active": False,
        "cancellation_blocked": False,
        "shutdown_cancel_receipts": [],
        "shutdown_cancellation_settled": True,
    }
    receipt.update(overrides)
    return receipt


def _runtime_health_post_close(**overrides):
    kline_manager_shutdown_diagnostics = {
        "active_close_clean": True,
        "active_close_attempted": 0,
        "active_close_succeeded": 0,
        "active_fallback_disposed": 0,
        "pooled_dispose_clean": True,
        "prewarm_dispose_clean": True,
        "return_timer_clean": True,
        "idle_guard_clean": True,
        "preflight_clean": True,
        "active_windows": 0,
        "managed_keepers": 0,
        "pending_open": False,
        "prewarm_main_window_retained": False,
        "clean": True,
    }
    post_close = {
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
        "f5_runtime_artifacts_diagnostics_available": True,
        "f5_runtime_artifacts": {
            "clean": True,
            "active_snapshot_id": "a" * 32,
            "active_snapshot_complete": True,
            "generation_count": 2,
            "generation_limit": 2,
            "unexpected_generation_ids": [],
            "incomplete_generation_ids": [],
            "invalid_generation_entries": [],
            "unfinished_job_ids": [],
            "ready_to_activate_job_ids": [],
            "invalid_job_ids": [],
            "invalid_job_entries": [],
            "job_count": 0,
            "terminal_job_count": 0,
            "terminal_job_ids": [],
            "temporary_file_count": 0,
            "temporary_files": [],
        },
        "webengine_available": True,
        "webengine_child_count": 0,
        "workspace_background_preload_diagnostics_available": True,
        "workspace_background_preload": _workspace_background_preload_shutdown(),
        "kline_manager_shutdown_diagnostics_available": True,
        "kline_manager_shutdown_diagnostics": kline_manager_shutdown_diagnostics,
    }
    post_close.update(overrides)
    return post_close


def test_runtime_health_budget_accepts_structured_suite_report():
    report = {
        "startup_ready_ms": 900.0,
        "mode": {"tabs": ["stock_candidates"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 0.0}),
            _runtime_health_sample(
                timers={"active": 5, "total": 9},
                process={"rss_mb": 504.0, "thread_count": 25},
                ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 0.0},
            ),
        ],
    }

    assert check_runtime_health_budget(report) == []


def _completed_background_preload_report() -> dict:
    expected = list(startup_tab_keys())
    return {
        "status": "ok",
        "contract_ok": True,
        "enabled": True,
        "started": True,
        "finished": True,
        "expected_order": expected,
        "planned_order": expected,
        "planned_count": len(expected),
        "start_order": expected,
        "completion_order": expected,
        "loaded_keys": list(reversed(expected)),
        "loaded_count": len(expected),
        "max_concurrent_steps": 1,
        "auto_refresh_task_diagnostics_available": True,
        "auto_refresh_task_ids_observed": [],
        "preload_task_window_observed": True,
        "startup_task_ids_observed": [],
        "background_task_ids_observed": [],
        "preload_baseline_task_ids": [],
        "startup_network_task_diagnostics_available": True,
        "startup_network_task_ids_observed": [],
        "startup_network_task_categories": {},
        "startup_cache_bootstrap_required": False,
        "startup_cache_bootstrap_ready": True,
        "active_key": "",
        "ready_keys": expected,
        "remaining_keys": [],
        "failures": {},
        "dependency_failures": {},
        "timeouts": [],
        "pending_priority_keys": [],
        "cancelling_key": "",
        "cancel_receipt": {},
        "cancellation_settlement_timeout_ms": 5000,
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


def _completed_background_preload_network_sample() -> dict:
    definitions = lineage_tab_definitions()
    exclusions = lineage_exclusion_tab_definitions()
    return _runtime_health_sample(
        label="after_background_preload",
        measurement_phase="background_preload",
        data_lineage=[
            {
                "key": definition.key,
                "title": definition.title,
                "source": definition.lineage.source,
                "cache_refs": list(definition.lineage.cache_refs),
                "network_capable": bool(definition.lineage.network_capable),
                "triggered_network": False,
                "fallback_or_degraded": definition.lineage.fallback_or_degraded,
                "loaded": True,
            }
            for definition in definitions
        ],
        data_lineage_coverage={
            "covered": [definition.key for definition in definitions],
            "excluded": [definition.key for definition in exclusions],
        },
        data_lineage_exclusions=[
            {
                "key": definition.key,
                "reason": definition.lineage_exclusion.reason,
                "description": definition.lineage_exclusion.description,
                "loaded": True,
            }
            for definition in exclusions
        ],
    )


def test_runtime_health_budget_accepts_completed_serial_background_preload():
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_recomputes_background_preload_contract_from_typed_fields():
    invalid = _completed_background_preload_report()
    invalid["contract_ok"] = True
    invalid["remaining_keys"] = ()
    invalid["failures"] = []
    invalid.pop("enabled")
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": invalid,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.diagnostics" in failures

    valid = _completed_background_preload_report()
    valid["contract_ok"] = False
    report["background_preload"] = valid
    assert check_runtime_health_budget(report) == []


def test_runtime_health_background_preload_requires_clean_auto_refresh_task_receipt():
    missing = _completed_background_preload_report()
    missing.pop("auto_refresh_task_diagnostics_available")
    missing_window = _completed_background_preload_report()
    missing_window.pop("preload_task_window_observed")
    observed = _completed_background_preload_report()
    observed["auto_refresh_task_ids_observed"] = ["auto_refresh_asian_market_runtime"]
    startup_overlap = _completed_background_preload_report()
    startup_overlap["startup_task_ids_observed"] = ["smart_startup"]
    bootstrap_unready = _completed_background_preload_report()
    bootstrap_unready["startup_cache_bootstrap_required"] = True
    bootstrap_unready["startup_cache_bootstrap_ready"] = False
    report = {
        "mode": {"background_prewarm": True},
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    report["background_preload"] = missing
    missing_failures = {item["check"] for item in check_runtime_health_budget(report)}
    report["background_preload"] = missing_window
    window_failures = {item["check"] for item in check_runtime_health_budget(report)}
    report["background_preload"] = observed
    observed_failures = {item["check"] for item in check_runtime_health_budget(report)}
    report["background_preload"] = startup_overlap
    startup_failures = {item["check"] for item in check_runtime_health_budget(report)}
    report["background_preload"] = bootstrap_unready
    bootstrap_failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.diagnostics" in missing_failures
    assert "runtime_health.background_preload.diagnostics" in window_failures
    assert "runtime_health.background_preload.clean_completion" in observed_failures
    assert "runtime_health.background_preload.clean_completion" in startup_failures
    assert "runtime_health.background_preload.clean_completion" in bootstrap_failures


def test_runtime_health_budget_rejects_global_asian_network_task_during_hidden_preload():
    startup_task_settle = _completed_startup_task_settle()
    startup_task_settle["observed_task_ids"] = ["asian_data_sync_bg"]
    report = {
        "mode": {
            "background_prewarm": True,
            "startup_enabled": True,
        },
        "startup_task_settle": startup_task_settle,
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.global_network_task" in failures


def test_runtime_health_budget_rejects_auto_refresh_asian_task_during_hidden_preload():
    startup_task_settle = _completed_startup_task_settle()
    startup_task_settle["observed_task_ids"] = ["auto_refresh_asian_market_runtime"]
    report = {
        "mode": {"background_prewarm": True, "startup_enabled": True},
        "startup_task_settle": startup_task_settle,
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.global_network_task" in failures


def test_runtime_health_budget_rejects_typed_startup_or_network_task_during_hidden_preload():
    preload = _completed_background_preload_report()
    preload["startup_network_task_ids_observed"] = ["cn_trade_calendar_refresh"]
    preload["startup_network_task_categories"] = {"cn_trade_calendar_refresh": "startup"}
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.global_network_task" in failures
    assert "runtime_health.background_preload.clean_completion" in failures


def test_runtime_health_budget_fails_closed_without_startup_network_task_diagnostics():
    preload = _completed_background_preload_report()
    preload.pop("startup_network_task_diagnostics_available")
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.diagnostics" in failures


def test_runtime_health_budget_fails_closed_when_enabled_preload_diagnostics_are_missing():
    report = {
        "mode": {"background_prewarm": True},
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.diagnostics" in failures


def test_runtime_health_budget_rejects_incomplete_background_preload():
    preload = _completed_background_preload_report()
    preload.update(
        {
            "status": "failed",
            "contract_ok": False,
            "completion_order": preload["completion_order"][:-1],
            "loaded_keys": preload["loaded_keys"][:-1],
            "loaded_count": 10,
            "max_concurrent_steps": 2,
            "failures": {"stock_candidates": "task failed"},
        }
    )
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert failures >= {
        "runtime_health.background_preload.status",
        "runtime_health.background_preload.order",
        "runtime_health.background_preload.loaded",
        "runtime_health.background_preload.concurrency",
        "runtime_health.background_preload.clean_completion",
    }


def test_runtime_health_budget_requires_typed_background_preload_readiness_receipt():
    preload = _completed_background_preload_report()
    preload["ready_keys"] = tuple(preload["ready_keys"])
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.diagnostics" in failures


def test_runtime_health_budget_requires_every_background_preload_tab_ready_in_order():
    preload = _completed_background_preload_report()
    preload["ready_keys"] = preload["ready_keys"][:-1]
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.ready" in failures


def test_runtime_health_budget_rejects_active_or_cancellation_blocked_background_preload():
    preload = _completed_background_preload_report()
    preload.update(
        {
            "active_step_count": 1,
            "timer_active": True,
            "cancelling_key": "stock_candidates",
            "cancellation_blocked": True,
            "blocked_reason": "cancellation_timeout",
            "cancellation_timeouts": {"stock_candidates": {"elapsed_ms": 5000.0}},
            "cancellation_timeout_keys": ["stock_candidates"],
            "dependency_failures": {"stock_candidates": ["cache_bootstrap"]},
        }
    )
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": preload,
        "runtime_health_samples": [_completed_background_preload_network_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert failures >= {
        "runtime_health.background_preload.concurrency",
        "runtime_health.background_preload.clean_completion",
    }


def test_runtime_health_budget_fails_closed_without_background_preload_network_sample():
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [_runtime_health_sample(label="final")],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_hidden_background_preload_network_activity():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage"][0]["triggered_network"] = True
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.triggered_network" in failures


def test_runtime_health_budget_rejects_non_boolean_background_network_evidence():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage"][0]["triggered_network"] = "false"
    report = {
        "mode": {"background_prewarm": True, "tabs": ["stock_candidates"]},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures
    assert "runtime_health.data_lineage.boolean_fields" in failures


def test_runtime_health_budget_rejects_missing_background_network_field():
    sample = _completed_background_preload_network_sample()
    key = sample["data_lineage"][0]["key"]
    del sample["data_lineage"][0]["triggered_network"]
    report = {
        "mode": {"background_prewarm": True, "tabs": [key]},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures
    assert "runtime_health.data_lineage.fields" in failures


def test_runtime_health_budget_rejects_lineage_error():
    sample = _completed_background_preload_network_sample()
    key = sample["data_lineage"][0]["key"]
    sample["data_lineage"][0]["lineage_error"] = True
    report = {
        "mode": {"background_prewarm": True, "tabs": [key]},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures
    assert "runtime_health.data_lineage.lineage_error" in failures


def test_runtime_health_budget_rejects_incomplete_background_lineage_coverage():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage_coverage"]["covered"].pop()
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_missing_background_lineage_exclusion():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage_coverage"]["excluded"].clear()
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_duplicate_background_lineage_coverage():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage_coverage"]["covered"].append(sample["data_lineage_coverage"]["covered"][0])
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_unloaded_background_lineage_row():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage"][0]["loaded"] = False
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_overlapping_background_lineage_coverage():
    sample = _completed_background_preload_network_sample()
    sample["data_lineage_coverage"]["excluded"].append(sample["data_lineage_coverage"]["covered"][0])
    report = {
        "mode": {"background_prewarm": True},
        "background_preload": _completed_background_preload_report(),
        "runtime_health_samples": [sample],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.background_preload.network_evidence" in failures


def test_runtime_health_budget_rejects_incomplete_workload_phases():
    report = {
        "mode": {
            "f5_cycles": 1,
            "quote_cycles": 1,
            "post_tab_idle_timeout_ms": 5000,
        },
        "post_tab_idle": {"status": "timeout", "active_after": 1},
        "f5_cycle": {"status": "timeout", "cycles": 0},
        "quote_cycle": {"status": "timeout", "cycles": 0},
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert failures >= {
        "runtime_health.post_tab_idle.status",
        "runtime_health.f5_cycle.status",
        "runtime_health.f5_cycle.completion",
        "runtime_health.quote_cycle.status",
        "runtime_health.quote_cycle.completion",
    }


def test_runtime_health_post_tab_idle_failure_reports_phase_sample_and_task_context():
    report = {
        "mode": {"post_tab_idle_timeout_ms": 5000},
        "post_tab_idle": {
            "status": "timeout",
            "active_after": 2,
            "baseline_task_ids": ["preexisting"],
            "started_task_ids": ["tab_job"],
            "remaining_task_ids": ["tab_job"],
            "concurrent_startup_task_ids": ["asian_data_sync_bg"],
        },
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failure = next(
        item for item in check_runtime_health_budget(report) if item["check"] == "runtime_health.post_tab_idle.status"
    )

    assert failure["phase"] == "tab_async_tail"
    assert failure["sample"] == "post_tab_idle"
    assert failure["task_ids"] == ["tab_job"]
    assert failure["baseline_task_ids"] == ["preexisting"]
    assert failure["concurrent_startup_task_ids"] == ["asian_data_sync_bg"]


def _real_f5_runtime_report() -> dict:
    run_id = "a" * 32
    return {
        "mode": {"f5_probe_mode": "real_process", "f5_cycles": 1},
        "f5_cycle": {
            "status": "ok",
            "probe_mode": "real_process",
            "cycles": 1,
            "cycle_timings": [
                {
                    "cycle": 1,
                    "status": "ok",
                    "started": True,
                    "finished": True,
                    "elapsed_ms": 1.0,
                    "execution": "real_process",
                    "job_status": "succeeded",
                    "post_refresh_settled": True,
                    "parent_pid": 111,
                    "worker_pid": 222,
                    "run_id": run_id,
                    "snapshot_id": run_id,
                    "effective_trade_date": "20260715",
                    "symbol_count": 5122,
                    "rps_valid_count": 5121,
                    "sector_count": 377,
                    "event_count": 7,
                    "event_phases": [
                        "prepare",
                        "gbbq",
                        "market_sync",
                        "market_stage",
                        "rps",
                        "sector_rps",
                        "validate",
                    ],
                }
            ],
        },
    }


def test_runtime_health_real_f5_budget_accepts_complete_process_receipt():
    failures = []

    perf_budget_check._check_runtime_health_real_f5(_real_f5_runtime_report(), failures)

    assert failures == []


def test_runtime_health_real_f5_budget_requires_exact_ordered_cycle_receipts():
    missing = _real_f5_runtime_report()
    missing["mode"]["f5_cycles"] = 2
    missing["f5_cycle"]["cycles"] = 2

    wrong_cycle = _real_f5_runtime_report()
    wrong_cycle["f5_cycle"]["cycle_timings"][0]["cycle"] = 2

    missing_failures = []
    wrong_cycle_failures = []
    perf_budget_check._check_runtime_health_real_f5(missing, missing_failures)
    perf_budget_check._check_runtime_health_real_f5(wrong_cycle, wrong_cycle_failures)

    assert "runtime_health.f5_cycle.real_receipt" in {item["check"] for item in missing_failures}
    assert "runtime_health.f5_cycle.real_receipt" in {item["check"] for item in wrong_cycle_failures}


def test_runtime_health_real_f5_budget_rejects_same_pid_and_missing_proof():
    report = _real_f5_runtime_report()
    timing = report["f5_cycle"]["cycle_timings"][0]
    timing["worker_pid"] = timing["parent_pid"]
    timing["snapshot_id"] = ""
    timing["event_phases"] = ["prepare"]
    timing["event_count"] = 1
    failures = []

    perf_budget_check._check_runtime_health_real_f5(report, failures)

    assert [item["check"] for item in failures] == ["runtime_health.f5_cycle.real_receipt"]
    assert failures[0]["missing_phases"] == [
        "gbbq",
        "market_sync",
        "market_stage",
        "rps",
        "sector_rps",
        "validate",
    ]


def test_runtime_health_real_f5_budget_rejects_out_of_order_or_unknown_phases():
    out_of_order = _real_f5_runtime_report()
    phases = out_of_order["f5_cycle"]["cycle_timings"][0]["event_phases"]
    phases[1], phases[2] = phases[2], phases[1]
    unknown = _real_f5_runtime_report()
    unknown_timing = unknown["f5_cycle"]["cycle_timings"][0]
    unknown_timing["event_phases"].append("unexpected")
    unknown_timing["event_count"] += 1

    out_of_order_failures = []
    unknown_failures = []
    perf_budget_check._check_runtime_health_real_f5(out_of_order, out_of_order_failures)
    perf_budget_check._check_runtime_health_real_f5(unknown, unknown_failures)

    assert [item["check"] for item in out_of_order_failures] == ["runtime_health.f5_cycle.real_receipt"]
    assert [item["check"] for item in unknown_failures] == ["runtime_health.f5_cycle.real_receipt"]


def test_runtime_health_real_f5_budget_rejects_callback_only_probe():
    report = _real_f5_runtime_report()
    report["f5_cycle"]["probe_mode"] = "post_refresh_callback"
    failures = []

    perf_budget_check._check_runtime_health_real_f5(report, failures)

    assert [item["check"] for item in failures] == ["runtime_health.f5_cycle.real_mode"]


def test_runtime_health_budget_accepts_declared_non_data_tab_exclusion():
    report = {
        "startup_ready_ms": 900.0,
        "mode": {"tabs": ["stock_candidates", "system_log"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
                {"cycle": 1, "key": "system_log", "status": "ok", "elapsed_ms": 80.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage_coverage={
                    "covered": ["stock_candidates"],
                    "excluded": ["system_log"],
                },
                data_lineage_exclusions=[
                    {
                        "key": "system_log",
                        "title": "系统日志",
                        "group": "系统",
                        "loaded": True,
                        "class_name": "LogTab",
                        "reason": "non_data_tab",
                        "description": "Operational log surface, not a data source.",
                    }
                ],
                ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 0.0},
            )
        ],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_incomplete_lineage_exclusion_contract():
    report = {
        "mode": {"tabs": ["stock_candidates", "system_log", "unknown_control"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
                {"cycle": 1, "key": "system_log", "status": "ok", "elapsed_ms": 80.0},
                {"cycle": 1, "key": "unknown_control", "status": "ok", "elapsed_ms": 80.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage_coverage={
                    "covered": ["stock_candidates", "system_log"],
                    "excluded": ["system_log"],
                },
                data_lineage_exclusions=[],
            )
        ],
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.data_lineage.coverage.overlap",
        "runtime_health.data_lineage.coverage.requested_tabs",
        "runtime_health.data_lineage.exclusions.declared_tabs",
    }


def test_runtime_health_budget_rejects_malformed_or_undeclared_lineage_exclusions():
    report = {
        "mode": {"tabs": ["stock_candidates", "system_log"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
                {"cycle": 1, "key": "system_log", "status": "ok", "elapsed_ms": 80.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage_coverage={
                    "covered": ["stock_candidates"],
                    "excluded": ["system_log"],
                },
                data_lineage_exclusions=[
                    {"key": "system_log", "reason": "", "loaded": True},
                    {
                        "key": "ghost_tab",
                        "reason": "non_data_tab",
                        "description": "Not declared by coverage.",
                        "loaded": False,
                    },
                ],
            )
        ],
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.data_lineage.exclusions.undeclared_tabs",
        "runtime_health.data_lineage.exclusions.fields",
    }


def test_runtime_health_budget_accepts_clean_shutdown_and_keeps_old_reports_compatible():
    old_report = {"runtime_health_samples": [_runtime_health_sample()]}
    report = {
        **old_report,
        "shutdown": {
            "close_elapsed_ms": 42.5,
            "post_close": _runtime_health_post_close(),
        },
    }

    assert check_runtime_health_budget(old_report) == []
    assert check_runtime_health_budget(report) == []


def test_runtime_health_stability_suite_requires_shutdown_receipt():
    report = {
        "report_type": "runtime_health_stability_suite",
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.present" in failures


def test_runtime_health_budget_rejects_incomplete_post_close_cleanup():
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 45.0,
            "post_close": _runtime_health_post_close(
                task_manager_active_count=1,
                qthread_pool_active_count=2,
                pending_qthread_count=2,
                watchdog_running=True,
                f5_controller_running=True,
                webengine_child_count=1,
            ),
        },
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.shutdown.background_tasks",
        "runtime_health.shutdown.qthread_pool",
        "runtime_health.shutdown.pending_qthreads",
        "runtime_health.shutdown.watchdog",
        "runtime_health.shutdown.f5_controller",
        "runtime_health.shutdown.webengine",
    }


def test_runtime_health_budget_requires_workspace_background_preload_shutdown_diagnostics():
    post_close = _runtime_health_post_close()
    post_close["workspace_background_preload_diagnostics_available"] = False
    post_close["workspace_background_preload"] = None
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {"close_elapsed_ms": 45.0, "post_close": post_close},
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.background_preload.diagnostics" in failures


def test_runtime_health_budget_rejects_workspace_background_preload_shutdown_leaks():
    post_close = _runtime_health_post_close(
        workspace_background_preload=_workspace_background_preload_shutdown(
            active_key="stock_candidates",
            remaining_keys=["self_selection"],
            active_step_count=1,
            timer_active=True,
            cancellation_blocked=True,
        )
    )
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {"close_elapsed_ms": 45.0, "post_close": post_close},
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.background_preload.cleanup" in failures


def test_runtime_health_budget_requires_physically_settled_preload_shutdown_receipts():
    post_close = _runtime_health_post_close(
        workspace_background_preload=_workspace_background_preload_shutdown(
            shutdown_cancel_receipts=[
                {
                    "accepted": True,
                    "local_settled": True,
                    "settled": False,
                    "task_ids": ["preload:stock_candidates"],
                    "active_task_ids": ["preload:stock_candidates"],
                }
            ],
            shutdown_cancellation_settled=False,
        )
    )
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {"close_elapsed_ms": 45.0, "post_close": post_close},
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.background_preload.receipts" in failures


def test_runtime_health_budget_requires_f5_runtime_artifact_cleanup_receipt():
    missing = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 45.0,
            "post_close": _runtime_health_post_close(),
        },
    }
    missing["shutdown"]["post_close"].pop("f5_runtime_artifacts")
    leaky = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 45.0,
            "post_close": _runtime_health_post_close(
                f5_runtime_artifacts={
                    "clean": True,
                    "active_snapshot_id": "a" * 32,
                    "active_snapshot_complete": True,
                    "generation_count": 3,
                    "generation_limit": 2,
                    "unexpected_generation_ids": ["b" * 32],
                    "incomplete_generation_ids": [],
                    "invalid_generation_entries": [],
                    "unfinished_job_ids": ["c" * 32],
                    "ready_to_activate_job_ids": ["d" * 32],
                    "invalid_job_entries": [],
                    "invalid_job_ids": [],
                    "job_count": 3,
                    "terminal_job_count": 3,
                    "terminal_job_ids": ["e" * 32, "f" * 32, "1" * 32],
                    "temporary_file_count": 1,
                    "temporary_files": ["f5_generations/b/market.parquet.tmp"],
                }
            ),
        },
    }

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    leaky_failures = {item["check"] for item in check_runtime_health_budget(leaky)}

    assert "runtime_health.shutdown.f5_runtime_artifacts.diagnostics" in missing_failures
    assert leaky_failures >= {
        "runtime_health.shutdown.f5_runtime_artifacts.clean",
        "runtime_health.shutdown.f5_runtime_artifacts.generations",
        "runtime_health.shutdown.f5_runtime_artifacts.unexpected_generations",
        "runtime_health.shutdown.f5_runtime_artifacts.unfinished_jobs",
        "runtime_health.shutdown.f5_runtime_artifacts.ready_to_activate",
        "runtime_health.shutdown.f5_runtime_artifacts.temporary_files",
        "runtime_health.shutdown.f5_runtime_artifacts.terminal_jobs",
    }


def test_runtime_health_real_f5_requires_post_close_active_and_terminal_run_identity():
    report = {
        **_real_f5_runtime_report(),
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 45.0,
            "post_close": _runtime_health_post_close(),
        },
    }
    stale = {
        **report,
        "shutdown": {
            "close_elapsed_ms": 45.0,
            "post_close": _runtime_health_post_close(
                f5_runtime_artifacts={
                    **_runtime_health_post_close()["f5_runtime_artifacts"],
                    "active_snapshot_id": "b" * 32,
                    "job_count": 1,
                    "terminal_job_count": 1,
                    "terminal_job_ids": ["b" * 32],
                }
            ),
        },
    }

    report["shutdown"]["post_close"]["f5_runtime_artifacts"].update(
        {
            "job_count": 1,
            "terminal_job_count": 1,
            "terminal_job_ids": ["a" * 32],
        }
    )
    clean_failures = {item["check"] for item in check_runtime_health_budget(report)}
    stale_failures = {item["check"] for item in check_runtime_health_budget(stale)}

    assert "runtime_health.shutdown.f5_runtime_artifacts.active_identity" not in clean_failures
    assert stale_failures >= {
        "runtime_health.shutdown.f5_runtime_artifacts.active_identity",
        "runtime_health.shutdown.f5_runtime_artifacts.terminal_identity",
    }


def _complete_kline_stage_contract(cycles: int) -> dict:
    from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER

    timings = {stage: float(index + 1) for index, stage in enumerate(KLINE_OPEN_STAGE_ORDER)}
    return {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "complete": True,
        "cycles": [
            {
                "required_stages": list(KLINE_OPEN_STAGE_ORDER),
                "completed_stages": list(KLINE_OPEN_STAGE_ORDER),
                "pending_stages": [],
                "timings_ms": dict(timings),
                "complete": True,
            }
            for _ in range(cycles)
        ],
    }


def _clean_kline_open_ui_stalls(cycles: int) -> list[dict]:
    return [
        {
            "cycle_index": cycle_index,
            "ui_stalls": {
                "installed": True,
                "scope": "kline_open_to_chart_ready",
                "reset_succeeded": True,
                "critical_count": 0,
                "event_loop_critical_count": 0,
                "max_elapsed_ms": 80.0,
            },
        }
        for cycle_index in range(1, cycles + 1)
    ]


def _clean_requested_kline_report(*, prewarm: bool = False) -> dict:
    keeper_count = int(prewarm)
    return {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": prewarm},
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": keeper_count})],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "chart_ready",
            "cycles": 1,
            "opened": 1,
            "closed": 1,
            "blocked": 0,
            "prewarm": {"requested": prewarm, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": keeper_count,
            "final_webengine_child_count": keeper_count,
            "webengine_child_count_net_delta": 0,
            "baseline_managed_webengine_keeper_count": keeper_count,
            "final_managed_webengine_keeper_count": keeper_count,
            "managed_webengine_keeper_count": keeper_count,
            "active_chart_view_count_after_close": 0,
            "stage_contract": _complete_kline_stage_contract(1),
            "open_ui_stalls": _clean_kline_open_ui_stalls(1),
        },
        "shutdown": {
            "close_elapsed_ms": 40.0,
            "post_close": _runtime_health_post_close(),
        },
    }


def test_runtime_health_budget_requires_requested_kline_cycles_to_open_and_close():
    report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "chart_ready",
            "cycles": 1,
            "opened": 0,
            "closed": 0,
            "blocked": 1,
        },
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.kline_cycle.completion" for failure in failures)


def test_runtime_health_budget_accepts_clean_requested_kline_cycles():
    report = {
        "mode": {"kline_cycles": 2, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "chart_ready",
            "cycles": 2,
            "opened": 2,
            "closed": 2,
            "blocked": 0,
            "prewarm": {"requested": False, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": 0,
            "final_webengine_child_count": 0,
            "webengine_child_count_net_delta": 0,
            "baseline_managed_webengine_keeper_count": 0,
            "final_managed_webengine_keeper_count": 0,
            "managed_webengine_keeper_count": 0,
            "active_chart_view_count_after_close": 0,
            "stage_contract": _complete_kline_stage_contract(2),
            "open_ui_stalls": _clean_kline_open_ui_stalls(2),
        },
        "shutdown": {
            "close_elapsed_ms": 40.0,
            "post_close": _runtime_health_post_close(),
        },
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_requires_chart_ready_and_complete_six_stage_contract():
    from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER

    report = _clean_requested_kline_report()
    report["kline_cycle"]["open_success_criterion"] = "browser_ready"
    report["kline_cycle"].pop("stage_contract")
    failures = {item["check"] for item in check_runtime_health_budget(report)}
    assert "runtime_health.kline_cycle.open_criterion" in failures
    assert "runtime_health.kline_cycle.stage_contract" in failures

    timings = {stage: float(index + 1) for index, stage in enumerate(KLINE_OPEN_STAGE_ORDER)}
    report["kline_cycle"]["open_success_criterion"] = "chart_ready"
    report["kline_cycle"]["stage_contract"] = {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "complete": True,
        "cycles": [
            {
                "required_stages": list(KLINE_OPEN_STAGE_ORDER),
                "completed_stages": list(KLINE_OPEN_STAGE_ORDER),
                "pending_stages": [],
                "timings_ms": timings,
                "complete": True,
            }
        ],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_enforces_each_actual_kline_open_ui_stall():
    missing = _clean_requested_kline_report()
    missing["kline_cycle"].pop("open_ui_stalls")
    slow = _clean_requested_kline_report()
    slow["kline_cycle"]["open_ui_stalls"][0]["ui_stalls"].update(
        {
            "critical_count": 1,
            "event_loop_critical_count": 1,
            "max_elapsed_ms": 100.1,
        }
    )

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    slow_failures = {item["check"] for item in check_runtime_health_budget(slow)}

    assert "runtime_health.kline_cycle.ui_stall.diagnostics" in missing_failures
    assert {
        "runtime_health.kline_cycle.ui_stall.critical_count",
        "runtime_health.kline_cycle.ui_stall.event_loop_critical_count",
        "runtime_health.kline_cycle.ui_stall.max_elapsed",
    } <= slow_failures


def test_runtime_health_stability_kline_evidence_requires_native_visible_scope():
    report = _clean_requested_kline_report()
    report["report_type"] = "runtime_health_stability_suite"
    report["mode"].update(
        {
            "native_qt": False,
            "show_window": False,
            "qt_platform": "offscreen",
        }
    )

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.kline_cycle.execution_scope" in failures


def test_runtime_health_budget_rejects_missing_and_out_of_order_kline_stage_timings():
    from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER

    report = _clean_requested_kline_report()
    report["kline_cycle"]["open_success_criterion"] = "chart_ready"
    report["kline_cycle"]["stage_contract"] = {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "complete": True,
        "cycles": [
            {
                "required_stages": list(KLINE_OPEN_STAGE_ORDER),
                "completed_stages": list(KLINE_OPEN_STAGE_ORDER),
                "pending_stages": [],
                "timings_ms": {
                    "shell_ready": 1.0,
                    "browser_ready": 3.0,
                    "data_ready": 2.0,
                    "js_ready": 4.0,
                    "chart_ready": 5.0,
                },
                "complete": True,
            }
        ],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}
    assert "runtime_health.kline_cycle.stage_timings" in failures


def test_runtime_health_budget_accepts_one_managed_kline_webengine_until_shutdown():
    report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": True},
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 1})],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "chart_ready",
            "cycles": 1,
            "opened": 1,
            "closed": 1,
            "blocked": 0,
            "prewarm": {"requested": True, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": 1,
            "final_webengine_child_count": 1,
            "baseline_managed_webengine_keeper_count": 1,
            "final_managed_webengine_keeper_count": 1,
            "managed_webengine_keeper_count": 1,
            "active_chart_view_count_after_close": 0,
            "webengine_child_count_net_delta": 0,
            "stage_contract": _complete_kline_stage_contract(1),
            "open_ui_stalls": _clean_kline_open_ui_stalls(1),
        },
        "shutdown": {
            "close_elapsed_ms": 40.0,
            "post_close": _runtime_health_post_close(),
        },
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_unavailable_kline_cycle_diagnostics():
    report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "browser_ready",
            "cycles": 1,
            "opened": 1,
            "closed": 1,
            "blocked": 0,
            "prewarm": {"requested": False, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": False,
            "baseline_webengine_child_count": 0,
            "final_webengine_child_count": None,
            "webengine_child_count_net_delta": None,
            "baseline_managed_webengine_keeper_count": 0,
            "final_managed_webengine_keeper_count": 0,
            "managed_webengine_keeper_count": 0,
            "active_chart_view_count_after_close": 0,
        },
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.webengine.cycle_diagnostics" in failures


def test_runtime_health_budget_rejects_missing_or_invalid_active_chart_view_count():
    base_cycle = {
        "status": "ok",
        "open_success_criterion": "browser_ready",
        "cycles": 1,
        "opened": 1,
        "closed": 1,
        "blocked": 0,
        "prewarm": {"requested": False, "status": "ok"},
        "baseline_webengine_available": True,
        "final_webengine_available": True,
        "baseline_webengine_child_count": 0,
        "final_webengine_child_count": 0,
        "webengine_child_count_net_delta": 0,
        "baseline_managed_webengine_keeper_count": 0,
        "final_managed_webengine_keeper_count": 0,
        "managed_webengine_keeper_count": 0,
    }
    invalid_values = (None, "invalid", -1, True)

    for value in invalid_values:
        cycle = dict(base_cycle)
        cycle["active_chart_view_count_after_close"] = value
        report = {
            "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
            "runtime_health_samples": [_runtime_health_sample()],
            "kline_cycle": cycle,
        }
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.webengine.active_chart_views_diagnostics" in failures

    missing_report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": dict(base_cycle),
    }
    positive_report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {**base_cycle, "active_chart_view_count_after_close": 1},
    }

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing_report)}
    positive_failures = {item["check"] for item in check_runtime_health_budget(positive_report)}
    assert "runtime_health.webengine.active_chart_views_diagnostics" in missing_failures
    assert "runtime_health.webengine.active_chart_views_final" in positive_failures


def test_runtime_health_budget_requires_requested_keeper_before_cycle_baseline():
    report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": True},
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 1})],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "browser_ready",
            "cycles": 1,
            "opened": 1,
            "closed": 1,
            "blocked": 0,
            "prewarm": {"requested": True, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": 0,
            "final_webengine_child_count": 1,
            "webengine_child_count_net_delta": 1,
            "baseline_managed_webengine_keeper_count": 0,
            "final_managed_webengine_keeper_count": 1,
            "managed_webengine_keeper_count": 1,
        },
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.webengine.keeper_stability" in failures
    assert "runtime_health.webengine.requested_keeper_ready" in failures


def test_runtime_health_budget_requires_shutdown_for_requested_kline_cycles():
    report = {
        "mode": {"kline_cycles": 1, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {
            "status": "ok",
            "open_success_criterion": "browser_ready",
            "cycles": 1,
            "opened": 1,
            "closed": 1,
            "blocked": 0,
            "prewarm": {"requested": False, "status": "ok"},
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": 0,
            "final_webengine_child_count": 0,
            "webengine_child_count_net_delta": 0,
            "baseline_managed_webengine_keeper_count": 0,
            "final_managed_webengine_keeper_count": 0,
            "managed_webengine_keeper_count": 0,
            "active_chart_view_count_after_close": 0,
        },
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.present" in failures


def test_runtime_health_budget_rejects_mode_result_kline_cycle_mismatch():
    report = {
        "mode": {"kline_cycles": 0, "kline_prewarm_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
        "kline_cycle": {"status": "ok", "cycles": 1, "opened": 1, "closed": 1, "blocked": 0},
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.kline_cycle.mode_consistency" in failures


def test_runtime_health_budget_requires_strict_kline_mode_and_execution_counts():
    for invalid in (None, True, 1.0, "1", -1):
        report = _clean_requested_kline_report()
        report["mode"]["kline_cycles"] = invalid
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.kline_cycle.requested" in failures

    missing_mode_count = _clean_requested_kline_report()
    missing_mode_count["mode"].pop("kline_cycles")
    failures = {item["check"] for item in check_runtime_health_budget(missing_mode_count)}
    assert "runtime_health.kline_cycle.requested" in failures

    for field in ("cycles", "opened", "closed", "blocked"):
        for invalid in (None, True, 1.0, "1", -1):
            report = _clean_requested_kline_report()
            report["kline_cycle"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert "runtime_health.kline_cycle.completion" in failures

        missing = _clean_requested_kline_report()
        missing["kline_cycle"].pop(field)
        failures = {item["check"] for item in check_runtime_health_budget(missing)}
        assert "runtime_health.kline_cycle.completion" in failures

    missing_status = _clean_requested_kline_report()
    missing_status["kline_cycle"].pop("status")
    failures = {item["check"] for item in check_runtime_health_budget(missing_status)}
    assert "runtime_health.kline_cycle.completion" in failures


def test_runtime_health_budget_requires_strict_prewarm_and_keeper_contract():
    for invalid in (None, 0, 1, "false"):
        report = _clean_requested_kline_report()
        report["mode"]["kline_prewarm_enabled"] = invalid
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.webengine.prewarm_mode" in failures

    mismatch = _clean_requested_kline_report()
    mismatch["kline_cycle"]["prewarm"]["requested"] = True
    failures = {item["check"] for item in check_runtime_health_budget(mismatch)}
    assert "runtime_health.webengine.prewarm_consistency" in failures

    keeper_fields = (
        "baseline_managed_webengine_keeper_count",
        "final_managed_webengine_keeper_count",
        "managed_webengine_keeper_count",
    )
    for field in keeper_fields:
        for invalid in (None, True, 0.0, "0", -1):
            report = _clean_requested_kline_report()
            report["kline_cycle"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert "runtime_health.webengine.keeper_diagnostics" in failures

        missing = _clean_requested_kline_report()
        missing["kline_cycle"].pop(field)
        failures = {item["check"] for item in check_runtime_health_budget(missing)}
        assert "runtime_health.webengine.keeper_diagnostics" in failures

    overflow = _clean_requested_kline_report(prewarm=True)
    overflow["kline_cycle"]["managed_webengine_keeper_count"] = 2
    failures = {item["check"] for item in check_runtime_health_budget(overflow)}
    assert "runtime_health.webengine.managed_keeper_limit" in failures
    assert "runtime_health.webengine.keeper_consistency" in failures

    unexpected = _clean_requested_kline_report()
    for field in keeper_fields:
        unexpected["kline_cycle"][field] = 1
    failures = {item["check"] for item in check_runtime_health_budget(unexpected)}
    assert "runtime_health.webengine.unexpected_keeper" in failures


def test_runtime_health_budget_requires_strict_webengine_cycle_and_final_diagnostics():
    for field in ("baseline_webengine_child_count", "final_webengine_child_count"):
        for invalid in (None, True, 0.0, "0", -1):
            report = _clean_requested_kline_report()
            report["kline_cycle"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert "runtime_health.webengine.cycle_diagnostics" in failures

    for field in ("baseline_webengine_available", "final_webengine_available"):
        for invalid in (None, False, 1, "true"):
            report = _clean_requested_kline_report()
            report["kline_cycle"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert "runtime_health.webengine.cycle_diagnostics" in failures

    for invalid in (None, True, 0.0, "0"):
        report = _clean_requested_kline_report()
        report["kline_cycle"]["webengine_child_count_net_delta"] = invalid
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.webengine.cycle_diagnostics" in failures

    mismatched_delta = _clean_requested_kline_report()
    mismatched_delta["kline_cycle"]["webengine_child_count_net_delta"] = -1
    failures = {item["check"] for item in check_runtime_health_budget(mismatched_delta)}
    assert "runtime_health.webengine.cycle_delta" in failures

    growth = _clean_requested_kline_report()
    growth["kline_cycle"]["final_webengine_child_count"] = 1
    growth["kline_cycle"]["webengine_child_count_net_delta"] = 1
    failures = {item["check"] for item in check_runtime_health_budget(growth)}
    assert "runtime_health.webengine.child_growth" in failures

    for invalid in (None, True, "0", -1.0):
        report = _clean_requested_kline_report()
        report["budget_trend"] = {"webengine_children": {"last": invalid}}
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.webengine.final_diagnostics" in failures


def test_runtime_health_budget_requires_strict_kline_shutdown_diagnostics():
    for availability in (None, False, 1, "true"):
        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"]["webengine_available"] = availability
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.shutdown.webengine_available" in failures

    for child_count in (None, True, 0.0, "0", -1, 1):
        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"]["webengine_child_count"] = child_count
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.shutdown.webengine" in failures

    missing_post_close = _clean_requested_kline_report()
    missing_post_close["shutdown"].pop("post_close")
    failures = {item["check"] for item in check_runtime_health_budget(missing_post_close)}
    assert "runtime_health.shutdown.post_close" in failures


def test_runtime_health_budget_requires_strict_kline_manager_shutdown_receipt():
    for availability in (None, False, 0, 1, "true"):
        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics_available"] = availability
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.shutdown.kline_manager.diagnostics" in failures

    for invalid in (None, [], "clean", {}):
        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics"] = invalid
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.shutdown.kline_manager.diagnostics" in failures

    for field in (
        "clean",
        "active_close_clean",
        "pooled_dispose_clean",
        "prewarm_dispose_clean",
        "return_timer_clean",
        "idle_guard_clean",
        "preflight_clean",
    ):
        for invalid in (None, False, 0, "true"):
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert f"runtime_health.shutdown.kline_manager.{field}" in failures

    for field in ("active_windows", "managed_keepers"):
        for invalid in (None, True, 0.0, "0", -1, 1):
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert f"runtime_health.shutdown.kline_manager.{field}" in failures

    for field in ("pending_open", "prewarm_main_window_retained"):
        for invalid in (None, True, 0, "false"):
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert f"runtime_health.shutdown.kline_manager.{field}" in failures


def test_perf_budget_cli_fails_on_kline_manager_shutdown_strong_ref(tmp_path, capsys):
    report = _clean_requested_kline_report()
    report["shutdown"]["post_close"]["kline_manager_shutdown_diagnostics"]["managed_keepers"] = 1
    report_path = tmp_path / "runtime_health.json"
    report_path.write_text(perf_budget_check.json.dumps(report), encoding="utf-8")

    assert perf_budget_check.main(["--runtime-health-report", str(report_path)]) == 1
    assert "runtime_health.shutdown.kline_manager.managed_keepers" in capsys.readouterr().out


def test_runtime_health_budget_requires_strict_owned_shutdown_receipts():
    count_fields = (
        (
            "task_manager_active_count",
            "task_manager_diagnostics_available",
            "runtime_health.shutdown.background_tasks",
        ),
        (
            "qthread_pool_active_count",
            "qthread_pool_diagnostics_available",
            "runtime_health.shutdown.qthread_pool",
        ),
        (
            "pending_qthread_count",
            "pending_qthread_diagnostics_available",
            "runtime_health.shutdown.pending_qthreads",
        ),
    )
    for field, availability_field, check in count_fields:
        invalid_values = (None, True, 0.0, "0", -1, 1)
        for invalid in invalid_values:
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert check in failures

        missing = _clean_requested_kline_report()
        missing["shutdown"]["post_close"].pop(field)
        failures = {item["check"] for item in check_runtime_health_budget(missing)}
        assert check in failures

        for unavailable in (None, False, 0, "true"):
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"][availability_field] = unavailable
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert f"{check}.diagnostics" in failures

    for field, availability_field, check in (
        (
            "watchdog_running",
            "watchdog_diagnostics_available",
            "runtime_health.shutdown.watchdog",
        ),
        (
            "f5_controller_running",
            "f5_controller_diagnostics_available",
            "runtime_health.shutdown.f5_controller",
        ),
    ):
        for invalid in (None, True, 0, "false"):
            report = _clean_requested_kline_report()
            report["shutdown"]["post_close"][field] = invalid
            failures = {item["check"] for item in check_runtime_health_budget(report)}
            assert check in failures

        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"][availability_field] = False
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert f"{check}.diagnostics" in failures

    for invalid in (None, 0, 1, "false"):
        report = _clean_requested_kline_report()
        report["shutdown"]["post_close"]["f5_controller_present"] = invalid
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.shutdown.f5_controller_present" in failures


def test_runtime_health_budget_fails_closed_when_background_diagnostics_are_unavailable():
    for background_tasks in (
        None,
        [],
        {"available": False, "count": None},
        {"available": True, "count": "0"},
    ):
        report = {"runtime_health_samples": [_runtime_health_sample(background_tasks=background_tasks)]}
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert failures & {
            "runtime_health.background_tasks.diagnostics",
            "runtime_health.background_tasks.count",
        }


def test_runtime_health_budget_rejects_keeper_overflow_and_child_process_growth():
    overflow_report = {
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 2})],
        "kline_cycle": {
            "managed_webengine_keeper_count": 2,
            "active_chart_view_count_after_close": 0,
            "webengine_child_count_net_delta": 0,
        },
    }
    growth_report = {
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 2})],
        "kline_cycle": {
            "managed_webengine_keeper_count": 1,
            "active_chart_view_count_after_close": 0,
            "baseline_webengine_available": True,
            "final_webengine_available": True,
            "baseline_webengine_child_count": 1,
            "final_webengine_child_count": 2,
            "webengine_child_count_net_delta": 1,
        },
    }
    active_view_report = {
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 1})],
        "kline_cycle": {
            "managed_webengine_keeper_count": 1,
            "active_chart_view_count_after_close": 1,
            "webengine_child_count_net_delta": 0,
        },
    }
    process_bound_report = {
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 3})],
        "kline_cycle": {
            "managed_webengine_keeper_count": 1,
            "active_chart_view_count_after_close": 0,
            "webengine_child_count_net_delta": 0,
        },
    }

    overflow_failures = {item["check"] for item in check_runtime_health_budget(overflow_report)}
    growth_failures = {item["check"] for item in check_runtime_health_budget(growth_report)}
    active_view_failures = {item["check"] for item in check_runtime_health_budget(active_view_report)}
    process_bound_failures = {item["check"] for item in check_runtime_health_budget(process_bound_report)}

    assert "runtime_health.webengine.managed_keeper_limit" in overflow_failures
    assert "runtime_health.webengine.child_growth" in growth_failures
    assert "runtime_health.webengine.active_chart_views_final" in active_view_failures
    assert "runtime_health.webengine.managed_process_bound" in process_bound_failures


def test_runtime_health_budget_rejects_raw_webengine_process_after_shutdown_with_managed_pool():
    report = {
        "runtime_health_samples": [_runtime_health_sample(webengine={"count": 1})],
        "kline_cycle": {
            "managed_webengine_keeper_count": 1,
            "active_chart_view_count_after_close": 0,
            "webengine_child_count_net_delta": 0,
        },
        "shutdown": {
            "close_elapsed_ms": 40.0,
            "post_close": _runtime_health_post_close(webengine_child_count=1),
        },
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.webengine" in failures


def test_runtime_health_budget_rejects_unavailable_shutdown_webengine_diagnostics():
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 40.0,
            "post_close": _runtime_health_post_close(
                webengine_available=False,
                webengine_child_count=None,
            ),
        },
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.shutdown.webengine_available" in failures
    assert "runtime_health.shutdown.webengine" in failures


def test_runtime_health_budget_rejects_shutdown_over_five_seconds():
    report = {
        "runtime_health_samples": [_runtime_health_sample()],
        "shutdown": {
            "close_elapsed_ms": 5000.1,
            "post_close": _runtime_health_post_close(),
        },
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.shutdown.close_elapsed" for failure in failures)


def test_runtime_health_budget_accepts_controlled_probe_skipped_tabs():
    fund_lineage = {
        "key": "fund_holdings",
        "title": "基金持仓",
        "source": "fund_holdings_store",
        "cache_refs": ["data/fund_holdings.db"],
        "network_capable": True,
        "triggered_network": False,
        "fallback_or_degraded": False,
        "loaded": False,
    }
    report = {
        "startup_ready_ms": 900.0,
        "mode": {"tabs": ["fund_holdings"]},
        "tab_cycle": {
            "tabs": [
                {
                    "cycle": 1,
                    "key": "fund_holdings",
                    "status": "skipped_controlled_probe",
                    "elapsed_ms": 0.0,
                },
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage=[fund_lineage],
                ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 0.0},
            ),
        ],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_slow_startup_ready():
    report = {
        "startup_ready_ms": 1900.0,
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.startup.ready" for failure in failures)


def test_runtime_health_budget_rejects_slow_initial_tab_ready():
    report = {
        "startup_ready_ms": 900.0,
        "initial_tab_ready_ms": 3100.0,
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.startup.initial_tab_ready" for failure in failures)


def test_perf_budget_cli_registers_startup_thresholds(capsys):
    args = perf_budget_check._parse_args(
        [
            "--runtime-health-initial-tab-ready-max-ms",
            "1234",
            "--runtime-health-inclusive-first-paint-max-ms",
            "2345",
            "--runtime-health-inclusive-initial-tab-ready-max-ms",
            "3456",
        ]
    )

    assert args.runtime_health_initial_tab_ready_max_ms == 1234.0
    assert args.runtime_health_inclusive_first_paint_max_ms == 2345.0
    assert args.runtime_health_inclusive_initial_tab_ready_max_ms == 3456.0
    assert perf_budget_check.main(["--runtime-health-initial-tab-ready-max-ms", "1234"]) == 0
    assert "No performance reports were provided." in capsys.readouterr().out


def _production_initial_tab_report(**overrides):
    samples = [_runtime_health_sample()]
    report = {
        "mode": {
            "native_qt": True,
            "show_window": True,
            "startup_enabled": True,
            "idle_seconds": 5,
            "post_tab_idle_timeout_ms": 5000,
        },
        "window_visibility": {
            "required": True,
            "status": "ok",
            "planned_observation_seconds": 5,
            "actual_observation_seconds": 5,
            "first_invisible_at_seconds": None,
            "first_invisible_reason": None,
        },
        "startup_ready_ms": 600.0,
        "initial_tab_ready_ms": 900.0,
        "startup_inclusive_first_paint_ms": 1600.0,
        "startup_inclusive_initial_tab_ready_ms": 1900.0,
        "startup_app_init_first_paint_ms": 1200.0,
        "startup_app_init_initial_tab_ready_ms": 1500.0,
        "startup_timing": {
            "scope": {
                "clock": "time.perf_counter",
                "script_module_origin": "script_module_after_time_import",
                "application_origin": "run_suite_entry_before_qapplication",
                "includes_python_interpreter_startup": False,
                "includes_process_creation": False,
                "includes_qt_runtime_configuration": True,
                "includes_qapplication_initialization": True,
                "includes_native_dataframe_runtime_initialization": True,
                "includes_search_filter_runtime_initialization": True,
                "window_only_metrics_preserved": True,
            }
        },
        "native_dataframe_runtime": {
            "included_in_script_module_inclusive_startup_timing": True,
            "included_in_application_initialization_startup_timing": True,
        },
        "search_filter_runtime": {
            "ready": True,
            "initialization_ms": 280.0,
            "excluded_from_window_only_startup_timing": True,
            "included_in_script_module_inclusive_startup_timing": True,
            "included_in_application_initialization_startup_timing": True,
        },
        "initial_tab_loaded": True,
        "initial_tab_ready": True,
        "initial_tab_status": "ok",
        "startup_task_settle": _completed_startup_task_settle(),
        "post_tab_idle": _completed_post_tab_idle(),
        "ui_stall_sampling": _completed_ui_stall_sampling(),
        "runtime_health_samples": samples,
        "budget_trend": _resource_trend(samples),
    }
    report.update(overrides)
    return report


def _production_full_mode() -> dict:
    return {
        "native_qt": True,
        "show_window": True,
        "startup_enabled": True,
        "background_prewarm": True,
        "central_quotes_enabled": True,
        "kline_prewarm_enabled": True,
        "f5_probe_mode": "real_process",
        "tabs": list(health_probe_tab_keys()),
        "tab_cycles": 1,
        "f5_cycles": 1,
        "quote_cycles": 1,
        "kline_cycles": 1,
        "post_tab_idle_timeout_ms": 5000,
    }


def _production_full_gate_report(**overrides) -> dict:
    samples = [_runtime_health_sample()]
    report = {
        "report_type": "runtime_health_stability_suite",
        "validation_profile": "production_full",
        "mode": _production_full_mode(),
        "runtime_health_samples": samples,
        "budget_trend": _resource_trend(samples),
        "ui_stall_sampling": _completed_ui_stall_sampling(),
        "unhandled_ui_exceptions": [],
    }
    report.update(overrides)
    return report


def test_runtime_health_budget_accepts_complete_production_initial_tab_contract():
    assert check_runtime_health_budget(_production_initial_tab_report()) == []


def test_runtime_health_production_resources_fail_closed_without_ui_stalls_or_rss():
    missing_stalls = _production_initial_tab_report()
    missing_stalls["runtime_health_samples"][0].pop("ui_stalls")
    missing_rss = _production_initial_tab_report()
    missing_rss["runtime_health_samples"][0]["process"].pop("rss_mb")

    stall_failures = {item["check"] for item in check_runtime_health_budget(missing_stalls)}
    rss_failures = {item["check"] for item in check_runtime_health_budget(missing_rss)}

    assert "runtime_health.ui_stall.diagnostics" in stall_failures
    assert "runtime_health.resources.sample_diagnostics" in rss_failures


def test_runtime_health_production_resource_trend_rejects_missing_or_nan_fields():
    missing = _production_initial_tab_report()
    missing["budget_trend"].pop("threads")
    nonfinite = _production_initial_tab_report()
    nonfinite["budget_trend"]["rss_mb"]["last"] = float("nan")

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    nonfinite_failures = {item["check"] for item in check_runtime_health_budget(nonfinite)}

    assert "runtime_health.resources.trend_diagnostics" in missing_failures
    assert "runtime_health.resources.trend_diagnostics" in nonfinite_failures


def test_runtime_health_production_requires_complete_phase_local_ui_stall_boundaries():
    missing = _production_initial_tab_report()
    missing.pop("ui_stall_sampling")
    invalid = _production_initial_tab_report()
    invalid["ui_stall_sampling"]["scope"] = "global"
    invalid["ui_stall_sampling"]["phase_boundaries"].pop(3)

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    invalid_failures = {item["check"] for item in check_runtime_health_budget(invalid)}

    assert "runtime_health.ui_stall_sampling.diagnostics" in missing_failures
    assert "runtime_health.ui_stall_sampling.diagnostics" in invalid_failures


def test_runtime_health_production_full_profile_requires_exact_mode_contract():
    missing_profile = _production_full_gate_report()
    missing_profile.pop("validation_profile")
    missing_flag = _production_full_gate_report()
    missing_flag["mode"].pop("central_quotes_enabled")
    wrong_tabs = _production_full_gate_report()
    wrong_tabs["mode"]["tabs"] = list(reversed(health_probe_tab_keys()))

    for report in (missing_profile, missing_flag, wrong_tabs):
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.production_profile" in failures


def test_runtime_health_production_profile_uses_health_probe_registry_order():
    failures = {item["check"] for item in check_runtime_health_budget(_production_full_gate_report())}

    assert "runtime_health.production_profile" not in failures


def test_runtime_health_checker_keeps_probe_and_preload_registry_orders_separate():
    assert perf_budget_check.RUNTIME_HEALTH_PROBE_ORDER == tuple(health_probe_tab_keys())
    assert perf_budget_check.RUNTIME_HEALTH_BACKGROUND_PRELOAD_ORDER == tuple(startup_tab_keys())


def test_runtime_health_production_tab_cycle_requires_exact_registry_matrix():
    keys = list(health_probe_tab_keys())
    report = _production_full_gate_report(
        tab_cycle={
            "status": "ok",
            "cycles": 1,
            "visited": len(keys),
            "tabs": [
                {"cycle": 1, "key": key, "status": "ok", "elapsed_ms": 1.0}
                for key in reversed(keys)
            ],
        }
    )

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.tab_cycle.production_contract" in failures


def test_runtime_health_production_tab_cycle_accepts_health_probe_registry_matrix():
    keys = list(health_probe_tab_keys())
    report = _production_full_gate_report(
        tab_cycle={
            "status": "ok",
            "cycles": 1,
            "visited": len(keys),
            "tabs": [
                {"cycle": 1, "key": key, "status": "ok", "elapsed_ms": 1.0}
                for key in keys
            ],
        }
    )

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.tab_cycle.production_contract" not in failures


def test_runtime_health_production_quote_cycle_requires_each_typed_timing():
    report = _production_full_gate_report(
        quote_cycle={"status": "ok", "cycles": 1, "cycle_timings": []}
    )

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.quote_cycle.receipts" in failures


def test_runtime_health_production_shutdown_requires_zero_settled_qthreads():
    post_close = _runtime_health_post_close(pending_qthread_count=1)
    report = _production_full_gate_report(
        shutdown={
            "close_elapsed_ms": 1.0,
            "pending_qthread_settle_ok": False,
            "post_close": post_close,
        }
    )

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert failures >= {
        "runtime_health.shutdown.pending_qthread_settle",
        "runtime_health.shutdown.pending_qthreads",
    }


def test_runtime_health_suite_core_checker_rejects_missing_or_unhandled_ui_exceptions():
    missing = _production_full_gate_report()
    missing.pop("unhandled_ui_exceptions")
    unhandled = _production_full_gate_report(unhandled_ui_exceptions=[{"type": "RuntimeError"}])

    for report in (missing, unhandled):
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.unhandled_ui_exceptions" in failures


def test_runtime_health_budget_fails_closed_for_missing_show_window_visibility_evidence():
    report = _production_initial_tab_report()
    report.pop("window_visibility")

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.window_visibility.evidence" in failures


def test_runtime_health_budget_rejects_invisible_or_incomplete_show_window_soak():
    invisible = _production_initial_tab_report()
    invisible["window_visibility"].update(
        {
            "status": "not_visible",
            "actual_observation_seconds": 2,
            "first_invisible_at_seconds": 2,
            "first_invisible_reason": "window_not_visible",
        }
    )
    incomplete = _production_initial_tab_report()
    incomplete["window_visibility"]["actual_observation_seconds"] = 4

    invisible_failures = {item["check"] for item in check_runtime_health_budget(invisible)}
    incomplete_failures = {item["check"] for item in check_runtime_health_budget(incomplete)}

    assert invisible_failures >= {
        "runtime_health.window_visibility.status",
        "runtime_health.window_visibility.duration",
    }
    assert "runtime_health.window_visibility.duration" in incomplete_failures


def test_runtime_health_budget_enforces_fixed_soak_duration_and_sampling_cadence():
    short = _production_initial_tab_report()
    short["mode"].update(
        {
            "mode": "soak30",
            "idle_seconds": 60,
            "sample_every_seconds": 60,
        }
    )
    short["window_visibility"].update(
        {
            "planned_observation_seconds": 60,
            "actual_observation_seconds": 60,
        }
    )
    short["runtime_health_samples"] = [
        _runtime_health_sample(label="startup"),
        _runtime_health_sample(label="idle:60s"),
    ]

    sparse = _production_initial_tab_report()
    sparse["mode"].update(
        {
            "mode": "soak30",
            "idle_seconds": 1800,
            "sample_every_seconds": 60,
        }
    )
    sparse["window_visibility"].update(
        {
            "planned_observation_seconds": 1800,
            "actual_observation_seconds": 1800,
        }
    )
    sparse["runtime_health_samples"] = [
        _runtime_health_sample(label="startup"),
        _runtime_health_sample(label="idle:60s"),
        _runtime_health_sample(label="idle:1800s"),
    ]

    short_failures = {item["check"] for item in check_runtime_health_budget(short)}
    sparse_failures = {item["check"] for item in check_runtime_health_budget(sparse)}

    assert "runtime_health.soak.minimum_duration" in short_failures
    assert "runtime_health.soak.sampling" in sparse_failures


def test_runtime_health_budget_accepts_complete_thirty_minute_soak_sampling_contract():
    report = _production_initial_tab_report()
    report["mode"].update(
        {
            "mode": "soak30",
            "idle_seconds": 1800,
            "sample_every_seconds": 60,
        }
    )
    report["window_visibility"].update(
        {
            "planned_observation_seconds": 1800,
            "actual_observation_seconds": 1800,
        }
    )
    report["runtime_health_samples"] = [
        _runtime_health_sample(label="startup"),
        *[
            _runtime_health_sample(label=f"idle:{elapsed}s")
            for elapsed in range(60, 1801, 60)
        ],
    ]
    report["budget_trend"] = _resource_trend(report["runtime_health_samples"])

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert not {check for check in failures if check.startswith("runtime_health.soak.")}


def test_runtime_health_budget_rejects_missing_search_filter_runtime_evidence():
    missing = _production_initial_tab_report()
    missing.pop("search_filter_runtime")
    unready = _production_initial_tab_report()
    unready["search_filter_runtime"]["ready"] = False
    nonfinite = _production_initial_tab_report()
    nonfinite["search_filter_runtime"]["initialization_ms"] = float("nan")
    missing_inclusion = _production_initial_tab_report()
    missing_inclusion["search_filter_runtime"].pop("included_in_application_initialization_startup_timing")

    for report in (missing, unready, nonfinite, missing_inclusion):
        failures = {item["check"] for item in check_runtime_health_budget(report)}
        assert "runtime_health.startup.search_filter_runtime" in failures

    missing_scope = _production_initial_tab_report()
    missing_scope["startup_timing"]["scope"].pop("includes_search_filter_runtime_initialization")
    failures = {item["check"] for item in check_runtime_health_budget(missing_scope)}
    assert "runtime_health.startup.inclusive_scope" in failures


def test_runtime_health_budget_rejects_missing_or_invalid_inclusive_startup_timing():
    missing = _production_initial_tab_report()
    missing.pop("startup_inclusive_first_paint_ms")
    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    nan_failures = {
        item["check"]
        for item in check_runtime_health_budget(
            _production_initial_tab_report(startup_inclusive_initial_tab_ready_ms=float("nan"))
        )
    }
    negative_failures = {
        item["check"]
        for item in check_runtime_health_budget(_production_initial_tab_report(startup_app_init_first_paint_ms=-1.0))
    }

    assert "runtime_health.startup.inclusive_first_paint.diagnostics" in missing_failures
    assert "runtime_health.startup.inclusive_initial_tab_ready.diagnostics" in nan_failures
    assert "runtime_health.startup.app_init_first_paint.diagnostics" in negative_failures


def test_runtime_health_budget_treats_script_module_startup_timing_as_diagnostic_only():
    failures = {
        item["check"]
        for item in check_runtime_health_budget(
            _production_initial_tab_report(
                startup_inclusive_first_paint_ms=3500.1,
                startup_inclusive_initial_tab_ready_ms=5500.1,
            )
        )
    }

    assert not failures.intersection(
        {
            "runtime_health.startup.inclusive_first_paint",
            "runtime_health.startup.inclusive_initial_tab_ready",
            "runtime_health.startup.app_init_first_paint",
            "runtime_health.startup.app_init_initial_tab_ready",
        }
    )


def test_runtime_health_budget_rejects_app_init_startup_over_budget():
    failures = {
        item["check"]
        for item in check_runtime_health_budget(
            _production_initial_tab_report(
                startup_inclusive_first_paint_ms=4000.0,
                startup_inclusive_initial_tab_ready_ms=6000.0,
                startup_app_init_first_paint_ms=3500.1,
                startup_app_init_initial_tab_ready_ms=5500.1,
            )
        )
    }

    assert failures >= {
        "runtime_health.startup.app_init_first_paint",
        "runtime_health.startup.app_init_initial_tab_ready",
    }


def test_runtime_health_budget_rejects_false_interpreter_scope_or_reversed_scope_order():
    report = _production_initial_tab_report(startup_app_init_first_paint_ms=1700.0)
    report["startup_timing"]["scope"]["includes_python_interpreter_startup"] = True

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.startup.inclusive_scope" in failures
    assert "runtime_health.startup.first_paint.scope_order" in failures


def test_runtime_health_budget_rejects_missing_or_nonfinite_production_initial_tab_timing():
    missing = _production_initial_tab_report()
    missing.pop("initial_tab_ready_ms")

    missing_failures = {item["check"] for item in check_runtime_health_budget(missing)}
    nan_failures = {
        item["check"]
        for item in check_runtime_health_budget(_production_initial_tab_report(initial_tab_ready_ms=float("nan")))
    }
    negative_failures = {
        item["check"] for item in check_runtime_health_budget(_production_initial_tab_report(initial_tab_ready_ms=-1.0))
    }

    assert "runtime_health.startup.initial_tab_ready.diagnostics" in missing_failures
    assert "runtime_health.startup.initial_tab_ready.diagnostics" in nan_failures
    assert "runtime_health.startup.initial_tab_ready.diagnostics" in negative_failures


def test_runtime_health_budget_rejects_unready_production_initial_tab():
    failures = {
        item["check"]
        for item in check_runtime_health_budget(
            _production_initial_tab_report(
                initial_tab_loaded=False,
                initial_tab_ready=False,
                initial_tab_status="data_timeout",
            )
        )
    }

    assert failures >= {
        "runtime_health.startup.initial_tab_loaded",
        "runtime_health.startup.initial_tab_status",
    }

    missing_readiness = _production_initial_tab_report()
    missing_readiness.pop("initial_tab_ready")
    missing_failures = {item["check"] for item in check_runtime_health_budget(missing_readiness)}
    assert "runtime_health.startup.initial_tab_status" in missing_failures


def test_runtime_health_budget_keeps_legacy_nonproduction_startup_optional():
    report = {
        "mode": {"native_qt": False, "show_window": False, "startup_enabled": False},
        "runtime_health_samples": [_runtime_health_sample()],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_contaminated_startup_task_settle():
    report = {
        "startup_task_settle": {"contaminated": True, "task_id": "smart_startup", "timeout_ms": 3000},
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.startup.task_settle" for failure in failures)


def test_runtime_health_budget_fails_closed_for_invalid_startup_task_settle_receipt():
    receipt = _completed_startup_task_settle()
    receipt["active_after"] = "0"
    receipt["remaining_task_ids"] = ()
    receipt.pop("observed_task_ids")
    report = {
        "mode": {"startup_enabled": True},
        "startup_task_settle": receipt,
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.startup.task_settle" in failures


def test_runtime_health_budget_fails_closed_for_invalid_post_tab_idle_receipt():
    receipt = _completed_post_tab_idle()
    receipt["task_id_diagnostics_available"] = False
    receipt["ownership"] = "global_task_count"
    receipt["active_after"] = 1
    receipt["remaining_task_ids"] = ()
    report = {
        "mode": {"post_tab_idle_timeout_ms": 5000},
        "post_tab_idle": receipt,
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.post_tab_idle.contract" in failures


def test_runtime_health_budget_accepts_live_baseline_tasks_after_phase_owned_tasks_settle():
    report = {
        "mode": {"post_tab_idle_timeout_ms": 5000},
        "post_tab_idle": _completed_post_tab_idle_with_live_baseline(),
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert not {check for check in failures if check.startswith("runtime_health.post_tab_idle")}


@pytest.mark.parametrize(
    "mutation",
    [
        {"active_after": 1},
        {"remaining_task_ids": ["tab_job"]},
        {"started_task_ids": ["tab_job", "tab_job"]},
    ],
)
def test_runtime_health_budget_rejects_inconsistent_post_tab_idle_task_identity(mutation):
    receipt = _completed_post_tab_idle_with_live_baseline()
    receipt.update(mutation)
    report = {
        "mode": {"post_tab_idle_timeout_ms": 5000},
        "post_tab_idle": receipt,
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = {item["check"] for item in check_runtime_health_budget(report)}

    assert "runtime_health.post_tab_idle.contract" in failures


def test_runtime_health_budget_rejects_slow_tab_first_open():
    report = {
        "mode": {"tabs": ["stock_candidates"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 7000.0},
                {"cycle": 2, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
            ]
        },
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.tab_first_open.elapsed" for failure in failures)


def test_runtime_health_budget_measures_tab_interaction_through_actual_settle():
    report = {
        "mode": {"tabs": ["stock_candidates"]},
        "tab_cycle": {
            "tabs": [
                {
                    "cycle": 1,
                    "key": "stock_candidates",
                    "status": "ok",
                    "elapsed_ms": 100.0,
                    "interaction_to_stable_ms": 1300.0,
                }
            ]
        },
        "runtime_health_samples": [_runtime_health_sample()],
    }

    failure = next(
        item for item in check_runtime_health_budget(report) if item["check"] == "runtime_health.tab_first_open.elapsed"
    )

    assert failure["phase"] == "tab_cycle"
    assert failure["sample"] == "tab_cycle.tabs"
    assert failure["tabs"][0]["metric"] == "interaction_to_stable_ms"
    assert failure["tabs"][0]["elapsed_ms"] == 1300.0


def test_runtime_health_budget_does_not_reclassify_loaded_tab_activation_as_first_open():
    report = {
        "mode": {"tabs": ["stock_candidates"]},
        "tab_cycle": {
            "tabs": [
                {
                    "cycle": 1,
                    "key": "stock_candidates",
                    "status": "ok",
                    "elapsed_ms": 7000.0,
                    "loaded_before": True,
                }
            ]
        },
        "runtime_health_samples": [_runtime_health_sample()],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_critical_ui_stalls():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(
                ui_stalls={
                    "installed": True,
                    "critical_count": 16,
                    "event_loop_critical_count": 13,
                    "max_elapsed_ms": 501.0,
                }
            )
        ],
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.ui_stall.critical_count",
        "runtime_health.ui_stall.event_loop_critical_count",
        "runtime_health.ui_stall.max_elapsed",
    }


def test_runtime_health_ui_stall_failure_reports_peak_sample_phase_and_tasks():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(
                label="after_tab_cycle",
                measurement_phase="tab_cycle",
                background_tasks={"count": 0, "ids": []},
                ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 120.0},
            ),
            _runtime_health_sample(
                label="after_tab_async_tail",
                measurement_phase="tab_async_tail",
                background_tasks={"count": 1, "ids": ["tab_job"]},
                ui_stalls={"installed": True, "critical_count": 0, "max_elapsed_ms": 501.0},
            ),
        ]
    }

    failure = next(
        item for item in check_runtime_health_budget(report) if item["check"] == "runtime_health.ui_stall.max_elapsed"
    )

    assert failure["actual"] == 501.0
    assert failure["phase"] == "tab_async_tail"
    assert failure["sample"] == "after_tab_async_tail"
    assert failure["task_ids"] == ["tab_job"]


def test_runtime_health_budget_excludes_startup_ui_stalls_from_tab_budget():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(
                label="startup",
                ui_stalls={
                    "installed": True,
                    "critical_count": 1,
                    "event_loop_critical_count": 1,
                    "max_elapsed_ms": 650.0,
                },
            ),
            _runtime_health_sample(
                label="after_tab_cycle",
                ui_stalls={
                    "installed": True,
                    "critical_count": 2,
                    "event_loop_critical_count": 2,
                    "max_elapsed_ms": 130.0,
                },
            ),
        ],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_accepts_key_tab_first_open_within_budget():
    report = {
        "mode": {"tabs": ["foreign_block", "fund_holdings", "earnings"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "foreign_block", "status": "ok", "elapsed_ms": 450.0},
                {"cycle": 1, "key": "fund_holdings", "status": "ok", "elapsed_ms": 350.0},
                {"cycle": 1, "key": "earnings", "status": "ok", "elapsed_ms": 300.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage=[
                    {
                        "key": "foreign_block",
                        "title": "大宗交易",
                        "source": "foreign_block_trade_latest.json",
                        "cache_refs": ["data/Cache/foreign_block_trade_latest.json"],
                        "network_capable": True,
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    },
                    {
                        "key": "fund_holdings",
                        "title": "基金持仓",
                        "source": "fund_holdings_store",
                        "cache_refs": ["data/vcp_hunter.db"],
                        "network_capable": True,
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    },
                    {
                        "key": "earnings",
                        "title": "业绩异动",
                        "source": "earnings_state",
                        "cache_refs": ["data/vcp_hunter.db"],
                        "network_capable": True,
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    },
                ]
            )
        ],
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_key_tab_first_open_before_global_budget():
    report = {
        "mode": {"tabs": ["foreign_block"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "foreign_block", "status": "ok", "elapsed_ms": 800.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage=[
                    {
                        "key": "foreign_block",
                        "title": "大宗交易",
                        "source": "foreign_block_trade_latest.json",
                        "cache_refs": ["data/Cache/foreign_block_trade_latest.json"],
                        "network_capable": True,
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    }
                ]
            )
        ],
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.tab_first_open.key_elapsed" for failure in failures)
    assert not any(failure["check"] == "runtime_health.tab_first_open.elapsed" for failure in failures)


def test_runtime_health_budget_prefers_post_warmup_budget_trend():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(process={"rss_mb": 500.0, "thread_count": 20}),
            _runtime_health_sample(process={"rss_mb": 700.0, "thread_count": 100}),
        ],
        "budget_trend": {
            "rss_mb": {"range": 200.0, "tail_range": 4.0, "basis": "post_kline_close_samples"},
            "background_tasks": {"last": 0},
            "active_timers": {"net_delta": 0},
            "total_timers": {"net_delta": 0},
            "event_receivers": {"net_delta": 0},
            "threads": {"net_delta": 0, "basis": "post_kline_close_samples"},
            "webengine_children": {"last": 0},
        },
    }

    assert check_runtime_health_budget(report) == []


def test_runtime_health_budget_rejects_post_close_rss_trend_range():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(process={"rss_mb": 500.0, "thread_count": 20}),
            _runtime_health_sample(process={"rss_mb": 504.0, "thread_count": 20}),
        ],
        "budget_trend": {
            "rss_mb": {"range": 140.0, "tail_range": 140.0, "basis": "post_kline_close_samples"},
            "background_tasks": {"last": 0},
            "active_timers": {"net_delta": 0},
            "total_timers": {"net_delta": 0},
            "event_receivers": {"net_delta": 0},
            "threads": {"net_delta": 0},
            "webengine_children": {"last": 0},
        },
    }

    failures = check_runtime_health_budget(report)

    assert any(failure["check"] == "runtime_health.memory.rss_tail_range" for failure in failures)


def test_runtime_health_budget_rejects_growth_and_missing_sections():
    report = {
        "runtime_health_samples": [
            _runtime_health_sample(),
            {
                "background_tasks": {"available": True, "count": 3},
                "timers": {"active": 12, "total": 20},
                "event_bus": {"total_receivers": 14},
                "process": {"rss_mb": 700.0, "thread_count": 60},
                "webengine": {"count": 2},
                "quotes": {},
                "f5_cache": {},
                "data_lineage": {},
            },
        ]
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.quotes.request_stats",
        "runtime_health.quotes.provider_degraded",
        "runtime_health.quotes.last_network_error",
        "runtime_health.market_data.present",
        "runtime_health.f5_refresh.present",
        "runtime_health.data_lineage.type",
        "runtime_health.background_tasks.final",
        "runtime_health.timers.active_growth",
        "runtime_health.timers.total_growth",
        "runtime_health.events.receiver_growth",
        "runtime_health.threads.growth",
        "runtime_health.webengine.final",
        "runtime_health.memory.rss_tail_range",
    }


def test_runtime_health_budget_rejects_tab_cycle_and_lineage_gaps():
    report = {
        "mode": {"tabs": ["stock_candidates", "scan"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
                {"cycle": 1, "key": "scan", "status": "timeout", "elapsed_ms": 2100.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(
                data_lineage=[
                    {
                        "key": "stock_candidates",
                        "source": "workspace_stock_context",
                        "triggered_network": False,
                    }
                ]
            )
        ],
    }

    failures = check_runtime_health_budget(report)

    assert {failure["check"] for failure in failures} >= {
        "runtime_health.tab_cycle.status",
        "runtime_health.data_lineage.requested_tabs",
        "runtime_health.data_lineage.fields",
    }
