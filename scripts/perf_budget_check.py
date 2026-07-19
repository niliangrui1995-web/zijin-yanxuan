from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import pairwise
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER
from ui.workspaces.tab_registry import (
    health_probe_tab_keys,
    lineage_exclusion_tab_definitions,
    lineage_tab_definitions,
    startup_tab_keys,
)

_RUNTIME_HEALTH_SOAK_MINIMUM_SECONDS = {
    "long": 30 * 60,
    "soak30": 30 * 60,
    "soak60": 60 * 60,
}
_RUNTIME_HEALTH_SOAK_MAX_SAMPLE_SECONDS = 60

DEFAULT_THRESHOLDS = {
    "gbbq_single_max_rss_delta_mb": 8.0,
    "gbbq_single_max_elapsed_ms": 1500.0,
    "gbbq_full_max_rss_delta_mb": 130.0,
    "gbbq_full_max_elapsed_ms": 10000.0,
    "tab_cycle_max_rss_delta_mb": 24.0,
    "kline_max_rss_delta_mb": 140.0,
    "kline_max_final_webengine_children": 0,
    "kline_lifecycle_min_cycles": 10,
    "kline_shell_ready_p95_max_ms": 120.0,
    "kline_browser_ready_p95_max_ms": 500.0,
    "kline_chart_ready_p50_max_ms": 800.0,
    "kline_chart_ready_p95_max_ms": 1500.0,
    "kline_cached_switch_min_samples": 10,
    "kline_cached_switch_p95_max_ms": 300.0,
    "kline_open_ui_critical_stall_max": 0,
    "kline_open_ui_event_loop_critical_stall_max": 0,
    "kline_open_ui_max_stall_ms": 100.0,
    "kline_lifecycle_rss_net_growth_max_mb": 24.0,
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
    "runtime_health_managed_webengine_process_final_max": 2,
    "runtime_health_rss_tail_range_mb": 96.0,
    "runtime_health_startup_ready_max_ms": 1800.0,
    "runtime_health_initial_tab_ready_max_ms": 3000.0,
    "runtime_health_inclusive_first_paint_max_ms": 3500.0,
    "runtime_health_inclusive_initial_tab_ready_max_ms": 5500.0,
    "runtime_health_shutdown_close_max_ms": 5000.0,
    "runtime_health_tab_first_open_max_ms": 1200.0,
    "runtime_health_key_tab_first_open_max_ms": 500.0,
    "runtime_health_ui_critical_stall_max": 15,
    "runtime_health_ui_event_loop_critical_stall_max": 12,
    "runtime_health_ui_max_stall_ms": 500.0,
}

RUNTIME_HEALTH_KEY_TAB_FIRST_OPEN_BUDGET_KEYS = frozenset({"foreign_block", "fund_holdings", "earnings"})
RUNTIME_HEALTH_ACCEPTED_TAB_STATUSES = frozenset({"ok", "skipped_controlled_probe"})
RUNTIME_HEALTH_STARTUP_MODULE_ORIGIN = "script_module_after_time_import"
RUNTIME_HEALTH_STARTUP_APP_ORIGIN = "run_suite_entry_before_qapplication"
RUNTIME_HEALTH_BACKGROUND_PRELOAD_COVERED_TABS = tuple(
    definition.key for definition in lineage_tab_definitions()
)
RUNTIME_HEALTH_BACKGROUND_PRELOAD_EXCLUDED_TABS = tuple(
    definition.key for definition in lineage_exclusion_tab_definitions()
)
RUNTIME_HEALTH_BACKGROUND_PRELOAD_TAB_COUNT = (
    len(RUNTIME_HEALTH_BACKGROUND_PRELOAD_COVERED_TABS)
    + len(RUNTIME_HEALTH_BACKGROUND_PRELOAD_EXCLUDED_TABS)
)
RUNTIME_HEALTH_PROBE_ORDER = tuple(health_probe_tab_keys())
RUNTIME_HEALTH_BACKGROUND_PRELOAD_ORDER = tuple(startup_tab_keys())
RUNTIME_HEALTH_HIDDEN_ASIAN_NETWORK_TASK_IDS = frozenset(
    {"asian_data_sync_bg", "auto_refresh_asian_market_runtime"}
)
RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES = (
    "prepare",
    "gbbq",
    "market_sync",
    "market_stage",
    "rps",
    "sector_rps",
    "validate",
)
CLI_THRESHOLD_KEYS = tuple(key for key in DEFAULT_THRESHOLDS if key != "runtime_health_shutdown_close_max_ms")


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _optional_float(value) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _optional_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_nonnegative_int(value) -> int | None:
    parsed = _optional_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _optional_finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _optional_nonnegative_number(value) -> float | None:
    parsed = _optional_finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _snapshot_webengine_children(snapshot: dict | None) -> int:
    return _as_int((snapshot or {}).get("webengine_child_count"), default=0)


def _last_snapshot(report: dict) -> dict:
    snapshots = report.get("snapshots") or report.get("samples") or []
    if not isinstance(snapshots, list):
        return {}
    return snapshots[-1] if snapshots else {}


def _fail(failures: list[dict], check: str, detail: str, **values) -> None:
    failures.append({"check": check, "detail": detail, **values})


def _check_gbbq_single_sample(failures: list[dict], budget: dict, single: dict) -> None:
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


def _check_gbbq_full_sample(failures: list[dict], budget: dict, full: dict) -> None:
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


def check_gbbq_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    samples = (report.get("gbbq_profile") or {}).get("samples") or {}
    single = samples.get("single_code")
    full = samples.get("full")

    if single is None and full is None:
        _fail(failures, "gbbq.present", "gbbq_profile has no single_code or full sample")
        return failures
    if single is not None:
        _check_gbbq_single_sample(failures, budget, single)
    if full is not None:
        _check_gbbq_full_sample(failures, budget, full)
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


def _check_kline_cycle_counts(failures: list[dict], sample: dict) -> None:
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


def _check_kline_after_close_children(failures: list[dict], budget: dict, cycle_samples: list[dict]) -> None:
    for cycle_sample in cycle_samples or []:
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


def check_kline_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    sample = (report.get("samples") or {}).get("kline_cycles")
    if sample is None:
        _fail(failures, "kline.present", "K-line cycle sample is missing")
        return failures

    _check_kline_cycle_counts(failures, sample)
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

    result = sample.get("result") or {}
    _check_kline_after_close_children(failures, budget, result.get("cycle_samples") or [])
    return failures


_KLINE_LIFECYCLE_ZERO_GROWTH_FIELDS = (
    "thread_count",
    "background_task_count",
    "active_timer_count",
    "total_timer_count",
    "event_receiver_count",
    "webengine_child_count",
)
_KLINE_LIFECYCLE_SAMPLE_RESOURCE_FIELDS = (
    "rss_mb",
    *_KLINE_LIFECYCLE_ZERO_GROWTH_FIELDS,
)
_KLINE_LIFECYCLE_SAMPLE_SUFFIXES = (
    "before_open",
    "after_chart_ready",
    "after_close",
)


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one sample")
    position = (len(ordered) - 1) * max(0.0, min(1.0, float(quantile)))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _check_kline_lifecycle_report_identity(failures: list[dict], report: dict) -> dict:
    if report.get("status") == "skipped":
        _fail(
            failures,
            "kline_lifecycle.skipped",
            "K-line lifecycle evidence was skipped; an executed native report is required",
            reason=report.get("skip_reason"),
        )
    elif report.get("status") != "ok":
        _fail(
            failures,
            "kline_lifecycle.report_status",
            "K-line lifecycle report did not finish with status ok",
            actual=report.get("status"),
        )
    if report.get("report_type") != "kline_webengine_lifecycle_smoke":
        _fail(
            failures,
            "kline_lifecycle.report_type",
            "K-line lifecycle report type is missing or invalid",
            actual=report.get("report_type"),
        )

    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    if mode.get("native_qt") is not True or mode.get("allow_offscreen") is not False:
        _fail(
            failures,
            "kline_lifecycle.native_mode",
            "K-line lifecycle performance evidence must come from native visible Qt",
            native_qt=mode.get("native_qt"),
            allow_offscreen=mode.get("allow_offscreen"),
        )
    return mode


def _production_local_row_evidence_valid(rows: int | None, minimum: int | None) -> bool:
    return bool(
        rows is not None
        and minimum is not None
        and minimum >= 250
        and rows >= minimum
    )


def _production_local_code_evidence_valid(item) -> bool:
    if not isinstance(item, dict):
        return False
    rows = _optional_nonnegative_int(item.get("row_count"))
    minimum = _optional_nonnegative_int(item.get("minimum_required_rows"))
    source_layer = str(item.get("source_layer") or "").strip().lower()
    local_source = any(token in source_layer for token in ("parquet", "vipdoc", "memory_cache"))
    return bool(
        item.get("status") == "ok"
        and _production_local_row_evidence_valid(rows, minimum)
        and str(item.get("latest_trade_date") or "").strip()
        and local_source
    )


def _production_local_provider_evidence(report: dict) -> tuple[dict, list, dict]:
    raw_evidence = report.get("data_provider")
    evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    raw_requested = evidence.get("requested_codes")
    requested = raw_requested if isinstance(raw_requested, list) else []
    raw_codes = evidence.get("codes")
    codes = raw_codes if isinstance(raw_codes, dict) else {}
    return evidence, requested, codes


def _production_local_provider_contract(
    evidence: dict, requested: list, codes: dict, expected_codes: set[str]
) -> tuple[bool, ...]:
    return (
        evidence.get("status") == "ok",
        evidence.get("mode") == "production-local",
        evidence.get("provider_class") == "infra.market_data.tdx_data_provider.TdxDataProvider",
        evidence.get("production_provider_contract") is True,
        evidence.get("synthetic") is False,
        evidence.get("local_only") is True,
        evidence.get("read_only") is True,
        evidence.get("network_access_enabled") is False,
        evidence.get("network_request_count") == 0,
        evidence.get("network_guard_active") is True,
        evidence.get("underlying_offline") is True,
        evidence.get("underlying_server_pool_size") == 0,
        evidence.get("frozen_after_prime") is True,
        expected_codes.issubset(set(requested)),
        len(codes) >= 2,
        all(_production_local_code_evidence_valid(item) for item in codes.values()),
    )


def _production_local_cycle_source_valid(cycle: dict) -> bool:
    cached_switch = cycle.get("cached_switch")
    return bool(
        isinstance(cached_switch, dict)
        and cached_switch.get("provider_mode") == "production-local"
        and cached_switch.get("cache_source") == "production_local_frozen_cache"
    )


def _production_local_cycle_sources_valid(report: dict) -> bool:
    raw_cycles = report.get("cycles")
    cycles = raw_cycles if isinstance(raw_cycles, list) else []
    return all(
        _production_local_cycle_source_valid(cycle)
        for cycle in cycles
        if isinstance(cycle, dict)
    )


def _check_kline_lifecycle_production_local_provider(
    failures: list[dict], report: dict, mode: dict
) -> None:
    if mode.get("provider_mode") != "production-local":
        return
    evidence, requested, codes = _production_local_provider_evidence(report)
    expected_codes = {
        str(mode.get("code") or "").strip(),
        str(mode.get("switch_code") or "").strip(),
    }.difference({""})
    contract = _production_local_provider_contract(evidence, requested, codes, expected_codes)
    cached_switch = report.get("cached_switch") if isinstance(report.get("cached_switch"), dict) else {}
    cycle_sources_valid = _production_local_cycle_sources_valid(report)
    cache_contract = (
        cached_switch.get("provider_mode") == "production-local",
        cached_switch.get("cache_source") == "production_local_frozen_cache",
        cycle_sources_valid,
    )
    if not all((*contract, *cache_contract)):
        _fail(
            failures,
            "kline_lifecycle.provider.production_local",
            "K-line performance evidence did not prove targeted read-only production-local A-share data",
            expected_codes=sorted(expected_codes),
            requested_codes=requested,
            provider=evidence,
            cached_switch={
                "provider_mode": cached_switch.get("provider_mode"),
                "cache_source": cached_switch.get("cache_source"),
                "cycle_sources_valid": cycle_sources_valid,
            },
        )


def _native_lifecycle_true_fields(payload, fields: tuple[str, ...]) -> bool:
    return isinstance(payload, dict) and all(payload.get(field) is True for field in fields)


def _native_network_guard_valid(payload) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and _native_lifecycle_true_fields(payload, ("network_guard_active", "no_network_requests"))
        and payload.get("network_access_enabled") is False
        and payload.get("network_request_count_before") == 0
        and payload.get("network_request_count_after") == 0
    )


def _native_same_stock_multi_window_valid(payload) -> bool:
    fields = (
        "same_code",
        "window_ids_distinct",
        "task_ids_distinct",
        "frame_owners_isolated",
        "browser_instances_distinct",
        "first_closed",
        "second_survived_first_close",
        "second_closed",
    )
    first_open = payload.get("first_open") if isinstance(payload, dict) else None
    second_open = payload.get("second_open") if isinstance(payload, dict) else None
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and isinstance(first_open, dict)
        and first_open.get("chart_ready") is True
        and isinstance(second_open, dict)
        and second_open.get("chart_ready") is True
        and _native_lifecycle_true_fields(payload, fields)
    )


def _native_visibility_transition_valid(payload) -> bool:
    transition_fields = ("pause_observed", "runtime_reactivated", "chart_ready_after_resume")
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and _native_lifecycle_true_fields(payload, transition_fields)
    )


def _native_visibility_pause_resume_valid(payload) -> bool:
    open_evidence = payload.get("open") if isinstance(payload, dict) else None
    outer_fields = (
        "browser_preserved",
        "identity_preserved",
        "latest_snapshot_owned_after_resume",
        "frame_owner_current",
        "closed",
    )
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and isinstance(open_evidence, dict)
        and open_evidence.get("chart_ready") is True
        and _native_visibility_transition_valid(payload.get("hidden"))
        and _native_visibility_transition_valid(payload.get("minimized"))
        and _native_lifecycle_true_fields(payload, outer_fields)
    )


def _native_render_process_recovery_valid(payload) -> bool:
    fields = (
        "guard_installed",
        "controlled_termination_emitted",
        "browser_replaced",
        "browser_epoch_advanced",
        "structure_ready",
        "chart_ready_after_recovery",
        "identity_preserved",
        "frame_owner_current",
        "latest_snapshot_identity_preserved",
        "ack_received",
        "last_snapshot_replayed",
        "at_most_one_recovery",
        "closed",
    )
    open_evidence = payload.get("open") if isinstance(payload, dict) else None
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and isinstance(open_evidence, dict)
        and open_evidence.get("chart_ready") is True
        and _native_lifecycle_true_fields(payload, fields)
        and payload.get("recovery_attempts") == 1
        and payload.get("recovery_limit") == 1
        and payload.get("second_recovery_allowed") is False
    )


def _check_kline_native_lifecycle_contract(failures: list[dict], report: dict, mode: dict) -> None:
    if mode.get("provider_mode") != "production-local":
        return
    lifecycle = report.get("native_lifecycle")
    evidence = lifecycle if isinstance(lifecycle, dict) else {}
    checks = {
        "envelope": (
            evidence.get("required") is True
            and evidence.get("status") == "ok"
            and evidence.get("provider_mode") == "production-local"
        ),
        "network_guard": _native_network_guard_valid(evidence.get("network_guard")),
        "same_stock_multi_window": _native_same_stock_multi_window_valid(
            evidence.get("same_stock_multi_window")
        ),
        "visibility_pause_resume": _native_visibility_pause_resume_valid(
            evidence.get("visibility_pause_resume")
        ),
        "render_process_recovery": _native_render_process_recovery_valid(
            evidence.get("render_process_recovery")
        ),
    }
    if not all(checks.values()):
        _fail(
            failures,
            "kline_lifecycle.native_lifecycle",
            "Native production-local K-line lifecycle scenarios are missing or incomplete",
            checks=checks,
        )


def _normalized_kline_lifecycle_cycles(failures: list[dict], report: dict) -> list[dict]:
    raw_cycles = report.get("cycles")
    cycles = raw_cycles if isinstance(raw_cycles, list) else []
    if not isinstance(raw_cycles, list) or any(not isinstance(cycle, dict) for cycle in cycles):
        _fail(
            failures,
            "kline_lifecycle.cycles.diagnostics",
            "K-line lifecycle cycles must be a list of objects",
        )
        cycles = [cycle for cycle in cycles if isinstance(cycle, dict)]
    return cycles


def _check_kline_lifecycle_cycle_count_contract(
    failures: list[dict], budget: dict, report: dict, mode: dict, cycles: list[dict]
) -> None:
    minimum = int(budget["kline_lifecycle_min_cycles"])
    measured = len(cycles)
    if measured < minimum:
        _fail(
            failures,
            "kline_lifecycle.cycles.samples",
            "K-line lifecycle report has too few measured cycles",
            actual=measured,
            minimum=minimum,
        )

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    declared = {
        "mode_cycles": _optional_nonnegative_int(mode.get("cycles")),
        "mode_minimum_cycles": _optional_nonnegative_int(mode.get("minimum_cycles")),
        "summary_cycles": _optional_nonnegative_int(summary.get("cycles")),
        "summary_expected_cycles": _optional_nonnegative_int(summary.get("expected_cycles")),
        "summary_minimum_cycles": _optional_nonnegative_int(summary.get("minimum_cycles")),
    }
    exact_counts = all(
        declared[key] == measured for key in ("mode_cycles", "summary_cycles", "summary_expected_cycles")
    )
    minimum_counts = [declared["mode_minimum_cycles"], declared["summary_minimum_cycles"]]
    minimums_valid = all(value is not None and value >= minimum for value in minimum_counts)
    completion_flags = (
        summary.get("minimum_cycle_gate") is True,
        summary.get("cycle_count_complete") is True,
    )
    if not all((exact_counts, minimums_valid, *completion_flags)):
        _fail(
            failures,
            "kline_lifecycle.cycles.contract",
            "K-line lifecycle declared cycle counts or minimum-cycle evidence are inconsistent",
            actual=declared,
            measured=measured,
            minimum=minimum,
        )


def _check_kline_lifecycle_report_contract(failures: list[dict], budget: dict, report: dict) -> list[dict]:
    mode = _check_kline_lifecycle_report_identity(failures, report)
    _check_kline_lifecycle_production_local_provider(failures, report, mode)
    _check_kline_native_lifecycle_contract(failures, report, mode)
    cycles = _normalized_kline_lifecycle_cycles(failures, report)
    _check_kline_lifecycle_cycle_count_contract(failures, budget, report, mode, cycles)
    return cycles


def _expected_kline_cycle_sample_labels(label: str) -> list[str]:
    return [f"{label}:{suffix}" for suffix in _KLINE_LIFECYCLE_SAMPLE_SUFFIXES]


def _kline_cycle_samples(cycle: dict) -> list[dict]:
    samples = cycle.get("samples")
    if not isinstance(samples, list) or any(not isinstance(sample, dict) for sample in samples):
        return []
    return samples


def _kline_cycle_contract_valid(cycle: dict, *, index: int, label: str, role: str) -> bool:
    samples = _kline_cycle_samples(cycle)
    return bool(
        cycle.get("cycle_index") == index
        and cycle.get("label") == label
        and cycle.get("measurement_role") == role
        and [sample.get("label") for sample in samples] == _expected_kline_cycle_sample_labels(label)
    )


def _check_kline_lifecycle_cycle_identity(failures: list[dict], report: dict, cycles: list[dict]) -> None:
    invalid = [
        position
        for position, cycle in enumerate(cycles, start=1)
        if not _kline_cycle_contract_valid(cycle, index=position, label=f"cycle_{position}", role="measured")
    ]
    if invalid:
        _fail(
            failures,
            "kline_lifecycle.cycles.identity",
            "measured K-line cycles must have unique consecutive indexes, labels, roles, and exact sample labels",
            cycles=invalid,
        )

    warmup_contracts = (
        ("cold_warmup_cycle", "cold_warmup"),
        ("warmup_cycle", "warmup"),
    )
    invalid_warmups = [
        field
        for field, label in warmup_contracts
        if not isinstance(report.get(field), dict)
        or not _kline_cycle_contract_valid(report[field], index=0, label=label, role=label)
    ]
    if invalid_warmups:
        _fail(
            failures,
            "kline_lifecycle.warmup.identity",
            "K-line cold and steady warmup cycles must have exact identities and sample labels",
            cycles=invalid_warmups,
        )


def _check_kline_lifecycle_flat_samples(failures: list[dict], report: dict, cycles: list[dict]) -> None:
    expected = [sample for cycle in cycles for sample in _kline_cycle_samples(cycle)]
    if report.get("samples") != expected:
        _fail(
            failures,
            "kline_lifecycle.samples.contract",
            "top-level K-line samples must exactly mirror the measured-cycle samples",
        )


def _kline_stage_samples(cycles: list[dict], stage: str) -> tuple[list[float], list[int]]:
    samples = []
    invalid_cycles = []
    for position, cycle in enumerate(cycles, start=1):
        diagnostics = cycle.get("stage_diagnostics")
        timings = diagnostics.get("timings_ms") if isinstance(diagnostics, dict) else None
        value = _optional_nonnegative_number(timings.get(stage)) if isinstance(timings, dict) else None
        if value is None:
            invalid_cycles.append(_as_int(cycle.get("cycle_index"), default=position))
        else:
            samples.append(value)
    return samples, invalid_cycles


def _kline_lifecycle_stage_contract_cycles(report: dict, measured_cycles: list[dict]) -> list[tuple[str, dict]]:
    cycles: list[tuple[str, dict]] = []
    for field in ("cold_warmup_cycle", "warmup_cycle"):
        cycle = report.get(field)
        cycles.append((field, cycle if isinstance(cycle, dict) else {}))
    cycles.extend(
        (
            f"cycle_{_as_int(cycle.get('cycle_index'), default=position)}",
            cycle,
        )
        for position, cycle in enumerate(measured_cycles, start=1)
    )
    return cycles


def _check_kline_lifecycle_stage_contract(
    failures: list[dict], report: dict, measured_cycles: list[dict]
) -> None:
    invalid = []
    for label, cycle in _kline_lifecycle_stage_contract_cycles(report, measured_cycles):
        diagnostics = cycle.get("stage_diagnostics")
        timings = diagnostics.get("timings_ms") if isinstance(diagnostics, dict) else None
        if not (
            cycle.get("first_interaction_triggered") is True
            and cycle.get("first_interaction_ready") is True
            and _valid_kline_stage_cycle(diagnostics)
            and _valid_kline_stage_timings(timings)
        ):
            invalid.append(label)
    if invalid:
        _fail(
            failures,
            "kline_lifecycle.stage_contract",
            "K-line lifecycle did not prove the ordered six-stage contract through a real first interaction",
            cycles=invalid,
            expected=list(KLINE_OPEN_STAGE_ORDER),
        )


