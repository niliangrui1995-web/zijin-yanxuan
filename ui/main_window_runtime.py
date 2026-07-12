# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from PyQt6.QtCore import QThread, QTimer

from app.services.ui_task_lifecycle_service import task_lifecycle_for
from app.services.ui_task_service import WINDOW_F5_PRECOMPUTE
from core.global_store import global_store
from core.logger import get_logger

log = get_logger(__name__)

F5_SYSTEM_LOG_NAV_SETTLE_MS = 10_000
F5_SYSTEM_LOG_STALL_GRACE_MS = 12_000
F5_SYSTEM_LOG_FOREGROUND_RECHECK_MS = 2_500
F5_BACKGROUND_TASK_PRIORITY = -1
F5_BACKGROUND_THREAD_PRIORITY = QThread.Priority.LowestPriority


def workspace_tables(main_window):
    workspace = getattr(main_window, "_workspace", None)
    if workspace is None:
        return []
    iter_tables = getattr(workspace, "iter_tables", None)
    return iter_tables() if callable(iter_tables) else []


def run_post_online_refresh(main_window, task_manager):
    main_window._update_network_ui(True)
    workspace = getattr(main_window, "_workspace", None)
    if workspace is None:
        return
    workspace.run_post_online_refresh(task_manager)


def safe_run_post_online_refresh(main_window, task_manager):
    try:
        run_post_online_refresh(main_window, task_manager)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.error(f"[智能启动] 联网后Tab刷新异常: {exc}")


