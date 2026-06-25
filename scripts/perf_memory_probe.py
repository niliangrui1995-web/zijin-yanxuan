from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess  # nosec B404
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

try:
    import psutil
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None

from PyQt6.QtWidgets import QApplication

from ui.main_window_qt import MainWindowQT


def _mb(value: int | float | None) -> float:
    return round(float(value or 0) / 1024.0 / 1024.0, 1)


def _rss_mb() -> float | None:
    if psutil is None:
        return None
    return _mb(psutil.Process(os.getpid()).memory_info().rss)


def _process_info(process) -> dict | None:
    if psutil is None:
        return None
    try:
        memory = process.memory_info()
        item = {
            "pid": process.pid,
            "name": process.name(),
            "rss_mb": _mb(getattr(memory, "rss", 0)),
            "vms_mb": _mb(getattr(memory, "vms", 0)),
            "thread_count": process.num_threads(),
        }
        private_value = getattr(memory, "private", None)
        if private_value is not None:
            item["private_mb"] = _mb(private_value)
        working_set = getattr(memory, "wset", None)
        if working_set is not None:
            item["working_set_mb"] = _mb(working_set)
        return item
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, psutil.Error):
        return None


def _qt_object_counts() -> dict:
    counts: dict = {}
    try:
        app = QApplication.instance()
        if app is not None:
            counts["qt_top_level_widgets"] = len(app.topLevelWidgets())
            counts["qt_all_widgets"] = len(app.allWidgets())
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        from ui.components.kline_window_manager import kline_manager

        charts = list(getattr(kline_manager, "_charts", []) or [])
        counts["kline_tracked_windows"] = len(charts)
        counts["kline_active_windows"] = kline_manager.active_count
        counts["kline_prewarm_view"] = 1 if getattr(kline_manager, "_prewarm_view", None) is not None else 0
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        pass
    return counts


def collect_process_snapshot(label: str = "") -> dict:
    if psutil is None:
        return {
            "label": label,
            "main": {"pid": os.getpid(), "rss_mb": None, "vms_mb": None, "thread_count": None},
            "children": [],
            "webengine_children": [],
            "child_count": 0,
            "webengine_child_count": 0,
            "webengine_rss_mb": None,
            "webengine_private_mb": None,
            "object_counts": _qt_object_counts(),
        }
    process = psutil.Process(os.getpid())
    children = [item for item in (_process_info(child) for child in process.children(recursive=True)) if item]
    webengine_children = [
        item
        for item in children
        if "qtwebengine" in str(item.get("name", "")).lower()
        or "chrome" in str(item.get("name", "")).lower()
        or "chromium" in str(item.get("name", "")).lower()
    ]
    return {
        "label": label,
        "main": _process_info(process),
        "children": children,
        "webengine_children": webengine_children,
        "child_count": len(children),
        "webengine_child_count": len(webengine_children),
        "webengine_rss_mb": round(sum(float(item.get("rss_mb") or 0.0) for item in webengine_children), 1),
        "webengine_private_mb": round(sum(float(item.get("private_mb") or 0.0) for item in webengine_children), 1),
        "object_counts": _qt_object_counts(),
    }


def _snapshot_value(snapshot: dict, key: str) -> float | None:
    value = ((snapshot or {}).get("main") or {}).get(key)
    return None if value is None else float(value)


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


def _should_defer_probe_tab_load(workspace, key: str, *, reason: str = "perf_memory_probe") -> bool:
    should_defer = getattr(workspace, "should_defer_probe_tab_load", None)
    if not callable(should_defer):
        return False
    try:
        return bool(should_defer(key, reason=reason))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _deferred_probe_tab_sample(label: str, key: str) -> dict:
    return {
        "label": label,
        "elapsed_ms": 0.0,
        "status": "skipped_controlled_probe",
        "result": {"key": key, "reason": "controlled_startup_probe_deferred"},
    }


def _measure(label: str, fn):
    before_snapshot = collect_process_snapshot(f"{label}:before")
    before = _snapshot_value(before_snapshot, "rss_mb")
    started = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    after_snapshot = collect_process_snapshot(f"{label}:after")
    after = _snapshot_value(after_snapshot, "rss_mb")
    private_before = _snapshot_value(before_snapshot, "private_mb")
    private_after = _snapshot_value(after_snapshot, "private_mb")
    vms_before = _snapshot_value(before_snapshot, "vms_mb")
    vms_after = _snapshot_value(after_snapshot, "vms_mb")
    return {
        "label": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "rss_before_mb": before,
        "rss_after_mb": after,
        "rss_delta_mb": None if before is None or after is None else round(after - before, 1),
        "private_before_mb": private_before,
        "private_after_mb": private_after,
        "private_delta_mb": (
            None if private_before is None or private_after is None else round(private_after - private_before, 1)
        ),
        "vms_before_mb": vms_before,
        "vms_after_mb": vms_after,
        "vms_delta_mb": None if vms_before is None or vms_after is None else round(vms_after - vms_before, 1),
        "snapshot_before": before_snapshot,
        "snapshot_after": after_snapshot,
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
    configure_qt_webengine_runtime(env)
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    if "--no-sandbox" not in flags:
        flags.append("--no-sandbox")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)
    return env