def _check_kline_stage_statistic(
    failures: list[dict],
    *,
    stage: str,
    statistic: str,
    quantile: float,
    samples: list[float],
    budget_ms: float,
) -> None:
    actual = _percentile(samples, quantile)
    if actual > budget_ms:
        _fail(
            failures,
            f"kline_lifecycle.performance.{stage}.{statistic}",
            f"K-line {stage} {statistic} exceeded budget",
            actual=actual,
            budget=budget_ms,
            samples=len(samples),
        )


def _check_kline_lifecycle_stage_performance(failures: list[dict], budget: dict, cycles: list[dict]) -> None:
    minimum = int(budget["kline_lifecycle_min_cycles"])
    stage_samples = {}
    for stage in ("shell_ready", "browser_ready", "chart_ready"):
        samples, invalid_cycles = _kline_stage_samples(cycles, stage)
        stage_samples[stage] = samples
        if invalid_cycles:
            _fail(
                failures,
                f"kline_lifecycle.performance.{stage}.diagnostics",
                f"K-line {stage} timing was missing, non-finite, or negative",
                cycles=invalid_cycles,
            )
        if len(samples) < minimum:
            _fail(
                failures,
                f"kline_lifecycle.performance.{stage}.samples",
                f"K-line {stage} has too few valid timing samples",
                actual=len(samples),
                minimum=minimum,
            )

    checks = (
        ("shell_ready", "p95", 0.95, "kline_shell_ready_p95_max_ms"),
        ("browser_ready", "p95", 0.95, "kline_browser_ready_p95_max_ms"),
        ("chart_ready", "p50", 0.50, "kline_chart_ready_p50_max_ms"),
        ("chart_ready", "p95", 0.95, "kline_chart_ready_p95_max_ms"),
    )
    for stage, statistic, quantile, budget_key in checks:
        samples = stage_samples[stage]
        if len(samples) >= minimum:
            _check_kline_stage_statistic(
                failures,
                stage=stage,
                statistic=statistic,
                quantile=quantile,
                samples=samples,
                budget_ms=float(budget[budget_key]),
            )


def _numeric_sample_list(values) -> tuple[list[float], list[int]]:
    if not isinstance(values, list):
        return [], [-1]
    samples = []
    invalid_positions = []
    for position, value in enumerate(values):
        parsed = _optional_nonnegative_number(value)
        if parsed is None:
            invalid_positions.append(position)
        else:
            samples.append(parsed)
    return samples, invalid_positions


def _check_kline_cached_switch_performance(failures: list[dict], budget: dict, report: dict) -> None:
    cached_switch = report.get("cached_switch")
    raw_samples = cached_switch.get("samples_ms") if isinstance(cached_switch, dict) else None
    samples, invalid_positions = _numeric_sample_list(raw_samples)
    if invalid_positions:
        _fail(
            failures,
            "kline_lifecycle.cached_switch.diagnostics",
            "cached K-line switch samples were missing, non-finite, or negative",
            invalid_positions=invalid_positions,
        )
    minimum = int(budget["kline_cached_switch_min_samples"])
    if len(samples) < minimum:
        _fail(
            failures,
            "kline_lifecycle.cached_switch.samples",
            "cached K-line switch has too few valid timing samples",
            actual=len(samples),
            minimum=minimum,
        )
        return
    actual = _percentile(samples, 0.95)
    maximum = float(budget["kline_cached_switch_p95_max_ms"])
    if actual > maximum:
        _fail(
            failures,
            "kline_lifecycle.cached_switch.p95",
            "cached K-line switch P95 exceeded budget",
            actual=actual,
            budget=maximum,
            samples=len(samples),
        )


def _kline_open_stall_identity(cycle: dict, position: int) -> dict:
    cycle_index = _as_int(cycle.get("cycle_index"), default=position)
    return {
        "cycle": cycle_index,
        "label": str(cycle.get("label") or f"cycle_{cycle_index}"),
        "measurement_role": str(cycle.get("measurement_role") or "unknown"),
    }


def _kline_open_stall_sample(cycle: dict, position: int) -> tuple[dict, dict | None]:
    identity = _kline_open_stall_identity(cycle, position)
    stalls = cycle.get("ui_stalls")
    if not isinstance(stalls, dict) or stalls.get("installed") is not True:
        return identity, None
    values = {
        "critical_count": _optional_nonnegative_int(stalls.get("critical_count")),
        "event_loop_critical_count": _optional_nonnegative_int(stalls.get("event_loop_critical_count")),
        "max_elapsed_ms": _optional_nonnegative_number(stalls.get("max_elapsed_ms")),
    }
    return (identity, None) if any(value is None for value in values.values()) else (identity, values)


def _kline_open_stall_series(cycles: list[dict]) -> tuple[dict[str, list[dict]], list[int]]:
    series = {
        "critical_count": [],
        "event_loop_critical_count": [],
        "max_elapsed_ms": [],
    }
    invalid_cycles = []
    for position, cycle in enumerate(cycles, start=1):
        identity, values = _kline_open_stall_sample(cycle, position)
        if values is None:
            invalid_cycles.append(identity["cycle"])
            continue
        for field, value in values.items():
            series[field].append({**identity, "stage": field, "actual": value})
    return series, invalid_cycles


def _check_kline_stall_peak(
    failures: list[dict], budget: dict, values: list[dict], budget_key: str, check: str, detail: str
) -> None:
    if not values:
        return
    maximum = budget[budget_key]
    violations = [item for item in values if item["actual"] > maximum]
    if violations:
        peak = max(violations, key=lambda item: (item["actual"], item["cycle"]))
        _fail(
            failures,
            check,
            detail,
            actual=peak["actual"],
            budget=maximum,
            cycle=peak["cycle"],
            label=peak["label"],
            measurement_role=peak["measurement_role"],
            violations=violations,
        )


def _check_kline_open_stalls(failures: list[dict], budget: dict, cycles: list[dict]) -> None:
    series, invalid_cycles = _kline_open_stall_series(cycles)
    if invalid_cycles:
        _fail(
            failures,
            "kline_lifecycle.ui_stall.diagnostics",
            "per-open K-line UI stall diagnostics were missing or invalid",
            cycles=invalid_cycles,
        )
    checks = (
        ("critical_count", "kline_open_ui_critical_stall_max", "critical_count"),
        ("event_loop_critical_count", "kline_open_ui_event_loop_critical_stall_max", "event_loop_critical_count"),
        ("max_elapsed_ms", "kline_open_ui_max_stall_ms", "max_elapsed"),
    )
    for field, budget_key, check_suffix in checks:
        _check_kline_stall_peak(
            failures,
            budget,
            series[field],
            budget_key,
            f"kline_lifecycle.ui_stall.{check_suffix}",
            f"K-line {field} exceeded budget",
        )


def _kline_lifecycle_open_stall_cycles(
    failures: list[dict], report: dict, measured_cycles: list[dict]
) -> list[dict]:
    cycles = []
    for field in ("cold_warmup_cycle", "warmup_cycle"):
        cycle = report.get(field)
        if not isinstance(cycle, dict):
            _fail(
                failures,
                f"kline_lifecycle.ui_stall.{field}.diagnostics",
                f"K-line {field} evidence is missing or invalid",
            )
            continue
        cycles.append(cycle)
    cycles.extend(measured_cycles)
    return cycles


def _kline_lifecycle_cycle_samples(report: dict, cycles: list[dict]) -> list[dict]:
    evidence = []
    for field in ("cold_warmup_cycle", "warmup_cycle"):
        cycle = report.get(field)
        if isinstance(cycle, dict):
            evidence.extend(_kline_cycle_samples(cycle))
    for cycle in cycles:
        evidence.extend(_kline_cycle_samples(cycle))
    return evidence


def _invalid_kline_resource_sample_fields(sample: dict) -> list[str]:
    invalid = [] if sample.get("webengine_available") is True else ["webengine_available"]
    invalid.extend(
        field
        for field in _KLINE_LIFECYCLE_SAMPLE_RESOURCE_FIELDS
        if _optional_nonnegative_number(sample.get(field)) is None
    )
    return invalid


def _check_kline_lifecycle_sample_resources(failures: list[dict], report: dict, cycles: list[dict]) -> None:
    samples = _kline_lifecycle_cycle_samples(report, cycles)
    shutdown = report.get("shutdown")
    post_close = shutdown.get("post_close") if isinstance(shutdown, dict) else None
    if isinstance(post_close, dict):
        samples.append(post_close)
    invalid = [
        {
            "label": sample.get("label"),
            "fields": _invalid_kline_resource_sample_fields(sample),
        }
        for sample in samples
        if _invalid_kline_resource_sample_fields(sample)
    ]
    if invalid:
        _fail(
            failures,
            "kline_lifecycle.resources.sample_diagnostics",
            "every lifecycle sample must contain finite non-negative resource diagnostics",
            samples=invalid,
        )


def _exact_kline_cycle_sample(cycle: dict, label: str) -> dict | None:
    matches = [sample for sample in _kline_cycle_samples(cycle) if sample.get("label") == label]
    return matches[0] if len(matches) == 1 else None


def _kline_resource_evidence_samples(report: dict, cycles: list[dict]) -> tuple[dict | None, dict | None]:
    warmup = report.get("warmup_cycle")
    final_cycle = cycles[-1] if cycles else None
    baseline = _exact_kline_cycle_sample(warmup, "warmup:after_close") if isinstance(warmup, dict) else None
    final_label = f"cycle_{len(cycles)}:after_close"
    final = _exact_kline_cycle_sample(final_cycle, final_label) if isinstance(final_cycle, dict) else None
    return baseline, final


def _same_finite_number(actual, expected: float) -> bool:
    parsed = _optional_finite_number(actual)
    return parsed is not None and math.isclose(parsed, expected, rel_tol=0.0, abs_tol=1e-6)


def _kline_recomputed_resource_valid(
    item, *, baseline: float, final: float, delta: float, maximum: float, status: str
) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("available") is True
        and _same_finite_number(item.get("baseline"), baseline)
        and _same_finite_number(item.get("final"), final)
        and _same_finite_number(item.get("delta"), delta)
        and _same_finite_number(item.get("budget"), maximum)
        and item.get("status") == status
    )


def _check_kline_recomputed_resource_field(
    failures: list[dict], resources: dict, field: str, baseline: dict, final: dict, maximum: float
) -> None:
    baseline_value = _optional_nonnegative_number(baseline.get(field))
    final_value = _optional_nonnegative_number(final.get(field))
    if baseline_value is None or final_value is None:
        _fail(
            failures,
            f"kline_lifecycle.resources.{field}.recomputed",
            f"K-line lifecycle {field} raw samples are missing or invalid",
        )
        return
    delta = final_value - baseline_value
    expected_status = "ok" if delta <= maximum else "fail"
    item = resources.get(field)
    valid = _kline_recomputed_resource_valid(
        item,
        baseline=baseline_value,
        final=final_value,
        delta=delta,
        maximum=maximum,
        status=expected_status,
    )
    if not valid:
        _fail(
            failures,
            f"kline_lifecycle.resources.{field}.recomputed",
            f"K-line lifecycle {field} summary does not match the raw warm-to-final samples",
            expected={"baseline": baseline_value, "final": final_value, "delta": delta, "status": expected_status},
            actual=item,
        )


def _check_kline_resource_growth_metadata(
    failures: list[dict], report: dict, baseline: dict, final: dict, resources: dict
) -> None:
    growth = report.get("resource_growth")
    valid = bool(
        isinstance(growth, dict)
        and growth.get("status") == "ok"
        and growth.get("basis") == "warmup_after_close_to_last_measured_after_close"
        and growth.get("warm_baseline_label") == baseline.get("label")
        and growth.get("measured_final_label") == final.get("label")
        and growth.get("resource_net_growth") == resources
    )
    if not valid:
        _fail(
            failures,
            "kline_lifecycle.resources.basis",
            "K-line resource growth evidence must use the exact warmup-to-last-measured samples",
        )


def _kline_lifecycle_resource_summary(failures: list[dict], report: dict) -> dict | None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    resources = summary.get("resource_net_growth")
    if not isinstance(resources, dict):
        _fail(
            failures,
            "kline_lifecycle.resources.diagnostics",
            "K-line lifecycle resource net-growth diagnostics are missing",
        )
        return None
    if resources.get("diagnostics_available") is not True:
        _fail(
            failures,
            "kline_lifecycle.resources.diagnostics",
            "K-line lifecycle resource net-growth diagnostics are unavailable",
            actual=resources.get("diagnostics_available"),
        )
    if resources.get("status") != "ok":
        _fail(
            failures,
            "kline_lifecycle.resources.status",
            "K-line lifecycle resource net-growth summary did not pass",
            actual=resources.get("status"),
        )
    return resources


def _check_kline_lifecycle_resource_field(failures: list[dict], resources: dict, field: str, maximum: float) -> None:
    item = resources.get(field)
    delta = _optional_finite_number(item.get("delta")) if isinstance(item, dict) else None
    if not isinstance(item, dict) or item.get("available") is not True or delta is None:
        _fail(
            failures,
            f"kline_lifecycle.resources.{field}.diagnostics",
            f"K-line lifecycle {field} net-growth evidence is missing or invalid",
            actual=item,
        )
        return
    if item.get("status") != "ok":
        _fail(
            failures,
            f"kline_lifecycle.resources.{field}.status",
            f"K-line lifecycle {field} resource status did not pass",
            actual=item.get("status"),
        )
    if delta > maximum:
        _fail(
            failures,
            f"kline_lifecycle.resources.{field}.net_growth",
            f"K-line lifecycle {field} net growth exceeded budget",
            actual=delta,
            budget=maximum,
        )


def _check_kline_lifecycle_resources(
    failures: list[dict], budget: dict, report: dict, cycles: list[dict]
) -> None:
    resources = _kline_lifecycle_resource_summary(failures, report)
    if resources is None:
        return
    limits = {field: 0.0 for field in _KLINE_LIFECYCLE_ZERO_GROWTH_FIELDS}
    limits["rss_mb"] = float(budget["kline_lifecycle_rss_net_growth_max_mb"])
    baseline, final = _kline_resource_evidence_samples(report, cycles)
    if baseline is None or final is None:
        _fail(
            failures,
            "kline_lifecycle.resources.samples",
            "K-line resource growth requires exact warmup and last measured after-close samples",
        )
        return
    _check_kline_resource_growth_metadata(failures, report, baseline, final, resources)
    for field, maximum in limits.items():
        _check_kline_recomputed_resource_field(failures, resources, field, baseline, final, maximum)
        _check_kline_lifecycle_resource_field(failures, resources, field, maximum)


def _check_kline_lifecycle_summary(failures: list[dict], budget: dict, report: dict) -> None:
    summary = report.get("summary") or {}
    if summary.get("status") != "ok":
        _fail(
            failures,
            "kline_lifecycle.status",
            "K-line lifecycle smoke did not finish cleanly",
            actual=summary.get("status"),
            failed_cycles=summary.get("failed_cycles") or [],
        )

    final_children = _optional_nonnegative_int(summary.get("final_webengine_child_count"))
    if final_children is None:
        _fail(
            failures,
            "kline_lifecycle.webengine_children.final_diagnostics",
            "final QtWebEngine child-process diagnostics are missing or invalid",
            actual=summary.get("final_webengine_child_count"),
        )
    elif final_children > budget["kline_max_final_webengine_children"]:
        _fail(
            failures,
            "kline_lifecycle.webengine_children.final",
            "QtWebEngine child processes remained after lifecycle smoke",
            actual=final_children,
            budget=budget["kline_max_final_webengine_children"],
        )


def _kline_shutdown_contract_valid(shutdown: dict, post_close: dict, children: int | None) -> bool:
    return bool(
        shutdown.get("included_in_lifecycle_resource_growth") is False
        and post_close.get("label") == "shutdown:post_close"
        and post_close.get("webengine_available") is True
        and children == 0
    )


def _kline_shutdown_summary_valid(summary: dict, shutdown_children: int | None, steady_children: int | None) -> bool:
    return bool(
        summary.get("shutdown_webengine_diagnostics_available") is True
        and _optional_nonnegative_int(summary.get("final_webengine_child_count")) == shutdown_children == 0
        and _optional_nonnegative_int(summary.get("steady_state_final_webengine_child_count")) == steady_children
    )


