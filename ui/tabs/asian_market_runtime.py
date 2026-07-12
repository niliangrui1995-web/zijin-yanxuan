# -*- coding: utf-8 -*-
"""亚洲页运行时编排逻辑。"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import QTimer

from app.services.asian_market_cache_service import cache_mtime
from app.services.ui_task_lifecycle_service import task_lifecycle_for
from core.logger import get_logger
from ui.tabs.asian_market_meta import get_market_status
from ui.tabs.asian_market_workers import JSON_CACHE, AsianCacheFetcherThread

log = get_logger(__name__)


def _set_tab_status(tab, primary: str, *segments: str, freshness: str = "", next_step: str = ""):
    setter = getattr(tab, "_set_asian_status", None)
    if callable(setter):
        setter(primary, *segments, freshness=freshness, next_step=next_step)
        return
    tab.lbl_status.setText(" | ".join(part for part in (primary, *segments) if part))


def runtime_state_text(state: str) -> str:
    mapping = {
        "running": "运行",
        "paused_for_cache_sync": "暂停",
        "manual_refresh_once": "手动单次",
        "deferred": "延后",
        "degraded": "降级",
    }
    return mapping.get(state, state)


def call_worker_method(tab, method_name: str):
    worker = getattr(tab, "worker", None)
    if worker is None:
        return None

    method = getattr(worker, method_name, None)
    if not callable(method):
        return None

    try:
        return method()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[亚洲页] 调用 worker.{method_name} 失败: {exc}")
        return None


def worker_resume_auto_refresh(tab):
    return call_worker_method(tab, "resume_auto_refresh")


def worker_pause_for_cache_sync(tab):
    return call_worker_method(tab, "pause_for_cache_sync")


def worker_trigger_refresh(tab):
    return call_worker_method(tab, "trigger_refresh")


def check_auto_cache(tab):
    from app.services.ui_market_calendar_service import MarketCalendar

    if tab._is_fetching_cache or tab._pending_auto_cache_sync:
        return

    now = MarketCalendar.now("CN")
    target_dt = now.replace(hour=16, minute=30, second=0, microsecond=0)
    if now < target_dt:
        target_dt -= dt.timedelta(days=1)
    while target_dt.weekday() >= 5:
        target_dt -= dt.timedelta(days=1)

    mtime = cache_mtime(JSON_CACHE)
    cache_dt = MarketCalendar.from_timestamp(mtime, "CN") if mtime else dt.datetime.min
    cache_latest_trade_dates = (
        tab._get_cache_latest_trade_dates() if hasattr(tab, "_get_cache_latest_trade_dates") else {}
    )
    expected_latest_trade_dates = (
        tab._get_expected_latest_trade_dates() if hasattr(tab, "_get_expected_latest_trade_dates") else {}
    )

    cache_latest_trade_date = tab._get_cache_latest_trade_date()
    expected_latest_trade_date = tab._get_expected_latest_trade_date()

    stale_by_mtime = cache_dt < target_dt
    stale_by_trade_date = expected_latest_trade_date is not None and (
        cache_latest_trade_date is None or cache_latest_trade_date < expected_latest_trade_date
    )
    stale_markets = []
    for market, expected_date in expected_latest_trade_dates.items():
        cache_date = cache_latest_trade_dates.get(market)
        if expected_date is not None and (cache_date is None or cache_date < expected_date):
            stale_markets.append((market, cache_date, expected_date))
    if stale_markets:
        stale_by_trade_date = True

    if not (stale_by_mtime or stale_by_trade_date):
        return

    tab._pending_auto_cache_sync = True
    tab._cache_sync_wait_deadline = dt.datetime.now() + dt.timedelta(seconds=60)
    tab._set_runtime_state("paused_for_cache_sync")
    if hasattr(tab, "worker") and tab.worker is not None:
        worker_pause_for_cache_sync(tab)

    reason = []
    if stale_by_mtime:
        reason.append(f"修改时间早于 {target_dt.strftime('%m-%d %H:%M')}")
    if stale_by_trade_date:
        reason.append(f"缓存交易日 {cache_latest_trade_date} 早于 {expected_latest_trade_date}")
    reason_txt = "，".join(reason) if reason else "未知"
    log.info(
        f"[亚洲页] 检测到缓存过期({reason_txt})，"
        f"缓存时间={cache_dt.strftime('%m-%d %H:%M')}，"
        f"缓存交易日={cache_latest_trade_date}，"
        f"期望交易日={expected_latest_trade_date}"
    )
    _set_tab_status(tab, "检测到收盘缓存过期", "等待当前刷新轮次结束后同步")
    QTimer.singleShot(0, tab._continue_auto_cache_sync)


def continue_auto_cache_sync(tab):
    if not tab._pending_auto_cache_sync or tab._is_fetching_cache:
        return

    if getattr(tab, "cache_thread", None) is not None and tab.cache_thread.isRunning():
        return

    if hasattr(tab, "worker") and tab.worker is not None and tab.worker.isRunning():
        if not tab.worker.wait_for_cycle_idle(0.1):
            if tab._cache_sync_wait_deadline is not None and dt.datetime.now() >= tab._cache_sync_wait_deadline:
                tab._pending_auto_cache_sync = False
                tab._cache_sync_wait_deadline = None
                _set_tab_status(tab, "等待后台刷新超时", "本轮缓存同步已延后一轮")
                log.warning("[亚洲页] 等待后台轮次退出超时，本轮缓存同步延后一轮")
                return
            _set_tab_status(tab, "等待后台刷新结束", "随后开始收盘缓存同步")
            QTimer.singleShot(1000, tab._continue_auto_cache_sync)
            return

    tab._pending_auto_cache_sync = False
    tab._cache_sync_wait_deadline = None
    tab._is_fetching_cache = True
    _set_tab_status(tab, "正在同步收盘缓存", "完成后会自动重载本地 K 线")
    cancellation_token = task_lifecycle_for(tab).begin(
        "asian_cache_sync",
        timeout_sec=15 * 60.0,
    )
    thread = AsianCacheFetcherThread(cancellation_token=cancellation_token)
    thread.finished.connect(lambda: _handle_auto_cache_thread_finished(tab, thread))
    thread.finished.connect(thread.deleteLater)
    tab.cache_thread = thread
    thread.start()


def _handle_auto_cache_thread_finished(tab, thread):
    success = bool(getattr(thread, "result_success", False))
    msg = str(getattr(thread, "result_message", "") or "")
    if getattr(tab, "cache_thread", None) is thread:
        tab.cache_thread = None
    cancellation_token = getattr(thread, "cancellation_token", None)
    lifecycle = getattr(tab, "_task_lifecycle", None)
    if cancellation_token is not None and lifecycle is not None:
        if not lifecycle.complete("asian_cache_sync", cancellation_token) or cancellation_token.cancelled:
            return
    if getattr(tab, "_asian_shutting_down", False) or getattr(tab, "_runtime_cleanup_done", False):
        return
    tab._on_auto_cache_finished(success, msg)


def log_asian_health(tab):
    now_ts = dt.datetime.now().timestamp()
    if now_ts - tab._last_health_log_at < 60:
        return

    worker_running = bool(hasattr(tab, "worker") and tab.worker is not None and tab.worker.isRunning())
    cache_syncing = bool(
        tab._is_fetching_cache
        or tab._pending_auto_cache_sync
        or (getattr(tab, "cache_thread", None) is not None and tab.cache_thread.isRunning())
    )
    last_success = tab._last_asian_success_at.strftime("%Y-%m-%d %H:%M:%S") if tab._last_asian_success_at else "-"
    health_signature = (
        tab._runtime_state_text(),
        last_success,
        cache_syncing,
        worker_running,
        len(getattr(tab, "row_data", []) or []),
    )
    should_log = tab._is_quote_refresh_open()
    if not should_log:
        signature_changed = health_signature != tab._last_health_signature
        interval_reached = (now_ts - tab._last_health_log_at) >= 1800
        should_log = signature_changed or interval_reached

    if not should_log:
        return

    log.info(
        f"[亚洲页] 健康 状态={tab._runtime_state_text()} | "
        f"上次成功={last_success} | 缓存同步中={cache_syncing} | "
        f"后台运行中={worker_running} | 行数={len(getattr(tab, 'row_data', []) or [])}"
    )
    tab._last_health_signature = health_signature
    tab._last_health_log_at = now_ts


def on_minute_tick(tab):
    tab._refresh_market_status_rows()
    if not tab._is_fetching_cache and not tab._pending_auto_cache_sync:
        if tab._is_quote_refresh_open():
            if tab._asian_runtime_state != "manual_refresh_once":
                tab._set_runtime_state("running")
                if hasattr(tab, "worker") and tab.worker is not None:
                    worker_resume_auto_refresh(tab)
        else:
            tab._set_runtime_state("paused_for_cache_sync")
            if hasattr(tab, "worker") and tab.worker is not None:
                worker_pause_for_cache_sync(tab)
    tab._check_auto_cache()
    tab._log_asian_health()


def refresh_market_status_rows(tab, status_provider=None):
    rows = list(getattr(tab.model, "row_data", []) or [])
    if not rows:
        return

    status_provider = status_provider or get_market_status

    try:
        status_col = tab.model.headers.index("状态")
    except ValueError:
        return

    changed_rows = []
    for row_idx, row_dict in enumerate(rows):
        code = str(row_dict.get("代码", "")).strip()
        if not code:
            continue
        market = code.split(".")[-1] if "." in code else ""
        new_status = status_provider(market)
        if row_dict.get("状态") != new_status:
            row_dict["状态"] = new_status
            changed_rows.append(row_idx)

    for row_idx in changed_rows:
        tab.model.dataChanged.emit(
            tab.model.index(row_idx, status_col),
            tab.model.index(row_idx, status_col),
        )


def on_auto_cache_finished(tab, success, msg):
    tab._is_fetching_cache = False
    if success:
        tab._set_runtime_state("paused_for_cache_sync")
        if hasattr(tab, "worker") and tab.worker is not None:
            worker_pause_for_cache_sync(tab)
        tab._load_local_cache()
        tab._last_asian_success_at = dt.datetime.now()
        if "已保留现有缓存" in str(msg or ""):
            _set_tab_status(
                tab,
                "已保留本地缓存",
                "远端拉取失败，本次继续沿用上次成功结果",
                freshness="远端失败沿用",
                next_step="等待下一轮缓存同步",
            )
            log.warning(f"[亚洲页] 远端拉取失败，已保留旧缓存: {msg}")
        else:
            _set_tab_status(tab, "收盘缓存同步完成", "已重载本地 K 线", freshness="本地缓存")
            log.info("[亚洲页] 16:30 缓存同步完成，已重载本地 K 线，当前保持静默")
        return

    _set_tab_status(tab, "收盘缓存同步失败", msg or "请稍后重试", freshness="远端失败沿用", next_step="请稍后重试")
    log.warning(f"[亚洲页] 收盘缓存同步失败: {msg}")


def on_asian_klines_ready(tab):
    tab._load_local_cache()
    tab._last_asian_success_at = dt.datetime.now()
    _set_tab_status(tab, "本地缓存已更新", "K 线数据已重载", freshness="本地缓存")
