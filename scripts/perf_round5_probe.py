from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import threading
import time
import traceback
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

from PyQt6.QtWidgets import QApplication

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_service import background_job_runner as task_manager
from scripts.perf_memory_probe import collect_process_snapshot
from scripts.perf_round4_probe import (
    DEFAULT_TABS,
    _build_window,
    _loaded_tab,
    _runtime_sample,
    _settle,
    _stability_trend,
    probe_tab_first_open,
)
from ui.main_window_runtime import finish_f5_reload
from ui.workspaces.quote_universe_service import INFO_SOURCE_GROUP

INFO_SOURCE_TAB_KEYS = frozenset({"scan", "foreign_block", "earnings", "fund_holdings"})
NETWORK_TASK_MARKERS = (
    "central_quotes",
    "foreign_block_trade",
    "fund_holdings_sync",
    "earnings_scheduler:routine",
    "go_online",
    "force_reconnect",
)
EVENT_SIGNAL_NAMES = (
    "sig_cache_reload_completed",
    "sig_rt_quotes",
    "sig_scan_updated",
    "sig_block_trade_updated",
    "sig_earnings_updated",
    "sig_fund_holdings_updated",
    "sig_stock_context_snapshot_updated",
    "sig_watchlist_changed",
)


def _now_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _clean_codes(codes) -> list[str]:
    return [str(code or "").strip() for code in (codes or []) if str(code or "").strip()]


def _batch_signature(codes: list[str]) -> str:
    return "|".join(sorted(dict.fromkeys(codes)))


def _relative_stack(limit: int = 18) -> list[dict]:
    stack = traceback.extract_stack(limit=limit)[:-2]
    items: list[dict] = []
    for frame in stack:
        try:
            path = Path(frame.filename).resolve()
            rel = path.relative_to(PROJECT_ROOT)
        except (OSError, RuntimeError, ValueError):
            continue
        rel_text = str(rel).replace("\\", "/")
        if rel_text == "scripts/perf_round5_probe.py":
            continue
        items.append({"file": rel_text, "line": int(frame.lineno), "function": str(frame.name)})
    return items[-10:]


def _source_label(stack: list[dict]) -> str:
    for frame in reversed(stack):
        file_name = str(frame.get("file") or "")
        function = str(frame.get("function") or "")
        if file_name.endswith("central_quotes_worker.py"):
            return "central_quotes"
        if file_name.endswith("base_stock_refresh.py"):
            return "table_quote_hydration"
        if file_name.endswith("watchlist_tab.py"):
            return "watchlist"
        if file_name.endswith("stock_candidate_tab.py"):
            return "stock_candidates"
        if file_name.endswith("scan_tab.py"):
            return "scan"
        if file_name.endswith("foreign_block_trade_tab.py"):
            return "foreign_block"
        if file_name.endswith("earnings_tab.py"):
            return "earnings"
        if file_name.endswith("fund_holdings_tab.py"):
            return "fund_holdings"
        if function in {"refresh_after_cache_reload", "_trigger_fetch", "_trigger_fetch_for_reason"}:
            return "central_quotes"
    return "unknown"


def _event_receiver_snapshot() -> dict[str, int | None]:
    snapshot: dict[str, int | None] = {}
    for name in EVENT_SIGNAL_NAMES:
        signal = getattr(event_bus, name, None)
        try:
            snapshot[name] = int(event_bus.receivers(signal))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot[name] = None
    return snapshot


def _event_receiver_trend(samples: list[dict]) -> dict:
    keys = sorted({key for sample in samples for key in (sample.get("event_receivers") or {})})
    trend = {}
    for key in keys:
        values = [
            value
            for sample in samples
            for value in [(sample.get("event_receivers") or {}).get(key)]
            if value is not None
        ]
        if not values:
            trend[key] = {"count": 0, "first": None, "last": None, "net_delta": 0}
            continue
        trend[key] = {
            "count": len(values),
            "first": values[0],
            "last": values[-1],
            "net_delta": int(values[-1] - values[0]),
            "max": max(values),
        }
    return trend


def disable_rt_monitor_auto_start(workspace) -> dict:
    tab = _loaded_tab(workspace, "rt_monitor")
    if tab is None:
        return {"disabled": False, "reason": "rt_monitor_not_loaded"}
    timer = getattr(tab, "_auto_timer", None)
    if timer is None or not hasattr(timer, "stop"):
        return {"disabled": False, "reason": "auto_timer_unavailable"}
    try:
        timer.stop()
    except (RuntimeError, TypeError, ValueError) as exc:
        return {"disabled": False, "reason": f"timer_stop_failed:{exc.__class__.__name__}"}
    try:
        tab._manual_stop_requested = True
        tab._manual_stop_trade_date = tab._manual_stop_reference_date()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return {"disabled": True, "reason": "probe_isolation"}