def _check_kline_lifecycle_shutdown(failures: list[dict], report: dict, cycles: list[dict]) -> None:
    shutdown = report.get("shutdown")
    post_close = shutdown.get("post_close") if isinstance(shutdown, dict) else None
    if not isinstance(shutdown, dict) or not isinstance(post_close, dict):
        _fail(
            failures,
            "kline_lifecycle.shutdown.diagnostics",
            "K-line lifecycle shutdown and post-close evidence are required",
        )
        return
    shutdown_children = _optional_nonnegative_int(post_close.get("webengine_child_count"))
    contract_valid = _kline_shutdown_contract_valid(shutdown, post_close, shutdown_children)
    if not contract_valid:
        _fail(
            failures,
            "kline_lifecycle.shutdown.contract",
            "K-line shutdown must prove a native post-close sample with zero WebEngine children",
            actual=post_close,
        )

    _, final_sample = _kline_resource_evidence_samples(report, cycles)
    steady_children = (
        _optional_nonnegative_int(final_sample.get("webengine_child_count"))
        if isinstance(final_sample, dict)
        else None
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    summary_valid = _kline_shutdown_summary_valid(summary, shutdown_children, steady_children)
    if not summary_valid:
        _fail(
            failures,
            "kline_lifecycle.shutdown.summary",
            "K-line shutdown summary does not match raw steady-state and post-close samples",
        )


def _kline_lifecycle_after_close_budget(budget: dict, managed_keeper_count: int) -> int:
    if managed_keeper_count == 1:
        return budget["runtime_health_managed_webengine_process_final_max"]
    return budget["kline_max_final_webengine_children"]


def _check_kline_lifecycle_cycle_status(failures: list[dict], cycle: dict, cycle_index: int) -> None:
    cycle_summary = cycle.get("summary") or {}
    if cycle_summary.get("status") != "ok":
        _fail(
            failures,
            "kline_lifecycle.cycle.status",
            "K-line lifecycle cycle failed",
            cycle=cycle_index,
            actual=cycle_summary.get("status"),
        )


def _check_kline_lifecycle_after_close(
    failures: list[dict], cycle: dict, cycle_index: int, after_close_budget: int
) -> None:
    samples = cycle.get("samples")
    after_close = (
        [
            sample
            for sample in samples
            if isinstance(sample, dict) and str(sample.get("label") or "").endswith(":after_close")
        ]
        if isinstance(samples, list)
        else []
    )
    if len(after_close) != 1:
        _fail(
            failures,
            "kline_lifecycle.webengine_children.after_close_diagnostics",
            "lifecycle cycle must contain exactly one after-close WebEngine sample",
            cycle=cycle_index,
            actual=len(after_close),
        )
        return
    sample = after_close[0]
    children = _optional_nonnegative_int(sample.get("webengine_child_count"))
    if children is None:
        _fail(
            failures,
            "kline_lifecycle.webengine_children.after_close_diagnostics",
            "after-close QtWebEngine child-process diagnostics are missing or invalid",
            cycle=cycle_index,
            actual=sample.get("webengine_child_count"),
        )
    elif children > after_close_budget:
        _fail(
            failures,
            "kline_lifecycle.webengine_children.after_close",
            "QtWebEngine child process remained after lifecycle close sample",
            cycle=cycle_index,
            label=sample.get("label"),
            actual=children,
            budget=after_close_budget,
        )


def _check_kline_lifecycle_keeper_cycle(
    failures: list[dict], cycle: dict, cycle_index: int, managed_keeper_count: int
) -> None:
    if managed_keeper_count != 1:
        return
    keeper_contract = (
        cycle.get("baseline_managed_webengine_keeper_count") == 1,
        cycle.get("final_managed_webengine_keeper_count") == 1,
        cycle.get("baseline_managed_webengine_keeper_ready") is True,
        cycle.get("final_managed_webengine_keeper_ready") is True,
        cycle.get("active_chart_view_count_after_close") == 0,
    )
    if all(keeper_contract):
        return
    _fail(
        failures,
        "kline_lifecycle.webengine_keeper.cycle",
        "Managed WebEngine keeper was not ready and stable for the lifecycle cycle",
        cycle=cycle_index,
    )


def _check_kline_lifecycle_cycles(
    failures: list[dict],
    budget: dict,
    cycles: list[dict],
    *,
    managed_keeper_count: int = 0,
) -> None:
    after_close_budget = _kline_lifecycle_after_close_budget(budget, managed_keeper_count)
    for cycle in cycles or []:
        cycle_index = _as_int(cycle.get("cycle_index"))
        _check_kline_lifecycle_cycle_status(failures, cycle, cycle_index)
        _check_kline_lifecycle_after_close(failures, cycle, cycle_index, after_close_budget)
        _check_kline_lifecycle_keeper_cycle(failures, cycle, cycle_index, managed_keeper_count)


def check_kline_lifecycle_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []

    cycles = _check_kline_lifecycle_report_contract(failures, budget, report)
    _check_kline_lifecycle_cycle_identity(failures, report, cycles)
    _check_kline_lifecycle_flat_samples(failures, report, cycles)
    _check_kline_lifecycle_sample_resources(failures, report, cycles)
    _check_kline_lifecycle_summary(failures, budget, report)
    _check_kline_lifecycle_shutdown(failures, report, cycles)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    managed_keeper_count = _optional_nonnegative_int(summary.get("managed_webengine_keeper_count_during_cycles"))
    if managed_keeper_count != 1:
        _fail(
            failures,
            "kline_lifecycle.webengine_keeper.count",
            "Prewarmed K-line lifecycle evidence requires exactly one managed WebEngine keeper",
            actual=managed_keeper_count,
            budget=1,
        )
    if summary.get("managed_webengine_keeper_ready_during_cycles") is not True:
        _fail(
            failures,
            "kline_lifecycle.webengine_keeper.ready",
            "Managed WebEngine keeper did not complete its first page load",
            actual=summary.get("managed_webengine_keeper_ready_during_cycles"),
        )
    _check_kline_lifecycle_cycles(
        failures,
        budget,
        cycles,
        managed_keeper_count=managed_keeper_count or 0,
    )
    _check_kline_lifecycle_stage_contract(failures, report, cycles)
    _check_kline_lifecycle_stage_performance(failures, budget, cycles)
    _check_kline_cached_switch_performance(failures, budget, report)
    stall_cycles = _kline_lifecycle_open_stall_cycles(failures, report, cycles)
    _check_kline_open_stalls(failures, budget, stall_cycles)
    _check_kline_lifecycle_resources(failures, budget, report, cycles)
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


def _check_round4_startup(failures: list[dict], budget: dict, startup: dict) -> None:
    if not startup:
        _fail(failures, "round4.startup.present", "round4 startup report is missing")
        return

    elapsed = _as_float(startup.get("main_window_ready_ms"))
    if elapsed > budget["round4_startup_main_window_ready_max_ms"]:
        _fail(
            failures,
            "round4.startup.main_window_ready",
            "startup main-window-ready elapsed time exceeded budget",
            actual=elapsed,
            budget=budget["round4_startup_main_window_ready_max_ms"],
        )


def _check_round4_tabs(failures: list[dict], budget: dict, tab_first_open: dict) -> None:
    tabs = tab_first_open.get("tabs") or []
    if not isinstance(tabs, list) or not tabs:
        _fail(failures, "round4.tabs.present", "round4 tab first-open samples are missing")
    for tab in tabs if isinstance(tabs, list) else []:
        key = str(tab.get("key") or "")
        if tab.get("status") not in {"ok", None}:
            _fail(
                failures,
                "round4.tabs.status",
                "tab first-open did not complete cleanly",
                key=key,
                actual=tab.get("status"),
            )
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


def _check_round4_f5(failures: list[dict], budget: dict, f5_refresh: dict) -> None:
    if not f5_refresh:
        _fail(failures, "round4.f5.present", "round4 F5 refresh report is missing")
        return

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


def _check_round4_stability(failures: list[dict], budget: dict, stability: dict) -> None:
    if not stability:
        _fail(failures, "round4.stability.present", "round4 stability report is missing")
        return

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


def check_round4_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    _check_round4_startup(failures, budget, report.get("startup") or {})
    _check_round4_tabs(failures, budget, report.get("tab_first_open") or {})
    _check_round4_f5(failures, budget, report.get("f5_refresh") or {})
    _check_round4_stability(failures, budget, report.get("stability") or {})

    return failures


def _check_round5_mode(failures: list[dict], mode: dict) -> None:
    if isinstance(mode, dict) and mode.get("isolate_info_source_refresh") is True:
        _fail(
            failures,
            "round5.mode.isolated_info_source_refresh",
            "round5 report was captured with information-source refresh isolation enabled; rerun with --no-isolate-info-source-refresh for a full post-F5 regression gate",
        )


def _check_round5_quotes(failures: list[dict], budget: dict, quote_requests: dict) -> None:
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


def _check_round5_cache_guard(failures: list[dict], budget: dict, guard: dict) -> None:
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


def _check_round5_background(failures: list[dict], budget: dict, background_tasks: dict) -> None:
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


def _check_round5_runtime_trend(failures: list[dict], budget: dict, trend: dict) -> None:
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


def _check_round5_receiver_trend(failures: list[dict], budget: dict, receiver_trend: dict) -> None:
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


def check_round5_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    _check_round5_mode(failures, report.get("mode") or {})

    post_f5 = report.get("post_f5") or {}
    if not post_f5:
        _fail(failures, "round5.post_f5.present", "round5 post-F5 report is missing")
        return failures

    _check_round5_quotes(failures, budget, post_f5.get("quote_requests") or {})
    _check_round5_cache_guard(failures, budget, post_f5.get("cache_only_guard") or {})
    _check_round5_background(failures, budget, post_f5.get("background_tasks") or {})
    _check_round5_runtime_trend(failures, budget, post_f5.get("runtime_trend") or {})
    _check_round5_receiver_trend(failures, budget, post_f5.get("event_receiver_trend") or {})
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
        except AttributeError, RuntimeError, TypeError, ValueError:
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
    tail = values[-max(1, int(tail_count)) :]
    return round(max(tail) - min(tail), 3) if tail else 0.0


def _runtime_health_sample_rss_mb(sample: dict) -> float | None:
    if not isinstance(sample, dict):
        return None
    value = sample.get("rss_mb")
    if value is None:
        value = (sample.get("process") or {}).get("rss_mb")
    try:
        return float(value) if value is not None else None
    except TypeError, ValueError:
        return None


def _runtime_health_post_workload_rss_trend(report: dict, samples: list[dict]) -> dict:
    cycle_samples = list(((report.get("kline_cycle") or {}).get("cycle_samples") or []))
    values = [
        value
        for value in (
            _runtime_health_sample_rss_mb(sample)
            for sample in cycle_samples
            if str(sample.get("label") or "").endswith(":after_close")
        )
        if value is not None
    ]
    basis = "post_kline_close_samples"
    if values and samples:
        final_value = _runtime_health_sample_rss_mb(samples[-1])
        if final_value is not None:
            values.append(final_value)
    if len(values) < 2:
        stable_labels = {
            "after_tab_cycle",
            "after_tab_async_tail",
            "after_f5_cycle",
            "after_quote_cycle",
            "final",
        }
        tail_values = [
            value
            for value in (
                _runtime_health_sample_rss_mb(sample)
                for sample in samples
                if str(sample.get("label") or "") in stable_labels
            )
            if value is not None
        ]
        if len(tail_values) >= 2:
            values = tail_values[-3:]
            basis = "tail_runtime_health_samples"
    if len(values) < 2:
        return {}
    trend = _runtime_health_trend_one(values)
    trend["tail_range"] = _tail_range(values)
    trend["basis"] = basis
    return trend


def _requested_runtime_health_tabs(report: dict) -> list[str]:
    mode = report.get("mode") or {}
    tabs = mode.get("tabs") if isinstance(mode, dict) else []
    return [str(tab or "").strip() for tab in (tabs or []) if str(tab or "").strip()]


def _lineage_by_key(lineage: list[dict]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for item in lineage:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("view") or "").strip()
        if key:
            entries[key] = item
    return entries


def _normalized_lineage_keys(values: list) -> set[str]:
    return {str(key or "").strip() for key in values if str(key or "").strip()}


def _runtime_health_lineage_coverage(last: dict, failures: list[dict]) -> tuple[set[str], set[str]] | None:
    coverage = last.get("data_lineage_coverage")
    if coverage is None:
        return None
    if not isinstance(coverage, dict):
        _fail(
            failures,
            "runtime_health.data_lineage.coverage.type",
            "runtime health data lineage coverage must be an object",
        )
        return None

    covered_raw = coverage.get("covered")
    excluded_raw = coverage.get("excluded")
    if not isinstance(covered_raw, list) or not isinstance(excluded_raw, list):
        _fail(
            failures,
            "runtime_health.data_lineage.coverage.type",
            "runtime health data lineage covered and excluded fields must be lists",
        )
        return None
    return _normalized_lineage_keys(covered_raw), _normalized_lineage_keys(excluded_raw)


def _check_runtime_health_lineage_coverage(
    covered: set[str],
    excluded: set[str],
    requested_tabs: list[str],
    failures: list[dict],
) -> None:
    overlap = sorted(covered & excluded)
    if overlap:
        _fail(
            failures,
            "runtime_health.data_lineage.coverage.overlap",
            "runtime health tabs cannot be both lineage-covered and lineage-excluded",
            tabs=overlap,
        )

    unclassified = sorted(set(requested_tabs) - covered - excluded)
    if unclassified:
        _fail(
            failures,
            "runtime_health.data_lineage.coverage.requested_tabs",
            "runtime health requested tabs are missing lineage coverage or an explicit exclusion",
            missing=unclassified,
        )


def _runtime_health_lineage_partition(
    last: dict,
    requested_tabs: list[str],
    failures: list[dict],
) -> tuple[list[str], set[str]]:
    coverage = _runtime_health_lineage_coverage(last, failures)
    if coverage is None:
        return requested_tabs, set()
    covered, excluded = coverage
    _check_runtime_health_lineage_coverage(covered, excluded, requested_tabs, failures)
    return [tab for tab in requested_tabs if tab in covered], excluded


def _check_lineage_exclusion_declarations(
    declared_excluded: set[str],
    exclusions: dict[str, dict],
    failures: list[dict],
) -> None:
    missing = sorted(declared_excluded - set(exclusions))
    if missing:
        _fail(
            failures,
            "runtime_health.data_lineage.exclusions.declared_tabs",
            "declared lineage exclusions are missing exclusion details",
            missing=missing,
        )

    undeclared = sorted(set(exclusions) - declared_excluded)
    if undeclared:
        _fail(
            failures,
            "runtime_health.data_lineage.exclusions.undeclared_tabs",
            "runtime health lineage exclusions contain undeclared tabs",
            tabs=undeclared,
        )


def _missing_lineage_exclusion_fields(exclusion: dict) -> list[str]:
    required_fields = ("key", "reason", "description", "loaded")
    missing_fields = []
    for field in required_fields:
        if field not in exclusion:
            missing_fields.append(field)
            continue
        if field != "loaded" and not str(exclusion.get(field) or "").strip():
            missing_fields.append(field)
    return missing_fields


def _check_lineage_exclusion_fields(
    declared_excluded: set[str],
    exclusions: dict[str, dict],
    failures: list[dict],
) -> None:
    missing_fields = {}
    for key in sorted(declared_excluded & set(exclusions)):
        fields = _missing_lineage_exclusion_fields(exclusions[key])
        if fields:
            missing_fields[key] = fields
    if missing_fields:
        _fail(
            failures,
            "runtime_health.data_lineage.exclusions.fields",
            "runtime health data lineage exclusions are missing required fields",
            missing=missing_fields,
        )


def _check_lineage_exclusion_overlap(
    declared_excluded: set[str],
    lineage_entries: dict[str, dict],
    failures: list[dict],
) -> None:
    overlap = sorted(declared_excluded & set(lineage_entries))
    if overlap:
        _fail(
            failures,
            "runtime_health.data_lineage.exclusions.lineage_overlap",
            "lineage-excluded tabs must not emit data lineage entries",
            tabs=overlap,
        )


def _check_runtime_health_lineage_exclusions(
    last: dict,
    declared_excluded: set[str],
    lineage_entries: dict[str, dict],
    failures: list[dict],
) -> None:
    raw_exclusions = last.get("data_lineage_exclusions")
    if raw_exclusions is None and not declared_excluded:
        return
    if not isinstance(raw_exclusions, list):
        _fail(
            failures,
            "runtime_health.data_lineage.exclusions.type",
            "runtime health data lineage exclusions must be a list",
        )
        return

    exclusions = _lineage_by_key(raw_exclusions)
    _check_lineage_exclusion_declarations(declared_excluded, exclusions, failures)
    _check_lineage_exclusion_fields(declared_excluded, exclusions, failures)
    _check_lineage_exclusion_overlap(declared_excluded, lineage_entries, failures)


def _runtime_health_startup_ready_ms(report: dict) -> float | None:
    value = _optional_float(report.get("startup_ready_ms"))
    if value is not None:
        return value
    startup = (report.get("startup_lazy_budget") or {}).get("startup") or {}
    return _optional_float(startup.get("main_window_ready_ms"))


def _runtime_health_initial_tab_ready_ms(report: dict) -> float | None:
    value = _optional_nonnegative_number(_runtime_health_initial_tab_value(report, "initial_tab_ready_ms"))
    if value is not None:
        return value
    return None


def _runtime_health_initial_tab_value(report: dict, key: str):
    if key in report:
        return report.get(key)
    phases = report.get("startup_phases")
    if isinstance(phases, dict) and key in phases:
        return phases.get(key)
    startup = (report.get("startup_lazy_budget") or {}).get("startup") or {}
    if key in startup:
        return startup.get(key)
    startup_phases = startup.get("phases")
    return startup_phases.get(key) if isinstance(startup_phases, dict) else None


def _runtime_health_is_production_probe(report: dict) -> bool:
    mode = report.get("mode")
    if not isinstance(mode, dict):
        return False
    return all(mode.get(key) is True for key in ("native_qt", "show_window", "startup_enabled"))


def _runtime_health_is_production_full(report: dict) -> bool:
    return report.get("validation_profile") == "production_full"


def _production_mode_invalid_fields(mode: dict) -> list[str]:
    errors = []
    for field in (
        "native_qt",
        "show_window",
        "startup_enabled",
        "background_prewarm",
        "central_quotes_enabled",
        "kline_prewarm_enabled",
    ):
        if mode.get(field) is not True:
            errors.append(field)
    if mode.get("f5_probe_mode") != "real_process":
        errors.append("f5_probe_mode")
    if mode.get("tabs") != list(RUNTIME_HEALTH_PROBE_ORDER):
        errors.append("tabs")
    for field in ("tab_cycles", "f5_cycles", "quote_cycles", "kline_cycles"):
        value = _optional_nonnegative_int(mode.get(field))
        if value is None or value <= 0:
            errors.append(field)
    post_tab_idle_timeout = _optional_nonnegative_int(mode.get("post_tab_idle_timeout_ms"))
    if post_tab_idle_timeout is None or post_tab_idle_timeout <= 0:
        errors.append("post_tab_idle_timeout_ms")
    return errors


def _check_runtime_health_production_profile(report: dict, failures: list[dict]) -> None:
    suite_report = report.get("report_type") == "runtime_health_stability_suite"
    profile = report.get("validation_profile")
    if suite_report and (not isinstance(profile, str) or not profile.strip()):
        _fail(
            failures,
            "runtime_health.production_profile",
            "runtime health stability suite is missing an explicit validation profile",
            actual=profile,
        )
    production_candidate = _runtime_health_is_production_full(report) or (
        suite_report and _runtime_health_is_production_probe(report)
    )
    if not production_candidate:
        return
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    invalid_fields = _production_mode_invalid_fields(mode)
    if profile != "production_full":
        invalid_fields.append("validation_profile")
    if invalid_fields:
        _fail(
            failures,
            "runtime_health.production_profile",
            "production runtime health profile or mode contract is incomplete",
            invalid_fields=sorted(set(invalid_fields)),
            actual_profile=profile,
        )


def _check_runtime_health_unhandled_ui_exceptions(report: dict, failures: list[dict]) -> None:
    if report.get("report_type") != "runtime_health_stability_suite" and not _runtime_health_is_production_full(
        report
    ):
        return
    exceptions = report.get("unhandled_ui_exceptions")
    if not isinstance(exceptions, list) or exceptions:
        _fail(
            failures,
            "runtime_health.unhandled_ui_exceptions",
            "runtime health suite must provide an empty typed unhandled-UI-exception receipt",
            actual=exceptions,
        )


def _check_window_visibility_duration(mode: dict, evidence: dict, failures: list[dict]) -> None:
    mode_seconds = _optional_nonnegative_int(mode.get("idle_seconds"))
    planned = _optional_nonnegative_int(evidence.get("planned_observation_seconds"))
    actual = _optional_nonnegative_number(evidence.get("actual_observation_seconds"))
    if mode_seconds is not None and planned == mode_seconds and actual is not None and actual >= planned:
        return
    _fail(
        failures,
        "runtime_health.window_visibility.duration",
        "show-window visibility was not observed for the full planned idle duration",
        mode_idle_seconds=mode.get("idle_seconds"),
        planned_observation_seconds=evidence.get("planned_observation_seconds"),
        actual_observation_seconds=evidence.get("actual_observation_seconds"),
    )


def _check_window_visibility_status(evidence: dict, failures: list[dict]) -> None:
    status = evidence.get("status")
    first_invisible_at = evidence.get("first_invisible_at_seconds")
    first_invisible_reason = evidence.get("first_invisible_reason")
    if status == "ok" and first_invisible_at is None and first_invisible_reason is None:
        return
    _fail(
        failures,
        "runtime_health.window_visibility.status",
        "show-window runtime health probe did not remain visible throughout idle soak",
        actual=status,
        first_invisible_at_seconds=first_invisible_at,
        first_invisible_reason=first_invisible_reason,
    )


def _check_window_visibility_evidence(evidence: dict, failures: list[dict]) -> None:
    required_fields = (
        "required",
        "status",
        "planned_observation_seconds",
        "actual_observation_seconds",
        "first_invisible_at_seconds",
        "first_invisible_reason",
    )
    missing = [field for field in required_fields if field not in evidence]
    if missing:
        _fail(
            failures,
            "runtime_health.window_visibility.evidence",
            "show-window visibility evidence is missing required fields",
            missing=missing,
        )
    if evidence.get("required") is not True:
        _fail(
            failures,
            "runtime_health.window_visibility.required",
            "show-window runtime health probe did not require continuous visibility",
            actual=evidence.get("required"),
        )


def _check_runtime_health_window_visibility(report: dict, failures: list[dict]) -> None:
    mode = report.get("mode")
    if not isinstance(mode, dict) or mode.get("show_window") is not True:
        return
    evidence = report.get("window_visibility")
    if not isinstance(evidence, dict):
        _fail(
            failures,
            "runtime_health.window_visibility.evidence",
            "show-window runtime health report is missing window visibility evidence",
        )
        return
    _check_window_visibility_evidence(evidence, failures)
    _check_window_visibility_duration(mode, evidence, failures)
    _check_window_visibility_status(evidence, failures)


def _idle_sample_seconds(samples: list[dict]) -> tuple[list[int], list[str]]:
    positions: list[int] = []
    invalid: list[str] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        label = str(sample.get("label") or "")
        if not label.startswith("idle:"):
            continue
        if not label.endswith("s"):
            invalid.append(label)
            continue
        try:
            value = int(label[len("idle:") : -1])
        except ValueError:
            invalid.append(label)
            continue
        if value <= 0:
            invalid.append(label)
            continue
        positions.append(value)
    return positions, invalid


def _soak_sampling_complete(positions: list[int], *, duration: int, cadence: int) -> bool:
    if not positions or positions[0] > cadence or positions[-1] > duration:
        return False
    if duration - positions[-1] >= cadence:
        return False
    if len(positions) < duration // cadence:
        return False
    return all(0 < current - previous <= cadence for previous, current in pairwise(positions))


def _runtime_health_soak_duration_evidence(report: dict, mode: dict) -> dict:
    visibility = report.get("window_visibility")
    visibility = visibility if isinstance(visibility, dict) else {}
    return {
        "idle": _optional_nonnegative_int(mode.get("idle_seconds")),
        "planned": _optional_nonnegative_int(visibility.get("planned_observation_seconds")),
        "actual": _optional_nonnegative_number(visibility.get("actual_observation_seconds")),
        "raw_idle": mode.get("idle_seconds"),
        "raw_planned": visibility.get("planned_observation_seconds"),
        "raw_actual": visibility.get("actual_observation_seconds"),
    }


def _check_runtime_health_soak_duration(mode_name, minimum, evidence, failures) -> None:
    complete = all(evidence[field] is not None and evidence[field] >= minimum for field in ("idle", "planned", "actual"))
    if complete:
        return
    _fail(
        failures,
        "runtime_health.soak.minimum_duration",
        "runtime health soak mode did not observe its fixed minimum visible-window duration",
        mode=mode_name,
        mode_idle_seconds=evidence["raw_idle"],
        planned_observation_seconds=evidence["raw_planned"],
        actual_observation_seconds=evidence["raw_actual"],
        budget=minimum,
    )


def _check_runtime_health_soak_sampling(mode, samples, minimum, idle_seconds, failures) -> None:
    cadence = _optional_nonnegative_int(mode.get("sample_every_seconds"))
    positions, invalid_labels = _idle_sample_seconds(samples)
    duration = idle_seconds if idle_seconds is not None else minimum
    sampling_complete = bool(
        cadence is not None
        and 1 <= cadence <= _RUNTIME_HEALTH_SOAK_MAX_SAMPLE_SECONDS
        and not invalid_labels
        and duration >= minimum
        and _soak_sampling_complete(positions, duration=duration, cadence=cadence)
    )
    if sampling_complete:
        return
    _fail(
        failures,
        "runtime_health.soak.sampling",
        "runtime health soak samples were missing, sparse, out of order, or over the cadence budget",
        cadence_seconds=mode.get("sample_every_seconds"),
        cadence_budget_seconds=_RUNTIME_HEALTH_SOAK_MAX_SAMPLE_SECONDS,
        duration_seconds=duration,
        sample_count=len(positions),
        first_sample_seconds=positions[0] if positions else None,
        final_sample_seconds=positions[-1] if positions else None,
        invalid_labels=invalid_labels,
    )


def _check_runtime_health_soak_contract(report: dict, samples: list[dict], failures: list[dict]) -> None:
    mode = report.get("mode")
    if not isinstance(mode, dict):
        return
    mode_name = str(mode.get("mode") or "")
    minimum = _RUNTIME_HEALTH_SOAK_MINIMUM_SECONDS.get(mode_name)
    if minimum is None:
        return
    evidence = _runtime_health_soak_duration_evidence(report, mode)
    _check_runtime_health_soak_duration(mode_name, minimum, evidence, failures)
    _check_runtime_health_soak_sampling(mode, samples, minimum, evidence["idle"], failures)


def _check_runtime_health_initial_tab(report: dict, failures: list[dict], budget: dict) -> None:
    initial_tab_elapsed = _runtime_health_initial_tab_ready_ms(report)
    if initial_tab_elapsed is not None and initial_tab_elapsed > budget["runtime_health_initial_tab_ready_max_ms"]:
        _fail(
            failures,
            "runtime_health.startup.initial_tab_ready",
            "runtime health initial tab ready elapsed time exceeded budget",
            actual=initial_tab_elapsed,
            budget=budget["runtime_health_initial_tab_ready_max_ms"],
        )
    if not _runtime_health_is_production_probe(report):
        return
    if initial_tab_elapsed is None:
        _fail(
            failures,
            "runtime_health.startup.initial_tab_ready.diagnostics",
            "production runtime health initial tab ready timing was missing, negative, or non-finite",
            actual=_runtime_health_initial_tab_value(report, "initial_tab_ready_ms"),
        )
    if _runtime_health_initial_tab_value(report, "initial_tab_loaded") is not True:
        _fail(
            failures,
            "runtime_health.startup.initial_tab_loaded",
            "production runtime health initial tab was not loaded before startup completed",
            actual=_runtime_health_initial_tab_value(report, "initial_tab_loaded"),
        )
    initial_ready = _runtime_health_initial_tab_value(report, "initial_tab_ready")
    initial_status = _runtime_health_initial_tab_value(report, "initial_tab_status")
    if initial_ready is not True or initial_status != "ok":
        _fail(
            failures,
            "runtime_health.startup.initial_tab_status",
            "production runtime health initial tab did not reach its explicit ready state",
            actual=initial_ready,
            status=initial_status,
        )


def _runtime_health_background_preload_enabled(report: dict) -> bool:
    mode = report.get("mode")
    return isinstance(mode, dict) and mode.get("background_prewarm") is True


def _runtime_health_background_preload_report(report: dict, failures: list[dict]) -> dict | None:
    preload = report.get("background_preload")
    if isinstance(preload, dict):
        return preload
    _fail(
        failures,
        "runtime_health.background_preload.diagnostics",
        "enabled background preload is missing completion diagnostics",
    )
    return None


def _is_string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and bool(item.strip()) for item in value)


