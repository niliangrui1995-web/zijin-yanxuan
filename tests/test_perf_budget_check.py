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


def test_perf_budget_accepts_kline_lifecycle_smoke_report():
    report = {
        "report_type": "kline_webengine_lifecycle_smoke",
        "status": "ok",
        "summary": {
            "status": "ok",
            "cycles": 2,
            "final_webengine_child_count": 0,
        },
        "cycles": [
            {
                "cycle_index": 1,
                "summary": {"status": "ok"},
                "samples": [{"label": "cycle_1:after_close", "webengine_child_count": 0}],
            },
            {
                "cycle_index": 2,
                "summary": {"status": "ok"},
                "samples": [{"label": "cycle_2:after_close", "webengine_child_count": 0}],
            },
        ],
    }

    assert check_kline_lifecycle_budget(report) == []


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
        "background_tasks": {"count": 0},
        "timers": {"active": 4, "total": 8},
        "event_bus": {"total_receivers": 12},
        "process": {"rss_mb": 500.0, "thread_count": 24},
        "webengine": {"count": 0},
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
                "triggered_network": False,
                "fallback_or_degraded": False,
                "loaded": True,
            }
        ],
    }
    sample.update(overrides)
    return sample


def test_runtime_health_budget_accepts_structured_suite_report():
    report = {
        "mode": {"tabs": ["stock_candidates"]},
        "tab_cycle": {
            "tabs": [
                {"cycle": 1, "key": "stock_candidates", "status": "ok", "elapsed_ms": 120.0},
            ]
        },
        "runtime_health_samples": [
            _runtime_health_sample(),
            _runtime_health_sample(
                timers={"active": 5, "total": 9},
                process={"rss_mb": 504.0, "thread_count": 25},
            ),
        ],
    }

    assert check_runtime_health_budget(report) == []


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
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    },
                    {
                        "key": "fund_holdings",
                        "title": "基金持仓",
                        "source": "fund_holdings_store",
                        "cache_refs": ["data/vcp_hunter.db"],
                        "triggered_network": False,
                        "fallback_or_degraded": False,
                        "loaded": True,
                    },
                    {
                        "key": "earnings",
                        "title": "业绩异动",
                        "source": "earnings_state",
                        "cache_refs": ["data/vcp_hunter.db"],
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
                {"cycle": 1, "key": "foreign_block", "status": "ok", "elapsed_ms": 3000.0},
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
                "background_tasks": {"count": 3},
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
