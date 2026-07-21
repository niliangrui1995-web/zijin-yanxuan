from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, TypedDict

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CYCLES = 100
DEFAULT_WARMUP_CYCLES = 2
DEFAULT_STABILITY_WINDOW = 20
DEFAULT_SEGMENT_SIZE = 10
MB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SoakBudgets:
    max_physical_windows: int = 1
    max_browser_count: int = 1
    max_page_count: int = 1
    max_unique_physical_windows: int = 1
    max_unique_browsers: int = 1
    max_unique_pages: int = 1
    max_qtwebengine_processes: int = 8
    max_qtwebengine_process_growth: int = 0
    max_tree_rss_growth_mb: float = 48.0
    max_tail_rss_range_mb: float = 24.0
    max_tail_rss_slope_mb_per_cycle: float = 0.5


class SoakHarness(Protocol):
    manager: Any

    def prepare(self, *, timeout_ms: int) -> dict[str, Any]: ...

    def open_chart(self, args: argparse.Namespace, label: str) -> tuple[Any, dict[str, Any]]: ...

    def close_chart(self, chart: Any, *, timeout_ms: int) -> bool: ...

    def process_events(self) -> None: ...

    def provider_evidence(self) -> dict[str, Any]: ...

    def shutdown(self) -> dict[str, Any]: ...


class _CycleSampleArgs(TypedDict):
    label: str
    index: int
    role: str
    started_at: float
    process_sampler: Callable[[], dict[str, Any]]


class NativeKLineHarness:
    """Own one native Windows Qt process and drive the production KLine manager."""

    def __init__(self, args: argparse.Namespace) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("native Windows Qt WebEngine is required")
        os.environ.setdefault("QT_QPA_PLATFORM", "windows")
        qt_platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
        if not qt_platform.startswith("windows"):
            raise RuntimeError(f"QT_QPA_PLATFORM={qt_platform or 'unset'} is not a native Windows soak")

        from core.runtime_env import configure_qt_webengine_runtime

        configure_qt_webengine_runtime()

        from PyQt6.QtCore import QCoreApplication, Qt
        from PyQt6.QtWidgets import QApplication

        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

        from scripts import kline_webengine_lifecycle_smoke as lifecycle
        from ui.components.kline_window_manager import kline_manager

        self._lifecycle = lifecycle
        existing_app = QApplication.instance()
        if existing_app is not None and not isinstance(existing_app, QApplication):
            raise RuntimeError("existing Qt application is not a QApplication")
        self._app = existing_app or QApplication(sys.argv)
        self._window = lifecycle._new_smoke_window(args)
        self.manager = kline_manager

    def prepare(self, *, timeout_ms: int) -> dict[str, Any]:
        return self._lifecycle._prepare_smoke_runtime(
            self._app,
            self._window,
            timeout_ms=timeout_ms,
        )

    def open_chart(self, args: argparse.Namespace, label: str) -> tuple[Any, dict[str, Any]]:
        return self._lifecycle._open_acceptance_chart(
            self._app,
            self.manager,
            self._window,
            args,
            label,
        )

    def close_chart(self, chart: Any, *, timeout_ms: int) -> bool:
        closed = self._lifecycle._close_acceptance_chart(
            self._app,
            chart,
            timeout_ms=timeout_ms,
        )
        pooled = self._lifecycle._wait_until(
            self._app,
            lambda: _chart_is_idle_keeper(self.manager, chart),
            timeout_ms=timeout_ms,
            step_ms=25,
        )
        return bool(closed and pooled)

    def process_events(self) -> None:
        self._lifecycle._process_events(
            self._app,
            rounds=4,
            sleep_ms=20,
            flush_deferred_deletes=True,
        )

    def provider_evidence(self) -> dict[str, Any]:
        return self._lifecycle._smoke_provider_evidence(
            getattr(self._window, "data_provider", None)
        )

    def shutdown(self) -> dict[str, Any]:
        shutdown_report: dict[str, Any] = {}
        self._lifecycle._shutdown_smoke_window(
            self._app,
            self._window,
            shutdown_report,
        )
        return {
            "manager_shutdown": dict(getattr(self.manager, "shutdown_diagnostics", {}) or {}),
            "lifecycle_shutdown": shutdown_report.get("shutdown", {}),
        }