def _cancellation_receipt_schema_errors(receipts, *, field: str) -> list[str]:
    if not isinstance(receipts, list):
        return [field]
    errors: list[str] = []
    for index, receipt in enumerate(receipts):
        prefix = f"{field}[{index}]"
        if not isinstance(receipt, dict):
            errors.append(prefix)
            continue
        for name in ("accepted", "local_settled", "settled"):
            if not isinstance(receipt.get(name), bool):
                errors.append(f"{prefix}.{name}")
        for name in ("task_ids", "active_task_ids"):
            if not _is_string_list(receipt.get(name)):
                errors.append(f"{prefix}.{name}")
    return errors


_BACKGROUND_PRELOAD_STRING_LIST_FIELDS = (
    "expected_order",
    "planned_order",
    "start_order",
    "completion_order",
    "ready_keys",
    "loaded_keys",
    "remaining_keys",
    "timeouts",
    "cancellation_timeout_keys",
    "pending_priority_keys",
    "auto_refresh_task_ids_observed",
    "startup_task_ids_observed",
    "background_task_ids_observed",
    "preload_baseline_task_ids",
    "startup_network_task_ids_observed",
)
_BACKGROUND_PRELOAD_DICT_FIELDS = (
    "failures",
    "dependency_failures",
    "cancellation_timeouts",
    "cancel_receipt",
    "startup_network_task_categories",
)
_BACKGROUND_PRELOAD_STRING_FIELDS = ("active_key", "cancelling_key", "blocked_reason", "status")
_BACKGROUND_PRELOAD_NONNEGATIVE_INT_FIELDS = (
    "planned_count",
    "loaded_count",
    "active_step_count",
    "max_concurrent_steps",
    "cancellation_settlement_timeout_ms",
    "cancellation_blocked_poll_interval_ms",
)
_BACKGROUND_PRELOAD_BOOL_FIELDS = (
    "enabled",
    "started",
    "finished",
    "auto_refresh_task_diagnostics_available",
    "startup_network_task_diagnostics_available",
    "preload_task_window_observed",
    "startup_cache_bootstrap_required",
    "startup_cache_bootstrap_ready",
    "cancellation_blocked",
    "timer_active",
    "shutdown_cancellation_settled",
)


def _is_dict(value) -> bool:
    return isinstance(value, dict)


def _is_string(value) -> bool:
    return isinstance(value, str)


def _is_nonnegative_int(value) -> bool:
    return _optional_nonnegative_int(value) is not None


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _invalid_payload_fields(payload: dict, fields: tuple[str, ...], validator) -> list[str]:
    return [field for field in fields if not validator(payload.get(field))]


def _background_preload_schema_errors(preload: dict) -> list[str]:
    field_validators = (
        (_BACKGROUND_PRELOAD_STRING_LIST_FIELDS, _is_string_list),
        (_BACKGROUND_PRELOAD_DICT_FIELDS, _is_dict),
        (_BACKGROUND_PRELOAD_STRING_FIELDS, _is_string),
        (_BACKGROUND_PRELOAD_NONNEGATIVE_INT_FIELDS, _is_nonnegative_int),
        (_BACKGROUND_PRELOAD_BOOL_FIELDS, _is_bool),
    )
    errors: list[str] = []
    for fields, validator in field_validators:
        errors.extend(_invalid_payload_fields(preload, fields, validator))
    errors.extend(
        _cancellation_receipt_schema_errors(
            preload.get("shutdown_cancel_receipts"),
            field="shutdown_cancel_receipts",
        )
    )
    return errors


def _check_runtime_health_background_preload_schema(preload: dict, failures: list[dict]) -> bool:
    invalid_fields = _background_preload_schema_errors(preload)
    if invalid_fields:
        _fail(
            failures,
            "runtime_health.background_preload.diagnostics",
            "enabled background preload receipt is missing typed contract evidence",
            invalid_fields=sorted(set(invalid_fields)),
        )
        return False
    return True


def _check_runtime_health_background_preload_status(preload: dict, failures: list[dict]) -> None:
    if not all(
        (
            preload.get("status") == "ok",
            preload.get("enabled") is True,
            preload.get("started") is True,
            preload.get("finished") is True,
        )
    ):
        _fail(
            failures,
            "runtime_health.background_preload.status",
            "background preload did not satisfy its completion contract",
            actual=preload.get("status"),
            enabled=preload.get("enabled"),
            started=preload.get("started"),
            finished=preload.get("finished"),
        )


def _background_preload_orders(preload: dict) -> tuple[list, list, list, list]:
    return (
        preload["expected_order"],
        preload["planned_order"],
        preload["start_order"],
        preload["completion_order"],
    )


def _check_runtime_health_background_preload_order(preload: dict, failures: list[dict]) -> None:
    expected, planned, started, completed = _background_preload_orders(preload)
    if not (
        expected == list(RUNTIME_HEALTH_BACKGROUND_PRELOAD_ORDER)
        and preload.get("planned_count") == len(expected)
        and planned == expected
        and started == expected
        and completed == expected
    ):
        _fail(
            failures,
            "runtime_health.background_preload.order",
            "all tabs were not preloaded once in the declared dependency order",
            expected=expected,
            planned=planned,
            started=started,
            completed=completed,
        )


def _check_runtime_health_background_preload_loaded(preload: dict, failures: list[dict]) -> None:
    expected = preload["expected_order"]
    loaded = preload["loaded_keys"]
    loaded_count = _optional_nonnegative_int(preload.get("loaded_count"))
    if loaded_count != len(expected) or len(loaded) != len(expected) or set(loaded) != set(expected):
        _fail(
            failures,
            "runtime_health.background_preload.loaded",
            "background preload did not materialize every declared tab",
            loaded_count=preload.get("loaded_count"),
            loaded_keys=loaded,
        )


def _check_runtime_health_background_preload_ready(preload: dict, failures: list[dict]) -> None:
    expected = preload["expected_order"]
    ready = preload["ready_keys"]
    if ready != expected:
        _fail(
            failures,
            "runtime_health.background_preload.ready",
            "background preload did not prove every declared tab ready in dependency order",
            expected=expected,
            ready_keys=ready,
        )


def _check_runtime_health_background_preload_concurrency(preload: dict, failures: list[dict]) -> None:
    if preload.get("max_concurrent_steps") != 1 or preload.get("active_step_count") != 0:
        _fail(
            failures,
            "runtime_health.background_preload.concurrency",
            "background preload was not proven to be single-step serial",
            actual=preload.get("max_concurrent_steps"),
            active_step_count=preload.get("active_step_count"),
            budget=1,
        )


def _background_preload_clean_checks(preload: dict) -> dict[str, bool]:
    return {
        "failures": not preload["failures"],
        "dependency_failures": not preload["dependency_failures"],
        "timeouts": not preload["timeouts"],
        "cancellation_timeouts": (
            not preload["cancellation_timeouts"] and not preload["cancellation_timeout_keys"]
        ),
        "cancellation_blocked": preload["cancellation_blocked"] is False,
        "cancelling_key": not preload["cancelling_key"].strip(),
        "blocked_reason": not preload["blocked_reason"].strip(),
        "active_step_count": preload["active_step_count"] == 0,
        "timer_active": preload["timer_active"] is False,
        "remaining_keys": not preload["remaining_keys"],
        "active_key": not preload["active_key"].strip(),
        "pending_priority_keys": not preload["pending_priority_keys"],
        "shutdown_cancellation_settled": preload["shutdown_cancellation_settled"] is True,
        "auto_refresh_diagnostics": preload["auto_refresh_task_diagnostics_available"] is True,
        "auto_refresh_tasks": not preload["auto_refresh_task_ids_observed"],
        "task_window": preload["preload_task_window_observed"] is True,
        "startup_tasks": not preload["startup_task_ids_observed"],
        "startup_network_diagnostics": preload["startup_network_task_diagnostics_available"] is True,
        "startup_network_tasks": not preload["startup_network_task_ids_observed"],
        "bootstrap": (
            not preload["startup_cache_bootstrap_required"] or preload["startup_cache_bootstrap_ready"] is True
        ),
    }


def _check_runtime_health_background_preload_clean_completion(
    preload: dict,
    failures: list[dict],
) -> None:
    checks = _background_preload_clean_checks(preload)
    if all(checks.values()):
        return
    failures_by_key = preload["failures"]
    dependency_failures = preload["dependency_failures"]
    timeouts = preload["timeouts"]
    cancellation_timeouts = preload["cancellation_timeouts"]
    remaining = preload["remaining_keys"]
    active_key = preload["active_key"].strip()
    auto_refresh_task_ids = preload["auto_refresh_task_ids_observed"]
    auto_refresh_diagnostics = preload["auto_refresh_task_diagnostics_available"]
    startup_task_ids = preload["startup_task_ids_observed"]
    startup_network_task_ids = preload["startup_network_task_ids_observed"]
    task_window_observed = preload["preload_task_window_observed"]
    bootstrap_required = preload["startup_cache_bootstrap_required"]
    bootstrap_ready = preload["startup_cache_bootstrap_ready"]
    _fail(
        failures,
        "runtime_health.background_preload.clean_completion",
        "background preload ended with failures, timeouts, queued tabs, or an active step",
        invalid_fields=[field for field, valid in checks.items() if not valid],
        failure_details=failures_by_key,
        dependency_failures=dependency_failures,
        timeouts=timeouts,
        cancellation_timeouts=cancellation_timeouts,
        cancellation_blocked=preload["cancellation_blocked"],
        cancellation_timeout_keys=preload["cancellation_timeout_keys"],
        active_step_count=preload["active_step_count"],
        timer_active=preload["timer_active"],
        remaining=remaining,
        active_key=active_key,
        auto_refresh_task_diagnostics_available=auto_refresh_diagnostics,
        auto_refresh_task_ids_observed=auto_refresh_task_ids,
        preload_task_window_observed=task_window_observed,
        startup_task_ids_observed=startup_task_ids,
        startup_network_task_ids_observed=startup_network_task_ids,
        startup_network_task_categories=preload["startup_network_task_categories"],
        background_task_ids_observed=preload["background_task_ids_observed"],
        startup_cache_bootstrap_required=bootstrap_required,
        startup_cache_bootstrap_ready=bootstrap_ready,
    )


def _check_runtime_health_background_preload(report: dict, failures: list[dict]) -> None:
    if not _runtime_health_background_preload_enabled(report):
        return
    preload = _runtime_health_background_preload_report(report, failures)
    if preload is None:
        return
    if not _check_runtime_health_background_preload_schema(preload, failures):
        return
    _check_runtime_health_background_preload_status(preload, failures)
    _check_runtime_health_background_preload_order(preload, failures)
    _check_runtime_health_background_preload_ready(preload, failures)
    _check_runtime_health_background_preload_loaded(preload, failures)
    _check_runtime_health_background_preload_concurrency(preload, failures)
    if preload["startup_network_task_ids_observed"]:
        _fail(
            failures,
            "runtime_health.background_preload.global_network_task",
            "hidden staged preload started a STARTUP or NETWORK task",
            task_ids=preload["startup_network_task_ids_observed"],
            task_categories=preload["startup_network_task_categories"],
        )
    _check_runtime_health_background_preload_clean_completion(preload, failures)


def _background_preload_network_sample(samples: list[dict], failures: list[dict]) -> dict | None:
    matches = [
        sample
        for sample in samples
        if isinstance(sample, dict) and str(sample.get("label") or "") == "after_background_preload"
    ]
    if len(matches) != 1:
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload requires exactly one after_background_preload network sample",
            sample_count=len(matches),
        )
        return None
    return matches[0]


def _normalized_lineage_key_list(values: list) -> list[str]:
    return [str(key or "").strip() for key in values if str(key or "").strip()]


def _duplicate_lineage_keys(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for key in keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return sorted(duplicates)


def _background_preload_partition_is_exact(
    covered_raw: list,
    excluded_raw: list,
    covered: list[str],
    excluded: list[str],
    duplicate_covered: list[str],
    duplicate_excluded: list[str],
    overlap: list[str],
) -> bool:
    return all(
        (
            len(covered) == len(covered_raw),
            len(excluded) == len(excluded_raw),
            not duplicate_covered,
            not duplicate_excluded,
            not overlap,
            set(covered) == set(RUNTIME_HEALTH_BACKGROUND_PRELOAD_COVERED_TABS),
            set(excluded) == set(RUNTIME_HEALTH_BACKGROUND_PRELOAD_EXCLUDED_TABS),
        )
    )


def _fail_background_preload_partition(
    failures: list[dict],
    covered: list[str],
    excluded: list[str],
    duplicate_covered: list[str],
    duplicate_excluded: list[str],
    overlap: list[str],
) -> None:
    expected_covered = set(RUNTIME_HEALTH_BACKGROUND_PRELOAD_COVERED_TABS)
    expected_excluded = set(RUNTIME_HEALTH_BACKGROUND_PRELOAD_EXCLUDED_TABS)
    _fail(
        failures,
        "runtime_health.background_preload.network_evidence",
        "background preload lineage coverage must be the exact 10-data-tab plus system-log partition",
        missing_covered=sorted(expected_covered - set(covered)),
        unexpected_covered=sorted(set(covered) - expected_covered),
        missing_excluded=sorted(expected_excluded - set(excluded)),
        unexpected_excluded=sorted(set(excluded) - expected_excluded),
        duplicate_covered=duplicate_covered,
        duplicate_excluded=duplicate_excluded,
        overlap=overlap,
    )


def _background_preload_lineage_partition(
    sample: dict,
    failures: list[dict],
) -> tuple[list[str], list[str]] | None:
    coverage = sample.get("data_lineage_coverage")
    if not isinstance(coverage, dict):
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload network sample is missing lineage coverage partition",
        )
        return None
    covered_raw = coverage.get("covered")
    excluded_raw = coverage.get("excluded")
    if not isinstance(covered_raw, list) or not isinstance(excluded_raw, list):
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload lineage covered and excluded fields must be lists",
        )
        return None

    covered = _normalized_lineage_key_list(covered_raw)
    excluded = _normalized_lineage_key_list(excluded_raw)
    duplicate_covered = _duplicate_lineage_keys(covered)
    duplicate_excluded = _duplicate_lineage_keys(excluded)
    overlap = sorted(set(covered) & set(excluded))
    exact = _background_preload_partition_is_exact(
        covered_raw,
        excluded_raw,
        covered,
        excluded,
        duplicate_covered,
        duplicate_excluded,
        overlap,
    )
    if not exact:
        _fail_background_preload_partition(
            failures,
            covered,
            excluded,
            duplicate_covered,
            duplicate_excluded,
            overlap,
        )
        return None
    return covered, excluded


def _background_preload_lineage_row_keys(lineage: list) -> list[str]:
    return [
        str((item.get("key") or item.get("view") or "") if isinstance(item, dict) else "").strip()
        for item in lineage
    ]


def _background_preload_lineage_rows_are_exact(
    raw_keys: list[str],
    covered: list[str],
    duplicates: list[str],
) -> bool:
    return len(raw_keys) == len(covered) and all(raw_keys) and not duplicates and set(raw_keys) == set(covered)


def _background_preload_network_entries(sample: dict, failures: list[dict]) -> tuple[dict[str, dict], list[str]] | None:
    partition = _background_preload_lineage_partition(sample, failures)
    lineage = sample.get("data_lineage")
    if partition is None:
        return None
    if not isinstance(lineage, list):
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload network sample is missing data lineage rows",
        )
        return None

    covered, _excluded = partition
    raw_keys = _background_preload_lineage_row_keys(lineage)
    duplicates = _duplicate_lineage_keys([key for key in raw_keys if key])
    exact_rows = _background_preload_lineage_rows_are_exact(raw_keys, covered, duplicates)
    if not exact_rows:
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload network evidence must contain exactly one row per covered data tab",
            missing=sorted(set(covered) - set(raw_keys)),
            unexpected=sorted(set(raw_keys) - set(covered)),
            duplicates=duplicates,
        )
        return None
    return _lineage_by_key(lineage), covered


def _network_evidence_issues(
    entries: dict[str, dict],
    keys: list[str],
) -> tuple[list[str], dict[str, list[str]], list[str], list[str]]:
    missing: list[str] = []
    invalid: dict[str, list[str]] = {}
    unloaded: list[str] = []
    lineage_errors: list[str] = []
    for key in keys:
        entry = entries.get(key)
        if entry is None:
            missing.append(key)
            continue
        invalid_fields = [
            field
            for field in ("network_capable", "triggered_network")
            if type(entry.get(field)) is not bool
        ]
        if invalid_fields:
            invalid[key] = invalid_fields
        if entry.get("loaded") is not True:
            unloaded.append(key)
        if entry.get("lineage_error"):
            lineage_errors.append(key)
    return sorted(missing), invalid, sorted(unloaded), sorted(lineage_errors)


def _check_background_preload_network_entries(
    entries: dict[str, dict],
    keys: list[str],
    failures: list[dict],
) -> None:
    missing, invalid, unloaded, lineage_errors = _network_evidence_issues(entries, keys)
    if missing or invalid or unloaded or lineage_errors:
        _fail(
            failures,
            "runtime_health.background_preload.network_evidence",
            "background preload network evidence is incomplete or invalid",
            missing=missing,
            invalid=invalid,
            unloaded=unloaded,
            lineage_errors=lineage_errors,
        )
        return
    triggered = sorted(key for key in keys if entries[key]["triggered_network"] is not False)
    if triggered:
        _fail(
            failures,
            "runtime_health.background_preload.triggered_network",
            "hidden background preload triggered network activity",
            tabs=triggered,
        )


def _check_runtime_health_background_preload_network(
    report: dict,
    samples: list[dict],
    failures: list[dict],
) -> None:
    if not _runtime_health_background_preload_enabled(report):
        return
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    startup_task_settle = report.get("startup_task_settle")
    require_global_task_evidence = mode.get("startup_enabled") is True
    observed_task_ids = (
        startup_task_settle.get("observed_task_ids")
        if isinstance(startup_task_settle, dict)
        else None
    )
    if require_global_task_evidence and not isinstance(observed_task_ids, list):
        _fail(
            failures,
            "runtime_health.background_preload.global_network_evidence",
            "startup-enabled background preload is missing global startup task evidence",
            actual=observed_task_ids,
        )
    hidden_asian_tasks = (
        sorted(set(observed_task_ids) & RUNTIME_HEALTH_HIDDEN_ASIAN_NETWORK_TASK_IDS)
        if isinstance(observed_task_ids, list)
        else []
    )
    if hidden_asian_tasks:
        _fail(
            failures,
            "runtime_health.background_preload.global_network_task",
            "hidden startup/background preload window started the Asian remote sync task",
            task_ids=hidden_asian_tasks,
            observed_task_ids=observed_task_ids,
        )
    sample = _background_preload_network_sample(samples, failures)
    if sample is None:
        return
    evidence = _background_preload_network_entries(sample, failures)
    if evidence is None:
        return
    entries, keys = evidence
    _check_background_preload_network_entries(entries, keys, failures)


def _runtime_health_startup_timing_value(report: dict, event: str, *, application: bool = False):
    direct_prefix = "startup_app_init" if application else "startup_inclusive"
    direct_key = f"{direct_prefix}_{event}_ms"
    if direct_key in report:
        return report.get(direct_key)

    timing = report.get("startup_timing")
    section_key = "application_initialization_inclusive" if application else "script_module_inclusive"
    if isinstance(timing, dict):
        section = timing.get(section_key)
        if isinstance(section, dict) and f"{event}_ms" in section:
            return section.get(f"{event}_ms")

    startup = (report.get("startup_lazy_budget") or {}).get("startup") or {}
    lazy_key = f"app_init_{event}_ms" if application else f"inclusive_{event}_ms"
    return startup.get(lazy_key)


def _runtime_health_startup_timing_scope(report: dict) -> dict | None:
    timing = report.get("startup_timing")
    if isinstance(timing, dict) and isinstance(timing.get("scope"), dict):
        return timing["scope"]
    startup = (report.get("startup_lazy_budget") or {}).get("startup") or {}
    scope = startup.get("timing_scope")
    return scope if isinstance(scope, dict) else None


def _runtime_inclusion_declared(section) -> bool:
    return isinstance(section, dict) and all(
        section.get(key) is True
        for key in (
            "included_in_script_module_inclusive_startup_timing",
            "included_in_application_initialization_startup_timing",
        )
    )


def _check_runtime_health_search_filter_runtime(report: dict, failures: list[dict]) -> None:
    runtime = report.get("search_filter_runtime")
    initialization_ms = _optional_nonnegative_number(
        runtime.get("initialization_ms") if isinstance(runtime, dict) else None
    )
    valid = (
        isinstance(runtime, dict)
        and runtime.get("ready") is True
        and initialization_ms is not None
        and runtime.get("excluded_from_window_only_startup_timing") is True
        and _runtime_inclusion_declared(runtime)
    )
    if not valid:
        _fail(
            failures,
            "runtime_health.startup.search_filter_runtime",
            "production startup did not prove main-thread search-filter runtime initialization",
            ready=runtime.get("ready") if isinstance(runtime, dict) else None,
            initialization_ms=runtime.get("initialization_ms") if isinstance(runtime, dict) else None,
        )


def _check_runtime_health_startup_timing_scope(report: dict, failures: list[dict]) -> None:
    scope = _runtime_health_startup_timing_scope(report)
    expected = {
        "clock": "time.perf_counter",
        "script_module_origin": RUNTIME_HEALTH_STARTUP_MODULE_ORIGIN,
        "application_origin": RUNTIME_HEALTH_STARTUP_APP_ORIGIN,
        "includes_python_interpreter_startup": False,
        "includes_process_creation": False,
        "includes_qt_runtime_configuration": True,
        "includes_qapplication_initialization": True,
        "includes_native_dataframe_runtime_initialization": True,
        "includes_search_filter_runtime_initialization": True,
        "window_only_metrics_preserved": True,
    }
    invalid_fields = [key for key, value in expected.items() if not isinstance(scope, dict) or scope.get(key) != value]
    native_scope_valid = _runtime_inclusion_declared(report.get("native_dataframe_runtime"))
    search_scope_valid = _runtime_inclusion_declared(report.get("search_filter_runtime"))
    if invalid_fields or not native_scope_valid or not search_scope_valid:
        _fail(
            failures,
            "runtime_health.startup.inclusive_scope",
            "production startup timing scope was missing or could falsely imply process/interpreter-inclusive timing",
            invalid_fields=invalid_fields,
            native_runtime_inclusion_declared=native_scope_valid,
            search_filter_runtime_inclusion_declared=search_scope_valid,
        )


