from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "gbbq_single_max_rss_delta_mb": 8.0,
    "gbbq_single_max_elapsed_ms": 1500.0,
    "gbbq_full_max_rss_delta_mb": 130.0,
    "gbbq_full_max_elapsed_ms": 10000.0,
    "tab_cycle_max_rss_delta_mb": 24.0,
    "kline_max_rss_delta_mb": 140.0,
    "kline_max_final_webengine_children": 0,
    "soak_max_tail_range_mb": 24.0,
    "round4_startup_main_window_ready_max_ms": 6500.0,
    "round4_tab_first_open_max_ms": 6500.0,
    "round4_f5_total_max_ms": 6500.0,
    "round4_f5_tab_refresh_max_ms": 2500.0,
    "round4_quote_duplicate_max": 0,
    "round4_active_task_final_max": 1,
    "round4_new_active_task_final_max": 0,
    "round4_active_timer_growth_max": 2,
    "round4_thread_growth_max": 16,
    "round5_post_f5_quote_batch_total_max": 1,
    "round5_duplicate_batch_signature_max": 0,
    "round5_duplicate_quote_code_max": 0,
    "round5_cache_only_quote_request_max": 0,
    "round5_information_source_task_max": 0,
    "round5_new_active_task_final_max": 0,
    "round5_active_earnings_worker_final_max": 0,
    "round5_active_timer_growth_max": 2,
    "round5_event_receiver_growth_max": 0,
    "round5_thread_growth_max": 16,
    "runtime_health_active_task_final_max": 1,
    "runtime_health_active_timer_growth_max": 4,
    "runtime_health_total_timer_growth_max": 6,
    "runtime_health_event_receiver_growth_max": 0,
    "runtime_health_thread_growth_max": 16,
    "runtime_health_webengine_final_max": 0,
    "runtime_health_rss_tail_range_mb": 96.0,
}


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _snapshot_webengine_children(snapshot: dict | None) -> int:
    return _as_int((snapshot or {}).get("webengine_child_count"), default=0)


def _last_snapshot(report: dict) -> dict:
    snapshots = report.get("snapshots") or report.get("samples") or []
    if not isinstance(snapshots, list):
        return {}
    return snapshots[-1] if snapshots else {}


def _fail(failures: list[dict], check: str, detail: str, **values) -> None:
    failures.append({"check": check, "detail": detail, **values})


def check_gbbq_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    samples = ((report.get("gbbq_profile") or {}).get("samples") or {})
    single = samples.get("single_code")
    full = samples.get("full")

    if single is None and full is None:
        _fail(failures, "gbbq.present", "gbbq_profile has no single_code or full sample")
        return failures

    if single is not None:
        result = single.get("result") or {}
        if result.get("full_loaded") is not False:
            _fail(failures, "gbbq.single.lazy", "single-code gbbq load materialized the full cache")
        if _as_int(result.get("codes")) > 1:
            _fail(failures, "gbbq.single.codes", "single-code gbbq load returned more than one code")
        if _as_float(single.get("rss_delta_mb")) > budget["gbbq_single_max_rss_delta_mb"]:
            _fail(
                failures,
                "gbbq.single.rss_delta",
                "single-code gbbq RSS delta exceeded budget",
                actual=single.get("rss_delta_mb"),
                budget=budget["gbbq_single_max_rss_delta_mb"],
            )
        if _as_float(single.get("elapsed_ms")) > budget["gbbq_single_max_elapsed_ms"]:
            _fail(
                failures,
                "gbbq.single.elapsed",
                "single-code gbbq elapsed time exceeded budget",
                actual=single.get("elapsed_ms"),
                budget=budget["gbbq_single_max_elapsed_ms"],
            )

    if full is not None:
        result = full.get("result") or {}
        if result.get("full_loaded") is not True:
            _fail(failures, "gbbq.full.loaded", "full gbbq run did not materialize the full cache")
        if _as_float(full.get("rss_delta_mb")) > budget["gbbq_full_max_rss_delta_mb"]:
            _fail(
                failures,
                "gbbq.full.rss_delta",
                "full gbbq RSS delta exceeded budget",
                actual=full.get("rss_delta_mb"),
                budget=budget["gbbq_full_max_rss_delta_mb"],
            )
        if _as_float(full.get("elapsed_ms")) > budget["gbbq_full_max_elapsed_ms"]:
            _fail(
                failures,
                "gbbq.full.elapsed",
                "full gbbq elapsed time exceeded budget",
                actual=full.get("elapsed_ms"),
                budget=budget["gbbq_full_max_elapsed_ms"],
            )

    return failures


