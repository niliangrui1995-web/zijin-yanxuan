from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

if "--native-qt" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_env import configure_qt_webengine_runtime

configure_qt_webengine_runtime()

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from app.services.ui_diagnostics_service import get_ui_stall_probe
from infra.diagnostics.runtime_health import collect_runtime_health_summary
from scripts.runtime_health_stability_suite import (
    _close_kline_charts,
    _kline_browser_ready,
    _kline_chart_closed,
    _kline_stage_contract_ready,
    _process_events,
    _trigger_kline_first_interaction,
    _wait_until,
)
from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER
from ui.main_window_qt import MainWindowQT

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

MINIMUM_LIFECYCLE_CYCLES = 10
MINIMUM_CACHED_SWITCH_SAMPLES = 10
MAX_RSS_NET_GROWTH_MB = 24.0
SYNTHETIC_PROVIDER_MODE = "offline-synthetic"
PRODUCTION_LOCAL_PROVIDER_MODE = "production-local"
SMOKE_PROVIDER_MODES = (SYNTHETIC_PROVIDER_MODE, PRODUCTION_LOCAL_PROVIDER_MODE)
MINIMUM_LOCAL_A_SHARE_BARS = 250
SMOKE_SETUP_TIMEOUT_MS = 40_000
_LOCAL_SOURCE_LAYERS = frozenset(
    {
        "legacy_parquet_bootstrap",
        "legacy_parquet_without_manifest",
        "memory_cache",
        "memory_cache_after_vipdoc",
        "parquet_sqlite_warehouse",
        "vipdoc_fallback",
    }
)
_UI_STALL_SCOPE = "kline_open_to_chart_ready"
_UI_STALL_REQUIRED_INTEGER_FIELDS = (
    "critical_count",
    "event_loop_critical_count",
)
_UI_STALL_REQUIRED_NUMBER_FIELDS = ("max_elapsed_ms",)
_ZERO_GROWTH_RESOURCE_FIELDS = (
    "thread_count",
    "background_task_count",
    "active_timer_count",
    "total_timer_count",
    "event_receiver_count",
    "webengine_child_count",
)
_NATIVE_LIFECYCLE_SCENARIOS = (
    "same_stock_multi_window",
    "visibility_pause_resume",
    "render_process_recovery",
)
_SAME_STOCK_REQUIRED_TRUE_FIELDS = (
    "same_code",
    "window_ids_distinct",
    "task_ids_distinct",
    "frame_owners_isolated",
    "browser_instances_distinct",
    "first_closed",
    "second_survived_first_close",
    "second_closed",
)
_VISIBILITY_REQUIRED_TRUE_FIELDS = (
    "browser_preserved",
    "identity_preserved",
    "latest_snapshot_owned_after_resume",
    "frame_owner_current",
    "closed",
)
_RECOVERY_REQUIRED_TRUE_FIELDS = (
    "guard_installed",
    "controlled_termination_emitted",
    "browser_replaced",
    "browser_epoch_advanced",
    "structure_ready",
    "chart_ready_after_recovery",
    "identity_preserved",
    "frame_owner_current",
    "latest_snapshot_identity_preserved",
    "ack_received",
    "last_snapshot_replayed",
    "at_most_one_recovery",
    "closed",
)


def _native_lifecycle_required(mode: dict[str, Any]) -> bool:
    return bool(
        mode.get("native_qt") is True
        and mode.get("allow_offscreen") is False
        and str(mode.get("qt_platform") or "").lower() != "offscreen"
        and mode.get("provider_mode") == PRODUCTION_LOCAL_PROVIDER_MODE
    )


def _new_native_lifecycle_report(mode: dict[str, Any]) -> dict[str, Any]:
    required = _native_lifecycle_required(mode)
    return {
        "required": required,
        "status": "pending" if required else "not_applicable",
        "provider_mode": mode.get("provider_mode"),
        "network_guard": {},
        **{field: {} for field in _NATIVE_LIFECYCLE_SCENARIOS},
    }


def _sample(label: str, window: MainWindowQT | None) -> dict[str, Any]:
    from ui.components.kline_window_manager import kline_manager

    report = collect_runtime_health_summary(window, kline_manager_instance=kline_manager)
    process = report.get("process") or {}
    webengine = report.get("webengine") or {}
    background_tasks = report.get("background_tasks") or {}
    timers = report.get("timers") or {}
    event_bus = report.get("event_bus") or {}
    return {
        "label": label,
        "rss_mb": process.get("rss_mb"),
        "thread_count": process.get("thread_count"),
        "webengine_available": webengine.get("available") is True,
        "webengine_child_count": webengine.get("count"),
        "webengine_rss_mb": webengine.get("rss_mb"),
        "webengine_private_mb": webengine.get("private_mb"),
        "webengine_processes": webengine.get("processes", []),
        "background_task_count": background_tasks.get("count"),
        "active_timer_count": timers.get("active"),
        "total_timer_count": timers.get("total"),
        "event_receiver_count": event_bus.get("total_receivers"),
    }


def _webengine_count(sample: MappingLike) -> int | None:
    if not isinstance(sample, dict) or sample.get("webengine_available") is not True:
        return None
    value = sample.get("webengine_child_count")
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (AttributeError, TypeError, ValueError):
        return None
    return count if count >= 0 else None


MappingLike = Any


class _OfflineSmokeDataProvider:
    """Deterministic local-only daily bars for isolating the WebEngine lifecycle."""

    def __init__(self) -> None:
        import pandas as pd

        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=260)
        closes = [10.0 + index * 0.01 + (index % 7) * 0.02 for index in range(len(dates))]
        opens = [close - 0.03 + (index % 3) * 0.01 for index, close in enumerate(closes)]
        self._frame = pd.DataFrame(
            {
                "open": opens,
                "high": [max(open_price, close) + 0.08 for open_price, close in zip(opens, closes, strict=True)],
                "low": [min(open_price, close) - 0.08 for open_price, close in zip(opens, closes, strict=True)],
                "close": closes,
                "volume": [100_000 + index * 1_000 for index in range(len(dates))],
            },
            index=dates,
        )
        self._cache_lock = threading.Lock()
        self._cached_frames: dict[str, Any] = {}
        self._cache_hits: dict[str, int] = {}

    provider_mode = SYNTHETIC_PROVIDER_MODE
    cache_source = "offline_synthetic_memory"

    def prime_codes(self, codes) -> None:
        with self._cache_lock:
            for value in codes or ():
                code = str(value or "").strip()
                if code:
                    self._cached_frames.setdefault(code, self._frame)
                    self._cache_hits.setdefault(code, 0)

    def get_data(self, code: str):
        normalized = str(code or "").strip()
        with self._cache_lock:
            frame = self._cached_frames.get(normalized)
            if frame is not None:
                self._cache_hits[normalized] = self._cache_hits.get(normalized, 0) + 1
        return frame.copy() if frame is not None else None

    def get_data_fresh_for_chart(self, code: str, **_kwargs):
        return self.get_data(code)

    def is_cached_code(self, code: str) -> bool:
        normalized = str(code or "").strip()
        with self._cache_lock:
            return normalized in self._cached_frames

    def cached_hit_count(self, code: str) -> int:
        normalized = str(code or "").strip()
        with self._cache_lock:
            return int(self._cache_hits.get(normalized, 0))

    @staticmethod
    def is_online() -> bool:
        return False

    def evidence(self) -> dict[str, Any]:
        with self._cache_lock:
            codes = sorted(self._cached_frames)
        return {
            "status": "ok",
            "mode": self.provider_mode,
            "provider_class": f"{self.__class__.__module__}.{self.__class__.__name__}",
            "production_provider_contract": False,
            "synthetic": True,
            "local_only": True,
            "read_only": True,
            "network_access_enabled": False,
            "network_request_count": 0,
            "cache_source": self.cache_source,
            "requested_codes": codes,
            "codes": {
                code: {
                    "status": "ok",
                    "row_count": len(self._frame),
                    "minimum_required_rows": MINIMUM_LOCAL_A_SHARE_BARS,
                    "source_layer": self.cache_source,
                }
                for code in codes
            },
        }


def _production_provider_class_name(provider) -> str:
    cls = provider.__class__
    return f"{cls.__module__}.{cls.__name__}"


def _is_production_provider(provider) -> bool:
    return _production_provider_class_name(provider) == (
        "infra.market_data.tdx_data_provider.TdxDataProvider"
    )


def _source_status(provider) -> dict[str, Any]:
    status = getattr(provider, "_last_market_data_source_status", None)
    return dict(status) if isinstance(status, dict) else {}


def _local_source_layer(status: dict[str, Any]) -> str:
    return str(status.get("active_layer") or "").strip()


def _is_local_source_layer(layer: str) -> bool:
    normalized = str(layer or "").strip().lower()
    return bool(
        normalized in _LOCAL_SOURCE_LAYERS
        or "parquet" in normalized
        or "vipdoc" in normalized
        or "memory_cache" in normalized
    )


def _copy_history_frame(value):
    if value is None:
        return None
    converter = getattr(value, "to_pandas", None)
    frame = converter() if callable(converter) else value
    copier = getattr(frame, "copy", None)
    return copier(deep=True) if callable(copier) else None


def _history_latest_date(frame) -> str:
    try:
        import pandas as pd

        date_column = next(
            (name for name in ("datetime", "date", "trade_date") if name in frame.columns),
            None,
        )
        value = frame[date_column].max() if date_column else frame.index.max()
        timestamp = pd.Timestamp(value)
    except (AttributeError, KeyError, TypeError, ValueError):
        return ""
    return "" if pd.isna(timestamp) else timestamp.date().isoformat()


def _production_local_source_evidence(status: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "active_layer",
        "data_status",
        "trade_date",
        "source",
        "source_version",
        "parquet_path",
        "fallback_reason",
    )
    return {field: status.get(field) for field in fields if status.get(field) not in (None, "")}


def _create_production_local_provider():
    from infra.market_data.tdx_data_provider import TdxDataProvider

    return TdxDataProvider(offline=True)


