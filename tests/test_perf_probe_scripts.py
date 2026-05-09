from scripts.perf_round5_probe import summarize_background_tasks, summarize_quote_calls
from scripts.runtime_health_stability_suite import (
    _apply_mode_defaults,
    _build_budget_trend,
    _build_startup_lazy_budget,
    _parse_args,
)
from scripts.soak_leak_probe import _trend


def _sample(rss: float, private: float, threads: int = 20, label: str = "") -> dict:
    return {
        "label": label,
        "main": {
            "rss_mb": rss,
            "private_mb": private,
            "thread_count": threads,
        }
    }


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
    assert str(args.sample_output_dir).endswith("runtime_health_samples")


def test_runtime_health_suite_soak60_defaults_to_one_hour():
    args = _apply_mode_defaults(_parse_args(["--mode", "soak60"]))

    assert args.idle_seconds == 3600
    assert args.tab_cycles == 2
    assert args.f5_cycles == 2
    assert args.quote_cycles == 2
    assert args.kline_cycles == 1


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


def test_runtime_health_suite_startup_lazy_budget_summarizes_key_timings():
    report = {
        "startup_ready_ms": 420.0,
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
    assert budget["tab_first_open"]["max_elapsed_ms"] == 150.0
    assert [item["key"] for item in budget["tab_first_open"]["tabs"]] == ["scan", "watchlist"]
    assert budget["f5_quiet"]["max_cycle_elapsed_ms"] == 220.0
    assert budget["background_settle"]["final_background_task_count"] == 0
