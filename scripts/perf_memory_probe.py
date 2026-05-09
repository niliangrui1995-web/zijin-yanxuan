from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psutil
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None

from PyQt6.QtWidgets import QApplication

from ui.main_window_qt import MainWindowQT


def _rss_mb() -> float | None:
    if psutil is None:
        return None
    return round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)


def _process_events(app: QApplication, rounds: int = 8, sleep_ms: int = 0) -> None:
    for _ in range(max(0, int(rounds))):
        app.processEvents()
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)


def _settle(app: QApplication, settle_ms: int) -> None:
    if settle_ms <= 0:
        _process_events(app, rounds=3)
        return
    _process_events(app, rounds=max(1, int(settle_ms) // 50), sleep_ms=50)


def _measure(label: str, fn):
    before = _rss_mb()
    started = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    after = _rss_mb()
    return {
        "label": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "rss_before_mb": before,
        "rss_after_mb": after,
        "rss_delta_mb": None if before is None or after is None else round(after - before, 1),
        "result": result,
    }


def _webengine_smoke_code() -> str:
    return r"""
import faulthandler
import sys
import time
faulthandler.enable()
print("stage: import_qt", flush=True)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
print("stage: create_app", flush=True)
app = QApplication(sys.argv)
print("stage: import_webengine", flush=True)
from PyQt6.QtWebEngineWidgets import QWebEngineView
print("stage: create_view", flush=True)
view = QWebEngineView()
print("stage: set_html", flush=True)
view.setHtml("<!doctype html><html><body>ok</body></html>", QUrl("about:blank"))
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
print("stage: close", flush=True)
view.close()
view.deleteLater()
for _ in range(10):
    app.processEvents()
print("stage: done", flush=True)
"""


def _webengine_smoke_env(*, native_qt: bool) -> dict[str, str]:
    env = dict(os.environ)
    if native_qt:
        env.pop("QT_QPA_PLATFORM", None)
    else:
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QT_OPENGL", "software")
    chromium_flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    required_flags = "--disable-gpu --disable-gpu-compositing --no-sandbox"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{chromium_flags} {required_flags}".strip()
    return env


def _run_webengine_smoke(*, native_qt: bool, timeout_s: int = 12) -> dict:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _webengine_smoke_code()],
            cwd=str(PROJECT_ROOT),
            env=_webengine_smoke_env(native_qt=native_qt),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "returncode_hex": hex(completed.returncode) if completed.returncode is not None else None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return {
            "ok": False,
            "returncode": None,
            "timeout": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "stdout": str(stdout)[-2000:],
            "stderr": str(stderr)[-2000:],
        }


def _load_workspace_tabs(window: MainWindowQT, app: QApplication) -> list[dict]:
    workspace = getattr(window, "_workspace", None)
    if workspace is None:
        return []

    tab_specs = getattr(workspace, "tab_specs", None)
    ensure_tab_loaded = getattr(workspace, "ensure_tab_loaded", None)
    if not callable(tab_specs) or not callable(ensure_tab_loaded):
        return []

    loaded: list[dict] = []
    for spec in tab_specs() or []:
        key = str(spec.get("key", "")).strip()
        if not key:
            continue

        def _load_one():
            ensure_tab_loaded(key, reason="perf_memory_probe")
            _process_events(app, rounds=3)
            return {"key": key}

        sample = _measure(f"load_tab:{key}", _load_one)
        loaded.append(sample)
    return loaded


def _cycle_workspace_tabs(window: MainWindowQT, app: QApplication, *, cycles: int, settle_ms: int) -> dict:
    workspace = getattr(window, "_workspace", None)
    tabs = getattr(workspace, "tabs", None)
    if workspace is None or tabs is None:
        return {"cycles": 0, "tab_count": 0}

    ensure_tab_loaded = getattr(workspace, "ensure_tab_loaded", None)
    count = int(tabs.count())
    if count <= 0:
        return {"cycles": 0, "tab_count": 0}

    visited = 0
    for _ in range(max(0, int(cycles))):
        for index in range(count):
            if callable(ensure_tab_loaded):
                ensure_tab_loaded(index, reason="perf_memory_probe_cycle")
            tabs.setCurrentIndex(index)
            _settle(app, settle_ms)
            visited += 1
    return {"cycles": int(cycles), "tab_count": count, "visited": visited}


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


def _cycle_kline_windows(
    window: MainWindowQT,
    app: QApplication,
    *,
    cycles: int,
    settle_ms: int,
    code: str,
    name: str,
) -> dict:
    from ui.components.kline_window_manager import kline_manager

    opened = 0
    blocked = 0
    closed = 0
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
    return {"cycles": int(cycles), "opened": opened, "blocked": blocked, "closed": closed, "code": code_text}


def run_probe(args: argparse.Namespace) -> dict:
    if int(args.kline_cycles) > 0 and QApplication.instance() is None:
        from PyQt6.QtCore import Qt

        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    gc.collect()

    metrics: dict = {
        "mode": {
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "native_qt": bool(args.native_qt),
            "startup_enabled": bool(args.startup_enabled),
            "background_prewarm": bool(args.background_prewarm),
            "kline_prewarm_enabled": bool(args.kline_prewarm_enabled),
            "central_quotes_enabled": bool(args.central_quotes_enabled),
            "load_tabs": bool(args.load_tabs),
            "settle_ms": int(args.settle_ms),
            "tab_cycles": int(args.tab_cycles),
            "kline_cycles": int(args.kline_cycles),
            "cycle_settle_ms": int(args.cycle_settle_ms),
        },
        "samples": {},
        "loaded_tabs": [],
    }

    webengine_smoke = None
    if args.webengine_smoke or (args.kline_cycles > 0 and not args.skip_webengine_smoke):
        webengine_smoke = _run_webengine_smoke(native_qt=args.native_qt)
        metrics["webengine_smoke"] = webengine_smoke

    window_holder: dict[str, MainWindowQT] = {}

    def _build_window():
        window = MainWindowQT(
            startup_enabled=args.startup_enabled,
            background_prewarm=args.background_prewarm,
            kline_prewarm_enabled=args.kline_prewarm_enabled,
            central_quotes_enabled=args.central_quotes_enabled,
        )
        window_holder["window"] = window
        _process_events(app, rounds=8)
        workspace = getattr(window, "_workspace", None)
        loaded_count = len(getattr(workspace, "_tabs_by_key", {}) or {}) if workspace is not None else 0
        tab_count = window.tabs.count() if getattr(window, "tabs", None) is not None else 0
        return {"loaded_tabs": loaded_count, "tab_count": tab_count}

    metrics["samples"]["build_window"] = _measure("build_window", _build_window)
    window = window_holder["window"]

    if args.settle_ms > 0:
        metrics["samples"]["settle_after_build"] = _measure(
            "settle_after_build",
            lambda: _process_events(app, rounds=max(1, args.settle_ms // 50), sleep_ms=50),
        )

    if args.load_tabs or args.tab_cycles > 0:
        metrics["loaded_tabs"] = _load_workspace_tabs(window, app)
        if args.settle_ms > 0:
            metrics["samples"]["settle_after_tabs"] = _measure(
                "settle_after_tabs",
                lambda: _process_events(app, rounds=max(1, args.settle_ms // 50), sleep_ms=50),
            )

    if args.tab_cycles > 0:
        metrics["samples"]["tab_cycles"] = _measure(
            "tab_cycles",
            lambda: _cycle_workspace_tabs(
                window,
                app,
                cycles=args.tab_cycles,
                settle_ms=args.cycle_settle_ms,
            ),
        )

    if args.kline_cycles > 0:
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and not args.allow_offscreen_kline:
            metrics["samples"]["kline_cycles"] = {
                "label": "kline_cycles",
                "elapsed_ms": 0.0,
                "rss_before_mb": _rss_mb(),
                "rss_after_mb": _rss_mb(),
                "rss_delta_mb": 0.0,
                "result": {
                    "cycles": 0,
                    "skipped": True,
                    "reason": "QtWebEngine is not reliable with QT_QPA_PLATFORM=offscreen",
                },
            }
        else:
            if webengine_smoke is not None and not webengine_smoke.get("ok"):
                rss = _rss_mb()
                metrics["samples"]["kline_cycles"] = {
                    "label": "kline_cycles",
                    "elapsed_ms": 0.0,
                    "rss_before_mb": rss,
                    "rss_after_mb": rss,
                    "rss_delta_mb": 0.0,
                    "result": {
                        "cycles": 0,
                        "skipped": True,
                        "reason": "QtWebEngine isolated smoke failed",
                        "webengine_returncode": webengine_smoke.get("returncode"),
                        "webengine_returncode_hex": webengine_smoke.get("returncode_hex"),
                    },
                }
            else:
                metrics["samples"]["kline_cycles"] = _measure(
                    "kline_cycles",
                    lambda: _cycle_kline_windows(
                        window,
                        app,
                        cycles=args.kline_cycles,
                        settle_ms=args.cycle_settle_ms,
                        code=args.kline_code,
                        name=args.kline_name,
                    ),
                )

    def _close_window():
        window.close()
        _process_events(app, rounds=20, sleep_ms=20)
        return {"closed": bool(getattr(window, "_is_closing", False))}

    metrics["samples"]["close_window"] = _measure("close_window", _close_window)
    window.deleteLater()
    _process_events(app, rounds=5)
    gc.collect()
    metrics["rss_final_mb"] = _rss_mb()
    return metrics


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure PyQt startup and RSS for Zijin Research UI.")
    parser.add_argument("--native-qt", action="store_true", help="Do not force QT_QPA_PLATFORM=offscreen.")
    parser.add_argument("--startup-enabled", action="store_true", help="Run normal startup timers.")
    parser.add_argument("--background-prewarm", action="store_true", help="Enable workspace background tab prewarm.")
    parser.add_argument("--kline-prewarm-enabled", action="store_true", help="Enable hidden K-line WebEngine prewarm.")
    parser.add_argument("--central-quotes-enabled", action="store_true", help="Enable central quotes timer.")
    parser.add_argument("--load-tabs", action="store_true", help="Load every workspace tab and record per-tab RSS deltas.")
    parser.add_argument("--settle-ms", type=int, default=0, help="Wait/process events after build and tab loading.")
    parser.add_argument("--tab-cycles", type=int, default=0, help="Repeatedly switch through all workspace tabs.")
    parser.add_argument("--kline-cycles", type=int, default=0, help="Repeatedly open and close a K-line window.")
    parser.add_argument("--kline-code", default="000001", help="Stock code used by --kline-cycles.")
    parser.add_argument("--kline-name", default="平安银行", help="Stock name used by --kline-cycles.")
    parser.add_argument("--allow-offscreen-kline", action="store_true", help="Attempt K-line WebEngine cycles even with QT_QPA_PLATFORM=offscreen.")
    parser.add_argument("--webengine-smoke", action="store_true", help="Run an isolated QtWebEngine smoke probe and include its result.")
    parser.add_argument("--skip-webengine-smoke", action="store_true", help="Run K-line cycles without the isolated QtWebEngine preflight.")
    parser.add_argument("--cycle-settle-ms", type=int, default=150, help="Wait/process events inside repeated tab or K-line cycles.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    metrics = run_probe(args)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