def _check_runtime_health_startup_metric_order(
    failures: list[dict],
    *,
    event: str,
    module_inclusive: float | None,
    app_inclusive: float | None,
    window_only: float | None,
) -> None:
    tolerance_ms = 0.01
    if (
        module_inclusive is not None and app_inclusive is not None and module_inclusive + tolerance_ms < app_inclusive
    ) or (app_inclusive is not None and window_only is not None and app_inclusive + tolerance_ms < window_only):
        _fail(
            failures,
            f"runtime_health.startup.{event}.scope_order",
            "startup timing scopes were not ordered module-inclusive >= app-inclusive >= window-only",
            module_inclusive_ms=module_inclusive,
            app_inclusive_ms=app_inclusive,
            window_only_ms=window_only,
        )


def _check_runtime_health_scoped_startup_value(
    failures: list[dict],
    *,
    metric_key: str,
    description: str,
    raw_value,
    required: bool,
    threshold: float | None = None,
) -> float | None:
    value = _optional_nonnegative_number(raw_value)
    if value is None:
        if required:
            _fail(
                failures,
                f"runtime_health.startup.{metric_key}.diagnostics",
                f"{description} was missing, negative, or non-finite",
                actual=raw_value,
            )
        return None
    if threshold is not None and value > threshold:
        _fail(
            failures,
            f"runtime_health.startup.{metric_key}",
            "inclusive startup timing exceeded budget",
            actual=value,
            budget=threshold,
        )
    return value


def _check_runtime_health_inclusive_startup(report: dict, failures: list[dict], budget: dict) -> None:
    production = _runtime_health_is_production_probe(report)
    module_values: dict[str, float | None] = {}
    app_values: dict[str, float | None] = {}
    thresholds = {
        "first_paint": budget["runtime_health_inclusive_first_paint_max_ms"],
        "initial_tab_ready": budget["runtime_health_inclusive_initial_tab_ready_max_ms"],
    }
    for event, threshold in thresholds.items():
        raw_module = _runtime_health_startup_timing_value(report, event)
        module_values[event] = _check_runtime_health_scoped_startup_value(
            failures,
            metric_key=f"inclusive_{event}",
            description="inclusive startup timing",
            raw_value=raw_module,
            required=production or raw_module is not None,
        )

        raw_app = _runtime_health_startup_timing_value(report, event, application=True)
        app_values[event] = _check_runtime_health_scoped_startup_value(
            failures,
            metric_key=f"app_init_{event}",
            description="application-initialization startup timing",
            raw_value=raw_app,
            required=production or raw_app is not None,
            threshold=threshold,
        )

    if production:
        _check_runtime_health_search_filter_runtime(report, failures)
        _check_runtime_health_startup_timing_scope(report, failures)

    window_values = {
        "first_paint": _optional_nonnegative_number(_runtime_health_startup_ready_ms(report)),
        "initial_tab_ready": _runtime_health_initial_tab_ready_ms(report),
    }
    for event in thresholds:
        _check_runtime_health_startup_metric_order(
            failures,
            event=event,
            module_inclusive=module_values[event],
            app_inclusive=app_values[event],
            window_only=window_values[event],
        )


def _startup_settle_list_errors(receipt: dict) -> list[str]:
    fields = (
        "task_ids",
        "active_before_ids",
        "observed_task_ids",
        "remaining_task_ids",
        "delayed_task_ids",
    )
    return [field for field in fields if not _is_string_list(receipt.get(field))]


def _startup_settle_active_after_is_zero(value) -> bool:
    return value is False or (isinstance(value, int) and not isinstance(value, bool) and value == 0)


def _startup_settle_scalar_errors(receipt: dict) -> list[str]:
    checks = {
        "status": receipt.get("status") == "ok",
        "task_id": isinstance(receipt.get("task_id"), str) and bool(receipt.get("task_id", "").strip()),
        "active_before": isinstance(receipt.get("active_before"), bool),
        "active_after": _startup_settle_active_after_is_zero(receipt.get("active_after")),
        "remaining_task_ids_not_empty": receipt.get("remaining_task_ids") == [],
        "contaminated": receipt.get("contaminated") is False,
        "delay_horizon_ms": _optional_nonnegative_int(receipt.get("delay_horizon_ms")) is not None,
        "timeout_ms": _optional_nonnegative_int(receipt.get("timeout_ms")) is not None,
    }
    return [field for field, valid in checks.items() if not valid]


def _startup_task_settle_invalid_fields(receipt: dict) -> list[str]:
    return _startup_settle_list_errors(receipt) + _startup_settle_scalar_errors(receipt)


def _check_runtime_health_startup_task_settle(report: dict, failures: list[dict]) -> None:
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    receipt = report.get("startup_task_settle")
    required = mode.get("startup_enabled") is True
    if receipt is None and not required:
        return
    if not isinstance(receipt, dict):
        _fail(
            failures,
            "runtime_health.startup.task_settle",
            "startup task-settle receipt is missing or invalid",
            actual=receipt,
        )
        return
    invalid_fields = _startup_task_settle_invalid_fields(receipt)
    if not invalid_fields:
        return
    task_ids = receipt.get("remaining_task_ids") or receipt.get("observed_task_ids") or []
    _fail(
        failures,
        "runtime_health.startup.task_settle",
        "startup-owned work did not provide a clean typed settle receipt",
        task_id=receipt.get("task_id"),
        task_ids=task_ids if isinstance(task_ids, list) else [],
        timeout_ms=receipt.get("timeout_ms"),
        invalid_fields=sorted(set(invalid_fields)),
        phase="startup_settle",
        sample="startup_task_settle",
    )


def _check_runtime_health_startup(report: dict, failures: list[dict], budget: dict) -> None:
    elapsed = _runtime_health_startup_ready_ms(report)
    if elapsed is not None and elapsed > budget["runtime_health_startup_ready_max_ms"]:
        _fail(
            failures,
            "runtime_health.startup.ready",
            "runtime health startup ready elapsed time exceeded budget",
            actual=elapsed,
            budget=budget["runtime_health_startup_ready_max_ms"],
        )
    _check_runtime_health_initial_tab(report, failures, budget)
    _check_runtime_health_inclusive_startup(report, failures, budget)
    _check_runtime_health_startup_task_settle(report, failures)


def _first_open_tab_items(tab_items: list) -> dict[str, dict]:
    first_open_by_key: dict[str, dict] = {}
    for item in tab_items:
        if not isinstance(item, dict) or item.get("loaded_before") is True:
            continue
        key = str(item.get("key") or "").strip()
        if key and key not in first_open_by_key:
            first_open_by_key[key] = item
    return first_open_by_key


def _tab_interaction_elapsed(item: dict) -> tuple[float | None, str]:
    stable = _optional_float(item.get("interaction_to_stable_ms"))
    if stable is not None:
        return stable, "interaction_to_stable_ms"
    return _optional_float(item.get("elapsed_ms")), "elapsed_ms"


def _tab_cycle_bad_statuses(tab_items: list) -> list[dict]:
    return [
        {
            "key": str(item.get("key") or ""),
            "status": str(item.get("status") or ""),
            "cycle": item.get("cycle"),
        }
        for item in tab_items
        if isinstance(item, dict) and str(item.get("status") or "") not in RUNTIME_HEALTH_ACCEPTED_TAB_STATUSES
    ]


def _check_runtime_health_tab_entries(requested_tabs: list[str], tab_items: list, failures: list[dict]) -> None:
    seen_keys = {str(item.get("key") or "").strip() for item in tab_items if isinstance(item, dict)}
    missing = sorted(set(requested_tabs) - seen_keys)
    if missing:
        _fail(
            failures,
            "runtime_health.tab_cycle.requested_tabs",
            "runtime health tab cycle did not visit all requested tabs",
            missing=missing,
        )

    bad_statuses = _tab_cycle_bad_statuses(tab_items)
    if bad_statuses:
        _fail(
            failures,
            "runtime_health.tab_cycle.status",
            "runtime health tab cycle had missing or timed-out tabs",
            tabs=bad_statuses,
        )


def _tab_elapsed_failures(first_open_by_key, *, budget_ms: float, keys, over_reason: str) -> list[dict]:
    selected_keys = list(first_open_by_key) if keys is None else sorted(keys)
    results = []
    for key in selected_keys:
        item = first_open_by_key.get(key)
        if item is None:
            continue
        elapsed, metric = _tab_interaction_elapsed(item)
        if elapsed is None:
            results.append({"key": key, "elapsed_ms": item.get(metric), "metric": metric, "reason": "missing"})
            continue
        if elapsed > budget_ms:
            results.append(
                {
                    "key": key,
                    "elapsed_ms": elapsed,
                    "metric": metric,
                    "budget": budget_ms,
                    "reason": over_reason,
                }
            )
    return results


def _record_tab_elapsed_failures(report, failures, *, check, detail, items) -> None:
    if items:
        post_tab_idle = report.get("post_tab_idle")
        task_ids = post_tab_idle.get("started_task_ids") if isinstance(post_tab_idle, dict) else []
        _fail(
            failures,
            check,
            detail,
            tabs=items,
            phase="tab_cycle",
            sample="tab_cycle.tabs",
            task_ids=task_ids or [],
        )


def _check_runtime_health_tab_cycle(report: dict, failures: list[dict], budget: dict) -> None:
    requested_tabs = _requested_runtime_health_tabs(report)
    tab_cycle = report.get("tab_cycle")
    if not requested_tabs and tab_cycle is None:
        return
    if not isinstance(tab_cycle, dict):
        _fail(failures, "runtime_health.tab_cycle.present", "runtime health suite tab cycle report is missing")
        return
    tab_items = tab_cycle.get("tabs") or []
    if not isinstance(tab_items, list):
        _fail(failures, "runtime_health.tab_cycle.type", "runtime health tab cycle entries must be a list")
        return
    _check_runtime_health_tab_entries(requested_tabs, tab_items, failures)
    first_open_by_key = _first_open_tab_items(tab_items)
    general = _tab_elapsed_failures(
        first_open_by_key,
        budget_ms=budget["runtime_health_tab_first_open_max_ms"],
        keys=None,
        over_reason="over_budget",
    )
    _record_tab_elapsed_failures(
        report,
        failures,
        check="runtime_health.tab_first_open.elapsed",
        detail="runtime health tab first-open elapsed time exceeded budget or was not recorded",
        items=general,
    )
    key_tabs = _tab_elapsed_failures(
        first_open_by_key,
        budget_ms=budget["runtime_health_key_tab_first_open_max_ms"],
        keys=RUNTIME_HEALTH_KEY_TAB_FIRST_OPEN_BUDGET_KEYS,
        over_reason="over_key_tab_budget",
    )
    _record_tab_elapsed_failures(
        report,
        failures,
        check="runtime_health.tab_first_open.key_elapsed",
        detail="runtime health key tab first-open elapsed time exceeded budget or was not recorded",
        items=key_tabs,
    )


def _production_tab_expected_entries(requested: int | None) -> list[tuple[int, str]]:
    return [
        (cycle, key)
        for cycle in range(1, (requested or 0) + 1)
        for key in RUNTIME_HEALTH_PROBE_ORDER
    ]


def _production_tab_item_valid(item, expected: tuple[int, str]) -> bool:
    return bool(
        isinstance(item, dict)
        and _optional_nonnegative_int(item.get("cycle")) == expected[0]
        and item.get("key") == expected[1]
        and item.get("status") == "ok"
        and _optional_nonnegative_number(item.get("elapsed_ms")) is not None
    )


def _production_tab_cycle_items(tabs, expected_entries) -> tuple[list[tuple], list[int]]:
    actual_entries = []
    invalid_items = []
    if isinstance(tabs, list):
        for position, item in enumerate(tabs):
            if not isinstance(item, dict):
                invalid_items.append(position)
                continue
            actual_entries.append((item.get("cycle"), item.get("key")))
            expected = expected_entries[position] if position < len(expected_entries) else (-1, "")
            if not _production_tab_item_valid(item, expected):
                invalid_items.append(position)
    return actual_entries, invalid_items


def _production_tab_cycle_contract_valid(result, requested, tabs, expected_entries, invalid_items) -> bool:
    return bool(
        requested
        and isinstance(result, dict)
        and result.get("status") == "ok"
        and _optional_nonnegative_int(result.get("cycles")) == requested
        and _optional_nonnegative_int(result.get("visited")) == len(expected_entries)
        and isinstance(tabs, list)
        and len(tabs) == len(expected_entries)
        and not invalid_items
    )


def _check_runtime_health_production_tab_cycle(report: dict, failures: list[dict]) -> None:
    if not _runtime_health_is_production_full(report):
        return
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    requested = _optional_nonnegative_int(mode.get("tab_cycles"))
    result = report.get("tab_cycle")
    tabs = result.get("tabs") if isinstance(result, dict) else None
    expected_entries = _production_tab_expected_entries(requested)
    actual_entries, invalid_items = _production_tab_cycle_items(tabs, expected_entries)
    if _production_tab_cycle_contract_valid(result, requested, tabs, expected_entries, invalid_items):
        return
    _fail(
        failures,
        "runtime_health.tab_cycle.production_contract",
        "production tab cycle must visit the exact registry matrix in cycle order",
        requested_cycles=requested,
        expected_count=len(expected_entries),
        actual_count=len(tabs) if isinstance(tabs, list) else None,
        visited=result.get("visited") if isinstance(result, dict) else None,
        actual_entries=actual_entries,
        invalid_items=invalid_items,
    )


def _runtime_health_stall_samples(samples: list[dict]) -> list[dict]:
    budget_samples = [
        sample
        for sample in samples
        if isinstance(sample, dict) and str(sample.get("label") or "").strip().lower() != "startup"
    ]
    return budget_samples or [sample for sample in samples if isinstance(sample, dict)]


def _stall_peak(samples: list[dict], field: str) -> tuple[float, dict]:
    candidates = [
        sample
        for sample in samples
        if isinstance(sample.get("ui_stalls") or {}, dict) and (sample.get("ui_stalls") or {}).get("installed") is True
    ]
    if not candidates:
        return 0.0, {}
    sample = max(candidates, key=lambda item: _as_float((item.get("ui_stalls") or {}).get(field)))
    return _as_float((sample.get("ui_stalls") or {}).get(field)), sample


def _stall_failure_context(sample: dict) -> dict:
    background_tasks = sample.get("background_tasks")
    task_ids = background_tasks.get("ids") if isinstance(background_tasks, dict) else []
    return {
        "phase": sample.get("measurement_phase") or sample.get("label") or "unknown",
        "sample": sample.get("label") or "runtime_health",
        "task_ids": list(task_ids) if isinstance(task_ids, list) else [],
    }


def _check_runtime_health_ui_stalls(samples: list[dict], failures: list[dict], budget: dict) -> None:
    samples = _runtime_health_stall_samples(samples)
    checks = (
        (
            "critical_count",
            "runtime_health_ui_critical_stall_max",
            "runtime_health.ui_stall.critical_count",
            "critical UI stall count exceeded budget",
        ),
        (
            "event_loop_critical_count",
            "runtime_health_ui_event_loop_critical_stall_max",
            "runtime_health.ui_stall.event_loop_critical_count",
            "critical event-loop stall count exceeded budget",
        ),
        (
            "max_elapsed_ms",
            "runtime_health_ui_max_stall_ms",
            "runtime_health.ui_stall.max_elapsed",
            "maximum UI stall elapsed time exceeded budget",
        ),
    )
    for field, budget_key, check, detail in checks:
        actual, sample = _stall_peak(samples, field)
        if sample and actual > budget[budget_key]:
            _fail(
                failures,
                check,
                detail,
                actual=actual,
                budget=budget[budget_key],
                **_stall_failure_context(sample),
            )


_POST_CLOSE_COUNT_DIAGNOSTICS = (
    (
        "task_manager_active_count",
        "task_manager_diagnostics_available",
        "runtime_health.shutdown.background_tasks",
        "background tasks remained after the main window closed",
    ),
    (
        "qthread_pool_active_count",
        "qthread_pool_diagnostics_available",
        "runtime_health.shutdown.qthread_pool",
        "QThreadPool workers remained active after the main window closed",
    ),
    (
        "pending_qthread_count",
        "pending_qthread_diagnostics_available",
        "runtime_health.shutdown.pending_qthreads",
        "owned QThreads remained pending after the main window closed",
    ),
)

_KLINE_MANAGER_CLEAN_FIELDS = (
    "clean",
    "active_close_clean",
    "pooled_dispose_clean",
    "prewarm_dispose_clean",
    "return_timer_clean",
    "idle_guard_clean",
    "preflight_clean",
)

_KLINE_MANAGER_ZERO_REF_FIELDS = (
    "active_windows",
    "managed_keepers",
)

_KLINE_MANAGER_FALSE_REF_FIELDS = (
    "pending_open",
    "prewarm_main_window_retained",
)

_F5_RUNTIME_EMPTY_LIST_FIELDS = (
    ("unexpected_generation_ids", "unexpected_generations"),
    ("incomplete_generation_ids", "incomplete_generations"),
    ("invalid_generation_entries", "invalid_generation_entries"),
    ("unfinished_job_ids", "unfinished_jobs"),
    ("ready_to_activate_job_ids", "ready_to_activate"),
    ("invalid_job_ids", "invalid_jobs"),
    ("invalid_job_entries", "invalid_job_entries"),
    ("temporary_files", "temporary_files"),
)


def _check_zero_post_close_fields(post_close: dict, failures: list[dict]) -> None:
    for field, availability_field, check, detail in _POST_CLOSE_COUNT_DIAGNOSTICS:
        if post_close.get(availability_field) is not True:
            _fail(
                failures,
                f"{check}.diagnostics",
                f"{field} diagnostics were missing, invalid, or unavailable",
                actual=post_close.get(availability_field),
            )
        value = _optional_nonnegative_int(post_close.get(field))
        budget = 0
        if value is None or value > budget:
            _fail(failures, check, detail, actual=post_close.get(field), budget=budget)


def _check_post_close_bool_diagnostic(
    post_close: dict,
    failures: list[dict],
    *,
    field: str,
    availability_field: str,
    check: str,
    detail: str,
) -> None:
    if post_close.get(availability_field) is not True:
        _fail(
            failures,
            f"{check}.diagnostics",
            f"{field} diagnostics were missing, invalid, or unavailable",
            actual=post_close.get(availability_field),
        )
    if post_close.get(field) is not False:
        _fail(failures, check, detail, actual=post_close.get(field))


def _check_kline_manager_clean_fields(diagnostics: dict, failures: list[dict]) -> None:
    for field in _KLINE_MANAGER_CLEAN_FIELDS:
        if diagnostics.get(field) is not True:
            _fail(
                failures,
                f"runtime_health.shutdown.kline_manager.{field}",
                f"K-line manager shutdown receipt {field} did not pass",
                actual=diagnostics.get(field),
            )


def _check_kline_manager_zero_ref_fields(diagnostics: dict, failures: list[dict]) -> None:
    for field in _KLINE_MANAGER_ZERO_REF_FIELDS:
        value = _optional_nonnegative_int(diagnostics.get(field))
        if value != 0:
            _fail(
                failures,
                f"runtime_health.shutdown.kline_manager.{field}",
                f"K-line manager retained {field} after shutdown",
                actual=diagnostics.get(field),
                budget=0,
            )


def _check_kline_manager_false_ref_fields(diagnostics: dict, failures: list[dict]) -> None:
    for field in _KLINE_MANAGER_FALSE_REF_FIELDS:
        if diagnostics.get(field) is not False:
            _fail(
                failures,
                f"runtime_health.shutdown.kline_manager.{field}",
                f"K-line manager retained {field} after shutdown",
                actual=diagnostics.get(field),
            )


def _check_kline_manager_shutdown_diagnostics(post_close: dict, failures: list[dict]) -> None:
    diagnostics = post_close.get("kline_manager_shutdown_diagnostics")
    available = post_close.get("kline_manager_shutdown_diagnostics_available")
    if available is not True or not isinstance(diagnostics, dict) or not diagnostics:
        _fail(
            failures,
            "runtime_health.shutdown.kline_manager.diagnostics",
            "K-line manager shutdown diagnostics were missing, invalid, or unavailable",
            actual=available,
        )
    if not isinstance(diagnostics, dict) or not diagnostics:
        return

    _check_kline_manager_clean_fields(diagnostics, failures)
    _check_kline_manager_zero_ref_fields(diagnostics, failures)
    _check_kline_manager_false_ref_fields(diagnostics, failures)


_F5_ARTIFACT_CHECK_PREFIX = "runtime_health.shutdown.f5_runtime_artifacts"


def _check_f5_snapshot_and_generations(receipt: dict, failures: list[dict]) -> bool:
    valid = True
    if receipt.get("active_snapshot_complete") is not True:
        valid = False
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.active_snapshot",
            "active F5 snapshot artifacts were missing, empty, or outside their generation",
            actual=receipt.get("active_snapshot_complete"),
        )
    generation_count = _optional_nonnegative_int(receipt.get("generation_count"))
    generation_limit = _optional_nonnegative_int(receipt.get("generation_limit"))
    if generation_count is None or generation_limit != 2 or generation_count > 2:
        valid = False
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.generations",
            "F5 generations must be bounded to active plus previous",
            actual=generation_count,
            declared_limit=generation_limit,
            budget=2,
        )
    return valid


def _check_f5_empty_artifact_lists(receipt: dict, failures: list[dict]) -> bool:
    valid = True
    for field, suffix in _F5_RUNTIME_EMPTY_LIST_FIELDS:
        value = receipt.get(field)
        if not isinstance(value, list) or value:
            valid = False
            _fail(
                failures,
                f"{_F5_ARTIFACT_CHECK_PREFIX}.{suffix}",
                f"F5 runtime cleanup receipt {field} must be an empty list",
                actual=value,
                budget=0,
            )
    return valid


def _check_f5_temporary_file_count(receipt: dict, failures: list[dict]) -> bool:
    temporary_file_count = _optional_nonnegative_int(receipt.get("temporary_file_count"))
    if temporary_file_count == 0:
        return True
    already_failed = any(
        item.get("check") == f"{_F5_ARTIFACT_CHECK_PREFIX}.temporary_files" for item in failures
    )
    if not already_failed:
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.temporary_files",
            "F5 runtime retained temporary files after shutdown",
            actual=receipt.get("temporary_file_count"),
            budget=0,
        )
    return False


