# -*- coding: utf-8 -*-
"""主窗口冷启动与后台预热编排。"""

from __future__ import annotations

import datetime
import json
import time
from collections.abc import Callable
from typing import Any, Protocol, cast

from PyQt6.QtCore import QTimer

from core.background_job_runner import background_job_runner
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from core.process_watchdog import log_process_snapshot
from core.runtime_paths import CACHE_DIR, PROJECT_ROOT
from domains.quotes.tdx_name_map import normalize_code_name_targets as _normalize_a_share_codes
from domains.runtime import domain_events as event_bus
from infra.features import service_toggle_registry
from infra.tasks import (
    STARTUP_ASIAN_DATA_SYNC,
    STARTUP_DEFERRED_LOAD,
    STARTUP_SMART,
    CancellationToken,
    ProcessExecutionError,
    ProcessTimeoutError,
    run_python_module,
    run_python_module_cancellable,
    task_registry,
)

log = get_logger(__name__)
ASIAN_DATA_SYNC_TIME_BUDGET_SEC = 20
ASIAN_DATA_SYNC_TIMEOUT_SEC = 30
ASIAN_DATA_SYNC_PROCESS_TIMEOUT_SEC = ASIAN_DATA_SYNC_TIMEOUT_SEC - 1
ASIAN_DATA_SYNC_START_DELAY_MS = 8500
ASIAN_DATA_SYNC_RUNTIME_DEFER_SEC = ASIAN_DATA_SYNC_TIMEOUT_SEC + 15
ASIAN_DATA_SYNC_TIMEOUT_RUNTIME_BACKOFF_SEC = 10 * 60
ASIAN_DATA_SYNC_SHELL_NAV_QUIET_SEC = 8.0
ASIAN_DATA_SYNC_BUSY_RETRY_DELAY_MS = 1000
SMART_STARTUP_PRELOAD_RETRY_DELAY_MS = 500
GLOBAL_EARNINGS_CALENDAR_PRELOAD_RETRY_DELAY_MS = 500
DEFERRED_LOAD_TASK_ID = STARTUP_DEFERRED_LOAD.task_id
ASIAN_DATA_SYNC_TASK_ID = STARTUP_ASIAN_DATA_SYNC.task_id
SMART_STARTUP_TASK_ID = STARTUP_SMART.task_id
GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID = task_registry.network(
    "global_earnings_calendar_sync",
    description="Global oligarch earnings calendar silent sync",
).task_id
GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC = 30
GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS = 15 * 60 * 1000
GLOBAL_EARNINGS_CALENDAR_SYNC_ACTIVE_MAX_RETRY_DELAY_MS = 60 * 60 * 1000
GLOBAL_EARNINGS_CALENDAR_SYNC_OFFPEAK_STALE_CACHE_MIN_RETRY_DELAY_MS = 2 * 60 * 60 * 1000
GLOBAL_EARNINGS_CALENDAR_SYNC_OFFPEAK_MAX_RETRY_DELAY_MS = 4 * 60 * 60 * 1000
GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR = 2
GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE = 0
GLOBAL_EARNINGS_CALENDAR_OFFPEAK_START_MINUTE = 18 * 60
GLOBAL_EARNINGS_CALENDAR_OFFPEAK_END_MINUTE = 8 * 60
RefreshResult = dict[str, object]


class StartupHostPort(Protocol):
    """Stable main-window capabilities required by startup orchestration."""

    data_provider: Any
    cache_manager: Any
    engine: Any
    asian_market_service: Any
    tab_watchlist: Any
    lbl_code_count: Any
    lbl_status: Any

    def current_workspace(self) -> Any: ...

    def is_closing(self) -> bool: ...

    def call_in_ui(self, callback: Callable[[], object]) -> None: ...

    def refresh_code_count_label_from_provider(self) -> Any: ...

    def set_titlebar_sync_state(self, *args: Any) -> None: ...

    def update_network_ui(self, online: bool) -> None: ...

    def on_smart_startup_online_done(self) -> None: ...


def _central_scheduler_owns_asian_sync(now: datetime.datetime | None = None) -> bool:
    local_now = now or datetime.datetime.now()
    return (local_now.hour, local_now.minute) >= (16, 30)


def _is_display_a_share_code(raw_code: object) -> bool:
    code = str(raw_code or "").strip()
    return len(code) == 6 and code.isdigit() and code.startswith(("60", "68", "00", "30"))


def _run_startup_asian_sync_subprocess(cancellation_token: CancellationToken):
    return run_python_module_cancellable(
        "vcp.fetchers.asian_kline_fetcher",
        [
            "--strict-sync",
            "--workers",
            "3",
            "--period",
            "1y",
            "--output-dir",
            CACHE_DIR,
            "--time-budget-sec",
            str(ASIAN_DATA_SYNC_TIME_BUDGET_SEC),
        ],
        cancellation_token=cancellation_token,
        timeout=ASIAN_DATA_SYNC_PROCESS_TIMEOUT_SEC,
        check=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        no_window=True,
    )


