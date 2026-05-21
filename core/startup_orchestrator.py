# -*- coding: utf-8 -*-
"""主窗口冷启动与后台预热编排。"""

from __future__ import annotations

import datetime
import os
import time

from PyQt6.QtCore import QTimer

from core.background_job_runner import background_job_runner
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from core.process_watchdog import log_process_snapshot
from domains.runtime import domain_events as event_bus
from infra.features import service_toggle_registry
from infra.tasks import (
    STARTUP_ASIAN_DATA_SYNC,
    STARTUP_DEFERRED_LOAD,
    STARTUP_SMART,
    ProcessExecutionError,
    ProcessTimeoutError,
    run_python_module,
    task_registry,
)

log = get_logger(__name__)
ASIAN_DATA_SYNC_TIMEOUT_SEC = 120
DEFERRED_LOAD_TASK_ID = STARTUP_DEFERRED_LOAD.task_id
ASIAN_DATA_SYNC_TASK_ID = STARTUP_ASIAN_DATA_SYNC.task_id
SMART_STARTUP_TASK_ID = STARTUP_SMART.task_id
GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID = task_registry.startup(
    "global_earnings_calendar_sync",
    description="Global oligarch earnings calendar silent sync",
).task_id
GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR = 2
GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE = 0
AUTO_RT_MONITOR_NETWORK_TASK_ID = task_registry.network(
    "auto_rt_network_probe",
    description="Connectivity probe for intraday monitor auto-start retry",
).task_id


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


def _normalize_a_share_codes(codes) -> list[str]:
    normalized: list[str] = []
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if len(code) == 6 and code.isdigit():
            normalized.append(code)
    return list(dict.fromkeys(normalized))


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


class StartupHostAdapter:
    """Narrow host boundary used by StartupOrchestrator."""

    def __init__(self, main_window):
        self._main_window = main_window

    @property
    def timer_parent(self):
        return self._main_window

    @property
    def data_provider(self):
        return getattr(self._main_window, "data_provider", None)

    @property
    def workspace(self):
        return getattr(self._main_window, "_workspace", None)

    @property
    def fallback_watchlist_tab(self):
        return getattr(self._main_window, "tab_watchlist", None)

    @property
    def cache_manager(self):
        return getattr(self._main_window, "cache_manager", None)

    @property
    def engine(self):
        return getattr(self._main_window, "engine", None)

    def is_closing(self) -> bool:
        return bool(getattr(self._main_window, "_is_closing", False))

    def call_in_ui(self, callback, alive_checker) -> None:
        call_in_ui = getattr(self._main_window, "_call_in_ui", None)
        if callable(call_in_ui):
            call_in_ui(lambda: callback() if alive_checker() else None)
            return
        if alive_checker():
            callback()

    def refresh_code_count_label_from_provider(self):
        callback = getattr(self._main_window, "_refresh_code_count_label_from_provider", None)
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
        callback = getattr(self._main_window, "_set_titlebar_sync_state", None)
        if callable(callback):
            callback(*args)

    def get_rt_table(self):
        workspace = self.workspace
        getter = getattr(workspace, "get_rt_table", None)
        return getter() if callable(getter) else None

    def load_rt_cache(self) -> None:
        cache_manager = self.cache_manager
        if cache_manager is not None:
            cache_manager.load_rt_cache(self.get_rt_table(), self.set_status_text)

    def try_load_rps_from_disk(self, set_status_callback) -> None:
        cache_manager = self.cache_manager
        if cache_manager is not None:
            cache_manager.try_load_rps_from_disk(
                self.engine,
                data_provider=self.data_provider,
                set_status_callback=set_status_callback,
            )

    def update_network_ui(self, online: bool) -> None:
        callback = getattr(self._main_window, "_update_network_ui", None)
        if callable(callback):
            callback(online)

    def on_smart_startup_online_done(self) -> None:
        callback = getattr(self._main_window, "_on_smart_startup_online_done", None)
        if callable(callback):
            callback()

    def refresh_watchlist_names(self, code2name: dict) -> None:
        workspace = self.workspace
        callback = getattr(workspace, "refresh_watchlist_names", None)
        if callable(callback):
            callback(code2name)

    def auto_start_rt_monitor(self) -> bool:
        callback = getattr(self._main_window, "auto_start_rt_monitor", None)
        if callable(callback):
            return bool(callback())
        workspace = self.workspace
        callback = getattr(workspace, "auto_start_rt_monitor", None)
        return bool(callback()) if callable(callback) else False