def check_tab_cycle_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    sample = (report.get("samples") or {}).get("tab_cycles")
    if sample is None:
        _fail(failures, "tab_cycle.present", "tab cycle sample is missing")
        return failures

    if _as_float(sample.get("rss_delta_mb")) > budget["tab_cycle_max_rss_delta_mb"]:
        _fail(
            failures,
            "tab_cycle.rss_delta",
            "tab cycle RSS delta exceeded budget",
            actual=sample.get("rss_delta_mb"),
            budget=budget["tab_cycle_max_rss_delta_mb"],
        )
    return failures


def check_kline_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    sample = (report.get("samples") or {}).get("kline_cycles")
    if sample is None:
        _fail(failures, "kline.present", "K-line cycle sample is missing")
        return failures

    result = sample.get("result") or {}
    cycles = _as_int(result.get("cycles"))
    opened = _as_int(result.get("opened"))
    closed = _as_int(result.get("closed"))
    blocked = _as_int(result.get("blocked"))
    if cycles <= 0 or opened != cycles or closed != cycles or blocked != 0:
        _fail(
            failures,
            "kline.cycles",
            "K-line cycles did not open and close cleanly",
            cycles=cycles,
            opened=opened,
            closed=closed,
            blocked=blocked,
        )

    if _as_float(sample.get("rss_delta_mb")) > budget["kline_max_rss_delta_mb"]:
        _fail(
            failures,
            "kline.rss_delta",
            "K-line RSS delta exceeded budget",
            actual=sample.get("rss_delta_mb"),
            budget=budget["kline_max_rss_delta_mb"],
        )

    end_children = _snapshot_webengine_children(_last_snapshot(report))
    if end_children > budget["kline_max_final_webengine_children"]:
        _fail(
            failures,
            "kline.webengine_children.final",
            "QtWebEngine child processes remained after probe end",
            actual=end_children,
            budget=budget["kline_max_final_webengine_children"],
        )

    for cycle_sample in result.get("cycle_samples") or []:
        label = str(cycle_sample.get("label") or "")
        if label.endswith(":after_close"):
            children = _snapshot_webengine_children(cycle_sample)
            if children > budget["kline_max_final_webengine_children"]:
                _fail(
                    failures,
                    "kline.webengine_children.after_close",
                    "QtWebEngine child process remained after a close sample",
                    label=label,
                    actual=children,
                    budget=budget["kline_max_final_webengine_children"],
                )
    return failures


def check_soak_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    trend = report.get("trend") or {}
    if trend.get("growth_basis") != "stable_close_samples":
        _fail(
            failures,
            "soak.growth_basis",
            "soak trend is not based on stable close samples",
            actual=trend.get("growth_basis"),
        )

    for key in ("rss", "private"):
        item = trend.get(key) or {}
        if item.get("status") != "ok":
            _fail(failures, f"soak.{key}.status", f"soak {key} trend is not ok", actual=item.get("status"))
        if _as_float(item.get("tail_range")) > budget["soak_max_tail_range_mb"]:
            _fail(
                failures,
                f"soak.{key}.tail_range",
                f"soak {key} tail range exceeded budget",
                actual=item.get("tail_range"),
                budget=budget["soak_max_tail_range_mb"],
            )

    end_children = _snapshot_webengine_children(_last_snapshot(report))
    if end_children > budget["kline_max_final_webengine_children"]:
        _fail(
            failures,
            "soak.webengine_children.final",
            "QtWebEngine child processes remained after soak close sample",
            actual=end_children,
            budget=budget["kline_max_final_webengine_children"],
        )
    return failures