def _loaded_info_source_keys(workspace) -> set[str]:
    tab_specs = getattr(workspace, "tab_specs", None)
    specs = list(tab_specs() or []) if callable(tab_specs) else []
    keys = set()
    for spec in specs:
        key = str(spec.get("key") or "").strip()
        group = str(spec.get("group") or "").strip()
        if not spec.get("loaded"):
            continue
        if key and group == INFO_SOURCE_GROUP:
            keys.add(key)
    return keys


def _effective_probe_tabs(tabs: tuple[str, ...], *, isolate_info_source_refresh: bool) -> tuple[str, ...]:
    if not isolate_info_source_refresh:
        return tabs
    return tuple(tab for tab in tabs if tab not in INFO_SOURCE_TAB_KEYS)


def disable_information_source_refresh_after_f5(workspace) -> dict:
    if workspace is None:
        return {"disabled": False, "reason": "workspace_unavailable"}
    original = getattr(workspace, "refresh_information_sources_after_f5", None)
    if not callable(original):
        return {"disabled": False, "reason": "refresh_method_unavailable"}
    try:
        workspace.refresh_information_sources_after_f5 = lambda: {}
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"disabled": False, "reason": f"patch_failed:{exc.__class__.__name__}"}
    return {"disabled": True, "reason": "probe_isolation"}


def _active_earnings_workers(window) -> list[str]:
    workspace = getattr(window, "_workspace", None)
    tab = _loaded_tab(workspace, "earnings") if workspace is not None else None
    scheduler = getattr(tab, "scheduler", None)
    workers = getattr(scheduler, "active_workers", None)
    if not workers:
        return []
    return sorted(str(getattr(worker, "mode", worker.__class__.__name__)) for worker in workers)


def _runtime_timeline_sample(window, label: str, offset_s: float | None = None) -> dict:
    sample = _runtime_sample(window, label)
    sample["offset_s"] = offset_s
    sample["event_receivers"] = _event_receiver_snapshot()
    sample["active_earnings_workers"] = _active_earnings_workers(window)
    sample["active_earnings_worker_count"] = len(sample["active_earnings_workers"])
    return sample


