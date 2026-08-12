"""Native Windows Qt profiler for startup and the first Watchlist render.

The probe uses QApplication.exec() plus QAbstractEventDispatcher awake/aboutToBlock
signals. It deliberately rejects offscreen/minimal Qt plugins so dispatcher sleep
is not confused with real event-handling work.
"""

from __future__ import annotations

import argparse
import atexit
import cProfile
import json
import os
import platform
import pstats
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QRegion

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NON_NATIVE_QT_PLATFORMS = frozenset({"offscreen", "minimal", "minimalegl", "vnc", "webgl"})
RESIDUAL_REPAINT_METRICS = (
    "watchlist_model_update_ms",
    "watchlist_table_paint_ms",
    "tab_transition_snapshot_ms",
    "tab_transition_snapshot_skipped",
)
WATCHLIST_REVEAL_METRICS = RESIDUAL_REPAINT_METRICS + ("ui_event_loop_stall_ms",)
PREWARM_RUNTIME_METRICS = ("ui_method_stall_ms", "ui_event_loop_stall_ms")
SHELL_NAV_REPAINT_METRICS = (
    "watchlist_table_paint_ms",
    "watchlist_table_paint_delay_ms",
    "watchlist_shell_nav_repaint_guard",
    "watchlist_membership_reconcile",
    "ui_event_loop_stall_ms",
)


def _native_platform_error(*, requested: str, actual: str, system: str | None = None) -> str:
    system_name = str(system or sys.platform).strip().lower()
    requested_name = str(requested or "").strip().lower()
    actual_name = str(actual or "").strip().lower()
    if system_name != "win32":
        return f"native Watchlist profile requires Windows, current platform={system_name or 'unknown'}"
    if requested_name in NON_NATIVE_QT_PLATFORMS:
        return f"QT_QPA_PLATFORM={requested_name} is not a native desktop platform"
    if actual_name != "windows":
        return f"Qt platform plugin must be windows, actual={actual_name or 'unknown'}"
    return ""


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, float(percentile)))
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_durations(values: list[float]) -> dict:
    clean = [max(0.0, float(value)) for value in values]
    return {
        "count": len(clean),
        "total_ms": round(sum(clean), 3),
        "max_ms": round(max(clean, default=0.0), 3),
        "p50_ms": round(_percentile(clean, 0.50), 3),
        "p95_ms": round(_percentile(clean, 0.95), 3),
        "p99_ms": round(_percentile(clean, 0.99), 3),
        "over_50ms": sum(value >= 50.0 for value in clean),
        "over_100ms": sum(value >= 100.0 for value in clean),
    }


def _sample_delivered_full_viewport(sample) -> bool:
    tags = getattr(sample, "tags", {}) or {}
    value = tags.get("delivered_full_viewport")
    if value is not None:
        return str(value).strip().lower() == "true"
    return float(tags.get("dirty_bounding_area_ratio", 0.0) or 0.0) >= 0.99


def _summarize_shell_nav_paint_metrics(paint_samples: list) -> dict:
    """Summarize Watchlist paint metrics observed during a shell-nav return."""
    samples = list(paint_samples or ())
    full_flags = [_sample_delivered_full_viewport(sample) for sample in samples]
    other_full_flags = [
        full and str((getattr(sample, "tags", {}) or {}).get("reason", "")).strip() == "other"
        for sample, full in zip(samples, full_flags, strict=True)
    ]
    after_first = samples[1:]
    after_first_full_flags = full_flags[1:]
    after_first_other_full_flags = other_full_flags[1:]
    return {
        "count": len(samples),
        "durations": summarize_durations([sample.value for sample in samples]),
        "full_viewport_count": sum(full_flags),
        "full_viewport_after_first_count": sum(after_first_full_flags),
        "other_full_viewport_count": sum(other_full_flags),
        "other_full_viewport_after_first_count": sum(after_first_other_full_flags),
        "samples": [
            {
                "elapsed_ms": round(float(sample.value), 3),
                "reason": str(sample.tags.get("reason", "")),
                "delivered_full_viewport": str(sample.tags.get("delivered_full_viewport", "")),
                "delivery_kind": str(sample.tags.get("delivery_kind", "")),
                "paint_event_spontaneous": str(sample.tags.get("paint_event_spontaneous", "")),
                "workspace_load_reason": str(sample.tags.get("workspace_load_reason", "")),
                "structural_reason": str(sample.tags.get("structural_reason", "")),
                "pending_reasons": str(sample.tags.get("pending_reasons", "")),
                "changed_rows": str(sample.tags.get("changed_rows", "")),
                "changed_indexes": str(sample.tags.get("changed_indexes", "")),
                "model_rows": str(sample.tags.get("model_rows", "")),
            }
            for sample in samples
        ],
        "after_first_count": len(after_first),
    }


def _summarize_paint_delay_metrics(delay_samples: list) -> dict:
    samples = list(delay_samples or ())
    return {
        "count": len(samples),
        "durations": summarize_durations([sample.value for sample in samples]),
        "samples": [
            {
                "delay_ms": round(float(sample.value), 3),
                "reason": str(sample.tags.get("reason", "")),
                "structural_reason": str(sample.tags.get("structural_reason", "")),
                "pending_reasons": str(sample.tags.get("pending_reasons", "")),
                "changed_rows": str(sample.tags.get("changed_rows", "")),
                "changed_indexes": str(sample.tags.get("changed_indexes", "")),
                "model_rows": str(sample.tags.get("model_rows", "")),
            }
            for sample in samples
        ],
    }


def _summarize_membership_reconcile_metrics(samples: list) -> dict:
    rows = list(samples or ())
    return {
        "count": len(rows),
        "modes": dict(
            sorted(
                (
                    mode,
                    sum(
                        str((getattr(sample, "tags", {}) or {}).get("mode", "")) == mode
                        for sample in rows
                    ),
                )
                for mode in {
                    str((getattr(sample, "tags", {}) or {}).get("mode", "")) for sample in rows
                }
            )
        ),
        "samples": [
            {
                "mode": str(sample.tags.get("mode", "")),
                "old_rows": str(sample.tags.get("old_rows", "")),
                "new_rows": str(sample.tags.get("new_rows", "")),
                "source": str(sample.tags.get("source", "")),
            }
            for sample in rows
        ],
    }


def _summarize_shell_nav_guard_metrics(guard_samples: list) -> dict:
    """Summarize the Watchlist-only shell-nav paint guard's phase-local decisions."""
    samples = list(guard_samples or ())
    decision_counts: dict[str, int] = defaultdict(int)
    fallback_reason_counts: dict[str, int] = defaultdict(int)
    normalized_samples = []
    for sample in samples:
        tags = getattr(sample, "tags", {}) or {}
        decision = str(tags.get("decision", "") or "").strip() or "unspecified"
        fallback_reason = str(tags.get("fallback_reason", "") or "").strip()
        decision_counts[decision] += 1
        if fallback_reason:
            fallback_reason_counts[fallback_reason] += 1
        normalized_samples.append(
            {
                "decision": decision,
                "workspace_load_reason": str(tags.get("workspace_load_reason", "") or ""),
                "age_ms": str(tags.get("age_ms", "") or ""),
                "remaining": str(tags.get("remaining", "") or ""),
                "suppressed": str(tags.get("suppressed", "") or ""),
                "dirty_bounding_area_ratio": str(
                    tags.get("dirty_bounding_area_ratio", "") or ""
                ),
                "dirty_region_rects": str(tags.get("dirty_region_rects", "") or ""),
                "fallback_reason": fallback_reason,
            }
        )
    return {
        "count": len(samples),
        "decision_counts": dict(sorted(decision_counts.items())),
        "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
        "samples": normalized_samples,
    }


def _summarize_named_runtime_spans(samples: list, *, names: tuple[str, ...]) -> dict:
    requested_names = tuple(str(name) for name in names)
    grouped: dict[str, list] = {name: [] for name in requested_names}
    for sample in samples or ():
        method = str((getattr(sample, "tags", {}) or {}).get("method", ""))
        if method in grouped:
            grouped[method].append(sample)
    return {
        "count": sum(len(rows) for rows in grouped.values()),
        "methods": {
            name: {
                "durations": summarize_durations([sample.value for sample in rows]),
                "samples": [
                    {
                        "elapsed_ms": round(float(sample.value), 3),
                        "tab": str((getattr(sample, "tags", {}) or {}).get("tab", "")),
                        "signal": str((getattr(sample, "tags", {}) or {}).get("signal", "")),
                    }
                    for sample in rows
                ],
            }
            for name, rows in grouped.items()
        },
    }


def _summarize_residual_repaint_metrics(samples_by_name: dict[str, list]) -> dict:
    paint_samples = list(samples_by_name.get("watchlist_table_paint_ms", ()))
    paint_ratios = [
        float(sample.tags.get("dirty_bounding_area_ratio", 0.0) or 0.0)
        for sample in paint_samples
    ]
    after_first_paint_samples = paint_samples[1:]
    after_first_paint_ratios = paint_ratios[1:]
    paint_full_flags = [_sample_delivered_full_viewport(sample) for sample in paint_samples]
    after_first_paint_full_flags = paint_full_flags[1:]
    paint_other_full_flags = [
        full and str((getattr(sample, "tags", {}) or {}).get("reason", "")).strip() == "other"
        for sample, full in zip(paint_samples, paint_full_flags, strict=True)
    ]
    after_first_paint_other_full_flags = paint_other_full_flags[1:]
    snapshot_samples = list(samples_by_name.get("tab_transition_snapshot_ms", ()))
    skipped_samples = list(samples_by_name.get("tab_transition_snapshot_skipped", ()))
    model_samples = list(samples_by_name.get("watchlist_model_update_ms", ()))
    return {
        "model_updates": [
            {
                "elapsed_ms": round(float(sample.value), 3),
                "changed_headers": sample.tags.get("changed_headers", ""),
                "mode": sample.tags.get("mode", ""),
                "reason": sample.tags.get("reason", ""),
            }
            for sample in model_samples
        ],
        "paint": {
            "durations": summarize_durations([sample.value for sample in paint_samples]),
            "full_viewport_count": sum(paint_full_flags),
            "other_full_viewport_count": sum(paint_other_full_flags),
            "max_dirty_bounding_area_ratio": round(max(paint_ratios, default=0.0), 4),
            "reasons": [str(sample.tags.get("reason", "")) for sample in paint_samples],
            "samples": [
                {
                    "elapsed_ms": round(float(sample.value), 3),
                    "reason": str(sample.tags.get("reason", "")),
                    "requested_dirty_bounding_area_ratio": sample.tags.get(
                        "requested_dirty_bounding_area_ratio"
                    ),
                    "delivered_dirty_bounding_area_ratio": sample.tags.get(
                        "delivered_dirty_bounding_area_ratio",
                        sample.tags.get("dirty_bounding_area_ratio", ""),
                    ),
                    "targeted_request_reason": str(sample.tags.get("targeted_request_reason", "")),
                    "region_expanded": str(sample.tags.get("region_expanded", "false")),
                    "delivery_kind": str(sample.tags.get("delivery_kind", "")),
                    "delivered_full_viewport": str(
                        sample.tags.get("delivered_full_viewport", "")
                    ),
                    "pending_reasons": str(sample.tags.get("pending_reasons", "")),
                    "structural_reason": str(sample.tags.get("structural_reason", "")),
                    "targeted_request_coalesced_with_structural": str(
                        sample.tags.get("targeted_request_coalesced_with_structural", "")
                    ),
                    "paint_event_spontaneous": str(
                        sample.tags.get("paint_event_spontaneous", "")
                    ),
                    "background_prewarm_active_key_at_paint": str(
                        sample.tags.get("background_prewarm_active_key_at_paint", "")
                    ),
                    "preload_staged": str(sample.tags.get("preload_staged", "")),
                    "background_preload_ready": str(sample.tags.get("background_preload_ready", "")),
                }
                for sample in paint_samples
            ],
            "first": (
                {
                    "elapsed_ms": round(float(paint_samples[0].value), 3),
                    "dirty_bounding_area_ratio": round(paint_ratios[0], 4),
                    "reason": str(paint_samples[0].tags.get("reason", "")),
                    "delivered_full_viewport": paint_full_flags[0],
                    "delivery_kind": str(paint_samples[0].tags.get("delivery_kind", "")),
                }
                if paint_samples
                else None
            ),
            "after_first": {
                "durations": summarize_durations([sample.value for sample in after_first_paint_samples]),
                "full_viewport_count": sum(after_first_paint_full_flags),
                "other_full_viewport_count": sum(after_first_paint_other_full_flags),
                "max_dirty_bounding_area_ratio": round(max(after_first_paint_ratios, default=0.0), 4),
            },
        },
        "snapshot": {
            "capture_count": len(snapshot_samples),
            "durations": summarize_durations([sample.value for sample in snapshot_samples]),
            "skipped_count": len(skipped_samples),
            "skipped_pairs": [dict(sample.tags) for sample in skipped_samples],
        },
    }


def _background_prewarm_acceptance(
    status: dict,
    paint_region: dict,
    *,
    tab_count: int | None = None,
    mounted_keys: list[str] | None = None,
    staged_keys: list[str] | None = None,
    lazy_keys: list[str] | None = None,
) -> dict:
    violations: list[str] = []
    planned_count = int(status.get("planned_count", 0) or 0)
    planned_order = [str(key or "") for key in status.get("planned_order", ()) or ()]
    start_order = [str(key or "") for key in status.get("start_order", ()) or ()]
    completion_order = [str(key or "") for key in status.get("completion_order", ()) or ()]
    handoff_keys = [
        str(key or "") for key in status.get("startup_lazy_handoff_keys", ()) or ()
    ]
    expected_handoff = planned_order[1:] if planned_order[:1] == ["watchlist"] else []
    if not bool(status.get("finished")):
        violations.append("prewarm_not_finished")
    if planned_count <= 0 or len(planned_order) != planned_count:
        violations.append(f"planned={len(planned_order)}/{planned_count}")
    if start_order != ["watchlist"]:
        violations.append(f"start_order={start_order}")
    if completion_order != ["watchlist"]:
        violations.append(f"completion_order={completion_order}")
    if handoff_keys != expected_handoff:
        violations.append(
            f"startup_lazy_handoff_keys={handoff_keys} expected={expected_handoff}"
        )
    if str(status.get("completion_scope") or "") != "visible_watchlist_ready":
        violations.append(f"completion_scope={status.get('completion_scope')!r}")
    if status.get("failures"):
        violations.append(f"failures={sorted(status['failures'])}")
    if tab_count is not None and int(tab_count) != planned_count:
        violations.append(f"tab_count={int(tab_count)} expected={planned_count}")
    if mounted_keys is not None and list(mounted_keys) != ["watchlist"]:
        violations.append(f"mounted_keys={list(mounted_keys)}")
    if staged_keys is not None and list(staged_keys):
        violations.append(f"staged_keys={list(staged_keys)} expected=[]")
    if lazy_keys is not None and list(lazy_keys) != handoff_keys:
        violations.append(f"lazy_keys={list(lazy_keys)} expected={handoff_keys}")
    full_viewport_count = int(paint_region.get("full_viewport_count", 0) or 0)
    if full_viewport_count:
        violations.append(f"watchlist_full_viewport_during_hidden_prewarm={full_viewport_count}")
    return {"status": "pass" if not violations else "fail", "violations": violations}