class _ProductionLocalSmokeDataProvider:
    """Freeze targeted bars from the production local provider behind a no-network port."""

    provider_mode = PRODUCTION_LOCAL_PROVIDER_MODE
    cache_source = "production_local_frozen_cache"

    def __init__(self, provider=None) -> None:
        self._provider = provider if provider is not None else _create_production_local_provider()
        self._cache_lock = threading.Lock()
        self._cached_frames: dict[str, Any] = {}
        self._cache_hits: dict[str, int] = {}
        self._code_evidence: dict[str, dict[str, Any]] = {}
        self._requested_codes: set[str] = set()

    def _network_guard_evidence(self) -> dict[str, Any]:
        server_pool = getattr(self._provider, "server_pool", None)
        offline = getattr(self._provider, "_offline", None) is True
        empty_server_pool = isinstance(server_pool, (list, tuple)) and not server_pool
        return {
            "underlying_offline": offline,
            "underlying_server_pool_size": len(server_pool) if isinstance(server_pool, (list, tuple)) else None,
            "network_guard_active": offline and empty_server_pool,
        }

    def _prime_one(self, code: str) -> None:
        with self._cache_lock:
            if code in self._cached_frames:
                return
        frame = _copy_history_frame(self._provider.get_data(code))
        row_count = len(frame) if frame is not None else 0
        source_status = _source_status(self._provider)
        source_layer = _local_source_layer(source_status)
        ready = bool(
            frame is not None
            and row_count >= MINIMUM_LOCAL_A_SHARE_BARS
            and _is_local_source_layer(source_layer)
        )
        evidence = {
            "status": "ok" if ready else "fail",
            "row_count": row_count,
            "minimum_required_rows": MINIMUM_LOCAL_A_SHARE_BARS,
            "latest_trade_date": _history_latest_date(frame) if frame is not None else "",
            "source_layer": source_layer,
            "source": _production_local_source_evidence(source_status),
        }
        with self._cache_lock:
            self._code_evidence[code] = evidence
            if ready:
                self._cached_frames[code] = frame
                self._cache_hits.setdefault(code, 0)
        if not ready:
            raise RuntimeError(
                f"production-local A-share history unavailable for {code}: "
                f"rows={row_count}, source={source_layer or 'unavailable'}"
            )

    def prime_codes(self, codes) -> None:
        normalized = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in (codes or ())
                if str(value or "").strip()
            )
        )
        self._requested_codes.update(normalized)
        guards = self._network_guard_evidence()
        if guards["network_guard_active"] is not True:
            raise RuntimeError("production-local provider network guard is not active")
        for code in normalized:
            self._prime_one(code)

    def get_data(self, code: str):
        normalized = str(code or "").strip()
        with self._cache_lock:
            frame = self._cached_frames.get(normalized)
            if frame is not None:
                self._cache_hits[normalized] = self._cache_hits.get(normalized, 0) + 1
        return _copy_history_frame(frame)

    def get_data_fresh_for_chart(self, code: str, **_kwargs):
        return self.get_data(code)

    def is_cached_code(self, code: str) -> bool:
        normalized = str(code or "").strip()
        with self._cache_lock:
            return normalized in self._cached_frames

    def cached_hit_count(self, code: str) -> int:
        normalized = str(code or "").strip()
        with self._cache_lock:
            return int(self._cache_hits.get(normalized, 0))

    @staticmethod
    def is_online() -> bool:
        return False

    def evidence(self) -> dict[str, Any]:
        guards = self._network_guard_evidence()
        with self._cache_lock:
            requested = sorted(self._requested_codes)
            codes = {code: dict(details) for code, details in sorted(self._code_evidence.items())}
        production_contract = _is_production_provider(self._provider)
        complete = bool(
            requested
            and set(codes) == set(requested)
            and all(item.get("status") == "ok" for item in codes.values())
        )
        status_ok = bool(production_contract and guards["network_guard_active"] and complete)
        return {
            "status": "ok" if status_ok else "fail",
            "mode": self.provider_mode,
            "provider_class": _production_provider_class_name(self._provider),
            "production_provider_contract": production_contract,
            "synthetic": False,
            "local_only": True,
            "read_only": True,
            "network_access_enabled": False,
            "network_request_count": 0,
            "network_policy": "offline production provider, then frozen targeted frames",
            "frozen_after_prime": True,
            "cache_source": self.cache_source,
            "requested_codes": requested,
            "codes": codes,
            **guards,
        }


def _ui_stall_failure(error: str, **details) -> dict[str, Any]:
    return {
        "installed": False,
        "scope": _UI_STALL_SCOPE,
        "reset_succeeded": False,
        "error": str(error or "stall_snapshot_unavailable"),
        **details,
    }


def _begin_ui_stall_scope(app) -> dict[str, Any]:
    """Flush unrelated work, then reset one real process-wide stall probe."""

    try:
        _process_events(app, rounds=2, sleep_ms=25)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"ready": False, "error": "stall_scope_settle_failed", "exception": str(exc)}
    try:
        probe = get_ui_stall_probe()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {"ready": False, "error": "stall_probe_lookup_failed", "exception": str(exc)}
    if probe is None:
        return {"ready": False, "error": "stall_probe_not_installed"}
    try:
        probe.reset_stall_snapshot()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "ready": False,
            "error": "stall_snapshot_reset_failed",
            "exception": str(exc),
        }
    return {"ready": True, "probe": probe}


def _nonnegative_integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _nonnegative_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _normalized_ui_stall_snapshot(snapshot) -> dict[str, Any]:
    raw = dict(snapshot) if isinstance(snapshot, dict) else {}
    integers = {field: _nonnegative_integer(raw.get(field)) for field in _UI_STALL_REQUIRED_INTEGER_FIELDS}
    numbers = {field: _nonnegative_number(raw.get(field)) for field in _UI_STALL_REQUIRED_NUMBER_FIELDS}
    valid = raw.get("installed") is True and all(
        value is not None for value in (*integers.values(), *numbers.values())
    )
    if not valid:
        return _ui_stall_failure(
            "stall_snapshot_invalid",
            probe_installed=raw.get("installed") is True,
            raw_snapshot=raw,
        )
    return {
        **raw,
        **integers,
        **numbers,
        "installed": True,
        "scope": _UI_STALL_SCOPE,
        "reset_succeeded": True,
    }


def _finish_ui_stall_scope(app, scope) -> dict[str, Any]:
    if not isinstance(scope, dict) or scope.get("ready") is not True:
        payload = dict(scope) if isinstance(scope, dict) else {}
        return _ui_stall_failure(
            str(payload.get("error") or "stall_scope_not_started"),
            exception=str(payload.get("exception") or ""),
        )
    try:
        # Let the precise event-loop timer observe the tail of the chart-ready callback.
        _process_events(app, rounds=2, sleep_ms=25)
        snapshot = scope["probe"].stall_snapshot()
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return _ui_stall_failure("stall_snapshot_capture_failed", exception=str(exc))
    return _normalized_ui_stall_snapshot(snapshot)


def _ui_stall_snapshot_valid(snapshot) -> bool:
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("installed") is True
        and all(_nonnegative_integer(snapshot.get(field)) is not None for field in _UI_STALL_REQUIRED_INTEGER_FIELDS)
        and all(_nonnegative_number(snapshot.get(field)) is not None for field in _UI_STALL_REQUIRED_NUMBER_FIELDS)
    )


def _page_only_keeper_shape(kline_manager) -> dict[str, Any]:
    try:
        reader = getattr(kline_manager, "runtime_health_snapshot", None)
        snapshot = dict(reader()) if callable(reader) else {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        snapshot = {}
    browser_count = _nonnegative_integer(snapshot.get("browser_count"))
    page_count = _nonnegative_integer(snapshot.get("page_count"))
    main_window_retained = bool(getattr(kline_manager, "_prewarm_main_window", None))
    verified = bool(browser_count == 0 and page_count == 1 and not main_window_retained)
    return {
        "mode": "page_only" if verified else "unexpected",
        "browser_count": browser_count,
        "page_count": page_count,
        "main_window_retained": main_window_retained,
        "verified": verified,
    }


def _capture_prewarmed_page(kline_manager):
    try:
        return getattr(kline_manager, "_prewarm_view", None)
    except (AttributeError, RuntimeError, TypeError):
        return None


def _prewarmed_page_reused(chart, prewarmed_page) -> bool:
    if chart is None or prewarmed_page is None:
        return False
    try:
        browser = getattr(chart, "browser", None)
        page_reader = getattr(browser, "page", None)
        return bool(callable(page_reader) and page_reader() is prewarmed_page)
    except (AttributeError, RuntimeError, TypeError):
        return False


def _smoke_runtime_ready(window: MainWindowQT, kline_manager) -> bool:
    keeper_count = int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0)
    return (
        bool(getattr(window, "_first_paint_recorded", False))
        and getattr(window, "data_provider", None) is not None
        and getattr(kline_manager, "_webengine_available", None) is True
        and not bool(getattr(kline_manager, "_prewarm_started", False))
        and keeper_count == 1
        and bool(getattr(kline_manager, "managed_webengine_keeper_ready", False))
        and _page_only_keeper_shape(kline_manager)["verified"]
    )


def _smoke_setup_report(window, kline_manager, *, ready: bool, scheduled: bool, timeout_ms: int) -> dict:
    keeper_count = int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0)
    keeper_shape = _page_only_keeper_shape(kline_manager)
    preflight = getattr(kline_manager, "_webengine_preflight_diagnostics", {})
    if not isinstance(preflight, dict):
        preflight = {}
    return {
        "status": "ok" if ready and keeper_count == 1 and keeper_shape["verified"] else "fail",
        "timeout_ms": int(timeout_ms),
        "first_paint_recorded": bool(getattr(window, "_first_paint_recorded", False)),
        "data_provider_ready": getattr(window, "data_provider", None) is not None,
        "preflight_scheduled": bool(scheduled),
        "preflight_pending": bool(
            getattr(kline_manager, "_webengine_preflight_started", False)
        ),
        "preflight_failure": str(
            getattr(kline_manager, "_webengine_failure", "") or ""
        ),
        "preflight_attempt_count": int(preflight.get("attempt_count") or 0),
        "preflight_attempts": list(preflight.get("attempts") or []),
        "webengine_available": getattr(kline_manager, "_webengine_available", None) is True,
        "prewarm_pending": bool(getattr(kline_manager, "_prewarm_started", False)),
        "managed_webengine_keeper_count": keeper_count,
        "managed_webengine_keeper_ready": bool(
            getattr(kline_manager, "managed_webengine_keeper_ready", False)
        ),
        "keeper_shape": keeper_shape,
        "prewarm_failure": str(getattr(kline_manager, "_prewarm_failure", "") or ""),
    }


def _prepare_smoke_runtime(app: QApplication, window: MainWindowQT, *, timeout_ms: int) -> dict[str, Any]:
    from ui.components.kline_window_manager import kline_manager

    window.show()
    preflight_scheduled = kline_manager.prewarm(
        delay_ms=0,
        hidden_view=True,
    )
    ready = _wait_until(
        app,
        lambda: _smoke_runtime_ready(window, kline_manager),
        timeout_ms=max(1, int(timeout_ms)),
        step_ms=25,
    )
    return _smoke_setup_report(
        window,
        kline_manager,
        ready=ready,
        scheduled=preflight_scheduled,
        timeout_ms=timeout_ms,
    )


def _webengine_sample_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [_webengine_count(sample) for sample in samples]
    diagnostics_available = bool(counts) and all(count is not None for count in counts)
    valid_counts = [count for count in counts if count is not None]
    return {
        "diagnostics_available": diagnostics_available,
        "counts": valid_counts,
        "baseline": valid_counts[0] if diagnostics_available else None,
        "final": valid_counts[-1] if diagnostics_available else None,
        "maximum": max(valid_counts) if diagnostics_available else None,
    }


def _webengine_child_seen(stats: dict[str, Any]) -> bool:
    baseline = stats["baseline"]
    maximum = stats["maximum"]
    counts = stats["counts"]
    return bool(
        stats["diagnostics_available"]
        and baseline is not None
        and maximum is not None
        and (any(count > baseline for count in counts[1:]) or (baseline > 0 and maximum > 0))
    )


def _webengine_child_reclaimed(stats: dict[str, Any]) -> bool:
    baseline = stats["baseline"]
    final = stats["final"]
    return bool(
        stats["diagnostics_available"]
        and final is not None
        and baseline is not None
        and final <= baseline
    )