def _should_defer_startup_asian_sync(host) -> bool:
    if not _background_preload_is_settled(host):
        return True
    workspace = host.workspace
    last_shell_nav_at = getattr(workspace, "_last_shell_nav_load_at", 0.0)
    try:
        shell_nav_age = time.perf_counter() - float(last_shell_nav_at or 0.0)
    except (TypeError, ValueError):
        shell_nav_age = ASIAN_DATA_SYNC_SHELL_NAV_QUIET_SEC
    if last_shell_nav_at and 0.0 <= shell_nav_age < ASIAN_DATA_SYNC_SHELL_NAV_QUIET_SEC:
        return True

    main_window = getattr(host, "_main_window", None)
    if bool(
        getattr(main_window, "_pending_f5_request", False)
        or getattr(main_window, "_f5_precompute_start_pending", False)
    ):
        return True
    controller = getattr(main_window, "_f5_job_controller", None)
    running = getattr(controller, "is_running", False)
    try:
        return bool(running() if callable(running) else running)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _background_preload_is_settled(host) -> bool:
    workspace = host.workspace
    status_reader = getattr(workspace, "background_preload_status", None)
    if not callable(status_reader):
        return True
    try:
        status = status_reader()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    if not isinstance(status, dict):
        return False
    if status.get("enabled") is False:
        return True
    return all(
        (
            status.get("enabled") is True,
            status.get("finished") is True,
            not str(status.get("active_key") or "").strip(),
            not list(status.get("remaining_keys") or []),
            not list(status.get("pending_priority_keys") or []),
            not str(status.get("cancelling_key") or "").strip(),
            status.get("active_step_count") == 0,
        )
    )


def _smart_startup_ready(orchestrator) -> bool:
    if not orchestrator._alive():
        return False
    if _background_preload_is_settled(orchestrator.host):
        return True
    orchestrator._smart_timer.start(SMART_STARTUP_PRELOAD_RETRY_DELAY_MS)
    return False


def _complete_smart_startup_online(orchestrator, provider) -> bool:
    if not orchestrator._alive():
        return False
    if provider is not None:
        provider.set_online_mode(True)
    log.info("[智能启动] 网络可用，已自动切换到联机模式")
    try:
        if not orchestrator._alive():
            return False
        code2name = orchestrator._refresh_startup_code_names()
        orchestrator._safe_call_in_ui(lambda: orchestrator.host.refresh_watchlist_names(code2name))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.error(f"[智能启动] 后台同步代码名称映射失败: {exc}")
    orchestrator._safe_call_in_ui(lambda: orchestrator.host.update_network_ui(True))
    orchestrator._safe_call_in_ui(orchestrator.host.on_smart_startup_online_done)
    return True


def _record_smart_startup_completion(started_at: float, online: bool) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric(
        "smart_startup_network_probe_ms",
        elapsed_ms,
        unit="ms",
        tags={"online": str(bool(online)).lower()},
    )
    log_process_snapshot(
        "startup.smart.end",
        logger=log,
        extra={"elapsed_ms": int(round(elapsed_ms)), "online": bool(online)},
    )
    emit_structured_log(
        "startup.network_probe.completed",
        elapsed_ms=round(elapsed_ms, 3),
        online=bool(online),
    )


def _execute_smart_startup(orchestrator) -> None:
    started_at = time.perf_counter()
    try:
        if not orchestrator._alive():
            return
        log_process_snapshot("startup.smart.begin", logger=log)
        provider = orchestrator.host.data_provider
        online = bool(provider and provider.test_network(timeout=2))
        if not orchestrator._alive():
            return
        if online and not _complete_smart_startup_online(orchestrator, provider):
            return
        if not online:
            log.info("[智能启动] 网络不可用，保持离线模式")
        _record_smart_startup_completion(started_at, online)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log_process_snapshot(
            "startup.smart.end",
            logger=log,
            level="warning",
            extra={"status": "failed"},
        )
        log.error(f"[智能启动] 网络检测异常: {exc}")


def _startup_asian_tab_is_visible(host) -> bool:
    workspace = host.workspace
    current_tab_key = getattr(workspace, "current_tab_key", None)
    if not callable(current_tab_key):
        return False
    try:
        return str(current_tab_key() or "").strip() == "asian_market"
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _startup_asian_cache_is_stale(orchestrator, cancellation_token: CancellationToken) -> bool:
    service = orchestrator.host.asian_market_service
    cache_staleness = getattr(service, "cache_staleness", None)
    if not callable(cache_staleness):
        log.info("[启动] 亚洲市场缓存服务不可用，跳过静默同步")
        return False
    try:
        staleness = dict(cache_staleness() or {})
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning("[启动] 亚洲市场缓存新鲜度检查失败，已跳过本次同步（%s）", exc)
        return False
    cancellation_token.raise_if_cancelled()
    if staleness.get("stale"):
        return True
    log.info("[启动] 亚洲市场 K 线缓存已是最新，跳过静默同步")
    return False


