# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QObject, QTimer

from app.services.ui_market_calendar_service import shutdown_market_calendar_tasks
from core.global_store import global_store
from core.logger import get_logger
from ui.components.kline_window_manager import kline_manager

log = get_logger(__name__)

F5_SYSTEM_LOG_NAV_SETTLE_MS = 10_000
F5_SYSTEM_LOG_STALL_GRACE_MS = 12_000
F5_SYSTEM_LOG_FOREGROUND_RECHECK_MS = 2_500
F5_SYSTEM_LOG_FOREGROUND_HOLD_MAX_MS = 20_000
F5_REFRESH_FRAME_INTERVAL_MS = 16


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
    if not wait_for_system_log or _current_workspace_tab_key(main_window) != "system_log":
        return False
    deadline = getattr(main_window, "_f5_system_log_hold_deadline", 0.0)
    if not deadline:
        return True
    try:
        return time.perf_counter() < float(deadline)
    except (TypeError, ValueError):
        return False


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


def _set_f5_status(main_window, message: str, state: str = "working") -> None:
    if hasattr(main_window, "lbl_status"):
        main_window.lbl_status.setText(str(message or ""))
    if hasattr(main_window, "_set_titlebar_sync_state"):
        main_window._set_titlebar_sync_state(state, str(message or "").strip())


def _ensure_f5_controller(main_window):
    if getattr(main_window, "data_provider", None) is None or getattr(main_window, "engine", None) is None:
        raise RuntimeError("F5 runtime dependencies are not ready")
    existing = getattr(main_window, "_f5_job_controller", None)
    if existing is not None:
        return existing

    from app.services.f5_job_runner import ProcessF5JobRunner
    from app.services.f5_snapshot_installer import F5SnapshotInstaller
    from core.runtime_paths import CACHE_DIR, PROJECT_ROOT, get_data_dir
    from ui.services.f5_job_controller import F5JobController

    runner = getattr(main_window, "_f5_job_runner", None) or ProcessF5JobRunner()
    installer = F5SnapshotInstaller(
        data_provider=main_window.data_provider,
        engine=main_window.engine,
        database_path=os.path.join(get_data_dir(""), "vcp_hunter.db"),
        cache_dir=CACHE_DIR,
    )
    parent = main_window if isinstance(main_window, QObject) else None
    controller = F5JobController(runner=runner, installer=installer, parent=parent)
    setattr(main_window, "_f5_job_controller", controller)
    setattr(main_window, "_f5_job_project_root", PROJECT_ROOT)
    return controller


def _submit_owned_f5_task(main_window) -> None:
    from app.services.f5_job_contract import F5JobRequest, F5JobStatus
    from core.runtime_paths import CACHE_DIR, PROJECT_ROOT, get_data_dir

    controller = _ensure_f5_controller(main_window)
    request = F5JobRequest.build(
        project_root=PROJECT_ROOT,
        data_dir=get_data_dir(""),
        cache_dir=CACHE_DIR,
        tdx_vipdoc=str(getattr(main_window.data_provider, "tdx_vipdoc", "") or ""),
    )

    def _on_event(event) -> None:
        if not getattr(main_window, "_is_closing", False):
            _set_f5_status(main_window, event.message, "working")

    def _on_finished(result) -> None:
        _clear_f5_ui_stall_grace(main_window)
        if getattr(main_window, "_is_closing", False):
            return
        if result.status is F5JobStatus.SUCCEEDED:
            main_window._on_f5_done(result.symbol_count, result.elapsed_seconds)
            return
        if result.status is F5JobStatus.CANCELLED:
            if result.error_code == "deadline_exceeded":
                _set_f5_status(main_window, "F5 预计算超时", "error")
            else:
                _set_f5_status(main_window, "F5 预计算已取消", "cache")
            return
        message = result.error_message or "未知错误"
        _set_f5_status(main_window, f"F5 预计算失败: {message}", "error")

    if not controller.start(request, on_event=_on_event, on_finished=_on_finished):
        log.info("[F5] isolated job lane is already running")


def start_f5_precompute(main_window):
    if getattr(main_window, "_f5_precompute_start_pending", False):
        log.info("[F5] precompute start already pending")
        return

    wait_for_system_log = False

    def _submit_precompute():
        if getattr(main_window, "_f5_cancelled", False):
            setattr(main_window, "_f5_precompute_start_pending", False)
            setattr(main_window, "_f5_system_log_hold_deadline", 0.0)
            _clear_f5_ui_stall_grace(main_window)
            return
        if _should_hold_f5_for_system_log_foreground(main_window, wait_for_system_log=wait_for_system_log):
            log.info("[F5] hold precompute start while system_log remains foreground after shell navigation")
            QTimer.singleShot(F5_SYSTEM_LOG_FOREGROUND_RECHECK_MS, _submit_precompute)
            return
        setattr(main_window, "_f5_precompute_start_pending", False)
        setattr(main_window, "_f5_system_log_hold_deadline", 0.0)
        _mark_f5_ui_stall_grace(main_window)
        _submit_owned_f5_task(main_window)

    delay_ms = _system_log_shell_nav_grace_remaining_ms(main_window)
    if delay_ms > 0:
        wait_for_system_log = True
        setattr(main_window, "_f5_precompute_start_pending", True)
        setattr(
            main_window,
            "_f5_system_log_hold_deadline",
            time.perf_counter() + F5_SYSTEM_LOG_FOREGROUND_HOLD_MAX_MS / 1000.0,
        )
        log.info("[F5] defer precompute start %sms after system_log shell navigation", delay_ms)
        QTimer.singleShot(delay_ms, _submit_precompute)
        return

    _submit_precompute()


