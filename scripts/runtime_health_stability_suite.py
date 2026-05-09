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

from PyQt6.QtWidgets import QApplication

from app.services.ui_runtime_service import background_job_runner as task_manager
from app.services.ui_runtime_service import domain_events as event_bus
from infra.diagnostics.runtime_health import (
    build_runtime_health_trend,
    collect_runtime_health,
    export_runtime_health_report,
)
from scripts.perf_budget_check import check_runtime_health_budget
from ui.main_window_qt import MainWindowQT
from ui.main_window_runtime import finish_f5_reload

DEFAULT_TABS = (
    "watchlist",
    "stock_candidates",
    "lhb",
    "rt_monitor",
    "scan",
    "foreign_block",
    "earnings",
    "fund_holdings",
)


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


def _sample(
    window: MainWindowQT,
    *,
    label: str,
    samples: list[dict],
    exported_paths: list[str],
    export_each_sample: bool,
) -> dict:
    report = collect_runtime_health(window)
    report["label"] = label
    samples.append(report)
    if export_each_sample:
        path = export_runtime_health_report(
            window,
            project_root=PROJECT_ROOT,
            report=report,
        )
        exported_paths.append(str(path))
    return report


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
    for _ in range(max(0, int(cycles))):
        for key in tabs:
            index = _tab_index(workspace, key)
            if index < 0:
                continue
            tab_widget.setCurrentIndex(index)
            _wait_until(app, lambda key=key: _loaded_tab(workspace, key) is not None, timeout_ms=2000)
            _settle(app, settle_ms)
            visited += 1
    return {"status": "ok", "cycles": int(cycles), "visited": visited}


def _cycle_f5(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int) -> dict:
    workspace = getattr(window, "_workspace", None)
    completed = 0
    for _ in range(max(0, int(cycles))):
        finish_f5_reload(window, count=123, elapsed=1.23, event_bus=event_bus)
        _wait_until(
            app,
            lambda: getattr(getattr(workspace, "_f5_refresh_scheduler", None), "is_running", lambda: False)()
            is False,
            timeout_ms=6000,
        )
        _settle(app, settle_ms)
        completed += 1
    return {"status": "ok", "cycles": completed}


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
    code_text = str(code or "").strip() or "000001"
    name_text = str(name or "").strip() or code_text
    for _ in range(max(0, int(cycles))):
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
        closed += _close_kline_charts(app)
        gc.collect()
        _settle(app, settle_ms)
    return {"status": "ok", "cycles": int(cycles), "opened": opened, "closed": closed, "blocked": blocked}


def _apply_mode_defaults(args: argparse.Namespace) -> argparse.Namespace:
    long_mode = args.mode == "long"
    if args.idle_seconds is None:
        args.idle_seconds = 1800 if long_mode else 5
    if args.tab_cycles is None:
        args.tab_cycles = 2 if long_mode else 1
    if args.f5_cycles is None:
        args.f5_cycles = 2 if long_mode else 1
    if args.quote_cycles is None:
        args.quote_cycles = 2 if long_mode else 1
    if args.kline_cycles is None:
        args.kline_cycles = 1 if long_mode else 0
    return args


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
            "tab_cycles": int(args.tab_cycles),
            "f5_cycles": int(args.f5_cycles),
            "quote_cycles": int(args.quote_cycles),
            "kline_cycles": int(args.kline_cycles),
            "tabs": list(tabs),
        },
        "individual_report_paths": exported_paths,
    }

    window = MainWindowQT(
        startup_enabled=bool(args.startup_enabled),
        background_prewarm=bool(args.background_prewarm),
        kline_prewarm_enabled=bool(args.kline_prewarm_enabled),
        central_quotes_enabled=bool(args.central_quotes_enabled),
    )
    try:
        _settle(app, args.startup_settle_ms)
        _sample(
            window,
            label="startup",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
        )

        for second in range(max(0, int(args.idle_seconds))):
            _settle(app, 1000)
            if (second + 1) % max(1, int(args.sample_every_seconds)) == 0:
                _sample(
                    window,
                    label=f"idle:{second + 1}s",
                    samples=samples,
                    exported_paths=exported_paths,
                    export_each_sample=not args.no_export_samples,
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
        )

        report["f5_cycle"] = _cycle_f5(window, app, cycles=args.f5_cycles, settle_ms=args.cycle_settle_ms)
        _sample(
            window,
            label="after_f5_cycle",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
        )

        report["quote_cycle"] = _cycle_quotes(window, app, cycles=args.quote_cycles, settle_ms=args.cycle_settle_ms)
        _sample(
            window,
            label="after_quote_cycle",
            samples=samples,
            exported_paths=exported_paths,
            export_each_sample=not args.no_export_samples,
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
    failures = check_runtime_health_budget(report)
    report["budget"] = {
        "status": "fail" if failures else "ok",
        "failures": failures,
    }
    report["status"] = report["budget"]["status"]
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime health and long-running stability suite.")
    parser.add_argument("--mode", choices=("short", "long"), default="short")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--startup-enabled", action="store_true")
    parser.add_argument("--background-prewarm", action="store_true")
    parser.add_argument("--kline-prewarm-enabled", action="store_true")
    parser.add_argument("--central-quotes-enabled", action="store_true")
    parser.add_argument("--startup-settle-ms", type=int, default=300)
    parser.add_argument("--cycle-settle-ms", type=int, default=120)
    parser.add_argument("--sample-every-seconds", type=int, default=5)
    parser.add_argument("--idle-seconds", type=int, default=None)
    parser.add_argument("--tab-cycles", type=int, default=None)
    parser.add_argument("--f5-cycles", type=int, default=None)
    parser.add_argument("--quote-cycles", type=int, default=None)
    parser.add_argument("--kline-cycles", type=int, default=None)
    parser.add_argument("--kline-code", default="000001")
    parser.add_argument("--kline-name", default="平安银行")
    parser.add_argument("--allow-offscreen-kline", action="store_true")
    parser.add_argument("--tabs", nargs="*", default=list(DEFAULT_TABS))
    parser.add_argument("--no-export-samples", action="store_true")
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