def _record_startup_asian_sync_terminal(started_at: float, status: str, *, level: str = "info") -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric("startup_asian_sync_ms", elapsed_ms, unit="ms", tags={"status": status})
    log_process_snapshot(
        "startup.asian_sync.end",
        logger=log,
        level=level,
        extra={"elapsed_ms": int(round(elapsed_ms)), "status": status},
    )
    emit_structured_log(
        "startup.asian_sync.completed",
        elapsed_ms=round(elapsed_ms, 3),
        status=status,
    )


def _execute_startup_asian_sync(orchestrator, cancellation_token: CancellationToken) -> None:
    started_at = time.perf_counter()
    if not orchestrator._alive():
        return
    if _central_scheduler_owns_asian_sync():
        log.info("[启动] 16:30 后的亚洲 K 线同步由自动刷新调度器统一处理")
        return
    cancellation_token.raise_if_cancelled()
    if not _startup_asian_cache_is_stale(orchestrator, cancellation_token):
        return

    log_process_snapshot("startup.asian_sync.begin", logger=log)
    orchestrator._defer_asian_market_auto_refresh(ASIAN_DATA_SYNC_RUNTIME_DEFER_SEC, "startup_asian_sync")
    try:
        _run_startup_asian_sync_subprocess(cancellation_token)
        cancellation_token.raise_if_cancelled()
    except ProcessTimeoutError:
        orchestrator._defer_asian_market_auto_refresh(
            ASIAN_DATA_SYNC_TIMEOUT_RUNTIME_BACKOFF_SEC,
            "startup_asian_sync_timeout",
        )
        _record_startup_asian_sync_terminal(started_at, "timeout", level="warning")
        log.warning("[启动] 亚洲市场后台静默同步超时(%ss)，已终止并回收子进程", ASIAN_DATA_SYNC_TIMEOUT_SEC)
    except (OSError, ProcessExecutionError, ValueError) as exc:
        _record_startup_asian_sync_terminal(started_at, "failed", level="warning")
        summary, raw_detail = _format_subprocess_failure(exc)
        log.warning("[启动] 亚洲市场静默同步失败，已保留现有缓存（%s）", summary)
        if raw_detail:
            log.debug("[启动] 亚洲市场静默同步原始输出: %s", raw_detail)
    else:
        _record_startup_asian_sync_terminal(started_at, "success")
        orchestrator._safe_call_in_ui(lambda: event_bus.sig_asian_klines_ready.emit())
        if orchestrator._alive():
            orchestrator._safe_call_in_ui(orchestrator.host.resume_asian_market_auto_refresh)


def _normalize_log_detail(text: str, limit: int = 120) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    compact = " | ".join(part.strip() for part in raw.splitlines() if part.strip()) or raw
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _format_subprocess_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ProcessExecutionError):
        raw_detail = str(exc.stderr or "").strip() or str(exc.stdout or "").strip()
        summary = f"退出码 {exc.returncode}"
        summary_detail = _normalize_log_detail(raw_detail)
        if summary_detail:
            summary = f"{summary}：{summary_detail}"
        return summary, raw_detail

    message = str(exc or "").strip() or exc.__class__.__name__
    return message, message


def _parse_global_earnings_calendar_refresh_stdout(stdout: str | bytes | None) -> dict[str, object]:
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status", "") or "").strip()
        if status not in {"success", "degraded"}:
            continue
        result = dict(payload)
        result["status"] = status
        result["events"] = max(0, int(result.get("events") or 0))
        return result
    raise ValueError("global earnings calendar refresh result missing")


def _run_global_earnings_calendar_refresh_subprocess() -> dict[str, object]:
    completed = run_python_module(
        "domains.global_earnings_calendar.refresh_cache",
        no_window=True,
        capture_output=True,
        text=True,
        check=True,
        timeout=GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC,
    )
    return _parse_global_earnings_calendar_refresh_stdout(getattr(completed, "stdout", ""))


def _coerce_global_earnings_calendar_refresh_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        coerced = dict(result)
        coerced["status"] = str(coerced.get("status", "") or "success").strip()
        coerced["events"] = max(0, int(coerced.get("events") or 0))
        return coerced
    return {"status": "success", "events": max(0, int(result or 0))}


def _nonnegative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _provider_names(raw_providers: object) -> list[str]:
    if not isinstance(raw_providers, (list, tuple, set)):
        return []
    return [str(provider or "").strip() for provider in raw_providers if str(provider or "").strip()]