def _check_f5_terminal_jobs(receipt: dict, failures: list[dict]) -> tuple[bool, list | None]:
    terminal_job_ids = receipt.get("terminal_job_ids")
    job_count = _optional_nonnegative_int(receipt.get("job_count"))
    terminal_job_count = _optional_nonnegative_int(receipt.get("terminal_job_count"))
    terminal_jobs_valid = bool(
        isinstance(terminal_job_ids, list)
        and len(terminal_job_ids) <= 1
        and job_count is not None
        and job_count <= 1
        and terminal_job_count == len(terminal_job_ids)
    )
    if not terminal_jobs_valid:
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.terminal_jobs",
            "F5 runtime retained more than the current terminal job",
            job_count=receipt.get("job_count"),
            terminal_job_count=receipt.get("terminal_job_count"),
            terminal_job_ids=terminal_job_ids,
            budget=1,
        )
    return terminal_jobs_valid, terminal_job_ids if isinstance(terminal_job_ids, list) else None


def _check_f5_artifact_identity(receipt, terminal_ids, expected_id, failures) -> bool:
    if expected_id is None:
        return True
    active_id = str(receipt.get("active_snapshot_id") or "")
    active_valid = bool(expected_id) and active_id == expected_id
    terminal_expected = [expected_id] if expected_id else []
    terminal_valid = terminal_ids == terminal_expected
    if not active_valid:
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.active_identity",
            "post-close active F5 snapshot does not match the final successful real-process run",
            expected=expected_id,
            actual=active_id,
        )
    if not terminal_valid:
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.terminal_identity",
            "post-close terminal F5 job does not match the final successful real-process run",
            expected=terminal_expected,
            actual=terminal_ids,
        )
    return active_valid and terminal_valid


def _check_f5_runtime_artifact_diagnostics(post_close, failures, *, expected_active_snapshot_id=None) -> None:
    receipt = post_close.get("f5_runtime_artifacts")
    available = post_close.get("f5_runtime_artifacts_diagnostics_available")
    if available is not True or not isinstance(receipt, dict) or not receipt:
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.diagnostics",
            "F5 runtime artifact diagnostics were missing, invalid, or unavailable",
            actual=available,
            error=post_close.get("f5_runtime_artifacts_diagnostics_error"),
        )
        return
    terminal_valid, terminal_ids = _check_f5_terminal_jobs(receipt, failures)
    checks = (
        _check_f5_snapshot_and_generations(receipt, failures),
        _check_f5_empty_artifact_lists(receipt, failures),
        _check_f5_temporary_file_count(receipt, failures),
        terminal_valid,
        _check_f5_artifact_identity(receipt, terminal_ids, expected_active_snapshot_id, failures),
    )
    if receipt.get("clean") is not True or not all(checks):
        _fail(
            failures,
            f"{_F5_ARTIFACT_CHECK_PREFIX}.clean",
            "F5 runtime artifact cleanup receipt did not pass",
            actual=receipt.get("clean"),
        )


def _runtime_health_requested_kline_cycles(report: dict) -> int | None:
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    return _optional_nonnegative_int(mode.get("kline_cycles"))


def _runtime_health_shutdown_payload(
    report: dict, failures: list[dict], *, required: bool, requested: int | None
) -> dict | None:
    if "shutdown" not in report:
        if required:
            _fail(
                failures,
                "runtime_health.shutdown.present",
                "runtime health shutdown report is required for the stability suite or requested K-line cycles",
                requested=requested,
            )
        return None

    shutdown = report.get("shutdown")
    if not isinstance(shutdown, dict):
        _fail(failures, "runtime_health.shutdown.type", "runtime health shutdown report must be an object")
        return None
    return shutdown


def _check_runtime_health_shutdown_close(shutdown: dict, failures: list[dict], budget: dict) -> None:
    close_budget = budget["runtime_health_shutdown_close_max_ms"]
    close_elapsed_ms = _optional_float(shutdown.get("close_elapsed_ms"))
    invalid = close_elapsed_ms is None or close_elapsed_ms < 0 or close_elapsed_ms > close_budget
    if invalid:
        _fail(
            failures,
            "runtime_health.shutdown.close_elapsed",
            "runtime health shutdown close elapsed time is missing, invalid, or over budget",
            actual=shutdown.get("close_elapsed_ms"),
            budget=close_budget,
        )


def _check_post_close_webengine(post_close: dict, failures: list[dict]) -> None:
    webengine_child_count = _optional_nonnegative_int(post_close.get("webengine_child_count"))
    if webengine_child_count != 0:
        _fail(
            failures,
            "runtime_health.shutdown.webengine",
            "QtWebEngine child process count after shutdown was missing, invalid, or non-zero",
            actual=post_close.get("webengine_child_count"),
            budget=0,
        )
    if post_close.get("webengine_available") is not True:
        _fail(
            failures,
            "runtime_health.shutdown.webengine_available",
            "QtWebEngine process diagnostics were unavailable after shutdown",
            actual=post_close.get("webengine_available"),
        )


def _check_post_close_runtime_controllers(post_close: dict, failures: list[dict]) -> None:
    _check_post_close_bool_diagnostic(
        post_close,
        failures,
        field="watchdog_running",
        availability_field="watchdog_diagnostics_available",
        check="runtime_health.shutdown.watchdog",
        detail="process watchdog remained active after the main window closed",
    )
    if not isinstance(post_close.get("f5_controller_present"), bool):
        _fail(
            failures,
            "runtime_health.shutdown.f5_controller_present",
            "F5 controller presence receipt was missing or invalid",
            actual=post_close.get("f5_controller_present"),
        )
    _check_post_close_bool_diagnostic(
        post_close,
        failures,
        field="f5_controller_running",
        availability_field="f5_controller_diagnostics_available",
        check="runtime_health.shutdown.f5_controller",
        detail="F5 controller, owned worker process, or activation remained active after shutdown",
    )


def _workspace_preload_shutdown_schema_errors(receipt) -> list[str]:
    if not isinstance(receipt, dict):
        return ["workspace_background_preload"]
    errors: list[str] = []
    if not isinstance(receipt.get("active_key"), str):
        errors.append("active_key")
    if not isinstance(receipt.get("cancelling_key"), str):
        errors.append("cancelling_key")
    if not _is_string_list(receipt.get("remaining_keys")):
        errors.append("remaining_keys")
    if _optional_nonnegative_int(receipt.get("active_step_count")) is None:
        errors.append("active_step_count")
    for field in ("timer_active", "cancellation_blocked", "shutdown_cancellation_settled"):
        if not isinstance(receipt.get(field), bool):
            errors.append(field)
    errors.extend(
        _cancellation_receipt_schema_errors(
            receipt.get("shutdown_cancel_receipts"),
            field="shutdown_cancel_receipts",
        )
    )
    return errors


def _workspace_preload_shutdown_cleanup_errors(receipt: dict) -> list[str]:
    checks = {
        "active_key": not receipt["active_key"].strip(),
        "cancelling_key": not receipt["cancelling_key"].strip(),
        "remaining_keys": not receipt["remaining_keys"],
        "active_step_count": receipt["active_step_count"] == 0,
        "timer_active": receipt["timer_active"] is False,
        "cancellation_blocked": receipt["cancellation_blocked"] is False,
    }
    return [field for field, valid in checks.items() if not valid]


def _workspace_preload_cancel_receipt_settled(receipt: dict) -> bool:
    return all(
        (
            receipt["accepted"] is True,
            receipt["settled"] is True,
            receipt["local_settled"] is True,
            not receipt["active_task_ids"],
        )
    )


def _workspace_preload_shutdown_receipts_settled(receipt: dict) -> bool:
    return all(
        (
            receipt["shutdown_cancellation_settled"] is True,
            all(
                _workspace_preload_cancel_receipt_settled(item)
                for item in receipt["shutdown_cancel_receipts"]
            ),
        )
    )


def _check_workspace_background_preload_shutdown(post_close: dict, failures: list[dict]) -> None:
    available = post_close.get("workspace_background_preload_diagnostics_available")
    receipt = post_close.get("workspace_background_preload")
    invalid_fields = _workspace_preload_shutdown_schema_errors(receipt)
    if available is not True or invalid_fields:
        _fail(
            failures,
            "runtime_health.shutdown.background_preload.diagnostics",
            "workspace background-preload shutdown diagnostics were missing, invalid, or unavailable",
            actual=available,
            invalid_fields=invalid_fields,
        )
        return

    cleanup_errors = _workspace_preload_shutdown_cleanup_errors(receipt)
    if cleanup_errors:
        _fail(
            failures,
            "runtime_health.shutdown.background_preload.cleanup",
            "workspace background-preload owner retained active state, queue entries, or its timer",
            invalid_fields=cleanup_errors,
            receipt=receipt,
        )

    if not _workspace_preload_shutdown_receipts_settled(receipt):
        _fail(
            failures,
            "runtime_health.shutdown.background_preload.receipts",
            "workspace background-preload cancellation receipts were not accepted and physically settled",
            shutdown_cancellation_settled=receipt["shutdown_cancellation_settled"],
            receipts=receipt["shutdown_cancel_receipts"],
        )


def _check_runtime_health_post_close(
    post_close: dict,
    failures: list[dict],
    *,
    expected_active_snapshot_id: str | None = None,
) -> None:
    _check_zero_post_close_fields(post_close, failures)
    _check_post_close_webengine(post_close, failures)
    _check_post_close_runtime_controllers(post_close, failures)
    _check_workspace_background_preload_shutdown(post_close, failures)
    _check_f5_runtime_artifact_diagnostics(
        post_close,
        failures,
        expected_active_snapshot_id=expected_active_snapshot_id,
    )
    _check_kline_manager_shutdown_diagnostics(post_close, failures)


def _final_real_f5_snapshot_id(report: dict, requested: int) -> str:
    result = report.get("f5_cycle") if isinstance(report.get("f5_cycle"), dict) else {}
    timings = result.get("cycle_timings")
    if not isinstance(timings, list) or len(timings) != requested or not timings:
        return ""
    final_timing = timings[-1]
    if not isinstance(final_timing, dict):
        return ""
    run_id = str(final_timing.get("run_id") or "")
    snapshot_id = str(final_timing.get("snapshot_id") or "")
    return run_id if run_id and snapshot_id == run_id else ""


def _runtime_health_expected_active_f5_snapshot_id(report: dict) -> str | None:
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    requested = _optional_nonnegative_int(mode.get("f5_cycles"))
    if mode.get("f5_probe_mode") != "real_process" or not requested:
        return None
    return _final_real_f5_snapshot_id(report, requested)


def _check_runtime_health_shutdown(report: dict, failures: list[dict], budget: dict) -> None:
    requested = _runtime_health_requested_kline_cycles(report)
    suite_report = report.get("report_type") == "runtime_health_stability_suite"
    shutdown = _runtime_health_shutdown_payload(
        report,
        failures,
        required=suite_report or (requested is not None and requested > 0),
        requested=requested,
    )
    if shutdown is None:
        return

    if _runtime_health_is_production_full(report) and shutdown.get("pending_qthread_settle_ok") is not True:
        _fail(
            failures,
            "runtime_health.shutdown.pending_qthread_settle",
            "production shutdown did not settle all owned QThreads before post-close sampling",
            actual=shutdown.get("pending_qthread_settle_ok"),
        )

    _check_runtime_health_shutdown_close(shutdown, failures, budget)
    post_close = shutdown.get("post_close")
    if not isinstance(post_close, dict):
        _fail(
            failures,
            "runtime_health.shutdown.post_close",
            "runtime health post-close state is missing",
        )
        return
    _check_runtime_health_post_close(
        post_close,
        failures,
        expected_active_snapshot_id=_runtime_health_expected_active_f5_snapshot_id(report),
    )


def _runtime_health_kline_cycle_actual(result: dict) -> dict[str, int | None]:
    return {
        "cycles": _optional_nonnegative_int(result.get("cycles")),
        "opened": _optional_nonnegative_int(result.get("opened")),
        "closed": _optional_nonnegative_int(result.get("closed")),
        "blocked": _optional_nonnegative_int(result.get("blocked")),
    }


def _runtime_health_kline_cycle_complete(result: dict, requested: int, actual: dict[str, int | None]) -> bool:
    expected = {"cycles": requested, "opened": requested, "closed": requested, "blocked": 0}
    return str(result.get("status") or "") == "ok" and actual == expected


def _runtime_health_kline_contract(report: dict) -> tuple[dict, object, bool, int | None]:
    mode = report.get("mode")
    result = report.get("kline_cycle")
    contract_present = (isinstance(mode, dict) and "kline_cycles" in mode) or "kline_cycle" in report
    requested = _optional_nonnegative_int((mode or {}).get("kline_cycles")) if isinstance(mode, dict) else None
    return mode if isinstance(mode, dict) else {}, result, contract_present, requested


def _check_zero_requested_kline_cycles(result, failures: list[dict]) -> None:
    actual_cycles = _optional_nonnegative_int(result.get("cycles")) if isinstance(result, dict) else 0
    if actual_cycles != 0:
        _fail(
            failures,
            "runtime_health.kline_cycle.mode_consistency",
            "K-line result cycles did not match mode.kline_cycles",
            requested=0,
            actual=actual_cycles,
        )


def _check_requested_kline_cycles(result, requested: int, failures: list[dict]) -> None:
    if not isinstance(result, dict):
        _fail(
            failures,
            "runtime_health.kline_cycle.present",
            "runtime health K-line cycle report is missing",
            requested=requested,
        )
        return
    actual = _runtime_health_kline_cycle_actual(result)
    if not _runtime_health_kline_cycle_complete(result, requested, actual):
        _fail(
            failures,
            "runtime_health.kline_cycle.completion",
            "runtime health K-line cycles did not all open and close cleanly",
            requested=requested,
            **actual,
        )


def _check_runtime_health_kline_cycle(report: dict, failures: list[dict]) -> None:
    mode, result, contract_present, requested = _runtime_health_kline_contract(report)
    if not contract_present:
        return
    if requested is None:
        _fail(
            failures,
            "runtime_health.kline_cycle.requested",
            "mode.kline_cycles must be a present non-negative integer",
            actual=(mode or {}).get("kline_cycles") if isinstance(mode, dict) else None,
        )
        return
    if requested == 0:
        _check_zero_requested_kline_cycles(result, failures)
        return
    _check_requested_kline_cycles(result, requested, failures)


def _runtime_health_cycle_phase_contract(report: dict, result_key: str, mode_key: str):
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    mode_declared = mode_key in mode
    requested = _optional_nonnegative_int(mode.get(mode_key)) if mode_declared else None
    return mode, mode_declared, requested, report.get(result_key)


def _check_runtime_health_cycle_phase_result(
    failures: list[dict], result: dict, result_key: str, requested: int | None
) -> None:
    if result.get("status") != "ok":
        _fail(
            failures,
            f"runtime_health.{result_key}.status",
            f"runtime health {result_key} did not complete cleanly",
            actual=result.get("status"),
        )
    if requested is None:
        return
    actual_cycles = _optional_nonnegative_int(result.get("cycles"))
    if actual_cycles != requested:
        _fail(
            failures,
            f"runtime_health.{result_key}.completion",
            f"runtime health {result_key} cycle count did not match the requested count",
            requested=requested,
            actual=actual_cycles,
        )


def _check_runtime_health_cycle_phase(report: dict, failures: list[dict], *, result_key: str, mode_key: str) -> None:
    mode, mode_declared, requested, result = _runtime_health_cycle_phase_contract(report, result_key, mode_key)

    if mode_declared and requested is None:
        _fail(
            failures,
            f"runtime_health.{result_key}.requested",
            f"mode.{mode_key} must be a present non-negative integer",
            actual=mode.get(mode_key),
        )
    if result is None:
        if requested:
            _fail(
                failures,
                f"runtime_health.{result_key}.present",
                f"runtime health {result_key} report is missing",
                requested=requested,
            )
        return
    if not isinstance(result, dict):
        _fail(
            failures,
            f"runtime_health.{result_key}.type",
            f"runtime health {result_key} report must be an object",
        )
        return
    _check_runtime_health_cycle_phase_result(failures, result, result_key, requested)


def _runtime_health_real_f5_contract(report: dict) -> tuple[int, dict] | None:
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    if mode.get("f5_probe_mode") != "real_process":
        return None
    requested = _optional_nonnegative_int(mode.get("f5_cycles"))
    if not requested:
        return None
    result = report.get("f5_cycle") if isinstance(report.get("f5_cycle"), dict) else {}
    return requested, result


def _real_f5_worker_is_child(parent_pid, worker_pid) -> bool:
    return all((worker_pid is not None, worker_pid != 0, worker_pid != parent_pid))


def _real_f5_trade_date_valid(value: str) -> bool:
    return len(value) == 8 and value.isdigit()


def _real_f5_positive_count(timing: dict, field: str) -> bool:
    value = _optional_nonnegative_int(timing.get(field))
    return value is not None and value > 0


def _real_f5_phases(timing: dict) -> list[str] | None:
    phases = timing.get("event_phases")
    return phases if _is_string_list(phases) else None


def _real_f5_event_count_valid(event_count: int | None) -> bool:
    return event_count is not None and event_count >= len(RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES)


def _real_f5_validity_checks(timing, expected_cycle, parsed) -> dict[str, bool]:
    run_id = parsed["run_id"]
    return {
        "execution": timing.get("execution") == "real_process",
        "cycle": parsed["cycle"] == expected_cycle,
        "status": timing.get("status") == "ok",
        "started": timing.get("started") is True,
        "finished": timing.get("finished") is True,
        "elapsed_ms": _optional_nonnegative_number(timing.get("elapsed_ms")) is not None,
        "job_status": timing.get("job_status") == "succeeded",
        "post_refresh_settled": timing.get("post_refresh_settled") is True,
        "run_id": bool(run_id),
        "snapshot_id": parsed["snapshot_id"] == run_id,
        "worker_pid": _real_f5_worker_is_child(parsed["parent_pid"], parsed["worker_pid"]),
        "effective_trade_date": _real_f5_trade_date_valid(parsed["trade_date"]),
        "symbol_count": _real_f5_positive_count(timing, "symbol_count"),
        "rps_valid_count": _real_f5_positive_count(timing, "rps_valid_count"),
        "sector_count": _real_f5_positive_count(timing, "sector_count"),
        "event_phases": parsed["phases"] == list(RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES),
        "event_count": _real_f5_event_count_valid(parsed["event_count"]),
    }


def _real_f5_phase_evidence(phases: list[str] | None) -> dict:
    actual = phases or []
    return {
        "actual_phases": phases,
        "expected_phases": list(RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES),
        "missing_phases": [phase for phase in RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES if phase not in actual],
        "unexpected_phases": [phase for phase in actual if phase not in RUNTIME_HEALTH_REAL_F5_REQUIRED_PHASES],
    }


def _runtime_health_real_f5_evidence(timing: dict, *, expected_cycle: int) -> dict:
    parsed = {
        "run_id": str(timing.get("run_id") or ""),
        "snapshot_id": str(timing.get("snapshot_id") or ""),
        "parent_pid": _optional_nonnegative_int(timing.get("parent_pid")),
        "worker_pid": _optional_nonnegative_int(timing.get("worker_pid")),
        "phases": _real_f5_phases(timing),
        "event_count": _optional_nonnegative_int(timing.get("event_count")),
        "cycle": _optional_nonnegative_int(timing.get("cycle")),
        "trade_date": str(timing.get("effective_trade_date") or ""),
    }
    checks = _real_f5_validity_checks(timing, expected_cycle, parsed)
    return {
        "valid": all(checks.values()),
        "invalid_fields": [field for field, valid in checks.items() if not valid],
        "run_id": parsed["run_id"],
        "snapshot_id": parsed["snapshot_id"],
        "parent_pid": parsed["parent_pid"],
        "worker_pid": parsed["worker_pid"],
        "effective_trade_date": parsed["trade_date"],
        "event_count": parsed["event_count"],
        "actual_cycle": parsed["cycle"],
        "expected_cycle": expected_cycle,
        **_real_f5_phase_evidence(parsed["phases"]),
    }


def _check_runtime_health_real_f5_timing(failures: list[dict], timing, *, cycle: int) -> None:
    if not isinstance(timing, dict):
        _fail(
            failures,
            "runtime_health.f5_cycle.real_receipt",
            "real F5 cycle receipt must be an object",
            cycle=cycle,
        )
        return
    evidence = _runtime_health_real_f5_evidence(timing, expected_cycle=cycle)
    if evidence.pop("valid"):
        return
    _fail(
        failures,
        "runtime_health.f5_cycle.real_receipt",
        "real F5 cycle is missing subprocess, artifact, phase, or activation evidence",
        cycle=cycle,
        job_status=timing.get("job_status"),
        **evidence,
    )


def _check_runtime_health_real_f5(report: dict, failures: list[dict]) -> None:
    contract = _runtime_health_real_f5_contract(report)
    if contract is None:
        return
    requested, result = contract
    if result.get("probe_mode") != "real_process":
        _fail(
            failures,
            "runtime_health.f5_cycle.real_mode",
            "production runtime health must execute the real isolated F5 process",
            actual=result.get("probe_mode"),
        )
        return

    timings = result.get("cycle_timings")
    if not isinstance(timings, list) or len(timings) != requested:
        _fail(
            failures,
            "runtime_health.f5_cycle.real_receipt",
            "real F5 cycle receipts must exactly match the requested cycle count",
            requested=requested,
            actual=len(timings) if isinstance(timings, list) else None,
        )
        if not isinstance(timings, list):
            return
    for index, timing in enumerate(timings, start=1):
        _check_runtime_health_real_f5_timing(failures, timing, cycle=index)


def _production_quote_timing_valid(timing, expected_cycle: int) -> bool:
    return bool(
        isinstance(timing, dict)
        and _optional_nonnegative_int(timing.get("cycle")) == expected_cycle
        and timing.get("status") == "ok"
        and _optional_nonnegative_number(timing.get("elapsed_ms")) is not None
    )


def _production_quote_invalid_cycles(timings) -> list[int]:
    if not isinstance(timings, list):
        return []
    return [
        cycle
        for cycle, timing in enumerate(timings, start=1)
        if not _production_quote_timing_valid(timing, cycle)
    ]


def _production_quote_contract_valid(result, timings, requested, invalid) -> bool:
    return bool(
        requested
        and isinstance(result, dict)
        and result.get("status") == "ok"
        and _optional_nonnegative_int(result.get("cycles")) == requested
        and isinstance(timings, list)
        and len(timings) == requested
        and not invalid
    )


def _check_runtime_health_production_quote_receipts(report: dict, failures: list[dict]) -> None:
    if not _runtime_health_is_production_full(report):
        return
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    requested = _optional_nonnegative_int(mode.get("quote_cycles"))
    result = report.get("quote_cycle")
    timings = result.get("cycle_timings") if isinstance(result, dict) else None
    invalid = _production_quote_invalid_cycles(timings)
    if _production_quote_contract_valid(result, timings, requested, invalid):
        return
    _fail(
        failures,
        "runtime_health.quote_cycle.receipts",
        "production quote cycle requires one typed successful timing per requested cycle",
        requested=requested,
        actual=len(timings) if isinstance(timings, list) else None,
        invalid_cycles=invalid,
    )


