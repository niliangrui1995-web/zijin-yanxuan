from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_service import background_job_runner as task_manager
from infra.diagnostics.runtime_health import (
    build_runtime_health_trend,
    collect_runtime_health,
    export_runtime_health_report,
)
from infra.diagnostics.ui_stall_probe import get_ui_stall_probe
from scripts.perf_budget_check import check_runtime_health_budget
from ui.main_window_qt import MainWindowQT
from ui.main_window_runtime import finish_f5_reload

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

DEFAULT_TABS = (
    "watchlist",
    "asian_market",
    "na_daily",
    "stock_candidates",
    "ai_industry_chain",
    "lhb",
    "rt_monitor",
    "scan",
    "foreign_block",
    "earnings",
    "fund_holdings",
)

SOAK_MODE_MINUTES = {
    "short": 0,
    "long": 30,
    "soak30": 30,
    "soak60": 60,
}
POST_TAB_IDLE_TIMEOUT_MS = 5000


def _process_events(app: QApplication, rounds: int = 1, sleep_ms: int = 0) -> None:
    for _ in range(max(0, int(rounds))):
        app.processEvents()
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)


def _settle(app: QApplication, settle_ms: int) -> None:
    if settle_ms <= 0:
        _process_events(app, rounds=3)
        return
    _process_events(app, rounds=max(1, int(settle_ms) // 50), sleep_ms=50)


def _wait_until(app: QApplication, predicate, *, timeout_ms: int, step_ms: int = 25) -> bool:
    deadline = time.perf_counter() + max(1, int(timeout_ms)) / 1000.0
    while time.perf_counter() < deadline:
        _process_events(app, rounds=1)
        if predicate():
            return True
        time.sleep(max(1, int(step_ms)) / 1000.0)
    _process_events(app, rounds=2)
    return bool(predicate())


def _active_background_task_count() -> int:
    try:
        return int(getattr(task_manager, "active_count", 0) or 0)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return 0


def _wait_for_background_tasks_idle(
    app: QApplication,
    *,
    timeout_ms: int,
    step_ms: int = 50,
) -> dict:
    before = _active_background_task_count()
    if int(timeout_ms) <= 0:
        return {"status": "skipped", "timeout_ms": int(timeout_ms), "active_before": before, "active_after": before}
    idle = _wait_until(app, lambda: _active_background_task_count() == 0, timeout_ms=timeout_ms, step_ms=step_ms)
    after = _active_background_task_count()
    return {
        "status": "ok" if idle else "timeout",
        "timeout_ms": int(timeout_ms),
        "active_before": before,
        "active_after": after,
    }


def _reset_ui_stall_snapshot() -> bool:
    stall_probe = get_ui_stall_probe()
    if stall_probe is None:
        return False
    stall_probe.reset_stall_snapshot()
    return True


def _tab_specs(workspace) -> list[dict]:
    tab_specs = getattr(workspace, "tab_specs", None)
    return list(tab_specs() or []) if callable(tab_specs) else []


def _tab_index(workspace, key: str) -> int:
    key_text = str(key or "").strip()
    for index, spec in enumerate(_tab_specs(workspace)):
        if str(spec.get("key") or "").strip() == key_text:
            return index
    return -1


def _loaded_tab(workspace, key: str):
    getter = getattr(workspace, "get_loaded_tab", None)
    return getter(key) if callable(getter) else None


def _ensure_probe_tab_loaded(workspace, key: str, index: int):
    if _loaded_tab(workspace, key) is not None:
        return _loaded_tab(workspace, key)
    ensure_tab_loaded = getattr(workspace, "ensure_tab_loaded", None)
    if not callable(ensure_tab_loaded):
        return None
    try:
        return ensure_tab_loaded(key, reason="perf_memory_probe")
    except TypeError:
        return ensure_tab_loaded(index)


def _sample(
    window: MainWindowQT,
    *,
    label: str,
    samples: list[dict],
    exported_paths: list[str],
    export_each_sample: bool,
    sample_output_dir: Path | None = None,
) -> dict:
    report = collect_runtime_health(window)
    report["label"] = label
    samples.append(report)
    if export_each_sample:
        if sample_output_dir is not None:
            sample_output_dir.mkdir(parents=True, exist_ok=True)
            safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label)
            path = sample_output_dir / f"runtime_health_sample_{len(samples):04d}_{safe_label}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path = export_runtime_health_report(
                window,
                project_root=PROJECT_ROOT,
                report=report,
            )
        exported_paths.append(str(path))
    return report


