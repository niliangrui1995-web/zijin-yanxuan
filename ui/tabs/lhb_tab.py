# -*- coding: utf-8 -*-
"""
ui/tabs/lhb_tab.py
龙虎榜 · 30 日关注池 Tab

替代旧的"单日视图"，改为滚动 30 个交易日的关注池：
- 入池条件：30 日内至少有一天同时满足 上榜净买额>0 且 机构净买>0
- 展示每只合格标的的最近一次上榜详情 + 30 日内满足条件天数
- 每天 20:00 后自动抓取当天龙虎榜数据并刷新池
- 首次使用自动回填缺失的历史交易日数据
"""

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.services.scan_runtime_service import create_scan_engine
from app.services.tab_data_lineage_service import TabDataLineageService
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_industry_chain_service import load_cached_ai_industry_chain_context_map, normalize_ai_chain_code
from app.services.ui_lhb_pool_service import POOL_WINDOW, LhbPoolManager
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import (
    TaskCancelledError,
    TaskDeadlineExceeded,
    TaskLifecycleGroup,
)
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)
POST_F5_POOL_BOOTSTRAP_DEFER_MS = 5000
LHB_POOL_UPDATE_DEBOUNCE_MS = 1200
LHB_POOL_BOOTSTRAP_TIMEOUT_SECONDS = 3 * 60
LHB_POOL_BACKFILL_TIMEOUT_SECONDS = 15 * 60
LHB_TASK_SHUTDOWN_WAIT_TIMEOUT_MS = 750


def _task_lifecycle_for(owner) -> TaskLifecycleGroup:
    lifecycle = getattr(owner, "_task_lifecycle", None)
    if lifecycle is None:
        lifecycle = TaskLifecycleGroup(task_manager)
        owner._task_lifecycle = lifecycle
    return lifecycle


def _load_lhb_pool_payload(owner, cancellation_token) -> dict:
    cancellation_token.raise_if_cancelled()
    trade_dates = owner._get_lhb_trade_dates()
    cancellation_token.raise_if_cancelled()
    if not trade_dates:
        return {"status": "calendar_missing"}
    pool_manager = LhbPoolManager()
    pool_manager.prune(trade_dates)
    cancellation_token.raise_if_cancelled()
    pool = pool_manager.compute_pool(data_provider=owner.data_provider, engine=owner._get_engine())
    cancellation_token.raise_if_cancelled()
    ai_chain_context_map = owner._load_ai_chain_context_map()
    row_data = owner._build_pool_display_rows(pool, ai_chain_context_map)
    validation_ref_date = max(trade_dates)
    return {
        "status": "ok",
        "pool_manager": pool_manager,
        "pool": pool,
        "row_data": row_data,
        "ai_chain_context_map": ai_chain_context_map,
        "missing": pool_manager.get_missing_dates(trade_dates),
        "pending_validation": pool_manager.get_dates_pending_validation(trade_dates, validation_ref_date),
        "validation_ref_date": validation_ref_date,
    }


def _wait_lhb_backfill_step(cancellation_token, step: int, total: int) -> None:
    if step < total and cancellation_token.wait(0.8):
        cancellation_token.raise_if_cancelled()


def _as_lhb_fetch_payload(value) -> dict:
    if isinstance(value, dict):
        return value
    records = list(value or [])
    return {"records": records, "count": len(records), "status": "ok", "message": ""}


def _fetch_missing_lhb_dates(owner, dates, total: int, log_emit, cancellation_token):
    from app.services.lhb_market_data_service import fetch_lhb_pool_for_date

    fetched: dict[str, dict] = {}
    step = 0
    for date_str in dates:
        cancellation_token.raise_if_cancelled()
        step += 1
        try:
            payload = _as_lhb_fetch_payload(
                fetch_lhb_pool_for_date(
                    date_str,
                    emit_success_log=False,
                    return_meta=True,
                    cancellation_token=cancellation_token,
                )
            )
            if str(payload.get("status", "ok") or "ok") != "error":
                fetched[date_str] = {"records": payload.get("records", []), "meta": None}
            level, message = owner._build_backfill_progress_log(step, total, date_str, payload)
            log_emit(level, message)
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] 回填 {date_str} 失败: {exc}")
            log_emit("warn", f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 抓取失败: {exc}")
        _wait_lhb_backfill_step(cancellation_token, step, total)
    return fetched, step


def _probe_validation_message(date_str, step, total, cached_count, probe_payload) -> tuple[str, str]:
    status = str(probe_payload.get("status", "ok") or "ok")
    if status == "ok":
        return "info", f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 校验通过 | {cached_count}条"
    if status == "empty":
        return "warn", f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 源头暂为空，保留缓存{cached_count}条"
    return "warn", f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 校验异常，保留缓存{cached_count}条"


def _validate_lhb_date(owner, pool_manager, date_str, validation_ref_date, step, total, cancellation_token):
    from app.services.lhb_market_data_service import fetch_lhb_pool_for_date, probe_lhb_detail_count_for_date

    cached_count = pool_manager.get_cached_record_count(date_str)
    probe_payload = _as_lhb_fetch_payload(
        probe_lhb_detail_count_for_date(
            date_str,
            return_meta=True,
            cancellation_token=cancellation_token,
        )
    )
    if not owner._should_refresh_after_probe(cached_count, probe_payload):
        level, message = _probe_validation_message(date_str, step, total, cached_count, probe_payload)
        return None, probe_payload, level, message

    refresh_payload = _as_lhb_fetch_payload(
        fetch_lhb_pool_for_date(
            date_str,
            emit_success_log=False,
            return_meta=True,
            cancellation_token=cancellation_token,
        )
    )
    if str(refresh_payload.get("status", "ok") or "ok") == "error":
        validated = {"count": probe_payload.get("count", cached_count), "status": "repair_failed"}
        message = f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 校验发现条数差异，但补刷失败，暂保留缓存{cached_count}条"
        return None, validated, "warn", message
    source_count = int(probe_payload.get("count", refresh_payload.get("count", 0)) or 0)
    fetched = {
        "records": refresh_payload.get("records", []),
        "meta": {"source_count": source_count, "last_probe_ref_date": validation_ref_date, "probe_status": "ok"},
    }
    message = f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 校验发现缓存脏数据 | 缓存{cached_count}条 -> 源头{source_count}条，已自动补刷"
    return fetched, None, "warn", message


def _validate_lhb_dates(owner, pool_manager, dates, validation_ref_date, step, total, log_emit, cancellation_token):
    fetched: dict[str, dict] = {}
    validated: dict[str, dict] = {}
    for date_str in dates:
        cancellation_token.raise_if_cancelled()
        step += 1
        try:
            repaired, probe, level, message = _validate_lhb_date(
                owner, pool_manager, date_str, validation_ref_date, step, total, cancellation_token
            )
            if repaired is not None:
                fetched[date_str] = repaired
            if probe is not None:
                validated[date_str] = probe
            log_emit(level, message)
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] 校验 {date_str} 失败: {exc}")
            log_emit("warn", f"[龙虎榜池] [{step:02d}/{total:02d}] {date_str} 校验失败: {exc}")
        _wait_lhb_backfill_step(cancellation_token, step, total)
    return fetched, validated