def _run_webengine_smoke(*, native_qt: bool, timeout_s: int = 12) -> dict:
    started = time.perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
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

        if _should_defer_probe_tab_load(workspace, key, reason="perf_memory_probe"):
            loaded.append(_deferred_probe_tab_sample(f"load_tab:{key}", key))
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
    tab_specs = getattr(workspace, "tab_specs", None)
    specs = list(tab_specs() or []) if callable(tab_specs) else []
    count = int(tabs.count())
    if count <= 0:
        return {"cycles": 0, "tab_count": 0}

    visited = 0
    deferred: list[str] = []
    for _ in range(max(0, int(cycles))):
        for index in range(count):
            spec = specs[index] if index < len(specs) else {}
            key = str((spec or {}).get("key", "")).strip()
            if key and _should_defer_probe_tab_load(workspace, key, reason="perf_memory_probe_cycle"):
                deferred.append(key)
                continue
            if callable(ensure_tab_loaded):
                ensure_tab_loaded(index, reason="perf_memory_probe_cycle")
            tabs.setCurrentIndex(index)
            _settle(app, settle_ms)
            visited += 1
    return {"cycles": int(cycles), "tab_count": count, "visited": visited, "deferred_tabs": deferred}


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


def _webengine_lifecycle_info(chart) -> dict:
    info = {
        "chart_alive": chart is not None,
        "has_browser": False,
        "has_page": False,
    }
    if chart is None:
        return info

    for attr_name in (
        "_last_chart_payload_bytes",
        "_last_chart_html_bytes",
        "_last_chart_points",
    ):
        value = getattr(chart, attr_name, None)
        if value is not None:
            info[attr_name.removeprefix("_last_chart_")] = value

    try:
        from ui.kline_window_qt import _ECHARTS_JS_PATH

        info["echarts_js_bytes"] = Path(_ECHARTS_JS_PATH).stat().st_size
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        pass

    browser = getattr(chart, "browser", None)
    info["has_browser"] = browser is not None
    if browser is None:
        return info

    try:
        info["browser_object_name"] = str(browser.objectName() or "")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    try:
        page = browser.page()
    except (AttributeError, RuntimeError, TypeError):
        page = None
    info["has_page"] = page is not None
    if page is None:
        return info

    try:
        info["page_url"] = page.url().toString()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    try:
        profile = page.profile()
    except (AttributeError, RuntimeError, TypeError):
        profile = None
    info["has_profile"] = profile is not None
    if profile is None:
        return info

    for key, getter_name in (
        ("profile_off_the_record", "isOffTheRecord"),
        ("profile_storage_name", "storageName"),
        ("profile_cache_path", "cachePath"),
        ("profile_persistent_storage_path", "persistentStoragePath"),
    ):
        try:
            getter = getattr(profile, getter_name)
            info[key] = getter()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    return info


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
    cycle_samples: list[dict] = []
    code_text = str(code or "").strip() or "000001"
    name_text = str(name or "").strip() or code_text
    for cycle_index in range(max(0, int(cycles))):
        cycle_samples.append(collect_process_snapshot(f"kline_cycle_{cycle_index + 1}:before_open"))
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
        after_open = collect_process_snapshot(f"kline_cycle_{cycle_index + 1}:after_open")
        after_open["webengine_lifecycle"] = _webengine_lifecycle_info(chart)
        cycle_samples.append(after_open)
        closed += _close_kline_charts(app)
        gc.collect()
        _settle(app, settle_ms)
        cycle_samples.append(collect_process_snapshot(f"kline_cycle_{cycle_index + 1}:after_close"))
    return {
        "cycles": int(cycles),
        "opened": opened,
        "blocked": blocked,
        "closed": closed,
        "code": code_text,
        "cycle_samples": cycle_samples,
    }