def check_round4_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    startup = report.get("startup") or {}
    tab_first_open = report.get("tab_first_open") or {}
    f5_refresh = report.get("f5_refresh") or {}
    stability = report.get("stability") or {}

    if not startup:
        _fail(failures, "round4.startup.present", "round4 startup report is missing")
    else:
        elapsed = _as_float(startup.get("main_window_ready_ms"))
        if elapsed > budget["round4_startup_main_window_ready_max_ms"]:
            _fail(
                failures,
                "round4.startup.main_window_ready",
                "startup main-window-ready elapsed time exceeded budget",
                actual=elapsed,
                budget=budget["round4_startup_main_window_ready_max_ms"],
            )

    tabs = tab_first_open.get("tabs") or []
    if not isinstance(tabs, list) or not tabs:
        _fail(failures, "round4.tabs.present", "round4 tab first-open samples are missing")
    for tab in tabs if isinstance(tabs, list) else []:
        key = str(tab.get("key") or "")
        if tab.get("status") not in {"ok", None}:
            _fail(failures, "round4.tabs.status", "tab first-open did not complete cleanly", key=key, actual=tab.get("status"))
        elapsed = _as_float(tab.get("elapsed_ms"))
        if elapsed > budget["round4_tab_first_open_max_ms"]:
            _fail(
                failures,
                "round4.tabs.elapsed",
                "tab first-open elapsed time exceeded budget",
                key=key,
                actual=elapsed,
                budget=budget["round4_tab_first_open_max_ms"],
            )

    if not f5_refresh:
        _fail(failures, "round4.f5.present", "round4 F5 refresh report is missing")
    else:
        elapsed = _as_float(f5_refresh.get("total_elapsed_ms"))
        if elapsed > budget["round4_f5_total_max_ms"]:
            _fail(
                failures,
                "round4.f5.total_elapsed",
                "F5 total elapsed time exceeded budget",
                actual=elapsed,
                budget=budget["round4_f5_total_max_ms"],
            )
        for item in f5_refresh.get("tab_timings") or []:
            tab_elapsed = _as_float(item.get("elapsed_ms"))
            if tab_elapsed > budget["round4_f5_tab_refresh_max_ms"]:
                _fail(
                    failures,
                    "round4.f5.tab_elapsed",
                    "F5 per-tab refresh elapsed time exceeded budget",
                    key=item.get("label"),
                    actual=tab_elapsed,
                    budget=budget["round4_f5_tab_refresh_max_ms"],
                )
        quote_requests = f5_refresh.get("quote_requests") or {}
        duplicate_total = _as_int(quote_requests.get("duplicate_across_batches")) + _as_int(
            quote_requests.get("duplicate_in_batch")
        )
        if duplicate_total > budget["round4_quote_duplicate_max"]:
            _fail(
                failures,
                "round4.f5.quote_duplicates",
                "F5 quote requests contained duplicate codes",
                actual=duplicate_total,
                budget=budget["round4_quote_duplicate_max"],
                duplicates=quote_requests.get("duplicates_by_code") or {},
            )
        new_active_tasks = _as_int(f5_refresh.get("new_active_background_tasks_after"))
        if new_active_tasks > budget["round4_new_active_task_final_max"]:
            _fail(
                failures,
                "round4.f5.new_active_tasks_after",
                "F5 left newly-started background tasks active after probe settling",
                actual=new_active_tasks,
                budget=budget["round4_new_active_task_final_max"],
            )

    if not stability:
        _fail(failures, "round4.stability.present", "round4 stability report is missing")
    else:
        trend = stability.get("trend") or {}
        active_tasks = trend.get("active_tasks") or {}
        if _as_float(active_tasks.get("last")) > budget["round4_active_task_final_max"]:
            _fail(
                failures,
                "round4.stability.active_tasks_final",
                "stability cycle ended with active background tasks",
                actual=active_tasks.get("last"),
                budget=budget["round4_active_task_final_max"],
            )
        active_timers = trend.get("active_timers") or {}
        if _as_float(active_timers.get("net_delta")) > budget["round4_active_timer_growth_max"]:
            _fail(
                failures,
                "round4.stability.active_timer_growth",
                "active timer count grew beyond budget",
                actual=active_timers.get("net_delta"),
                budget=budget["round4_active_timer_growth_max"],
            )
        threads = trend.get("threads") or {}
        if _as_float(threads.get("net_delta")) > budget["round4_thread_growth_max"]:
            _fail(
                failures,
                "round4.stability.thread_growth",
                "thread count grew beyond budget",
                actual=threads.get("net_delta"),
                budget=budget["round4_thread_growth_max"],
            )

    return failures