def _runtime_health_post_tab_idle_contract(report: dict):
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    declared = "post_tab_idle_timeout_ms" in mode
    timeout_ms = _optional_nonnegative_int(mode.get("post_tab_idle_timeout_ms")) if declared else None
    return mode, declared, timeout_ms, report.get("post_tab_idle")


def _post_tab_idle_task_context(result: dict) -> dict:
    remaining = result.get("remaining_task_ids")
    started = result.get("started_task_ids")
    task_ids = remaining if isinstance(remaining, list) and remaining else started
    return {
        "phase": "tab_async_tail",
        "sample": "post_tab_idle",
        "task_ids": task_ids if isinstance(task_ids, list) else [],
        "baseline_task_ids": result.get("baseline_task_ids") or [],
        "started_task_ids": started or [],
        "remaining_task_ids": remaining or [],
        "concurrent_startup_task_ids": result.get("concurrent_startup_task_ids") or [],
    }


POST_TAB_IDLE_TASK_ID_FIELDS = (
    "baseline_task_ids",
    "started_task_ids",
    "remaining_task_ids",
    "concurrent_startup_task_ids",
    "active_before_ids",
    "active_after_ids",
)


def _post_tab_idle_task_list_errors(result: dict) -> list[str]:
    errors = []
    for field in POST_TAB_IDLE_TASK_ID_FIELDS:
        value = result.get(field)
        if not _is_string_list(value) or len(value) != len(set(value)):
            errors.append(field)
    return errors


def _post_tab_idle_task_count_errors(result: dict) -> list[str]:
    errors = []
    active_before = _optional_nonnegative_int(result.get("active_before"))
    active_after = _optional_nonnegative_int(result.get("active_after"))
    if active_before is None or active_before != len(result["active_before_ids"]):
        errors.append("active_before")
    if active_after is None or active_after != len(result["active_after_ids"]):
        errors.append("active_after")
    return errors


def _post_tab_idle_task_set_errors(result: dict) -> list[str]:
    errors = []
    baseline = set(result["baseline_task_ids"])
    started = set(result["started_task_ids"])
    concurrent = set(result["concurrent_startup_task_ids"])
    if baseline & started:
        errors.append("baseline_started_overlap")
    if not concurrent <= baseline | started:
        errors.append("concurrent_startup_task_ids_membership")
    owned = started - concurrent
    if set(result["remaining_task_ids"]) != set(result["active_after_ids"]) & owned:
        errors.append("remaining_task_ids_identity")
    return errors


def _post_tab_idle_task_identity_errors(result: dict) -> list[str]:
    errors = _post_tab_idle_task_list_errors(result)
    if errors:
        return errors
    return _post_tab_idle_task_count_errors(result) + _post_tab_idle_task_set_errors(result)


def _post_tab_idle_invalid_fields(result: dict) -> list[str]:
    errors = _post_tab_idle_task_identity_errors(result)
    if result.get("task_id_diagnostics_available") is not True:
        errors.append("task_id_diagnostics_available")
    if result.get("ownership") != "phase_started_task_ids":
        errors.append("ownership")
    if result.get("remaining_task_ids") != []:
        errors.append("remaining_task_ids_not_empty")
    if _optional_nonnegative_int(result.get("timeout_ms")) is None:
        errors.append("timeout_ms")
    return errors


def _check_post_tab_idle_result(result: dict, timeout_ms: int | None, failures: list[dict]) -> None:
    invalid_fields = _post_tab_idle_invalid_fields(result)
    if invalid_fields:
        _fail(
            failures,
            "runtime_health.post_tab_idle.contract",
            "post-tab idle receipt is missing clean phase-owned task diagnostics",
            invalid_fields=sorted(set(invalid_fields)),
            **_post_tab_idle_task_context(result),
        )
    accepted_statuses = {"ok", "skipped"} if timeout_ms == 0 else {"ok"}
    if result.get("status") not in accepted_statuses:
        _fail(
            failures,
            "runtime_health.post_tab_idle.status",
            "background work did not become idle after the tab cycle",
            actual=result.get("status"),
            active_after=result.get("active_after"),
            **_post_tab_idle_task_context(result),
        )
        return
    if result.get("status") == "skipped":
        return
    remaining = result.get("remaining_task_ids")
    if isinstance(remaining, list) and remaining:
        _fail(
            failures,
            "runtime_health.post_tab_idle.remaining_tasks",
            "phase-owned background work remained after the tab async-tail settle",
            **_post_tab_idle_task_context(result),
        )


def _check_runtime_health_post_tab_idle(report: dict, failures: list[dict]) -> None:
    mode, timeout_declared, timeout_ms, result = _runtime_health_post_tab_idle_contract(report)

    if timeout_declared and timeout_ms is None:
        _fail(
            failures,
            "runtime_health.post_tab_idle.timeout",
            "mode.post_tab_idle_timeout_ms must be a present non-negative integer",
            actual=mode.get("post_tab_idle_timeout_ms"),
        )
    if result is None:
        if bool(timeout_ms):
            _fail(
                failures,
                "runtime_health.post_tab_idle.present",
                "runtime health post-tab background-idle report is missing",
            )
        return
    if not isinstance(result, dict):
        _fail(
            failures,
            "runtime_health.post_tab_idle.type",
            "runtime health post-tab background-idle report must be an object",
        )
        return

    _check_post_tab_idle_result(result, timeout_ms, failures)


def _check_runtime_health_workload_phases(report: dict, failures: list[dict]) -> None:
    _check_runtime_health_post_tab_idle(report, failures)
    _check_runtime_health_cycle_phase(
        report,
        failures,
        result_key="f5_cycle",
        mode_key="f5_cycles",
    )
    _check_runtime_health_real_f5(report, failures)
    _check_runtime_health_cycle_phase(
        report,
        failures,
        result_key="quote_cycle",
        mode_key="quote_cycles",
    )
    _check_runtime_health_production_quote_receipts(report, failures)


def _check_runtime_health_execution(report: dict, samples: list[dict], last: dict, failures: list[dict], budget: dict):
    _check_runtime_health_startup(report, failures, budget)
    _check_runtime_health_tab_cycle(report, failures, budget)
    _check_runtime_health_production_tab_cycle(report, failures)
    _check_runtime_health_workload_phases(report, failures)
    _check_runtime_health_kline_cycle(report, failures)
    _check_runtime_health_ui_stalls(samples, failures, budget)
    _check_runtime_health_shutdown(report, failures, budget)
    _check_runtime_health_lineage(report, last, failures)


def _invalid_lineage_network_fields(data_tabs: list[str], entries: dict[str, dict]) -> dict[str, list[str]]:
    invalid: dict[str, list[str]] = {}
    for key in data_tabs:
        entry = entries.get(key)
        if entry is None:
            continue
        fields = [
            field
            for field in ("network_capable", "triggered_network")
            if type(entry.get(field)) is not bool
        ]
        if fields:
            invalid[key] = fields
    return invalid


def _check_lineage_network_boolean_fields(
    data_tabs: list[str],
    entries: dict[str, dict],
    failures: list[dict],
) -> None:
    invalid = _invalid_lineage_network_fields(data_tabs, entries)
    if not invalid:
        return
    _fail(
        failures,
        "runtime_health.data_lineage.boolean_fields",
        "runtime health network lineage fields must be strict booleans",
        invalid=invalid,
    )


def _check_lineage_errors(
    data_tabs: list[str],
    entries: dict[str, dict],
    failures: list[dict],
) -> None:
    errors = sorted(
        key
        for key in data_tabs
        if key in entries and bool(entries[key].get("lineage_error"))
    )
    if errors:
        _fail(
            failures,
            "runtime_health.data_lineage.lineage_error",
            "runtime health data lineage getter failed",
            tabs=errors,
        )


def _check_runtime_health_lineage(report: dict, last: dict, failures: list[dict]) -> None:
    lineage = last.get("data_lineage")
    if not isinstance(lineage, list):
        _fail(failures, "runtime_health.data_lineage.type", "data lineage must be a list")
        return

    requested_tabs = _requested_runtime_health_tabs(report)
    if not requested_tabs:
        return

    entries = _lineage_by_key(lineage)
    data_tabs, declared_excluded = _runtime_health_lineage_partition(last, requested_tabs, failures)
    _check_runtime_health_lineage_exclusions(last, declared_excluded, entries, failures)
    missing = sorted(tab for tab in data_tabs if tab not in entries)
    if missing:
        _fail(
            failures,
            "runtime_health.data_lineage.requested_tabs",
            "runtime health data lineage is missing requested tabs",
            missing=missing,
        )

    required_fields = (
        "key",
        "title",
        "source",
        "cache_refs",
        "network_capable",
        "triggered_network",
        "fallback_or_degraded",
        "loaded",
    )
    missing_fields = {
        key: [field for field in required_fields if field not in entries[key]]
        for key in data_tabs
        if key in entries and any(field not in entries[key] for field in required_fields)
    }
    if missing_fields:
        _fail(
            failures,
            "runtime_health.data_lineage.fields",
            "runtime health data lineage entries are missing required fields",
            missing=missing_fields,
        )
    _check_lineage_errors(data_tabs, entries, failures)
    _check_lineage_network_boolean_fields(data_tabs, entries, failures)


_RUNTIME_HEALTH_REQUIRED_SECTIONS = (
    "background_tasks",
    "timers",
    "event_bus",
    "process",
    "webengine",
    "quotes",
    "market_data",
    "f5_refresh",
    "f5_cache",
    "data_lineage",
)
_RUNTIME_HEALTH_QUOTE_FIELDS = (
    ("request_stats", "runtime_health.quotes.request_stats", "quote request stats are missing"),
    ("provider_degraded", "runtime_health.quotes.provider_degraded", "provider degraded state is missing"),
    ("last_network_error", "runtime_health.quotes.last_network_error", "last network error field is missing"),
)
_RUNTIME_HEALTH_TREND_LIMITS = (
    (
        "background_tasks",
        "last",
        "runtime_health_active_task_final_max",
        "runtime_health.background_tasks.final",
        "runtime health ended with too many active background tasks",
    ),
    (
        "active_timers",
        "net_delta",
        "runtime_health_active_timer_growth_max",
        "runtime_health.timers.active_growth",
        "active timer count grew beyond runtime health budget",
    ),
    (
        "total_timers",
        "net_delta",
        "runtime_health_total_timer_growth_max",
        "runtime_health.timers.total_growth",
        "total timer count grew beyond runtime health budget",
    ),
    (
        "event_receivers",
        "net_delta",
        "runtime_health_event_receiver_growth_max",
        "runtime_health.events.receiver_growth",
        "event receiver count grew beyond runtime health budget",
    ),
    (
        "threads",
        "net_delta",
        "runtime_health_thread_growth_max",
        "runtime_health.threads.growth",
        "thread count grew beyond runtime health budget",
    ),
)
_RUNTIME_HEALTH_STRICT_TREND_KEYS = (
    "background_tasks",
    "active_timers",
    "total_timers",
    "event_receivers",
    "threads",
    "rss_mb",
)
_RUNTIME_HEALTH_REQUIRED_STALL_PHASES = (
    "idle",
    "background_preload",
    "tab_cycle",
    "tab_async_tail",
    "f5_cycle",
    "quote_cycle",
    "kline_cycle",
    "shutdown",
)


def _runtime_health_requires_strict_resources(report: dict) -> bool:
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    return (
        _runtime_health_is_production_full(report)
        or _runtime_health_is_production_probe(report)
        or str(mode.get("mode") or "") in _RUNTIME_HEALTH_SOAK_MINIMUM_SECONDS
    )


def _sample_section(sample: dict, key: str) -> dict:
    value = sample.get(key)
    return value if isinstance(value, dict) else {}


def _sample_background_errors(sample: dict) -> list[str]:
    background = _sample_section(sample, "background_tasks")
    checks = {
        "background_tasks.available": background.get("available") is True,
        "background_tasks.count": _optional_nonnegative_int(background.get("count")) is not None,
    }
    return [field for field, valid in checks.items() if not valid]


def _sample_timer_errors(sample: dict) -> list[str]:
    timers = _sample_section(sample, "timers")
    active_timers = _optional_nonnegative_int(timers.get("active"))
    total_timers = _optional_nonnegative_int(timers.get("total"))
    errors = [field for field, value in (("timers.active", active_timers), ("timers.total", total_timers)) if value is None]
    if active_timers is not None and total_timers is not None and active_timers > total_timers:
        errors.append("timers.active_gt_total")
    return errors


def _sample_process_event_webengine_errors(sample: dict) -> list[str]:
    event_bus = _sample_section(sample, "event_bus")
    process = _sample_section(sample, "process")
    webengine = _sample_section(sample, "webengine")
    checks = {
        "event_bus.total_receivers": _optional_nonnegative_int(event_bus.get("total_receivers")) is not None,
        "process.rss_mb": _optional_nonnegative_number(process.get("rss_mb")) is not None,
        "process.thread_count": _optional_nonnegative_int(process.get("thread_count")) is not None,
        "webengine.available": webengine.get("available") is True,
        "webengine.count": _optional_nonnegative_int(webengine.get("count")) is not None,
    }
    return [field for field, valid in checks.items() if not valid]


def _sample_stall_errors(sample: dict) -> list[str]:
    stalls = _sample_section(sample, "ui_stalls")
    checks = {
        "ui_stalls.installed": stalls.get("installed") is True,
        "ui_stalls.critical_count": _optional_nonnegative_int(stalls.get("critical_count")) is not None,
        "ui_stalls.event_loop_critical_count": (
            _optional_nonnegative_int(stalls.get("event_loop_critical_count")) is not None
        ),
        "ui_stalls.max_elapsed_ms": _optional_nonnegative_number(stalls.get("max_elapsed_ms")) is not None,
    }
    return [field for field, valid in checks.items() if not valid]


def _strict_runtime_sample_errors(sample) -> tuple[list[str], list[str]]:
    if not isinstance(sample, dict):
        return ["sample"], ["ui_stalls"]
    resources = (
        _sample_background_errors(sample)
        + _sample_timer_errors(sample)
        + _sample_process_event_webengine_errors(sample)
    )
    return resources, _sample_stall_errors(sample)


def _check_runtime_health_strict_samples(samples: list[dict], failures: list[dict]) -> None:
    resource_invalid = []
    stall_invalid = []
    for position, sample in enumerate(samples):
        resource_errors, stall_errors = _strict_runtime_sample_errors(sample)
        label = sample.get("label") if isinstance(sample, dict) else None
        if resource_errors:
            resource_invalid.append({"position": position, "label": label, "fields": resource_errors})
        if stall_errors:
            stall_invalid.append({"position": position, "label": label, "fields": stall_errors})
    if resource_invalid:
        _fail(
            failures,
            "runtime_health.resources.sample_diagnostics",
            "production/soak runtime samples are missing finite non-negative resource evidence",
            samples=resource_invalid,
        )
    if stall_invalid:
        _fail(
            failures,
            "runtime_health.ui_stall.diagnostics",
            "production/soak runtime samples are missing installed UI-stall diagnostics",
            samples=stall_invalid,
        )


def _strict_runtime_resource_values(samples: list[dict], key: str) -> list[float]:
    getters = {
        "background_tasks": lambda sample: sample["background_tasks"]["count"],
        "active_timers": lambda sample: sample["timers"]["active"],
        "total_timers": lambda sample: sample["timers"]["total"],
        "event_receivers": lambda sample: sample["event_bus"]["total_receivers"],
        "threads": lambda sample: sample["process"]["thread_count"],
        "rss_mb": lambda sample: sample["process"]["rss_mb"],
    }
    values = []
    for sample in samples:
        try:
            value = _optional_nonnegative_number(getters[key](sample))
        except (KeyError, TypeError):
            value = None
        if value is not None:
            values.append(value)
    return values


def _parsed_trend_snapshot(snapshot: dict) -> dict:
    return {
        "count": _optional_nonnegative_int(snapshot.get("count")),
        "first": _optional_nonnegative_number(snapshot.get("first")),
        "last": _optional_nonnegative_number(snapshot.get("last")),
        "net_delta": _optional_finite_number(snapshot.get("net_delta")),
        "range": _optional_nonnegative_number(snapshot.get("range")),
        "max": _optional_nonnegative_number(snapshot.get("max")),
    }


def _trend_net_delta_consistent(parsed: dict) -> bool:
    values = [parsed[field] for field in ("first", "last", "net_delta")]
    return None in values or math.isclose(values[1] - values[0], values[2], abs_tol=1e-3)


def _trend_range_consistent(parsed: dict) -> bool:
    values = [parsed[field] for field in ("first", "last", "range")]
    return None in values or values[2] + 1e-3 >= abs(values[1] - values[0])


def _trend_max_consistent(parsed: dict) -> bool:
    values = [parsed[field] for field in ("first", "last", "max")]
    return None in values or values[2] + 1e-3 >= max(values[0], values[1])


def _trend_snapshot_metadata_errors(snapshot: dict) -> list[str]:
    errors = []
    if "tail_range" in snapshot and _optional_nonnegative_number(snapshot.get("tail_range")) is None:
        errors.append("tail_range")
    valid_bases = {
        "post_kline_close_samples",
        "tail_runtime_health_samples",
    }
    if "basis" in snapshot and snapshot.get("basis") not in valid_bases:
        errors.append("basis")
    return errors


def _trend_snapshot_invalid_fields(snapshot) -> list[str]:
    if not isinstance(snapshot, dict):
        return ["snapshot"]
    parsed = _parsed_trend_snapshot(snapshot)
    errors = [field for field, value in parsed.items() if value is None]
    if parsed["count"] == 0:
        errors.append("count")
    consistency = {
        "net_delta_inconsistent": _trend_net_delta_consistent(parsed),
        "range_inconsistent": _trend_range_consistent(parsed),
        "max_inconsistent": _trend_max_consistent(parsed),
    }
    errors.extend(field for field, valid in consistency.items() if not valid)
    return errors + _trend_snapshot_metadata_errors(snapshot)


def _trend_snapshot_matches_samples(snapshot: dict, samples: list[dict], key: str) -> bool:
    values = _strict_runtime_resource_values(samples, key)
    if not values:
        return False
    if "basis" in snapshot:
        return math.isclose(float(snapshot["last"]), values[-1], abs_tol=1e-3)
    expected = _runtime_health_trend_one(values)
    if snapshot.get("count") != expected["count"]:
        return False
    return all(
        math.isclose(float(snapshot[field]), float(expected[field]), abs_tol=1e-3)
        for field in ("first", "last", "net_delta", "range", "max")
    )


def _check_runtime_health_strict_trend(trend, samples: list[dict], failures: list[dict]) -> None:
    invalid = {}
    if not isinstance(trend, dict):
        invalid["trend"] = ["type"]
    else:
        for key in _RUNTIME_HEALTH_STRICT_TREND_KEYS:
            snapshot = trend.get(key)
            errors = _trend_snapshot_invalid_fields(snapshot)
            if not errors and not _trend_snapshot_matches_samples(snapshot, samples, key):
                errors.append("sample_mismatch")
            if errors:
                invalid[key] = errors
    if invalid:
        _fail(
            failures,
            "runtime_health.resources.trend_diagnostics",
            "production/soak resource trend is missing finite or sample-consistent evidence",
            invalid=invalid,
        )


def _runtime_stall_boundary_invalid_fields(boundary) -> list[str]:
    if not isinstance(boundary, dict):
        return ["type"]
    errors = []
    phase = boundary.get("phase")
    if not isinstance(phase, str) or not phase.strip():
        errors.append("phase")
    if _optional_nonnegative_int(boundary.get("settle_ms")) is None:
        errors.append("settle_ms")
    if _optional_nonnegative_number(boundary.get("elapsed_ms")) is None:
        errors.append("elapsed_ms")
    if boundary.get("stall_snapshot_reset") is not True:
        errors.append("stall_snapshot_reset")
    return errors


def _stall_phase_order_complete(phases: list[str]) -> bool:
    required_positions = []
    for phase in _RUNTIME_HEALTH_REQUIRED_STALL_PHASES:
        positions = [index for index, actual in enumerate(phases) if actual == phase]
        if len(positions) != 1:
            return False
        required_positions.append(positions[0])
    return required_positions == sorted(required_positions)


def _stall_sampling_structure(sampling) -> tuple[list[str], list]:
    invalid = []
    if not isinstance(sampling, dict):
        return ["ui_stall_sampling"], []
    checks = {
        "scope": sampling.get("scope") == "phase_local",
        "boundary_strategy": sampling.get("boundary_strategy") == "qt_event_loop_settle_then_reset",
        "phase_boundaries": isinstance(sampling.get("phase_boundaries"), list),
    }
    invalid.extend(field for field, valid in checks.items() if not valid)
    boundaries = sampling.get("phase_boundaries") if checks["phase_boundaries"] else []
    return invalid, boundaries


def _stall_boundary_evidence(boundaries) -> tuple[dict, list]:
    boundary_errors = {
        position: errors
        for position, boundary in enumerate(boundaries)
        if (errors := _runtime_stall_boundary_invalid_fields(boundary))
    }
    phases = [boundary.get("phase") for boundary in boundaries if isinstance(boundary, dict)]
    return boundary_errors, phases


def _check_runtime_health_ui_stall_sampling(report: dict, failures: list[dict]) -> None:
    if not _runtime_health_requires_strict_resources(report):
        return
    invalid, boundaries = _stall_sampling_structure(report.get("ui_stall_sampling"))
    boundary_errors, phases = _stall_boundary_evidence(boundaries)
    if boundary_errors:
        invalid.append("phase_boundary_fields")
    if not _stall_phase_order_complete(phases):
        invalid.append("phase_order")
    if not invalid:
        return
    _fail(
        failures,
        "runtime_health.ui_stall_sampling.diagnostics",
        "production/soak UI-stall sampling lacks complete phase-local reset boundaries",
        invalid_fields=invalid,
        boundary_errors=boundary_errors,
        actual_phases=phases,
        required_phases=list(_RUNTIME_HEALTH_REQUIRED_STALL_PHASES),
    )


def _check_runtime_health_strict_resource_evidence(
    report: dict,
    samples: list[dict],
    trend,
    failures: list[dict],
) -> None:
    if not _runtime_health_requires_strict_resources(report):
        return
    _check_runtime_health_strict_samples(samples, failures)
    _check_runtime_health_strict_trend(trend, samples, failures)
    _check_runtime_health_ui_stall_sampling(report, failures)


