from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication

from infra.diagnostics.runtime_health import collect_runtime_health
from ui.main_window_qt import MainWindowQT

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def _process_events(app: QApplication, rounds: int = 1, sleep_ms: int = 0) -> None:
    for _ in range(max(0, int(rounds))):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)


def _sample(label: str, window: MainWindowQT | None) -> dict[str, Any]:
    report = collect_runtime_health(window)
    process = report.get("process") or {}
    webengine = report.get("webengine") or {}
    background_tasks = report.get("background_tasks") or {}
    timers = report.get("timers") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "webengine_processes": webengine.get("processes", []),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
    }


def _webengine_count(sample: MappingLike) -> int:
    try:
        return int(sample.get("webengine_child_count") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _wait_for_sample(
    app: QApplication,
    window: MainWindowQT,
    *,
    label: str,
    samples: list[dict[str, Any]],
    timeout_ms: int,
    predicate,
    step_ms: int = 100,
) -> bool:
    deadline = time.perf_counter() + max(1, int(timeout_ms)) / 1000.0
    matched = False
    while time.perf_counter() < deadline:
        _process_events(app, rounds=1)
        sample = _sample(label, window)
        samples.append(sample)
        if predicate(sample):
            matched = True
            break
        time.sleep(max(1, int(step_ms)) / 1000.0)
    _process_events(app, rounds=3, sleep_ms=20)
    return matched


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
    _process_events(app, rounds=15, sleep_ms=30)
    try:
        kline_manager._charts = []
    except (AttributeError, RuntimeError, TypeError):
        pass
    return closed


MappingLike = Any


def evaluate_lifecycle(samples: list[dict[str, Any]], *, opened: bool, blocked: bool, load_events: list[bool]) -> dict:
    counts = [_webengine_count(sample) for sample in samples]
    baseline_count = counts[0] if counts else 0
    final_count = counts[-1] if counts else 0
    max_count = max(counts) if counts else 0
    child_seen = any(count > baseline_count for count in counts[1:]) or (baseline_count > 0 and max_count > 0)
    reclaimed = final_count <= baseline_count
    load_failed = bool(load_events and load_events[-1] is False)
    status = "ok" if opened and not blocked and child_seen and reclaimed and not load_failed else "fail"
    return {
        "status": status,
        "opened": bool(opened),
        "blocked": bool(blocked),
        "webengine_child_seen": bool(child_seen),
        "webengine_child_reclaimed": bool(reclaimed),
        "baseline_webengine_child_count": baseline_count,
        "max_webengine_child_count": max_count,
        "final_webengine_child_count": final_count,
        "load_events": list(load_events),
        "load_failed": load_failed,
    }


def run_smoke(args: argparse.Namespace) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    qt_platform = os.environ.get("QT_QPA_PLATFORM", "")
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "kline_webengine_lifecycle_smoke",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": {
            "native_qt": bool(args.native_qt),
            "qt_platform": qt_platform,
            "allow_offscreen": bool(args.allow_offscreen),
            "code": str(args.code or ""),
            "name": str(args.name or ""),
            "open_timeout_ms": int(args.open_timeout_ms),
            "close_timeout_ms": int(args.close_timeout_ms),
        },
        "samples": [],
        "load_status": {},
    }
    if qt_platform.lower() == "offscreen" and not args.allow_offscreen:
        report["status"] = "skipped"
        report["skip_reason"] = "QtWebEngine lifecycle smoke requires native Qt or --allow-offscreen."
        report["manual_command"] = (
            ".\\.venv\\Scripts\\python.exe scripts\\kline_webengine_lifecycle_smoke.py "
            "--native-qt --output tmp\\kline_webengine_lifecycle_smoke.json"
        )
        return report

    window = MainWindowQT(
        startup_enabled=False,
        background_prewarm=False,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
    )
    opened = False
    blocked = False
    load_events: list[bool] = []
    load_events_before_close: list[bool] = []
    load_signal = None
    load_callback = None
    chart = None
    try:
        _process_events(app, rounds=10, sleep_ms=30)
        report["samples"].append(_sample("baseline", window))

        from ui.components.kline_window_manager import kline_manager

        code_text = str(args.code or "").strip() or "000001"
        name_text = str(args.name or "").strip() or code_text
        row = {
            "code": code_text,
            "name": name_text,
            "\u4ee3\u7801": code_text,
            "\u540d\u79f0": name_text,
        }
        try:
            chart = kline_manager.open_chart(
                window,
                code_text,
                name_text,
                getattr(window, "data_provider", None),
                row,
                [row],
                0,
            )
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            chart = None
            report["open_error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        opened = chart is not None
        blocked = chart is None
        if chart is not None:
            browser = getattr(chart, "browser", None)
            load_signal = getattr(browser, "loadFinished", None)
            if load_signal is not None:
                def _record_load_finished(ok) -> None:
                    load_events.append(bool(ok))

                load_callback = _record_load_finished
                try:
                    load_signal.connect(load_callback)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        report["samples"].append(_sample("after_open_request", window))
        baseline_count = _webengine_count(report["samples"][0])
        if chart is not None:
            _wait_for_sample(
                app,
                window,
                label="wait_after_open",
                samples=report["samples"],
                timeout_ms=args.open_timeout_ms,
                predicate=lambda sample: _webengine_count(sample) > baseline_count or bool(load_events),
            )

        if chart is not None:
            try:
                status_label = getattr(chart, "info_lbl", None)
                report["load_status"]["status_text"] = str(status_label.text() or "") if status_label else ""
            except (AttributeError, RuntimeError, TypeError, ValueError):
                report["load_status"]["status_text"] = ""

        load_events_before_close = list(load_events)
        if load_signal is not None and load_callback is not None:
            try:
                load_signal.disconnect(load_callback)
            except (AttributeError, RuntimeError, TypeError):
                pass

        closed = _close_kline_charts(app)
        report["closed"] = closed
        gc.collect()
        _wait_for_sample(
            app,
            window,
            label="wait_after_close",
            samples=report["samples"],
            timeout_ms=args.close_timeout_ms,
            predicate=lambda sample: _webengine_count(sample) <= baseline_count,
        )
        report["samples"].append(_sample("final", window))
    finally:
        try:
            if chart is not None:
                chart.close()
            window.close()
            window.deleteLater()
            gc.collect()
            _process_events(app, rounds=10, sleep_ms=20)
        except RuntimeError:
            pass

    report["load_status"]["events_before_close"] = list(load_events_before_close)
    report["load_status"]["events"] = list(load_events)
    report["summary"] = evaluate_lifecycle(
        report["samples"],
        opened=opened,
        blocked=blocked,
        load_events=load_events_before_close,
    )
    report["status"] = report["summary"]["status"]
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native Qt K-line WebEngine lifecycle smoke probe.")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--allow-offscreen", action="store_true")
    parser.add_argument("--code", default="000001")
    parser.add_argument("--name", default="\u5e73\u5b89\u94f6\u884c")
    parser.add_argument("--open-timeout-ms", type=int, default=8000)
    parser.add_argument("--close-timeout-ms", type=int, default=8000)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_smoke(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if args.fail_on_error and report.get("status") == "fail":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