def check_round5_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    post_f5 = report.get("post_f5") or {}
    if not post_f5:
        _fail(failures, "round5.post_f5.present", "round5 post-F5 report is missing")
        return failures

    quote_requests = post_f5.get("quote_requests") or {}
    batch_count = _as_int(quote_requests.get("batch_count"))
    if batch_count > budget["round5_post_f5_quote_batch_total_max"]:
        _fail(
            failures,
            "round5.quote.batch_count",
            "post-F5 quote request batch count exceeded budget",
            actual=batch_count,
            budget=budget["round5_post_f5_quote_batch_total_max"],
        )

    repeated_signatures = _as_int(quote_requests.get("repeated_batch_signature_count"))
    if repeated_signatures > budget["round5_duplicate_batch_signature_max"]:
        _fail(
            failures,
            "round5.quote.repeated_batch_signatures",
            "post-F5 repeated quote batch signatures exceeded budget",
            actual=repeated_signatures,
            budget=budget["round5_duplicate_batch_signature_max"],
            repeated=quote_requests.get("repeated_batch_signatures") or {},
        )

    duplicate_codes = _as_int(quote_requests.get("duplicate_quote_code_count"))
    if duplicate_codes > budget["round5_duplicate_quote_code_max"]:
        _fail(
            failures,
            "round5.quote.duplicate_codes",
            "post-F5 duplicate quote codes exceeded budget",
            actual=duplicate_codes,
            budget=budget["round5_duplicate_quote_code_max"],
            duplicates=quote_requests.get("duplicates_by_code") or {},
        )

    guard = post_f5.get("cache_only_guard") or {}
    cache_only_quote_count = _as_int(guard.get("cache_only_quote_request_count"))
    if cache_only_quote_count > budget["round5_cache_only_quote_request_max"]:
        _fail(
            failures,
            "round5.cache_only.quote_requests",
            "cache-only or information-source tabs triggered quote requests",
            actual=cache_only_quote_count,
            budget=budget["round5_cache_only_quote_request_max"],
        )

    info_task_count = _as_int(guard.get("information_source_background_task_count"))
    if info_task_count > budget["round5_information_source_task_max"]:
        _fail(
            failures,
            "round5.cache_only.background_tasks",
            "information-source tabs scheduled post-F5 background network tasks",
            actual=info_task_count,
            budget=budget["round5_information_source_task_max"],
        )

    background_tasks = post_f5.get("background_tasks") or {}
    new_active_tasks = _as_int(background_tasks.get("new_active_task_final"))
    if new_active_tasks > budget["round5_new_active_task_final_max"]:
        _fail(
            failures,
            "round5.background.new_active_tasks_final",
            "post-F5 background tasks did not return to baseline by final sample",
            actual=new_active_tasks,
            budget=budget["round5_new_active_task_final_max"],
            active=background_tasks.get("new_active_task_ids_final") or [],
        )

    active_earnings = _as_int(background_tasks.get("active_earnings_worker_count_final"))
    if active_earnings > budget["round5_active_earnings_worker_final_max"]:
        _fail(
            failures,
            "round5.background.active_earnings_workers_final",
            "post-F5 earnings workers remained active by final sample",
            actual=active_earnings,
            budget=budget["round5_active_earnings_worker_final_max"],
            active=background_tasks.get("active_earnings_workers_final") or [],
        )

    trend = post_f5.get("runtime_trend") or {}
    active_timers = trend.get("active_timers") or {}
    if _as_float(active_timers.get("net_delta")) > budget["round5_active_timer_growth_max"]:
        _fail(
            failures,
            "round5.runtime.active_timer_growth",
            "post-F5 active timer count grew beyond budget",
            actual=active_timers.get("net_delta"),
            budget=budget["round5_active_timer_growth_max"],
        )

    threads = trend.get("threads") or {}
    if _as_float(threads.get("net_delta")) > budget["round5_thread_growth_max"]:
        _fail(
            failures,
            "round5.runtime.thread_growth",
            "post-F5 thread count grew beyond budget",
            actual=threads.get("net_delta"),
            budget=budget["round5_thread_growth_max"],
        )

    receiver_trend = post_f5.get("event_receiver_trend") or {}
    growing_receivers = {
        name: item
        for name, item in receiver_trend.items()
        if _as_float((item or {}).get("net_delta")) > budget["round5_event_receiver_growth_max"]
    }
    if growing_receivers:
        _fail(
            failures,
            "round5.events.receiver_growth",
            "event receiver counts grew beyond budget",
            actual=growing_receivers,
            budget=budget["round5_event_receiver_growth_max"],
        )

    return failures