class PostF5NetworkRecorder:
    def __init__(self, *, stub_quote_provider: bool = False):
        self.started_at = time.perf_counter()
        self.f5_returned_at: float | None = None
        self.stub_quote_provider = bool(stub_quote_provider)
        self._lock = threading.RLock()
        self.quote_calls: list[dict] = []
        self.background_tasks: list[dict] = []
        self.tab_calls: list[dict] = []
        self.event_counts = {name: 0 for name in EVENT_SIGNAL_NAMES}
        self._restore_callbacks: list = []

    def rel_ms(self) -> float:
        return _now_ms(self.started_at)

    def phase(self) -> str:
        return "post_f5" if self.f5_returned_at is not None else "during_f5"

    def append(self, bucket: list[dict], item: dict) -> None:
        with self._lock:
            bucket.append(item)

    def connect_event_counters(self) -> None:
        for name in EVENT_SIGNAL_NAMES:
            signal = getattr(event_bus, name, None)
            if signal is None:
                continue

            def _counter(*_args, signal_name=name):
                self.event_counts[signal_name] = int(self.event_counts.get(signal_name, 0) or 0) + 1

            try:
                signal.connect(_counter)
                self._restore_callbacks.append(lambda signal=signal, counter=_counter: signal.disconnect(counter))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

    def wrap_provider(self, provider) -> None:
        original = getattr(provider, "fetch_realtime_quotes_batch", None)
        if not callable(original):
            return

        def _wrapped(codes, *args, **kwargs):
            started_ms = self.rel_ms()
            requested = _clean_codes(codes)
            stack = _relative_stack()
            status = "ok"
            result_count = 0
            error = ""
            try:
                if self.stub_quote_provider:
                    result = {
                        code: {
                            "close": 10.0,
                            "last_close": 9.8,
                            "source": "round5_stub",
                        }
                        for code in dict.fromkeys(requested)
                    }
                else:
                    result = original(codes, *args, **kwargs)
                try:
                    result_count = len(result or {})
                except TypeError:
                    result_count = 0
                return result
            except Exception as exc:
                status = "error"
                error = str(exc or exc.__class__.__name__)
                raise
            finally:
                ended_ms = self.rel_ms()
                unique_codes = list(dict.fromkeys(requested))
                source = _source_label(stack)
                self.append(
                    self.quote_calls,
                    {
                        "phase": self.phase(),
                        "started_at_ms": started_ms,
                        "ended_at_ms": ended_ms,
                        "elapsed_ms": round(ended_ms - started_ms, 3),
                        "requested_count": len(requested),
                        "unique_count": len(unique_codes),
                        "duplicate_in_batch": max(0, len(requested) - len(unique_codes)),
                        "codes": requested,
                        "signature": _batch_signature(requested),
                        "source": source,
                        "is_cache_only_source": source in INFO_SOURCE_TAB_KEYS,
                        "status": status,
                        "error": error,
                        "result_count": result_count,
                        "stack": stack,
                    },
                )

        provider.fetch_realtime_quotes_batch = _wrapped

    def wrap_background_runner(self) -> None:
        original = task_manager.run_in_background

        def _wrapped(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
            stack = _relative_stack()
            event = {
                "phase": self.phase(),
                "scheduled_at_ms": self.rel_ms(),
                "task_id": str(task_id or ""),
                "fn": getattr(fn, "__name__", fn.__class__.__name__),
                "source": _source_label(stack),
                "status": "scheduled",
                "stack": stack,
            }
            self.append(self.background_tasks, event)

            def _success(result):
                event["completed_at_ms"] = self.rel_ms()
                event["status"] = "success"
                if callable(on_success):
                    return on_success(result)
                return None

            def _error(message):
                event["completed_at_ms"] = self.rel_ms()
                event["status"] = "error"
                event["error"] = str(message or "")
                if callable(on_error):
                    return on_error(message)
                return None

            resolved = original(
                fn,
                *args,
                on_success=_success,
                on_error=_error,
                task_id=task_id,
                **kwargs,
            )
            event["resolved_task_id"] = str(resolved or "")
            return resolved

        task_manager.run_in_background = _wrapped
        self._restore_callbacks.append(lambda: setattr(task_manager, "run_in_background", original))

    def wrap_earnings_scheduler(self) -> None:
        try:
            import domains.earnings.scheduler as scheduler_module
        except ImportError:
            return
        scheduler_cls = getattr(scheduler_module, "EarningsScheduler", None)
        original = getattr(scheduler_cls, "_run_in_background", None)
        if scheduler_cls is None or not callable(original):
            return

        def _wrapped(instance, mode, missing_dates=None, target_date=None):
            stack = _relative_stack()
            self.append(
                self.background_tasks,
                {
                    "phase": self.phase(),
                    "scheduled_at_ms": self.rel_ms(),
                    "task_id": f"earnings_scheduler:{mode}",
                    "fn": "_run_in_background",
                    "source": "earnings",
                    "status": "scheduled",
                    "stack": stack,
                },
            )
            return original(instance, mode, missing_dates=missing_dates, target_date=target_date)

        scheduler_cls._run_in_background = _wrapped
        self._restore_callbacks.append(lambda: setattr(scheduler_cls, "_run_in_background", original))

    def wrap_tab_methods(self, workspace, tabs: tuple[str, ...]) -> None:
        for key in tabs:
            tab = _loaded_tab(workspace, key)
            if tab is None:
                continue
            for method_name in (
                "refresh_table_from_latest_snapshot",
                "refresh_table_quotes_and_market_caps",
                "refresh_data_after_f5",
                "_on_cache_reload_completed",
                "_schedule_context_refresh",
            ):
                self._wrap_tab_method(tab, key, method_name)

    def _wrap_tab_method(self, tab, key: str, method_name: str) -> None:
        original = getattr(tab, method_name, None)
        if not callable(original):
            return

        def _wrapped(_self=None, *args, **kwargs):
            started_ms = self.rel_ms()
            status = "ok"
            try:
                return original(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                ended_ms = self.rel_ms()
                self.append(
                    self.tab_calls,
                    {
                        "phase": self.phase(),
                        "tab": key,
                        "method": method_name,
                        "started_at_ms": started_ms,
                        "ended_at_ms": ended_ms,
                        "elapsed_ms": round(ended_ms - started_ms, 3),
                        "status": status,
                    },
                )

        setattr(tab, method_name, MethodType(_wrapped, tab))

    def restore(self) -> None:
        while self._restore_callbacks:
            callback = self._restore_callbacks.pop()
            try:
                callback()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass


def summarize_quote_calls(calls: list[dict]) -> dict:
    post_calls = [call for call in calls if call.get("phase") == "post_f5"]
    all_codes: list[str] = []
    duplicate_in_batch = 0
    signatures = []
    for call in post_calls:
        codes = list(call.get("codes") or [])
        all_codes.extend(codes)
        duplicate_in_batch += int(call.get("duplicate_in_batch") or 0)
        signature = str(call.get("signature") or "")
        if signature:
            signatures.append(signature)
    code_counts = Counter(all_codes)
    repeated_codes = {code: count for code, count in code_counts.items() if count > 1}
    signature_counts = Counter(signatures)
    repeated_signatures = {
        signature: count
        for signature, count in signature_counts.items()
        if count > 1
    }
    return {
        "batch_count": len(post_calls),
        "requested_count": len(all_codes),
        "unique_count": len(code_counts),
        "duplicate_in_batch": duplicate_in_batch,
        "duplicate_across_batches": int(sum(count - 1 for count in repeated_codes.values())),
        "duplicate_quote_code_count": int(sum(count - 1 for count in repeated_codes.values()) + duplicate_in_batch),
        "duplicates_by_code": repeated_codes,
        "repeated_batch_signature_count": int(sum(count - 1 for count in repeated_signatures.values())),
        "repeated_batch_signatures": repeated_signatures,
        "cache_only_quote_request_count": len([call for call in post_calls if call.get("is_cache_only_source")]),
        "calls": post_calls,
    }


def summarize_background_tasks(tasks: list[dict], final_sample: dict, baseline_task_ids: set[str]) -> dict:
    post_tasks = [task for task in tasks if task.get("phase") == "post_f5"]
    active_ids = set(final_sample.get("active_background_task_ids") or [])
    new_active = sorted(active_ids - baseline_task_ids)
    info_tasks = [
        task
        for task in post_tasks
        if any(
            marker in " ".join(
                (
                    str(task.get("task_id") or ""),
                    str(task.get("resolved_task_id") or ""),
                    str(task.get("fn") or ""),
                )
            )
            for marker in NETWORK_TASK_MARKERS
        )
    ]
    return {
        "scheduled_task_count": len(post_tasks),
        "information_source_task_count": len(info_tasks),
        "active_task_ids_final": sorted(active_ids),
        "new_active_task_ids_final": new_active,
        "new_active_task_final": len(new_active),
        "active_earnings_workers_final": final_sample.get("active_earnings_workers") or [],
        "active_earnings_worker_count_final": int(final_sample.get("active_earnings_worker_count") or 0),
        "tasks": post_tasks,
    }


def _force_quote_runtime(args: argparse.Namespace, window) -> None:
    if args.force_online:
        provider = getattr(window, "data_provider", None)
        set_online = getattr(provider, "set_online_mode", None)
        if callable(set_online):
            try:
                set_online(True)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
        if provider is not None and hasattr(provider, "_offline"):
            provider._offline = False

    if args.synthetic_quote_codes:
        codes = set(_clean_codes(args.synthetic_quote_codes))
        central = getattr(window, "central_quotes_svc", None)
        if central is not None:
            central._code_supplier = lambda codes=codes: set(codes)

    if args.force_quote_refresh_time:
        try:
            import ui.workers.central_quotes_worker as central_quotes_worker

            central_quotes_worker.MarketCalendar.is_quote_refresh_time = staticmethod(lambda *args, **kwargs: True)
        except (AttributeError, ImportError):
            pass


def run_post_f5_network_probe(args: argparse.Namespace, app: QApplication) -> dict:
    requested_tabs = tuple(dict.fromkeys(args.tabs or DEFAULT_TABS))
    tabs = _effective_probe_tabs(
        requested_tabs,
        isolate_info_source_refresh=bool(args.isolate_info_source_refresh),
    )
    recorder = PostF5NetworkRecorder(stub_quote_provider=args.stub_quote_provider)
    recorder.connect_event_counters()
    recorder.wrap_background_runner()
    recorder.wrap_earnings_scheduler()
    report = {
        "schema_version": 1,
        "probe": "perf_round5_post_f5_network",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": {
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "native_qt": bool(args.native_qt),
            "startup_enabled": bool(args.startup_enabled),
            "background_prewarm": bool(args.background_prewarm),
            "central_quotes_enabled": bool(args.central_quotes_enabled),
            "execute_post_f5_refresh": True,
            "stub_quote_provider": bool(args.stub_quote_provider),
            "force_online": bool(args.force_online),
            "force_quote_refresh_time": bool(args.force_quote_refresh_time),
            "isolate_rt_monitor_autostart": bool(args.isolate_rt_monitor_autostart),
            "isolate_info_source_refresh": bool(args.isolate_info_source_refresh),
            "synthetic_quote_codes": list(args.synthetic_quote_codes or []),
            "post_windows_s": list(args.post_windows),
            "requested_tabs": list(requested_tabs),
            "tabs": list(tabs),
        },
    }

    window = None
    try:
        window, startup = _build_window(args, app)
        report["startup"] = startup
        workspace = getattr(window, "_workspace", None)
        if workspace is None:
            report["post_f5"] = {"error": "workspace_unavailable"}
            return report

        if args.probe_tabs:
            report["tab_first_open"] = probe_tab_first_open(
                window,
                app,
                tabs,
                timeout_ms=args.tab_timeout_ms,
                settle_ms=args.tab_settle_ms,
            )
        if args.isolate_rt_monitor_autostart:
            report["rt_monitor_auto_start_isolation"] = disable_rt_monitor_auto_start(workspace)
        if args.isolate_info_source_refresh:
            report["information_source_refresh_isolation"] = disable_information_source_refresh_after_f5(workspace)
        _force_quote_runtime(args, window)
        recorder.wrap_provider(getattr(window, "data_provider", None))
        recorder.wrap_tab_methods(workspace, tabs)

        before = _runtime_timeline_sample(window, "post_f5:before", offset_s=None)
        baseline_task_ids = set(before.get("active_background_task_ids") or [])
        started = time.perf_counter()
        finish_f5_reload(window, count=123, elapsed=1.23, event_bus=event_bus)
        recorder.f5_returned_at = time.perf_counter()
        f5_return_elapsed_ms = round((recorder.f5_returned_at - started) * 1000.0, 3)

        timeline = []
        base_return = recorder.f5_returned_at
        for offset_s in sorted({max(0.0, float(item)) for item in args.post_windows}):
            while (time.perf_counter() - base_return) < offset_s:
                _settle(app, min(100, max(1, int((offset_s - (time.perf_counter() - base_return)) * 1000))))
            _settle(app, args.sample_settle_ms)
            timeline.append(_runtime_timeline_sample(window, f"post_f5:+{offset_s:g}s", offset_s=offset_s))

        final_sample = timeline[-1] if timeline else _runtime_timeline_sample(window, "post_f5:final", offset_s=None)
        quote_summary = summarize_quote_calls(list(recorder.quote_calls))
        background_summary = summarize_background_tasks(list(recorder.background_tasks), final_sample, baseline_task_ids)
        post_f5 = {
            "f5_return_elapsed_ms": f5_return_elapsed_ms,
            "timeline": timeline,
            "runtime_trend": _stability_trend([before, *timeline]),
            "event_counts": dict(recorder.event_counts),
            "event_receiver_trend": _event_receiver_trend([before, *timeline]),
            "tab_calls": [call for call in recorder.tab_calls if call.get("phase") == "post_f5"],
            "quote_requests": quote_summary,
            "background_tasks": background_summary,
            "cache_only_guard": {
                "cache_only_quote_request_count": quote_summary["cache_only_quote_request_count"],
                "information_source_background_task_count": background_summary["information_source_task_count"],
            },
            "loaded_information_source_tabs": sorted(_loaded_info_source_keys(workspace)),
        }
        report["post_f5"] = post_f5
    finally:
        if window is not None:
            try:
                window.close()
                _settle(app, 200)
                window.deleteLater()
                gc.collect()
                _settle(app, 100)
            finally:
                report["final_snapshot"] = collect_process_snapshot("round5_probe_end")
        recorder.restore()
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Round 5 post-F5 network synchronization timeline probe.")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--startup-enabled", action="store_true")
    parser.add_argument("--background-prewarm", action="store_true")
    parser.add_argument("--kline-prewarm-enabled", action="store_true")
    parser.add_argument("--central-quotes-enabled", action="store_true")
    parser.add_argument("--show-window", action="store_true")
    parser.add_argument("--startup-settle-ms", type=int, default=300)
    parser.add_argument("--tabs", nargs="*", default=list(DEFAULT_TABS))
    parser.add_argument("--probe-tabs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tab-timeout-ms", type=int, default=6000)
    parser.add_argument("--tab-settle-ms", type=int, default=100)
    parser.add_argument("--post-windows", nargs="*", type=float, default=[0, 1, 3, 10, 30])
    parser.add_argument("--sample-settle-ms", type=int, default=40)
    parser.add_argument("--synthetic-quote-codes", nargs="*", default=[])
    parser.add_argument("--stub-quote-provider", action="store_true")
    parser.add_argument("--force-online", action="store_true")
    parser.add_argument("--force-quote-refresh-time", action="store_true")
    parser.add_argument("--isolate-rt-monitor-autostart", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--isolate-info-source-refresh", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    app = QApplication.instance() or QApplication(sys.argv)
    report = run_post_f5_network_probe(args, app)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