class StartupOrchestrator:
    """主窗口启动流程协调器。"""

    def __init__(self, main_window=None, job_runner=None, host=None):
        self.host = host or StartupHostAdapter(main_window)
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
        self._auto_rt_timer = None
        self._auto_rt_network_probe_active = False
        self._global_earnings_calendar_sync_running = False
        self._last_auto_rt_skip_reason = ""

    def schedule_startup(self):
        if self._closed:
            return
        self._deferred_timer.start(2500)
        self._smart_timer.start(4500)
        if service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            self._schedule_next_global_earnings_calendar_daily_refresh()
        else:
            log.info("[startup] daily_global_earnings_calendar_sync toggle disabled, skip earnings calendar daily sync")
        log.info("[startup] workspace_auto_rt_monitor retry timer is owned by AutoRefreshScheduler")

    def _schedule_next_global_earnings_calendar_daily_refresh(self):
        if self._closed:
            return
        self._global_earnings_calendar_daily_timer.start(ms_until_next_global_earnings_calendar_daily_refresh())

    def _run_daily_global_earnings_calendar_refresh(self):
        self.refresh_global_earnings_calendar()
        if not self._closed and service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync"):
            self._schedule_next_global_earnings_calendar_daily_refresh()

    def shutdown(self):
        self._closed = True
        self._deferred_timer.stop()
        self._smart_timer.stop()
        self._global_earnings_calendar_daily_timer.stop()
        if self._auto_rt_timer is not None:
            self._auto_rt_timer.stop()
        for task_id in (
            DEFERRED_LOAD_TASK_ID,
            ASIAN_DATA_SYNC_TASK_ID,
            GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID,
            SMART_STARTUP_TASK_ID,
            AUTO_RT_MONITOR_NETWORK_TASK_ID,
        ):
            self._job_runner.abandon(task_id)

    def _alive(self):
        return not self._closed and self.host.timer_parent is not None and not self.host.is_closing()

    def _safe_call_in_ui(self, callback):
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

    def deferred_data_load(self):
        """延迟恢复历史缓存、实时缓存和 RPS 缓存。"""

        def _is_display_a_share_code(raw_code) -> bool:
            code = str(raw_code or "").strip()
            return len(code) == 6 and code.isdigit() and code.startswith(("60", "68", "00", "30"))

        def _refresh_code_count_label_from_provider():
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

        def _load_bg():
            started_at = time.perf_counter()
            if not self._alive():
                return
            log_process_snapshot("startup.deferred_load.begin", logger=log)

            cache_date = ""
            if service_toggle_registry.is_enabled("startup_history_cache_load"):
                provider = self.host.data_provider
                if provider is not None:
                    cache_date = provider.load_cache_from_disk()
            else:
                log.info("[启动] 已跳过全量历史缓存预载，历史K线将在扫描/盘中监控/K线窗口按需加载")
                self._safe_call_in_ui(_refresh_code_count_label_from_provider)
            if not self._alive():
                log_process_snapshot(
                    "startup.deferred_load.cancelled",
                    logger=log,
                    extra={"stage": "history_cache"},
                )
                return
            if cache_date and self._alive():
                count = len(getattr(self.host.data_provider, "cache_data", None) or {})
                self._safe_call_in_ui(lambda: self.host.set_code_count_text(f"标的池 {count}"))
                self._safe_call_in_ui(_refresh_code_count_label_from_provider)
                self._safe_call_in_ui(
                    lambda: self.host.set_status_text(f"已加载 {count} 只标的缓存(日线: {cache_date})")
                )
                self._safe_call_in_ui(
                    lambda: self.host.set_titlebar_sync_state(
                        "cache",
                        "本地缓存已加载",
                        f"快照 {cache_date}",
                    )
                )

            self._safe_call_in_ui(self.host.load_rt_cache)
            if not self._alive():
                log_process_snapshot(
                    "startup.deferred_load.cancelled",
                    logger=log,
                    extra={"stage": "rt_cache"},
                )
                return

            self.host.try_load_rps_from_disk(
                set_status_callback=lambda msg: self._safe_call_in_ui(lambda: self.host.set_status_text(msg)),
            )
            if not self._alive():
                log_process_snapshot(
                    "startup.deferred_load.cancelled",
                    logger=log,
                    extra={"stage": "rps_cache"},
                )
                return

            self._safe_call_in_ui(lambda: event_bus.sig_cache_bootstrap_ready.emit())
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

        self._job_runner.run(STARTUP_DEFERRED_LOAD, _load_bg)

        def _check_asian_data_bg():
            started_at = time.perf_counter()
            if not self._alive():
                return

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(project_root, "data", "Cache")
            json_cache = os.path.join(output_dir, "asian_klines_latest.json")
            module_entry = os.path.join(
                project_root,
                "vcp",
                "fetchers",
                "asian_kline_fetcher.py",
            )

            needs_update = False
            if not os.path.exists(json_cache):
                needs_update = True
            else:
                mtime = os.path.getmtime(json_cache)
                mdate = datetime.date.fromtimestamp(mtime)
                if mdate < datetime.date.today():
                    needs_update = True

            if needs_update and os.path.exists(module_entry):
                log_process_snapshot("startup.asian_sync.begin", logger=log)
                log.info("[启动] 亚洲市场 JSON 非最新，后台静默增量同步中...")
                try:
                    run_python_module(
                        "vcp.fetchers.asian_kline_fetcher",
                        ["--strict-sync", "--output-dir", output_dir],
                        check=True,
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                        timeout=ASIAN_DATA_SYNC_TIMEOUT_SEC,
                        no_window=True,
                    )
                    log.info("[启动] 亚洲市场静默同步完成，触发界面刷新。")
                    if not self._alive():
                        return
                    self._safe_call_in_ui(lambda: event_bus.sig_asian_klines_ready.emit())
                    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                    record_metric("startup_asian_sync_ms", elapsed_ms, unit="ms")
                    log_process_snapshot(
                        "startup.asian_sync.end",
                        logger=log,
                        extra={"elapsed_ms": int(round(elapsed_ms)), "status": "success"},
                    )
                    emit_structured_log(
                        "startup.asian_sync.completed",
                        elapsed_ms=round(elapsed_ms, 3),
                        output_dir=output_dir,
                    )
                except ProcessTimeoutError:
                    log_process_snapshot(
                        "startup.asian_sync.end",
                        logger=log,
                        level="warning",
                        extra={"status": "timeout"},
                    )
                    log.warning(f"[启动] 亚洲市场后台静默同步超时({ASIAN_DATA_SYNC_TIMEOUT_SEC}s)，已跳过本次同步")
                except (OSError, ProcessExecutionError, ValueError) as exc:
                    log_process_snapshot(
                        "startup.asian_sync.end",
                        logger=log,
                        level="warning",
                        extra={"status": "failed"},
                    )
                    summary, raw_detail = _format_subprocess_failure(exc)
                    log.warning(f"[启动] 亚洲市场静默同步失败，已跳过本次更新（{summary}）")
                    if raw_detail:
                        log.debug(f"[启动] 亚洲市场静默同步原始输出: {raw_detail}")

        if service_toggle_registry.is_enabled("silent_asian_sync"):
            self._job_runner.run(STARTUP_ASIAN_DATA_SYNC, _check_asian_data_bg)
        else:
            log.info("[启动] silent_asian_sync toggle disabled, skip background sync")

    def refresh_global_earnings_calendar(self):
        """Silently refresh the global oligarch earnings calendar cache."""
        if self._global_earnings_calendar_sync_running or not self._alive():
            return
        sync_enabled = service_toggle_registry.is_enabled("daily_global_earnings_calendar_sync")
        if not sync_enabled:
            log.info("[startup] daily_global_earnings_calendar_sync toggle disabled, skip earnings calendar sync")
            return

        self._global_earnings_calendar_sync_running = True

        def _refresh_bg():
            started_at = time.perf_counter()
            if not self._alive():
                self._global_earnings_calendar_sync_running = False
                return
            log_process_snapshot("startup.global_earnings_calendar.begin", logger=log)
            try:
                from domains.global_earnings_calendar.service import GlobalEarningsCalendarService

                events = GlobalEarningsCalendarService().refresh_events()
                if not self._alive():
                    return
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                event_count = len(events or [])
                log.info(f"[启动] 寡头财报日历静默刷新完成: {event_count} 条")
                record_metric(
                    "startup_global_earnings_calendar_sync_ms",
                    elapsed_ms,
                    unit="ms",
                    tags={"events": str(event_count)},
                )
                log_process_snapshot(
                    "startup.global_earnings_calendar.end",
                    logger=log,
                    extra={"elapsed_ms": int(round(elapsed_ms)), "events": event_count, "status": "success"},
                )
                emit_structured_log(
                    "startup.global_earnings_calendar.completed",
                    elapsed_ms=round(elapsed_ms, 3),
                    events=event_count,
                )
                self._safe_call_in_ui(lambda: event_bus.sig_earnings_updated.emit())
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log_process_snapshot(
                    "startup.global_earnings_calendar.end",
                    logger=log,
                    level="warning",
                    extra={"status": "failed"},
                )
                log.warning(f"[启动] 寡头财报日历静默刷新失败，已沿用本地缓存: {exc}")

            finally:
                self._global_earnings_calendar_sync_running = False

        try:
            self._job_runner.run(GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID, _refresh_bg)
        except Exception:
            self._global_earnings_calendar_sync_running = False
            raise

    def smart_startup(self):
        """异步检测网络；可联机时切到在线模式并驱动后续刷新。"""

        def _check_and_go_online():
            started_at = time.perf_counter()
            try:
                if not self._alive():
                    return
                log_process_snapshot("startup.smart.begin", logger=log)
                provider = self.host.data_provider
                online = bool(provider and provider.test_network(timeout=3))
                if not self._alive():
                    return
                if online:
                    if not self._alive():
                        return
                    if provider is not None:
                        provider.set_online_mode(True)
                    log.info("[智能启动] 网络可用，已自动切换到联机模式")

                    try:
                        if not self._alive():
                            return
                        code2name = self._refresh_startup_code_names()
                        self._safe_call_in_ui(lambda: self.host.refresh_watchlist_names(code2name))
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        log.error(f"[智能启动] 后台同步代码名称映射失败: {exc}")

                    self._safe_call_in_ui(lambda: self.host.update_network_ui(True))
                    self._safe_call_in_ui(self.host.on_smart_startup_online_done)
                    self._safe_call_in_ui(self.auto_start_rt_if_ready)
                else:
                    log.info("[智能启动] 网络不可用，保持离线模式")
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
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log_process_snapshot(
                    "startup.smart.end",
                    logger=log,
                    level="warning",
                    extra={"status": "failed"},
                )
                log.error(f"[智能启动] 网络检测异常: {exc}")

        self._job_runner.run(STARTUP_SMART, _check_and_go_online)

    def _log_auto_rt_skip(self, reason: str, message: str) -> None:
        if self._last_auto_rt_skip_reason == reason:
            return
        self._last_auto_rt_skip_reason = reason
        log.info(message)

    def _provider_is_online(self) -> bool:
        provider = self.host.data_provider
        is_online = getattr(provider, "is_online", None)
        if callable(is_online):
            return bool(is_online())
        return True

    def _probe_network_for_auto_rt(self) -> None:
        if self._auto_rt_network_probe_active or not self._alive():
            return

        provider = self.host.data_provider
        test_network = getattr(provider, "test_network", None)
        set_online_mode = getattr(provider, "set_online_mode", None)
        if not callable(test_network) or not callable(set_online_mode):
            self._log_auto_rt_skip(
                "auto_rt_offline_no_probe",
                "[盘中监控] 自动启动等待联网，当前数据源不支持后台探测",
            )
            return

        self._auto_rt_network_probe_active = True

        def _probe():
            ok = bool(test_network(timeout=3))
            if not self._alive():
                return False
            if ok:
                set_online_mode(True)
            return ok

        def _on_probe_result(ok):
            self._auto_rt_network_probe_active = False
            if not self._alive():
                return
            if not ok:
                self._log_auto_rt_skip("auto_rt_offline", "[盘中监控] 自动启动等待网络可用")
                return
            self._last_auto_rt_skip_reason = ""
            self._safe_call_in_ui(lambda: self.host.update_network_ui(True))
            self._safe_call_in_ui(self.host.on_smart_startup_online_done)
            self._safe_call_in_ui(self.auto_start_rt_if_ready)

        def _on_probe_error(msg):
            self._auto_rt_network_probe_active = False
            self._log_auto_rt_skip(
                "auto_rt_offline_error",
                f"[盘中监控] 自动启动联网探测异常: {_normalize_log_detail(msg)}",
            )

        self._job_runner.run(
            AUTO_RT_MONITOR_NETWORK_TASK_ID,
            _probe,
            on_success=_on_probe_result,
            on_error=_on_probe_error,
        )

    def auto_start_rt_if_ready(self):
        """按条件自动开启盘中监控；由启动完成和全局重试定时器共同驱动。"""
        try:
            if not self._alive():
                return
            if not service_toggle_registry.is_enabled("workspace_auto_rt_monitor"):
                self._log_auto_rt_skip(
                    "auto_rt_toggle_disabled",
                    "[盘中监控] workspace_auto_rt_monitor toggle disabled",
                )
                return

            from core.market_calendar import MarketCalendar

            if not MarketCalendar.is_market_active():
                self._log_auto_rt_skip("auto_rt_inactive", "[盘中监控] 非交易活跃时段，跳过自动监控")
                return
            provider = self.host.data_provider
            cache_data = getattr(provider, "cache_data", None) or {}
            if not cache_data or len(cache_data) < 100:
                self._log_auto_rt_skip("auto_rt_cache_missing", "[盘中监控] 数据不足，等待缓存就绪后自动重试")
                return
            if not self._provider_is_online():
                self._log_auto_rt_skip("auto_rt_offline", "[盘中监控] 自动启动等待网络可用")
                self._probe_network_for_auto_rt()
                return

            if self.host.auto_start_rt_monitor():
                self._last_auto_rt_skip_reason = ""
                log.info("[智能启动] 盘中监控已自动启动")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[智能启动] 自动监控启动异常: {exc}")