def _check_runtime_health_required_fields(last: dict, failures: list[dict]) -> None:
    for key in _RUNTIME_HEALTH_REQUIRED_SECTIONS:
        if key not in last:
            _fail(failures, f"runtime_health.{key}.present", f"runtime health report missing {key}")

    quotes = last.get("quotes") or {}
    for field, check, detail in _RUNTIME_HEALTH_QUOTE_FIELDS:
        if field not in quotes:
            _fail(failures, check, detail)

    f5_cache = last.get("f5_cache") or {}
    for key in ("cache_version", "trade_date", "updated_at"):
        if key not in f5_cache:
            _fail(failures, f"runtime_health.f5_cache.{key}", f"F5 cache {key} field is missing")


def _check_runtime_health_background_task_snapshot(last: dict, failures: list[dict]) -> None:
    background_tasks = last.get("background_tasks")
    if not isinstance(background_tasks, dict):
        _fail(
            failures,
            "runtime_health.background_tasks.diagnostics",
            "background task diagnostics must be an object",
        )
        return
    if background_tasks.get("available") is not True:
        _fail(
            failures,
            "runtime_health.background_tasks.diagnostics",
            "background task diagnostics were missing, invalid, or unavailable",
            actual=background_tasks.get("available"),
        )
    if _optional_nonnegative_int(background_tasks.get("count")) is None:
        _fail(
            failures,
            "runtime_health.background_tasks.count",
            "background task count was missing or invalid",
            actual=background_tasks.get("count"),
        )


def _check_runtime_health_resource_trends(trend: dict, failures: list[dict], budget: dict) -> None:
    for trend_key, value_key, budget_key, check, detail in _RUNTIME_HEALTH_TREND_LIMITS:
        snapshot = trend.get(trend_key) or {}
        actual = snapshot.get(value_key)
        if _as_float(actual) > budget[budget_key]:
            _fail(failures, check, detail, actual=actual, budget=budget[budget_key])


def _runtime_health_webengine_context(report: dict, trend: dict) -> dict:
    cycle = report.get("kline_cycle") if isinstance(report.get("kline_cycle"), dict) else {}
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    requested = _optional_nonnegative_int(mode.get("kline_cycles"))
    requested = requested if requested is not None else 0
    prewarm_mode = mode.get("kline_prewarm_enabled")
    prewarm = cycle.get("prewarm") if isinstance(cycle.get("prewarm"), dict) else {}
    baseline_keeper = _optional_nonnegative_int(cycle.get("baseline_managed_webengine_keeper_count"))
    final_keeper = _optional_nonnegative_int(cycle.get("final_managed_webengine_keeper_count"))
    managed_keeper = _optional_nonnegative_int(cycle.get("managed_webengine_keeper_count"))
    baseline_process = _optional_nonnegative_int(cycle.get("baseline_webengine_child_count"))
    final_process = _optional_nonnegative_int(cycle.get("final_webengine_child_count"))
    process_delta = _optional_int(cycle.get("webengine_child_count_net_delta"))
    diagnostics_valid = all(
        (
            cycle.get("baseline_webengine_available") is True,
            cycle.get("final_webengine_available") is True,
            baseline_process is not None,
            final_process is not None,
            process_delta is not None,
        )
    )
    children = trend.get("webengine_children") or {}
    return {
        "cycle": cycle,
        "requested": requested,
        "prewarm_mode": prewarm_mode,
        "prewarm_requested": prewarm_mode is True,
        "prewarm": prewarm,
        "keeper_counts": (baseline_keeper, final_keeper, managed_keeper),
        "baseline_keeper": baseline_keeper,
        "final_keeper": final_keeper,
        "managed_keeper": managed_keeper,
        "active_views": _optional_nonnegative_int(cycle.get("active_chart_view_count_after_close")),
        "baseline_process": baseline_process,
        "final_process": final_process,
        "process_delta": process_delta,
        "diagnostics_valid": diagnostics_valid,
        "children": children,
        "raw_final_process": _optional_nonnegative_number(children.get("last")),
    }


def _keeper_count_over_limit(value: int | None) -> bool:
    return value is not None and value > 1


def _different_present_counts(left: int | None, right: int | None) -> bool:
    return left is not None and right is not None and left != right


def _check_runtime_health_keeper_diagnostics(context: dict, failures: list[dict]) -> None:
    cycle = context["cycle"]
    counts = context["keeper_counts"]
    if any(map(_keeper_count_over_limit, counts)):
        _fail(
            failures,
            "runtime_health.webengine.managed_keeper_limit",
            "managed K-line WebEngine keeper exceeded its single-view bound",
            actual=list(counts),
            budget=1,
        )
    if context["requested"] > 0 and None in counts:
        _fail(
            failures,
            "runtime_health.webengine.keeper_diagnostics",
            "managed K-line WebEngine keeper baseline/final/current diagnostics were missing or invalid",
            baseline=cycle.get("baseline_managed_webengine_keeper_count"),
            final=cycle.get("final_managed_webengine_keeper_count"),
            current=cycle.get("managed_webengine_keeper_count"),
        )
    if _different_present_counts(context["baseline_keeper"], context["final_keeper"]):
        _fail(
            failures,
            "runtime_health.webengine.keeper_stability",
            "managed K-line WebEngine keeper count changed inside the measured cycle",
            baseline=context["baseline_keeper"],
            final=context["final_keeper"],
        )
    if _different_present_counts(context["managed_keeper"], context["final_keeper"]):
        _fail(
            failures,
            "runtime_health.webengine.keeper_consistency",
            "managed K-line WebEngine current keeper count did not match the final cycle count",
            current=context["managed_keeper"],
            final=context["final_keeper"],
        )


def _check_runtime_health_prewarm_mode(context: dict, failures: list[dict]) -> None:
    requested = context["requested"]
    prewarm_mode = context["prewarm_mode"]
    if requested > 0 and not isinstance(prewarm_mode, bool):
        _fail(
            failures,
            "runtime_health.webengine.prewarm_mode",
            "mode.kline_prewarm_enabled must be an explicit boolean for requested K-line cycles",
            actual=prewarm_mode,
        )
    prewarm = context["prewarm"]
    if requested > 0 and prewarm.get("requested") is not context["prewarm_requested"]:
        _fail(
            failures,
            "runtime_health.webengine.prewarm_consistency",
            "K-line prewarm report did not match mode.kline_prewarm_enabled",
            mode_requested=context["prewarm_requested"],
            cycle_requested=prewarm.get("requested"),
        )


def _check_runtime_health_keeper_expectation(context: dict, failures: list[dict]) -> None:
    counts = context["keeper_counts"]
    prewarm = context["prewarm"]
    if context["prewarm_requested"] and (counts != (1, 1, 1) or prewarm.get("status") != "ok"):
        _fail(
            failures,
            "runtime_health.webengine.requested_keeper_ready",
            "requested K-line WebEngine prewarm did not produce one stable keeper before the measured cycle",
            actual=list(counts),
            prewarm_status=prewarm.get("status"),
            budget=1,
        )
    unexpected = context["requested"] > 0 and context["prewarm_mode"] is False
    if unexpected and counts != (0, 0, 0):
        _fail(
            failures,
            "runtime_health.webengine.unexpected_keeper",
            "K-line WebEngine keeper existed although prewarm was not requested",
            actual=list(counts),
            budget=0,
        )


def _check_runtime_health_active_views(context: dict, failures: list[dict]) -> None:
    active_views = context["active_views"]
    cycle = context["cycle"]
    if context["requested"] > 0 and active_views is None:
        _fail(
            failures,
            "runtime_health.webengine.active_chart_views_diagnostics",
            "K-line active chart view count after close was missing or invalid",
            actual=cycle.get("active_chart_view_count_after_close"),
        )
    elif active_views is not None and active_views > 0:
        _fail(
            failures,
            "runtime_health.webengine.active_chart_views_final",
            "K-line chart WebEngine views remained active after the close cycle",
            actual=active_views,
            budget=0,
        )


def _check_runtime_health_open_criterion(context: dict, failures: list[dict]) -> None:
    criterion = context["cycle"].get("open_success_criterion")
    if context["requested"] > 0 and criterion != "chart_ready":
        _fail(
            failures,
            "runtime_health.kline_cycle.open_criterion",
            "runtime health K-line cycle did not declare chart_ready as its open criterion",
            actual=criterion,
            expected="chart_ready",
        )


def _valid_kline_stage_timings(timings) -> bool:
    if not isinstance(timings, dict) or set(timings) != set(KLINE_OPEN_STAGE_ORDER):
        return False
    values = [_optional_nonnegative_number(timings.get(stage)) for stage in KLINE_OPEN_STAGE_ORDER]
    if any(value is None for value in values):
        return False
    return all(left <= right for left, right in pairwise(values))


def _valid_kline_stage_cycle(cycle) -> bool:
    return bool(
        isinstance(cycle, dict)
        and cycle.get("required_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and cycle.get("completed_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and cycle.get("pending_stages") == []
        and cycle.get("complete") is True
    )


def _valid_kline_stage_contract(contract, stage_cycles, requested: int) -> bool:
    return bool(
        isinstance(contract, dict)
        and contract.get("required_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and contract.get("complete") is True
        and isinstance(stage_cycles, list)
        and len(stage_cycles) == requested
        and all(_valid_kline_stage_cycle(cycle) for cycle in stage_cycles)
    )


def _invalid_kline_stage_cycle_timings(stage_cycles: list) -> bool:
    return any(
        not _valid_kline_stage_timings(cycle.get("timings_ms")) for cycle in stage_cycles if isinstance(cycle, dict)
    )


def _check_runtime_health_stage_contract(context: dict, failures: list[dict]) -> None:
    if context["requested"] <= 0:
        return
    contract = context["cycle"].get("stage_contract")
    stage_cycles = contract.get("cycles") if isinstance(contract, dict) else None
    if not _valid_kline_stage_contract(contract, stage_cycles, context["requested"]):
        _fail(
            failures,
            "runtime_health.kline_cycle.stage_contract",
            "runtime health K-line cycle did not complete the ordered six-stage contract",
            actual=contract,
            expected=list(KLINE_OPEN_STAGE_ORDER),
        )
    if not isinstance(stage_cycles, list):
        return
    if _invalid_kline_stage_cycle_timings(stage_cycles):
        _fail(
            failures,
            "runtime_health.kline_cycle.stage_timings",
            "runtime health K-line stage timings were missing, invalid, or out of order",
            expected=list(KLINE_OPEN_STAGE_ORDER),
        )


def _valid_runtime_health_stall_scope(cycle: dict) -> bool:
    stalls = cycle.get("ui_stalls")
    return bool(
        isinstance(stalls, dict)
        and stalls.get("scope") == "kline_open_to_chart_ready"
        and stalls.get("reset_succeeded") is True
    )


def _runtime_health_stall_evidence(cycles: list) -> dict:
    actual_indices = []
    valid_cycles = []
    for cycle in cycles:
        actual_indices.append(
            _optional_nonnegative_int(cycle.get("cycle_index")) if isinstance(cycle, dict) else None
        )
        if isinstance(cycle, dict):
            valid_cycles.append(cycle)
    series, invalid_cycles = _kline_open_stall_series(valid_cycles)
    return {
        "actual_indices": actual_indices,
        "valid_cycles": valid_cycles,
        "series": series,
        "invalid_cycles": invalid_cycles,
        "invalid_scope_cycles": [
            cycle.get("cycle_index") for cycle in valid_cycles if not _valid_runtime_health_stall_scope(cycle)
        ],
    }


def _runtime_health_stall_evidence_invalid(evidence: dict, cycles: list, expected_indices: list[int]) -> bool:
    return bool(
        len(cycles) != len(expected_indices)
        or evidence["actual_indices"] != expected_indices
        or len(evidence["valid_cycles"]) != len(cycles)
        or evidence["invalid_cycles"]
        or evidence["invalid_scope_cycles"]
    )


def _report_runtime_health_stall_diagnostics(
    failures: list[dict],
    evidence: dict,
    expected_indices: list[int],
) -> None:
    _fail(
        failures,
        "runtime_health.kline_cycle.ui_stall.diagnostics",
        "per-open runtime health K-line UI stall diagnostics were incomplete or invalid",
        expected_cycles=expected_indices,
        actual_cycles=evidence["actual_indices"],
        invalid_cycles=evidence["invalid_cycles"],
        invalid_scope_cycles=evidence["invalid_scope_cycles"],
    )


def _check_runtime_health_stall_peaks(failures: list[dict], budget: dict, series: dict) -> None:
    checks = (
        (
            "critical_count",
            "kline_open_ui_critical_stall_max",
            "runtime_health.kline_cycle.ui_stall.critical_count",
        ),
        (
            "event_loop_critical_count",
            "kline_open_ui_event_loop_critical_stall_max",
            "runtime_health.kline_cycle.ui_stall.event_loop_critical_count",
        ),
        (
            "max_elapsed_ms",
            "kline_open_ui_max_stall_ms",
            "runtime_health.kline_cycle.ui_stall.max_elapsed",
        ),
    )
    for field, budget_key, check in checks:
        _check_kline_stall_peak(
            failures,
            budget,
            series[field],
            budget_key,
            check,
            f"runtime health K-line {field} exceeded the per-open budget",
        )


def _check_runtime_health_open_ui_stalls(
    context: dict,
    failures: list[dict],
    budget: dict,
) -> None:
    requested = context["requested"]
    if requested <= 0:
        return
    cycles = context["cycle"].get("open_ui_stalls")
    if not isinstance(cycles, list):
        _fail(
            failures,
            "runtime_health.kline_cycle.ui_stall.diagnostics",
            "per-open runtime health K-line UI stall diagnostics were missing or invalid",
        )
        return
    expected_indices = list(range(1, requested + 1))
    evidence = _runtime_health_stall_evidence(cycles)
    if _runtime_health_stall_evidence_invalid(evidence, cycles, expected_indices):
        _report_runtime_health_stall_diagnostics(failures, evidence, expected_indices)
    _check_runtime_health_stall_peaks(failures, budget, evidence["series"])


def _check_runtime_health_kline_execution_scope(
    report: dict,
    context: dict,
    failures: list[dict],
) -> None:
    if context["requested"] <= 0 or report.get("report_type") != "runtime_health_stability_suite":
        return
    mode = report.get("mode") if isinstance(report.get("mode"), dict) else {}
    qt_platform = str(mode.get("qt_platform") or "").strip().lower()
    if mode.get("native_qt") is True and mode.get("show_window") is True and qt_platform != "offscreen":
        return
    _fail(
        failures,
        "runtime_health.kline_cycle.execution_scope",
        "runtime health K-line cycles require native Qt on a visible non-offscreen window",
        native_qt=mode.get("native_qt"),
        show_window=mode.get("show_window"),
        qt_platform=mode.get("qt_platform"),
    )


def _check_runtime_health_cycle_diagnostics(context: dict, failures: list[dict]) -> None:
    cycle = context["cycle"]
    valid = context["diagnostics_valid"]
    baseline = context["baseline_process"]
    final = context["final_process"]
    delta = context["process_delta"]
    if context["requested"] > 0 and not valid:
        _fail(
            failures,
            "runtime_health.webengine.cycle_diagnostics",
            "QtWebEngine cycle diagnostics were unavailable or invalid",
            baseline_available=cycle.get("baseline_webengine_available"),
            final_available=cycle.get("final_webengine_available"),
            baseline_count=cycle.get("baseline_webengine_child_count"),
            final_count=cycle.get("final_webengine_child_count"),
            net_delta=cycle.get("webengine_child_count_net_delta"),
        )
    if valid and delta != final - baseline:
        _fail(
            failures,
            "runtime_health.webengine.cycle_delta",
            "QtWebEngine cycle process delta did not match its baseline and final counts",
            actual=delta,
            expected=final - baseline,
        )
    if valid and delta > 0:
        _fail(
            failures,
            "runtime_health.webengine.child_growth",
            "QtWebEngine child process count grew across the K-line close cycle",
            actual=delta,
            budget=0,
        )


def _webengine_process_budget(context: dict, budget: dict) -> tuple[int, str]:
    if context["managed_keeper"] == 1:
        return budget["runtime_health_managed_webengine_process_final_max"], "managed"
    return budget["runtime_health_webengine_final_max"], "unmanaged"


def _check_runtime_health_cycle_process_bound(context: dict, failures: list[dict], budget: dict) -> None:
    if not context["diagnostics_valid"]:
        return
    process_budget, kind = _webengine_process_budget(context, budget)
    if context["final_process"] <= process_budget:
        return
    _fail(
        failures,
        "runtime_health.webengine.managed_process_bound" if kind == "managed" else "runtime_health.webengine.final",
        "QtWebEngine child process count exceeded its final cycle bound",
        actual=context["final_process"],
        budget=process_budget,
    )


def _check_runtime_health_final_process_bound(context: dict, failures: list[dict], budget: dict) -> None:
    raw_final = context["raw_final_process"]
    if context["requested"] > 0 and raw_final is None:
        _fail(
            failures,
            "runtime_health.webengine.final_diagnostics",
            "final QtWebEngine trend count was missing or invalid",
            actual=context["children"].get("last"),
        )
        return
    if raw_final is None:
        return
    effective_keeper = context["managed_keeper"] if context["managed_keeper"] is not None else 0
    if effective_keeper not in {0, 1}:
        return
    process_budget, kind = _webengine_process_budget(context, budget)
    if raw_final <= process_budget:
        return
    check = "runtime_health.webengine.managed_process_bound" if kind == "managed" else "runtime_health.webengine.final"
    detail = (
        "QtWebEngine child process count exceeded the managed keeper bound"
        if kind == "managed"
        else "QtWebEngine child processes remained at final runtime health sample"
    )
    _fail(failures, check, detail, actual=raw_final, budget=process_budget)


def _check_runtime_health_memory_trend(
    report: dict, samples: list[dict], trend: dict, failures: list[dict], budget: dict
) -> None:
    rss_values = _runtime_health_values(samples, lambda item: (item.get("process") or {}).get("rss_mb"))
    rss_tail_range = _tail_range(rss_values)
    rss_trend = trend.get("rss_mb") or _runtime_health_post_workload_rss_trend(report, samples)
    range_key = "tail_range" if "tail_range" in rss_trend else "range"
    rss_trend_range = _as_float(rss_trend.get(range_key))
    if rss_trend_range is not None and rss_trend.get("basis") in {
        "post_kline_close_samples",
        "tail_runtime_health_samples",
    }:
        rss_tail_range = rss_trend_range
    if rss_tail_range > budget["runtime_health_rss_tail_range_mb"]:
        _fail(
            failures,
            "runtime_health.memory.rss_tail_range",
            "runtime health RSS tail range exceeded budget",
            actual=rss_tail_range,
            budget=budget["runtime_health_rss_tail_range_mb"],
        )


def check_runtime_health_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    _check_runtime_health_production_profile(report, failures)
    _check_runtime_health_unhandled_ui_exceptions(report, failures)
    _check_runtime_health_window_visibility(report, failures)
    _check_runtime_health_background_preload(report, failures)
    samples = _runtime_health_samples(report)
    if not samples:
        _fail(failures, "runtime_health.present", "runtime health samples are missing")
        return failures
    _check_runtime_health_soak_contract(report, samples, failures)
    _check_runtime_health_background_preload_network(report, samples, failures)

    last = samples[-1] if isinstance(samples[-1], dict) else {}
    _check_runtime_health_required_fields(last, failures)
    _check_runtime_health_background_task_snapshot(last, failures)
    _check_runtime_health_execution(report, samples, last, failures, budget)
    raw_trend = report.get("budget_trend") or report.get("trend") or _runtime_health_trend(samples)
    _check_runtime_health_strict_resource_evidence(report, samples, raw_trend, failures)
    trend = raw_trend if isinstance(raw_trend, dict) else {}
    _check_runtime_health_resource_trends(trend, failures, budget)

    context = _runtime_health_webengine_context(report, trend)
    _check_runtime_health_keeper_diagnostics(context, failures)
    _check_runtime_health_prewarm_mode(context, failures)
    _check_runtime_health_keeper_expectation(context, failures)
    _check_runtime_health_active_views(context, failures)
    _check_runtime_health_open_criterion(context, failures)
    _check_runtime_health_stage_contract(context, failures)
    _check_runtime_health_open_ui_stalls(context, failures, budget)
    _check_runtime_health_kline_execution_scope(report, context, failures)
    _check_runtime_health_cycle_diagnostics(context, failures)
    _check_runtime_health_cycle_process_bound(context, failures, budget)
    _check_runtime_health_final_process_bound(context, failures, budget)
    _check_runtime_health_memory_trend(report, samples, trend, failures, budget)
    return failures


def _thresholds_from_args(args: argparse.Namespace) -> dict:
    return {key: getattr(args, key) for key in CLI_THRESHOLD_KEYS}


def run_budget_checks(args: argparse.Namespace) -> dict:
    thresholds = _thresholds_from_args(args)
    checks: list[dict] = []

    for label, path, checker in (
        ("gbbq", args.gbbq_report, check_gbbq_budget),
        ("tab_cycle", args.tab_report, check_tab_cycle_budget),
        ("kline", args.kline_report, check_kline_budget),
        ("kline_lifecycle", args.kline_lifecycle_report, check_kline_lifecycle_budget),
        ("soak", args.soak_report, check_soak_budget),
        ("round4", args.round4_report, check_round4_budget),
        ("round5", args.round5_report, check_round5_budget),
        ("runtime_health", args.runtime_health_report, check_runtime_health_budget),
    ):
        if not path:
            continue
        failures = checker(_read_json(path), thresholds)
        checks.append(
            {
                "label": label,
                "path": str(path),
                "status": "fail" if failures else "ok",
                "failures": failures,
            }
        )

    status = "fail" if any(check["failures"] for check in checks) else "ok"
    return {
        "status": status,
        "thresholds": thresholds,
        "checks": checks,
    }


def _add_threshold_arguments(parser: argparse.ArgumentParser) -> None:
    for key in CLI_THRESHOLD_KEYS:
        default = DEFAULT_THRESHOLDS[key]
        value_type = int if isinstance(default, int) and not isinstance(default, bool) else float
        parser.add_argument(f"--{key.replace('_', '-')}", type=value_type, default=default)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate repeatable performance probe reports against budgets.")
    parser.add_argument("--gbbq-report", type=Path, default=None)
    parser.add_argument("--tab-report", type=Path, default=None)
    parser.add_argument("--kline-report", type=Path, default=None)
    parser.add_argument("--kline-lifecycle-report", type=Path, default=None)
    parser.add_argument("--soak-report", type=Path, default=None)
    parser.add_argument("--round4-report", type=Path, default=None)
    parser.add_argument("--round5-report", type=Path, default=None)
    parser.add_argument("--runtime-health-report", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    _add_threshold_arguments(parser)
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