def _watchlist_reveal_acceptance(paint_metrics: dict, *, viewport_background: dict | None = None) -> dict:
    """Inspect actual VCPTableView paint metrics, not raw QPaintEvent observations."""
    violations: list[str] = []
    paint_count = int((paint_metrics.get("durations") or {}).get("count", 0) or 0)
    if paint_count <= 0:
        violations.append("watchlist_reveal_paint_missing")
    first_paint = paint_metrics.get("first") or {}
    if paint_count > 0 and not bool(first_paint.get("delivered_full_viewport")):
        violations.append("watchlist_first_reveal_not_full_viewport")
    elif paint_count > 0 and str(first_paint.get("reason", "")).strip() != "preload_reveal":
        violations.append(
            f"watchlist_first_reveal_reason={str(first_paint.get('reason', '')).strip()!r}"
        )
    after_first = paint_metrics.get("after_first", {}) or {}
    full_after_first = int(after_first.get("full_viewport_count", 0) or 0)
    if full_after_first:
        violations.append(f"watchlist_full_viewport_after_reveal={full_after_first}")
    other_full_after_first = int(after_first.get("other_full_viewport_count", 0) or 0)
    if other_full_after_first:
        violations.append(
            f"watchlist_other_full_viewport_after_reveal={other_full_after_first}"
        )
    if viewport_background and bool(viewport_background.get("available")):
        if not bool(viewport_background.get("auto_fill_background")):
            violations.append("watchlist_viewport_base_background_disabled")
        if str(viewport_background.get("background_role", "")) != "Base":
            violations.append(
                "watchlist_viewport_background_role="
                f"{str(viewport_background.get('background_role', ''))!r}"
            )
    return {"status": "pass" if not violations else "fail", "violations": violations}


