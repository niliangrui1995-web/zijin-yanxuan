from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from pathlib import Path

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from scripts.perf_memory_probe import _close_kline_charts, _settle, collect_process_snapshot
from ui.components.kline_window_manager import kline_manager
from ui.main_window_qt import MainWindowQT


def _sample(samples: list[dict], label: str) -> None:
    samples.append(collect_process_snapshot(label))


def _values(samples: list[dict], key: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = (sample.get("main") or {}).get(key)
        if value is not None:
            values.append(float(value))
    return values


def _trend(samples: list[dict], *, threshold_mb: float) -> dict:
    stable_samples = _stable_growth_samples(samples)
    growth_samples = stable_samples if len(stable_samples) >= 3 else samples
    rss_values = _values(growth_samples, "rss_mb")
    private_values = _values(growth_samples, "private_mb")
    thread_values = _values(growth_samples, "thread_count")

    def _one(values: list[float], threshold: float) -> dict:
        if len(values) < 2:
            return {"count": len(values), "status": "insufficient"}
        deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
        tail = values[max(0, len(values) // 2) :]
        net_delta = values[-1] - values[0]
        tail_range = (max(tail) - min(tail)) if tail else 0.0
        return {
            "count": len(values),
            "first": round(values[0], 1),
            "last": round(values[-1], 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "net_delta": round(net_delta, 1),
            "avg_step_delta": round(sum(deltas) / len(deltas), 3),
            "tail_range": round(tail_range, 1),
            "sustained_growth": bool(net_delta > threshold and tail_range > threshold / 2.0),
            "status": "warn" if net_delta > threshold and tail_range > threshold / 2.0 else "ok",
        }

    return {
        "rss": _one(rss_values, threshold_mb),
        "private": _one(private_values, threshold_mb),
        "threads": _one(thread_values, 16.0),
        "growth_basis": "stable_close_samples" if growth_samples is stable_samples else "all_samples",
        "growth_sample_count": len(growth_samples),
        "threshold_mb": float(threshold_mb),
    }


def _stable_growth_samples(samples: list[dict]) -> list[dict]:
    stable: list[dict] = []
    for sample in samples:
        label = str(sample.get("label", "") or "")
        if label.endswith("_close") or label == "after_window_close":
            stable.append(sample)
    return stable


def _cycle_tabs(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int, samples: list[dict]) -> int:
    workspace = getattr(window, "_workspace", None)
    tabs = getattr(workspace, "tabs", None)
    ensure_tab_loaded = getattr(workspace, "ensure_tab_loaded", None)
    if workspace is None or tabs is None:
        return 0
    visited = 0
    for cycle in range(max(0, int(cycles))):
        for index in range(int(tabs.count())):
            if callable(ensure_tab_loaded):
                ensure_tab_loaded(index, reason="soak_leak_probe")
            tabs.setCurrentIndex(index)
            _settle(app, settle_ms)
            visited += 1
        _sample(samples, f"tab_cycle_{cycle + 1}")
    return visited


def _cycle_refresh(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int, samples: list[dict]) -> int:
    workspace = getattr(window, "_workspace", None)
    refresh_all = getattr(workspace, "refresh_all_tabs_after_f5", None)
    if not callable(refresh_all):
        return 0
    completed = 0
    for cycle in range(max(0, int(cycles))):
        refresh_all()
        _settle(app, settle_ms)
        gc.collect()
        completed += 1
        _sample(samples, f"refresh_cycle_{cycle + 1}")
    return completed


def _cycle_gbbq(window: MainWindowQT, *, cycles: int, code: str, samples: list[dict]) -> int:
    provider = getattr(window, "data_provider", None)
    loader = getattr(provider, "_load_local_gbbq_for_code", None)
    if not callable(loader):
        return 0
    completed = 0
    for cycle in range(max(0, int(cycles))):
        loader(code)
        gc.collect()
        completed += 1
        _sample(samples, f"gbbq_cycle_{cycle + 1}")
    return completed


def _cycle_kline(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycles: int,
    settle_ms: int,
    code: str,
    name: str,
    allow_offscreen: bool,
    samples: list[dict],
) -> dict:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and not allow_offscreen:
        return {"cycles": 0, "skipped": True, "reason": "offscreen_kline_disabled"}

    opened = 0
    closed = 0
    for cycle in range(max(0, int(cycles))):
        chart = kline_manager.open_chart(
            window,
            code,
            name,
            getattr(window, "data_provider", None),
            {"code": code, "name": name},
            [{"code": code, "name": name}],
            0,
        )
        if chart is not None:
            opened += 1
        _settle(app, settle_ms)
        _sample(samples, f"kline_cycle_{cycle + 1}_open")
        closed += _close_kline_charts(app)
        gc.collect()
        _settle(app, settle_ms)
        _sample(samples, f"kline_cycle_{cycle + 1}_close")
    return {"cycles": int(cycles), "opened": opened, "closed": closed}


def run_soak(args: argparse.Namespace) -> dict:
    if int(args.kline_cycles) > 0:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    samples: list[dict] = []
    _sample(samples, "process_start")
    window = MainWindowQT(
        startup_enabled=args.startup_enabled,
        background_prewarm=args.background_prewarm,
        kline_prewarm_enabled=args.kline_prewarm_enabled,
        central_quotes_enabled=args.central_quotes_enabled,
        restore_last_tab_enabled=False,
    )
    _settle(app, args.settle_ms)
    _sample(samples, "after_window")

    idle_ticks = 0
    for second in range(max(0, int(args.idle_seconds))):
        _settle(app, 1000)
        idle_ticks += 1
        if (second + 1) % max(1, int(args.sample_every_seconds)) == 0:
            _sample(samples, f"idle_{second + 1}s")

    tab_visits = _cycle_tabs(window, app, cycles=args.tab_cycles, settle_ms=args.settle_ms, samples=samples)
    refresh_cycles = _cycle_refresh(window, app, cycles=args.refresh_cycles, settle_ms=args.settle_ms, samples=samples)
    gbbq_cycles = _cycle_gbbq(window, cycles=args.gbbq_cycles, code=args.gbbq_code, samples=samples)
    kline_result = _cycle_kline(
        window,
        app,
        cycles=args.kline_cycles,
        settle_ms=args.settle_ms,
        code=args.kline_code,
        name=args.kline_name,
        allow_offscreen=args.allow_offscreen_kline,
        samples=samples,
    )

    window.close()
    _settle(app, args.settle_ms)
    window.deleteLater()
    gc.collect()
    _settle(app, args.settle_ms)
    _sample(samples, "after_window_close")

    return {
        "mode": {
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "native_qt": bool(args.native_qt),
            "startup_enabled": bool(args.startup_enabled),
            "background_prewarm": bool(args.background_prewarm),
            "kline_prewarm_enabled": bool(args.kline_prewarm_enabled),
            "central_quotes_enabled": bool(args.central_quotes_enabled),
            "idle_seconds": int(args.idle_seconds),
            "tab_cycles": int(args.tab_cycles),
            "refresh_cycles": int(args.refresh_cycles),
            "gbbq_cycles": int(args.gbbq_cycles),
            "kline_cycles": int(args.kline_cycles),
            "settle_ms": int(args.settle_ms),
        },
        "result": {
            "idle_ticks": idle_ticks,
            "tab_visits": tab_visits,
            "refresh_cycles": refresh_cycles,
            "gbbq_cycles": gbbq_cycles,
            "kline": kline_result,
        },
        "trend": _trend(samples, threshold_mb=args.growth_threshold_mb),
        "samples": samples,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repeatable soak/leak checks for Zijin Research UI.")
    parser.add_argument("--native-qt", action="store_true", help="Do not force QT_QPA_PLATFORM=offscreen.")
    parser.add_argument("--startup-enabled", action="store_true", help="Run normal startup timers.")
    parser.add_argument("--background-prewarm", action="store_true", help="Enable workspace background tab prewarm.")
    parser.add_argument("--kline-prewarm-enabled", action="store_true", help="Enable K-line prewarm path.")
    parser.add_argument("--central-quotes-enabled", action="store_true", help="Enable central quotes timer.")
    parser.add_argument("--idle-seconds", type=int, default=0, help="Idle duration before active cycles.")
    parser.add_argument("--sample-every-seconds", type=int, default=5, help="Idle sample interval.")
    parser.add_argument("--tab-cycles", type=int, default=0, help="Repeatedly switch through all workspace tabs.")
    parser.add_argument("--refresh-cycles", type=int, default=0, help="Repeatedly run post-F5 table refresh hooks.")
    parser.add_argument("--gbbq-cycles", type=int, default=0, help="Repeatedly access single-code gbbq cache.")
    parser.add_argument("--gbbq-code", default="000001", help="Stock code used by --gbbq-cycles.")
    parser.add_argument("--kline-cycles", type=int, default=0, help="Repeatedly open and close K-line windows.")
    parser.add_argument("--kline-code", default="000001", help="Stock code used by --kline-cycles.")
    parser.add_argument("--kline-name", default="Ping An Bank", help="Stock name used by --kline-cycles.")
    parser.add_argument("--allow-offscreen-kline", action="store_true", help="Attempt K-line cycles offscreen.")
    parser.add_argument("--settle-ms", type=int, default=250, help="Wait/process events after each action.")
    parser.add_argument(
        "--growth-threshold-mb", type=float, default=48.0, help="Warn when net growth stays above this."
    )
    parser.add_argument("--output", default="", help="Write the JSON report to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_soak(args)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