def _runtime_health_sample_summary(label: str, window: MainWindowQT) -> dict:
    report = collect_runtime_health(window)
    process = report.get("process") or {}
    webengine = report.get("webengine") or {}
    background_tasks = report.get("background_tasks") or {}
    timers = report.get("timers") or {}
    event_bus = report.get("event_bus") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "private_mb": process.get("private_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
        "event_receiver_count": event_bus.get("total_receivers"),
    }


def _trend_one(values: list[float]) -> dict:
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


def _tail_range(values: list[float], tail_count: int = 3) -> float:
    tail = values[-max(1, int(tail_count)) :]
    return round(max(tail) - min(tail), 3) if tail else 0.0


def _as_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _runtime_summary_from_health(sample: dict, label: str) -> dict:
    process = sample.get("process") or {}
    timers = sample.get("timers") or {}
    event_bus = sample.get("event_bus") or {}
    webengine = sample.get("webengine") or {}
    background_tasks = sample.get("background_tasks") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "private_mb": process.get("private_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
        "event_receiver_count": event_bus.get("total_receivers"),
    }


def _build_budget_trend(samples: list[dict], kline_cycle: dict | None) -> dict:
    trend = build_runtime_health_trend(samples)
    cycle_samples = list((kline_cycle or {}).get("cycle_samples") or [])
    close_samples = [sample for sample in cycle_samples if str(sample.get("label") or "").endswith(":after_close")]
    if close_samples and samples:
        return _overlay_budget_trend(
            trend,
            close_samples + [_runtime_summary_from_health(samples[-1], "final_runtime_health")],
            basis="post_kline_close_samples",
        )

    tail_samples = _post_workload_tail_summaries(samples)
    if len(tail_samples) < 2:
        return trend
    return _overlay_budget_trend(trend, tail_samples, basis="tail_runtime_health_samples")


def _post_workload_tail_summaries(samples: list[dict]) -> list[dict]:
    stable_labels = {"after_tab_cycle", "after_f5_cycle", "after_quote_cycle", "final"}
    selected = [
        _runtime_summary_from_health(sample, str(sample.get("label") or "runtime_health"))
        for sample in samples
        if str(sample.get("label") or "") in stable_labels
    ]
    if len(selected) >= 2:
        return selected[-3:]
    return [
        _runtime_summary_from_health(sample, str(sample.get("label") or "runtime_health")) for sample in samples[-3:]
    ]


def _overlay_budget_trend(trend: dict, summary_samples: list[dict], *, basis: str) -> dict:
    def _values(key: str) -> list[float]:
        values: list[float] = []
        for sample in summary_samples:
            value = _as_float(sample.get(key))
            if value is not None:
                values.append(value)
        return values

    trend = dict(trend)
    for output_key, sample_key in (
        ("rss_mb", "rss_mb"),
        ("private_mb", "private_mb"),
        ("background_tasks", "background_task_count"),
        ("threads", "thread_count"),
        ("active_timers", "active_timer_count"),
        ("total_timers", "total_timer_count"),
        ("event_receivers", "event_receiver_count"),
        ("webengine_children", "webengine_child_count"),
        ("webengine_rss_mb", "webengine_rss_mb"),
        ("webengine_private_mb", "webengine_private_mb"),
    ):
        values = _values(sample_key)
        stats = _trend_one(values)
        if output_key in {"rss_mb", "private_mb"}:
            stats["tail_range"] = _tail_range(values)
        trend[output_key] = {
            **stats,
            "basis": basis,
        }
    return trend