def _current_workspace_tab_key(main_window) -> str:
    current_key = getattr(main_window, "_current_workspace_tab_key", None)
    if not callable(current_key):
        return ""
    try:
        return str(current_key() or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _system_log_shell_nav_grace_remaining_ms(main_window) -> int:
    if _current_workspace_tab_key(main_window) != "system_log":
        return 0
    workspace = getattr(main_window, "_workspace", None)
    last_nav_at = getattr(workspace, "_last_system_log_shell_nav_load_at", 0.0)
    try:
        elapsed_ms = (time.perf_counter() - float(last_nav_at or 0.0)) * 1000.0
    except (TypeError, ValueError):
        return 0
    if elapsed_ms < 0 or elapsed_ms >= F5_SYSTEM_LOG_NAV_SETTLE_MS:
        return 0
    return int(F5_SYSTEM_LOG_NAV_SETTLE_MS - elapsed_ms)


def _should_hold_f5_for_system_log_foreground(main_window, *, wait_for_system_log: bool) -> bool:
    return bool(wait_for_system_log and _current_workspace_tab_key(main_window) == "system_log")


def _mark_f5_ui_stall_grace(main_window) -> None:
    try:
        deadline = time.perf_counter() + F5_SYSTEM_LOG_STALL_GRACE_MS / 1000.0
    except (TypeError, ValueError):
        return
    current_deadline = getattr(main_window, "_f5_precompute_ui_grace_until", 0.0)
    try:
        deadline = max(deadline, float(current_deadline or 0.0))
    except (TypeError, ValueError):
        pass
    setattr(main_window, "_f5_precompute_ui_grace_until", deadline)


def _clear_f5_ui_stall_grace(main_window) -> None:
    setattr(main_window, "_f5_precompute_ui_grace_until", 0.0)


class _F5TaskCallbacks:
    def __init__(self, main_window) -> None:
        self.main_window = main_window
        self.token = None

    def cancelled(self) -> bool:
        return bool(
            getattr(self.main_window, "_is_closing", False)
            or getattr(self.main_window, "_f5_cancelled", False)
            or (self.token is not None and self.token.cancelled)
        )

    def _call_in_ui(self, action) -> None:
        if not self.cancelled():
            self.main_window._call_in_ui(lambda: None if self.cancelled() else action())

    def set_status(self, message: str) -> None:
        def _apply() -> None:
            if hasattr(self.main_window, "lbl_status"):
                self.main_window.lbl_status.setText(message)
            if hasattr(self.main_window, "_set_titlebar_sync_state"):
                self.main_window._set_titlebar_sync_state("working", str(message or "").strip())

        self._call_in_ui(_apply)

    def done(self, count, elapsed) -> None:
        self._call_in_ui(
            lambda: (
                _clear_f5_ui_stall_grace(self.main_window),
                self.main_window._on_f5_done(count, elapsed),
            )
        )

    def run(self, cancellation_token):
        from core.rps_precomputer import RPSPrecomputer

        self.token = cancellation_token
        return RPSPrecomputer.run_f5_pipeline(
            data_provider=self.main_window.data_provider,
            engine=self.main_window.engine,
            cancelled_checker=self.cancelled,
            set_status_callback=self.set_status,
            done_callback=self.done,
        )


def _submit_owned_f5_task(main_window, task_manager) -> None:
    callbacks = _F5TaskCallbacks(main_window)
    task_lifecycle_for(main_window, runner=task_manager).run_background(
        "f5_precompute",
        callbacks.run,
        task_id=WINDOW_F5_PRECOMPUTE,
        timeout_sec=30 * 60.0,
        runner=task_manager,
        task_priority=F5_BACKGROUND_TASK_PRIORITY,
        thread_priority=F5_BACKGROUND_THREAD_PRIORITY,
    )


def start_f5_precompute(main_window, *, task_manager):
    if getattr(main_window, "_f5_precompute_start_pending", False):
        log.info("[F5] precompute start already pending")
        return

    wait_for_system_log = False

    def _submit_precompute():
        if getattr(main_window, "_f5_cancelled", False):
            setattr(main_window, "_f5_precompute_start_pending", False)
            _clear_f5_ui_stall_grace(main_window)
            return
        if _should_hold_f5_for_system_log_foreground(main_window, wait_for_system_log=wait_for_system_log):
            log.info("[F5] hold precompute start while system_log remains foreground after shell navigation")
            QTimer.singleShot(F5_SYSTEM_LOG_FOREGROUND_RECHECK_MS, _submit_precompute)
            return
        setattr(main_window, "_f5_precompute_start_pending", False)
        _mark_f5_ui_stall_grace(main_window)
        _submit_owned_f5_task(main_window, task_manager)

    delay_ms = _system_log_shell_nav_grace_remaining_ms(main_window)
    if delay_ms > 0:
        wait_for_system_log = True
        setattr(main_window, "_f5_precompute_start_pending", True)
        log.info("[F5] defer precompute start %sms after system_log shell navigation", delay_ms)
        QTimer.singleShot(delay_ms, _submit_precompute)
        return

    _submit_precompute()


def finish_f5_reload(main_window, *, count, elapsed, event_bus):
    """完成 F5 后的缓存重载收尾，只保留 UI 壳层必须知道的结果。"""
    main_window._update_last_f5_time()
    workspace = getattr(main_window, "_workspace", None)

    refresh_after_reload = getattr(
        getattr(main_window, "central_quotes_svc", None),
        "refresh_after_cache_reload",
        None,
    )
    if callable(refresh_after_reload):
        try:
            refresh_after_reload()
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.error(f"[F5] 刷新全局报价快照异常: {exc}")

    scheduled_refresh_started = False
    refresh_all_tabs_after_f5_scheduled = getattr(workspace, "refresh_all_tabs_after_f5_scheduled", None)
    if callable(refresh_all_tabs_after_f5_scheduled):
        try:
            try:
                scheduled_refresh_started = bool(
                    refresh_all_tabs_after_f5_scheduled(
                        interval_ms=0,
                        skip_cache_reload_tabs=True,
                    )
                )
            except TypeError:
                scheduled_refresh_started = bool(refresh_all_tabs_after_f5_scheduled(interval_ms=0))
        except (AttributeError, RuntimeError, TypeError) as exc:
            scheduled_refresh_started = False
            log.error(f"[F5] 工作区快照分帧刷新异常: {exc}")

    refresh_all_tabs_after_f5 = getattr(workspace, "refresh_all_tabs_after_f5", None)
    if not scheduled_refresh_started and callable(refresh_all_tabs_after_f5):
        try:
            try:
                refresh_all_tabs_after_f5(skip_cache_reload_tabs=True)
            except TypeError:
                refresh_all_tabs_after_f5()
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.error(f"[F5] 刷新各 Tab 表格快照异常: {exc}")

    try:
        event_bus.sig_cache_reload_completed.emit()
    except (AttributeError, RuntimeError, TypeError) as exc:
        log.error(f"[F5] 广播缓存重载完成信号异常: {exc}")

    scheduled_info_refresh_started = False
    refresh_information_sources_after_f5_scheduled = getattr(
        workspace,
        "refresh_information_sources_after_f5_scheduled",
        None,
    )
    if callable(refresh_information_sources_after_f5_scheduled):
        try:
            scheduled_info_refresh_started = bool(refresh_information_sources_after_f5_scheduled(interval_ms=2500))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            scheduled_info_refresh_started = False
            log.error(f"[F5] scheduled information source refresh failed: {exc}")

    refresh_information_sources_after_f5 = getattr(workspace, "refresh_information_sources_after_f5", None)
    if not scheduled_info_refresh_started and callable(refresh_information_sources_after_f5):
        try:
            refresh_information_sources_after_f5()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[F5] 情报源自动刷新异常: {exc}")
    elif not scheduled_info_refresh_started:
        auto_sync_after_f5 = getattr(workspace, "run_fund_holdings_auto_sync_after_f5", None)
        if callable(auto_sync_after_f5):
            try:
                auto_sync_after_f5()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.error(f"[F5] 基金持仓自动更新异常: {exc}")

    if count > 0:
        if hasattr(main_window, "lbl_status"):
            main_window.lbl_status.setText(f"F5预计算完成: {count}只 | 耗时{elapsed:.1f}s")
        if hasattr(main_window, "lbl_code_count"):
            main_window.lbl_code_count.setText(f"标的池: {count} 只")
        if hasattr(main_window, "_set_titlebar_sync_state"):
            main_window._set_titlebar_sync_state("success", f"已同步 {count}只")
        return

    if hasattr(main_window, "lbl_status"):
        main_window.lbl_status.setText("F5预计算完成: 无新增数据")
    if hasattr(main_window, "_set_titlebar_sync_state"):
        main_window._set_titlebar_sync_state("cache", "无新增，沿用现有快照")


def shutdown_main_window(main_window, *, event_bus, task_manager):
    main_window._is_closing = True
    main_window._f5_cancelled = True

    def _run(label: str, action):
        try:
            action()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[关闭] {label}异常: {exc}")

    lifecycle = getattr(main_window, "_task_lifecycle", None)
    if lifecycle is not None:
        _run("停止窗口后台任务", lambda: lifecycle.shutdown(timeout_ms=1_500))

    from app.services.ui_market_calendar_service import shutdown_market_calendar_tasks

    _run("停止交易日历后台任务", lambda: shutdown_market_calendar_tasks(timeout_ms=750))

    if hasattr(main_window, "startup_orchestrator"):
        _run("停止启动编排器", main_window.startup_orchestrator.shutdown)

    auto_refresh_scheduler = getattr(main_window, "auto_refresh_scheduler", None)
    auto_refresh_shutdown = getattr(auto_refresh_scheduler, "shutdown", None)
    if callable(auto_refresh_shutdown):
        _run("停止自动刷新调度器", auto_refresh_shutdown)

    asian_market_service = getattr(main_window, "asian_market_service", None)
    asian_service_shutdown = getattr(asian_market_service, "shutdown", None)
    if callable(asian_service_shutdown):
        _run("stop asian market service", asian_service_shutdown)

    earnings_refresh_service = getattr(main_window, "earnings_refresh_service", None)
    earnings_service_shutdown = getattr(earnings_refresh_service, "shutdown", None)
    if callable(earnings_service_shutdown):
        _run("stop earnings refresh service", earnings_service_shutdown)

    central_quotes_svc = getattr(main_window, "central_quotes_svc", None)
    central_quotes_shutdown = getattr(central_quotes_svc, "shutdown", None)
    if callable(central_quotes_shutdown):
        _run("停止中央报价服务", central_quotes_shutdown)

    if main_window._workspace is not None:
        _run("停止工作区", main_window._workspace.shutdown)

    _run("保存UI状态", main_window._save_ui_state)

    _run("广播关闭信号", event_bus.sig_app_closing.emit)
    _run("重置全局快照状态", global_store.reset_runtime_state)
    _run("TaskManager 关停", task_manager.shutdown)