def _merge_lhb_backfill_state(pool_manager, fetched, validated, validation_ref_date, cancellation_token) -> None:
    for date_str, payload in fetched.items():
        cancellation_token.raise_if_cancelled()
        pool_manager.add_day(date_str, payload.get("records", []), meta=payload.get("meta"))
    for date_str, payload in validated.items():
        cancellation_token.raise_if_cancelled()
        pool_manager.mark_day_probe(
            date_str,
            source_count=payload.get("count", 0),
            validation_ref_date=validation_ref_date,
            status=payload.get("status", "ok"),
        )


def _build_lhb_backfill_payload(owner, missing, validation, validation_ref_date, log_emit, cancellation_token):
    total = len(missing) + len(validation)
    cancellation_token.raise_if_cancelled()
    pool_manager = LhbPoolManager()
    fetched, step = _fetch_missing_lhb_dates(owner, missing, total, log_emit, cancellation_token)
    repaired, validated = _validate_lhb_dates(
        owner, pool_manager, validation, validation_ref_date, step, total, log_emit, cancellation_token
    )
    fetched.update(repaired)
    _merge_lhb_backfill_state(pool_manager, fetched, validated, validation_ref_date, cancellation_token)
    cancellation_token.raise_if_cancelled()
    pool_manager.save()
    pool = pool_manager.compute_pool(data_provider=owner.data_provider, engine=owner._get_engine())
    cancellation_token.raise_if_cancelled()
    ai_chain_context_map = owner._load_ai_chain_context_map()
    return {
        "fetched": fetched,
        "validated": validated,
        "pool_manager": pool_manager,
        "pool": pool,
        "row_data": owner._build_pool_display_rows(pool, ai_chain_context_map),
        "ai_chain_context_map": ai_chain_context_map,
    }