def _chart_is_idle_keeper(manager: Any, chart: Any) -> bool:
    return bool(
        getattr(manager, "_idle_chart", None) is chart
        and getattr(manager, "_reclaiming_chart", None) is None
        and int(getattr(manager, "active_count", 0) or 0) == 0
        and int(getattr(manager, "managed_webengine_keeper_count", 0) or 0) == 1
        and bool(getattr(manager, "managed_webengine_keeper_ready", False))
    )


def _safe_process_details(process: psutil.Process) -> dict[str, Any] | None:
    try:
        rss = int(process.memory_info().rss)
        return {
            "pid": int(process.pid),
            "name": str(process.name()),
            "rss_mb": round(rss / MB, 3),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def collect_owned_process_tree(root_pid: int | None = None) -> dict[str, Any]:
    """Sample only this probe's root process and recursive descendants."""

    pid = os.getpid() if root_pid is None else int(root_pid)
    root = psutil.Process(pid)
    try:
        descendants = root.children(recursive=True)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        descendants = []
    details = [item for item in (_safe_process_details(proc) for proc in [root, *descendants]) if item]
    webengine = [item for item in details if item["name"].lower().startswith("qtwebengineprocess")]
    return {
        "scope": "probe_root_and_recursive_descendants_only",
        "root_pid": pid,
        "process_count": len(details),
        "qtwebengine_process_count": len(webengine),
        "rss_mb": round(sum(float(item["rss_mb"]) for item in details), 3),
        "qtwebengine_rss_mb": round(sum(float(item["rss_mb"]) for item in webengine), 3),
        "processes": details,
    }


def _unique_resources(*resources: Any) -> list[Any]:
    result: dict[int, Any] = {}
    for resource in resources:
        if resource is not None:
            result[id(resource)] = resource
    return list(result.values())


def _manager_counts(manager: Any) -> dict[str, int]:
    health_reader = getattr(manager, "runtime_health_snapshot", None)
    health = dict(health_reader()) if callable(health_reader) else {}
    physical = _unique_resources(
        *tuple(getattr(manager, "_charts", ()) or ()),
        getattr(manager, "_idle_chart", None),
        getattr(manager, "_reclaiming_chart", None),
        getattr(manager, "_prewarm_window", None),
    )
    return {
        "physical_window_count": len(physical),
        "browser_count": int(health.get("browser_count") or 0),
        "page_count": int(health.get("page_count") or 0),
        "active_window_count": int(health.get("active_window_count") or 0),
        "keeper_count": int(health.get("keeper_count") or 0),
    }


def _object_identity(resource: Any | None) -> int:
    if resource is None:
        return 0
    try:
        sip = importlib.import_module("PyQt6.sip")
        native_address = getattr(sip, "unwrapinstance")(resource)
        return int(native_address)
    except (ImportError, TypeError, ValueError):
        return id(resource)


def _chart_identities(chart: Any | None) -> dict[str, int]:
    browser = getattr(chart, "browser", None) if chart is not None else None
    page_reader = getattr(browser, "page", None)
    try:
        page = page_reader() if callable(page_reader) else None
    except RuntimeError:
        page = None
    render_pid_reader = getattr(page, "renderProcessPid", None)
    try:
        render_pid = int(render_pid_reader() or 0) if callable(render_pid_reader) else 0
    except (RuntimeError, TypeError, ValueError):
        render_pid = 0
    return {
        "physical_window_id": _object_identity(chart),
        "browser_id": _object_identity(browser),
        "page_id": _object_identity(page),
        "render_process_pid": max(0, render_pid),
    }


def collect_sample(
    manager: Any,
    chart: Any | None,
    *,
    label: str,
    cycle_index: int,
    phase: str,
    role: str,
    started_at: float,
    process_sampler: Callable[[], dict[str, Any]] = collect_owned_process_tree,
) -> dict[str, Any]:
    return {
        "label": label,
        "role": role,
        "cycle_index": cycle_index,
        "phase": phase,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "manager": _manager_counts(manager),
        "identities": _chart_identities(chart),
        "process_tree": process_sampler(),
    }


def _cycle_sample(
    harness: SoakHarness,
    chart: Any | None,
    *,
    label: str,
    index: int,
    phase: str,
    role: str,
    started_at: float,
    process_sampler: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return collect_sample(
        harness.manager,
        chart,
        label=f"{label}:{phase}",
        cycle_index=index,
        phase=phase,
        role=role,
        started_at=started_at,
        process_sampler=process_sampler,
    )


def _run_cycle(
    harness: SoakHarness,
    args: argparse.Namespace,
    *,
    index: int,
    role: str,
    started_at: float,
    process_sampler: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    label = f"{role}_{index}"
    sample_args: _CycleSampleArgs = {
        "label": label,
        "index": index,
        "role": role,
        "started_at": started_at,
        "process_sampler": process_sampler,
    }
    samples = [_cycle_sample(harness, None, phase="before_open", **sample_args)]
    chart, opened, open_sample = _open_and_sample_cycle(
        harness,
        args,
        sample_args,
    )
    samples.append(open_sample)
    closed, closed_sample = _close_and_sample_cycle(harness, args, chart, sample_args)
    samples.append(closed_sample)
    ready = bool(opened.get("browser_ready") and opened.get("chart_ready"))
    return {
        "cycle_index": index,
        "role": role,
        "status": "ok" if ready and closed else "fail",
        "open": opened,
        "closed_to_idle_keeper": closed,
        "samples": samples,
    }


def _open_and_sample_cycle(
    harness: SoakHarness,
    args: argparse.Namespace,
    sample_args: _CycleSampleArgs,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    chart, opened = harness.open_chart(args, str(sample_args["label"]))
    sample = _cycle_sample(harness, chart, phase="open", **sample_args)
    return chart, opened, sample


def _close_and_sample_cycle(
    harness: SoakHarness,
    args: argparse.Namespace,
    chart: Any,
    sample_args: _CycleSampleArgs,
) -> tuple[bool, dict[str, Any]]:
    closed = harness.close_chart(chart, timeout_ms=args.close_timeout_ms) if chart is not None else False
    harness.process_events()
    return closed, _cycle_sample(harness, chart, phase="closed", **sample_args)


def _numeric_values(samples: list[dict[str, Any]], section: str, field: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = (sample.get(section) or {}).get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            values.append(parsed)
    return values


def _linear_slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_x = (len(values) - 1) / 2.0
    mean_y = statistics.fmean(values)
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    if denominator == 0:
        return 0.0
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    return numerator / denominator


def _max_metric(samples: list[dict[str, Any]], section: str, field: str) -> float:
    return max(_numeric_values(samples, section, field), default=0)


def _segment_rss_summary(rss: list[float]) -> dict[str, float | None]:
    if not rss:
        return {
            "closed_tree_rss_start_mb": None,
            "closed_tree_rss_end_mb": None,
            "closed_tree_rss_delta_mb": None,
            "closed_tree_rss_range_mb": None,
        }
    return {
        "closed_tree_rss_start_mb": rss[0],
        "closed_tree_rss_end_mb": rss[-1],
        "closed_tree_rss_delta_mb": round(rss[-1] - rss[0], 3),
        "closed_tree_rss_range_mb": round(max(rss) - min(rss), 3),
    }


def _failed_cycle_indexes(cycles: list[dict[str, Any]]) -> list[int]:
    return [int(item["cycle_index"]) for item in cycles if item["status"] != "ok"]


def _summarize_segment(group: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [sample for cycle in group for sample in cycle["samples"]]
    closed = [sample for sample in samples if sample["phase"] == "closed"]
    rss = _numeric_values(closed, "process_tree", "rss_mb")
    return {
        "start_cycle": group[0]["cycle_index"],
        "end_cycle": group[-1]["cycle_index"],
        "cycle_count": len(group),
        "failed_cycles": _failed_cycle_indexes(group),
        "max_physical_window_count": _max_metric(samples, "manager", "physical_window_count"),
        "max_browser_count": _max_metric(samples, "manager", "browser_count"),
        "max_page_count": _max_metric(samples, "manager", "page_count"),
        "max_qtwebengine_process_count": _max_metric(
            samples, "process_tree", "qtwebengine_process_count"
        ),
        **_segment_rss_summary(rss),
    }


def _segment_summary(cycles: list[dict[str, Any]], segment_size: int) -> list[dict[str, Any]]:
    groups = [cycles[offset : offset + segment_size] for offset in range(0, len(cycles), segment_size)]
    return [_summarize_segment(group) for group in groups]


def _failure(check: str, actual: Any, budget: Any, detail: str) -> dict[str, Any]:
    return {"check": check, "actual": actual, "budget": budget, "detail": detail}


def _identity_values(open_samples: list[dict[str, Any]], field: str) -> set[int]:
    return {
        int((sample.get("identities") or {}).get(field) or 0)
        for sample in open_samples
        if int((sample.get("identities") or {}).get(field) or 0) > 0
    }


def _count_budget_failures(samples: list[dict[str, Any]], budgets: SoakBudgets) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    checks = (
        ("physical_window_count", budgets.max_physical_windows),
        ("browser_count", budgets.max_browser_count),
        ("page_count", budgets.max_page_count),
    )
    for field, budget in checks:
        actual = max(_numeric_values(samples, "manager", field), default=0)
        if actual > budget:
            failures.append(_failure(f"manager.{field}", actual, budget, "count exceeded"))
    webengine = max(_numeric_values(samples, "process_tree", "qtwebengine_process_count"), default=0)
    if webengine > budgets.max_qtwebengine_processes:
        failures.append(
            _failure(
                "process_tree.qtwebengine_process_count",
                webengine,
                budgets.max_qtwebengine_processes,
                "owned QtWebEngine child count exceeded",
            )
        )
    return failures


def _cycle_budget_failures(cycles: list[dict[str, Any]], minimum_cycles: int) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if len(cycles) < minimum_cycles:
        failures.append(_failure("cycles.minimum", len(cycles), minimum_cycles, "measured cycle count too low"))
    failed = [cycle.get("cycle_index") for cycle in cycles if cycle.get("status") != "ok"]
    if failed:
        failures.append(_failure("cycles.status", failed, [], "open/close cycle failed"))
    return failures


def _active_after_close_failure(closed_samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [
        sample["cycle_index"]
        for sample in closed_samples
        if (sample.get("manager") or {}).get("active_window_count") != 0
    ]
    return (
        [_failure("manager.active_after_close", active, [], "active window remained after close")]
        if active
        else []
    )


def evaluate_budget(
    report: dict[str, Any],
    budgets: SoakBudgets,
    *,
    minimum_cycles: int,
    stability_window: int,
) -> list[dict[str, Any]]:
    cycles = list(report.get("cycles") or [])
    samples = [sample for cycle in cycles for sample in cycle.get("samples", [])]
    open_samples = [sample for sample in samples if sample.get("phase") == "open"]
    closed_samples = [sample for sample in samples if sample.get("phase") == "closed"]
    failures = _count_budget_failures(samples, budgets)
    failures.extend(_cycle_budget_failures(cycles, minimum_cycles))
    failures.extend(_identity_budget_failures(open_samples, budgets))
    failures.extend(_resource_budget_failures(report, closed_samples, budgets, stability_window))
    failures.extend(_active_after_close_failure(closed_samples))
    return failures


def _identity_budget_failures(
    open_samples: list[dict[str, Any]], budgets: SoakBudgets
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    checks = (
        ("physical_window_id", budgets.max_unique_physical_windows),
        ("browser_id", budgets.max_unique_browsers),
        ("page_id", budgets.max_unique_pages),
    )
    for field, budget in checks:
        identities = _identity_values(open_samples, field)
        if len(identities) != 1 or len(identities) > budget:
            failures.append(_failure(f"reuse.{field}", len(identities), budget, "identity reuse is not stable"))
    missing_render_pids = [sample["cycle_index"] for sample in open_samples if not sample["identities"]["render_process_pid"]]
    if missing_render_pids:
        failures.append(_failure("renderer.pid", missing_render_pids, [], "renderProcessPid was unavailable"))
    return failures


def _resource_budget_failures(
    report: dict[str, Any],
    closed_samples: list[dict[str, Any]],
    budgets: SoakBudgets,
    stability_window: int,
) -> list[dict[str, Any]]:
    warmup = list(report.get("warmup_cycles") or [])
    warmup_closed = [sample for cycle in warmup for sample in cycle.get("samples", []) if sample["phase"] == "closed"]
    baseline_sample = warmup_closed[-1] if warmup_closed else (closed_samples[0] if closed_samples else None)
    if baseline_sample is None or not closed_samples:
        return [_failure("resources.samples", 0, 1, "closed resource samples are missing")]
    failures, trend = _rss_budget_failures(
        baseline_sample,
        closed_samples,
        budgets,
        stability_window,
    )
    child_failures, child_trend = _child_process_budget_failures(
        baseline_sample,
        closed_samples[-1],
        budgets,
    )
    report["trend"] = {**trend, **child_trend}
    return [*failures, *child_failures]


def _rss_budget_failures(
    baseline_sample: dict[str, Any],
    closed_samples: list[dict[str, Any]],
    budgets: SoakBudgets,
    stability_window: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    baseline_rss = float(baseline_sample["process_tree"]["rss_mb"])
    final_rss = float(closed_samples[-1]["process_tree"]["rss_mb"])
    growth = round(final_rss - baseline_rss, 3)
    if growth > budgets.max_tree_rss_growth_mb:
        failures.append(_failure("resources.tree_rss_growth_mb", growth, budgets.max_tree_rss_growth_mb, "RSS grew"))
    tail = closed_samples[-min(stability_window, len(closed_samples)) :]
    tail_rss = _numeric_values(tail, "process_tree", "rss_mb")
    tail_range = round(max(tail_rss) - min(tail_rss), 3)
    if tail_range > budgets.max_tail_rss_range_mb:
        failures.append(_failure("resources.tail_rss_range_mb", tail_range, budgets.max_tail_rss_range_mb, "tail RSS unstable"))
    slope = round(_linear_slope(tail_rss), 6)
    if slope > budgets.max_tail_rss_slope_mb_per_cycle:
        failures.append(
            _failure(
                "resources.tail_rss_slope_mb_per_cycle",
                slope,
                budgets.max_tail_rss_slope_mb_per_cycle,
                "tail RSS trend is positive",
            )
        )
    trend = {
        "baseline_tree_rss_mb": baseline_rss,
        "final_tree_rss_mb": final_rss,
        "tree_rss_growth_mb": growth,
        "stability_window": len(tail),
        "tail_tree_rss_range_mb": tail_range,
        "tail_tree_rss_slope_mb_per_cycle": slope,
    }
    return failures, trend


def _child_process_budget_failures(
    baseline_sample: dict[str, Any],
    final_sample: dict[str, Any],
    budgets: SoakBudgets,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline_children = int(baseline_sample["process_tree"]["qtwebengine_process_count"])
    final_children = int(final_sample["process_tree"]["qtwebengine_process_count"])
    child_growth = final_children - baseline_children
    failures: list[dict[str, Any]] = []
    if child_growth > budgets.max_qtwebengine_process_growth:
        failures.append(
            _failure(
                "resources.qtwebengine_process_growth",
                child_growth,
                budgets.max_qtwebengine_process_growth,
                "owned QtWebEngine child count grew",
            )
        )
    trend = {
        "baseline_qtwebengine_process_count": baseline_children,
        "final_qtwebengine_process_count": final_children,
        "qtwebengine_process_growth": child_growth,
    }
    return failures, trend


def _validate_args(args: argparse.Namespace) -> None:
    positive = ("cycles", "minimum_cycles", "stability_window", "segment_size", "open_timeout_ms", "close_timeout_ms")
    for field in positive:
        if int(getattr(args, field)) <= 0:
            raise ValueError(f"--{field.replace('_', '-')} must be positive")
    if int(args.warmup_cycles) < 0:
        raise ValueError("--warmup-cycles must be non-negative")
    if int(args.minimum_cycles) > int(args.cycles):
        raise ValueError("--minimum-cycles cannot exceed --cycles")
    if int(args.stability_window) > int(args.cycles):
        raise ValueError("--stability-window cannot exceed --cycles")


def _new_report(args: argparse.Namespace, budgets: SoakBudgets) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_type": "kline_webengine_pool_soak",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running",
        "safety": {
            "process_scope": "probe_root_and_recursive_descendants_only",
            "terminates_external_processes": False,
            "global_process_name_kill": False,
        },
        "mode": {
            "platform": platform.system(),
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "provider_mode": args.provider_mode,
            "cycles": args.cycles,
            "minimum_cycles": args.minimum_cycles,
            "warmup_cycles": args.warmup_cycles,
            "stability_window": args.stability_window,
            "segment_size": args.segment_size,
        },
        "budgets": asdict(budgets),
        "warmup_cycles": [],
        "cycles": [],
        "segments": [],
    }


def _execute_soak_cycles(
    harness: SoakHarness,
    args: argparse.Namespace,
    report: dict[str, Any],
    *,
    started_at: float,
    process_sampler: Callable[[], dict[str, Any]],
) -> None:
    roles = (
        ("warmup_cycles", "warmup", args.warmup_cycles),
        ("cycles", "measured", args.cycles),
    )
    for report_field, role, cycle_count in roles:
        for index in range(1, cycle_count + 1):
            report[report_field].append(
                _run_cycle(
                    harness,
                    args,
                    index=index,
                    role=role,
                    started_at=started_at,
                    process_sampler=process_sampler,
                )
            )


def _safe_harness_shutdown(harness: SoakHarness | None) -> dict[str, Any]:
    if harness is None:
        return {"status": "not_started"}
    try:
        return harness.shutdown()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"status": "fail", "error": str(exc)}


def _safe_provider_evidence(harness: SoakHarness | None) -> dict[str, Any]:
    if harness is None:
        return {"status": "not_started"}
    try:
        return harness.provider_evidence()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"status": "fail", "error": str(exc)}


def _shutdown_budget_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    shutdown = dict(report.get("shutdown") or {})
    manager = dict(shutdown.get("manager_shutdown") or {})
    lifecycle = dict(shutdown.get("lifecycle_shutdown") or {})
    post_close = dict(lifecycle.get("post_close") or {})
    checks = (
        ("shutdown.manager_clean", manager.get("clean"), True),
        ("shutdown.active_windows", manager.get("active_windows"), 0),
        ("shutdown.managed_keepers", manager.get("managed_keepers"), 0),
        ("shutdown.webengine_children", post_close.get("webengine_child_count"), 0),
    )
    return [
        _failure(check, actual, expected, "shutdown did not release owned Qt resources")
        for check, actual, expected in checks
        if actual != expected
    ]


def _provider_budget_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = dict(report.get("data_provider") or {})
    return (
        []
        if evidence.get("status") == "ok"
        else [_failure("provider.evidence", evidence, "ok", "provider evidence is incomplete")]
    )


def _finalize_report(
    report: dict[str, Any],
    args: argparse.Namespace,
    budgets: SoakBudgets,
) -> dict[str, Any]:
    report["segments"] = _segment_summary(report["cycles"], max(1, args.segment_size))
    failures = evaluate_budget(
        report,
        budgets,
        minimum_cycles=args.minimum_cycles,
        stability_window=args.stability_window,
    )
    failures.extend(_provider_budget_failures(report))
    failures.extend(_shutdown_budget_failures(report))
    if report.get("error"):
        failures.insert(0, _failure("runtime.error", report["error"], None, "soak aborted"))
    report["budget"] = {"status": "fail" if failures else "ok", "failures": failures}
    report["status"] = report["budget"]["status"]
    return report


def run_soak(
    args: argparse.Namespace,
    *,
    harness_factory: Callable[[argparse.Namespace], SoakHarness] = NativeKLineHarness,
    process_sampler: Callable[[], dict[str, Any]] = collect_owned_process_tree,
) -> dict[str, Any]:
    budgets = _budgets_from_args(args)
    report = _new_report(args, budgets)
    started_at = time.perf_counter()
    harness: SoakHarness | None = None
    try:
        _validate_args(args)
        harness = harness_factory(args)
        report["mode"]["qt_platform"] = os.environ.get("QT_QPA_PLATFORM", "")
        report["setup"] = harness.prepare(timeout_ms=max(40_000, args.open_timeout_ms))
        if report["setup"].get("status") != "ok":
            raise RuntimeError("native KLine WebEngine setup failed")
        _execute_soak_cycles(
            harness,
            args,
            report,
            started_at=started_at,
            process_sampler=process_sampler,
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report["error"] = {"type": exc.__class__.__name__, "message": str(exc)}
    finally:
        report["data_provider"] = _safe_provider_evidence(harness)
        report["shutdown"] = _safe_harness_shutdown(harness)
        report["elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
    return _finalize_report(report, args, budgets)


def _budgets_from_args(args: argparse.Namespace) -> SoakBudgets:
    return SoakBudgets(
        max_physical_windows=args.max_physical_windows,
        max_browser_count=args.max_browser_count,
        max_page_count=args.max_page_count,
        max_unique_physical_windows=args.max_unique_physical_windows,
        max_unique_browsers=args.max_unique_browsers,
        max_unique_pages=args.max_unique_pages,
        max_qtwebengine_processes=args.max_qtwebengine_processes,
        max_qtwebengine_process_growth=args.max_qtwebengine_process_growth,
        max_tree_rss_growth_mb=args.max_tree_rss_growth_mb,
        max_tail_rss_range_mb=args.max_tail_rss_range_mb,
        max_tail_rss_slope_mb_per_cycle=args.max_tail_rss_slope_mb_per_cycle,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native Windows KLine WebEngine 100-cycle pool soak.")
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)
    parser.add_argument("--minimum-cycles", type=int, default=DEFAULT_CYCLES)
    parser.add_argument("--warmup-cycles", type=int, default=DEFAULT_WARMUP_CYCLES)
    parser.add_argument("--stability-window", type=int, default=DEFAULT_STABILITY_WINDOW)
    parser.add_argument("--segment-size", type=int, default=DEFAULT_SEGMENT_SIZE)
    parser.add_argument("--code", default="000001")
    parser.add_argument("--name", default="平安银行")
    parser.add_argument("--switch-code", default="000002")
    parser.add_argument("--switch-name", default="万科A")
    parser.add_argument(
        "--provider-mode",
        choices=("production-local", "offline-synthetic"),
        default="production-local",
    )
    parser.add_argument("--open-timeout-ms", type=int, default=10_000)
    parser.add_argument("--close-timeout-ms", type=int, default=10_000)
    parser.add_argument("--switch-timeout-ms", type=int, default=3_000)
    parser.add_argument("--max-physical-windows", type=int, default=1)
    parser.add_argument("--max-browser-count", type=int, default=1)
    parser.add_argument("--max-page-count", type=int, default=1)
    parser.add_argument("--max-unique-physical-windows", type=int, default=1)
    parser.add_argument("--max-unique-browsers", type=int, default=1)
    parser.add_argument("--max-unique-pages", type=int, default=1)
    parser.add_argument("--max-qtwebengine-processes", type=int, default=8)
    parser.add_argument("--max-qtwebengine-process-growth", type=int, default=0)
    parser.add_argument("--max-tree-rss-growth-mb", type=float, default=48.0)
    parser.add_argument("--max-tail-rss-range-mb", type=float, default=24.0)
    parser.add_argument("--max-tail-rss-slope-mb-per-cycle", type=float, default=0.5)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "tmp" / "kline_webengine_pool_soak.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = run_soak(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