def _lifecycle_succeeded(
    *, browser_ready: bool, chart_ready: bool, blocked: bool, closed: bool,
    diagnostics_available: bool, child_seen: bool, reclaimed: bool, load_succeeded: bool,
) -> bool:
    return bool(
        browser_ready
        and chart_ready
        and not blocked
        and closed
        and diagnostics_available
        and child_seen
        and reclaimed
        and load_succeeded
    )


def evaluate_lifecycle(
    samples: list[dict[str, Any]],
    *,
    browser_ready: bool,
    chart_ready: bool,
    blocked: bool,
    closed: bool,
    load_events: list[bool],
) -> dict:
    stats = _webengine_sample_stats(samples)
    child_seen = _webengine_child_seen(stats)
    reclaimed = _webengine_child_reclaimed(stats)
    load_succeeded = bool(load_events) and load_events[-1] is True
    load_failed = not load_succeeded
    succeeded = _lifecycle_succeeded(
        browser_ready=browser_ready,
        chart_ready=chart_ready,
        blocked=blocked,
        closed=closed,
        diagnostics_available=stats["diagnostics_available"],
        child_seen=child_seen,
        reclaimed=reclaimed,
        load_succeeded=load_succeeded,
    )
    return {
        "status": "ok" if succeeded else "fail",
        "opened": bool(browser_ready),
        "open_success_criterion": "chart_ready",
        "browser_ready": bool(browser_ready),
        "chart_ready": bool(chart_ready),
        "blocked": bool(blocked),
        "closed": bool(closed),
        "webengine_diagnostics_available": stats["diagnostics_available"],
        "webengine_child_seen": bool(child_seen),
        "webengine_child_reclaimed": bool(reclaimed),
        "baseline_webengine_child_count": stats["baseline"],
        "max_webengine_child_count": stats["maximum"],
        "final_webengine_child_count": stats["final"],
        "load_events": list(load_events),
        "load_succeeded": load_succeeded,
        "load_failed": load_failed,
    }


def _chart_render_ready(chart, load_events: list[bool]) -> bool:
    try:
        browser_epoch = int(getattr(chart, "_browser_epoch", -1))
        load_epoch = int(getattr(chart, "_last_shell_load_epoch", -2))
        page_loaded = load_epoch == browser_epoch and getattr(chart, "_last_shell_load_ok", None) is True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        page_loaded = False
    if not page_loaded:
        return False
    try:
        stages = getattr(chart, "_open_stages", None)
        ready = "chart_ready" in set(getattr(stages, "recorded_stages", set()) or set())
    except (AttributeError, RuntimeError, TypeError):
        return False
    if ready and (not load_events or load_events[-1] is not True):
        load_events.append(True)
    return ready


def _normalized_smoke_arg(args: argparse.Namespace, field: str, fallback: str) -> str:
    value = str(getattr(args, field, "") or "").strip()
    if value:
        return value
    return fallback


def _alternate_smoke_code(code: str) -> str:
    if code == "000002":
        return "000001"
    return "000002"


def _smoke_navigation_row(code: str, name: str) -> dict[str, str]:
    return {"code": code, "name": name, "\u4ee3\u7801": code, "\u540d\u79f0": name}


def _smoke_navigation(args: argparse.Namespace) -> list[dict[str, str]]:
    code = _normalized_smoke_arg(args, "code", "000001")
    name = _normalized_smoke_arg(args, "name", code)
    switch_code = _normalized_smoke_arg(args, "switch_code", "000002")
    if switch_code == code:
        switch_code = _alternate_smoke_code(code)
    switch_name = _normalized_smoke_arg(args, "switch_name", switch_code)
    return [
        _smoke_navigation_row(code, name),
        _smoke_navigation_row(switch_code, switch_name),
    ]


def _matching_managed_chart(kline_manager, *, code: str, excluded_ids: set[int]):
    for chart in tuple(getattr(kline_manager, "_charts", ()) or ()):
        try:
            if (
                id(chart) not in excluded_ids
                and str(getattr(chart, "code", "") or "").strip() == code
                and not bool(getattr(chart, "_closing", False))
            ):
                return chart
        except (AttributeError, RuntimeError, TypeError):
            continue
    return None


def _open_cycle_chart(app, kline_manager, window, args: argparse.Namespace, cycle: dict):
    rows = _smoke_navigation(args)
    row = rows[0]
    code = row["code"]
    name = row["name"]
    provider = getattr(window, "data_provider", None)
    prime_codes = getattr(provider, "prime_codes", None)
    existing_chart_ids = {
        id(chart) for chart in tuple(getattr(kline_manager, "_charts", ()) or ())
    }
    try:
        if callable(prime_codes):
            prime_codes(item["code"] for item in rows)
        chart = kline_manager.open_chart(
            window,
            code,
            name,
            provider,
            row,
            rows,
            0,
        )
        if chart is not None:
            return chart
        pending_chart = None

        def _pending_open_resumed() -> bool:
            nonlocal pending_chart
            pending_chart = _matching_managed_chart(
                kline_manager,
                code=code,
                excluded_ids=existing_chart_ids,
            )
            return pending_chart is not None

        _wait_until(
            app,
            _pending_open_resumed,
            timeout_ms=max(1, int(args.open_timeout_ms)),
            step_ms=25,
        )
        return pending_chart
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        cycle["open_error"] = {"type": exc.__class__.__name__, "message": str(exc)}
        return None


def _connect_chart_load_signal(chart, load_events: list[bool]):
    browser = getattr(chart, "browser", None)
    load_signal = getattr(browser, "loadFinished", None)
    if load_signal is None:
        return None, None

    def _record_load_finished(ok) -> None:
        load_events.append(bool(ok))

    try:
        load_signal.connect(_record_load_finished)
    except (AttributeError, RuntimeError, TypeError):
        return load_signal, None
    return load_signal, _record_load_finished


def _snapshot_evidence(snapshot) -> dict[str, Any]:
    if snapshot is None:
        return {}
    try:
        return {
            "window_id": str(snapshot.window_id),
            "generation": int(snapshot.generation),
            "code": str(snapshot.code),
            "points": int(snapshot.points),
            "version": snapshot.version,
        }
    except (AttributeError, TypeError, ValueError):
        return {}