def _cycle_tabs(
    window: MainWindowQT,
    app: QApplication,
    tabs: tuple[str, ...],
    *,
    cycles: int,
    settle_ms: int,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    tab_widget = getattr(workspace, "tabs", None)
    if workspace is None or tab_widget is None:
        return {"status": "skipped", "reason": "workspace_unavailable", "visited": 0}

    visited = 0
    timings: list[dict] = []
    for cycle_index in range(max(0, int(cycles))):
        for key in tabs:
            index = _tab_index(workspace, key)
            if index < 0:
                timings.append(
                    {
                        "cycle": cycle_index + 1,
                        "key": key,
                        "status": "missing",
                        "elapsed_ms": 0.0,
                    }
                )
                continue
            started = time.perf_counter()
            _ensure_probe_tab_loaded(workspace, key, index)
            tab_widget.setCurrentIndex(index)
            loaded = _wait_until(app, lambda key=key: _loaded_tab(workspace, key) is not None, timeout_ms=2000)
            _settle(app, settle_ms)
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            timings.append(
                {
                    "cycle": cycle_index + 1,
                    "key": key,
                    "status": "ok" if loaded else "timeout",
                    "elapsed_ms": elapsed_ms,
                }
            )
            visited += 1
    return {"status": "ok", "cycles": int(cycles), "visited": visited, "tabs": timings}


def _cycle_f5(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int) -> dict:
    workspace = getattr(window, "_workspace", None)
    completed = 0
    timings: list[dict] = []
    for _ in range(max(0, int(cycles))):
        started = time.perf_counter()
        finish_f5_reload(window, count=123, elapsed=1.23, event_bus=event_bus)
        settled = _wait_until(
            app,
            lambda: getattr(getattr(workspace, "_f5_refresh_scheduler", None), "is_running", lambda: False)() is False,
            timeout_ms=6000,
        )
        _settle(app, settle_ms)
        completed += 1
        timings.append(
            {
                "cycle": completed,
                "status": "ok" if settled else "timeout",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        )
    total_elapsed_ms = round(sum(float(item.get("elapsed_ms") or 0.0) for item in timings), 3)
    return {"status": "ok", "cycles": completed, "total_elapsed_ms": total_elapsed_ms, "cycle_timings": timings}


def _cycle_quotes(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int) -> dict:
    central = getattr(window, "central_quotes_svc", None)
    trigger_fetch = getattr(central, "_trigger_fetch", None)
    if not callable(trigger_fetch):
        return {"status": "skipped", "reason": "central_quotes_unavailable", "cycles": 0}

    completed = 0
    for _ in range(max(0, int(cycles))):
        trigger_fetch()
        _wait_until(app, lambda: int(getattr(task_manager, "active_count", 0) or 0) == 0, timeout_ms=5000)
        _settle(app, settle_ms)
        completed += 1
    return {"status": "ok", "cycles": completed}


def _close_kline_charts(app: QApplication) -> int:
    from ui.components.kline_window_manager import kline_manager

    charts = list(getattr(kline_manager, "_charts", []) or [])
    closed = 0
    for chart in charts:
        try:
            chart.close()
            closed += 1
        except RuntimeError:
            pass
    _process_events(app, rounds=12, sleep_ms=20)
    try:
        kline_manager._charts = []
    except (AttributeError, RuntimeError, TypeError):
        pass
    return closed


def _cycle_kline(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycles: int,
    settle_ms: int,
    code: str,
    name: str,
    allow_offscreen: bool,
) -> dict:
    if cycles <= 0:
        return {"status": "skipped", "reason": "disabled", "cycles": 0}
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and not allow_offscreen:
        return {"status": "skipped", "reason": "offscreen_webengine_guard", "cycles": 0}

    from ui.components.kline_window_manager import kline_manager

    opened = 0
    closed = 0
    blocked = 0
    cycle_samples: list[dict] = []
    code_text = str(code or "").strip() or "000001"
    name_text = str(name or "").strip() or code_text
    for cycle_index in range(max(0, int(cycles))):
        cycle_samples.append(_runtime_health_sample_summary(f"kline_cycle_{cycle_index + 1}:before_open", window))
        chart = kline_manager.open_chart(
            window,
            code_text,
            name_text,
            getattr(window, "data_provider", None),
            {"代码": code_text, "名称": name_text},
            [{"代码": code_text, "名称": name_text}],
            0,
        )
        if chart is None:
            blocked += 1
        else:
            opened += 1
        _settle(app, settle_ms)
        cycle_samples.append(_runtime_health_sample_summary(f"kline_cycle_{cycle_index + 1}:after_open", window))
        closed += _close_kline_charts(app)
        gc.collect()
        _settle(app, settle_ms)
        cycle_samples.append(_runtime_health_sample_summary(f"kline_cycle_{cycle_index + 1}:after_close", window))
    final_count = (cycle_samples[-1] if cycle_samples else {}).get("webengine_child_count")
    return {
        "status": "ok",
        "cycles": int(cycles),
        "opened": opened,
        "closed": closed,
        "blocked": blocked,
        "cycle_samples": cycle_samples,
        "final_webengine_child_count": final_count,
    }


def _apply_mode_defaults(args: argparse.Namespace) -> argparse.Namespace:
    long_mode = args.mode in {"long", "soak30", "soak60"}
    if args.idle_minutes is not None:
        args.idle_seconds = int(max(0.0, float(args.idle_minutes)) * 60)
    if args.idle_seconds is None:
        args.idle_seconds = SOAK_MODE_MINUTES.get(args.mode, 0) * 60 if long_mode else 5
    if args.tab_cycles is None:
        args.tab_cycles = 2 if long_mode else 1
    if args.f5_cycles is None:
        args.f5_cycles = 2 if long_mode else 1
    if args.quote_cycles is None:
        args.quote_cycles = 2 if long_mode else 1
    if args.kline_cycles is None:
        args.kline_cycles = 1 if long_mode else 0
    return args


def _build_startup_lazy_budget(report: dict, samples: list[dict]) -> dict:
    mode = report.get("mode") or {}
    tab_cycle = report.get("tab_cycle") or {}
    f5_cycle = report.get("f5_cycle") or {}
    tab_timings = list(tab_cycle.get("tabs") or [])
    first_tabs: dict[str, dict] = {}
    for item in tab_timings:
        key = str(item.get("key") or "").strip()
        if key and key not in first_tabs:
            first_tabs[key] = dict(item)

    f5_timings = list(f5_cycle.get("cycle_timings") or [])
    final_sample = samples[-1] if samples else {}
    final_background = final_sample.get("background_tasks") or {}
    final_process = final_sample.get("process") or {}
    final_timers = final_sample.get("timers") or {}

    return {
        "startup": {
            "main_window_ready_ms": report.get("startup_ready_ms"),
            "startup_settle_ms": mode.get("startup_settle_ms"),
            "startup_enabled": mode.get("startup_enabled"),
            "background_prewarm": mode.get("background_prewarm"),
        },
        "tab_first_open": {
            "tabs": list(first_tabs.values()),
            "max_elapsed_ms": max(
                [float(item.get("elapsed_ms") or 0.0) for item in first_tabs.values()],
                default=0.0,
            ),
        },
        "f5_quiet": {
            "cycles": f5_timings,
            "total_elapsed_ms": f5_cycle.get("total_elapsed_ms"),
            "max_cycle_elapsed_ms": max(
                [float(item.get("elapsed_ms") or 0.0) for item in f5_timings],
                default=0.0,
            ),
            "cycle_settle_ms": mode.get("cycle_settle_ms"),
        },
        "background_settle": {
            "final_background_task_count": final_background.get("count"),
            "final_thread_count": final_process.get("thread_count"),
            "final_active_timer_count": final_timers.get("active"),
            "final_sample_label": final_sample.get("label", ""),
        },
    }


def run_suite(args: argparse.Namespace) -> dict:
    args = _apply_mode_defaults(args)
    app = QApplication.instance() or QApplication(sys.argv)
    tabs = tuple(dict.fromkeys(args.tabs or DEFAULT_TABS))
    samples: list[dict] = []
    exported_paths: list[str] = []
    started = time.perf_counter()
    report: dict = {
        "schema_version": 1,
        "report_type": "runtime_health_stability_suite",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": {
            "mode": args.mode,
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "native_qt": bool(args.native_qt),
            "startup_enabled": bool(args.startup_enabled),
            "background_prewarm": bool(args.background_prewarm),
            "central_quotes_enabled": bool(args.central_quotes_enabled),
            "idle_seconds": int(args.idle_seconds),
            "idle_minutes": round(float(args.idle_seconds) / 60.0, 3),
            "sample_every_seconds": int(args.sample_every_seconds),
            "startup_settle_ms": int(args.startup_settle_ms),
            "cycle_settle_ms": int(args.cycle_settle_ms),
            "post_tab_idle_timeout_ms": int(args.post_tab_idle_timeout_ms),
            "tab_cycles": int(args.tab_cycles),
            "f5_cycles": int(args.f5_cycles),
            "quote_cycles": int(args.quote_cycles),
            "kline_cycles": int(args.kline_cycles),
            "tabs": list(tabs),
            "sample_output_dir": str(args.sample_output_dir or ""),
        },
        "individual_report_paths": exported_paths,
    }

    window = MainWindowQT(
        startup_enabled=bool(args.startup_enabled),
        background_prewarm=bool(args.background_prewarm),
        kline_prewarm_enabled=bool(args.kline_prewarm_enabled),
        central_quotes_enabled=bool(args.central_quotes_enabled),
        restore_last_tab_enabled=False,
    )
    try:
        _settle(app, args.startup_settle_ms)
        report["startup_ready_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        _sample(
            window,
            label="startup",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
            sample_output_dir=args.sample_output_dir,
        )
        _reset_ui_stall_snapshot()

        for second in range(max(0, int(args.idle_seconds))):
            _settle(app, 1000)
            if (second + 1) % max(1, int(args.sample_every_seconds)) == 0:
                _sample(
                    window,
                    label=f"idle:{second + 1}s",
                    samples=samples,
                    exported_paths=exported_paths,
                    export_each_sample=not args.no_export_samples,
                    sample_output_dir=args.sample_output_dir,
                )

        report["tab_cycle"] = _cycle_tabs(
            window,
            app,
            tabs,
            cycles=args.tab_cycles,
            settle_ms=args.cycle_settle_ms,
        )
        _sample(
            window,
            label="after_tab_cycle",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
            sample_output_dir=args.sample_output_dir,
        )
        report["post_tab_idle"] = _wait_for_background_tasks_idle(
            app,
            timeout_ms=args.post_tab_idle_timeout_ms,
        )
        _settle(app, args.cycle_settle_ms)
        _reset_ui_stall_snapshot()

        report["f5_cycle"] = _cycle_f5(window, app, cycles=args.f5_cycles, settle_ms=args.cycle_settle_ms)
        _sample(
            window,
            label="after_f5_cycle",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
            sample_output_dir=args.sample_output_dir,
        )

        report["quote_cycle"] = _cycle_quotes(window, app, cycles=args.quote_cycles, settle_ms=args.cycle_settle_ms)
        _sample(
            window,
            label="after_quote_cycle",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
            sample_output_dir=args.sample_output_dir,
        )

        report["kline_cycle"] = _cycle_kline(
            window,
            app,
            cycles=args.kline_cycles,
            settle_ms=args.cycle_settle_ms,
            code=args.kline_code,
            name=args.kline_name,
            allow_offscreen=bool(args.allow_offscreen_kline),
        )
        gc.collect()
        _settle(app, args.cycle_settle_ms)
        _sample(
            window,
            label="final",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
            sample_output_dir=args.sample_output_dir,
        )
    finally:
        try:
            window.close()
            _settle(app, 200)
            window.deleteLater()
            gc.collect()
            _settle(app, 100)
        except RuntimeError:
            pass

    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    report["runtime_health_samples"] = samples
    report["trend"] = build_runtime_health_trend(samples)
    report["budget_trend"] = _build_budget_trend(samples, report.get("kline_cycle"))
    report["startup_lazy_budget"] = _build_startup_lazy_budget(report, samples)
    failures = check_runtime_health_budget(report)
    report["budget"] = {
        "status": "fail" if failures else "ok",
        "failures": failures,
    }
    report["status"] = report["budget"]["status"]
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime health and long-running stability suite.")
    parser.add_argument("--mode", choices=("short", "long", "soak30", "soak60"), default="short")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--startup-enabled", action="store_true")
    parser.add_argument("--background-prewarm", action="store_true")
    parser.add_argument("--kline-prewarm-enabled", action="store_true")
    parser.add_argument("--central-quotes-enabled", action="store_true")
    parser.add_argument("--startup-settle-ms", type=int, default=300)
    parser.add_argument("--cycle-settle-ms", type=int, default=120)
    parser.add_argument("--post-tab-idle-timeout-ms", type=int, default=POST_TAB_IDLE_TIMEOUT_MS)
    parser.add_argument("--sample-every-seconds", type=int, default=5)
    parser.add_argument("--idle-seconds", type=int, default=None)
    parser.add_argument("--idle-minutes", type=float, default=None)
    parser.add_argument("--tab-cycles", type=int, default=None)
    parser.add_argument("--f5-cycles", type=int, default=None)
    parser.add_argument("--quote-cycles", type=int, default=None)
    parser.add_argument("--kline-cycles", type=int, default=None)
    parser.add_argument("--kline-code", default="000001")
    parser.add_argument("--kline-name", default="平安银行")
    parser.add_argument("--allow-offscreen-kline", action="store_true")
    parser.add_argument("--tabs", nargs="*", default=list(DEFAULT_TABS))
    parser.add_argument("--no-export-samples", action="store_true")
    parser.add_argument("--sample-output-dir", type=Path, default=None)
    parser.add_argument("--fail-on-budget", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_suite(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if args.fail_on_budget and report.get("status") != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