def _profile_gbbq(provider, *, mode: str, code: str) -> dict:
    code_text = str(code or "").strip() or "000001"
    mode_text = str(mode or "none").strip().lower()
    samples: dict = {}
    if provider is None:
        return {"mode": mode_text, "code": code_text, "error": "provider_unavailable", "samples": samples}

    if mode_text in {"single", "both"}:
        samples["single_code"] = _measure(
            f"gbbq_single:{code_text}",
            lambda: {
                "code": code_text,
                "codes": len(provider._load_local_gbbq_for_code(code_text)),
                "full_loaded": bool(getattr(provider, "_local_gbbq_loaded", False)),
                "code_cache_size": len(getattr(provider, "_local_gbbq_code_cache", {}) or {}),
            },
        )

    if mode_text in {"full", "both"}:
        samples["full"] = _measure(
            "gbbq_full",
            lambda: {
                "codes": len(provider._load_local_gbbq(force=False)),
                "full_loaded": bool(getattr(provider, "_local_gbbq_loaded", False)),
                "code_cache_size": len(getattr(provider, "_local_gbbq_code_cache", {}) or {}),
            },
        )

    return {"mode": mode_text, "code": code_text, "samples": samples}


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
            "allow_controlled_probe_tab_loads": bool(args.allow_controlled_probe_tab_loads),
            "load_tabs": bool(args.load_tabs),
            "settle_ms": int(args.settle_ms),
            "tab_cycles": int(args.tab_cycles),
            "kline_cycles": int(args.kline_cycles),
            "cycle_settle_ms": int(args.cycle_settle_ms),
            "profile_gbbq": str(args.profile_gbbq),
        },
        "samples": {},
        "snapshots": [collect_process_snapshot("probe_start")],
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
            restore_last_tab_enabled=False,
            controlled_startup_probe_guard=False if args.allow_controlled_probe_tab_loads else None,
        )
        window_holder["window"] = window
        _process_events(app, rounds=8)
        workspace = getattr(window, "_workspace", None)
        loaded_count = len(getattr(workspace, "_tabs_by_key", {}) or {}) if workspace is not None else 0
        tab_count = window.tabs.count() if getattr(window, "tabs", None) is not None else 0
        return {"loaded_tabs": loaded_count, "tab_count": tab_count}

    metrics["samples"]["build_window"] = _measure("build_window", _build_window)
    window = window_holder["window"]
    metrics["snapshots"].append(collect_process_snapshot("after_build_window"))

    if args.settle_ms > 0:
        metrics["samples"]["settle_after_build"] = _measure(
            "settle_after_build",
            lambda: _process_events(app, rounds=max(1, args.settle_ms // 50), sleep_ms=50),
        )
        metrics["snapshots"].append(collect_process_snapshot("after_settle_build"))

    if args.profile_gbbq != "none":
        metrics["gbbq_profile"] = _profile_gbbq(
            getattr(window, "data_provider", None),
            mode=args.profile_gbbq,
            code=args.gbbq_code,
        )
        metrics["snapshots"].append(collect_process_snapshot("after_profile_gbbq"))

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
    metrics["snapshots"].append(collect_process_snapshot("probe_end"))
    return metrics


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure PyQt startup and RSS for Zijin Research UI.")
    parser.add_argument("--native-qt", action="store_true", help="Do not force QT_QPA_PLATFORM=offscreen.")
    parser.add_argument("--startup-enabled", action="store_true", help="Run normal startup timers.")
    parser.add_argument("--background-prewarm", action="store_true", help="Enable workspace background tab prewarm.")
    parser.add_argument("--kline-prewarm-enabled", action="store_true", help="Enable hidden K-line WebEngine prewarm.")
    parser.add_argument("--central-quotes-enabled", action="store_true", help="Enable central quotes timer.")
    parser.add_argument(
        "--allow-controlled-probe-tab-loads",
        action="store_true",
        help="Allow controlled-startup probes to construct heavy lazy tabs.",
    )
    parser.add_argument(
        "--load-tabs", action="store_true", help="Load every workspace tab and record per-tab RSS deltas."
    )
    parser.add_argument("--settle-ms", type=int, default=0, help="Wait/process events after build and tab loading.")
    parser.add_argument("--tab-cycles", type=int, default=0, help="Repeatedly switch through all workspace tabs.")
    parser.add_argument("--kline-cycles", type=int, default=0, help="Repeatedly open and close a K-line window.")
    parser.add_argument("--kline-code", default="000001", help="Stock code used by --kline-cycles.")
    parser.add_argument("--kline-name", default="平安银行", help="Stock name used by --kline-cycles.")
    parser.add_argument(
        "--allow-offscreen-kline",
        action="store_true",
        help="Attempt K-line WebEngine cycles even with QT_QPA_PLATFORM=offscreen.",
    )
    parser.add_argument(
        "--webengine-smoke", action="store_true", help="Run an isolated QtWebEngine smoke probe and include its result."
    )
    parser.add_argument(
        "--skip-webengine-smoke",
        action="store_true",
        help="Run K-line cycles without the isolated QtWebEngine preflight.",
    )
    parser.add_argument(
        "--cycle-settle-ms", type=int, default=150, help="Wait/process events inside repeated tab or K-line cycles."
    )
    parser.add_argument(
        "--profile-gbbq",
        choices=("none", "single", "full", "both"),
        default="none",
        help="Measure single-code and/or full gbbq loading in the current provider.",
    )
    parser.add_argument("--gbbq-code", default="000001", help="Stock code used by --profile-gbbq single/both.")
    parser.add_argument("--output", default="", help="Write the structured JSON report to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    metrics = run_probe(args)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