class LhbTab(BaseStockTab):
    """龙虎榜 30 日关注池 Tab"""

    AI_CHAIN_CONTEXT_COLUMN = "AI细分板块/备注"
    QUOTE_APPLY_DEBOUNCE_MS = 80
    QUOTE_SORT_DEBOUNCE_MS = 120
    OPENING_WARMUP_QUOTE_APPLY_CHUNK_SIZE = 20
    OPENING_WARMUP_QUOTE_APPLY_CONTINUE_MS = 16
    OPENING_WARMUP_STATUSES = frozenset(
        {
            "\u5f00\u76d8\u96c6\u5408\u7ade\u4ef7",
            "\u5f00\u5e02\u524d\u65f6\u6bb5",
        }
    )
    _DISPLAY_PLACEHOLDER = "--"
    _chain_context_provider = staticmethod(load_cached_ai_industry_chain_context_map)

    def __init__(
        self,
        data_provider,
        parent=None,
        autoload_pool: bool = True,
        initial_load_delay_ms: int = 0,
    ):
        super().__init__(data_provider=data_provider, parent=parent)

        try:
            self._initial_load_delay_ms = max(0, int(initial_load_delay_ms))
        except (TypeError, ValueError):
            self._initial_load_delay_ms = 0
        self._autoload_pool = bool(autoload_pool)
        self._pool_bootstrap_started = False
        self._pool_load_in_progress = False
        self.pool_manager = None
        self._backfill_in_progress = False
        # 记录今天是否已经自动抓取过，避免重复拉取
        self._today_auto_fetched = False
        # 交易日历加载重试计数器，防止网络永久断开时无限重试
        self._calendar_retry_count = 0
        self._status_primary = "加载中..."
        self._status_segments = ()
        self._status_freshness = ""
        self._status_next_step = ""
        self._lhb_lineage_service = TabDataLineageService(
            key="lhb",
            source="LhbPoolManager cache + local_quote_snapshot",
            provider="LhbPoolManager/scan_runtime_service",
            cache_refs=(
                "data/Cache/lhb_pool_30d.json",
                "global_store.quotes",
                "local_tdx_cache",
            ),
            provider_status_reader=self._read_provider_status,
        )
        self._last_lhb_result = None
        self._last_lhb_signature = ""
        self._ai_chain_context_map: dict[str, str] | None = None
        self._handling_lhb_pool_update = False
        self._pending_pool_refresh = False
        self._pool_bootstrap_not_before = 0.0
        self._post_f5_pool_defer_until = 0.0
        self._post_f5_pool_pending = False
        self._post_f5_pool_emit_event = False
        self._pool_update_refresh_timer = QTimer(self)
        self._pool_update_refresh_timer.setSingleShot(True)
        self._pool_update_refresh_timer.timeout.connect(self._run_pending_pool_refresh)
        self._pool_retry_timer = QTimer(self)
        self._pool_retry_timer.setSingleShot(True)
        self._pool_retry_timer.timeout.connect(self._load_and_display_pool)
        self._pending_quote_snapshot: dict = {}
        self._applying_pending_quote_snapshot = False
        self._quote_apply_timer = QTimer(self)
        self._quote_apply_timer.setSingleShot(True)
        self._quote_apply_timer.timeout.connect(self._flush_pending_quote_snapshot)
        self._quote_sort_timer = QTimer(self)
        self._quote_sort_timer.setSingleShot(True)
        self._quote_sort_timer.timeout.connect(self._sort_model_for_default_lhb_order)

        self._init_ui()
        if self._autoload_pool:
            self._ensure_pool_bootstrap_started()
        else:
            self.table_state.show_loading("龙虎榜待加载", "首次进入时自动读取本地缓存")
            self._set_pool_status("等待进入龙虎榜", freshness="未加载", next_step="首次进入时自动读取缓存")

        # 订阅中央行情站实时报价 + 大一统市值更新
        self.subscribe_global_quotes()

        # 订阅全局缓存异步加载完成事件：
        # RPS 缓存是由后台线程在 2.5 秒后注入 engine 的。
        self._rps_injected_flag = False
        event_bus.sig_cache_bootstrap_ready.connect(self._on_cache_bootstrap_ready)
        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)
        event_bus.sig_lhb_pool_updated.connect(self._on_lhb_pool_updated)

    def showEvent(self, event):
        super().showEvent(event)
        if self._should_start_pool_on_show():
            if self._pending_pool_refresh and self._pool_bootstrap_started:
                self._pending_pool_refresh = False
                self._load_and_display_pool(emit_event=False)
            else:
                self._pending_pool_refresh = False
                self._ensure_pool_bootstrap_started(delay_ms=self._initial_load_delay_ms)

    def hideEvent(self, event):
        super().hideEvent(event)

    def prime_background_load(self):
        self._ensure_pool_bootstrap_started(delay_ms=0)

    def on_workspace_tab_activated(self) -> None:
        self._ensure_pool_bootstrap_started(delay_ms=self._initial_load_delay_ms)

    def prepare_post_f5_refresh(self) -> None:
        self._post_f5_pool_defer_until = max(
            float(getattr(self, "_post_f5_pool_defer_until", 0.0) or 0.0),
            time.monotonic() + POST_F5_POOL_BOOTSTRAP_DEFER_MS / 1000.0,
        )

    def _is_current_workspace_tab(self) -> bool:
        parent = self.parent()
        tabs = getattr(parent, "tabs", None)
        current_widget = getattr(tabs, "currentWidget", None)
        if not callable(current_widget):
            return True
        try:
            return current_widget() is self
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return True

    def _should_start_pool_on_show(self) -> bool:
        return BaseStockTab._should_start_interactive_runtime_on_show(self)

    def _ensure_pool_bootstrap_started(self, *, delay_ms: int | None = None):
        if self._pool_bootstrap_started:
            return
        self._pool_bootstrap_started = True
        if delay_ms is None:
            delay_ms = self._initial_load_delay_ms
        try:
            delay = max(0, int(delay_ms))
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            self._pool_bootstrap_not_before = max(
                float(getattr(self, "_pool_bootstrap_not_before", 0.0) or 0.0),
                time.monotonic() + delay / 1000.0,
            )
            QTimer.singleShot(delay, self._load_and_display_pool)
        else:
            self._pool_bootstrap_not_before = 0.0
            self._load_and_display_pool()

    def _on_lhb_pool_updated(self) -> None:
        if self._handling_lhb_pool_update:
            return
        if not self._pool_bootstrap_started:
            return
        try:
            is_visible = bool(self.isVisible())
        except RuntimeError:
            is_visible = False
        if not is_visible or not self._is_current_workspace_tab():
            self._pending_pool_refresh = True
            return
        self._schedule_pending_pool_refresh()

    def _schedule_pending_pool_refresh(self, *, delay_ms: int | None = None) -> bool:
        self._pending_pool_refresh = True
        if getattr(self, "_pool_load_in_progress", False):
            return False
        timer = getattr(self, "_pool_update_refresh_timer", None)
        if timer is None:
            return self._run_pending_pool_refresh()
        if timer.isActive():
            return False
        try:
            delay = int(LHB_POOL_UPDATE_DEBOUNCE_MS if delay_ms is None else delay_ms)
        except (TypeError, ValueError):
            delay = LHB_POOL_UPDATE_DEBOUNCE_MS
        timer.start(max(0, delay))
        return True

    def _run_pending_pool_refresh(self) -> bool:
        if not getattr(self, "_pending_pool_refresh", False):
            return False
        try:
            is_visible = bool(self.isVisible())
        except RuntimeError:
            is_visible = False
        if not is_visible or not self._is_current_workspace_tab():
            return False
        self._pending_pool_refresh = False
        self._handling_lhb_pool_update = True
        try:
            self._load_and_display_pool(emit_event=False)
        finally:
            self._handling_lhb_pool_update = False

    def _on_cache_bootstrap_ready(self):
        """处理延迟的 RPS 数据加载，仅执行一次避免和自身发出的同名信号造成无限死循环"""
        if self._rps_injected_flag:
            return
        self._rps_injected_flag = True
        if not self._pool_bootstrap_started:
            return
        self._load_and_display_pool()

    def _on_cache_reload_completed(self):
        if not self._pool_bootstrap_started:
            return
        self._load_and_display_pool()

    def refresh_data_after_ai_industry_chain_update(self) -> bool:
        self._ai_chain_context_map = None
        if not self._pool_bootstrap_started:
            return False
        self._load_and_display_pool()
        return True

    @staticmethod
    def _get_engine():
        """懒加载获取 VCPEngine 单例，用于读取 F5 预算的 RPS250 缓存"""
        try:
            return create_scan_engine()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return None

    def _get_pool_manager(self) -> LhbPoolManager:
        if self.pool_manager is None:
            self.pool_manager = LhbPoolManager()
        return self.pool_manager

    @classmethod
    def _load_ai_chain_context_map(cls) -> dict[str, str]:
        try:
            return dict(cls._chain_context_provider() or {})
        except (FileNotFoundError, RuntimeError, OSError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] AI产业链细分板块数据加载失败: {exc}")
            return {}

    @staticmethod
    def _record_stock_code(record: dict) -> str:
        if not isinstance(record, dict):
            return ""
        return normalize_ai_chain_code(
            record.get("代码")
            or record.get("股票代码")
            or record.get("证券代码")
            or record.get("stock_code")
            or record.get("code")
        )

    @classmethod
    def _context_text_for_code(cls, stock_code: str, context_map: dict[str, str] | None) -> str:
        code = normalize_ai_chain_code(stock_code)
        if not code:
            return cls._DISPLAY_PLACEHOLDER
        return str((context_map or {}).get(code) or "").strip() or cls._DISPLAY_PLACEHOLDER

    def _get_ai_chain_context_map(self) -> dict[str, str]:
        if self._ai_chain_context_map is None:
            self._ai_chain_context_map = self._load_ai_chain_context_map()
        return self._ai_chain_context_map or {}

    def _get_ai_chain_context_text(self, stock_code: str) -> str:
        return self._context_text_for_code(stock_code, self._get_ai_chain_context_map())

    @staticmethod
    def _ensure_log_line(message: str) -> str:
        text = str(message or "")
        return text if text.endswith("\n") else text + "\n"

    def _latest_cached_trade_date(self) -> str:
        cached_dates = self._get_pool_manager().get_cached_dates() or []
        return max(cached_dates) if cached_dates else ""

    def _latest_loaded_cached_trade_date(self) -> str:
        manager = getattr(self, "pool_manager", None)
        getter = getattr(manager, "get_cached_dates", None)
        if not callable(getter):
            return ""
        try:
            cached_dates = getter() or []
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""
        return max(cached_dates) if cached_dates else ""

    def _cached_pool_day_count(self) -> int:
        manager = getattr(self, "pool_manager", None)
        getter = getattr(manager, "get_cached_dates", None)
        if not callable(getter):
            return 0
        try:
            return len(getter() or [])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0

    def _lhb_lineage_status(self, rows: list[dict]) -> str:
        if self._backfill_in_progress:
            return "syncing"
        if self._pool_load_in_progress:
            return "loading"
        if rows:
            return "loaded"
        if not self._pool_bootstrap_started:
            return "deferred"
        return "empty"

    def _describe_lhb_rows(self, rows: list[dict]):
        warnings = []
        status = self._lhb_lineage_status(rows)
        if not rows:
            warnings.append("lhb_rows_deferred" if status == "deferred" else "lhb_rows_empty")
        return self._lhb_lineage_service.describe(
            rows,
            trade_date=self._latest_loaded_cached_trade_date(),
            triggered_network=bool(self._backfill_in_progress),
            warnings=warnings,
            extra={
                "status": status,
                "pool_bootstrap_started": self._pool_bootstrap_started,
                "pool_load_in_progress": self._pool_load_in_progress,
                "backfill_in_progress": self._backfill_in_progress,
                "cached_trade_days": self._cached_pool_day_count(),
                "pool_window_days": POOL_WINDOW,
                "last_table_freshness": self._status_freshness,
                "status_primary": self._status_primary,
            },
        )

    def _refresh_lhb_lineage(self, rows: list[dict] | None = None):
        row_list = list(rows if rows is not None else self.get_row_data(current_model=getattr(self, "model", None)))
        result = self._describe_lhb_rows(row_list)
        self._last_lhb_result = result
        self._last_lhb_signature = result.signature
        return result

    def get_data_lineage(self) -> dict:
        result = self._last_lhb_result
        if result is None:
            result = self._refresh_lhb_lineage()
        return result.lineage.as_dict()

    @classmethod
    def _build_backfill_progress_log(cls, index: int, total: int, date_str: str, payload: dict) -> tuple[str, str]:
        count = int(payload.get("count", 0) or 0)
        status = str(payload.get("status", "ok") or "ok")
        prefix = f"[龙虎榜池] [{index:02d}/{total:02d}] {date_str}"
        if status == "error":
            return "warn", f"{prefix} 抓取异常 | 已记{count}条"
        if status == "empty":
            return "info", f"{prefix} 无可用数据"
        return "info", f"{prefix} 完成 | {count}条"

    @staticmethod
    def _should_refresh_after_probe(cached_count: int, probe_payload: dict) -> bool:
        """探针成功且条数不一致时，说明当天缓存已经脏了，需要定点补刷。"""
        status = str(probe_payload.get("status", "") or "")
        if status != "ok":
            return False
        source_count = int(probe_payload.get("count", 0) or 0)
        return int(cached_count or 0) != source_count

    def _set_pool_status(
        self,
        primary: str,
        *segments: str,
        freshness: str = "",
        next_step: str = "",
    ):
        self._status_primary = str(primary or "").strip() or "龙虎榜已就绪"
        self._status_segments = tuple(str(segment or "").strip() for segment in segments if str(segment or "").strip())
        self._status_freshness = str(freshness or "").strip()
        self._status_next_step = str(next_step or "").strip()
        if hasattr(self, "_last_lhb_result"):
            self._last_lhb_result = None
        self._refresh_pool_status()

    def _refresh_pool_status(self):
        total = len(getattr(self.model, "row_data", []) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        search_text = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        latest_date = ""
        if self._pool_bootstrap_started or self.pool_manager is not None:
            latest_date = self._latest_cached_trade_date()
        freshness = self._status_freshness or (f"快照 {latest_date}" if latest_date else "待回补")
        next_step = self._status_next_step or ""
        self.lbl_status.setText(
            self.format_workspace_status(
                self._status_primary,
                result=f"{visible}/{total}只" if total else "0只",
                freshness=freshness,
                current_filter=search_text or "全部",
                next_step=next_step,
                extra_segments=self._status_segments,
            )
        )

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("加载中...")

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._filter_table)

        filter_widgets = [self.search_box]

        self.btn_refresh = QPushButton("历史回补")
        self.btn_refresh.setObjectName("primaryButton")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._manual_refresh)

        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("龙虎榜", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格列配置
        self.columns = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "买点",
            "上榜次数",
            "最近上榜",
            "上榜净买额(万)",
            "机构净买(万)",
            "外资净买入",
            "换手率%",
            self.AI_CHAIN_CONTEXT_COLUMN,
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无龙虎榜数据", loading_title="加载中...")

        # 列宽配置
        self.apply_table_column_preset(
            self.table,
            [64, 76, 72, 72, 88, 84, 82, 92, 118, 118, 106, 92, 220],
            stretch_last=True,
        )

        # 持久化表头（v9: 外资净买入列摘要+tooltip重构版）
        restored_sort = self.bind_header_persistence(self.table, "lhb_header_state_v9")
        self._clear_proxy_sort_for_default_lhb_order()
        if restored_sort:
            QTimer.singleShot(0, self._clear_proxy_sort_for_default_lhb_order)

        # 交互：双击查看 K 线，右键菜单
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    # ================================================================
    # 池加载与展示
    # ================================================================
    def _load_and_display_pool_sync(self):
        """启动时执行：用缓存计算池 → 展示 → 检查缺失天数 → 后台回填"""
        trade_dates = self._get_lhb_trade_dates()
        if not trade_dates:
            self._calendar_retry_count += 1
            if self._calendar_retry_count <= 3:
                self._set_pool_status("交易日历未就绪", f"第{self._calendar_retry_count}次重试")
                self._schedule_pool_retry()
            else:
                self._set_pool_status("交易日历加载失败", freshness="待回补", next_step="点击历史回补重新抓取")
            return
        self._calendar_retry_count = 0

        # 裁剪掉超出窗口的历史数据
        pool_manager = self._get_pool_manager()
        pool_manager.prune(trade_dates)

        # 先用现有缓存展示
        pool = pool_manager.compute_pool(data_provider=self.data_provider, engine=self._get_engine())
        if pool:
            self._display_pool(pool)

        validation_ref_date = max(trade_dates)
        pending_validation = pool_manager.get_dates_pending_validation(trade_dates, validation_ref_date)

        # 检查缺失天数和脏缓存，有问题就后台回填/纠偏
        missing = pool_manager.get_missing_dates(trade_dates)
        if missing or pending_validation:
            self._start_backfill(missing, pending_validation, validation_ref_date)
        elif not pool:
            self._set_pool_status("暂无龙虎榜数据", freshness="待回补", next_step="点击历史回补开始抓取")
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无龙虎榜数据")

    def _load_and_display_pool(self, *, emit_event: bool = True):
        """Schedule the cached pool computation off the UI thread."""
        defer_until = max(
            float(getattr(self, "_pool_bootstrap_not_before", 0.0) or 0.0),
            float(getattr(self, "_post_f5_pool_defer_until", 0.0) or 0.0),
        )
        if time.monotonic() < defer_until:
            self._schedule_post_f5_pool_load(emit_event=emit_event)
            return
        self._pool_bootstrap_not_before = 0.0
        if self._pool_load_in_progress:
            return
        task_id = task_registry.workspace("lhb_pool_bootstrap").task_id
        is_active_task = getattr(task_manager, "is_active_task", None)
        if callable(is_active_task) and is_active_task(task_id):
            return
        self._pool_load_in_progress = True
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在加载龙虎榜池", "首次进入先响应，缓存池在后台计算。")
        self._set_pool_status("正在加载龙虎榜池", freshness="后台计算", next_step="结果完成后自动落表")

        def _on_pool_loaded(payload):
            self._pool_load_in_progress = False
            status = str((payload or {}).get("status", "") or "")
            if status != "ok":
                self._calendar_retry_count += 1
                if self._calendar_retry_count <= 3:
                    self._set_pool_status("交易日历未就绪", f"第{self._calendar_retry_count}次重试")
                    self._schedule_pool_retry()
                else:
                    self._set_pool_status("交易日历加载失败", freshness="待回补", next_step="点击历史回补重新抓取")
                return

            self._calendar_retry_count = 0
            pool_manager = payload.get("pool_manager")
            if pool_manager is not None:
                self.pool_manager = pool_manager
            ai_chain_context_map = payload.get("ai_chain_context_map")
            if isinstance(ai_chain_context_map, dict):
                self._ai_chain_context_map = ai_chain_context_map

            pool = list(payload.get("pool") or [])
            row_data = list(payload.get("row_data") or [])
            if pool:
                self._display_pool(pool, emit_event=emit_event, row_data=row_data)

            missing = list(payload.get("missing") or [])
            pending_validation = list(payload.get("pending_validation") or [])
            validation_ref_date = str(payload.get("validation_ref_date") or "")
            if missing or pending_validation:
                self._start_backfill(missing, pending_validation, validation_ref_date)
            elif not pool:
                self._set_pool_status("暂无龙虎榜数据", freshness="待回补", next_step="点击历史回补开始抓取")
                if hasattr(self, "table_state"):
                    self.table_state.show_empty("暂无龙虎榜数据")

            if self._pending_pool_refresh:
                self._schedule_pending_pool_refresh()

        def _on_pool_error(error_message: str):
            self._pool_load_in_progress = False
            self._pool_bootstrap_started = False
            self._set_pool_status(
                "龙虎榜池加载失败",
                error_message,
                freshness="待重试",
                next_step="重新进入或点击历史回补",
            )
            if hasattr(self, "table_state"):
                self.table_state.show_error(
                    "龙虎榜池加载失败",
                    str(error_message or ""),
                    action_text="重试",
                    action_callback=self._ensure_pool_bootstrap_started,
                )

        _task_lifecycle_for(self).run_background(
            "pool_bootstrap",
            lambda token: _load_lhb_pool_payload(self, token),
            on_success=_on_pool_loaded,
            on_error=_on_pool_error,
            task_id=task_id,
            timeout_sec=LHB_POOL_BOOTSTRAP_TIMEOUT_SECONDS,
        )

    def _schedule_post_f5_pool_load(self, *, emit_event: bool = True) -> bool:
        self._post_f5_pool_emit_event = bool(getattr(self, "_post_f5_pool_emit_event", False) or emit_event)
        if getattr(self, "_post_f5_pool_pending", False):
            return False
        self._post_f5_pool_pending = True
        defer_until = max(
            float(getattr(self, "_pool_bootstrap_not_before", 0.0) or 0.0),
            float(getattr(self, "_post_f5_pool_defer_until", 0.0) or 0.0),
        )
        delay_ms = max(0, int((defer_until - time.monotonic()) * 1000))
        QTimer.singleShot(delay_ms, self._run_post_f5_pool_load)
        return True

    def _run_post_f5_pool_load(self) -> bool:
        defer_until = max(
            float(getattr(self, "_pool_bootstrap_not_before", 0.0) or 0.0),
            float(getattr(self, "_post_f5_pool_defer_until", 0.0) or 0.0),
        )
        if time.monotonic() < defer_until:
            self._post_f5_pool_pending = False
            return self._schedule_post_f5_pool_load(
                emit_event=bool(getattr(self, "_post_f5_pool_emit_event", True))
            )
        self._post_f5_pool_pending = False
        self._pool_bootstrap_not_before = 0.0
        self._post_f5_pool_defer_until = 0.0
        emit_event = bool(getattr(self, "_post_f5_pool_emit_event", True))
        self._post_f5_pool_emit_event = False
        self._load_and_display_pool(emit_event=emit_event)
        return True

    @staticmethod
    def _get_lhb_reference_trade_date():
        """龙虎榜手动/启动回填的参考交易日。

        龙虎榜当日数据通常在 20:00 后才稳定可抓。交易日但 20:00 前应回退到上一交易日，
        否则会把“尚未发布的今天”计入 30 日窗口，导致只能拿到前 29 个有效交易日。
        """
        from datetime import timedelta

        now_cn = MarketCalendar._get_market_now("CN")
        today = now_cn.date()
        latest = MarketCalendar.get_latest_trade_date("CN", ref_date=today)
        if latest is None:
            return None

        if not MarketCalendar.is_trade_day(today, market="CN"):
            return latest

        hhmm = now_cn.hour * 100 + now_cn.minute
        if hhmm < 2000:
            return MarketCalendar.get_latest_trade_date("CN", ref_date=today - timedelta(days=1))

        return latest

    def _get_lhb_trade_dates(self, n: int = POOL_WINDOW) -> list[str]:
        ref_trade_date = self._get_lhb_reference_trade_date()
        if ref_trade_date is None:
            return []
        return MarketCalendar.get_recent_trade_dates(n, ref_date=ref_trade_date)

    @staticmethod
    def _get_manual_refresh_trade_dates(n: int = POOL_WINDOW) -> tuple[list[str], str, str]:
        """手动刷新专用窗口。

        规则：
        1. 若今天是交易日，先探针尝试今天；
        2. 今天有数据 -> 以今天为 30 日窗口终点；
        3. 今天为空 -> 回退到上一交易日；
        4. 今天探针异常 -> 沿用保守参考交易日，避免误清缓存。
        """
        from datetime import timedelta

        from app.services.lhb_market_data_service import probe_lhb_detail_count_for_date

        now_cn = MarketCalendar._get_market_now("CN")
        today = now_cn.date()

        fallback_ref_date = LhbTab._get_lhb_reference_trade_date()
        if fallback_ref_date is None:
            return [], "", "warn"

        if not MarketCalendar.is_trade_day(today, market="CN"):
            return MarketCalendar.get_recent_trade_dates(n, ref_date=fallback_ref_date), "", "info"

        previous_trade_date = MarketCalendar.get_latest_trade_date("CN", ref_date=today - timedelta(days=1))
        today_str = today.strftime("%Y%m%d")

        try:
            probe_payload = probe_lhb_detail_count_for_date(today_str, return_meta=True)
        except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] 手动刷新探针 {today_str} 失败，沿用参考交易日: {exc}")
            ref_trade_date = fallback_ref_date
            message = f"[龙虎榜池] {today_str} 今日探针异常，手动刷新沿用参考交易日 {ref_trade_date.strftime('%Y%m%d')}"
            return MarketCalendar.get_recent_trade_dates(n, ref_date=ref_trade_date), message, "warn"

        probe_status = str(probe_payload.get("status", "error") or "error")
        probe_count = int(probe_payload.get("count", 0) or 0)

        if probe_status == "ok" and probe_count > 0:
            message = f"[龙虎榜池] 手动刷新优先抓取今日数据: {today_str} | 探针{probe_count}条"
            return MarketCalendar.get_recent_trade_dates(n, ref_date=today), message, "info"

        if probe_status == "empty" or (probe_status == "ok" and probe_count <= 0):
            if previous_trade_date is None:
                return [], f"[龙虎榜池] {today_str} 今日暂无可用数据，且未找到上一交易日", "warn"
            previous_str = previous_trade_date.strftime("%Y%m%d")
            message = f"[龙虎榜池] {today_str} 今日暂无可用数据，手动刷新回退到上一交易日 {previous_str}"
            return MarketCalendar.get_recent_trade_dates(n, ref_date=previous_trade_date), message, "info"

        ref_trade_date = fallback_ref_date
        message = f"[龙虎榜池] {today_str} 今日探针异常，手动刷新沿用参考交易日 {ref_trade_date.strftime('%Y%m%d')}"
        return MarketCalendar.get_recent_trade_dates(n, ref_date=ref_trade_date), message, "warn"

    @classmethod
    def _format_pool_row_with_context(cls, rec: dict, context_map: dict[str, str] | None) -> dict:
        row_dict = dict(rec or {})
        original_reason = str(row_dict.pop("上榜原因", "") or "").strip()
        if original_reason:
            row_dict["_原始上榜原因"] = original_reason
        row_dict[cls.AI_CHAIN_CONTEXT_COLUMN] = cls._context_text_for_code(cls._record_stock_code(row_dict), context_map)
        # "最近上榜" 格式化：yyyyMMdd -> MM-dd 更紧凑，同时保留原始日期给关注池汇总使用
        raw_date = str(row_dict.get("最近上榜", ""))
        if len(raw_date) == 8:
            row_dict["_最近上榜_raw"] = raw_date
            row_dict["最近上榜"] = f"{raw_date[4:6]}-{raw_date[6:8]}"
        return row_dict

    @classmethod
    def _build_pool_display_rows(cls, pool: list[dict], context_map: dict[str, str] | None) -> list[dict]:
        return LhbPoolManager.sort_pool_rows_for_display(
            [cls._format_pool_row_with_context(rec, context_map) for rec in pool]
        )

    def _format_pool_row(self, rec: dict) -> dict:
        return self._format_pool_row_with_context(rec, self._get_ai_chain_context_map())

    def _is_default_lhb_sort_active(self) -> bool:
        try:
            return int(self.proxy_model.sortColumn()) < 0
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return True

    def _clear_proxy_sort_for_default_lhb_order(self) -> None:
        try:
            self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _sort_model_for_default_lhb_order(self) -> None:
        if not self._is_default_lhb_sort_active():
            return
        rows = list(getattr(self.model, "row_data", []) or [])
        if not rows:
            return
        sorted_rows = LhbPoolManager.sort_pool_rows_for_display(rows)
        current_order = [row.get("代码") for row in rows]
        sorted_order = [row.get("代码") for row in sorted_rows]
        if sorted_order == current_order:
            return
        self.model.update_data(sorted_rows, hydrate_latest_quotes=False)
        self._refresh_lhb_lineage(sorted_rows)

    def _is_opening_warmup_window(self) -> bool:
        try:
            status = MarketCalendar.get_market_status("CN")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False
        return str(status or "").strip() in self.OPENING_WARMUP_STATUSES

    def _should_defer_visible_quote_snapshot(self, quotes: dict | None) -> bool:
        if not quotes or self._applying_pending_quote_snapshot:
            return False
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        try:
            return self.isVisible() and self._is_current_workspace_tab()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    def _queue_visible_quote_snapshot(self, quotes: dict) -> None:
        self._pending_quote_snapshot.update(dict(quotes or {}))
        self._quote_apply_timer.start(self.QUOTE_APPLY_DEBOUNCE_MS)

    def _quote_apply_chunk_size(self) -> int:
        if not self._is_opening_warmup_window():
            return max(1, len(self._pending_quote_snapshot) or 1)
        try:
            return max(1, int(self.OPENING_WARMUP_QUOTE_APPLY_CHUNK_SIZE))
        except (TypeError, ValueError):
            return 20

    def _flush_pending_quote_snapshot(self) -> None:
        pending_items = list(dict(self._pending_quote_snapshot).items())
        if not pending_items:
            self._pending_quote_snapshot.clear()
            return
        chunk_size = self._quote_apply_chunk_size()
        chunk = dict(pending_items[:chunk_size])
        self._pending_quote_snapshot = dict(pending_items[chunk_size:])
        has_more = bool(self._pending_quote_snapshot)
        self._applying_pending_quote_snapshot = True
        try:
            self._apply_quote_snapshot_now(chunk, defer_sort=True, skip_sort=has_more)
        finally:
            self._applying_pending_quote_snapshot = False
        if has_more:
            self._quote_apply_timer.start(max(1, int(self.OPENING_WARMUP_QUOTE_APPLY_CONTINUE_MS)))

    def _schedule_default_lhb_quote_sort(self) -> None:
        if not self._is_opening_warmup_window():
            return
        if not self._is_default_lhb_sort_active():
            return
        if not getattr(self.model, "row_data", None):
            return
        self._quote_sort_timer.start(self.QUOTE_SORT_DEBOUNCE_MS)

    def _apply_quote_snapshot_now(
        self,
        quotes: dict | None,
        *,
        defer_sort: bool = False,
        skip_sort: bool = False,
    ):
        result = super()._apply_quote_snapshot(quotes)
        if skip_sort:
            return result
        if defer_sort:
            self._schedule_default_lhb_quote_sort()
        else:
            self._sort_model_for_default_lhb_order()
        return result

    def _apply_quote_snapshot(self, quotes: dict | None):
        if self._should_defer_visible_quote_snapshot(quotes):
            self._queue_visible_quote_snapshot(quotes)
            return None
        return self._apply_quote_snapshot_now(quotes)

    def get_watchlist_radar_rows(self) -> list[dict]:
        """给关注池读取已展示的龙虎榜信号；冷缓存由工作区快照后台预热。"""
        rows = self.get_row_data()
        if rows:
            return [dict(row) for row in rows]
        return []

    def _display_pool(self, pool: list[dict], *, emit_event: bool = True, row_data: list[dict] | None = None):
        """将池数据渲染到表格"""
        if row_data is None:
            row_data = self._build_pool_display_rows(pool, self._get_ai_chain_context_map())
        else:
            row_data = [dict(row) for row in row_data]
        row_signature = self._describe_lhb_rows(row_data).signature
        rows_changed = row_signature != self._last_lhb_signature

        if rows_changed:
            self._clear_proxy_sort_for_default_lhb_order()
            self.model.update_data([dict(row) for row in row_data], hydrate_latest_quotes=False)

        cached_days = len(self._get_pool_manager().get_cached_dates())
        self._set_pool_status(
            self._status_metric("入池 ", len(pool), "只"),
            self._status_metric("覆盖 ", cached_days, "个交易日"),
            self._status_metric("窗口 ", POOL_WINDOW, "日"),
            freshness=f"快照 {self._latest_cached_trade_date()}" if self._latest_cached_trade_date() else "快照待更新",
        )
        if hasattr(self, "table_state"):
            if row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_empty("暂无龙虎榜数据")

        # 触发全局通知，让关注池 Tab 能扫描到龙虎榜数据
        self._refresh_lhb_lineage(row_data)

        if rows_changed and emit_event:
            previous_handling = self._handling_lhb_pool_update
            self._handling_lhb_pool_update = True
            try:
                event_bus.sig_lhb_pool_updated.emit()
            finally:
                self._handling_lhb_pool_update = previous_handling

        if rows_changed:
            if self._is_opening_warmup_window():
                self.refresh_table_from_latest_snapshot(async_local=True)
            else:
                self.refresh_table_quotes_and_market_caps(
                    quote_task_id=task_registry.quote_refresh("lhb").task_id,
                    async_local=True,
                )

    # ================================================================
    # 后台回填缺失天数
    # ================================================================
    def _start_backfill(
        self,
        missing_dates: list[str],
        validation_dates: list[str] | None = None,
        validation_ref_date: str = "",
    ):
        """后台逐日回填缺失的龙虎榜数据"""
        if self._backfill_in_progress:
            return
        self._backfill_in_progress = True
        self.btn_refresh.setEnabled(False)

        def _safe_log_emit(level: str, message: str):
            try:
                main_win = self.window()
                if main_win and getattr(main_win, "_is_closing", False):
                    return
                event_bus.sig_system_log.emit(level, self._ensure_log_line(message))
            except RuntimeError:
                pass

        missing_sorted = sorted(set(missing_dates))
        validation_sorted = sorted(set(validation_dates or []))
        total = len(missing_sorted) + len(validation_sorted)
        if total <= 0:
            self._backfill_in_progress = False
            self.btn_refresh.setEnabled(True)
            return

        if missing_sorted and validation_sorted:
            self._set_pool_status(
                "正在同步龙虎榜",
                self._status_metric("补缺 ", len(missing_sorted), "天"),
                self._status_metric("校验 ", len(validation_sorted), "天"),
                freshness="手动回补",
                next_step="等待结果落表",
            )
            _safe_log_emit(
                "info",
                f"[龙虎榜池] 开始同步 | 补缺{len(missing_sorted)}天 | 校验{len(validation_sorted)}天",
            )
        elif missing_sorted:
            self._set_pool_status(
                "正在回填龙虎榜",
                self._status_metric("天数 ", len(missing_sorted)),
                f"{missing_sorted[0]}→{missing_sorted[-1]}",
                freshness="手动回补",
                next_step="等待结果落表",
            )
            _safe_log_emit(
                "info",
                f"[龙虎榜池] 开始回填 {len(missing_sorted)} 个交易日 | {missing_sorted[0]} -> {missing_sorted[-1]}",
            )
        else:
            self._set_pool_status(
                "正在校验龙虎榜缓存",
                self._status_metric("天数 ", len(validation_sorted)),
                freshness="本地缓存",
                next_step="等待校验完成",
            )
            _safe_log_emit(
                "info",
                f"[龙虎榜池] 开始校验 {len(validation_sorted)} 个已缓存交易日",
            )

        def _on_backfill_done(results: dict):
            self._backfill_in_progress = False
            self.btn_refresh.setEnabled(True)

            fetched_results = results.get("fetched", {}) if isinstance(results, dict) else {}
            validated_results = results.get("validated", {}) if isinstance(results, dict) else {}
            if not fetched_results and not validated_results:
                self._set_pool_status(
                    "同步失败",
                    freshness="远端失败沿用" if getattr(self.model, "row_data", []) else "待回补",
                    next_step="请稍后重试",
                )
                event_bus.sig_system_log.emit("error", self._ensure_log_line("[龙虎榜池] 同步任务未产出有效结果"))
                return

            pool_manager = results.get("pool_manager")
            if pool_manager is not None:
                self.pool_manager = pool_manager
            ai_chain_context_map = results.get("ai_chain_context_map")
            if isinstance(ai_chain_context_map, dict):
                self._ai_chain_context_map = ai_chain_context_map

            pool = list(results.get("pool") or [])
            row_data = list(results.get("row_data") or [])
            self._display_pool(pool, row_data=row_data)
            event_bus.sig_system_log.emit(
                "info",
                self._ensure_log_line(
                    f"[龙虎榜池] 同步完成 | 更新{len(fetched_results)}天 | 校验{len(validated_results)}天 | 入池{len(pool)}只"
                ),
            )

        def _on_backfill_error(error_message: str):
            self._backfill_in_progress = False
            self.btn_refresh.setEnabled(True)
            self._set_pool_status(
                "抓取异常",
                error_message,
                freshness="远端失败沿用" if getattr(self.model, "row_data", []) else "待回补",
                next_step="请稍后重试",
            )
            event_bus.sig_system_log.emit("error", self._ensure_log_line(f"[龙虎榜池] 抓取任务异常: {error_message}"))

        _task_lifecycle_for(self).run_background(
            "pool_backfill",
            lambda token: _build_lhb_backfill_payload(
                self,
                missing_sorted,
                validation_sorted,
                validation_ref_date,
                _safe_log_emit,
                token,
            ),
            on_success=_on_backfill_done,
            on_error=_on_backfill_error,
            task_id=task_registry.workspace("lhb_pool_backfill").task_id,
            timeout_sec=LHB_POOL_BACKFILL_TIMEOUT_SECONDS,
        )

    # ================================================================
    # 历史回补
    # ================================================================
    def _manual_refresh(self):
        """历史回补：清空缓存，重新获取全新 30 个交易日的龙虎榜数据"""
        if self._backfill_in_progress:
            from ui.components.toast_widget import show_toast

            show_toast("正在抓取中，请稍候...", "warning", self)
            return

        trade_dates, strategy_message, strategy_level = self._get_manual_refresh_trade_dates()
        if not trade_dates:
            from ui.components.toast_widget import show_toast

            show_toast("交易日历尚未就绪", "warning", self)
            return
        if strategy_message:
            event_bus.sig_system_log.emit(strategy_level, self._ensure_log_line(strategy_message))

        # 清空全部缓存，强制全量重拉
        self._get_pool_manager().clear_all()
        self._start_backfill(trade_dates)

    def refresh_history(self) -> bool:
        self._manual_refresh()
        return True

    def _schedule_pool_retry(self) -> None:
        self._pool_retry_timer.start(5_000)

    def shutdown(self) -> None:
        _task_lifecycle_for(self).shutdown(timeout_ms=LHB_TASK_SHUTDOWN_WAIT_TIMEOUT_MS)
        self._pool_load_in_progress = False
        self._backfill_in_progress = False
        retry_timer = getattr(self, "_pool_retry_timer", None)
        if retry_timer is not None:
            retry_timer.stop()
        quote_apply_timer = getattr(self, "_quote_apply_timer", None)
        if quote_apply_timer is not None:
            quote_apply_timer.stop()
        quote_sort_timer = getattr(self, "_quote_sort_timer", None)
        if quote_sort_timer is not None:
            quote_sort_timer.stop()
        pool_update_timer = getattr(self, "_pool_update_refresh_timer", None)
        if pool_update_timer is not None:
            pool_update_timer.stop()
        try:
            event_bus.sig_cache_bootstrap_ready.disconnect(self._on_cache_bootstrap_ready)
        except (TypeError, RuntimeError):
            pass
        try:
            event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
        except (TypeError, RuntimeError):
            pass
        try:
            event_bus.sig_lhb_pool_updated.disconnect(self._on_lhb_pool_updated)
        except (TypeError, RuntimeError):
            pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def deleteLater(self):
        self.shutdown()
        super().deleteLater()

    def _filter_table(self):
        search_text = self.search_box.text().strip().lower()
        self.set_proxy_filter_text(self.proxy_model, search_text)
        self._refresh_pool_status()

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, index):
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")

        # 提取当前表格顺序以传递给 K 线窗口
        code_list = []
        clicked_visual_row = index.row()
        for r in range(self.proxy_model.rowCount()):
            s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            if s_idx.row() < len(self.model.row_data):
                rd = dict(self.model.row_data[s_idx.row()] or {})
                rd.setdefault("代码", rd.get("代码", ""))
                rd.setdefault("名称", rd.get("名称", ""))
                code_list.append(rd)

        current_idx = 0
        if 0 <= clicked_visual_row < len(code_list):
            current_idx = clicked_visual_row

        ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if not code:
            return

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(self, code, name, vcp_data=row_data)