def _truthy(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes"}


def _global_earnings_calendar_cache_snapshot() -> dict[str, object]:
    try:
        from domains.global_earnings_calendar.service import GlobalEarningsCalendarService

        service = GlobalEarningsCalendarService()
        events = service.load_events(allow_network=False)
        cache_status = service.load_cache_status()
    except Exception as exc:  # noqa: BLE001 - cache probe must never block the background refresh.
        return {"status": "unavailable", "events": 0, "error": _normalize_log_detail(exc)}

    status = str(cache_status.get("status") or ("hit" if events else "miss")).strip()
    snapshot: dict[str, object] = {
        "status": status,
        "events": max(0, len(events or [])),
    }
    if _truthy(cache_status.get("retryable")):
        snapshot["retryable"] = True
    try:
        reused_event_count = max(0, int(cache_status.get("reused_event_count", 0) or 0))
    except (TypeError, ValueError):
        reused_event_count = 0
    if reused_event_count:
        snapshot["reused_event_count"] = reused_event_count
    return snapshot


def _mark_global_earnings_calendar_refresh_degraded(error: object, *, reason: str) -> dict[str, object]:
    from domains.global_earnings_calendar.service import GlobalEarningsCalendarService

    cache_state = GlobalEarningsCalendarService().mark_refresh_failed(error, reason=reason)
    try:
        reused_event_count = max(0, int(cache_state.get("reused_event_count", 0) or 0))
    except (TypeError, ValueError):
        reused_event_count = 0
    result: dict[str, object] = {
        "status": "degraded",
        "events": reused_event_count,
        "retryable": True,
        "reused_event_count": reused_event_count,
        "reason": str(cache_state.get("reason") or reason),
    }
    providers = cache_state.get("providers")
    if isinstance(providers, (list, tuple, set)):
        result["providers"] = [str(provider or "").strip() for provider in providers if str(provider or "").strip()]
    return result


def ms_until_next_global_earnings_calendar_daily_refresh(now: datetime.datetime | None = None) -> int:
    now = now or datetime.datetime.now()
    target = now.replace(
        hour=GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR,
        minute=GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target = target + datetime.timedelta(days=1)
    return max(1000, int((target - now).total_seconds() * 1000))


def _is_global_earnings_calendar_offpeak(now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now()
    minute_of_day = now.hour * 60 + now.minute
    return (
        now.weekday() >= 5
        or minute_of_day < GLOBAL_EARNINGS_CALENDAR_OFFPEAK_END_MINUTE
        or minute_of_day >= GLOBAL_EARNINGS_CALENDAR_OFFPEAK_START_MINUTE
    )


def _global_earnings_calendar_retry_delay_ms(
    consecutive_failures: int,
    *,
    cache_events: int = 0,
    now: datetime.datetime | None = None,
) -> int:
    failures = max(1, int(consecutive_failures or 1))
    exponent = min(4, failures - 1)
    delay_ms = GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS * (2**exponent)
    if cache_events > 0 and _is_global_earnings_calendar_offpeak(now):
        delay_ms = max(
            delay_ms,
            GLOBAL_EARNINGS_CALENDAR_SYNC_OFFPEAK_STALE_CACHE_MIN_RETRY_DELAY_MS,
        )
        max_delay_ms = GLOBAL_EARNINGS_CALENDAR_SYNC_OFFPEAK_MAX_RETRY_DELAY_MS
    else:
        max_delay_ms = GLOBAL_EARNINGS_CALENDAR_SYNC_ACTIVE_MAX_RETRY_DELAY_MS
    return min(delay_ms, max_delay_ms)


class StartupHostAdapter:
    """Narrow host boundary used by StartupOrchestrator."""

    def __init__(self, main_window: StartupHostPort) -> None:
        self._main_window = main_window

    @property
    def timer_parent(self) -> Any:
        return self._main_window

    @property
    def data_provider(self) -> Any:
        return getattr(self._main_window, "data_provider", None)

    @property
    def workspace(self) -> Any:
        current_workspace = getattr(self._main_window, "current_workspace", None)
        return current_workspace() if callable(current_workspace) else None

    @property
    def fallback_watchlist_tab(self) -> Any:
        return getattr(self._main_window, "tab_watchlist", None)

    @property
    def cache_manager(self) -> Any:
        return getattr(self._main_window, "cache_manager", None)

    @property
    def engine(self) -> Any:
        return getattr(self._main_window, "engine", None)

    @property
    def asian_market_service(self) -> Any:
        return getattr(self._main_window, "asian_market_service", None)

    def is_closing(self) -> bool:
        is_closing = getattr(self._main_window, "is_closing", None)
        return bool(is_closing()) if callable(is_closing) else False

    def call_in_ui(self, callback: Callable[[], object], alive_checker: Callable[[], bool]) -> None:
        call_in_ui = getattr(self._main_window, "call_in_ui", None)
        if callable(call_in_ui):
            call_in_ui(lambda: callback() if alive_checker() else None)
            return
        if alive_checker():
            callback()

    def refresh_code_count_label_from_provider(self) -> Any:
        callback = getattr(self._main_window, "refresh_code_count_label_from_provider", None)
        if callable(callback):
            return callback()
        return None

    def set_code_count_text(self, text: str) -> None:
        label = getattr(self._main_window, "lbl_code_count", None)
        if label is not None:
            label.setText(text)

    def set_status_text(self, text: str) -> None:
        label = getattr(self._main_window, "lbl_status", None)
        if label is not None:
            label.setText(text)

    def set_titlebar_sync_state(self, *args) -> None:
        callback = getattr(self._main_window, "set_titlebar_sync_state", None)
        if callable(callback):
            callback(*args)

    def try_load_rps_from_disk(self, set_status_callback: Callable[..., object]) -> None:
        cache_manager = self.cache_manager
        if cache_manager is not None:
            cache_manager.try_load_rps_from_disk(
                self.engine,
                data_provider=self.data_provider,
                set_status_callback=set_status_callback,
            )

    def update_network_ui(self, online: bool) -> None:
        callback = getattr(self._main_window, "update_network_ui", None)
        if callable(callback):
            callback(online)

    def on_smart_startup_online_done(self) -> None:
        callback = getattr(self._main_window, "on_smart_startup_online_done", None)
        if callable(callback):
            callback()

    def refresh_watchlist_names(self, code2name: dict) -> None:
        workspace = self.workspace
        callback = getattr(workspace, "refresh_watchlist_names", None)
        if callable(callback):
            callback(code2name)

    def defer_asian_market_auto_refresh(self, seconds: float, reason: str = "") -> None:
        service = self.asian_market_service
        callback = getattr(service, "defer_auto_refresh", None)
        if callable(callback):
            callback(seconds, reason)

    def resume_asian_market_auto_refresh(self) -> None:
        service = self.asian_market_service
        clear_defer = getattr(service, "clear_auto_refresh_defer", None)
        if callable(clear_defer):
            clear_defer()
        sync_runtime_state = getattr(service, "sync_runtime_state", None)
        if callable(sync_runtime_state):
            sync_runtime_state()


class _StartupGlobalEarningsScheduleMixin:
    def _schedule_next_global_earnings_calendar_daily_refresh(self) -> None:
        if self._closed:
            return
        self._global_earnings_calendar_daily_timer.start(ms_until_next_global_earnings_calendar_daily_refresh())

    def _schedule_global_earnings_calendar_retry(self, reason: str, *, cache_events: int = 0) -> None:
        if self._closed or not service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            return
        try:
            reused_events = max(0, int(cache_events or 0))
        except (TypeError, ValueError):
            reused_events = 0
        self._global_earnings_calendar_retry_failures += 1
        retry_ms = _global_earnings_calendar_retry_delay_ms(
            self._global_earnings_calendar_retry_failures,
            cache_events=reused_events,
        )
        self._global_earnings_calendar_daily_timer.start(retry_ms)
        retry_seconds = retry_ms // 1000
        log.warning(
            "[startup] global earnings calendar retry scheduled in "
            f"{retry_seconds}s: {reason} | failures={self._global_earnings_calendar_retry_failures} "
            f"| cache_events={reused_events}"
        )

    def _run_daily_global_earnings_calendar_refresh(self) -> None:
        if not _background_preload_is_settled(self.host):
            self._schedule_global_earnings_calendar_preload_retry()
            return
        self.refresh_global_earnings_calendar()
        if not self._closed and service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            self._schedule_next_global_earnings_calendar_daily_refresh()

    def _schedule_global_earnings_calendar_preload_retry(self) -> None:
        if self._closed or not service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            return
        self._global_earnings_calendar_daily_timer.start(GLOBAL_EARNINGS_CALENDAR_PRELOAD_RETRY_DELAY_MS)
        emit_structured_log(
            "startup.global_earnings_calendar.deferred",
            reason="background_preload_active",
            retry_ms=GLOBAL_EARNINGS_CALENDAR_PRELOAD_RETRY_DELAY_MS,
        )

    def refresh_global_earnings_calendar(self) -> None:
        """Silently refresh the global oligarch earnings calendar cache."""
        if self._global_earnings_calendar_sync_running or not self._alive():
            return
        sync_enabled = service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync")
        if not sync_enabled:
            log.info("[startup] daily_global_earnings_calendar_sync toggle disabled, skip earnings calendar sync")
            return
        if not _background_preload_is_settled(self.host):
            self._schedule_global_earnings_calendar_preload_retry()
            return

        self._global_earnings_calendar_sync_running = True

        try:
            self._job_runner.run(GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID, self._refresh_global_earnings_calendar_bg)
        except Exception:
            self._global_earnings_calendar_sync_running = False
            raise

    def _refresh_global_earnings_calendar_bg(self) -> None:
        started_at = time.perf_counter()
        cache_events = 0
        try:
            if not self._alive():
                return
            cache_events = self._record_global_earnings_calendar_cache_ready()
            try:
                refresh_result = _coerce_global_earnings_calendar_refresh_result(
                    _run_global_earnings_calendar_refresh_subprocess()
                )
                self._handle_global_earnings_calendar_refresh_success(refresh_result, started_at)
            except ProcessTimeoutError as exc:
                self._handle_global_earnings_calendar_refresh_error(
                    exc,
                    reason="refresh_timeout",
                    cache_events=cache_events,
                    timed_out=True,
                )
            except (OSError, ProcessExecutionError, RuntimeError, TypeError, ValueError) as exc:
                self._handle_global_earnings_calendar_refresh_error(
                    exc,
                    reason="refresh_failed",
                    cache_events=cache_events,
                )
        finally:
            self._global_earnings_calendar_sync_running = False


class StartupOrchestrator(_StartupGlobalEarningsScheduleMixin):
    """主窗口启动流程协调器。"""

    def __init__(
        self,
        main_window: StartupHostPort | None = None,
        job_runner: Any = None,
        host: StartupHostAdapter | None = None,
    ) -> None:
        self.host = host or StartupHostAdapter(cast(StartupHostPort, main_window))
        self._job_runner = job_runner or background_job_runner
        self._closed = False
        timer_parent = self.host.timer_parent
        self._deferred_timer = QTimer(timer_parent)
        self._deferred_timer.setSingleShot(True)
        self._deferred_timer.timeout.connect(self.deferred_data_load)
        self._smart_timer = QTimer(timer_parent)
        self._smart_timer.setSingleShot(True)
        self._smart_timer.timeout.connect(self.smart_startup)
        self._global_earnings_calendar_daily_timer = QTimer(timer_parent)
        self._global_earnings_calendar_daily_timer.setSingleShot(True)
        self._global_earnings_calendar_daily_timer.timeout.connect(self._run_daily_global_earnings_calendar_refresh)
        self._global_earnings_calendar_sync_running = False
        self._global_earnings_calendar_retry_failures = 0

    def schedule_startup(self) -> None:
        if self._closed:
            return
        self._deferred_timer.start(0)
        self._smart_timer.start(4500)
        if service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            self._schedule_next_global_earnings_calendar_daily_refresh()
        else:
            log.info("[startup] daily_global_earnings_calendar_sync toggle disabled, skip earnings calendar daily sync")

    def shutdown(self) -> None:
        self._closed = True
        self._deferred_timer.stop()
        self._smart_timer.stop()
        self._global_earnings_calendar_daily_timer.stop()
        for task_id in (
            DEFERRED_LOAD_TASK_ID,
            ASIAN_DATA_SYNC_TASK_ID,
            GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID,
            SMART_STARTUP_TASK_ID,
        ):
            self._job_runner.abandon(task_id)

    def _alive(self) -> bool:
        return not self._closed and self.host.timer_parent is not None and not self.host.is_closing()

    def _safe_call_in_ui(self, callback: Callable[[], object]) -> None:
        if not self._alive():
            return
        try:
            self.host.call_in_ui(callback, self._alive)
        except RuntimeError:
            pass

    def _loaded_watchlist_codes(self) -> list[str]:
        workspace = self.host.workspace
        if workspace is None:
            return []

        watchlist_tab = None
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        if callable(get_loaded_tab):
            watchlist_tab = get_loaded_tab("watchlist")
        if watchlist_tab is None:
            watchlist_tab = getattr(workspace, "tab_watchlist", None)
        if watchlist_tab is None:
            watchlist_tab = self.host.fallback_watchlist_tab
        if watchlist_tab is None:
            return []

        get_quote_codes = getattr(watchlist_tab, "get_realtime_quote_codes", None)
        if callable(get_quote_codes):
            try:
                return _normalize_a_share_codes(get_quote_codes())
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                return []

        model = getattr(watchlist_tab, "model", None)
        rows = getattr(model, "row_data", None) or []
        return _normalize_a_share_codes(row.get("代码") or row.get("code", "") for row in rows if isinstance(row, dict))

    def _refresh_startup_code_names(self) -> dict:
        provider = self.host.data_provider
        if provider is None:
            return {}

        current_map = {
            str(raw_code or "").strip(): str(raw_name or "").strip()
            for raw_code, raw_name in dict(getattr(provider, "code2name", {}) or {}).items()
            if str(raw_code or "").strip()
        }
        watchlist_codes = self._loaded_watchlist_codes()
        ensure_code_name_map = getattr(provider, "ensure_code_name_map", None)
        if callable(ensure_code_name_map) and watchlist_codes:
            refreshed_map = ensure_code_name_map(watchlist_codes, refresh_missing=True) or {}
            current_map.update(
                {
                    str(raw_code or "").strip(): str(raw_name or "").strip()
                    for raw_code, raw_name in dict(refreshed_map).items()
                    if str(raw_code or "").strip()
                }
            )

        provider.code2name = current_map
        record_metric(
            "smart_startup_watchlist_name_codes",
            len(watchlist_codes),
            unit="count",
        )
        return current_map

    def _defer_asian_market_auto_refresh(self, seconds: float, reason: str) -> None:
        self._safe_call_in_ui(lambda: self.host.defer_asian_market_auto_refresh(seconds, reason))

    def _schedule_startup_asian_sync(self, callback: Callable[[CancellationToken], object]) -> None:
        def _run_if_alive() -> None:
            if not self._alive():
                return
            if not _startup_asian_tab_is_visible(self.host):
                log.info("[启动] 亚洲页当前不可见，跳过启动期远程 K 线静默同步")
                emit_structured_log(
                    "startup.asian_sync.skipped",
                    reason="asian_tab_hidden",
                )
                return
            if _should_defer_startup_asian_sync(self.host):
                QTimer.singleShot(ASIAN_DATA_SYNC_BUSY_RETRY_DELAY_MS, _run_if_alive)
                return
            token = CancellationToken.with_timeout(ASIAN_DATA_SYNC_TIMEOUT_SEC)
            self._job_runner.run(
                STARTUP_ASIAN_DATA_SYNC,
                lambda: callback(token),
                cancellation_token=token,
                timeout_sec=ASIAN_DATA_SYNC_TIMEOUT_SEC,
            )

        if ASIAN_DATA_SYNC_START_DELAY_MS <= 0:
            _run_if_alive()
        else:
            QTimer.singleShot(ASIAN_DATA_SYNC_START_DELAY_MS, _run_if_alive)

    def _refresh_deferred_code_count(self) -> int | None:
        host_count = self.host.refresh_code_count_label_from_provider()
        if host_count is not None:
            return host_count

        provider = self.host.data_provider
        cache_data = getattr(provider, "cache_data", None) or {}
        code_name_map = getattr(provider, "code2name", None) or {}
        count = len(cache_data)
        if count <= 0:
            count = sum(1 for raw_code in code_name_map if _is_display_a_share_code(raw_code))
        if count > 0:
            self.host.set_code_count_text(f"标的池: {count} 只")
        return count

    def _record_deferred_load_cancelled(self, stage: str) -> None:
        log_process_snapshot(
            "startup.deferred_load.cancelled",
            logger=log,
            extra={"stage": stage},
        )

    def _load_deferred_history_cache(self) -> str:
        if service_toggle_registry.is_enabled("startup_history_cache_load"):
            provider = self.host.data_provider
            return provider.load_cache_from_disk() if provider is not None else ""

        log.info("[启动] 已跳过全量历史缓存预载，历史K线将在扫描/K线窗口按需加载")
        self._safe_call_in_ui(self._refresh_deferred_code_count)
        return ""

    def _publish_deferred_history_cache(self, cache_date: str) -> None:
        count = len(getattr(self.host.data_provider, "cache_data", None) or {})
        self._safe_call_in_ui(lambda: self.host.set_code_count_text(f"标的池 {count}"))
        self._safe_call_in_ui(self._refresh_deferred_code_count)
        self._safe_call_in_ui(lambda: self.host.set_status_text(f"已加载 {count} 只标的缓存(日线: {cache_date})"))
        self._safe_call_in_ui(
            lambda: self.host.set_titlebar_sync_state(
                "cache",
                "本地缓存已加载",
                f"快照 {cache_date}",
            )
        )

    def _run_deferred_data_load(self) -> None:
        started_at = time.perf_counter()
        if not self._alive():
            return
        log_process_snapshot("startup.deferred_load.begin", logger=log)

        cache_date = self._load_deferred_history_cache()
        if not self._alive():
            self._record_deferred_load_cancelled("history_cache")
            return
        if cache_date:
            self._publish_deferred_history_cache(cache_date)
        if not self._alive():
            self._record_deferred_load_cancelled("history_cache_ui")
            return

        self.host.try_load_rps_from_disk(
            set_status_callback=lambda msg: self._safe_call_in_ui(lambda: self.host.set_status_text(msg)),
        )
        if not self._alive():
            self._record_deferred_load_cancelled("rps_cache")
            return

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        log_process_snapshot(
            "startup.deferred_load.end",
            logger=log,
            extra={"cache_loaded": bool(cache_date), "elapsed_ms": int(round(elapsed_ms))},
        )
        record_metric(
            "startup_deferred_load_ms",
            elapsed_ms,
            unit="ms",
            tags={"cache_loaded": str(bool(cache_date)).lower()},
        )
        emit_structured_log(
            "startup.deferred_load.completed",
            elapsed_ms=round(elapsed_ms, 3),
            cache_loaded=bool(cache_date),
            cache_date=str(cache_date or ""),
        )

    def _run_deferred_data_load_owned(self) -> None:
        try:
            self._run_deferred_data_load()
        finally:
            self._safe_call_in_ui(lambda: event_bus.sig_cache_bootstrap_ready.emit())

    def deferred_data_load(self) -> None:
        """延迟恢复历史缓存和 RPS 缓存。"""
        self._job_runner.run(STARTUP_DEFERRED_LOAD, self._run_deferred_data_load_owned)

        if service_toggle_registry.is_enabled("silent_asian_sync"):
            self._schedule_startup_asian_sync(lambda token: _execute_startup_asian_sync(self, token))
        else:
            log.info("[启动] silent_asian_sync toggle disabled, skip background sync")

    def _record_global_earnings_calendar_cache_ready(self) -> int:
        cache_started_at = time.perf_counter()
        cache_snapshot = _global_earnings_calendar_cache_snapshot()
        cache_elapsed_ms = (time.perf_counter() - cache_started_at) * 1000.0
        cache_events = _nonnegative_int(cache_snapshot.get("events", 0))
        cache_status = str(cache_snapshot.get("status", "") or "unknown").strip()
        record_metric(
            "global_earnings_calendar_cache_ready_ms",
            cache_elapsed_ms,
            unit="ms",
            tags={"events": str(cache_events), "status": cache_status},
        )
        emit_structured_log(
            "global_earnings_calendar.cache_ready",
            elapsed_ms=round(cache_elapsed_ms, 3),
            events=cache_events,
            status=cache_status,
        )
        log_process_snapshot(
            "global_earnings_calendar.background_refresh.begin",
            logger=log,
            extra={"cache_events": cache_events, "cache_status": cache_status},
        )
        return cache_events

    def _handle_global_earnings_calendar_refresh_success(self, refresh_result: RefreshResult, started_at: float) -> None:
        event_count = _nonnegative_int(refresh_result.get("events"))
        refresh_status = str(refresh_result.get("status", "") or "success").strip()
        if not self._alive():
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        providers = _provider_names(refresh_result.get("providers"))
        provider_text = " + ".join(providers)
        reused_event_count = _nonnegative_int(refresh_result.get("reused_event_count", 0))
        retryable = _truthy(refresh_result.get("retryable"))
        if refresh_status == "degraded":
            detail = f"{provider_text} 拉取异常" if provider_text else "上游拉取异常"
            detail = f"{detail}，已沿用旧快照 {reused_event_count} 条" if reused_event_count else f"{detail}，已沿用可用旧快照"
            log.warning(f"[global earnings calendar] background refresh degraded: {event_count} events ({detail})")
        else:
            log.info(f"[global earnings calendar] background refresh completed: {event_count} events")
        record_metric(
            "global_earnings_calendar_background_refresh_ms",
            elapsed_ms,
            unit="ms",
            tags={"events": str(event_count), "status": refresh_status},
        )
        self._log_global_earnings_calendar_refresh_end(
            elapsed_ms=elapsed_ms,
            event_count=event_count,
            status=refresh_status,
            provider_text=provider_text,
            reused_event_count=reused_event_count,
            retryable=retryable,
        )
        self._safe_call_in_ui(lambda: event_bus.sig_earnings_updated.emit())
        if refresh_status == "degraded" and retryable:
            self._safe_call_in_ui(
                lambda: self._schedule_global_earnings_calendar_retry(
                    "refresh_degraded_retryable",
                    cache_events=reused_event_count or event_count,
                )
            )
        else:
            self._global_earnings_calendar_retry_failures = 0

    def _log_global_earnings_calendar_refresh_end(
        self,
        *,
        elapsed_ms: float,
        event_count: int,
        status: str,
        provider_text: str = "",
        reused_event_count: int = 0,
        retryable: bool = False,
    ) -> None:
        snapshot_extra = {
            "elapsed_ms": int(round(elapsed_ms)),
            "events": event_count,
            "status": status,
        }
        if provider_text:
            snapshot_extra["providers"] = provider_text
        if reused_event_count:
            snapshot_extra["reused_event_count"] = reused_event_count
        if retryable:
            snapshot_extra["retryable"] = True
        log_process_snapshot(
            "global_earnings_calendar.background_refresh.end",
            logger=log,
            level="warning" if status == "degraded" else "info",
            extra=snapshot_extra,
        )
        emit_structured_log(
            "global_earnings_calendar.background_refresh.completed",
            elapsed_ms=round(elapsed_ms, 3),
            events=event_count,
            status=status,
        )

    def _handle_global_earnings_calendar_refresh_error(
        self,
        exc: Exception,
        *,
        reason: str,
        cache_events: int,
        timed_out: bool = False,
    ) -> None:
        degraded_events = self._mark_global_earnings_calendar_degraded_events(exc, reason, cache_events)
        log_process_snapshot(
            "global_earnings_calendar.background_refresh.end",
            logger=log,
            level="warning",
            extra={
                "status": "degraded",
                "reason": reason,
                "events": degraded_events,
                "reused_event_count": degraded_events,
                "retryable": True,
            },
        )
        if timed_out:
            log.warning(
                "[global earnings calendar] background refresh timed out "
                f"after {GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC}s; reused local cache: {exc}"
            )
        else:
            summary, raw_detail = _format_subprocess_failure(exc)
            log.warning(f"[global earnings calendar] background refresh failed; reused local cache ({summary})")
            if raw_detail:
                log.debug(f"[global earnings calendar] background refresh raw output: {raw_detail}")
        self._safe_call_in_ui(lambda: event_bus.sig_earnings_updated.emit())
        self._safe_call_in_ui(
            lambda: self._schedule_global_earnings_calendar_retry(reason, cache_events=degraded_events)
        )

    def _mark_global_earnings_calendar_degraded_events(
        self,
        exc: Exception,
        reason: str,
        cache_events: int,
    ) -> int:
        try:
            degraded_result = _mark_global_earnings_calendar_refresh_degraded(exc, reason=reason)
            return _nonnegative_int(degraded_result.get("events"))
        except Exception as state_exc:  # noqa: BLE001 - degradation state should never block fail-open reuse.
            log.warning(f"[global earnings calendar] failed to mark {reason} degradation: {state_exc}")
            return cache_events

    def smart_startup(self) -> None:
        """异步检测网络；可联机时切到在线模式并驱动后续刷新。"""
        if _smart_startup_ready(self):
            self._job_runner.run(STARTUP_SMART, lambda: _execute_smart_startup(self))