def _shell_nav_repaint_acceptance(results: list[dict], *, expected_cycles: int | None = None) -> dict:
    if not results:
        if expected_cycles is not None and int(expected_cycles) > 0:
            return {"status": "fail", "violations": ["shell_nav_cycles_missing"]}
        return {"status": "not_run", "violations": []}

    violations: list[str] = []
    expected_count = None if expected_cycles is None else max(0, int(expected_cycles))
    if expected_count is not None:
        observed_cycles = [int(result.get("cycle", -1) or -1) for result in results]
        for cycle in range(1, expected_count + 1):
            if observed_cycles.count(cycle) != 1:
                violations.append(f"cycle={cycle} result_count={observed_cycles.count(cycle)}")
        if len(results) != expected_count:
            violations.append(f"result_count={len(results)} expected={expected_count}")

    for result in results:
        cycle = result.get("cycle")
        paint_region = result.get("paint_region", {}) or {}
        paint_metrics = result.get("paint_metrics", {}) or {}
        stalls = result.get("ui_stall_snapshot", {}) or {}
        expected_tab_count = result.get("expected_tab_count")
        if expected_tab_count is not None and int(result.get("tab_count", -1) or -1) != int(expected_tab_count):
            violations.append(
                f"cycle={cycle} tab_count={int(result.get('tab_count', -1) or -1)} expected={int(expected_tab_count)}"
            )
        # The QApplication event filter records incoming QPaintEvents before
        # VCPTableView.viewportEvent can consume one. Keep paint_region in the
        # report as a native invalidation diagnostic, but judge the regression
        # only from the table's actual paintEvent metric below.
        if int(paint_metrics.get("count", 0) or 0) < 1:
            violations.append(f"cycle={cycle} actual_paint_metric_missing")
        if int(paint_metrics.get("full_viewport_count", 0) or 0) > 1:
            violations.append(f"cycle={cycle} actual_full_viewport_metric_budget")
        if int(paint_metrics.get("full_viewport_after_first_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} actual_full_viewport_after_first")
        if int(paint_metrics.get("other_full_viewport_count", 0) or 0) > 1:
            violations.append(f"cycle={cycle} other_full_viewport_metric_budget")
        if int(paint_metrics.get("other_full_viewport_after_first_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} other_full_viewport_after_first")
        if not bool(stalls.get("installed")):
            violations.append(f"cycle={cycle} stall_probe_missing")
        elif int(stalls.get("event_loop_critical_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} event_loop_critical_stall")
    return {"status": "pass" if not violations else "fail", "violations": violations}


def _residual_repaint_acceptance(results: list[dict], *, expected_cycles: int | None = None) -> dict:
    if not results:
        if expected_cycles is not None and int(expected_cycles) > 0:
            return {"status": "fail", "violations": ["residual_actions_missing"]}
        return {"status": "not_run", "violations": []}

    violations = []
    expected_count = None if expected_cycles is None else max(0, int(expected_cycles))
    if expected_count is not None:
        observed_counts: dict[tuple[int, str], int] = defaultdict(int)
        for result in results:
            try:
                result_cycle = int(result.get("cycle"))
            except (TypeError, ValueError):
                result_cycle = -1
            observed_counts[(result_cycle, str(result.get("action", "")))] += 1
        expected_actions = (
            "name_refresh",
            "watchlist_to_lhb",
            "lhb_to_watchlist",
        )
        for cycle in range(1, expected_count + 1):
            for action in expected_actions:
                count = observed_counts.get((cycle, action), 0)
                if count != 1:
                    violations.append(f"cycle={cycle} action={action} result_count={count}")
        expected_result_count = expected_count * len(expected_actions)
        if len(results) != expected_result_count:
            violations.append(f"result_count={len(results)} expected={expected_result_count}")

    for result in results:
        cycle = result.get("cycle")
        action = str(result.get("action", ""))
        metrics = result.get("metrics", {})
        paint = metrics.get("paint", {})
        paint_region = result.get("paint_region", {})
        snapshot = metrics.get("snapshot", {})
        visible_return = action == "lhb_to_watchlist"
        if visible_return:
            paint_full_count = int(paint.get("full_viewport_count", 0) or 0)
            region_full_count = int(paint_region.get("full_viewport_count", 0) or 0)
            if paint_full_count < 1 or paint_full_count > 2:
                violations.append(f"cycle={cycle} action={action} full_viewport_paint_budget")
            if region_full_count < 1 or region_full_count > 2:
                violations.append(f"cycle={cycle} action={action} full_viewport_region_budget")
            first_paint = paint.get("first") or {}
            if first_paint.get("reason") != "native_profile_tab_return":
                violations.append(f"cycle={cycle} action={action} first_paint_reason")
        else:
            if int(paint.get("full_viewport_count", 0) or 0) != 0:
                violations.append(f"cycle={cycle} action={action} full_viewport_paint")
            if int(paint_region.get("full_viewport_count", 0) or 0) != 0:
                violations.append(f"cycle={cycle} action={action} full_viewport_region")

        heartbeat = result.get("heartbeat_lateness", {})
        if int(heartbeat.get("count", 0) or 0) < 1:
            violations.append(f"cycle={cycle} action={action} heartbeat_missing")
        heartbeat_limit_ms = 100.0 if visible_return else 50.0
        if float(heartbeat.get("max_ms", 0.0) or 0.0) >= heartbeat_limit_ms:
            violations.append(f"cycle={cycle} action={action} heartbeat_stall")
        stall_snapshot = result.get("ui_stall_snapshot", {})
        if not bool(stall_snapshot.get("installed")):
            violations.append(f"cycle={cycle} action={action} stall_probe_missing")
        elif visible_return and int(stall_snapshot.get("critical_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} action={action} ui_critical_stall_recorded")
        elif not visible_return and int(stall_snapshot.get("total_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} action={action} ui_stall_recorded")

        if action == "name_refresh":
            if not bool(result.get("changed")):
                violations.append(f"cycle={cycle} action={action} update_missing")
            if int(result.get("proxy_layout_changed_count", 0) or 0) != 0:
                violations.append(f"cycle={cycle} action={action} proxy_layout_changed")
            model_updates = list(metrics.get("model_updates", ()))
            if not model_updates or any(sample.get("mode") != "direct" for sample in model_updates):
                violations.append(f"cycle={cycle} action={action} non_direct_model_update")
            if int(paint.get("durations", {}).get("count", 0) or 0) < 1:
                violations.append(f"cycle={cycle} action={action} paint_metric_missing")
            if int(paint_region.get("count", 0) or 0) < 1:
                violations.append(f"cycle={cycle} action={action} paint_region_missing")
        elif action in {"watchlist_to_lhb", "lhb_to_watchlist"}:
            if int(snapshot.get("capture_count", 0) or 0) != 0:
                violations.append(f"cycle={cycle} action={action} snapshot_captured")
            if int(snapshot.get("skipped_count", 0) or 0) < 1:
                violations.append(f"cycle={cycle} action={action} snapshot_skip_missing")
            expected_pair = (
                ("watchlist", "lhb")
                if action == "watchlist_to_lhb"
                else ("lhb", "watchlist")
            )
            skipped_pairs = list(snapshot.get("skipped_pairs", ()))
            if not any(
                (str(pair.get("source", "")), str(pair.get("target", ""))) == expected_pair
                for pair in skipped_pairs
                if isinstance(pair, dict)
            ):
                violations.append(f"cycle={cycle} action={action} snapshot_skip_pair_mismatch")
        else:
            violations.append(f"cycle={cycle} action={action} unexpected_action")
    return {"status": "pass" if not violations else "fail", "violations": violations}


def _quote_repaint_acceptance(results: list[dict], *, expected_cycles: int | None = None) -> dict:
    expected_count = None if expected_cycles is None else max(0, int(expected_cycles))
    if not results:
        if expected_count:
            return {"status": "fail", "violations": ["quote_cycles_missing"]}
        return {"status": "not_run", "violations": []}

    violations = []
    if expected_count is not None:
        observed_cycles = [int(result.get("cycle", -1) or -1) for result in results]
        for cycle in range(1, expected_count + 1):
            if observed_cycles.count(cycle) != 1:
                violations.append(f"cycle={cycle} result_count={observed_cycles.count(cycle)}")
        if len(results) != expected_count:
            violations.append(f"result_count={len(results)} expected={expected_count}")

    for result in results:
        cycle = result.get("cycle")
        metrics = result.get("metrics", {})
        paint = metrics.get("paint", {})
        paint_region = result.get("paint_region", {})
        model_updates = list(metrics.get("model_updates", ()))
        if int(result.get("payload_size", 0) or 0) < 1:
            violations.append(f"cycle={cycle} payload_missing")
        if int(result.get("proxy_layout_changed_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} proxy_layout_changed")
        if not model_updates:
            violations.append(f"cycle={cycle} model_update_missing")
        elif any(
            sample.get("reason") != "quote_snapshot" or sample.get("mode") != "direct"
            for sample in model_updates
        ):
            violations.append(f"cycle={cycle} non_direct_model_update")
        if int(paint.get("full_viewport_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} full_viewport_paint")
        if int(paint_region.get("full_viewport_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} full_viewport_region")
        reasons = set(paint.get("reasons", ()))
        for reason in ("quote_data_changed", "flash_expiry"):
            if reason not in reasons:
                violations.append(f"cycle={cycle} paint_reason_missing={reason}")
        heartbeat = result.get("heartbeat_lateness", {})
        if int(heartbeat.get("count", 0) or 0) < 1:
            violations.append(f"cycle={cycle} heartbeat_missing")
        if float(heartbeat.get("max_ms", 0.0) or 0.0) >= 50.0:
            violations.append(f"cycle={cycle} heartbeat_stall")
        stall_snapshot = result.get("ui_stall_snapshot", {})
        if not bool(stall_snapshot.get("installed")):
            violations.append(f"cycle={cycle} stall_probe_missing")
        elif int(stall_snapshot.get("total_count", 0) or 0) != 0:
            violations.append(f"cycle={cycle} ui_stall_recorded")
    return {"status": "pass" if not violations else "fail", "violations": violations}


def _prepare_profile_database(source: Path, target: Path) -> dict:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"profile source database not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
    _register_profile_database_cleanup(target)
    return {
        "mode": "sqlite_backup_copy",
        "source": str(source),
        "target": str(target),
        "source_size_bytes": source.stat().st_size,
        "target_size_bytes": target.stat().st_size,
        "cleanup_on_process_exit": True,
    }


def _register_profile_database_cleanup(target: Path) -> None:
    target_text = str(target.resolve())

    def _cleanup() -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(target_text + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    atexit.register(_cleanup)


def _profile_top_functions(profile_path: Path, *, limit: int = 30) -> list[dict]:
    stats = pstats.Stats(str(profile_path))
    project_root = str(PROJECT_ROOT.resolve()).lower()
    rows: list[dict] = []
    raw_stats = getattr(stats, "stats", {})
    for (filename, line, function), values in raw_stats.items():
        if str(filename).startswith(("<", "~")):
            continue
        normalized = str(Path(filename).resolve()).lower() if not str(filename).startswith("~") else ""
        if normalized and not normalized.startswith(project_root):
            continue
        primitive_calls, total_calls, total_time, cumulative_time, _callers = values
        display_path = filename
        if normalized.startswith(project_root):
            display_path = str(Path(filename).resolve().relative_to(PROJECT_ROOT.resolve()))
        rows.append(
            {
                "function": f"{display_path}:{line}({function})",
                "primitive_calls": primitive_calls,
                "total_calls": total_calls,
                "self_ms": round(total_time * 1000.0, 3),
                "cumulative_ms": round(cumulative_time * 1000.0, 3),
            }
        )
    rows.sort(key=lambda row: (row["cumulative_ms"], row["self_ms"]), reverse=True)
    return rows[: max(1, int(limit))]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile the first Watchlist render with the native Windows Qt event dispatcher."
    )
    parser.add_argument("--source-db", type=Path, default=PROJECT_ROOT / "data" / "vcp_hunter.db")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--warmup-ms", type=int, default=500)
    parser.add_argument("--settle-ms", type=int, default=3500)
    parser.add_argument("--load-timeout-ms", type=int, default=8000)
    parser.add_argument("--heartbeat-ms", type=int, default=25)
    parser.add_argument(
        "--background-prewarm",
        action="store_true",
        help="Run the production 11-tab background prewarm and measure Watchlist repaints until it finishes.",
    )
    parser.add_argument(
        "--restore-last-tab",
        action="store_true",
        help="Activate Watchlist through the production restore_last_tab reason instead of a user click.",
    )
    parser.add_argument("--prewarm-timeout-ms", type=int, default=60_000)
    parser.add_argument(
        "--quote-cycles",
        type=int,
        default=0,
        help="After Watchlist settles, publish this many synthetic quote cycles through sig_rt_quotes.",
    )
    parser.add_argument("--quote-cycle-ms", type=int, default=1000)
    parser.add_argument("--quote-target-count", type=int, default=6)
    parser.add_argument(
        "--shell-nav-cycles",
        type=int,
        default=0,
        help="After Watchlist settles, return to it through the production shell navigation this many times.",
    )
    parser.add_argument(
        "--shell-nav-settle-ms",
        type=int,
        default=1200,
        help="Collection window after each production shell-navigation return.",
    )
    parser.add_argument(
        "--shell-nav-only",
        action="store_true",
        help=(
            "For a shell-navigation regression run, keep the pre-navigation "
            "initial-reveal gate diagnostic-only. Requires --shell-nav-cycles > 0."
        ),
    )
    parser.add_argument(
        "--membership-delta-probe",
        action="store_true",
        help=(
            "After Watchlist settles, remove and restore one existing member in the isolated profile "
            "to record source/proxy signals and reset-to-paint timing."
        ),
    )
    parser.add_argument(
        "--disable-market-pulse",
        action="store_true",
        help="For isolated provenance comparison only, stop the titlebar MarketPulseStrip timer after the window is shown.",
    )
    parser.add_argument(
        "--question-dialog-ms",
        type=int,
        default=0,
        help="Hold the production themed question dialog open for this many milliseconds and measure parent repaint.",
    )
    parser.add_argument(
        "--residual-repaint-cycles",
        type=int,
        default=0,
        help="After quote cycles, probe name refresh and both Watchlist snapshot directions this many times.",
    )
    parser.add_argument(
        "--legacy-quote-repaint",
        action="store_true",
        help="Re-enable the pre-fix Watchlist sparse coalescing and full-viewport flash repaint for comparison.",
    )
    parser.add_argument("--top-functions", type=int, default=30)
    parser.add_argument("--no-cprofile", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.shell_nav_only) and max(0, int(args.shell_nav_cycles)) <= 0:
        parser.error("--shell-nav-only requires --shell-nav-cycles > 0")
    return args


def _coerce_probe_price(value, default: float = 10.0) -> float:
    try:
        price = float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        price = 0.0
    return price if price > 0 else float(default)


def _build_synthetic_quote_payload(rows, *, cycle: int, target_count: int = 6) -> dict[str, dict]:
    coded_rows = [row for row in rows or () if isinstance(row, dict) and str(row.get("代码", "")).strip()]
    count = min(len(coded_rows), max(1, int(target_count)))
    if not count:
        return {}
    if count == 1:
        selected = [coded_rows[0]]
    else:
        last = len(coded_rows) - 1
        indexes = [round(position * last / (count - 1)) for position in range(count)]
        selected = [coded_rows[index] for index in dict.fromkeys(indexes)]

    payload: dict[str, dict] = {}
    bump = 0.137 * (max(0, int(cycle)) + 1)
    for row in selected:
        code = str(row.get("代码", "")).strip()
        base = _coerce_probe_price(row.get("现价") or row.get("市价") or row.get("_rt_close"))
        close = round(base + bump, 3)
        payload[code] = {
            "code": code,
            "open": base,
            "pre_close": base,
            "close": close,
            "price": close,
        }
    return payload


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return PROJECT_ROOT / "tmp" / "native_watchlist_profile" / stamp


def _configure_isolated_runtime(output_dir: Path, source_db: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_info = _prepare_profile_database(source_db, output_dir / "profile.db")
    os.environ["VCP_HUNTER_DB_PATH"] = database_info["target"]
    os.environ["VCP_HUNTER_LOG_DIR"] = str((output_dir / "logs").resolve())
    identity = f"NativeWatchlistProfile_{os.getpid()}_{time.time_ns()}"
    os.environ["VCP_HUNTER_SETTINGS_ORGANIZATION"] = "VCPHunterDiagnostics"
    os.environ["VCP_HUNTER_SETTINGS_APPLICATION"] = identity
    return database_info


def _event_dispatcher_summary(segments: list[dict]) -> dict:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for segment in segments:
        grouped[str(segment["phase"])][str(segment["kind"])].append(float(segment["elapsed_ms"]))
    phases = {
        phase: {
            "active_dispatch": summarize_durations(kinds.get("active_dispatch", [])),
            "blocked_wait": summarize_durations(kinds.get("blocked_wait", [])),
        }
        for phase, kinds in sorted(grouped.items())
    }
    active = sorted(
        (segment for segment in segments if segment["kind"] == "active_dispatch"),
        key=lambda segment: float(segment["elapsed_ms"]),
        reverse=True,
    )
    blocked = sorted(
        (segment for segment in segments if segment["kind"] == "blocked_wait"),
        key=lambda segment: float(segment["elapsed_ms"]),
        reverse=True,
    )
    return {
        "interpretation": {
            "active_dispatch": "time between dispatcher awake and aboutToBlock; long spans indicate event handling work",
            "blocked_wait": "time spent in the native dispatcher wait; this is sleep/wake time, not UI work",
        },
        "phases": phases,
        "largest_active_dispatch_segments": active[:12],
        "largest_blocked_wait_segments": blocked[:12],
    }


def _qt_types():
    from PyQt6.QtCore import QAbstractEventDispatcher, QCoreApplication, QEvent, QObject, Qt, QTimer
    from PyQt6.QtWidgets import QApplication

    return QAbstractEventDispatcher, QCoreApplication, QEvent, QObject, Qt, QTimer, QApplication


class _DispatcherPhaseProbe:
    def __init__(self, dispatcher, *, clock=time.perf_counter):
        self._dispatcher = dispatcher
        self._clock = clock
        self._phase = "bootstrap"
        self._state = ""
        self._state_started = 0.0
        self._state_phase = "bootstrap"
        self._origin = clock()
        self.segments: list[dict] = []
        dispatcher.awake.connect(self._on_awake)
        dispatcher.aboutToBlock.connect(self._on_about_to_block)

    def start(self, phase: str) -> None:
        self._phase = str(phase or "unknown")
        self._state = "active_dispatch"
        self._state_started = self._clock()
        self._state_phase = self._phase

    def set_phase(self, phase: str) -> None:
        now = self._clock()
        self._record_current(now)
        self._phase = str(phase or "unknown")
        if self._state:
            self._state_started = now
            self._state_phase = self._phase

    def finish(self) -> dict:
        self._record_current(self._clock())
        self._state = ""
        try:
            self._dispatcher.awake.disconnect(self._on_awake)
            self._dispatcher.aboutToBlock.disconnect(self._on_about_to_block)
        except (RuntimeError, TypeError):
            pass
        return _event_dispatcher_summary(self.segments)

    def _record_current(self, now: float) -> None:
        if not self._state or self._state_started <= 0:
            return
        elapsed_ms = max(0.0, (now - self._state_started) * 1000.0)
        self.segments.append(
            {
                "kind": self._state,
                "phase": self._state_phase,
                "elapsed_ms": round(elapsed_ms, 3),
                "ended_at_ms": round((now - self._origin) * 1000.0, 3),
            }
        )

    def _transition(self, state: str) -> None:
        now = self._clock()
        self._record_current(now)
        self._state = state
        self._state_started = now
        self._state_phase = self._phase

    def _on_awake(self) -> None:
        self._transition("active_dispatch")

    def _on_about_to_block(self) -> None:
        self._transition("blocked_wait")


class _FirstPaintProbe(QObject):
    def __init__(self, app, window, qevent_type, *, origin: float):
        super().__init__()
        self._app = app
        self._window = window
        self._qevent_type = qevent_type
        self._origin = origin
        self._activation_started = 0.0
        self._phase = "startup_idle"
        self._watchlist_viewport = None
        self._watchlist_update_targets: dict[int, str] = {}
        self._paint_regions_by_phase: dict[str, list[dict]] = defaultdict(list)
        self._viewport_update_requests_by_phase: dict[str, list[dict]] = defaultdict(list)
        self.events: dict[str, float] = {}
        app.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API naming
        event_type = event.type()
        update_target = self._watchlist_update_targets.get(id(watched), "")
        if watched is self._watchlist_viewport:
            if event_type == self._qevent_type.Paint:
                self._record_paint_region(watched, event)
        if update_target and event_type in {
            self._qevent_type.UpdateRequest,
            self._qevent_type.UpdateLater,
        }:
            self._record_viewport_update_request(event_type, target=update_target)
        if watched is self._window:
            self._record_widget_event("window", event_type)
        elif watched.__class__.__name__ == "WatchlistTab":
            self._record_widget_event("watchlist", event_type)
            if event_type == self._qevent_type.Show:
                table = getattr(watched, "table_sp", None)
                if table is not None:
                    self.attach_watchlist_table(table)
        return False

    def attach_watchlist_table(self, table) -> None:
        viewport = getattr(table, "viewport", None)
        self._watchlist_viewport = viewport() if callable(viewport) else None
        self._watchlist_update_targets.clear()
        if self._watchlist_viewport is not None:
            self._watchlist_update_targets[id(self._watchlist_viewport)] = "viewport"
        widget = table
        depth = 0
        while widget is not None:
            label = "table" if depth == 0 else f"ancestor_{depth}:{type(widget).__name__}"
            self._watchlist_update_targets[id(widget)] = label
            if widget is self._window:
                break
            parent_widget = getattr(widget, "parentWidget", None)
            widget = parent_widget() if callable(parent_widget) else None
            depth += 1

    def set_phase(self, phase: str) -> None:
        self._phase = str(phase or "unknown")

    def paint_region_summary(self, phase: str) -> dict:
        samples = list(self._paint_regions_by_phase.get(str(phase or "unknown"), ()))
        return self._summarize_paint_regions(samples)

    def paint_region_summary_prefix(self, prefix: str) -> dict:
        normalized = str(prefix or "")
        samples = [
            sample
            for phase, phase_samples in self._paint_regions_by_phase.items()
            if phase.startswith(normalized)
            for sample in phase_samples
        ]
        return self._summarize_paint_regions(samples)

    def viewport_update_request_summary(self, phase: str) -> dict:
        samples = list(self._viewport_update_requests_by_phase.get(str(phase or "unknown"), ()))
        return self._summarize_viewport_update_requests(samples)

    @staticmethod
    def _summarize_viewport_update_requests(samples: list[dict]) -> dict:
        normalized_samples = [dict(sample) for sample in samples]
        event_type_counts: dict[str, int] = defaultdict(int)
        target_counts: dict[str, int] = defaultdict(int)
        upstream_target_counts: dict[str, int] = defaultdict(int)
        upstream_update_request_count = 0
        upstream_update_later_count = 0
        for sample in normalized_samples:
            event_type = str(sample.get("event_type", ""))
            target = str(sample.get("target", ""))
            event_type_counts[event_type] += 1
            target_counts[target] += 1
            if target not in {"viewport", "table"}:
                upstream_target_counts[target] += 1
                if event_type == "UpdateRequest":
                    upstream_update_request_count += 1
                elif event_type == "UpdateLater":
                    upstream_update_later_count += 1
        return {
            "count": len(normalized_samples),
            "event_type_counts": dict(sorted(event_type_counts.items())),
            "target_counts": dict(sorted(target_counts.items())),
            "upstream_target_counts": dict(sorted(upstream_target_counts.items())),
            "backing_store_update_request_count": upstream_update_request_count,
            "upstream_update_later_count": upstream_update_later_count,
            "samples": normalized_samples,
        }

    @staticmethod
    def _summarize_paint_regions(samples: list[dict]) -> dict:
        ratios = [float(sample.get("dirty_bounding_area_ratio", 0.0) or 0.0) for sample in samples]
        full_flags = [bool(sample.get("delivered_full_viewport", False)) for sample in samples]
        spontaneous_flags = [bool(sample.get("paint_event_spontaneous", False)) for sample in samples]
        after_first_ratios = ratios[1:]
        after_first_full_flags = full_flags[1:]
        return {
            "count": len(samples),
            "full_viewport_count": sum(full_flags),
            "spontaneous_count": sum(spontaneous_flags),
            "max_dirty_bounding_area_ratio": round(max(ratios, default=0.0), 4),
            "max_region_rect_count": max(
                (int(sample.get("region_rect_count", 0) or 0) for sample in samples),
                default=0,
            ),
            "first": (
                {
                    "dirty_bounding_area_ratio": round(ratios[0], 4),
                    "region_rect_count": int(samples[0].get("region_rect_count", 0) or 0),
                    "delivered_full_viewport": full_flags[0],
                }
                if samples
                else None
            ),
            "after_first": {
                "count": len(after_first_ratios),
                "full_viewport_count": sum(after_first_full_flags),
                "max_dirty_bounding_area_ratio": round(max(after_first_ratios, default=0.0), 4),
            },
            "samples": [dict(sample) for sample in samples],
        }

    def mark_activation(self, started: float) -> None:
        self._activation_started = started

    def close(self) -> None:
        try:
            self._app.removeEventFilter(self)
        except RuntimeError:
            pass

    def report(self) -> dict:
        result = {key: round(value, 3) for key, value in sorted(self.events.items())}
        if self._activation_started > 0:
            activation_ms = (self._activation_started - self._origin) * 1000.0
            for key in ("watchlist_show_at_ms", "watchlist_first_paint_at_ms"):
                if key in result:
                    result[key.replace("_at_ms", "_after_activation_ms")] = round(result[key] - activation_ms, 3)
        result["watchlist_viewport_by_phase"] = {
            phase: self.paint_region_summary(phase)
            for phase in sorted(self._paint_regions_by_phase)
        }
        return result

    def _record_paint_region(self, watched, event) -> None:
        try:
            viewport_rect = watched.rect()
            dirty_rect = event.region().boundingRect()
            viewport_area = max(1, viewport_rect.width() * viewport_rect.height())
            dirty_area = max(0, dirty_rect.width() * dirty_rect.height())
            ratio = min(1.0, dirty_area / viewport_area)
            rect_count = int(event.region().rectCount())
            delivered_full = bool(
                not viewport_rect.isEmpty()
                and QRegion(viewport_rect).subtracted(event.region()).isEmpty()
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        recorded_at_ms = round((time.perf_counter() - self._origin) * 1000.0, 3)
        last_request = next(
            reversed(self._viewport_update_requests_by_phase.get(self._phase, ())),
            None,
        )
        sample = {
            "dirty_bounding_area_ratio": ratio,
            "region_rect_count": rect_count,
            "delivered_full_viewport": delivered_full,
            "paint_event_spontaneous": bool(event.spontaneous()),
            "background_prewarm_active_key_at_paint": str(
                getattr(getattr(self._window, "_workspace", None), "_background_prewarm_active_key", "")
                or ""
            ),
            "recorded_at_ms": recorded_at_ms,
        }
        if last_request is not None:
            sample["last_update_request_type"] = str(last_request["event_type"])
            sample["last_update_request_target"] = str(last_request.get("target", ""))
            sample["last_update_request_at_ms"] = float(last_request["recorded_at_ms"])
            sample["update_request_to_paint_ms"] = round(
                max(0.0, recorded_at_ms - float(last_request["recorded_at_ms"])),
                3,
            )
        self._paint_regions_by_phase[self._phase].append(sample)

    def _record_viewport_update_request(self, event_type, *, target: str) -> None:
        self._viewport_update_requests_by_phase[self._phase].append(
            {
                "event_type": str(getattr(event_type, "name", event_type)),
                "target": str(target),
                "recorded_at_ms": round((time.perf_counter() - self._origin) * 1000.0, 3),
            }
        )

    def _record_widget_event(self, prefix: str, event_type) -> None:
        if event_type == self._qevent_type.Show:
            key = f"{prefix}_show_at_ms"
        elif event_type == self._qevent_type.Paint:
            key = f"{prefix}_first_paint_at_ms"
        else:
            return
        self.events.setdefault(key, (time.perf_counter() - self._origin) * 1000.0)


def _summarize_model_signal_events(events: list[dict]) -> dict:
    normalized = [dict(event) for event in events or ()]
    counts: dict[str, int] = defaultdict(int)
    for event in normalized:
        counts[f"{event.get('model', 'unknown')}.{event.get('signal', 'unknown')}"] += 1
    return {
        "count": len(normalized),
        "counts": dict(sorted(counts.items())),
        "events": normalized,
    }


def _reset_to_first_paint_delays(events: list[dict], paint_region: dict) -> dict:
    paint_samples = sorted(
        list((paint_region or {}).get("samples", ()) or ()),
        key=lambda sample: float(sample.get("recorded_at_ms", 0.0) or 0.0),
    )
    samples = []
    for event in events or ():
        if str(event.get("signal", "")) != "model_reset":
            continue
        reset_at_ms = float(event.get("recorded_at_ms", 0.0) or 0.0)
        first_paint = next(
            (
                sample
                for sample in paint_samples
                if float(sample.get("recorded_at_ms", 0.0) or 0.0) >= reset_at_ms
            ),
            None,
        )
        samples.append(
            {
                "model": str(event.get("model", "unknown")),
                "reset_at_ms": round(reset_at_ms, 3),
                "first_paint_at_ms": (
                    round(float(first_paint.get("recorded_at_ms", 0.0) or 0.0), 3)
                    if first_paint is not None
                    else None
                ),
                "delay_ms": (
                    round(max(0.0, float(first_paint.get("recorded_at_ms", 0.0) or 0.0) - reset_at_ms), 3)
                    if first_paint is not None
                    else None
                ),
                "first_paint_full_viewport": (
                    bool(first_paint.get("delivered_full_viewport", False)) if first_paint is not None else None
                ),
            }
        )
    return {"count": len(samples), "samples": samples}


class _WatchlistModelSignalProbe:
    _SIGNALS = (
        ("modelAboutToBeReset", "model_about_to_reset"),
        ("modelReset", "model_reset"),
        ("rowsInserted", "rows_inserted"),
        ("rowsRemoved", "rows_removed"),
        ("layoutChanged", "layout_changed"),
    )

    def __init__(self, *, origin: float):
        self._origin = origin
        self._source_model = None
        self._proxy_model = None
        self._connections: list[tuple[object, object]] = []
        self.events: list[dict] = []

    def attach(self, source_model, proxy_model) -> bool:
        if source_model is self._source_model and proxy_model is self._proxy_model:
            return bool(self._connections)
        self.close()
        self._source_model = source_model
        self._proxy_model = proxy_model
        for model_name, model in (("source", source_model), ("proxy", proxy_model)):
            if model is None:
                continue
            for signal_name, normalized_name in self._SIGNALS:
                signal = getattr(model, signal_name, None)
                if signal is None:
                    continue
                slot = self._make_slot(model_name, normalized_name)
                try:
                    signal.connect(slot)
                except (RuntimeError, TypeError):
                    continue
                self._connections.append((signal, slot))
        return bool(self._connections)

    def close(self) -> None:
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()
        self._source_model = None
        self._proxy_model = None

    def offset(self) -> int:
        return len(self.events)

    def events_since(self, offset: int) -> list[dict]:
        return [dict(event) for event in self.events[max(0, int(offset)) :]]

    def _make_slot(self, model_name: str, signal_name: str):
        def slot(*args):
            event = {
                "model": model_name,
                "signal": signal_name,
                "recorded_at_ms": round((time.perf_counter() - self._origin) * 1000.0, 3),
            }
            if signal_name in {"rows_inserted", "rows_removed"} and len(args) >= 3:
                event["first_row"] = int(args[1])
                event["last_row"] = int(args[2])
            self.events.append(event)

        return slot


class _NativeProfileController:
    def __init__(
        self,
        *,
        app,
        window,
        qtimer_type,
        qt_timer_type,
        dispatcher_probe: _DispatcherPhaseProbe,
        paint_probe: _FirstPaintProbe,
        args: argparse.Namespace,
        activation_profile_path: Path,
        settle_profile_path: Path,
        report: dict,
        origin: float,
        cprofile_enabled: bool,
    ):
        self.app = app
        self.window = window
        self.QTimer = qtimer_type
        self.dispatcher_probe = dispatcher_probe
        self.paint_probe = paint_probe
        self.args = args
        self.activation_profile_path = activation_profile_path
        self.settle_profile_path = settle_profile_path
        self.report = report
        self.origin = origin
        self.cprofile_enabled = bool(cprofile_enabled)
        self.activation_profiler = cProfile.Profile()
        self.settle_profiler = cProfile.Profile()
        self._active_profiler: cProfile.Profile | None = None
        self._active_profile_path: Path | None = None
        self._activation_started = 0.0
        self._heartbeat_last = 0.0
        self._heartbeat_by_phase: dict[str, list[float]] = defaultdict(list)
        self._phase = "startup_idle"
        self._done = False
        self._quote_cycle_index = 0
        self._quote_cycle_payload_sizes: list[int] = []
        self._quote_results: list[dict] = []
        self._quote_phase: dict | None = None
        self._shell_nav_cycle_index = 0
        self._shell_nav_results: list[dict] = []
        self._shell_nav_phase: dict | None = None
        self._model_signal_probe = _WatchlistModelSignalProbe(origin=origin)
        self._membership_reconcile_metric_offset = self._metric_offsets(
            ("watchlist_membership_reconcile",)
        )
        self._membership_delta_probe_started = False
        self._membership_delta_phase: dict | None = None
        self._question_dialog_probed = False
        self._question_dialog_phase: dict | None = None
        self._residual_cycle_index = 0
        self._residual_results: list[dict] = []
        self._residual_phase: dict | None = None
        self._residual_isolation_started = False
        self._background_prewarm_started_at = 0.0
        self._background_prewarm_offsets: dict[str, float] | None = None
        self._background_prewarm_first_hidden_key = ""
        self._watchlist_reveal_started_at = 0.0
        self._watchlist_reveal_offsets: dict[str, float] | None = None
        self._watchlist_prewarm_offsets: dict[str, int] | None = None

        workspace = getattr(window, "_workspace", None)
        coordinator = getattr(workspace, "_background_preload_coordinator", None)
        step_started = getattr(coordinator, "stepStarted", None)
        if step_started is not None:
            step_started.connect(self._on_background_prewarm_step_started)

        self._heartbeat = qtimer_type()
        self._heartbeat.setTimerType(qt_timer_type.PreciseTimer)
        self._heartbeat.setInterval(max(5, int(args.heartbeat_ms)))
        self._heartbeat.timeout.connect(self._on_heartbeat)

        self._load_poll = qtimer_type()
        self._load_poll.setTimerType(qt_timer_type.PreciseTimer)
        self._load_poll.setInterval(10)
        self._load_poll.timeout.connect(self._poll_watchlist_loaded)

    def start(self) -> None:
        self.dispatcher_probe.start("startup_idle")
        self._heartbeat_last = time.perf_counter()
        self._heartbeat.start()
        self.QTimer.singleShot(max(0, int(self.args.warmup_ms)), self._activate_watchlist)
        load_timeout_ms = int(self.args.load_timeout_ms)
        if bool(self.args.background_prewarm):
            load_timeout_ms = max(load_timeout_ms, int(self.args.prewarm_timeout_ms))
        total_timeout = max(1000, int(self.args.warmup_ms) + load_timeout_ms)
        self.QTimer.singleShot(total_timeout, self._abort_on_timeout)

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self._heartbeat_last = time.perf_counter()
        self.paint_probe.set_phase(phase)
        self.dispatcher_probe.set_phase(phase)

    def _activate_watchlist(self) -> None:
        if self._done:
            return
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        stall_probe = get_ui_stall_probe()
        if stall_probe is not None:
            stall_probe.reset_stall_snapshot()
        self._set_phase("watchlist_activation")
        self._activation_started = time.perf_counter()
        self.paint_probe.mark_activation(self._activation_started)
        if self.cprofile_enabled:
            self.activation_profiler.enable()
            self._active_profiler = self.activation_profiler
            self._active_profile_path = self.activation_profile_path

        workspace = getattr(self.window, "_workspace", None)
        if workspace is None:
            self._fail("workspace unavailable")
            return
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        index = next((i for i, spec in enumerate(specs) if spec.get("key") == "watchlist"), -1)
        if index < 0:
            self._fail("watchlist tab unavailable")
            return

        if bool(self.args.background_prewarm):
            self._watchlist_reveal_started_at = time.perf_counter()
            self._watchlist_reveal_offsets = self._metric_offsets(WATCHLIST_REVEAL_METRICS)
            self._watchlist_prewarm_offsets = self._metric_offsets(PREWARM_RUNTIME_METRICS)
            self._reset_stall_probe()
            self._set_phase("watchlist_reveal")
        call_started = time.perf_counter()
        if bool(self.args.restore_last_tab):
            schedule_restore = getattr(workspace, "schedule_restore_last_tab", None)
            if callable(schedule_restore):
                schedule_restore(index, delay_ms=0)
                activated = True
            else:
                restore = getattr(workspace, "restore_last_tab", None)
                if callable(restore):
                    restore(index)
                    activated = True
                else:
                    activated = False
        else:
            activated = bool(workspace.activate_tab(index, reason="user"))
        self.report["timings"]["watchlist_activate_call_ms"] = round(
            (time.perf_counter() - call_started) * 1000.0, 3
        )
        if not activated:
            self._fail("watchlist activation rejected")
            return
        self._load_poll.start()

    def _poll_watchlist_loaded(self) -> None:
        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        if tab is None:
            return
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        watchlist_spec = next((spec for spec in specs if spec.get("key") == "watchlist"), {})
        if bool(self.args.background_prewarm) and not bool(watchlist_spec.get("mounted")):
            return
        self._load_poll.stop()
        loaded_ms = (time.perf_counter() - self._activation_started) * 1000.0
        model = getattr(tab, "model", None)
        try:
            row_count = int(model.rowCount()) if model is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            row_count = None
        self.report["timings"]["watchlist_loaded_ms"] = round(loaded_ms, 3)
        self.report["watchlist"] = {
            "row_count": row_count,
            "visible": bool(tab.isVisible()),
            "workspace_load_reason": str(getattr(tab, "_workspace_load_reason", "")),
        }
        staging_host = getattr(workspace, "_background_preload_staging_host", None)
        staging_parent = staging_host.parentWidget() if staging_host is not None else None
        self.report["watchlist"]["preload_staging_host"] = {
            "exists": staging_host is not None,
            "parent_type": type(staging_parent).__name__ if staging_parent is not None else "",
            "is_top_level": bool(staging_host is not None and staging_host.isWindow()),
            "visible": bool(staging_host is not None and staging_host.isVisible()),
        }
        table = getattr(tab, "table_sp", None)
        if table is not None:
            self.paint_probe.attach_watchlist_table(table)
            ambient_timer = getattr(table, "_ambient_repaint_timer", None)
            flash_timer = getattr(table, "_flash_repaint_timer", None)
            viewport = table.viewport()
            self.report["watchlist"]["repaint_runtime"] = {
                "watchlist_page_opaque_paint": bool(
                    tab.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
                ),
                "table_opaque_paint": bool(
                    table.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
                ),
                "viewport_opaque_paint": bool(
                    viewport is not None
                    and viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
                ),
                "watchlist_page_size": [int(tab.width()), int(tab.height())],
                "table_size": [int(table.width()), int(table.height())],
                "viewport_size": (
                    [int(viewport.width()), int(viewport.height())]
                    if viewport is not None
                    else None
                ),
                "copy_hook_installed": bool(getattr(table, "_copy_hook_installed", False)),
                "ambient_pulse_property": bool(table.property("ambientPulse")),
                "ambient_timer_active": bool(ambient_timer is not None and ambient_timer.isActive()),
                "ambient_timer_interval_ms": (
                    int(ambient_timer.interval()) if ambient_timer is not None else None
                ),
                "flash_timer_active": bool(flash_timer is not None and flash_timer.isActive()),
                "flash_timer_interval_ms": int(flash_timer.interval()) if flash_timer is not None else None,
                "coalesced_flash_repaint": bool(getattr(table, "_coalesced_flash_repaint", False)),
                "targeted_flash_repaint": bool(getattr(table, "_targeted_flash_repaint", False)),
                "viewport_background": self._watchlist_viewport_background_snapshot(),
            }
        proxy = getattr(tab, "proxy_model", None)
        self.report["watchlist"]["model_signal_monitor"] = {
            "attached": self._model_signal_probe.attach(model, proxy),
            "source_model": type(model).__name__ if model is not None else "",
            "proxy_model": type(proxy).__name__ if proxy is not None else "",
        }
        headers = list(getattr(model, "headers", None) or [])
        if proxy is not None and headers and max(0, int(self.args.residual_repaint_cycles)) > 0:
            sort_header = "RPS强度" if "RPS强度" in headers else "代码"
            proxy.sort(headers.index(sort_header))
            self.report["watchlist"]["probe_sort_header"] = sort_header
        if bool(self.args.legacy_quote_repaint):
            if model is not None:
                model.set_sparse_update_coalescing(True)
                model.set_sparse_quote_update_coalescing(True)
            if table is not None:
                table.set_targeted_flash_repaint_enabled(False, metric_scope="watchlist")
            self.report["watchlist"]["quote_repaint_mode"] = "legacy_full_viewport"
        else:
            self.report["watchlist"]["quote_repaint_mode"] = "targeted_dirty_region"
        if self.cprofile_enabled:
            self._stop_active_profiler()
            self.settle_profiler.enable()
            self._active_profiler = self.settle_profiler
            self._active_profile_path = self.settle_profile_path
        if not bool(self.args.background_prewarm):
            self._set_phase("watchlist_settle")
        self.QTimer.singleShot(max(0, int(self.args.settle_ms)), self._after_watchlist_settle)

    def _on_background_prewarm_step_started(self, key: str) -> None:
        key_text = str(key or "").strip()
        if (
            self._done
            or not bool(self.args.background_prewarm)
            or key_text == "watchlist"
            or self._background_prewarm_started_at > 0.0
        ):
            return

        started_at = time.perf_counter()
        self._record_watchlist_reveal(started_at)
        self._background_prewarm_first_hidden_key = key_text
        self._background_prewarm_started_at = started_at
        self._background_prewarm_offsets = self._metric_offsets()
        self._reset_stall_probe()
        self._set_phase("background_prewarm")

    def _watchlist_viewport_background_snapshot(self) -> dict:
        workspace = getattr(getattr(self, "window", None), "_workspace", None)
        tab_getter = getattr(workspace, "get_loaded_tab", None)
        tab = tab_getter("watchlist") if callable(tab_getter) else None
        table = getattr(tab, "table_sp", None)
        viewport = table.viewport() if table is not None else None
        if viewport is None:
            return {"available": False}
        role = viewport.backgroundRole()
        return {
            "available": True,
            "auto_fill_background": bool(viewport.autoFillBackground()),
            "background_role": str(getattr(role, "name", role)),
            "viewport_opaque_paint": bool(
                viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
            ),
        }

    def _record_watchlist_reveal(self, completed_at: float) -> None:
        if "watchlist_reveal" in self.report:
            return
        reveal_offsets = self._watchlist_reveal_offsets or self._metric_offsets(
            WATCHLIST_REVEAL_METRICS
        )
        reveal_started_at = self._watchlist_reveal_started_at or completed_at
        reveal_paint_region = self.paint_probe.paint_region_summary("watchlist_reveal")
        viewport_update_requests = getattr(self.paint_probe, "viewport_update_request_summary", None)
        reveal_samples_by_name = self._metrics_since(reveal_offsets)
        reveal_metrics = _summarize_residual_repaint_metrics(reveal_samples_by_name)
        prewarm_offsets = getattr(self, "_watchlist_prewarm_offsets", None)
        prewarm_runtime_samples = (
            self._metrics_since(prewarm_offsets) if prewarm_offsets is not None else {}
        )
        prewarm_runtime_spans = _summarize_named_runtime_spans(
            prewarm_runtime_samples.get("ui_method_stall_ms", []),
            names=(
                "ClassicWorkspace.ensure_tab_loaded",
                "ClassicWorkspace._prewarm_next_tab",
            ),
        )
        viewport_background = self._watchlist_viewport_background_snapshot()
        watchlist_report = self.report.get("watchlist")
        if isinstance(watchlist_report, dict):
            repaint_runtime = watchlist_report.get("repaint_runtime")
            if isinstance(repaint_runtime, dict):
                repaint_runtime["viewport_background"] = viewport_background
        reveal_acceptance = _watchlist_reveal_acceptance(
            reveal_metrics["paint"],
            viewport_background=viewport_background,
        )
        reveal_acceptance_enforced = not bool(getattr(self.args, "shell_nav_only", False))
        self.report["watchlist_reveal"] = {
            "elapsed_ms": round((completed_at - reveal_started_at) * 1000.0, 3),
            "paint_region": reveal_paint_region,
            "backing_store_update_requests": (
                viewport_update_requests("watchlist_reveal")
                if callable(viewport_update_requests)
                else {"count": 0, "samples": []}
            ),
            "metrics": reveal_metrics,
            "viewport_background": viewport_background,
            "prewarm_runtime_spans": prewarm_runtime_spans,
            "event_loop_stalls": summarize_durations(
                [sample.value for sample in reveal_samples_by_name.get("ui_event_loop_stall_ms", [])]
            ),
            "heartbeat_lateness": summarize_durations(
                list(self._heartbeat_by_phase.get("watchlist_reveal", ()))
            ),
            "ui_stall_snapshot": self._stall_snapshot(),
            "acceptance_metric_source": "watchlist_table_paint_ms",
            "acceptance_scope": "actual_vcp_first_preload_reveal_plus_zero_later_full_viewport_paints",
            "acceptance": reveal_acceptance,
            "acceptance_enforced": reveal_acceptance_enforced,
        }
        if reveal_acceptance_enforced and reveal_acceptance["status"] != "pass":
            self.report["errors"].append("watchlist reveal repaint acceptance failed")

    def _after_watchlist_settle(self) -> None:
        if self._done:
            return
        if bool(self.args.background_prewarm):
            self._poll_background_prewarm_finished()
            return
        self._continue_after_background_prewarm()

    def _poll_background_prewarm_finished(self) -> None:
        if self._done:
            return
        workspace = getattr(self.window, "_workspace", None)
        status = workspace.background_preload_status() if workspace is not None else {}
        if self._background_prewarm_started_at <= 0.0 and bool(status.get("finished")):
            terminal_at = time.perf_counter()
            self._record_watchlist_reveal(terminal_at)
            self._background_prewarm_started_at = terminal_at
            self._background_prewarm_offsets = self._metric_offsets()
            self._reset_stall_probe()
            self._set_phase("background_prewarm")
        if self._background_prewarm_started_at <= 0.0:
            wait_started_at = self._watchlist_reveal_started_at or self._activation_started
            elapsed_ms = (time.perf_counter() - wait_started_at) * 1000.0
            if elapsed_ms >= max(1, int(self.args.prewarm_timeout_ms)):
                self.report["background_prewarm"] = {
                    "elapsed_ms": round(elapsed_ms, 3),
                    "status": status,
                    "acceptance": {"status": "fail", "violations": ["hidden_prewarm_not_started"]},
                }
                self._fail("background prewarm did not start hidden tabs")
                return
            self.QTimer.singleShot(50, self._poll_background_prewarm_finished)
            return
        elapsed_ms = (time.perf_counter() - self._background_prewarm_started_at) * 1000.0
        if not bool(status.get("finished")):
            if elapsed_ms >= max(1, int(self.args.prewarm_timeout_ms)):
                self.report["background_prewarm"] = {
                    "elapsed_ms": round(elapsed_ms, 3),
                    "status": status,
                    "acceptance": {"status": "fail", "violations": ["prewarm_timeout"]},
                }
                self._fail("background prewarm timeout")
                return
            self.QTimer.singleShot(50, self._poll_background_prewarm_finished)
            return

        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        tab_count = int(getattr(getattr(workspace, "tabs", None), "count", lambda: 0)())
        specs_by_key = {str(spec.get("key") or ""): spec for spec in specs}
        planned_order = [str(key or "") for key in status.get("planned_order", ()) or ()]
        mounted_keys = [
            key
            for key in planned_order
            if specs_by_key.get(key, {}).get("loaded")
            and specs_by_key.get(key, {}).get("mounted", True)
        ]
        staged_keys = [
            key
            for key in planned_order
            if specs_by_key.get(key, {}).get("loaded")
            and not specs_by_key.get(key, {}).get("mounted", True)
        ]
        lazy_keys = [key for key in planned_order if not specs_by_key.get(key, {}).get("loaded")]
        paint_region = self.paint_probe.paint_region_summary("background_prewarm")
        offsets = self._background_prewarm_offsets or self._metric_offsets()
        prewarm_metrics = _summarize_residual_repaint_metrics(self._metrics_since(offsets))
        prewarm_offsets = getattr(self, "_watchlist_prewarm_offsets", None)
        visible_watchlist_runtime_samples = (
            self._metrics_since(prewarm_offsets) if prewarm_offsets is not None else {}
        )
        visible_watchlist_runtime_spans = _summarize_named_runtime_spans(
            visible_watchlist_runtime_samples.get("ui_method_stall_ms", []),
            names=(
                "ClassicWorkspace.ensure_tab_loaded",
                "ClassicWorkspace._prewarm_next_tab",
            ),
        )
        acceptance = _background_prewarm_acceptance(
            status,
            paint_region,
            tab_count=tab_count,
            mounted_keys=mounted_keys,
            staged_keys=staged_keys,
            lazy_keys=lazy_keys,
        )
        heartbeat_lateness = summarize_durations(
            list(self._heartbeat_by_phase.get("background_prewarm", ()))
        )
        stall_snapshot = self._stall_snapshot()
        event_loop_observation = {
            "status": (
                "stalls_observed"
                if int(stall_snapshot.get("total_count", 0) or 0) > 0
                or float(heartbeat_lateness.get("max_ms", 0.0) or 0.0) >= 50.0
                else "clear"
            ),
            "part_of_repaint_acceptance": False,
            "heartbeat_lateness": heartbeat_lateness,
            "ui_stall_snapshot": stall_snapshot,
        }
        self.report["background_prewarm"] = {
            "elapsed_ms": round(elapsed_ms, 3),
            "first_hidden_key": self._background_prewarm_first_hidden_key,
            "tab_count": tab_count,
            "mounted_keys": mounted_keys,
            "staged_keys": staged_keys,
            "lazy_keys": lazy_keys,
            "completion_scope": str(status.get("completion_scope") or ""),
            "status": status,
            "paint_region": paint_region,
            "metrics": prewarm_metrics,
            "visible_watchlist_runtime_spans": visible_watchlist_runtime_spans,
            "heartbeat_lateness": heartbeat_lateness,
            "ui_stall_snapshot": stall_snapshot,
            "event_loop_observation": event_loop_observation,
            "acceptance_scope": "watchlist_repaint_and_11_tab_startup_lazy_handoff",
            "acceptance": acceptance,
        }
        if acceptance["status"] != "pass":
            self.report["errors"].append("background prewarm repaint acceptance failed")
        self._continue_after_background_prewarm()

    def _resolve_shell_nav_targets(self):
        workspace = getattr(self.window, "_workspace", None)
        nav = getattr(self.window, "_shell_navigation_widget", None)
        switch_group = getattr(nav, "_switch_group", None)
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        watchlist_index = next(
            (index for index, spec in enumerate(specs) if str(spec.get("key") or "") == "watchlist"),
            -1,
        )
        group_to_indices = dict(getattr(nav, "_group_to_indices", {}) or {})
        if not group_to_indices:
            group_to_indices = dict(getattr(workspace, "tab_indices_by_group", lambda: {})() or {})
        watchlist_group = next(
            (
                group
                for group, indices in group_to_indices.items()
                if watchlist_index in list(indices or ())
            ),
            "",
        )
        outbound_group = next(
            (
                group
                for group, indices in group_to_indices.items()
                if group != watchlist_group and list(indices or ())
            ),
            "",
        )
        outbound_indices = list(group_to_indices.get(outbound_group, ()) or ())
        if (
            workspace is None
            or not callable(switch_group)
            or watchlist_index < 0
            or not watchlist_group
            or not outbound_group
            or not outbound_indices
        ):
            return None
        return {
            "workspace": workspace,
            "nav": nav,
            "switch_group": switch_group,
            "watchlist_index": watchlist_index,
            "watchlist_group": watchlist_group,
            "outbound_group": outbound_group,
            "outbound_indices": outbound_indices,
        }

    def _start_shell_nav_cycle(self) -> None:
        if self._done or self._shell_nav_phase is not None:
            return
        targets = self._resolve_shell_nav_targets()
        if targets is None:
            self._fail("production shell navigation unavailable")
            return
        cycle = self._shell_nav_cycle_index + 1
        self._shell_nav_phase = {
            **targets,
            "cycle": cycle,
            "outbound_started_at": time.perf_counter(),
        }
        self._set_phase(f"shell_nav_{cycle}_outbound")
        try:
            targets["switch_group"](targets["outbound_group"])
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self._fail(f"shell navigation outbound failed: {exc}")
            return
        self.QTimer.singleShot(0, self._poll_shell_nav_outbound)

    def _poll_shell_nav_outbound(self) -> None:
        phase = self._shell_nav_phase
        if self._done or phase is None:
            return
        workspace = phase["workspace"]
        tabs = getattr(workspace, "tabs", None)
        current_index = int(getattr(tabs, "currentIndex", lambda: -1)()) if tabs is not None else -1
        if current_index in phase["outbound_indices"]:
            cycle = int(phase["cycle"])
            self._reset_stall_probe()
            phase["return_started_at"] = time.perf_counter()
            phase["metric_offsets"] = self._metric_offsets(SHELL_NAV_REPAINT_METRICS)
            phase["model_signal_offset"] = self._model_signal_probe.offset()
            self._set_phase(f"shell_nav_{cycle}_watchlist_return")
            try:
                phase["switch_group"](
                    phase["watchlist_group"],
                    preferred_index=phase["watchlist_index"],
                )
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                self._fail(f"shell navigation return failed: {exc}")
                return
            self.QTimer.singleShot(
                max(1, int(self.args.shell_nav_settle_ms)),
                self._finish_shell_nav_cycle,
            )
            return
        elapsed_ms = (time.perf_counter() - float(phase["outbound_started_at"])) * 1000.0
        timeout_ms = max(2_000, int(self.args.shell_nav_settle_ms) * 5)
        if elapsed_ms >= timeout_ms:
            self._fail("shell navigation outbound timeout")
            return
        self.QTimer.singleShot(25, self._poll_shell_nav_outbound)

    def _capture_shell_nav_visual_artifacts(self, cycle: int, table) -> dict[str, dict]:
        """Save read-only screenshots for the shell-nav result without changing acceptance."""
        artifact_dir = Path(self.activation_profile_path).parent
        paths = {
            "main_window": artifact_dir / f"shell_nav_cycle_{cycle}_main_window.png",
            "watchlist_viewport": artifact_dir / f"shell_nav_cycle_{cycle}_watchlist_viewport.png",
        }
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {
                name: {"path": str(path), "saved": False, "error": str(exc)}
                for name, path in paths.items()
            }

        viewport_getter = getattr(table, "viewport", None)
        try:
            viewport = viewport_getter() if callable(viewport_getter) else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            viewport = None
        widgets = {
            "main_window": self.window,
            "watchlist_viewport": viewport,
        }
        artifacts: dict[str, dict] = {}
        for name, widget in widgets.items():
            path = paths[name]
            artifact = {"path": str(path), "saved": False}
            try:
                if widget is None:
                    artifact["error"] = "unavailable"
                else:
                    pixmap = widget.grab()
                    if pixmap is None or pixmap.isNull():
                        artifact["error"] = "empty_grab"
                    elif not bool(pixmap.save(str(path), "PNG")):
                        artifact["error"] = "save_failed"
                    else:
                        artifact["saved"] = True
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                artifact["error"] = str(exc)
            artifacts[name] = artifact
        return artifacts

    def _finish_shell_nav_cycle(self) -> None:
        phase = self._shell_nav_phase
        if self._done or phase is None:
            return
        workspace = phase["workspace"]
        tabs = getattr(workspace, "tabs", None)
        current_index = int(getattr(tabs, "currentIndex", lambda: -1)()) if tabs is not None else -1
        if current_index != int(phase["watchlist_index"]):
            self._fail("shell navigation return did not activate watchlist")
            return
        cycle = int(phase["cycle"])
        phase_name = f"shell_nav_{cycle}_watchlist_return"
        tab_count = int(getattr(tabs, "count", lambda: 0)()) if tabs is not None else 0
        expected_tab_count = len(list(getattr(workspace, "tab_specs", lambda: [])() or []))
        paint_region = self.paint_probe.paint_region_summary(phase_name)
        phase_metrics = self._metrics_since(phase["metric_offsets"])
        signal_probe = getattr(self, "_model_signal_probe", None)
        phase_signal_events = (
            signal_probe.events_since(phase.get("model_signal_offset", 0))
            if signal_probe is not None
            else []
        )
        paint_metrics = _summarize_shell_nav_paint_metrics(
            phase_metrics.get("watchlist_table_paint_ms", [])
        )
        repaint_guard = _summarize_shell_nav_guard_metrics(
            phase_metrics.get("watchlist_shell_nav_repaint_guard", [])
        )
        try:
            tab_getter = getattr(workspace, "get_loaded_tab", None)
            watchlist_tab = tab_getter("watchlist") if callable(tab_getter) else None
            table = getattr(watchlist_tab, "table_sp", None)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            table = None
        visual_artifacts = self._capture_shell_nav_visual_artifacts(cycle, table)
        self._shell_nav_results.append(
            {
                "cycle": cycle,
                "activation_path": "ShellNavigationWidget._switch_group",
                "return_reason": "shell_nav",
                "outbound_group": str(phase["outbound_group"]),
                "watchlist_group": str(phase["watchlist_group"]),
                "tab_count": tab_count,
                "expected_tab_count": expected_tab_count,
                "elapsed_ms": round(
                    (time.perf_counter() - float(phase["return_started_at"])) * 1000.0,
                    3,
                ),
                "paint_region": paint_region,
                "paint_metrics": paint_metrics,
                "paint_delays": _summarize_paint_delay_metrics(
                    phase_metrics.get("watchlist_table_paint_delay_ms", [])
                ),
                "membership_reconcile": _summarize_membership_reconcile_metrics(
                    phase_metrics.get("watchlist_membership_reconcile", [])
                ),
                "repaint_guard": repaint_guard,
                "model_signals": _summarize_model_signal_events(phase_signal_events),
                "reset_to_first_paint": _reset_to_first_paint_delays(
                    phase_signal_events,
                    paint_region,
                ),
                "viewport_update_requests": (
                    self.paint_probe.viewport_update_request_summary(phase_name)
                    if callable(getattr(self.paint_probe, "viewport_update_request_summary", None))
                    else {"count": 0, "samples": []}
                ),
                "visual_artifacts": visual_artifacts,
                "heartbeat_lateness": summarize_durations(
                    list(self._heartbeat_by_phase.get(phase_name, ()))
                ),
                "event_loop_stalls": summarize_durations(
                    [sample.value for sample in phase_metrics.get("ui_event_loop_stall_ms", [])]
                ),
                "ui_stall_snapshot": self._stall_snapshot(),
            }
        )
        self._shell_nav_cycle_index = cycle
        self._shell_nav_phase = None
        self.QTimer.singleShot(0, self._continue_after_background_prewarm)

    def _continue_after_background_prewarm(self) -> None:
        if self._done:
            return
        if self._shell_nav_cycle_index < max(0, int(self.args.shell_nav_cycles)):
            self._start_shell_nav_cycle()
            return
        if bool(self.args.membership_delta_probe) and not self._membership_delta_probe_started:
            self._start_membership_delta_probe()
            return
        if max(0, int(self.args.question_dialog_ms)) > 0 and not self._question_dialog_probed:
            self._run_question_dialog_probe()
            return
        self._continue_after_watchlist_settle()

    def _watchlist_model_targets(self):
        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        model = getattr(tab, "model", None)
        proxy = getattr(tab, "proxy_model", None)
        return workspace, tab, model, proxy

    def _start_membership_delta_probe(self) -> None:
        if self._done or self._membership_delta_phase is not None:
            return
        workspace, _tab, model, proxy = self._watchlist_model_targets()
        rows = [dict(row) for row in list(getattr(model, "row_data", None) or []) if isinstance(row, dict)]
        if model is None or proxy is None or len(rows) < 2 or len(rows) != int(model.rowCount()):
            self._fail("watchlist membership-delta probe unavailable")
            return

        self._membership_delta_probe_started = True
        removed_row = len(rows) // 2
        removed_code = str(rows[removed_row].get("代码", "") or "").strip()
        if not removed_code:
            self._fail("watchlist membership-delta probe missing code identity")
            return
        self._reset_stall_probe()
        phase_name = "membership_delta_remove"
        self._set_phase(phase_name)
        metric_offsets = self._metric_offsets(SHELL_NAV_REPAINT_METRICS)
        signal_offset = self._model_signal_probe.offset()
        started_at = time.perf_counter()
        model.update_data(
            [dict(row) for index, row in enumerate(rows) if index != removed_row],
            hydrate_latest_quotes=False,
            allow_single_row_membership_delta=True,
            membership_reconcile_source="native_profile_membership_delta",
        )
        self._membership_delta_phase = {
            "workspace": workspace,
            "model": model,
            "proxy": proxy,
            "initial_rows": rows,
            "initial_row_count": len(rows),
            "removed_row": removed_row,
            "removed_code": removed_code,
            "remove": {
                "phase_name": phase_name,
                "call_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
                "metric_offsets": metric_offsets,
                "signal_offset": signal_offset,
            },
            "initial_signal_offset": signal_offset,
        }
        self.QTimer.singleShot(300, self._finish_membership_delta_remove)

    def _membership_delta_phase_result(self, phase: dict, details: dict) -> dict:
        phase_name = str(details["phase_name"])
        paint_region = self.paint_probe.paint_region_summary(phase_name)
        metrics = self._metrics_since(details["metric_offsets"])
        signal_events = self._model_signal_probe.events_since(details["signal_offset"])
        return {
            "phase": phase_name,
            "call_ms": details["call_ms"],
            "paint_region": paint_region,
            "paint_metrics": _summarize_shell_nav_paint_metrics(
                metrics.get("watchlist_table_paint_ms", [])
            ),
            "paint_delays": _summarize_paint_delay_metrics(
                metrics.get("watchlist_table_paint_delay_ms", [])
            ),
            "membership_reconcile": _summarize_membership_reconcile_metrics(
                metrics.get("watchlist_membership_reconcile", [])
            ),
            "model_signals": _summarize_model_signal_events(signal_events),
            "reset_to_first_paint": _reset_to_first_paint_delays(signal_events, paint_region),
            "viewport_update_requests": self.paint_probe.viewport_update_request_summary(phase_name),
            "ui_stall_snapshot": self._stall_snapshot(),
        }

    def _finish_membership_delta_remove(self) -> None:
        phase = self._membership_delta_phase
        if self._done or phase is None:
            return
        phase["remove_result"] = self._membership_delta_phase_result(phase, phase["remove"])
        model = phase["model"]
        self._reset_stall_probe()
        phase_name = "membership_delta_restore"
        self._set_phase(phase_name)
        metric_offsets = self._metric_offsets(SHELL_NAV_REPAINT_METRICS)
        signal_offset = self._model_signal_probe.offset()
        started_at = time.perf_counter()
        model.update_data(
            [dict(row) for row in phase["initial_rows"]],
            hydrate_latest_quotes=False,
            allow_single_row_membership_delta=True,
            membership_reconcile_source="native_profile_membership_delta",
        )
        phase["restore"] = {
            "phase_name": phase_name,
            "call_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            "metric_offsets": metric_offsets,
            "signal_offset": signal_offset,
        }
        self.QTimer.singleShot(300, self._finish_membership_delta_restore)

    def _finish_membership_delta_restore(self) -> None:
        phase = self._membership_delta_phase
        if self._done or phase is None:
            return
        restore_result = self._membership_delta_phase_result(phase, phase["restore"])
        signal_events = self._model_signal_probe.events_since(phase["initial_signal_offset"])
        signal_summary = _summarize_model_signal_events(signal_events)
        counts = signal_summary["counts"]
        model = phase["model"]
        proxy = phase["proxy"]
        workspace = phase["workspace"]
        tab_count = int(getattr(getattr(workspace, "tabs", None), "count", lambda: 0)())
        expected_tab_count = len(list(getattr(workspace, "tab_specs", lambda: [])() or []))
        expected_row_count = int(phase["initial_row_count"])
        expected_counts = {
            "source.rows_removed": 1,
            "proxy.rows_removed": 1,
            "source.rows_inserted": 1,
            "proxy.rows_inserted": 1,
            "source.model_reset": 0,
            "proxy.model_reset": 0,
        }
        violations = [
            f"{name}={counts.get(name, 0)} expected={expected}"
            for name, expected in expected_counts.items()
            if int(counts.get(name, 0)) != expected
        ]
        try:
            source_row_count = int(model.rowCount())
            proxy_row_count = int(proxy.rowCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            source_row_count = proxy_row_count = -1
        if source_row_count != expected_row_count or proxy_row_count != expected_row_count:
            violations.append(
                f"row_count=source:{source_row_count},proxy:{proxy_row_count},expected:{expected_row_count}"
            )
        if tab_count != expected_tab_count:
            violations.append(f"tab_count={tab_count} expected={expected_tab_count}")
        self.report["watchlist"]["membership_delta_probe"] = {
            "removed_row": int(phase["removed_row"]),
            "removed_code": phase["removed_code"],
            "initial_row_count": expected_row_count,
            "source_row_count": source_row_count,
            "proxy_row_count": proxy_row_count,
            "tab_count": tab_count,
            "expected_tab_count": expected_tab_count,
            "remove": phase["remove_result"],
            "restore": restore_result,
            "signals": signal_summary,
            "acceptance": {"status": "pass" if not violations else "fail", "violations": violations},
        }
        self._membership_delta_phase = None
        if violations:
            self._fail("watchlist membership-delta probe acceptance failed")
            return
        self._continue_after_background_prewarm()

    def _continue_after_watchlist_settle(self) -> None:
        if self._done:
            return
        if max(0, int(self.args.quote_cycles)) <= 0:
            self._prepare_residual_repaint()
            return
        self._start_quote_cycle()

    def _run_question_dialog_probe(self) -> None:
        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        table = getattr(tab, "table_sp", None)
        if table is None:
            self._fail("watchlist question-dialog probe unavailable")
            return

        from ui.components.message_box import ThemedQuestionDialog

        self._question_dialog_probed = True
        self._reset_stall_probe()
        self._set_phase("question_dialog_open")
        table._mark_pending_paint_metric("native_profile_question_dialog")
        offsets = self._metric_offsets()
        dialog = ThemedQuestionDialog(
            self.window,
            "运行时重绘探针",
            "自动关闭的关注池确认框重绘探针。",
            yes_text="继续",
            no_text="取消",
        )
        self.QTimer.singleShot(max(1, int(self.args.question_dialog_ms)), dialog.accept)
        started_at = time.perf_counter()
        dialog.exec()
        call_ms = (time.perf_counter() - started_at) * 1000.0
        self._question_dialog_phase = {
            "call_ms": round(call_ms, 3),
            "offsets": offsets,
        }
        self._set_phase("question_dialog_close_settle")
        self.QTimer.singleShot(250, self._finish_question_dialog_probe)

    def _finish_question_dialog_probe(self) -> None:
        phase = self._question_dialog_phase
        if self._done or not phase:
            return
        heartbeat_values = [
            value
            for name, values in self._heartbeat_by_phase.items()
            if name.startswith("question_dialog_")
            for value in values
        ]
        self.report["question_dialog"] = {
            "hold_ms": max(1, int(self.args.question_dialog_ms)),
            "call_ms": phase["call_ms"],
            "metrics": _summarize_residual_repaint_metrics(self._metrics_since(phase["offsets"])),
            "paint_region": self.paint_probe.paint_region_summary_prefix("question_dialog_"),
            "heartbeat_lateness": summarize_durations(heartbeat_values),
            "ui_stall_snapshot": self._stall_snapshot(),
        }
        self._question_dialog_phase = None
        self._continue_after_watchlist_settle()

    def _start_quote_cycle(self) -> None:
        if self._done:
            return
        cycle_count = max(0, int(self.args.quote_cycles))
        if self._quote_cycle_index >= cycle_count:
            self._prepare_residual_repaint()
            return

        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        model = getattr(tab, "model", None)
        rows = list(getattr(model, "row_data", None) or [])
        self.report["watchlist"]["row_count"] = len(rows)
        payload = _build_synthetic_quote_payload(
            rows,
            cycle=self._quote_cycle_index,
            target_count=max(1, int(self.args.quote_target_count)),
        )
        if not payload:
            self._fail("watchlist synthetic quote payload unavailable")
            return

        cycle = self._quote_cycle_index + 1
        self._quote_cycle_index = cycle
        self._quote_cycle_payload_sizes.append(len(payload))
        proxy = getattr(tab, "proxy_model", None)
        layout_events = []

        def layout_slot(*_args):
            layout_events.append(time.perf_counter())

        if proxy is not None:
            proxy.layoutChanged.connect(layout_slot)
        self._reset_stall_probe()
        self._set_phase(f"quote_cycle_{cycle}_initial_paint")
        offsets = self._metric_offsets()

        from domains.quotes.dispatcher import publish_rt_quotes

        started_at = time.perf_counter()
        publish_rt_quotes(payload, source="native_watchlist_profile")
        call_ms = (time.perf_counter() - started_at) * 1000.0
        self._quote_phase = {
            "cycle": cycle,
            "payload_size": len(payload),
            "call_ms": round(call_ms, 3),
            "layout_events": layout_events,
            "layout_slot": layout_slot,
            "offsets": offsets,
            "proxy": proxy,
        }
        cycle_ms = max(900, int(self.args.quote_cycle_ms))
        self.QTimer.singleShot(350, lambda cycle=cycle: self._set_quote_phase(cycle, "between_paints"))
        self.QTimer.singleShot(450, lambda cycle=cycle: self._set_quote_phase(cycle, "flash_expiry"))
        self.QTimer.singleShot(cycle_ms, lambda cycle=cycle: self._finish_quote_cycle(cycle))

    def _set_quote_phase(self, cycle: int, suffix: str) -> None:
        if self._done or cycle != self._quote_cycle_index:
            return
        self._set_phase(f"quote_cycle_{cycle}_{suffix}")

    def _finish_quote_cycle(self, cycle: int) -> None:
        if self._done or cycle != self._quote_cycle_index:
            return
        phase = self._quote_phase
        if not phase or phase.get("cycle") != cycle:
            self._fail("watchlist quote-cycle probe state unavailable")
            return
        proxy = phase.get("proxy")
        if proxy is not None:
            try:
                proxy.layoutChanged.disconnect(phase["layout_slot"])
            except (RuntimeError, TypeError):
                pass
        phase_prefix = f"quote_cycle_{cycle}_"
        heartbeat_values = [
            value
            for name, values in self._heartbeat_by_phase.items()
            if name.startswith(phase_prefix)
            for value in values
        ]
        self._quote_results.append(
            {
                "cycle": cycle,
                "payload_size": phase["payload_size"],
                "call_ms": phase["call_ms"],
                "proxy_layout_changed_count": len(phase["layout_events"]),
                "metrics": _summarize_residual_repaint_metrics(self._metrics_since(phase["offsets"])),
                "paint_region": self.paint_probe.paint_region_summary_prefix(phase_prefix),
                "heartbeat_lateness": summarize_durations(heartbeat_values),
                "ui_stall_snapshot": self._stall_snapshot(),
            }
        )
        self._quote_phase = None
        self._start_quote_cycle()

    @staticmethod
    def _reset_stall_probe() -> None:
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        stall_probe = get_ui_stall_probe()
        if stall_probe is not None:
            stall_probe.reset_stall_snapshot()

    @staticmethod
    def _stall_snapshot() -> dict:
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        stall_probe = get_ui_stall_probe()
        return stall_probe.stall_snapshot() if stall_probe is not None else {"installed": False}

    @staticmethod
    def _metric_offsets(metric_names: tuple[str, ...] = RESIDUAL_REPAINT_METRICS) -> dict[str, int]:
        from core.observability import metric_history

        return {str(name): len(metric_history(str(name))) for name in metric_names}

    @staticmethod
    def _metrics_since(offsets: dict[str, int]) -> dict[str, list]:
        from core.observability import metric_history

        return {
            str(name): list(metric_history(str(name)))[max(0, int(offset or 0)) :]
            for name, offset in offsets.items()
        }

    def _prepare_residual_repaint(self) -> None:
        if self._done:
            return
        if max(0, int(self.args.residual_repaint_cycles)) <= 0:
            self._finish()
            return
        if self._residual_isolation_started:
            return
        self._residual_isolation_started = True

        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        if tab is None:
            self._fail("watchlist residual repaint isolation unavailable")
            return
        shutdown = getattr(tab, "shutdown", None)
        if callable(shutdown):
            shutdown()
        residual_report = self.report.setdefault("residual_repaint", {})
        residual_report["background_runtime_isolated"] = True
        residual_report["probe_scope"] = {
            "name_refresh": "public_refresh_entrypoint",
            "watchlist_to_lhb": "outgoing_snapshot_only_current_changed_signals_blocked",
            "lhb_to_watchlist": "snapshot_skip_and_visible_index_return_current_changed_signals_blocked",
        }
        self._set_phase("residual_repaint_isolation_settle")
        self.QTimer.singleShot(800, self._start_residual_repaint_cycle)

    def _start_residual_repaint_cycle(self) -> None:
        if self._done:
            return
        cycle_count = max(0, int(self.args.residual_repaint_cycles))
        if self._residual_cycle_index >= cycle_count:
            self._finish()
            return

        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        model = getattr(tab, "model", None)
        proxy = getattr(tab, "proxy_model", None)
        table = getattr(tab, "table_sp", None)
        rows = list(getattr(model, "row_data", None) or [])
        if tab is None or model is None or proxy is None or table is None or not rows:
            self._fail("watchlist residual repaint probe unavailable")
            return

        cycle = self._residual_cycle_index + 1
        self._residual_cycle_index = cycle

        source_row = 0
        try:
            proxy_index = proxy.index(0, 0)
            mapped = proxy.mapToSource(proxy_index)
            if mapped.isValid():
                source_row = mapped.row()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            source_row = 0
        row = rows[source_row]
        code = str(row.get("代码", "") or "").strip()
        original_name = str(row.get("名称", "") or "").strip()
        resolved_name = original_name if original_name and original_name != code else f"运行时探针{cycle}"
        if not code:
            self._fail("watchlist residual repaint probe code unavailable")
            return

        # Prime only the isolated in-memory profile model; the public refresh
        # entrypoint below performs the measured change and emits its real signal.
        row["名称"] = code
        layout_events = []

        def layout_slot(*_args):
            layout_events.append(time.perf_counter())

        proxy.layoutChanged.connect(layout_slot)
        self._reset_stall_probe()
        self._set_phase(f"residual_{cycle}_name_refresh")
        table._mark_pending_paint_metric("native_profile_name_refresh")
        offsets = self._metric_offsets()
        started_at = time.perf_counter()
        changed = bool(tab.refresh_watchlist_names({code: resolved_name}))
        call_ms = (time.perf_counter() - started_at) * 1000.0
        self._residual_phase = {
            "cycle": cycle,
            "action": "name_refresh",
            "call_ms": round(call_ms, 3),
            "changed": changed,
            "layout_events": layout_events,
            "layout_slot": layout_slot,
            "offsets": offsets,
            "proxy": proxy,
        }
        self.QTimer.singleShot(160, self._finish_residual_name_refresh)

    def _finish_residual_name_refresh(self) -> None:
        phase = self._residual_phase
        if self._done or not phase or phase.get("action") != "name_refresh":
            return
        proxy = phase["proxy"]
        try:
            proxy.layoutChanged.disconnect(phase["layout_slot"])
        except (RuntimeError, TypeError):
            pass
        result = {
            "cycle": phase["cycle"],
            "action": phase["action"],
            "call_ms": phase["call_ms"],
            "changed": phase["changed"],
            "proxy_layout_changed_count": len(phase["layout_events"]),
            "metrics": _summarize_residual_repaint_metrics(self._metrics_since(phase["offsets"])),
            "paint_region": self.paint_probe.paint_region_summary(self._phase),
            "heartbeat_lateness": summarize_durations(
                list(self._heartbeat_by_phase.get(self._phase, ()))
            ),
            "ui_stall_snapshot": self._stall_snapshot(),
        }
        self._residual_results.append(result)
        self._start_residual_tab_switch(phase["cycle"])

    def _start_residual_tab_switch(self, cycle: int) -> None:
        workspace = getattr(self.window, "_workspace", None)
        tabs = getattr(workspace, "tabs", None)
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or []) if workspace is not None else []
        watchlist_index = next((i for i, spec in enumerate(specs) if spec.get("key") == "watchlist"), -1)
        target_index = next((i for i, spec in enumerate(specs) if spec.get("key") == "lhb"), -1)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        table = getattr(tab, "table_sp", None)
        if tabs is None or table is None or watchlist_index < 0 or target_index < 0:
            self._fail("watchlist tab-switch probe unavailable")
            return

        self._reset_stall_probe()
        self._set_phase(f"residual_{cycle}_watchlist_to_lhb")
        table._mark_pending_paint_metric("native_profile_tab_switch")
        offsets = self._metric_offsets()
        old_gap = int(getattr(tabs, "_min_transition_gap_ms", 0) or 0)
        old_suspended_until = float(getattr(tabs, "_transition_suspended_until", 0.0) or 0.0)
        old_last_transition_at = float(getattr(tabs, "_last_transition_at", 0.0) or 0.0)
        previous_blocked = tabs.blockSignals(True)
        try:
            tabs.setMinimumTransitionGap(0)
            tabs._transition_suspended_until = 0.0
            tabs._last_transition_at = 0.0
            started_at = time.perf_counter()
            tabs.setCurrentIndex(target_index)
            call_ms = (time.perf_counter() - started_at) * 1000.0
        finally:
            tabs._min_transition_gap_ms = old_gap
            tabs._transition_suspended_until = old_suspended_until
            tabs._last_transition_at = old_last_transition_at
            tabs.blockSignals(previous_blocked)
        self._residual_phase = {
            "cycle": cycle,
            "action": "watchlist_to_lhb",
            "call_ms": round(call_ms, 3),
            "offsets": offsets,
            "tabs": tabs,
            "watchlist_index": watchlist_index,
        }
        self.QTimer.singleShot(160, self._finish_residual_tab_switch)

    def _finish_residual_tab_switch(self) -> None:
        phase = self._residual_phase
        if self._done or not phase or phase.get("action") != "watchlist_to_lhb":
            return
        self._residual_results.append(
            {
                "cycle": phase["cycle"],
                "action": phase["action"],
                "call_ms": phase["call_ms"],
                "metrics": _summarize_residual_repaint_metrics(self._metrics_since(phase["offsets"])),
                "paint_region": self.paint_probe.paint_region_summary(self._phase),
                "heartbeat_lateness": summarize_durations(
                    list(self._heartbeat_by_phase.get(self._phase, ()))
                ),
                "ui_stall_snapshot": self._stall_snapshot(),
            }
        )

        self._start_residual_inbound_tab_return(phase)

    def _start_residual_inbound_tab_return(self, outbound_phase: dict) -> None:
        tabs = outbound_phase["tabs"]
        watchlist_index = outbound_phase["watchlist_index"]
        workspace = getattr(self.window, "_workspace", None)
        tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
        table = getattr(tab, "table_sp", None)
        if table is None:
            self._fail("watchlist inbound tab-return probe unavailable")
            return
        self._reset_stall_probe()
        self._set_phase(f"residual_{outbound_phase['cycle']}_lhb_to_watchlist")
        table._mark_pending_paint_metric("native_profile_tab_return")
        offsets = self._metric_offsets()
        old_gap = int(getattr(tabs, "_min_transition_gap_ms", 0) or 0)
        old_suspended_until = float(getattr(tabs, "_transition_suspended_until", 0.0) or 0.0)
        old_last_transition_at = float(getattr(tabs, "_last_transition_at", 0.0) or 0.0)
        previous_blocked = tabs.blockSignals(True)
        try:
            tabs.setMinimumTransitionGap(0)
            tabs._transition_suspended_until = 0.0
            tabs._last_transition_at = 0.0
            started_at = time.perf_counter()
            tabs.setCurrentIndex(watchlist_index)
            call_ms = (time.perf_counter() - started_at) * 1000.0
        finally:
            tabs._min_transition_gap_ms = old_gap
            tabs._transition_suspended_until = old_suspended_until
            tabs._last_transition_at = old_last_transition_at
            tabs.blockSignals(previous_blocked)
        self._residual_phase = {
            "cycle": outbound_phase["cycle"],
            "action": "lhb_to_watchlist",
            "call_ms": round(call_ms, 3),
            "offsets": offsets,
        }
        self.QTimer.singleShot(250, self._finish_residual_inbound_tab_return)

    def _finish_residual_inbound_tab_return(self) -> None:
        phase = self._residual_phase
        if self._done or not phase or phase.get("action") != "lhb_to_watchlist":
            return
        self._residual_results.append(
            {
                "cycle": phase["cycle"],
                "action": phase["action"],
                "call_ms": phase["call_ms"],
                "metrics": _summarize_residual_repaint_metrics(self._metrics_since(phase["offsets"])),
                "paint_region": self.paint_probe.paint_region_summary(self._phase),
                "heartbeat_lateness": summarize_durations(
                    list(self._heartbeat_by_phase.get(self._phase, ()))
                ),
                "ui_stall_snapshot": self._stall_snapshot(),
            }
        )
        self._set_phase(f"residual_{phase['cycle']}_cycle_settle")
        self._residual_phase = None
        self.QTimer.singleShot(160, self._start_residual_repaint_cycle)

    def _on_heartbeat(self) -> None:
        now = time.perf_counter()
        if self._heartbeat_last <= 0:
            self._heartbeat_last = now
            return
        interval_ms = max(5, int(self.args.heartbeat_ms))
        late_ms = max(0.0, (now - self._heartbeat_last) * 1000.0 - interval_ms)
        self._heartbeat_last = now
        self._heartbeat_by_phase[self._phase].append(late_ms)

    def _abort_on_timeout(self) -> None:
        if not self._done and "watchlist_loaded_ms" not in self.report["timings"]:
            self._fail("watchlist load timeout")

    def _fail(self, message: str) -> None:
        self.report["errors"].append(str(message))
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._heartbeat.stop()
        self._load_poll.stop()
        self._stop_active_profiler()

        from app.services.ui_task_service import background_job_runner
        from infra.diagnostics.ui_stall_probe import get_ui_stall_probe

        self.report["heartbeat_lateness"] = {
            phase: summarize_durations(values) for phase, values in sorted(self._heartbeat_by_phase.items())
        }
        quote_expected_cycles = max(0, int(self.args.quote_cycles))
        quote_acceptance = _quote_repaint_acceptance(
            self._quote_results,
            expected_cycles=quote_expected_cycles,
        )
        quote_acceptance_enforced = quote_expected_cycles > 0 and not bool(self.args.legacy_quote_repaint)
        self.report["quote_cycles"] = {
            "completed": self._quote_cycle_index,
            "payload_sizes": list(self._quote_cycle_payload_sizes),
            "acceptance": quote_acceptance,
            "acceptance_enforced": quote_acceptance_enforced,
            "results": list(self._quote_results),
        }
        if quote_acceptance_enforced and quote_acceptance["status"] != "pass":
            self.report["errors"].append("quote repaint acceptance failed")
        shell_nav_expected_cycles = max(0, int(self.args.shell_nav_cycles))
        shell_nav_acceptance = _shell_nav_repaint_acceptance(
            self._shell_nav_results,
            expected_cycles=shell_nav_expected_cycles,
        )
        self.report["shell_nav_cycles"] = {
            "completed": self._shell_nav_cycle_index,
            "settle_ms": max(1, int(self.args.shell_nav_settle_ms)),
            "acceptance": shell_nav_acceptance,
            "acceptance_enforced": shell_nav_expected_cycles > 0,
            "results": list(self._shell_nav_results),
        }
        if shell_nav_expected_cycles > 0 and shell_nav_acceptance["status"] != "pass":
            self.report["errors"].append("shell navigation repaint acceptance failed")
        expected_cycles = max(0, int(self.args.residual_repaint_cycles))
        acceptance = _residual_repaint_acceptance(
            self._residual_results,
            expected_cycles=expected_cycles,
        )
        self.report.setdefault("residual_repaint", {}).update(
            {
                "acceptance": acceptance,
                "completed_cycles": self._residual_cycle_index,
                "results": list(self._residual_results),
            }
        )
        if expected_cycles > 0 and acceptance["status"] != "pass":
            self.report["errors"].append("residual repaint acceptance failed")
        self.report["background_tasks_at_finish"] = int(getattr(background_job_runner, "active_count", 0) or 0)
        stall_probe = get_ui_stall_probe()
        self.report["ui_stall_snapshot"] = stall_probe.stall_snapshot() if stall_probe is not None else {"installed": False}
        self.report.setdefault("watchlist", {}).setdefault("model_signal_monitor", {}).update(
            _summarize_model_signal_events(self._model_signal_probe.events)
        )
        self.report.setdefault("watchlist", {})["membership_reconcile_monitor"] = (
            _summarize_membership_reconcile_metrics(
                self._metrics_since(self._membership_reconcile_metric_offset).get(
                    "watchlist_membership_reconcile", []
                )
            )
        )
        self._model_signal_probe.close()
        self._set_phase("profile_finalize")
        self.report["dispatcher"] = self.dispatcher_probe.finish()
        self.report["paint_events"] = self.paint_probe.report()
        self.paint_probe.close()
        self.report["timings"]["profile_elapsed_ms"] = round((time.perf_counter() - self.origin) * 1000.0, 3)
        try:
            self.window.close()
        finally:
            self.QTimer.singleShot(0, self.app.quit)

    def _stop_active_profiler(self) -> None:
        profiler = self._active_profiler
        profile_path = self._active_profile_path
        if profiler is None or profile_path is None:
            return
        profiler.disable()
        profiler.dump_stats(str(profile_path))
        self._active_profiler = None
        self._active_profile_path = None


def _build_environment_report(app) -> dict:
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

    screen = app.primaryScreen()
    geometry = screen.availableGeometry() if screen is not None else None
    return {
        "system": platform.platform(),
        "python": sys.version,
        "qt": QT_VERSION_STR,
        "pyqt": PYQT_VERSION_STR,
        "pid": os.getpid(),
        "session_name": os.environ.get("SESSIONNAME", ""),
        "requested_qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "actual_qt_platform": app.platformName(),
        "primary_screen": (
            {
                "name": screen.name(),
                "available_width": geometry.width(),
                "available_height": geometry.height(),
                "device_pixel_ratio": screen.devicePixelRatio(),
            }
            if screen is not None and geometry is not None
            else None
        ),
    }


def _create_native_qt_application():
    from app.services.runtime_services import (
        initialize_native_dataframe_runtime,
        is_native_dataframe_runtime_ready,
    )
    from core.runtime_env import configure_qt_webengine_runtime

    configure_qt_webengine_runtime()
    QAbstractEventDispatcher, QCoreApplication, QEvent, _QObject, Qt, QTimer, QApplication = _qt_types()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    native_runtime_started = time.perf_counter()
    initialize_native_dataframe_runtime()
    environment = _build_environment_report(app)
    environment["native_dataframe_runtime"] = {
        "ready": is_native_dataframe_runtime_ready(),
        "initialization_ms": round((time.perf_counter() - native_runtime_started) * 1000.0, 3),
    }
    platform_error = _native_platform_error(**{
        "requested": environment["requested_qt_platform"],
        "actual": environment["actual_qt_platform"],
    })
    if platform_error:
        raise RuntimeError(platform_error)
    return app, environment, QAbstractEventDispatcher, QEvent, Qt, QTimer


def _profile_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "report": output_dir / "native_watchlist_profile.json",
        "startup": output_dir / "startup.prof",
        "activation": output_dir / "watchlist_activation.prof",
        "settle": output_dir / "watchlist_settle.prof",
    }


def _build_profile_report(args, environment, database_info, paths) -> dict:
    enabled = not bool(args.no_cprofile)
    return {
        "schema_version": 1,
        "report_type": "native_watchlist_profile",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "running",
        "environment": environment,
        "isolation": {
            "database": database_info,
            "settings_application": os.environ["VCP_HUNTER_SETTINGS_APPLICATION"],
            "log_dir": os.environ["VCP_HUNTER_LOG_DIR"],
            "startup_orchestrator_suppressed": True,
            "auto_refresh_suppressed": True,
            "central_quotes_suppressed": True,
            "background_prewarm_suppressed": not bool(args.background_prewarm),
        },
        "configuration": {
            "warmup_ms": int(args.warmup_ms),
            "settle_ms": int(args.settle_ms),
            "load_timeout_ms": int(args.load_timeout_ms),
            "heartbeat_ms": int(args.heartbeat_ms),
            "background_prewarm": bool(args.background_prewarm),
            "restore_last_tab": bool(args.restore_last_tab),
            "prewarm_timeout_ms": int(args.prewarm_timeout_ms),
            "quote_cycles": int(args.quote_cycles),
            "quote_cycle_ms": int(args.quote_cycle_ms),
            "quote_target_count": int(args.quote_target_count),
            "shell_nav_cycles": int(args.shell_nav_cycles),
            "shell_nav_settle_ms": int(args.shell_nav_settle_ms),
            "shell_nav_only": bool(args.shell_nav_only),
            "membership_delta_probe": bool(args.membership_delta_probe),
            "disable_market_pulse": bool(args.disable_market_pulse),
            "question_dialog_ms": int(args.question_dialog_ms),
            "residual_repaint_cycles": int(args.residual_repaint_cycles),
            "legacy_quote_repaint": bool(args.legacy_quote_repaint),
            "cprofile_enabled": enabled,
        },
        "timings": {},
        "profiles": {
            "startup": str(paths["startup"]) if enabled else None,
            "watchlist_activation": str(paths["activation"]) if enabled else None,
            "watchlist_settle": str(paths["settle"]) if enabled else None,
        },
        "errors": [],
    }


def _build_profiled_main_window(app, qevent_type, args, report, paths, origin):
    startup_profiler = cProfile.Profile()
    if not args.no_cprofile:
        startup_profiler.enable()
    import_started = time.perf_counter()
    import ui.main_window_qt as main_window_module

    report["timings"]["main_window_import_ms"] = round((time.perf_counter() - import_started) * 1000.0, 3)
    construct_started = time.perf_counter()
    window = main_window_module.MainWindowQT(
        startup_enabled=True,
        auto_refresh_enabled=False,
        background_prewarm=bool(args.background_prewarm),
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
        controlled_startup_probe_guard=False,
    )
    # The Watchlist factory retains its production startup flags while the
    # later post-paint startup orchestrator remains suppressed for isolation.
    window._startup_enabled = False
    if bool(args.background_prewarm):
        workspace = getattr(window, "_workspace", None)
        mark_cache_ready = getattr(workspace, "_on_startup_cache_bootstrap_ready", None)
        if callable(mark_cache_ready):
            mark_cache_ready()
    report["timings"]["window_construct_ms"] = round((time.perf_counter() - construct_started) * 1000.0, 3)
    paint_probe = _FirstPaintProbe(app, window, qevent_type, origin=origin)
    show_started = time.perf_counter()
    window.show()
    window.raise_()
    window.activateWindow()
    pulse_strip = getattr(window, "_market_pulse_strip", None)
    pulse_timer = getattr(pulse_strip, "_timer", None)
    if bool(args.disable_market_pulse) and pulse_timer is not None:
        pulse_timer.stop()
    report["runtime_controls"] = {
        "market_pulse_disabled": bool(args.disable_market_pulse),
        "market_pulse_timer_active": bool(pulse_timer is not None and pulse_timer.isActive()),
    }
    report["timings"]["window_show_call_ms"] = round((time.perf_counter() - show_started) * 1000.0, 3)
    if not args.no_cprofile:
        startup_profiler.disable()
        startup_profiler.dump_stats(str(paths["startup"]))
    return window, paint_probe


def _append_profile_summaries(report, args, paths) -> None:
    if args.no_cprofile:
        return
    for report_key, path_key in (
        ("startup_top_cumulative", "startup"),
        ("watchlist_activation_top_cumulative", "activation"),
        ("watchlist_settle_top_cumulative", "settle"),
    ):
        report["profiles"][report_key] = _profile_top_functions(paths[path_key], limit=args.top_functions)


def run_profile(args: argparse.Namespace) -> tuple[dict, Path]:
    output_dir = (args.output_dir or _default_output_dir()).resolve()
    database_info = _configure_isolated_runtime(output_dir, args.source_db)
    app, environment, dispatcher_type, qevent_type, qt_type, qtimer_type = _create_native_qt_application()
    paths = _profile_paths(output_dir)
    report = _build_profile_report(args, environment, database_info, paths)

    origin = time.perf_counter()
    window, paint_probe = _build_profiled_main_window(app, qevent_type.Type, args, report, paths, origin)
    dispatcher = dispatcher_type.instance()
    if dispatcher is None:
        raise RuntimeError("native Qt event dispatcher unavailable")
    dispatcher_probe = _DispatcherPhaseProbe(dispatcher)
    controller = _NativeProfileController(
        app=app,
        window=window,
        qtimer_type=qtimer_type,
        qt_timer_type=qt_type.TimerType,
        dispatcher_probe=dispatcher_probe,
        paint_probe=paint_probe,
        args=args,
        activation_profile_path=paths["activation"],
        settle_profile_path=paths["settle"],
        report=report,
        origin=origin,
        cprofile_enabled=not args.no_cprofile,
    )
    controller.start()
    exit_code = app.exec()
    report["qt_exit_code"] = int(exit_code)
    _append_profile_summaries(report, args, paths)
    report["status"] = "ok" if not report["errors"] and "watchlist_loaded_ms" in report["timings"] else "error"
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, paths["report"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        report, report_path = run_profile(args)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(report_path),
                "watchlist_loaded_ms": report["timings"].get("watchlist_loaded_ms"),
                "watchlist_first_paint_ms": report.get("paint_events", {}).get(
                    "watchlist_first_paint_after_activation_ms"
                ),
                "shell_nav_acceptance": report.get("shell_nav_cycles", {}).get("acceptance", {}).get("status"),
                "max_active_dispatch_ms": max(
                    (
                        float(item["elapsed_ms"])
                        for item in report.get("dispatcher", {}).get("largest_active_dispatch_segments", [])
                    ),
                    default=0.0,
                ),
                "max_heartbeat_late_ms": max(
                    (float(item.get("max_ms", 0.0)) for item in report.get("heartbeat_lateness", {}).values()),
                    default=0.0,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