def _chart_ownership_evidence(chart) -> dict[str, Any]:
    controller = getattr(chart, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    frame_owner = getattr(controller, "frame_owner", None)
    lifecycle = getattr(chart, "_runtime_lifecycle", None)
    task_id = None
    try:
        if identity is not None:
            task_id = controller.task_id("history", identity=identity)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        task_id = None
    return {
        "window_id": str(getattr(identity, "window_id", "") or ""),
        "generation": getattr(identity, "generation", None),
        "code": str(getattr(identity, "code", "") or ""),
        "task_id": task_id,
        "frame_owner_window_id": str(getattr(frame_owner, "window_id", "") or ""),
        "frame_owner_generation": getattr(frame_owner, "generation", None),
        "frame_owner_code": str(getattr(frame_owner, "code", "") or ""),
        "frame_owner_current": identity is not None and frame_owner == identity,
        "latest_snapshot": _snapshot_evidence(getattr(lifecycle, "latest_snapshot", None)),
    }


def _open_acceptance_chart(app, kline_manager, window, args, label: str):
    probe = {"label": label, "load_status": {}}
    chart = _open_cycle_chart(app, kline_manager, window, args, probe)
    load_events: list[bool] = []
    browser_ready = bool(
        chart is not None
        and _wait_until(
            app,
            lambda: _kline_browser_ready(chart),
            timeout_ms=args.open_timeout_ms,
            step_ms=25,
        )
    )
    chart_ready = bool(
        browser_ready
        and _wait_until(
            app,
            lambda: _chart_render_ready(chart, load_events),
            timeout_ms=args.open_timeout_ms,
            step_ms=25,
        )
    )
    return chart, {
        "browser_ready": browser_ready,
        "chart_ready": chart_ready,
        "load_events": load_events,
        "open_error": probe.get("open_error"),
    }


def _close_acceptance_chart(app, chart, *, timeout_ms: int) -> bool:
    if chart is None:
        return False
    if _kline_chart_closed(chart):
        return True
    try:
        if chart.close() is False:
            return False
    except RuntimeError:
        return _kline_chart_closed(chart)
    return _wait_until(
        app,
        lambda: _kline_chart_closed(chart),
        timeout_ms=max(1, int(timeout_ms)),
        step_ms=25,
    )


def _same_stock_isolation_fields(first: dict, second: dict) -> dict[str, bool]:
    window_ids_distinct = bool(
        first.get("window_id")
        and second.get("window_id")
        and first["window_id"] != second["window_id"]
    )
    task_ids_distinct = bool(
        first.get("task_id")
        and second.get("task_id")
        and first["task_id"] != second["task_id"]
    )
    frame_owners_isolated = bool(
        first.get("frame_owner_current") is True
        and second.get("frame_owner_current") is True
        and first.get("frame_owner_window_id") != second.get("frame_owner_window_id")
    )
    return {
        "same_code": bool(first.get("code") and first.get("code") == second.get("code")),
        "window_ids_distinct": window_ids_distinct,
        "task_ids_distinct": task_ids_distinct,
        "frame_owners_isolated": frame_owners_isolated,
    }


def _open_same_stock_pair(app, kline_manager, window, args):
    first, first_open = _open_acceptance_chart(
        app, kline_manager, window, args, "same_stock:first"
    )
    second, second_open = _open_acceptance_chart(
        app, kline_manager, window, args, "same_stock:second"
    )
    return first, second, first_open, second_open


def _same_stock_second_survived(second, second_browser, first_closed: bool) -> bool:
    checks = (
        first_closed,
        second is not None,
        not getattr(second, "_closing", False),
        getattr(second, "browser", None) is second_browser,
        _chart_render_ready(second, []),
    )
    return all(checks)


def _same_stock_report_ok(report: dict) -> bool:
    opens_ready = (
        (report.get("first_open") or {}).get("chart_ready") is True,
        (report.get("second_open") or {}).get("chart_ready") is True,
    )
    return all(
        (*opens_ready, *(report.get(field) is True for field in _SAME_STOCK_REQUIRED_TRUE_FIELDS))
    )


def _run_same_stock_multi_window(app, window, args) -> dict[str, Any]:
    from ui.components.kline_window_manager import kline_manager

    first = second = None
    first_closed = False
    report: dict[str, Any] = {"status": "fail"}
    try:
        first, second, first_open, second_open = _open_same_stock_pair(
            app, kline_manager, window, args
        )
        first_owner = _chart_ownership_evidence(first)
        second_owner = _chart_ownership_evidence(second)
        first_browser = getattr(first, "browser", None)
        second_browser = getattr(second, "browser", None)
        first_closed = _close_acceptance_chart(
            app, first, timeout_ms=args.close_timeout_ms
        )
        report = {
            "first_open": first_open,
            "second_open": second_open,
            "first": first_owner,
            "second": second_owner,
            **_same_stock_isolation_fields(first_owner, second_owner),
            "browser_instances_distinct": all(
                (first_browser is not None, second_browser is not None, first_browser is not second_browser)
            ),
            "second_survived_first_close": _same_stock_second_survived(
                second, second_browser, first_closed
            ),
        }
    finally:
        if not first_closed:
            first_closed = _close_acceptance_chart(
                app, first, timeout_ms=args.close_timeout_ms
            )
        second_closed = _close_acceptance_chart(
            app, second, timeout_ms=args.close_timeout_ms
        )
    report.update(first_closed=first_closed, second_closed=second_closed)
    report["status"] = "ok" if _same_stock_report_ok(report) else "fail"
    return report


def _run_visibility_transition(app, chart, args, *, minimized: bool) -> dict[str, Any]:
    mode = "minimized" if minimized else "hidden"
    pause_error = _set_chart_paused_visibility(chart, minimized=minimized)
    if pause_error:
        return {"status": "fail", "mode": mode, "error": pause_error}
    pause_observed = _wait_until(
        app,
        lambda: _chart_visibility_paused(chart, minimized=minimized),
        timeout_ms=args.open_timeout_ms,
        step_ms=25,
    )
    resume_error = _restore_chart_visibility(chart, minimized=minimized)
    if resume_error:
        return {
            "status": "fail",
            "mode": mode,
            "pause_observed": pause_observed,
            "error": resume_error,
        }
    resume_observed = _wait_until(
        app,
        lambda: _chart_visibility_resumed(chart),
        timeout_ms=args.open_timeout_ms,
        step_ms=25,
    )
    chart_ready = bool(resume_observed and _chart_render_ready(chart, []))
    succeeded = all((pause_observed, resume_observed, chart_ready))
    return {
        "status": "ok" if succeeded else "fail",
        "mode": mode,
        "pause_observed": bool(pause_observed),
        "runtime_reactivated": bool(resume_observed),
        "chart_ready_after_resume": chart_ready,
    }


def _set_chart_paused_visibility(chart, *, minimized: bool) -> str:
    try:
        if minimized:
            chart.showMinimized()
        else:
            chart.hide()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return str(exc)
    return ""


def _chart_visibility_paused(chart, *, minimized: bool) -> bool:
    state_observed = chart.isMinimized() if minimized else chart.isHidden()
    return all((getattr(chart, "_runtime_active", None) is False, state_observed))


def _restore_chart_visibility(chart, *, minimized: bool) -> str:
    try:
        chart.showNormal() if minimized else chart.show()
        chart.raise_()
        chart.activateWindow()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return str(exc)
    return ""


def _chart_visibility_resumed(chart) -> bool:
    return all(
        (
            getattr(chart, "_runtime_active", None) is True,
            chart.isVisible(),
            not chart.isMinimized(),
        )
    )


def _ownership_identity_preserved(before: dict, after: dict) -> bool:
    fields = ("window_id", "generation", "code")
    return bool(before.get("window_id") and all(before.get(field) == after.get(field) for field in fields))


def _latest_snapshot_owned_by_window(ownership: dict) -> bool:
    snapshot = ownership.get("latest_snapshot") or {}
    fields = ("window_id", "generation", "code")
    return bool(snapshot.get("window_id") and all(snapshot.get(field) == ownership.get(field) for field in fields))


def _run_visibility_pause_resume(app, window, args) -> dict[str, Any]:
    from ui.components.kline_window_manager import kline_manager

    chart = None
    closed = False
    report: dict[str, Any] = {"status": "fail"}
    try:
        chart, opened = _open_acceptance_chart(
            app, kline_manager, window, args, "visibility"
        )
        before = _chart_ownership_evidence(chart)
        browser = getattr(chart, "browser", None)
        hidden = _run_visibility_transition(app, chart, args, minimized=False)
        minimized = _run_visibility_transition(app, chart, args, minimized=True)
        after = _chart_ownership_evidence(chart)
        report = {
            "open": opened,
            "hidden": hidden,
            "minimized": minimized,
            "browser_preserved": browser is not None
            and getattr(chart, "browser", None) is browser,
            "identity_preserved": _ownership_identity_preserved(before, after),
            "latest_snapshot_owned_after_resume": _latest_snapshot_owned_by_window(after),
            "frame_owner_current": after.get("frame_owner_current") is True,
        }
    finally:
        closed = _close_acceptance_chart(
            app, chart, timeout_ms=args.close_timeout_ms
        )
    report["closed"] = closed
    required = (
        ((report.get("open") or {}).get("chart_ready") is True),
        ((report.get("hidden") or {}).get("status") == "ok"),
        ((report.get("minimized") or {}).get("status") == "ok"),
        report.get("browser_preserved") is True,
        report.get("identity_preserved") is True,
        report.get("latest_snapshot_owned_after_resume") is True,
        report.get("frame_owner_current") is True,
        closed,
    )
    report["status"] = "ok" if all(required) else "fail"
    return report


def _emit_controlled_renderer_termination(chart) -> bool:
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage

        signal = chart.browser.page().renderProcessTerminated
        status = QWebEnginePage.RenderProcessTerminationStatus.CrashedTerminationStatus
        signal.emit(status, 86)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return False
    return True


def _recovery_structure_ready(chart, old_browser, old_epoch: int, snapshot: dict) -> bool:
    lifecycle = getattr(chart, "_runtime_lifecycle", None)
    try:
        browser_epoch = int(getattr(chart, "_browser_epoch", 0) or 0)
        load_epoch = int(getattr(chart, "_last_shell_load_epoch", -1) or -1)
    except (TypeError, ValueError):
        return False
    checks = (
        getattr(chart, "browser", None) is not None,
        getattr(chart, "browser", None) is not old_browser,
        browser_epoch > old_epoch,
        load_epoch == browser_epoch,
        getattr(chart, "_last_shell_load_ok", None) is True,
        getattr(chart, "_shell_loaded", None) is True,
        getattr(chart, "_snapshot_inflight", None) is None,
        getattr(lifecycle, "_pending_snapshot", object()) is None,
        _snapshot_evidence(getattr(lifecycle, "latest_snapshot", None)) == snapshot,
    )
    return all(checks)


def _probe_rendered_snapshot(app, chart, snapshot, *, timeout_ms: int) -> dict[str, bool]:
    from ui.kline_render_bridge import (
        build_snapshot_render_state_script,
        snapshot_render_ack_matches,
    )

    state = {"pending": False, "received": False, "matched": False}
    script = build_snapshot_render_state_script(snapshot)

    def _record(ack) -> None:
        state["pending"] = False
        state["received"] = True
        state["matched"] = snapshot_render_ack_matches(snapshot, ack)

    def _poll() -> bool:
        if state["matched"]:
            return True
        if state["pending"]:
            return False
        state["pending"] = True
        try:
            chart.browser.page().runJavaScript(script, _record)
        except (AttributeError, RuntimeError, TypeError):
            state["pending"] = False
        return False

    matched = _wait_until(
        app,
        _poll,
        timeout_ms=max(1, int(timeout_ms)),
        step_ms=25,
    )
    return {
        "ack_received": state["received"],
        "last_snapshot_replayed": bool(matched and state["matched"]),
    }


def _second_recovery_allowed(chart) -> bool | None:
    lifecycle = getattr(chart, "_runtime_lifecycle", None)
    browser = getattr(chart, "browser", None)
    try:
        return bool(lifecycle.request_recovery(browser).allowed)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _recovery_baseline(chart):
    lifecycle = getattr(chart, "_runtime_lifecycle", None)
    snapshot = getattr(lifecycle, "latest_snapshot", None)
    browser = getattr(chart, "browser", None)
    epoch = int(getattr(chart, "_browser_epoch", 0) or 0)
    guard_installed = getattr(browser, "_kline_render_process_callback", None) is not None
    return _chart_ownership_evidence(chart), snapshot, browser, epoch, guard_installed


def _wait_for_recovery_structure(app, chart, args, browser, epoch, before: dict) -> bool:
    return _wait_until(
        app,
        lambda: _recovery_structure_ready(
            chart, browser, epoch, before.get("latest_snapshot") or {}
        ),
        timeout_ms=args.open_timeout_ms,
        step_ms=25,
    )


def _post_recovery_probes(app, chart, snapshot, args, structure_ready: bool):
    if not structure_ready:
        return {"ack_received": False, "last_snapshot_replayed": False}, None
    replay = _probe_rendered_snapshot(
        app, chart, snapshot, timeout_ms=args.open_timeout_ms
    )
    return replay, _second_recovery_allowed(chart)


def _recovery_observation(
    chart, before, old_browser, old_epoch, guard_installed, signal_emitted,
    structure_ready, replay, second_allowed,
) -> dict[str, Any]:
    after = _chart_ownership_evidence(chart)
    current_epoch = int(getattr(chart, "_browser_epoch", 0) or 0)
    lifecycle = getattr(chart, "_runtime_lifecycle", None)
    recovery_used = bool(getattr(lifecycle, "_recovery_used", False))
    return {
        "guard_installed": guard_installed,
        "controlled_termination_emitted": signal_emitted,
        "recovery_attempts": 1 if signal_emitted and recovery_used else 0,
        "recovery_limit": 1,
        "browser_replaced": getattr(chart, "browser", None) is not old_browser,
        "browser_epoch_advanced": current_epoch > old_epoch,
        "structure_ready": structure_ready,
        "chart_ready_after_recovery": bool(
            structure_ready and _chart_render_ready(chart, [])
        ),
        "identity_preserved": _ownership_identity_preserved(before, after),
        "frame_owner_current": after.get("frame_owner_current") is True,
        "latest_snapshot_identity_preserved": bool(
            before.get("latest_snapshot")
            and before.get("latest_snapshot") == after.get("latest_snapshot")
        ),
        **replay,
        "second_recovery_allowed": second_allowed,
        "at_most_one_recovery": second_allowed is False,
    }


def _recovery_report_ok(report: dict) -> bool:
    checks = (
        (report.get("open") or {}).get("chart_ready") is True,
        *(report.get(field) is True for field in _RECOVERY_REQUIRED_TRUE_FIELDS),
        report.get("recovery_attempts") == 1,
        report.get("recovery_limit") == 1,
        report.get("second_recovery_allowed") is False,
    )
    return all(checks)


def _run_render_process_recovery(app, window, args) -> dict[str, Any]:
    from ui.components.kline_window_manager import kline_manager

    chart = None
    report: dict[str, Any] = {"status": "fail"}
    try:
        chart, opened = _open_acceptance_chart(
            app, kline_manager, window, args, "render_process_recovery"
        )
        before, snapshot, old_browser, old_epoch, guard_installed = _recovery_baseline(chart)
        signal_emitted = False
        if snapshot is not None:
            signal_emitted = _emit_controlled_renderer_termination(chart)
        structure_ready = bool(
            signal_emitted
            and _wait_for_recovery_structure(
                app, chart, args, old_browser, old_epoch, before
            )
        )
        replay, second_allowed = _post_recovery_probes(
            app, chart, snapshot, args, structure_ready
        )
        report = {
            "open": opened,
            **_recovery_observation(
                chart, before, old_browser, old_epoch, guard_installed,
                signal_emitted, structure_ready, replay, second_allowed,
            ),
        }
    finally:
        closed = _close_acceptance_chart(
            app, chart, timeout_ms=args.close_timeout_ms
        )
    report["closed"] = closed
    report["status"] = "ok" if _recovery_report_ok(report) else "fail"
    return report


def _chart_status_text(chart) -> str:
    try:
        status_label = getattr(chart, "info_lbl", None)
        return str(status_label.text() or "") if status_label else ""
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _cycle_label(cycle: dict) -> str:
    label = str(cycle.get("label") or "").strip()
    return label or f"cycle_{int(cycle.get('cycle_index') or 0)}"


def _wait_for_first_interaction(app, chart, args, *, chart_ready: bool) -> tuple[bool, bool]:
    if not chart_ready:
        return False, False
    triggered = _trigger_kline_first_interaction(chart)
    if not triggered:
        return False, False
    ready = _wait_until(
        app,
        lambda: _kline_stage_contract_ready(chart),
        timeout_ms=args.open_timeout_ms,
        step_ms=10,
    )
    return True, ready


def _observe_cycle_chart(
    app,
    window,
    args,
    cycle: dict,
    chart,
    load_events: list[bool],
    *,
    stall_scope=None,
    prewarmed_page=None,
) -> dict:
    browser_ready = bool(
        chart is not None
        and _wait_until(app, lambda: _kline_browser_ready(chart), timeout_ms=args.open_timeout_ms, step_ms=25)
    )
    if cycle.get("measurement_role") == "cold_warmup":
        cycle["prewarmed_page_reused"] = bool(
            browser_ready and _prewarmed_page_reused(chart, prewarmed_page)
        )
    load_signal, load_callback = (None, None)
    if browser_ready:
        load_signal, load_callback = _connect_chart_load_signal(chart, load_events)
    cycle["browser_ready"] = browser_ready
    cycle_label = _cycle_label(cycle)
    chart_ready = False
    if browser_ready:
        chart_ready = _wait_until(
            app,
            lambda: _chart_render_ready(chart, load_events),
            timeout_ms=args.open_timeout_ms,
            step_ms=25,
        )
        cycle["load_status"]["status_text"] = _chart_status_text(chart)
    cycle["ui_stalls"] = _finish_ui_stall_scope(app, stall_scope)
    first_interaction_triggered, first_interaction_ready = _wait_for_first_interaction(
        app,
        chart,
        args,
        chart_ready=chart_ready,
    )
    sample_suffix = "after_chart_ready" if chart_ready else "after_open_wait"
    cycle["samples"].append(_sample(f"{cycle_label}:{sample_suffix}", window))
    stages = getattr(chart, "_open_stages", None) if chart is not None else None
    stage_diagnostics = getattr(stages, "stage_diagnostics", None)
    cycle["stage_diagnostics"] = stage_diagnostics() if callable(stage_diagnostics) else {}
    cycle["browser_attach_diagnostics"] = dict(
        getattr(chart, "_browser_attach_diagnostics", None) or {}
    )
    cycle["chart_ready"] = chart_ready
    cycle["first_interaction_triggered"] = first_interaction_triggered
    cycle["first_interaction_ready"] = first_interaction_ready
    return {
        "browser_ready": browser_ready,
        "chart_ready": chart_ready,
        "first_interaction_ready": first_interaction_ready,
        "load_signal": load_signal,
        "load_callback": load_callback,
    }


def _cached_hit_count(provider, code: str) -> int | None:
    reader = getattr(provider, "cached_hit_count", None)
    if not callable(reader):
        return None
    try:
        return _nonnegative_integer(reader(code))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _provider_cache_source(provider) -> str:
    return str(getattr(provider, "cache_source", "") or "provider_memory_cache").strip()


def _provider_mode(provider) -> str:
    return str(getattr(provider, "provider_mode", "") or "unknown").strip()


def _cached_switch_target(chart) -> tuple[int, str] | None:
    rows = getattr(chart, "code_list", None)
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    current_idx = _nonnegative_integer(getattr(chart, "current_idx", None))
    target_idx = 1 if current_idx == 0 else 0
    if not 0 <= target_idx < len(rows) or not isinstance(rows[target_idx], dict):
        return None
    code = str(rows[target_idx].get("\u4ee3\u7801") or rows[target_idx].get("code") or "").strip()
    return (target_idx, code) if code else None


def _cached_switch_committed(chart, target_code: str, previous_generation: int) -> bool:
    controller = getattr(chart, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    owner = getattr(controller, "frame_owner", None)
    try:
        return bool(
            str(getattr(chart, "code", "") or "").strip() == target_code
            and identity is not None
            and owner == identity
            and str(getattr(identity, "code", "") or "").strip() == target_code
            and int(getattr(identity, "generation", 0) or 0) > int(previous_generation)
            and getattr(chart, "_snapshot_inflight", None) is None
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _cached_switch_failure(error: str, **details) -> dict[str, Any]:
    return {
        "status": "fail",
        "measurement": "real_cached_stock_switch_commit",
        "commit_criterion": "load_controller_frame_owner_after_echarts_rendered_ack",
        "error": str(error or "cached_switch_unavailable"),
        **details,
    }


def _cached_switch_cache_evidence(chart, target_code: str) -> tuple[Any, bool, int | None]:
    provider = getattr(chart, "data_provider", None)
    is_cached = getattr(provider, "is_cached_code", None)
    try:
        cache_verified = bool(callable(is_cached) and is_cached(target_code))
    except AttributeError, RuntimeError, TypeError, ValueError:
        cache_verified = False
    return provider, cache_verified, _cached_hit_count(provider, target_code)


def _cached_switch_runtime(chart) -> tuple[Any, int | None, Any]:
    controller = getattr(chart, "_load_controller", None)
    previous = getattr(controller, "current_identity", None)
    generation = _nonnegative_integer(getattr(previous, "generation", None))
    return controller, generation, getattr(chart, "_switch_to_stock", None)


def _dispatch_cached_switch(switch, target_idx: int, target_code: str) -> tuple[float | None, dict | None]:
    started_at = time.perf_counter()
    try:
        switch(target_idx)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return None, _cached_switch_failure(
            "cached_switch_dispatch_failed",
            target_code=target_code,
            cache_verified=True,
            exception=str(exc),
        )
    return started_at, None


def _await_cached_switch(
    app,
    chart,
    args,
    target_code: str,
    previous_generation: int,
    started_at: float,
) -> tuple[Any, float | None, dict | None]:
    try:
        committed = _wait_until(
            app,
            lambda: _cached_switch_committed(chart, target_code, previous_generation),
            timeout_ms=max(1, int(getattr(args, "switch_timeout_ms", 3000))),
            step_ms=10,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return (
            None,
            None,
            _cached_switch_failure(
                "cached_switch_wait_failed",
                target_code=target_code,
                cache_verified=True,
                exception=str(exc),
                observed_elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
            ),
        )
    return committed, (time.perf_counter() - started_at) * 1000.0, None


def _cached_switch_measurement(
    controller,
    provider,
    target_code: str,
    before_hits: int,
    committed,
    elapsed_ms: float,
) -> dict[str, Any]:
    after_hits = _cached_hit_count(provider, target_code)
    hit_delta = None if after_hits is None else after_hits - before_hits
    if not committed or hit_delta is None or hit_delta <= 0:
        return _cached_switch_failure(
            "cached_switch_commit_timeout" if not committed else "cached_switch_cache_hit_not_observed",
            target_code=target_code,
            cache_verified=True,
            commit_verified=bool(committed),
            provider_cache_hits_before=before_hits,
            provider_cache_hits_after=after_hits,
            provider_cache_hits_delta=hit_delta,
            observed_elapsed_ms=round(elapsed_ms, 3),
        )
    identity = getattr(controller, "current_identity", None)
    return {
        "status": "ok",
        "measurement": "real_cached_stock_switch_commit",
        "commit_criterion": "load_controller_frame_owner_after_echarts_rendered_ack",
        "cache_source": _provider_cache_source(provider),
        "provider_mode": _provider_mode(provider),
        "cache_verified": True,
        "commit_verified": True,
        "target_code": target_code,
        "generation": int(getattr(identity, "generation", 0) or 0),
        "provider_cache_hits_before": before_hits,
        "provider_cache_hits_after": after_hits,
        "provider_cache_hits_delta": hit_delta,
        "elapsed_ms": round(elapsed_ms, 3),
    }


def _measure_cached_switch(app, chart, args) -> dict[str, Any]:
    """Measure one real navigation request through the committed snapshot ack."""

    target = _cached_switch_target(chart)
    if target is None:
        return _cached_switch_failure("cached_switch_navigation_unavailable")
    target_idx, target_code = target
    provider, cache_verified, before_hits = _cached_switch_cache_evidence(chart, target_code)
    if not cache_verified or before_hits is None:
        return _cached_switch_failure(
            "cached_switch_cache_evidence_unavailable",
            target_code=target_code,
            cache_verified=cache_verified,
            provider_cache_hits_before=before_hits,
        )
    controller, previous_generation, switch = _cached_switch_runtime(chart)
    if previous_generation is None or not callable(switch):
        return _cached_switch_failure(
            "cached_switch_controller_unavailable",
            target_code=target_code,
            cache_verified=True,
        )
    started_at, failure = _dispatch_cached_switch(switch, target_idx, target_code)
    if failure is not None:
        return failure
    committed, elapsed_ms, failure = _await_cached_switch(
        app,
        chart,
        args,
        target_code,
        previous_generation,
        started_at,
    )
    if failure is not None:
        return failure
    return _cached_switch_measurement(
        controller,
        provider,
        target_code,
        before_hits,
        committed,
        elapsed_ms,
    )


def _disconnect_chart_load_signal(load_signal, load_callback) -> None:
    if load_signal is None or load_callback is None:
        return
    try:
        load_signal.disconnect(load_callback)
    except (AttributeError, RuntimeError, TypeError):
        return


def _close_and_record_cycle(
    app,
    window,
    kline_manager,
    cycle: dict,
    chart,
    baseline: tuple[int, bool],
    *,
    close_timeout_ms: int,
) -> bool:
    closed_count = _close_kline_charts(app)

    def _close_settled() -> bool:
        return bool(
            chart is not None
            and _kline_chart_closed(chart)
            and int(getattr(kline_manager, "active_chart_view_count", 0) or 0) == 0
            and int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0)
            == baseline[0]
            and bool(getattr(kline_manager, "managed_webengine_keeper_ready", False))
            is baseline[1]
        )

    closed = bool(
        _close_settled()
        or _wait_until(
            app,
            _close_settled,
            timeout_ms=max(1, int(close_timeout_ms)),
            step_ms=25,
        )
    )
    if closed:
        closed_count = max(1, int(closed_count or 0))
    cycle["closed"] = closed
    cycle["closed_count"] = closed_count
    cycle["samples"].append(_sample(f"{_cycle_label(cycle)}:after_close", window))
    cycle["baseline_managed_webengine_keeper_count"] = baseline[0]
    cycle["final_managed_webengine_keeper_count"] = int(
        getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0
    )
    cycle["baseline_managed_webengine_keeper_ready"] = baseline[1]
    cycle["final_managed_webengine_keeper_ready"] = bool(
        getattr(kline_manager, "managed_webengine_keeper_ready", False)
    )
    cycle["active_chart_view_count_after_close"] = int(
        getattr(kline_manager, "active_chart_view_count", 0) or 0
    )
    return closed


def _cycle_keeper_stable(cycle: dict) -> bool:
    return bool(
        cycle.get("baseline_managed_webengine_keeper_count") == 1
        and cycle.get("final_managed_webengine_keeper_count") == 1
        and cycle.get("baseline_managed_webengine_keeper_ready") is True
        and cycle.get("final_managed_webengine_keeper_ready") is True
    )


def _cycle_stage_contract_complete(cycle: dict) -> bool:
    diagnostics = cycle.get("stage_diagnostics")
    return bool(
        cycle.get("first_interaction_triggered") is True
        and cycle.get("first_interaction_ready") is True
        and isinstance(diagnostics, dict)
        and diagnostics.get("required_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("completed_stages") == list(KLINE_OPEN_STAGE_ORDER)
        and diagnostics.get("pending_stages") == []
        and diagnostics.get("complete") is True
    )


def _finalize_cycle(cycle: dict, observation: dict, closed: bool, load_events: list[bool]) -> dict:
    events_before_close = list(cycle["load_status"].get("events_before_close") or [])
    cycle["load_status"]["events"] = list(load_events)
    cycle["summary"] = evaluate_lifecycle(
        cycle["samples"],
        browser_ready=observation["browser_ready"],
        chart_ready=observation["chart_ready"],
        blocked=not observation["browser_ready"],
        closed=closed,
        load_events=events_before_close,
    )
    keeper_stable = _cycle_keeper_stable(cycle)
    active_views_released = cycle.get("active_chart_view_count_after_close") == 0
    cycle["summary"]["managed_keeper_stable"] = keeper_stable
    cycle["summary"]["active_chart_views_released"] = active_views_released
    stalls_valid = _ui_stall_snapshot_valid(cycle.get("ui_stalls"))
    cycle["summary"]["ui_stall_diagnostics_available"] = stalls_valid
    cached_switch = cycle.get("cached_switch")
    cached_switch_ok = not isinstance(cached_switch, dict) or cached_switch.get("status") == "ok"
    cycle["summary"]["cached_switch_ok"] = cached_switch_ok
    stage_contract_complete = _cycle_stage_contract_complete(cycle)
    cycle["summary"]["stage_contract_complete"] = stage_contract_complete
    cold_warmup_page_reused = bool(
        cycle.get("measurement_role") != "cold_warmup"
        or cycle.get("prewarmed_page_reused") is True
    )
    if (
        not keeper_stable
        or not active_views_released
        or not stalls_valid
        or not cached_switch_ok
        or not stage_contract_complete
        or not cold_warmup_page_reused
    ):
        cycle["summary"]["status"] = "fail"
    cycle["status"] = cycle["summary"]["status"]
    return cycle


def _run_one_cycle(
    app: QApplication,
    window: MainWindowQT,
    args: argparse.Namespace,
    cycle_index: int,
    *,
    measure_cached_switch: bool = True,
    measurement_role: str = "measured",
    prewarmed_page=None,
) -> dict:
    from ui.components.kline_window_manager import kline_manager

    role = str(measurement_role or "measured").strip() or "measured"
    measured_index = cycle_index + 1 if role == "measured" else 0
    label = f"cycle_{measured_index}" if role == "measured" else role
    cycle = {
        "cycle_index": measured_index,
        "label": label,
        "measurement_role": role,
        "samples": [],
        "load_status": {},
    }
    cycle["samples"].append(_sample(f"{label}:before_open", window))
    baseline = (
        int(getattr(kline_manager, "managed_webengine_keeper_count", 0) or 0),
        bool(getattr(kline_manager, "managed_webengine_keeper_ready", False)),
    )
    load_events: list[bool] = []
    chart = None
    closed = False
    stall_scope = _begin_ui_stall_scope(app)
    observation = {"browser_ready": False, "chart_ready": False, "load_signal": None, "load_callback": None}
    try:
        chart = _open_cycle_chart(app, kline_manager, window, args, cycle)
        observation = _observe_cycle_chart(
            app,
            window,
            args,
            cycle,
            chart,
            load_events,
            stall_scope=stall_scope,
            prewarmed_page=prewarmed_page,
        )
        stall_scope = None
        if measure_cached_switch:
            cycle["cached_switch"] = (
                _measure_cached_switch(app, chart, args)
                if observation["chart_ready"] and chart is not None
                else _cached_switch_failure("cached_switch_requires_chart_ready")
            )
        cycle["load_status"]["events_before_close"] = list(load_events)
        _disconnect_chart_load_signal(observation["load_signal"], observation["load_callback"])
        closed = _close_and_record_cycle(
            app,
            window,
            kline_manager,
            cycle,
            chart,
            baseline,
            close_timeout_ms=args.close_timeout_ms,
        )
    finally:
        if "ui_stalls" not in cycle:
            cycle["ui_stalls"] = _finish_ui_stall_scope(app, stall_scope)
        if chart is not None and not closed:
            with suppress(RuntimeError):
                chart.close()
        _process_events(app, rounds=5, sleep_ms=20, flush_deferred_deletes=True)
    return _finalize_cycle(cycle, observation, closed, load_events)


def _summarize_cycles(
    cycles: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    *,
    expected_cycles: int | None = None,
    minimum_cycles: int = MINIMUM_LIFECYCLE_CYCLES,
    resource_net_growth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed_cycles = _failed_cycle_indexes(cycles)
    stats = _webengine_sample_stats(samples)
    expected = len(cycles) if expected_cycles is None else max(0, int(expected_cycles))
    required = max(1, int(minimum_cycles))
    cycle_count_complete = len(cycles) == expected
    minimum_cycle_gate = expected >= required and len(cycles) >= required
    resource_growth = resource_net_growth or _resource_net_growth(samples)
    failed = bool(
        failed_cycles
        or not stats["diagnostics_available"]
        or not cycle_count_complete
        or not minimum_cycle_gate
        or resource_growth["status"] != "ok"
    )
    return {
        "status": "fail" if failed else "ok",
        "cycles": len(cycles),
        "expected_cycles": expected,
        "minimum_cycles": required,
        "minimum_cycle_gate": minimum_cycle_gate,
        "cycle_count_complete": cycle_count_complete,
        "ok_cycles": len(cycles) - len(failed_cycles),
        "failed_cycles": failed_cycles,
        "max_webengine_child_count": stats["maximum"],
        "final_webengine_child_count": stats["final"],
        "webengine_diagnostics_available": stats["diagnostics_available"],
        "webengine_child_reclaimed": _webengine_child_reclaimed(stats),
        "managed_webengine_keeper_count_during_cycles": _managed_keeper_count(cycles),
        "managed_webengine_keeper_ready_during_cycles": _managed_keepers_ready(cycles),
        "resource_net_growth": resource_growth,
    }


def _numeric_resource_delta(
    samples: list[dict[str, Any]],
    field: str,
    *,
    budget: float,
) -> dict[str, Any]:
    if not samples:
        return {"available": False, "baseline": None, "final": None, "delta": None, "budget": budget}
    baseline = samples[0].get(field)
    final = samples[-1].get(field)
    if isinstance(baseline, bool) or isinstance(final, bool):
        baseline = final = None
    try:
        baseline_value = float(baseline)
        final_value = float(final)
    except (TypeError, ValueError):
        return {"available": False, "baseline": None, "final": None, "delta": None, "budget": budget}
    if (
        not math.isfinite(baseline_value)
        or not math.isfinite(final_value)
        or baseline_value < 0
        or final_value < 0
    ):
        return {"available": False, "baseline": None, "final": None, "delta": None, "budget": budget}
    delta = final_value - baseline_value
    return {
        "available": True,
        "baseline": baseline_value,
        "final": final_value,
        "delta": delta,
        "budget": budget,
        "status": "ok" if delta <= budget else "fail",
    }


def _resource_net_growth(samples: list[dict[str, Any]]) -> dict[str, Any]:
    resources = {
        field: _numeric_resource_delta(samples, field, budget=0.0)
        for field in _ZERO_GROWTH_RESOURCE_FIELDS
    }
    resources["rss_mb"] = _numeric_resource_delta(
        samples,
        "rss_mb",
        budget=MAX_RSS_NET_GROWTH_MB,
    )
    diagnostics_available = all(entry.get("available") is True for entry in resources.values())
    failed = [entry for entry in resources.values() if entry.get("status") != "ok"]
    return {
        "status": "ok" if diagnostics_available and not failed else "fail",
        "diagnostics_available": diagnostics_available,
        **resources,
    }


def _samples_with_suffix(cycle, suffix: str) -> list[dict[str, Any]]:
    samples = cycle.get("samples") if isinstance(cycle, dict) else None
    return [
        sample
        for sample in (samples if isinstance(samples, list) else [])
        if isinstance(sample, dict) and str(sample.get("label") or "").endswith(suffix)
    ]


def _one_cycle_sample(cycle, suffix: str) -> dict[str, Any] | None:
    matches = _samples_with_suffix(cycle, suffix)
    return matches[0] if len(matches) == 1 else None


def _rss_delta(baseline, final) -> float | None:
    if not isinstance(baseline, dict) or not isinstance(final, dict):
        return None
    baseline_value = _nonnegative_number(baseline.get("rss_mb"))
    final_value = _nonnegative_number(final.get("rss_mb"))
    if baseline_value is None or final_value is None:
        return None
    return round(final_value - baseline_value, 3)


def _resource_growth_samples(
    warmup_cycle,
    measured_cycles: list[dict[str, Any]],
    cold_warmup_cycle,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    cold_cycle = cold_warmup_cycle or warmup_cycle
    cold_baseline = _one_cycle_sample(cold_cycle, ":before_open")
    warm_baseline = _one_cycle_sample(warmup_cycle, ":after_close")
    final_cycle = measured_cycles[-1] if measured_cycles else None
    measured_final = _one_cycle_sample(final_cycle, ":after_close")
    return cold_baseline, warm_baseline, measured_final


def _resource_diagnostics_available(
    cold_baseline,
    warm_baseline,
    measured_final,
    cold_retained,
    steady_growth,
    resource_net_growth: dict[str, Any],
) -> bool:
    return all(
        (
            cold_baseline is not None,
            warm_baseline is not None,
            measured_final is not None,
            cold_retained is not None,
            steady_growth is not None,
            resource_net_growth.get("diagnostics_available") is True,
        )
    )


def _resource_evidence_status(diagnostics_available: bool, resource_net_growth: dict[str, Any]) -> str:
    if not diagnostics_available:
        return "fail"
    if resource_net_growth.get("status") == "ok":
        return "ok"
    return "fail"


def _sample_label(sample) -> str | None:
    if sample is None:
        return None
    return sample.get("label")


def _resource_growth_evidence(
    warmup_cycle,
    measured_cycles: list[dict[str, Any]],
    *,
    cold_warmup_cycle=None,
) -> dict[str, Any]:
    cold_baseline, warm_baseline, measured_final = _resource_growth_samples(
        warmup_cycle,
        measured_cycles,
        cold_warmup_cycle,
    )
    exact_samples = all((warm_baseline is not None, measured_final is not None))
    growth_samples = [warm_baseline, measured_final] if exact_samples else []
    resource_net_growth = _resource_net_growth(growth_samples)
    cold_retained = _rss_delta(cold_baseline, warm_baseline)
    steady_growth = _rss_delta(warm_baseline, measured_final)
    diagnostics_available = _resource_diagnostics_available(
        cold_baseline,
        warm_baseline,
        measured_final,
        cold_retained,
        steady_growth,
        resource_net_growth,
    )
    return {
        "status": _resource_evidence_status(diagnostics_available, resource_net_growth),
        "basis": "warmup_after_close_to_last_measured_after_close",
        "cold_first_use_retained_mb": cold_retained,
        "steady_state_lifecycle_net_growth_mb": steady_growth,
        "cold_baseline_label": _sample_label(cold_baseline),
        "warm_baseline_label": _sample_label(warm_baseline),
        "measured_final_label": _sample_label(measured_final),
        "resource_net_growth": resource_net_growth,
    }


def _verified_cached_switch_elapsed(measurement) -> float | None:
    if not isinstance(measurement, dict):
        return None
    if measurement.get("status") != "ok":
        return None
    if measurement.get("measurement") != "real_cached_stock_switch_commit":
        return None
    if measurement.get("cache_verified") is not True:
        return None
    if measurement.get("commit_verified") is not True:
        return None
    hit_delta = _nonnegative_integer(measurement.get("provider_cache_hits_delta"))
    if hit_delta in (None, 0):
        return None
    return _nonnegative_number(measurement.get("elapsed_ms"))


def _cached_switch_error(measurement) -> str:
    if isinstance(measurement, dict):
        return str(measurement.get("error") or "cached_switch_sample_invalid")
    return "cached_switch_measurement_missing"


def _collect_cached_switch_samples(
    cycles: list[dict[str, Any]],
) -> tuple[list[float], list[int], list[dict[str, Any]], int]:
    samples: list[float] = []
    failed_cycles: list[int] = []
    failures: list[dict[str, Any]] = []
    attempts = 0
    for position, cycle in enumerate(cycles, start=1):
        attempts += 1
        cycle_index = int(cycle.get("cycle_index") or position)
        measurement = cycle.get("cached_switch")
        elapsed = _verified_cached_switch_elapsed(measurement)
        if elapsed is None:
            failed_cycles.append(cycle_index)
            failures.append(
                {
                    "cycle_index": cycle_index,
                    "error": _cached_switch_error(measurement),
                }
            )
        else:
            samples.append(round(elapsed, 3))
    return samples, failed_cycles, failures, attempts


def _verified_cached_switch_measurements(
    cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        measurement
        for cycle in cycles
        if isinstance(measurement := cycle.get("cached_switch"), dict)
        and _verified_cached_switch_elapsed(measurement) is not None
    ]


def _cached_switch_dimension(
    measurements: list[dict[str, Any]],
    field: str,
    default: str,
) -> str:
    values = sorted({str(item.get(field) or default) for item in measurements})
    return values[0] if len(values) == 1 else "mixed_or_unavailable"


def _cached_switch_summary(
    cycles: list[dict[str, Any]],
    *,
    minimum_samples: int = MINIMUM_CACHED_SWITCH_SAMPLES,
) -> dict[str, Any]:
    samples, failed_cycles, failures, attempts = _collect_cached_switch_samples(cycles)
    required = max(MINIMUM_CACHED_SWITCH_SAMPLES, int(minimum_samples))
    complete = attempts == len(cycles) and not failed_cycles
    verified_measurements = _verified_cached_switch_measurements(cycles)
    cache_source = _cached_switch_dimension(
        verified_measurements,
        "cache_source",
        "offline_memory_cache_provider",
    )
    provider_mode = _cached_switch_dimension(
        verified_measurements,
        "provider_mode",
        SYNTHETIC_PROVIDER_MODE,
    )
    return {
        "status": "ok" if complete and len(samples) >= required else "fail",
        "measurement": "real_cached_stock_switch_commit",
        "cache_source": cache_source,
        "provider_mode": provider_mode,
        "commit_criterion": "load_controller_frame_owner_after_echarts_rendered_ack",
        "minimum_samples": required,
        "attempts": attempts,
        "samples_ms": samples,
        "failed_cycles": failed_cycles,
        "failures": failures,
    }


def _failed_cycle_indexes(cycles: list[dict[str, Any]]) -> list[int]:
    return [
        int(cycle.get("cycle_index") or 0)
        for cycle in cycles
        if ((cycle.get("summary") or {}).get("status") != "ok")
    ]


def _managed_keeper_count(cycles: list[dict[str, Any]]) -> int:
    return max(
        [int(cycle.get("final_managed_webengine_keeper_count") or 0) for cycle in cycles],
        default=0,
    )


def _managed_keepers_ready(cycles: list[dict[str, Any]]) -> bool:
    return bool(cycles) and all(
        cycle.get("baseline_managed_webengine_keeper_ready") is True
        and cycle.get("final_managed_webengine_keeper_ready") is True
        for cycle in cycles
    )


def _selected_provider_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "provider_mode", "") or SYNTHETIC_PROVIDER_MODE).strip()
    return mode if mode in SMOKE_PROVIDER_MODES else SYNTHETIC_PROVIDER_MODE


def _new_smoke_report(args: argparse.Namespace, *, qt_platform: str, cycles: int) -> dict[str, Any]:
    provider_mode = _selected_provider_mode(args)
    report = {
        "schema_version": 2,
        "report_type": "kline_webengine_lifecycle_smoke",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": {
            "native_qt": bool(args.native_qt),
            "qt_platform": qt_platform,
            "allow_offscreen": bool(args.allow_offscreen),
            "code": str(args.code or ""),
            "name": str(args.name or ""),
            "switch_code": str(getattr(args, "switch_code", "") or ""),
            "switch_name": str(getattr(args, "switch_name", "") or ""),
            "open_timeout_ms": int(args.open_timeout_ms),
            "close_timeout_ms": int(args.close_timeout_ms),
            "switch_timeout_ms": int(getattr(args, "switch_timeout_ms", 3000)),
            "provider_mode": provider_mode,
            "requires_real_local_a_share": provider_mode == PRODUCTION_LOCAL_PROVIDER_MODE,
            "cycles": cycles,
            "minimum_cycles": max(
                MINIMUM_LIFECYCLE_CYCLES,
                int(getattr(args, "minimum_cycles", MINIMUM_LIFECYCLE_CYCLES)),
            ),
            "warmup_cycles": 2,
            "warmup_included_in_measured_cycles": False,
            "resource_growth_basis": "warmup_after_close_to_last_measured_after_close",
            "open_success_criterion": "chart_ready",
        },
        "samples": [],
        "cycles": [],
        "load_status": {},
    }
    report["native_lifecycle"] = _new_native_lifecycle_report(report["mode"])
    return report


def _mark_smoke_skipped(report: dict, cycles: int) -> dict:
    report["status"] = "skipped"
    report["skip_reason"] = "QtWebEngine lifecycle smoke requires native Qt or --allow-offscreen."
    provider_option = (
        " --provider-mode production-local"
        if (report.get("mode") or {}).get("provider_mode") == PRODUCTION_LOCAL_PROVIDER_MODE
        else ""
    )
    report["manual_command"] = (
        ".\\.venv\\Scripts\\python.exe scripts\\kline_webengine_lifecycle_smoke.py "
        f"--native-qt{provider_option} --cycles {cycles} "
        "--output tmp\\kline_webengine_lifecycle_smoke.json"
    )
    return report


def _new_smoke_data_provider(args: argparse.Namespace):
    if _selected_provider_mode(args) == PRODUCTION_LOCAL_PROVIDER_MODE:
        return _ProductionLocalSmokeDataProvider()
    return _OfflineSmokeDataProvider()


def _new_smoke_window(args: argparse.Namespace) -> MainWindowQT:
    window = MainWindowQT(
        startup_enabled=False,
        background_prewarm=False,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
    )
    window.data_provider = _new_smoke_data_provider(args)
    return window


def _smoke_provider_evidence(provider) -> dict[str, Any]:
    reader = getattr(provider, "evidence", None)
    if not callable(reader):
        return {
            "status": "fail",
            "mode": _provider_mode(provider),
            "error": "provider_evidence_unavailable",
        }
    try:
        evidence = reader()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "status": "fail",
            "mode": _provider_mode(provider),
            "error": "provider_evidence_failed",
            "exception": str(exc),
        }
    return dict(evidence) if isinstance(evidence, dict) else {
        "status": "fail",
        "mode": _provider_mode(provider),
        "error": "provider_evidence_invalid",
    }


def _run_native_lifecycle_scenario(name: str, callback) -> dict[str, Any]:
    try:
        result = callback()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "status": "fail",
            "scenario": name,
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
    if not isinstance(result, dict):
        return {"status": "fail", "scenario": name, "error": "invalid_result"}
    return result


def _native_network_guard_evidence(before: dict, after: dict) -> dict[str, Any]:
    before_count = before.get("network_request_count")
    after_count = after.get("network_request_count")
    guard_active = before.get("network_guard_active") is True and after.get("network_guard_active") is True
    access_disabled = (
        before.get("network_access_enabled") is False
        and after.get("network_access_enabled") is False
    )
    no_requests = before_count == 0 and after_count == 0
    return {
        "status": "ok" if guard_active and access_disabled and no_requests else "fail",
        "network_guard_active": guard_active,
        "network_access_enabled": not access_disabled,
        "network_request_count_before": before_count,
        "network_request_count_after": after_count,
        "no_network_requests": no_requests,
    }


def _run_native_lifecycle_acceptance(app, window, args) -> dict[str, Any]:
    provider = getattr(window, "data_provider", None)
    before = _smoke_provider_evidence(provider)
    callbacks = {
        "same_stock_multi_window": lambda: _run_same_stock_multi_window(app, window, args),
        "visibility_pause_resume": lambda: _run_visibility_pause_resume(app, window, args),
        "render_process_recovery": lambda: _run_render_process_recovery(app, window, args),
    }
    scenarios = {
        name: _run_native_lifecycle_scenario(name, callback)
        for name, callback in callbacks.items()
    }
    after = _smoke_provider_evidence(provider)
    network_guard = _native_network_guard_evidence(before, after)
    succeeded = bool(
        network_guard.get("status") == "ok"
        and all(scenarios[field].get("status") == "ok" for field in _NATIVE_LIFECYCLE_SCENARIOS)
    )
    return {
        "required": True,
        "status": "ok" if succeeded else "fail",
        "provider_mode": _provider_mode(provider),
        "network_guard": network_guard,
        **scenarios,
    }


def _run_smoke_cycles(app, window, args, report: dict, cycles: int) -> None:
    report["setup"] = _prepare_smoke_runtime(
        app,
        window,
        timeout_ms=max(SMOKE_SETUP_TIMEOUT_MS, int(args.open_timeout_ms)),
    )
    if report["setup"]["status"] != "ok":
        return
    from ui.components.kline_window_manager import kline_manager

    prewarmed_page = _capture_prewarmed_page(kline_manager)
    report["cold_warmup_cycle"] = _run_one_cycle(
        app,
        window,
        args,
        -2,
        measure_cached_switch=True,
        measurement_role="cold_warmup",
        prewarmed_page=prewarmed_page,
    )
    report["warmup_cycle"] = _run_one_cycle(
        app,
        window,
        args,
        -1,
        measure_cached_switch=True,
        measurement_role="warmup",
    )
    for cycle_index in range(cycles):
        cycle = _run_one_cycle(
            app,
            window,
            args,
            cycle_index,
            measure_cached_switch=True,
            measurement_role="measured",
        )
        report["cycles"].append(cycle)
        report["samples"].extend(cycle.get("samples") or [])
    if _native_lifecycle_required(report.get("mode") or {}):
        report["native_lifecycle"] = _run_native_lifecycle_acceptance(
            app, window, args
        )


def _shutdown_smoke_window(app, window, report: dict) -> None:
    try:
        _close_kline_charts(app)
        window.close()
        window.deleteLater()
        _process_events(app, rounds=10, sleep_ms=20, flush_deferred_deletes=True)
    except RuntimeError:
        pass
    report["shutdown"] = {
        "post_close": _sample("shutdown:post_close", None),
        "included_in_lifecycle_resource_growth": False,
    }


def _smoke_load_events(cycles: list[dict[str, Any]]) -> list[Any]:
    return [event for cycle in cycles for event in ((cycle.get("load_status") or {}).get("events") or [])]


def _warmup_statuses(report: dict) -> tuple[Any, Any]:
    warmup_status = ((report.get("warmup_cycle") or {}).get("summary") or {}).get("status")
    cold_warmup_cycle = report.get("cold_warmup_cycle")
    cold_warmup_status = ((cold_warmup_cycle or {}).get("summary") or {}).get("status")
    if cold_warmup_cycle is None:
        cold_warmup_status = warmup_status
    elif not isinstance(cold_warmup_cycle, dict) or cold_warmup_cycle.get("prewarmed_page_reused") is not True:
        cold_warmup_status = "fail"
    return warmup_status, cold_warmup_status


def _shutdown_webengine_children(report: dict) -> int | None:
    shutdown = report.get("shutdown")
    if not isinstance(shutdown, dict):
        shutdown = {}
    return _webengine_count(shutdown.get("post_close"))


def _update_smoke_summary_evidence(
    report: dict,
    resource_evidence: dict[str, Any],
    cached_switch: dict[str, Any],
) -> tuple[Any, Any, int | None]:
    summary = report["summary"]
    steady_state_final_children = summary.get("final_webengine_child_count")
    shutdown_children = _shutdown_webengine_children(report)
    warmup_status, cold_warmup_status = _warmup_statuses(report)
    summary.update(
        {
            "warmup_status": warmup_status,
            "cold_warmup_status": cold_warmup_status,
            "warmup_included_in_measured_cycles": False,
            "resource_growth_basis": resource_evidence["basis"],
            "cold_first_use_retained_mb": resource_evidence["cold_first_use_retained_mb"],
            "steady_state_lifecycle_net_growth_mb": resource_evidence["steady_state_lifecycle_net_growth_mb"],
            "steady_state_final_webengine_child_count": steady_state_final_children,
            "final_webengine_child_count": shutdown_children,
            "shutdown_webengine_diagnostics_available": shutdown_children is not None,
            "cached_switch_status": cached_switch["status"],
        }
    )
    return warmup_status, cold_warmup_status, shutdown_children


def _smoke_evidence_failed(
    warmup_status,
    cold_warmup_status,
    resource_evidence: dict[str, Any],
    cached_switch: dict[str, Any],
    shutdown_children: int | None,
) -> bool:
    if warmup_status != "ok":
        return True
    if cold_warmup_status != "ok":
        return True
    if resource_evidence["status"] != "ok":
        return True
    if cached_switch["status"] != "ok":
        return True
    if shutdown_children is None:
        return True
    return shutdown_children != 0


def _report_section(report: dict, field: str) -> dict:
    value = report.get(field)
    return value if isinstance(value, dict) else {}


def _production_local_identity_evidence_valid(evidence: dict) -> bool:
    return bool(
        evidence.get("status") == "ok"
        and evidence.get("mode") == PRODUCTION_LOCAL_PROVIDER_MODE
        and evidence.get("production_provider_contract") is True
        and evidence.get("synthetic") is False
    )


def _production_local_policy_evidence_valid(evidence: dict) -> bool:
    return bool(
        evidence.get("local_only") is True
        and evidence.get("read_only") is True
        and evidence.get("network_access_enabled") is False
        and evidence.get("network_request_count") == 0
        and evidence.get("network_guard_active") is True
        and evidence.get("frozen_after_prime") is True
    )


def _production_local_code_evidence_valid(codes: dict) -> bool:
    return len(codes) >= 2 and all(item.get("status") == "ok" for item in codes.values())


def _production_local_provider_evidence_failed(report: dict) -> bool:
    mode = _report_section(report, "mode")
    if mode.get("provider_mode") != PRODUCTION_LOCAL_PROVIDER_MODE:
        return False
    evidence = _report_section(report, "data_provider")
    codes = _report_section(evidence, "codes")
    return not bool(
        _production_local_identity_evidence_valid(evidence)
        and _production_local_policy_evidence_valid(evidence)
        and _production_local_code_evidence_valid(codes)
    )


def _native_network_guard_valid(evidence: dict) -> bool:
    checks = (
        evidence.get("status") == "ok",
        evidence.get("network_guard_active") is True,
        evidence.get("network_access_enabled") is False,
        evidence.get("network_request_count_before") == 0,
        evidence.get("network_request_count_after") == 0,
        evidence.get("no_network_requests") is True,
    )
    return all(checks)


def _same_stock_evidence_valid(evidence: dict) -> bool:
    checks = (
        evidence.get("status") == "ok",
        (evidence.get("first_open") or {}).get("chart_ready") is True,
        (evidence.get("second_open") or {}).get("chart_ready") is True,
        *(evidence.get(field) is True for field in _SAME_STOCK_REQUIRED_TRUE_FIELDS),
    )
    return all(checks)


def _visibility_transition_evidence_valid(evidence: dict) -> bool:
    fields = ("pause_observed", "runtime_reactivated", "chart_ready_after_resume")
    return all(
        (evidence.get("status") == "ok", *(evidence.get(field) is True for field in fields))
    )


def _visibility_evidence_valid(evidence: dict) -> bool:
    checks = (
        evidence.get("status") == "ok",
        (evidence.get("open") or {}).get("chart_ready") is True,
        _visibility_transition_evidence_valid(_report_section(evidence, "hidden")),
        _visibility_transition_evidence_valid(_report_section(evidence, "minimized")),
        *(evidence.get(field) is True for field in _VISIBILITY_REQUIRED_TRUE_FIELDS),
    )
    return all(checks)


def _recovery_evidence_valid(evidence: dict) -> bool:
    return evidence.get("status") == "ok" and _recovery_report_ok(evidence)


def _native_lifecycle_evidence_failed(report: dict) -> bool:
    if not _native_lifecycle_required(_report_section(report, "mode")):
        return False
    lifecycle = _report_section(report, "native_lifecycle")
    if lifecycle.get("required") is not True or lifecycle.get("status") != "ok":
        return True
    checks = (
        _native_network_guard_valid(_report_section(lifecycle, "network_guard")),
        _same_stock_evidence_valid(_report_section(lifecycle, "same_stock_multi_window")),
        _visibility_evidence_valid(_report_section(lifecycle, "visibility_pause_resume")),
        _recovery_evidence_valid(_report_section(lifecycle, "render_process_recovery")),
    )
    return not all(checks)


def _kline_budget_failures(report: dict) -> list[dict[str, Any]]:
    try:
        from scripts.perf_budget_check import check_kline_lifecycle_budget

        return check_kline_lifecycle_budget(report)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
        return [
            {
                "check": "kline_lifecycle.budget.diagnostics",
                "detail": "exact K-line lifecycle budget could not be evaluated",
                "error": str(exc),
            }
        ]


def _apply_kline_budget_result(report: dict, budget_failures: list[dict[str, Any]]) -> None:
    report["budget"] = {
        "status": "fail" if budget_failures else "ok",
        "failures": budget_failures,
    }
    if budget_failures:
        report["status"] = "fail"


def _finalize_smoke_report(report: dict, cycles: int, minimum_cycles: int) -> dict:
    report["load_status"]["events"] = _smoke_load_events(report["cycles"])
    resource_evidence = _resource_growth_evidence(
        report.get("warmup_cycle"),
        report["cycles"],
        cold_warmup_cycle=report.get("cold_warmup_cycle"),
    )
    cached_switch = _cached_switch_summary(report["cycles"])
    report["cached_switch"] = cached_switch
    report["resource_growth"] = resource_evidence
    report["summary"] = _summarize_cycles(
        report["cycles"],
        report["samples"],
        expected_cycles=cycles,
        minimum_cycles=minimum_cycles,
        resource_net_growth=resource_evidence["resource_net_growth"],
    )
    warmup_status, cold_warmup_status, shutdown_children = _update_smoke_summary_evidence(
        report,
        resource_evidence,
        cached_switch,
    )
    if _smoke_evidence_failed(
        warmup_status,
        cold_warmup_status,
        resource_evidence,
        cached_switch,
        shutdown_children,
    ):
        report["summary"]["status"] = "fail"
    if _production_local_provider_evidence_failed(report):
        report["summary"]["status"] = "fail"
    if _native_lifecycle_evidence_failed(report):
        report["summary"]["status"] = "fail"
    report["status"] = report["summary"]["status"]
    budget_failures = _kline_budget_failures(report)
    _apply_kline_budget_result(report, budget_failures)
    return report


def run_smoke(args: argparse.Namespace) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    qt_platform = os.environ.get("QT_QPA_PLATFORM", "")
    cycles = max(MINIMUM_LIFECYCLE_CYCLES, int(args.cycles or MINIMUM_LIFECYCLE_CYCLES))
    minimum_cycles = max(
        MINIMUM_LIFECYCLE_CYCLES,
        int(getattr(args, "minimum_cycles", MINIMUM_LIFECYCLE_CYCLES)),
    )
    report = _new_smoke_report(args, qt_platform=qt_platform, cycles=cycles)
    if qt_platform.lower() == "offscreen" and not args.allow_offscreen:
        return _mark_smoke_skipped(report, cycles)

    try:
        window = _new_smoke_window(args)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report["status"] = "fail"
        report["summary"] = {"status": "fail", "error": "provider_initialization_failed"}
        report["data_provider"] = {
            "status": "fail",
            "mode": _selected_provider_mode(args),
            "error": "provider_initialization_failed",
            "exception": str(exc),
        }
        return report
    try:
        _run_smoke_cycles(app, window, args, report, cycles)
    finally:
        report["data_provider"] = _smoke_provider_evidence(
            getattr(window, "data_provider", None)
        )
        _shutdown_smoke_window(app, window, report)
    return _finalize_smoke_report(report, cycles, minimum_cycles)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native Qt K-line WebEngine lifecycle smoke probe.")
    parser.add_argument("--native-qt", action="store_true")
    parser.add_argument("--allow-offscreen", action="store_true")
    parser.add_argument("--code", default="000001")
    parser.add_argument("--name", default="\u5e73\u5b89\u94f6\u884c")
    parser.add_argument("--switch-code", default="000002")
    parser.add_argument("--switch-name", default="\u4e07\u79d1A")
    parser.add_argument(
        "--provider-mode",
        choices=SMOKE_PROVIDER_MODES,
        default=SYNTHETIC_PROVIDER_MODE,
        help="Use deterministic synthetic bars or targeted read-only production-local A-share bars.",
    )
    parser.add_argument("--open-timeout-ms", type=int, default=8000)
    parser.add_argument("--close-timeout-ms", type=int, default=8000)
    parser.add_argument("--switch-timeout-ms", type=int, default=3000)
    parser.add_argument("--cycles", type=int, default=MINIMUM_LIFECYCLE_CYCLES)
    parser.add_argument("--minimum-cycles", type=int, default=MINIMUM_LIFECYCLE_CYCLES)
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