def _runtime_health_samples(report: dict) -> list[dict]:
    if report.get("report_type") == "runtime_health":
        return [report]
    samples = report.get("runtime_health_samples") or report.get("samples") or []
    return samples if isinstance(samples, list) else []


def _runtime_health_values(samples: list[dict], getter) -> list[float]:
    values = []
    for sample in samples or []:
        try:
            value = getter(sample)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            value = None
        if value is not None:
            values.append(float(value))
    return values


def _runtime_health_trend_one(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "first": None, "last": None, "net_delta": 0.0, "range": 0.0, "max": None}
    return {
        "count": len(values),
        "first": values[0],
        "last": values[-1],
        "net_delta": round(values[-1] - values[0], 3),
        "range": round(max(values) - min(values), 3),
        "max": max(values),
    }


def _runtime_health_trend(samples: list[dict]) -> dict:
    return {
        "background_tasks": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("background_tasks") or {}).get("count"))
        ),
        "active_timers": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("timers") or {}).get("active"))
        ),
        "total_timers": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("timers") or {}).get("total"))
        ),
        "event_receivers": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("event_bus") or {}).get("total_receivers"))
        ),
        "threads": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("process") or {}).get("thread_count"))
        ),
        "webengine_children": _runtime_health_trend_one(
            _runtime_health_values(samples, lambda item: (item.get("webengine") or {}).get("count"))
        ),
    }


def _tail_range(values: list[float], tail_count: int = 3) -> float:
    tail = values[-max(1, int(tail_count)):]
    return round(max(tail) - min(tail), 3) if tail else 0.0


