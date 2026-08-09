# -*- coding: utf-8 -*-
"""AI产业链独立 Tab 组件。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ai_industry_chain_period_return_service import build_period_return_rows
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_industry_chain_service import (
    AI_CHAIN_FILE,
    PLACEHOLDER,
    get_ai_industry_chain_source_mtime,
    load_cached_ai_industry_chain_rows,
    refresh_ai_industry_chain_rows,
)
from app.services.ui_task_lifecycle_service import task_lifecycle_for
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks

log = get_logger(__name__)


def _raise_if_cancelled(cancellation_token) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _load_chain_rows(
    workbook_path: Path,
    *,
    prefer_cache: bool,
    cancellation_token=None,
) -> tuple[list[dict], str]:
    _raise_if_cancelled(cancellation_token)
    if prefer_cache:
        cached_rows = load_cached_ai_industry_chain_rows(workbook_path)
        _raise_if_cancelled(cancellation_token)
        if cached_rows:
            return cached_rows, "cache"
    rows = refresh_ai_industry_chain_rows(workbook_path)
    _raise_if_cancelled(cancellation_token)
    return rows, "workbook"


def _is_current_visible_chain_tab(tab) -> bool:
    try:
        return bool(tab.isVisible() and tab._is_current_workspace_tab())
    except RuntimeError:
        return False


def _begin_chain_load(tab) -> None:
    if hasattr(tab, "table_state"):
        tab.table_state.show_loading("正在加载AI产业链...", "读取Excel并回填本地行情")
    tab._set_chain_status("AI产业链刷新中", freshness=tab._workbook_freshness(), next_step="读取Excel")


def _apply_chain_load_error(tab, message: str) -> None:
    tab.model.update_data([])
    tab._chain_codes = set()
    tab._set_chain_status("AI产业链加载失败", message, freshness="读取失败", next_step="检查Excel文件")
    if hasattr(tab, "table_state"):
        tab.table_state.show_error(
            "AI产业链加载失败",
            message,
            meta=str(tab.workbook_path),
            action_text="重新尝试",
            action_callback=lambda: _schedule_chain_load(tab, force_workbook=True),
        )


def _apply_chain_rows(
    tab,
    rows: list[dict],
    *,
    async_period_returns: bool,
    background_prime: bool,
) -> None:
    tab.model.update_data(rows)
    tab._chain_codes = {row.get("代码", "") for row in rows if row.get("代码")}
    if not rows:
        tab.table_state.show_empty("暂无AI产业链数据")
        tab._set_chain_status("AI产业链为空", freshness=tab._workbook_freshness(), next_step="检查Excel内容")
        return

    tab.table_state.show_table()
    tab._set_chain_status(
        "AI产业链已就绪",
        tab._status_metric("标的 ", len(tab._chain_codes), "只"),
        tab._status_metric("映射 ", len(rows), "条"),
        freshness=tab._workbook_freshness(),
        next_step="",
    )
    if background_prime:
        tab._apply_quote_store_snapshot()
    else:
        tab.refresh_table_from_latest_snapshot(current_model=tab.model, async_local=True)
        if async_period_returns:
            tab._schedule_period_returns(rows)
    event_bus.sig_ai_industry_chain_updated.emit()


def _finish_chain_load(tab, generation: int, payload, *, background_prime: bool) -> None:
    if generation != tab._chain_load_generation or getattr(tab, "_runtime_cleanup_done", False):
        return
    tab._chain_load_active = False
    tab._background_prime_loading = False
    if background_prime:
        tab._background_prime_done = True
    rows, _source = payload
    _apply_chain_rows(
        tab,
        rows,
        async_period_returns=not background_prime,
        background_prime=background_prime,
    )
    if background_prime:
        tab._runtime_started = True
        _queue_prime_period_returns(tab, rows)


def _fail_chain_load(tab, generation: int, message: str, *, background_prime: bool) -> None:
    if generation != tab._chain_load_generation or getattr(tab, "_runtime_cleanup_done", False):
        return
    tab._chain_load_active = False
    tab._background_prime_loading = False
    if background_prime:
        tab._background_prime_done = True
    _apply_chain_load_error(tab, str(message or "读取失败"))


def _schedule_chain_load(tab, *, force_workbook: bool, background_prime: bool = False) -> bool:
    if getattr(tab, "_runtime_cleanup_done", False):
        return False
    if tab._chain_load_active and not force_workbook:
        return False

    _begin_chain_load(tab)
    tab._chain_load_generation += 1
    generation = tab._chain_load_generation
    if tab._background_prime_loading and not background_prime:
        tab._background_prime_done = True
    tab._background_prime_loading = background_prime
    tab._chain_load_active = True
    workbook_path = Path(tab.workbook_path)
    task_lifecycle_for(tab, runner=task_manager).run_background(
        "chain-load",
        lambda token: _load_chain_rows(
            workbook_path,
            prefer_cache=not force_workbook,
            cancellation_token=token,
        ),
        on_success=lambda payload: _finish_chain_load(
            tab,
            generation,
            payload,
            background_prime=background_prime,
        ),
        on_error=lambda message: _fail_chain_load(
            tab,
            generation,
            message,
            background_prime=background_prime,
        ),
        task_id=task_registry.workspace("ai_industry_chain_load"),
        timeout_sec=tab.CHAIN_LOAD_TIMEOUT_SECONDS,
    )
    return True


def _start_visible_chain_runtime(tab) -> bool:
    if getattr(tab, "_runtime_cleanup_done", False) or tab._runtime_started:
        return False
    if not _is_current_visible_chain_tab(tab) or not tab._should_start_runtime_on_show():
        return False
    scheduled = _schedule_chain_load(tab, force_workbook=False)
    if scheduled or tab._chain_load_active:
        tab._runtime_started = True
        return True
    return False


def _start_manual_chain_refresh(tab) -> bool:
    tab._runtime_started = True
    return _schedule_chain_load(tab, force_workbook=True)


def _resume_pending_post_f5_chain_refresh(tab) -> bool:
    if not getattr(tab, "_pending_post_f5_refresh", False):
        return False
    if getattr(tab, "_runtime_cleanup_done", False) or not _is_current_visible_chain_tab(tab):
        return False

    tab._pending_post_f5_refresh = False
    timer = getattr(tab, "_runtime_start_timer", None)
    if timer is not None:
        timer.stop()
    tab._runtime_started = True
    tab._period_return_generation += 1
    tab._period_return_loading = False
    tab._pending_period_return_rows = None
    tab._pending_period_return_source_rows = None
    _cancel_period_return_task(tab, "post_f5_refresh")
    scheduled = False
    try:
        scheduled = _schedule_chain_load(tab, force_workbook=True)
    finally:
        tab._pending_post_f5_refresh = bool(
            not scheduled and not getattr(tab, "_runtime_cleanup_done", False)
        )
    return scheduled


def _request_post_f5_chain_refresh(tab) -> bool:
    if getattr(tab, "_runtime_cleanup_done", False):
        return False
    tab._pending_post_f5_refresh = True
    if not _is_current_visible_chain_tab(tab):
        return True
    return _resume_pending_post_f5_chain_refresh(tab)


def _period_return_rows(tab, rows, cancellation_token=None) -> list[dict]:
    return build_period_return_rows(
        rows,
        data_provider=tab.data_provider,
        period_columns=tab.PERIOD_COLUMNS,
        placeholder=PLACEHOLDER,
        cancellation_token=cancellation_token,
    )


def _prepare_chain_rows(tab, rows, *, async_period_returns: bool) -> list[dict]:
    if tab._background_prime_loading or async_period_returns:
        return rows
    return _period_return_rows(tab, rows)


def _commit_period_return_rows(tab, rows: list[dict]) -> None:
    values_by_code = {str(row.get("代码") or "").strip(): row for row in rows}
    merged_rows = []
    for current in getattr(tab.model, "row_data", None) or []:
        merged = dict(current)
        values = values_by_code.get(str(current.get("代码") or "").strip()) or {}
        for column in tab.PERIOD_COLUMNS.values():
            merged[column] = values.get(column, PLACEHOLDER)
        merged_rows.append(merged)
    if merged_rows:
        tab.model.update_data(merged_rows)
    tab._refresh_chain_status()


def _should_defer_period_return_commit(tab) -> bool:
    try:
        return bool(tab.window().isVisible() and not tab._is_current_workspace_tab())
    except RuntimeError:
        return False


def _on_period_returns_ready(
    tab,
    generation: int,
    rows: list[dict],
    *,
    commit_when_hidden: bool = False,
) -> None:
    if generation != tab._period_return_generation or getattr(tab, "_runtime_cleanup_done", False):
        return
    tab._period_return_loading = False
    if not commit_when_hidden and _should_defer_period_return_commit(tab):
        tab._pending_period_return_rows = (generation, [dict(row) for row in rows])
        return
    _commit_period_return_rows(tab, rows)


def _on_period_returns_error(tab, generation: int, message: str) -> None:
    if generation != tab._period_return_generation or getattr(tab, "_runtime_cleanup_done", False):
        return
    tab._period_return_loading = False
    log.debug(f"[AI产业链] 周期涨幅后台计算失败: {message}")
    tab._refresh_chain_status()


def _apply_pending_period_return_rows(tab) -> None:
    pending = tab._pending_period_return_rows
    tab._pending_period_return_rows = None
    if pending is None:
        return
    generation, rows = pending
    if generation == tab._period_return_generation:
        _commit_period_return_rows(tab, rows)


def _cancel_period_return_task(tab, reason: str) -> None:
    lifecycle = getattr(tab, "_task_lifecycle", None)
    cancel = getattr(lifecycle, "cancel", None)
    if callable(cancel):
        cancel("period-returns", reason=reason)


def _submit_period_return_task(
    tab,
    generation: int,
    rows: list[dict],
    *,
    commit_when_hidden: bool = False,
) -> None:
    tab._period_return_loading = True
    data_provider = tab.data_provider
    period_columns = dict(tab.PERIOD_COLUMNS)
    task_lifecycle_for(tab, runner=task_manager).run_background(
        "period-returns",
        lambda token: build_period_return_rows(
            rows,
            data_provider=data_provider,
            period_columns=period_columns,
            placeholder=PLACEHOLDER,
            cancellation_token=token,
        ),
        on_success=lambda payload: _on_period_returns_ready(
            tab,
            generation,
            payload,
            commit_when_hidden=commit_when_hidden,
        ),
        on_error=lambda message: _on_period_returns_error(tab, generation, message),
        task_id=task_registry.workspace("ai_industry_chain_period_returns"),
        timeout_sec=tab.PERIOD_RETURN_TIMEOUT_SECONDS,
    )


def _start_pending_period_return_work(tab) -> None:
    pending = tab._pending_period_return_source_rows
    if pending is None or _should_defer_period_return_commit(tab):
        return
    generation, rows = pending
    tab._pending_period_return_source_rows = None
    if generation == tab._period_return_generation:
        _submit_period_return_task(tab, generation, rows)


def _queue_prime_period_returns(tab, rows: list[dict]) -> None:
    if not rows or not tab.data_provider:
        return
    tab._period_return_generation += 1
    generation = tab._period_return_generation
    tab._period_return_loading = False
    tab._pending_period_return_rows = None
    tab._pending_period_return_source_rows = None
    _cancel_period_return_task(tab, "period_returns_replaced")
    _submit_period_return_task(
        tab,
        generation,
        [dict(row) for row in rows],
        commit_when_hidden=True,
    )


def _reset_ai_background_preload(tab) -> None:
    tab._runtime_start_timer.stop()
    tab._chain_load_generation += 1
    tab._chain_load_active = False
    tab._background_prime_loading = False
    tab._background_prime_done = False
    tab._period_return_generation += 1
    tab._period_return_loading = False
    tab._pending_period_return_rows = None
    tab._pending_period_return_source_rows = None
    tab._runtime_started = False


def _cancel_ai_background_preload(tab, *, reason: str):
    return cancel_background_preload_tasks(
        tab,
        lifecycle_names=("chain-load", "period-returns"),
        task_ids=(
            task_registry.workspace("ai_industry_chain_load"),
            task_registry.workspace("ai_industry_chain_period_returns"),
        ),
        reason=reason,
        reset_state=lambda: _reset_ai_background_preload(tab),
        local_settled=lambda: not tab._chain_load_active and not tab._period_return_loading,
        runner=task_manager,
    )


def _configure_ai_table_columns(tab) -> None:
    header = tab.table.horizontalHeader()
    header.setStretchLastSection(True)
    widths = [52, 76, 92, 70, 70, 86, 160, 82, 82, 82, 520]
    for col_idx, width in enumerate(widths):
        if col_idx < len(tab.model.headers):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            tab.table.setColumnWidth(col_idx, width)


class AIIndustryChainTab(BaseStockTab):
    COLUMNS = [
        "代码",
        "名称",
        "现价",
        "涨幅",
        "市值",
        "细分板块",
        "5日涨幅",
        "10日涨幅",
        "20日涨幅",
        "备注",
    ]

    PERIOD_COLUMNS = {
        5: "5日涨幅",
        10: "10日涨幅",
        20: "20日涨幅",
    }
    PERIOD_RETURN_TIMEOUT_SECONDS = 60.0
    CHAIN_LOAD_TIMEOUT_SECONDS = 60.0

    def __init__(
        self,
        data_provider,
        parent=None,
        workbook_path: str | Path | None = None,
        *,
        runtime_start_delay_ms: int = 350,
    ):
        super().__init__(data_provider=data_provider, parent=parent)
        self.workbook_path = Path(workbook_path) if workbook_path is not None else AI_CHAIN_FILE
        try:
            self._runtime_start_delay_ms = max(0, int(runtime_start_delay_ms))
        except (TypeError, ValueError):
            self._runtime_start_delay_ms = 350
        self._chain_codes: set[str] = set()
        self._status_primary = "等待AI产业链"
        self._status_segments: list[str] = []
        self._status_freshness = ""
        self._status_next_step = ""
        self._runtime_started = False
        self._background_prime_loading = False
        self._background_prime_done = False
        self._chain_load_generation = 0
        self._chain_load_active = False
        self._period_return_generation = 0
        self._period_return_loading = False
        self._pending_period_return_rows: tuple[int, list[dict]] | None = None
        self._pending_period_return_source_rows: tuple[int, list[dict]] | None = None
        self._pending_post_f5_refresh = False

        self._init_ui()
        self._runtime_start_timer = QTimer(self)
        self._runtime_start_timer.setSingleShot(True)
        self._runtime_start_timer.timeout.connect(lambda: _start_visible_chain_runtime(self))

    def _ensure_runtime_started(self):
        if (
            self._runtime_started
            or self._runtime_start_timer.isActive()
            or getattr(self, "_runtime_cleanup_done", False)
        ):
            return
        self._runtime_start_timer.start(self._runtime_start_delay_ms)

    def prime_background_load(self):
        if self._runtime_started or self._background_prime_done or self._chain_load_active:
            return False
        return _schedule_chain_load(self, force_workbook=False, background_prime=True)

    def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
        prepare = getattr(self.table, "prepare_workspace_preload_repaint_guard", None)
        if callable(prepare):
            prepare(load_reason=load_reason)

    def is_background_preload_complete(self) -> bool:
        """Return whether cache load and hidden period-return derivation have settled."""
        if getattr(self, "_runtime_cleanup_done", False):
            return True
        load_finished = bool(self._background_prime_done or self._runtime_started)
        return bool(
            load_finished
            and not self._chain_load_active
            and not self._period_return_loading
            and self._pending_period_return_rows is None
            and self._pending_period_return_source_rows is None
            and not self._pending_post_f5_refresh
        )

    cancel_background_preload = _cancel_ai_background_preload

    def showEvent(self, event):
        super().showEvent(event)
        if _resume_pending_post_f5_chain_refresh(self) or self._pending_post_f5_refresh:
            return
        _apply_pending_period_return_rows(self)
        _start_pending_period_return_work(self)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()

    def on_workspace_tab_activated(self) -> None:
        if _resume_pending_post_f5_chain_refresh(self) or self._pending_post_f5_refresh:
            return
        _apply_pending_period_return_rows(self)
        _start_pending_period_return_work(self)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status_label = QLabel("未加载")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、板块或备注...")
        self.search_box.setMinimumWidth(180)
        self.search_box.setMaximumWidth(280)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(lambda _checked=False: _start_manual_chain_refresh(self))

        btn_reset = QPushButton("解除排序")
        btn_reset.clicked.connect(self._reset_view)

        toolbar = self.build_tab_toolbar("AI产业链", self.status_label, [self.search_box], [self.btn_refresh, btn_reset])
        layout.addWidget(toolbar)

        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无AI产业链数据", loading_title="加载中...")

        self.model = StockTableModel(self.COLUMNS)
        self.model.set_sparse_update_coalescing(True)
        self.model.set_plain_style_headers(["细分板块", "备注"])
        self.model.set_muted_text_headers(["备注"])
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.table.set_coalesced_flash_repaint_enabled(True)
        self.table.set_targeted_flash_repaint_enabled(True, metric_scope="ai_industry_chain")

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

        _configure_ai_table_columns(self)

        restored_sort = self.bind_header_persistence(self.table, "header_state_ai_industry_chain_v1")
        if not restored_sort:
            self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)
        self._set_chain_status("等待AI产业链", freshness="待加载", next_step="点击刷新读取Excel")

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_chain_status()

    def _reset_view(self):
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        rows = getattr(self.model, "row_data", []) or []
        if not rows:
            self._refresh_chain_status()
            return
        self._set_chain_status(
            "AI产业链已就绪",
            self._status_metric("标的 ", len(self._chain_codes), "只"),
            self._status_metric("映射 ", len(rows), "条"),
            freshness=self._workbook_freshness(),
            next_step="已解除排序",
        )

    def _current_filter_summary(self) -> str:
        keyword = str(self.search_box.text() or "").strip()
        return keyword or "全部"

    def _refresh_chain_status(self):
        total = len(getattr(self.model, "row_data", None) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        self.status_label.setText(
            self.format_workspace_status(
                self._status_primary or ("AI产业链已就绪" if total else "等待AI产业链"),
                result=self._status_metric("结果 ", f"{visible}/{total}", "条") if total else "",
                freshness=self._status_freshness,
                current_filter=self._current_filter_summary(),
                next_step=self._status_next_step or ("" if total else "点击刷新读取Excel"),
                extra_segments=tuple(seg for seg in self._status_segments if seg),
            )
        )

    def _set_chain_status(self, primary: str, *segments: str, freshness: str = "", next_step: str = ""):
        self._status_primary = str(primary or "").strip()
        self._status_segments = [str(seg or "").strip() for seg in segments if str(seg or "").strip()]
        self._status_freshness = str(freshness or "").strip()
        self._status_next_step = str(next_step or "").strip()
        self._refresh_chain_status()

    def _read_workbook_rows(self) -> list[dict]:
        return refresh_ai_industry_chain_rows(self.workbook_path)

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        """情报源 tab 不向中央报价站贡献代码，避免盘中触发联网补价。"""
        return set()

    def _schedule_period_returns(self, rows: list[dict]) -> bool:
        if not rows or not self.data_provider:
            return False
        self._period_return_generation += 1
        generation = self._period_return_generation
        self._period_return_loading = False
        self._pending_period_return_rows = None
        row_snapshot = [dict(row) for row in rows]
        _cancel_period_return_task(self, "period_returns_replaced")
        if _should_defer_period_return_commit(self):
            self._pending_period_return_source_rows = (generation, row_snapshot)
            return True
        self._pending_period_return_source_rows = None
        _submit_period_return_task(self, generation, row_snapshot)
        return True

    def _workbook_freshness(self) -> str:
        stamp = get_ai_industry_chain_source_mtime(self.workbook_path)
        if stamp <= 0:
            return "待加载"

        from datetime import datetime

        return "Excel " + datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    def _load_chain_data(self, _checked=False, *, async_period_returns: bool = True):
        _begin_chain_load(self)

        try:
            rows = self._read_workbook_rows()
            rows = _prepare_chain_rows(self, rows, async_period_returns=async_period_returns)
        except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
            _apply_chain_load_error(self, str(exc))
            return

        _apply_chain_rows(
            self,
            rows,
            async_period_returns=async_period_returns,
            background_prime=self._background_prime_loading,
        )

    def refresh_data_after_f5(self) -> bool:
        return _request_post_f5_chain_refresh(self)

    def _cleanup_runtime_state(self):
        timer = getattr(self, "_runtime_start_timer", None)
        if timer is not None:
            timer.stop()
        self._chain_load_generation += 1
        self._chain_load_active = False
        self._background_prime_loading = False
        self._period_return_generation += 1
        self._period_return_loading = False
        self._pending_period_return_rows = None
        self._pending_period_return_source_rows = None
        self._pending_post_f5_refresh = False
        super()._cleanup_runtime_state()

    def _row_from_proxy_index(self, index):
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return None
        return self.model.row_data[row]

    def _on_double_click(self, index):
        row = self._row_from_proxy_index(index)
        if not row:
            return

        code = str(row.get("代码") or "").strip()
        if not code:
            return

        code_list = []
        current_idx = 0
        clicked_visual_row = index.row()
        for visual_row in range(self.proxy_model.rowCount()):
            source_idx = self.proxy_model.mapToSource(self.proxy_model.index(visual_row, 0))
            if source_idx.row() >= len(self.model.row_data):
                continue
            row_dict = dict(self.model.row_data[source_idx.row()] or {})
            row_dict.setdefault("代码", row_dict.get("代码", ""))
            row_dict.setdefault("名称", row_dict.get("名称", ""))
            code_list.append(row_dict)
            if visual_row == clicked_visual_row:
                current_idx = len(code_list) - 1

        ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        row = self._row_from_proxy_index(self.table.indexAt(pos))
        if not row:
            return

        code = str(row.get("代码") or "").strip()
        name = str(row.get("名称") or "").strip()
        if not code:
            return

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(self, code, name, vcp_data=self._build_watchlist_payload(row))

    @staticmethod
    def _build_watchlist_payload(row: dict) -> dict:
        payload = dict(row or {})
        segment = str(payload.get("细分板块") or payload.get("细分环节") or "").strip()
        if segment and not str(payload.get("细分板块") or "").strip():
            payload["细分板块"] = segment

        remark = str(payload.get("备注") or "").strip()
        if remark and not str(payload.get("AI产业链") or "").strip():
            payload["AI产业链"] = remark

        tags = payload.get("来源标签")
        if isinstance(tags, (list, tuple, set)):
            source_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        elif tags:
            source_tags = [part.strip() for part in str(tags).replace("｜", ",").split(",") if part.strip()]
        else:
            source_tags = []
        if "AI产业链" not in source_tags:
            source_tags.append("AI产业链")
        payload["来源标签"] = source_tags
        return payload