def _post_f5_quote_refresh_callback(main_window):
    refresh_after_reload = getattr(
        getattr(main_window, "central_quotes_svc", None),
        "refresh_after_cache_reload",
        None,
    )
    state = {"queued": False}

    def _queue() -> None:
        if state["queued"] or not callable(refresh_after_reload):
            return
        state["queued"] = True

        def _refresh() -> None:
            try:
                refresh_after_reload()
            except (AttributeError, RuntimeError, TypeError) as exc:
                log.error(f"[F5] 刷新全局报价快照异常: {exc}")

        QTimer.singleShot(F5_REFRESH_FRAME_INTERVAL_MS, _refresh)

    return _queue


def _try_scheduled_f5_snapshot_refresh(workspace, on_finished) -> bool:
    scheduled_refresh = getattr(workspace, "refresh_all_tabs_after_f5_scheduled", None)
    if not callable(scheduled_refresh):
        return False
    try:
        try:
            return bool(
                scheduled_refresh(
                    on_finished=on_finished,
                    interval_ms=F5_REFRESH_FRAME_INTERVAL_MS,
                    skip_cache_reload_tabs=True,
                )
            )
        except TypeError:
            started = bool(scheduled_refresh(interval_ms=F5_REFRESH_FRAME_INTERVAL_MS))
            if started:
                on_finished()
            return started
    except (AttributeError, RuntimeError, TypeError) as exc:
        log.error(f"[F5] 工作区快照分帧刷新异常: {exc}")
        return False


def _refresh_workspace_snapshots_after_f5(workspace, on_finished) -> None:
    if _try_scheduled_f5_snapshot_refresh(workspace, on_finished):
        return

    refresh_all_tabs = getattr(workspace, "refresh_all_tabs_after_f5", None)
    if callable(refresh_all_tabs):
        try:
            try:
                refresh_all_tabs(skip_cache_reload_tabs=True)
            except TypeError:
                refresh_all_tabs()
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.error(f"[F5] 刷新各 Tab 表格快照异常: {exc}")
    on_finished()


def finish_f5_reload(main_window, *, count, elapsed, event_bus):
    """完成 F5 后的缓存重载收尾，只保留 UI 壳层必须知道的结果。"""
    main_window._update_last_f5_time()
    workspace = getattr(main_window, "_workspace", None)
    _refresh_workspace_snapshots_after_f5(workspace, _post_f5_quote_refresh_callback(main_window))

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


def _shutdown_optional_service(main_window, attr_name: str, label: str, run) -> None:
    service = getattr(main_window, attr_name, None)
    shutdown = getattr(service, "shutdown", None)
    if callable(shutdown):
        run(label, shutdown)


def shutdown_main_window(main_window, *, event_bus, task_manager):
    main_window._is_closing = True
    main_window._f5_cancelled = True
    main_window._pending_f5_request = False

    def _run(label: str, action):
        try:
            clean = action()
            if clean is False:
                log.warning(f"[关闭] {label}未在时限内完成")
            return clean
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[关闭] {label}异常: {exc}")
            return False

    f5_controller = getattr(main_window, "_f5_job_controller", None)
    if f5_controller is not None:
        _run("停止F5子进程", lambda: f5_controller.shutdown(timeout_ms=2_500))
    _run("关闭K线窗口", lambda: kline_manager.shutdown())

    lifecycle = getattr(main_window, "_task_lifecycle", None)
    if lifecycle is not None:
        _run("停止窗口后台任务", lambda: lifecycle.shutdown(timeout_ms=1_500))

    _run("停止交易日历后台任务", lambda: shutdown_market_calendar_tasks(timeout_ms=750))

    _shutdown_optional_service(main_window, "startup_orchestrator", "停止启动编排器", _run)
    _shutdown_optional_service(main_window, "auto_refresh_scheduler", "停止自动刷新调度器", _run)
    _shutdown_optional_service(main_window, "asian_market_service", "stop asian market service", _run)
    _shutdown_optional_service(main_window, "earnings_refresh_service", "stop earnings refresh service", _run)
    _shutdown_optional_service(main_window, "central_quotes_svc", "停止中央报价服务", _run)

    _run("广播关闭信号", event_bus.sig_app_closing.emit)
    if main_window._workspace is not None:
        _run("停止工作区", main_window._workspace.shutdown)
    _run("保存UI状态", main_window._save_ui_state)
    _run("重置全局快照状态", global_store.reset_runtime_state)
    _run("TaskManager 关停", task_manager.shutdown)