def check_runtime_health_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    samples = _runtime_health_samples(report)
    if not samples:
        _fail(failures, "runtime_health.present", "runtime health samples are missing")
        return failures

    last = samples[-1] if isinstance(samples[-1], dict) else {}
    for key in (
        "background_tasks",
        "timers",
        "event_bus",
        "process",
        "webengine",
        "quotes",
        "f5_cache",
        "data_lineage",
    ):
        if key not in last:
            _fail(failures, f"runtime_health.{key}.present", f"runtime health report missing {key}")

    quotes = last.get("quotes") or {}
    if "request_stats" not in quotes:
        _fail(failures, "runtime_health.quotes.request_stats", "quote request stats are missing")
    if "provider_degraded" not in quotes:
        _fail(failures, "runtime_health.quotes.provider_degraded", "provider degraded state is missing")
    if "last_network_error" not in quotes:
        _fail(failures, "runtime_health.quotes.last_network_error", "last network error field is missing")

    f5_cache = last.get("f5_cache") or {}
    for key in ("cache_version", "trade_date", "updated_at"):
        if key not in f5_cache:
            _fail(failures, f"runtime_health.f5_cache.{key}", f"F5 cache {key} field is missing")

    if not isinstance(last.get("data_lineage"), list):
        _fail(failures, "runtime_health.data_lineage.type", "data lineage must be a list")

    trend = report.get("budget_trend") or report.get("trend") or _runtime_health_trend(samples)
    active_tasks = trend.get("background_tasks") or {}
    if _as_float(active_tasks.get("last")) > budget["runtime_health_active_task_final_max"]:
        _fail(
            failures,
            "runtime_health.background_tasks.final",
            "runtime health ended with too many active background tasks",
            actual=active_tasks.get("last"),
            budget=budget["runtime_health_active_task_final_max"],
        )

    active_timers = trend.get("active_timers") or {}
    if _as_float(active_timers.get("net_delta")) > budget["runtime_health_active_timer_growth_max"]:
        _fail(
            failures,
            "runtime_health.timers.active_growth",
            "active timer count grew beyond runtime health budget",
            actual=active_timers.get("net_delta"),
            budget=budget["runtime_health_active_timer_growth_max"],
        )

    total_timers = trend.get("total_timers") or {}
    if _as_float(total_timers.get("net_delta")) > budget["runtime_health_total_timer_growth_max"]:
        _fail(
            failures,
            "runtime_health.timers.total_growth",
            "total timer count grew beyond runtime health budget",
            actual=total_timers.get("net_delta"),
            budget=budget["runtime_health_total_timer_growth_max"],
        )

    event_receivers = trend.get("event_receivers") or {}
    if _as_float(event_receivers.get("net_delta")) > budget["runtime_health_event_receiver_growth_max"]:
        _fail(
            failures,
            "runtime_health.events.receiver_growth",
            "event receiver count grew beyond runtime health budget",
            actual=event_receivers.get("net_delta"),
            budget=budget["runtime_health_event_receiver_growth_max"],
        )

    threads = trend.get("threads") or {}
    if _as_float(threads.get("net_delta")) > budget["runtime_health_thread_growth_max"]:
        _fail(
            failures,
            "runtime_health.threads.growth",
            "thread count grew beyond runtime health budget",
            actual=threads.get("net_delta"),
            budget=budget["runtime_health_thread_growth_max"],
        )

    webengine_children = trend.get("webengine_children") or {}
    if _as_float(webengine_children.get("last")) > budget["runtime_health_webengine_final_max"]:
        _fail(
            failures,
            "runtime_health.webengine.final",
            "QtWebEngine child processes remained at final runtime health sample",
            actual=webengine_children.get("last"),
            budget=budget["runtime_health_webengine_final_max"],
        )

    rss_values = _runtime_health_values(samples, lambda item: (item.get("process") or {}).get("rss_mb"))
    rss_tail_range = _tail_range(rss_values)
    if rss_tail_range > budget["runtime_health_rss_tail_range_mb"]:
        _fail(
            failures,
            "runtime_health.memory.rss_tail_range",
            "runtime health RSS tail range exceeded budget",
            actual=rss_tail_range,
            budget=budget["runtime_health_rss_tail_range_mb"],
        )

    return failures


