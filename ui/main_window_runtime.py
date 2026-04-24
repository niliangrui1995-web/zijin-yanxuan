# -*- coding: utf-8 -*-
from __future__ import annotations

import gc

from PyQt6.QtCore import QTimer

from core.global_store import global_store
from core.logger import get_logger
from app.services.ui_runtime_service import WINDOW_F5_PRECOMPUTE

log = get_logger(__name__)


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


def start_f5_precompute(main_window, *, task_manager):
    from core.rps_precomputer import RPSPrecomputer

    def _set_status_cb(message: str):
        main_window._call_in_ui(
            lambda: (
                hasattr(main_window, "lbl_status") and main_window.lbl_status.setText(message),
                hasattr(main_window, "_set_titlebar_sync_state")
                and main_window._set_titlebar_sync_state("working", str(message or "").strip()),
            )
        )

    def _done_cb(count, elapsed):
        main_window._call_in_ui(lambda: main_window._on_f5_done(count, elapsed))

    task_manager.run_in_background(
        lambda: RPSPrecomputer.run_f5_pipeline(
            data_provider=main_window.data_provider,
            engine=main_window.engine,
            cancelled_checker=lambda: getattr(main_window, "_f5_cancelled", False),
            set_status_callback=_set_status_cb,
            done_callback=_done_cb,
        ),
        task_id=WINDOW_F5_PRECOMPUTE,
    )


def finish_f5_reload(main_window, *, count, elapsed, event_bus):
    """完成 F5 后的缓存重载收尾，只保留 UI 壳层必须知道的结果。"""
    QTimer.singleShot(2000, lambda: gc.collect())
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
            scheduled_refresh_started = bool(refresh_all_tabs_after_f5_scheduled(interval_ms=0))
        except (AttributeError, RuntimeError, TypeError) as exc:
            scheduled_refresh_started = False
            log.error(f"[F5] 工作区快照分帧刷新异常: {exc}")

    refresh_all_tabs_after_f5 = getattr(workspace, "refresh_all_tabs_after_f5", None)
    if not scheduled_refresh_started and callable(refresh_all_tabs_after_f5):
        try:
            refresh_all_tabs_after_f5()
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.error(f"[F5] 刷新各 Tab 表格快照异常: {exc}")

    try:
        event_bus.sig_cache_reload_completed.emit()
    except (AttributeError, RuntimeError, TypeError) as exc:
        log.error(f"[F5] 广播缓存重载完成信号异常: {exc}")

    refresh_information_sources_after_f5 = getattr(workspace, "refresh_information_sources_after_f5", None)
    if callable(refresh_information_sources_after_f5):
        try:
            refresh_information_sources_after_f5()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[F5] 情报源自动刷新异常: {exc}")
    else:
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

    if hasattr(main_window, "startup_orchestrator"):
        _run("停止启动编排器", main_window.startup_orchestrator.shutdown)

    if hasattr(main_window, "central_quotes_svc"):
        _run("停止中央报价服务", main_window.central_quotes_svc.shutdown)

    if main_window._workspace is not None:
        _run("停止工作区", main_window._workspace.shutdown)

    _run("保存UI状态", main_window._save_ui_state)

    workspace = getattr(main_window, "_workspace", None)
    get_rt_table = getattr(workspace, "get_rt_table", None)
    rt_table = get_rt_table() if callable(get_rt_table) else None
    if rt_table is not None:
        _run("保存盘中缓存", lambda: main_window.cache_manager.save_rt_cache(rt_table))

    _run("广播关闭信号", event_bus.sig_app_closing.emit)
    _run("重置全局快照状态", global_store.reset_runtime_state)
    _run("TaskManager 关停", task_manager.shutdown)
