from scripts.perf_budget_check import (
    check_gbbq_budget,
    check_kline_budget,
    check_soak_budget,
    check_tab_cycle_budget,
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