def run_budget_checks(args: argparse.Namespace) -> dict:
    thresholds = {
        "gbbq_single_max_rss_delta_mb": args.gbbq_single_max_rss_delta_mb,
        "gbbq_single_max_elapsed_ms": args.gbbq_single_max_elapsed_ms,
        "gbbq_full_max_rss_delta_mb": args.gbbq_full_max_rss_delta_mb,
        "gbbq_full_max_elapsed_ms": args.gbbq_full_max_elapsed_ms,
        "tab_cycle_max_rss_delta_mb": args.tab_cycle_max_rss_delta_mb,
        "kline_max_rss_delta_mb": args.kline_max_rss_delta_mb,
        "kline_max_final_webengine_children": args.kline_max_final_webengine_children,
        "soak_max_tail_range_mb": args.soak_max_tail_range_mb,
        "round4_startup_main_window_ready_max_ms": args.round4_startup_main_window_ready_max_ms,
        "round4_tab_first_open_max_ms": args.round4_tab_first_open_max_ms,
        "round4_f5_total_max_ms": args.round4_f5_total_max_ms,
        "round4_f5_tab_refresh_max_ms": args.round4_f5_tab_refresh_max_ms,
        "round4_quote_duplicate_max": args.round4_quote_duplicate_max,
        "round4_active_task_final_max": args.round4_active_task_final_max,
        "round4_new_active_task_final_max": args.round4_new_active_task_final_max,
        "round4_active_timer_growth_max": args.round4_active_timer_growth_max,
        "round4_thread_growth_max": args.round4_thread_growth_max,
        "round5_post_f5_quote_batch_total_max": args.round5_post_f5_quote_batch_total_max,
        "round5_duplicate_batch_signature_max": args.round5_duplicate_batch_signature_max,
        "round5_duplicate_quote_code_max": args.round5_duplicate_quote_code_max,
        "round5_cache_only_quote_request_max": args.round5_cache_only_quote_request_max,
        "round5_information_source_task_max": args.round5_information_source_task_max,
        "round5_new_active_task_final_max": args.round5_new_active_task_final_max,
        "round5_active_earnings_worker_final_max": args.round5_active_earnings_worker_final_max,
        "round5_active_timer_growth_max": args.round5_active_timer_growth_max,
        "round5_event_receiver_growth_max": args.round5_event_receiver_growth_max,
        "round5_thread_growth_max": args.round5_thread_growth_max,
        "runtime_health_active_task_final_max": args.runtime_health_active_task_final_max,
        "runtime_health_active_timer_growth_max": args.runtime_health_active_timer_growth_max,
        "runtime_health_total_timer_growth_max": args.runtime_health_total_timer_growth_max,
        "runtime_health_event_receiver_growth_max": args.runtime_health_event_receiver_growth_max,
        "runtime_health_thread_growth_max": args.runtime_health_thread_growth_max,
        "runtime_health_webengine_final_max": args.runtime_health_webengine_final_max,
        "runtime_health_rss_tail_range_mb": args.runtime_health_rss_tail_range_mb,
    }
    checks: list[dict] = []

    for label, path, checker in (
        ("gbbq", args.gbbq_report, check_gbbq_budget),
        ("tab_cycle", args.tab_report, check_tab_cycle_budget),
        ("kline", args.kline_report, check_kline_budget),
        ("soak", args.soak_report, check_soak_budget),
        ("round4", args.round4_report, check_round4_budget),
        ("round5", args.round5_report, check_round5_budget),
        ("runtime_health", args.runtime_health_report, check_runtime_health_budget),
    ):
        if not path:
            continue
        failures = checker(_read_json(path), thresholds)
        checks.append({
            "label": label,
            "path": str(path),
            "status": "fail" if failures else "ok",
            "failures": failures,
        })

    status = "fail" if any(check["failures"] for check in checks) else "ok"
    return {
        "status": status,
        "thresholds": thresholds,
        "checks": checks,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate repeatable performance probe reports against budgets.")
    parser.add_argument("--gbbq-report", type=Path, default=None)
    parser.add_argument("--tab-report", type=Path, default=None)
    parser.add_argument("--kline-report", type=Path, default=None)
    parser.add_argument("--soak-report", type=Path, default=None)
    parser.add_argument("--round4-report", type=Path, default=None)
    parser.add_argument("--round5-report", type=Path, default=None)
    parser.add_argument("--runtime-health-report", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--gbbq-single-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["gbbq_single_max_rss_delta_mb"])
    parser.add_argument("--gbbq-single-max-elapsed-ms", type=float, default=DEFAULT_THRESHOLDS["gbbq_single_max_elapsed_ms"])
    parser.add_argument("--gbbq-full-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["gbbq_full_max_rss_delta_mb"])
    parser.add_argument("--gbbq-full-max-elapsed-ms", type=float, default=DEFAULT_THRESHOLDS["gbbq_full_max_elapsed_ms"])
    parser.add_argument("--tab-cycle-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["tab_cycle_max_rss_delta_mb"])
    parser.add_argument("--kline-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["kline_max_rss_delta_mb"])
    parser.add_argument(
        "--kline-max-final-webengine-children",
        type=int,
        default=DEFAULT_THRESHOLDS["kline_max_final_webengine_children"],
    )
    parser.add_argument("--soak-max-tail-range-mb", type=float, default=DEFAULT_THRESHOLDS["soak_max_tail_range_mb"])
    parser.add_argument(
        "--round4-startup-main-window-ready-max-ms",
        type=float,
        default=DEFAULT_THRESHOLDS["round4_startup_main_window_ready_max_ms"],
    )
    parser.add_argument(
        "--round4-tab-first-open-max-ms",
        type=float,
        default=DEFAULT_THRESHOLDS["round4_tab_first_open_max_ms"],
    )
    parser.add_argument(
        "--round4-f5-total-max-ms",
        type=float,
        default=DEFAULT_THRESHOLDS["round4_f5_total_max_ms"],
    )
    parser.add_argument(
        "--round4-f5-tab-refresh-max-ms",
        type=float,
        default=DEFAULT_THRESHOLDS["round4_f5_tab_refresh_max_ms"],
    )
    parser.add_argument(
        "--round4-quote-duplicate-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round4_quote_duplicate_max"],
    )
    parser.add_argument(
        "--round4-active-task-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round4_active_task_final_max"],
    )
    parser.add_argument(
        "--round4-new-active-task-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round4_new_active_task_final_max"],
    )
    parser.add_argument(
        "--round4-active-timer-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round4_active_timer_growth_max"],
    )
    parser.add_argument(
        "--round4-thread-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round4_thread_growth_max"],
    )
    parser.add_argument(
        "--round5-post-f5-quote-batch-total-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_post_f5_quote_batch_total_max"],
    )
    parser.add_argument(
        "--round5-duplicate-batch-signature-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_duplicate_batch_signature_max"],
    )
    parser.add_argument(
        "--round5-duplicate-quote-code-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_duplicate_quote_code_max"],
    )
    parser.add_argument(
        "--round5-cache-only-quote-request-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_cache_only_quote_request_max"],
    )
    parser.add_argument(
        "--round5-information-source-task-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_information_source_task_max"],
    )
    parser.add_argument(
        "--round5-new-active-task-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_new_active_task_final_max"],
    )
    parser.add_argument(
        "--round5-active-earnings-worker-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_active_earnings_worker_final_max"],
    )
    parser.add_argument(
        "--round5-active-timer-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_active_timer_growth_max"],
    )
    parser.add_argument(
        "--round5-event-receiver-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_event_receiver_growth_max"],
    )
    parser.add_argument(
        "--round5-thread-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["round5_thread_growth_max"],
    )
    parser.add_argument(
        "--runtime-health-active-task-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_active_task_final_max"],
    )
    parser.add_argument(
        "--runtime-health-active-timer-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_active_timer_growth_max"],
    )
    parser.add_argument(
        "--runtime-health-total-timer-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_total_timer_growth_max"],
    )
    parser.add_argument(
        "--runtime-health-event-receiver-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_event_receiver_growth_max"],
    )
    parser.add_argument(
        "--runtime-health-thread-growth-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_thread_growth_max"],
    )
    parser.add_argument(
        "--runtime-health-webengine-final-max",
        type=int,
        default=DEFAULT_THRESHOLDS["runtime_health_webengine_final_max"],
    )
    parser.add_argument(
        "--runtime-health-rss-tail-range-mb",
        type=float,
        default=DEFAULT_THRESHOLDS["runtime_health_rss_tail_range_mb"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_budget_checks(args)
    if report.get("checks"):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("No performance reports were provided.")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
