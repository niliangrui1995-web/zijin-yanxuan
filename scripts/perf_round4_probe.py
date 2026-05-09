from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from types import MethodType

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app.services.ui_runtime_service import background_job_runner as task_manager
from app.services.ui_runtime_service import domain_events as event_bus
from scripts.perf_memory_probe import collect_process_snapshot
from ui.main_window_qt import MainWindowQT
from ui.main_window_runtime import finish_f5_reload

DEFAULT_TABS = (
    "stock_candidates",
    "watchlist",
    "scan",
    "fund_holdings",
    "asian_market",
    "system_log",
    "foreign_block",
    "earnings",
    "lhb",
)


def _now_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _process_events(app: QApplication, rounds: int = 1, sleep_ms: int = 0) -> None:
    for _ in range(max(0, int(rounds))):
        app.processEvents()
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)


def _settle(app: QApplication, settle_ms: int) -> None:
    if settle_ms <= 0:
        _process_events(app, rounds=3)
        return
    rounds = max(1, int(settle_ms) // 25)
    _process_events(app, rounds=rounds, sleep_ms=25)


def _wait_until(app: QApplication, predicate, *, timeout_ms: int, step_ms: int = 20) -> bool:
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


def _loaded_tab_keys(workspace) -> list[str]:
    return [
        str(spec.get("key") or "").strip()
        for spec in _tab_specs(workspace)
        if spec.get("loaded")
    ]


def _row_count_from_model(model) -> int | None:
    if model is None:
        return None
    try:
        return int(model.rowCount())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    rows = getattr(model, "row_data", None)
    if rows is not None:
        try:
            return len(rows)
        except TypeError:
            return None
    return None


def _tab_row_count(tab) -> int | None:
    if tab is None:
        return None
    for attr_name in ("model", "source_model", "proxy_model"):
        count = _row_count_from_model(getattr(tab, attr_name, None))
        if count is not None:
            return count
    table_getter = getattr(tab, "get_primary_table", None)
    table = table_getter() if callable(table_getter) else None
    if table is not None:
        try:
            return _row_count_from_model(table.model())
        except (AttributeError, RuntimeError, TypeError):
            return None
    return None


def _tab_state(workspace, key: str) -> dict:
    tab = _loaded_tab(workspace, key)
    return {
        "key": key,
        "loaded": tab is not None,
        "class_name": tab.__class__.__name__ if tab is not None else "",
        "row_count": _tab_row_count(tab),
        "loaded_tabs": _loaded_tab_keys(workspace),
    }


def _timer_snapshot(window) -> dict:
    timers = []
    try:
        all_timers = list(window.findChildren(QTimer))
    except (AttributeError, RuntimeError, TypeError):
        all_timers = []
    for timer in all_timers:
        try:
            timers.append(
                {
                    "object_name": str(timer.objectName() or ""),
                    "active": bool(timer.isActive()),
                    "interval_ms": int(timer.interval()),
                    "single_shot": bool(timer.isSingleShot()),
                }
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    active = [timer for timer in timers if timer.get("active")]
    return {
        "total": len(timers),
        "active": len(active),
        "active_intervals_ms": sorted(int(timer.get("interval_ms") or 0) for timer in active),
    }


def _runtime_sample(window, label: str) -> dict:
    snapshot = collect_process_snapshot(label)
    snapshot["active_background_tasks"] = int(getattr(task_manager, "active_count", 0) or 0)
    snapshot["active_background_task_ids"] = _active_task_ids()
    snapshot["timers"] = _timer_snapshot(window)
    return snapshot


def _active_task_ids() -> list[str]:
    try:
        manager = task_manager._resolve_manager()
        active_workers = getattr(manager, "active_workers", {}) or {}
        return sorted(str(task_id) for task_id in active_workers.keys())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def _values(samples: list[dict], getter) -> list[float]:
    values = []
    for sample in samples:
        try:
            value = getter(sample)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            value = None
        if value is not None:
            values.append(float(value))
    return values


def _range(values: list[float]) -> float:
    return round(max(values) - min(values), 3) if values else 0.0


def _stability_trend(samples: list[dict]) -> dict:
    task_values = _values(samples, lambda item: item.get("active_background_tasks"))
    active_timer_values = _values(samples, lambda item: (item.get("timers") or {}).get("active"))
    total_timer_values = _values(samples, lambda item: (item.get("timers") or {}).get("total"))
    thread_values = _values(samples, lambda item: ((item.get("main") or {}).get("thread_count")))

    def _one(values: list[float]) -> dict:
        if not values:
            return {"count": 0, "first": None, "last": None, "net_delta": 0.0, "range": 0.0}
        return {
            "count": len(values),
            "first": values[0],
            "last": values[-1],
            "net_delta": round(values[-1] - values[0], 3),
            "range": _range(values),
            "max": max(values),
        }

    return {
        "active_tasks": _one(task_values),
        "active_timers": _one(active_timer_values),
        "total_timers": _one(total_timer_values),
        "threads": _one(thread_values),
    }


class F5ProbeRecorder:
    def __init__(self):
        self._lock = threading.RLock()
        self.tab_calls: list[dict] = []
        self.quote_calls: list[dict] = []
        self.post_f5_calls: list[dict] = []
        self.central_refresh_calls: list[dict] = []
        self.cache_signal_count = 0
        self.suppressed_post_f5: list[str] = []

    def _append(self, target: list[dict], item: dict) -> None:
        with self._lock:
            target.append(item)

    def wrap_method(self, obj, method_name: str, label: str, bucket: str, *, suppress: bool = False) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return

        def _wrapped(_self=None, *args, **kwargs):
            started = time.perf_counter()
            status = "ok"
            try:
                if suppress:
                    self.suppressed_post_f5.append(label)
                    return False
                return original(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                item = {
                    "label": label,
                    "method": method_name,
                    "elapsed_ms": _now_ms(started),
                    "status": status,
                }
                if bucket == "tab":
                    self._append(self.tab_calls, item)
                elif bucket == "post_f5":
                    self._append(self.post_f5_calls, item)
                elif bucket == "central":
                    self._append(self.central_refresh_calls, item)

        setattr(obj, method_name, MethodType(_wrapped, obj))

    def wrap_provider(self, provider) -> None:
        original = getattr(provider, "fetch_realtime_quotes_batch", None)
        if not callable(original):
            return

        def _wrapped(codes, *args, **kwargs):
            started = time.perf_counter()
            requested = [str(code or "").strip() for code in (codes or []) if str(code or "").strip()]
            status = "ok"
            result_count = 0
            try:
                result = original(codes, *args, **kwargs)
                try:
                    result_count = len(result or {})
                except TypeError:
                    result_count = 0
                return result
            except Exception:
                status = "error"
                raise
            finally:
                unique_count = len(set(requested))
                self._append(
                    self.quote_calls,
                    {
                        "elapsed_ms": _now_ms(started),
                        "requested_count": len(requested),
                        "unique_count": unique_count,
                        "duplicate_in_batch": max(0, len(requested) - unique_count),
                        "codes": requested,
                        "status": status,
                        "result_count": result_count,
                    },
                )

        provider.fetch_realtime_quotes_batch = _wrapped

    def quote_summary(self) -> dict:
        all_codes: list[str] = []
        duplicate_in_batch = 0
        for call in self.quote_calls:
            all_codes.extend(call.get("codes") or [])
            duplicate_in_batch += int(call.get("duplicate_in_batch") or 0)
        counts = Counter(all_codes)
        repeated = {code: count for code, count in counts.items() if count > 1}
        return {
            "batch_count": len(self.quote_calls),
            "requested_count": len(all_codes),
            "unique_count": len(counts),
            "duplicate_in_batch": duplicate_in_batch,
            "duplicate_across_batches": int(sum(count - 1 for count in repeated.values())),
            "duplicates_by_code": repeated,
            "calls": list(self.quote_calls),
        }


def _build_window(args: argparse.Namespace, app: QApplication) -> tuple[MainWindowQT, dict]:
    started = time.perf_counter()
    stages = []
    window = MainWindowQT(
        startup_enabled=bool(args.startup_enabled),
        background_prewarm=bool(args.background_prewarm),
        kline_prewarm_enabled=bool(args.kline_prewarm_enabled),
        central_quotes_enabled=bool(args.central_quotes_enabled),
    )
    stages.append({"stage": "construct_window", "elapsed_ms": _now_ms(started)})
    if args.show_window:
        window.show()
        _settle(app, args.startup_settle_ms)
        stages.append({"stage": "show_and_settle", "elapsed_ms": _now_ms(started)})
    else:
        _settle(app, min(args.startup_settle_ms, 250))
        stages.append({"stage": "settle_without_show", "elapsed_ms": _now_ms(started)})
    return window, {
        "main_window_ready_ms": _now_ms(started),
        "stages": stages,
    }


def probe_startup(args: argparse.Namespace, app: QApplication) -> tuple[MainWindowQT, dict]:
    started = time.perf_counter()
    window, startup = _build_window(args, app)
    workspace = getattr(window, "_workspace", None)
    default_key = "watchlist"
    startup.update(
        {
            "total_probe_elapsed_ms": _now_ms(started),
            "workspace_loaded": workspace is not None,
            "default_tab": _tab_state(workspace, default_key) if workspace is not None else {},
            "snapshot": collect_process_snapshot("startup_ready"),
        }
    )
    return window, startup


def probe_tab_first_open(
    window: MainWindowQT,
    app: QApplication,
    tabs: tuple[str, ...],
    *,
    timeout_ms: int,
    settle_ms: int,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    tab_widget = getattr(workspace, "tabs", None)
    results = []
    if workspace is None or tab_widget is None:
        return {"tabs": results, "error": "workspace_unavailable"}

    for key in tabs:
        index = _tab_index(workspace, key)
        if index < 0:
            results.append({"key": key, "status": "missing"})
            continue
        before = collect_process_snapshot(f"tab:{key}:before")
        started = time.perf_counter()
        tab_widget.setCurrentIndex(index)
        loaded = _wait_until(
            app,
            lambda key=key: _loaded_tab(workspace, key) is not None,
            timeout_ms=timeout_ms,
        )
        _settle(app, settle_ms)
        state = _tab_state(workspace, key)
        after = collect_process_snapshot(f"tab:{key}:after")
        results.append(
            {
                **state,
                "status": "ok" if loaded else "timeout",
                "elapsed_ms": _now_ms(started),
                "rss_delta_mb": _rss_delta(before, after),
                "thread_delta": _thread_delta(before, after),
            }
        )
    return {"tabs": results}


def _rss_delta(before: dict, after: dict) -> float | None:
    try:
        left = float(((before.get("main") or {}).get("rss_mb")))
        right = float(((after.get("main") or {}).get("rss_mb")))
        return round(right - left, 1)
    except (TypeError, ValueError):
        return None


def _thread_delta(before: dict, after: dict) -> int | None:
    try:
        left = int(((before.get("main") or {}).get("thread_count")))
        right = int(((after.get("main") or {}).get("thread_count")))
        return right - left
    except (TypeError, ValueError):
        return None


def probe_f5_refresh(
    window: MainWindowQT,
    app: QApplication,
    tabs: tuple[str, ...],
    *,
    execute_post_f5_refresh: bool,
    timeout_ms: int,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    recorder = F5ProbeRecorder()
    if workspace is None:
        return {"error": "workspace_unavailable"}

    recorder.wrap_provider(getattr(window, "data_provider", None))
    central = getattr(window, "central_quotes_svc", None)
    central_timer = getattr(central, "_timer", None)
    if central_timer is not None:
        try:
            central_timer.stop()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    if central is not None:
        recorder.wrap_method(central, "refresh_after_cache_reload", "central_quotes", "central")

    for key in tabs:
        tab = _loaded_tab(workspace, key)
        if tab is None:
            continue
        recorder.wrap_method(tab, "refresh_table_from_latest_snapshot", key, "tab")
        recorder.wrap_method(
            tab,
            "refresh_data_after_f5",
            key,
            "post_f5",
            suppress=not execute_post_f5_refresh,
        )

    def _on_cache_signal():
        recorder.cache_signal_count += 1

    event_bus.sig_cache_reload_completed.connect(_on_cache_signal)
    before = _runtime_sample(window, "f5:before")
    before_task_ids = set(before.get("active_background_task_ids") or [])
    started = time.perf_counter()
    try:
        finish_f5_reload(window, count=123, elapsed=1.23, event_bus=event_bus)
        _wait_until(
            app,
            lambda: getattr(getattr(workspace, "_f5_refresh_scheduler", None), "is_running", lambda: False)() is False,
            timeout_ms=timeout_ms,
        )
        _settle(app, 100)
    finally:
        try:
            event_bus.sig_cache_reload_completed.disconnect(_on_cache_signal)
        except (TypeError, RuntimeError):
            pass
    after = _runtime_sample(window, "f5:after")
    after_task_ids = set(after.get("active_background_task_ids") or [])
    new_active_task_ids = sorted(after_task_ids - before_task_ids)
    total_elapsed_ms = _now_ms(started)

    return {
        "total_elapsed_ms": total_elapsed_ms,
        "tab_timings": list(recorder.tab_calls),
        "post_f5_calls": list(recorder.post_f5_calls),
        "suppressed_post_f5": list(recorder.suppressed_post_f5),
        "central_refresh_calls": list(recorder.central_refresh_calls),
        "cache_signal_count": recorder.cache_signal_count,
        "quote_requests": recorder.quote_summary(),
        "active_background_tasks_after": int(getattr(task_manager, "active_count", 0) or 0),
        "active_background_task_ids_after": sorted(after_task_ids),
        "new_active_background_tasks_after": len(new_active_task_ids),
        "new_active_background_task_ids_after": new_active_task_ids,
        "rss_delta_mb": _rss_delta(before, after),
        "thread_delta": _thread_delta(before, after),
    }


def probe_stability(
    window: MainWindowQT,
    app: QApplication,
    tabs: tuple[str, ...],
    *,
    idle_seconds: int,
    tab_cycles: int,
    f5_cycles: int,
    quote_cycles: int,
    settle_ms: int,
) -> dict:
    workspace = getattr(window, "_workspace", None)
    tab_widget = getattr(workspace, "tabs", None)
    samples = [_runtime_sample(window, "stability:start")]

    for second in range(max(0, int(idle_seconds))):
        _settle(app, 1000)
        samples.append(_runtime_sample(window, f"stability:idle:{second + 1}s"))

    visited = 0
    if workspace is not None and tab_widget is not None:
        for cycle in range(max(0, int(tab_cycles))):
            for key in tabs:
                index = _tab_index(workspace, key)
                if index < 0:
                    continue
                tab_widget.setCurrentIndex(index)
                _wait_until(app, lambda key=key: _loaded_tab(workspace, key) is not None, timeout_ms=1500)
                _settle(app, settle_ms)
                visited += 1
            samples.append(_runtime_sample(window, f"stability:tab_cycle:{cycle + 1}"))

    f5_completed = 0
    for cycle in range(max(0, int(f5_cycles))):
        probe_f5_refresh(
            window,
            app,
            tabs,
            execute_post_f5_refresh=False,
            timeout_ms=3000,
        )
        f5_completed += 1
        samples.append(_runtime_sample(window, f"stability:f5_cycle:{cycle + 1}"))

    quote_completed = 0
    central = getattr(window, "central_quotes_svc", None)
    trigger_fetch = getattr(central, "_trigger_fetch", None)
    if callable(trigger_fetch):
        for cycle in range(max(0, int(quote_cycles))):
            trigger_fetch()
            _wait_until(app, lambda: int(getattr(task_manager, "active_count", 0) or 0) == 0, timeout_ms=3000)
            _settle(app, settle_ms)
            quote_completed += 1
            samples.append(_runtime_sample(window, f"stability:quote_cycle:{cycle + 1}"))

    gc.collect()
    _settle(app, settle_ms)
    samples.append(_runtime_sample(window, "stability:end"))
    return {
        "mode": {
            "idle_seconds": int(idle_seconds),
            "tab_cycles": int(tab_cycles),
            "f5_cycles": int(f5_cycles),
            "quote_cycles": int(quote_cycles),
            "settle_ms": int(settle_ms),
        },
        "result": {
            "tab_visits": visited,
            "f5_cycles": f5_completed,
            "quote_cycles": quote_completed,
        },
        "trend": _stability_trend(samples),
        "samples": samples,
    }


def run_probe(args: argparse.Namespace) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    tabs = tuple(dict.fromkeys(args.tabs or DEFAULT_TABS))
    report = {
        "schema_version": 1,
        "probe": "perf_round4",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": {
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "native_qt": bool(args.native_qt),
            "startup_enabled": bool(args.startup_enabled),
            "background_prewarm": bool(args.background_prewarm),
            "central_quotes_enabled": bool(args.central_quotes_enabled),
            "execute_post_f5_refresh": bool(args.execute_post_f5_refresh),
            "tabs": list(tabs),
        },
    }
    window, startup = probe_startup(args, app)
    report["startup"] = startup
    try:
        if args.probe_tabs:
            report["tab_first_open"] = probe_tab_first_open(
                window,
                app,
                tabs,
                timeout_ms=args.tab_timeout_ms,
                settle_ms=args.tab_settle_ms,
            )
        if args.probe_f5:
            report["f5_refresh"] = probe_f5_refresh(
                window,
                app,
                tabs,
                execute_post_f5_refresh=args.execute_post_f5_refresh,
                timeout_ms=args.f5_timeout_ms,
            )
        if args.probe_stability:
            report["stability"] = probe_stability(
                window,
                app,
                tabs,
                idle_seconds=args.idle_seconds,
                tab_cycles=args.stability_tab_cycles,
                f5_cycles=args.stability_f5_cycles,
                quote_cycles=args.stability_quote_cycles,
                settle_ms=args.stability_settle_ms,
            )
    finally:
        try:
            window.close()
            _settle(app, 200)
            window.deleteLater()
            gc.collect()
            _settle(app, 100)
        finally:
            report["final_snapshot"] = collect_process_snapshot("round4_probe_end")
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Round 4 interaction, F5, and stability performance probe.")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--startup-enabled", action="store_true")
    parser.add_argument("--background-prewarm", action="store_true")
    parser.add_argument("--kline-prewarm-enabled", action="store_true")
    parser.add_argument("--central-quotes-enabled", action="store_true")
    parser.add_argument("--show-window", action="store_true")
    parser.add_argument("--startup-settle-ms", type=int, default=300)
    parser.add_argument("--tabs", nargs="*", default=list(DEFAULT_TABS))
    parser.add_argument("--probe-tabs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--probe-f5", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--probe-stability", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tab-timeout-ms", type=int, default=6000)
    parser.add_argument("--tab-settle-ms", type=int, default=100)
    parser.add_argument("--f5-timeout-ms", type=int, default=6000)
    parser.add_argument("--execute-post-f5-refresh", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=2)
    parser.add_argument("--stability-tab-cycles", type=int, default=1)
    parser.add_argument("--stability-f5-cycles", type=int, default=1)
    parser.add_argument("--stability-quote-cycles", type=int, default=1)
    parser.add_argument("--stability-settle-ms", type=int, default=100)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_probe(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
