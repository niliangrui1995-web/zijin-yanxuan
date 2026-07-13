# -*- coding: utf-8 -*-
"""
ui/tabs/foreign_block_trade_tab.py
大宗交易监控 Tab
展示包含指定外资关键字的营业部近期大宗交易明细，并高亮对倒等特殊行为。
"""

import datetime
import time
from contextlib import suppress
from functools import partial

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QComboBox, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.foreign_block_cache_service import load_foreign_block_cache, save_foreign_block_cache
from app.services.foreign_block_market_data_service import (
    BLOCK_TRADE_TIMEOUT_USER_MESSAGE as _BLOCK_TRADE_TIMEOUT_USER_MESSAGE,
)
from app.services.foreign_block_market_data_service import (
    BLOCK_TRADE_TOTAL_TIMEOUT as _BLOCK_TRADE_TOTAL_TIMEOUT,
)
from app.services.foreign_block_market_data_service import (
    FOREIGN_KEYWORDS,
    build_foreign_block_trade_rows,
    filter_foreign_block_rows_to_ai_chain,
    foreign_block_direction,
    should_include_foreign_block_row,
)
from app.services.foreign_block_market_data_service import (
    fetch_foreign_block_payload as _build_block_trade_fetch_payload,
)
from app.services.foreign_block_market_data_service import (
    format_incomplete_message as _format_incomplete_message,
)
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_task_lifecycle_service import (
    invoke_with_cancellation,
    task_lifecycle_for,
)
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.exceptions import CacheIOError
from ui.components import (
    MultiSelectFilterButton,
    SearchFilter,
    TableStateWrapper,
    VCPTableView,
    format_multi_select_summary,
)
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.theme import COLOR_FALL, COLOR_FLAT, COLOR_RISE


class BlockTradeFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.exact_filters = {}

    def setExactFilter(self, col_name, value):
        self.setExactFilters(col_name, [value] if value else [])

    def setExactFilters(self, col_name, values):
        normalized = {str(value or "").strip() for value in (values or []) if str(value or "").strip()}
        if normalized:
            self.exact_filters[col_name] = normalized
        else:
            self.exact_filters.pop(col_name, None)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if self.exact_filters:
            model = self.sourceModel()
            row_data = model.row_data[source_row]
            for col_name, values in self.exact_filters.items():
                cell_val = str(row_data.get(col_name, ""))
                candidate_values = values if isinstance(values, set) else {str(values or "").strip()}
                candidate_values = {value for value in candidate_values if value}
                if not candidate_values:
                    continue
                if col_name in ("买方营业部", "卖方营业部"):
                    # 席位做包含匹配（模糊搜索），匹配不上则拦截
                    if not any(value in cell_val for value in candidate_values):
                        return False
                elif col_name == "_branch":
                    # 特殊的席位联合逻辑
                    buyer_branch = str(row_data.get("买方营业部", ""))
                    seller_branch = str(row_data.get("卖方营业部", ""))
                    if not any(value in buyer_branch or value in seller_branch for value in candidate_values):
                        return False
                elif cell_val not in candidate_values:
                    return False

        filter_text = getattr(self, "_filter_text", "")
        if not filter_text:
            return True

        model = self.sourceModel()
        row_data = model.row_data[source_row]
        code_text = str(row_data.get("代码", "") or "").lower()
        name_text = str(row_data.get("名称", "") or "").lower()
        buyer_text = str(row_data.get("买方营业部", "") or "").lower()
        seller_text = str(row_data.get("卖方营业部", "") or "").lower()

        if SearchFilter.match_pinyin_or_text(filter_text, code_text, name_text):
            return True

        return filter_text in buyer_text or filter_text in seller_text


from core.logger import get_logger
from ui.tabs.base_stock_tab import (
    BaseStockTab,
    _show_kline_from_proxy_index,
    _show_stock_context_menu_from_proxy_index,
)

log = get_logger(__name__)

_FOREIGN_BLOCK_TRADE_TASK = task_registry.workspace("foreign_block_trade")
_FOREIGN_BLOCK_LOCAL_CACHE_TASK = task_registry.workspace("foreign_block_trade_local_cache")

# 模块级K线缓存：每只股票的文件只读一次，后续直接从内存取
_kline_cache: dict = {}
F5_AUTO_ONLINE_REFRESH_DELAY_MS = 24000
LOCAL_CACHE_LOAD_DELAY_MS = 650
POST_F5_LOCAL_CACHE_DEFER_MS = 5000


def _owner_attr(owner, name: str, default=None):
    try:
        return getattr(owner, name, default)
    except RuntimeError:
        return default


def _load_cache_payload(emit_event: bool, cancellation_token):
    result = dict(
        invoke_with_cancellation(
            load_foreign_block_cache,
            cancellation_token,
            row_filter=filter_foreign_block_rows_to_ai_chain,
        )
    )
    result["emit_event"] = bool(emit_event)
    return result


def _apply_cache_if_current(owner, generation: int, payload) -> None:
    if _owner_attr(owner, "_closing", False) or generation != _owner_attr(owner, "_local_cache_generation", 0):
        return
    owner._apply_local_cache_payload(payload)


def _apply_cache_error_if_current(owner, generation: int, error_message) -> None:
    if _owner_attr(owner, "_closing", False) or generation != _owner_attr(owner, "_local_cache_generation", 0):
        return
    owner._on_local_cache_failed(error_message)


def _apply_fetch_if_current(owner, generation: int, payload) -> None:
    if _owner_attr(owner, "_closing", False) or generation != _owner_attr(owner, "_fetch_generation", 0):
        return
    owner._on_data_fetched(payload)


def _apply_fetch_error_if_current(owner, generation: int, error_message) -> None:
    if _owner_attr(owner, "_closing", False) or generation != _owner_attr(owner, "_fetch_generation", 0):
        return
    owner._on_data_fetch_failed(error_message)


def determine_foreign_block_direction(buyer, seller):
    direction = foreign_block_direction(buyer, seller)
    color = {
        "外资对倒": "#F59E0B",
        "外资买入": COLOR_RISE,
        "外资卖出": COLOR_FALL,
    }.get(direction, COLOR_FLAT)
    return direction, color


class ForeignBlockTradeTab(BaseStockTab):
    def __init__(
        self,
        data_provider,
        parent=None,
        *,
        autoload: bool = True,
        initial_cache_load_delay_ms: int = LOCAL_CACHE_LOAD_DELAY_MS,
    ):
        super().__init__(data_provider=data_provider, parent=parent)
        try:
            self._initial_cache_load_delay_ms = max(0, int(initial_cache_load_delay_ms))
        except (TypeError, ValueError):
            self._initial_cache_load_delay_ms = LOCAL_CACHE_LOAD_DELAY_MS
        self._is_loading = False
        self._last_success_at = None
        self._status_primary = "等待加载"
        self._status_segments = ()
        self._status_freshness = ""
        self._status_next_step = ""
        self._had_rows_before_refresh = False
        self._pending_f5_online_refresh = False
        self._local_cache_loading = False
        self._local_cache_pending_emit_event: bool | None = None
        self._post_f5_local_cache_defer_until = 0.0
        self._post_f5_local_cache_pending = False
        self._post_f5_local_cache_emit_event = False
        self._initial_local_cache_load_started = False
        self._local_cache_generation = 0
        self._fetch_generation = 0
        self._closing = False
        self.days_to_fetch = 30  # 默认拉取最近30个交易日
        self._init_ui()
        if autoload:
            self._schedule_initial_local_cache_load()

        # 大宗交易页只消费 F5/本地快照，不加入盘中实时行情轮询。
        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)
        event_bus.sig_block_trade_updated.connect(self._on_block_trade_updated)

    def showEvent(self, event):
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._schedule_initial_local_cache_load()

    def _schedule_initial_local_cache_load(self) -> bool:
        if self._initial_local_cache_load_started:
            return False
        self._initial_local_cache_load_started = True
        QTimer.singleShot(self._initial_cache_load_delay_ms, self._load_local_cache)
        return True

    def prime_background_load(self) -> bool:
        return self._schedule_initial_local_cache_load()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("等待加载...")

        # ── 筛选器组：按数据维度从大到小排列 ──
        self.cmb_filter_date = MultiSelectFilterButton("全部日期")
        self.cmb_filter_date.setFixedWidth(128)
        self.cmb_filter_date.selectionChanged.connect(self._filter_table_combo)
        self.cmb_filter_date.set_options([], preserve_selection=False)

        self.cmb_filter_branch = MultiSelectFilterButton("全部监控席位")
        self.cmb_filter_branch.setFixedWidth(152)
        self.cmb_filter_branch.selectionChanged.connect(self._filter_table_combo)
        self.cmb_filter_branch.set_options([], preserve_selection=False)

        self.cmb_filter_direction = MultiSelectFilterButton("全部动作")
        self.cmb_filter_direction.setFixedWidth(128)
        self.cmb_filter_direction.selectionChanged.connect(self._filter_table_combo)
        self.cmb_filter_direction.set_options(["外资买入", "外资卖出", "外资对倒"], preserve_selection=False)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称或外资席位...")
        self.search_box.setFixedWidth(240)
        self.search_box.textChanged.connect(self._filter_table_combo)

        # ── 数据拉取范围 ──
        self.cmb_days = QComboBox()
        self.cmb_days.addItems(["近 10 交易日", "近 30 交易日", "近 40 交易日", "近 60 交易日"])
        # 默认选中 30 交易日，与 self.days_to_fetch 初始值保持一致
        self.cmb_days.setCurrentIndex(1)
        self.cmb_days.setFixedWidth(148)
        self.cmb_days.currentIndexChanged.connect(self._on_days_changed)

        self._refresh_filter_button_text(self.cmb_filter_date, "日期", "全部")
        self._refresh_filter_button_text(self.cmb_filter_branch, "席位", "全部")
        self._refresh_filter_button_text(self.cmb_filter_direction, "动作", "全部")

        filter_widgets = [
            self.cmb_filter_date,
            self.cmb_filter_branch,
            self.cmb_filter_direction,
            self.search_box,
            self.cmb_days,
        ]

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.run_post_online_refresh)

        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("外资大宗", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格
        self.columns = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "交易日期",
            "交易详情",
            "当日收盘价",
            "成交价格",
            "折/溢价率(%)",
            "成交数量(万股)",
            "成交金额(万元)",
            "买方营业部",
            "卖方营业部",
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.model.set_plain_style_headers(["交易日期"])
        self.proxy_model = BlockTradeFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无大宗交易数据", loading_title="抓取中...")

        # 列宽
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        # 严格压缩默认列宽，总和约1200px以内，确保即使在小屏幕/高缩放比下也不会超过屏幕宽度产生滚动条
        default_widths = [52, 60, 70, 55, 55, 55, 70, 85, 65, 65, 65, 75, 75, 140, 140]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置 (v4 强制刷新新布局)
        restored_sort = self.bind_header_persistence(self.table, "block_trade_header_state_v5")
        if not restored_sort:
            self.table.sortByColumn(self.model.headers.index("交易日期"), Qt.SortOrder.DescendingOrder)

        # 双击 → K线图
        self.table.doubleClicked.connect(self._on_double_click)
        # 右键菜单
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    def _on_days_changed(self, idx):
        days_map = {0: 10, 1: 30, 2: 40, 3: 60}
        self.days_to_fetch = days_map.get(idx, 10)
        self._load_block_trade_data()

    def _format_last_success_segment(self) -> str:
        if not self._last_success_at:
            return ""
        return self._status_metric("上次成功 ", self._last_success_at.strftime("%H:%M:%S"))

    def _latest_trade_date_text(self) -> str:
        dates = [
            str(row.get("交易日期", "")).strip()
            for row in (getattr(self.model, "row_data", []) or [])
            if isinstance(row, dict)
        ]
        dates = [date for date in dates if date]
        return max(dates) if dates else ""

    @staticmethod
    def _should_trigger_auto_refresh(
        now: datetime.datetime,
        *,
        is_trade_day: bool,
        last_auto_refresh_date: str,
        last_success_at: datetime.datetime | None = None,
        pending_auto_refresh_date: str = "",
    ) -> bool:
        today_compact = now.strftime("%Y%m%d")
        if pending_auto_refresh_date == today_compact:
            return False
        if now.hour < 20:
            return False
        if not is_trade_day:
            return False
        if last_auto_refresh_date == today_compact:
            return False
        if last_success_at is not None and last_success_at.date() == now.date() and last_success_at.hour >= 20:
            return False
        return True

    @staticmethod
    def _ensure_log_line(message: str) -> str:
        text = str(message or "")
        return text if text.endswith("\n") else text + "\n"

    @staticmethod
    def _should_save_cache(timeout_chunks, failed_chunks) -> bool:
        return not timeout_chunks and not failed_chunks

    @staticmethod
    def _extract_cache_filter_options(row_data: list[dict]) -> tuple[list[str], list[str]]:
        dates = sorted(
            {
                str(row.get("交易日期", "")).strip()
                for row in (row_data or [])
                if isinstance(row, dict) and str(row.get("交易日期", "")).strip()
            },
            reverse=True,
        )
        raw_branches = set()
        for row in row_data or []:
            if not isinstance(row, dict):
                continue
            raw_branches.add(str(row.get("买方营业部", "") or "").strip())
            raw_branches.add(str(row.get("卖方营业部", "") or "").strip())
        branches = sorted(
            branch for branch in raw_branches if branch and any(keyword in branch for keyword in FOREIGN_KEYWORDS)
        )
        return dates, branches

    def _apply_row_data(self, row_data: list[dict], *, preserve_selection: bool = True, already_filtered: bool = False):
        if not already_filtered:
            row_data = self._filter_rows_to_ai_chain(row_data)
        unique_dates, unique_branches = self._extract_cache_filter_options(row_data)
        self.cmb_filter_date.set_options(unique_dates, preserve_selection=preserve_selection)
        self.cmb_filter_branch.set_options(unique_branches, preserve_selection=preserve_selection)
        self._refresh_filter_button_text(self.cmb_filter_date, "日期", "全部")
        self._refresh_filter_button_text(self.cmb_filter_branch, "席位", "全部")
        self.model.update_data(row_data or [])
        return unique_dates, unique_branches

    @staticmethod
    def _filter_rows_to_ai_chain(row_data: list[dict]) -> list[dict]:
        return filter_foreign_block_rows_to_ai_chain(row_data)

    def _save_local_cache(self, row_data: list[dict]) -> bool:
        try:
            payload = save_foreign_block_cache(row_data, days_to_fetch=int(self.days_to_fetch))
            log.info(
                f"[外资大宗] 已保存本地缓存 "
                f"(rows={len(row_data or [])}, latest={payload['latest_trade_date'] or '-'})"
            )
            return True
        except CacheIOError as exc:
            log.warning(f"[外资大宗] 保存本地缓存失败: {exc}")
            return False

    def _load_local_cache(self, *, emit_event: bool = True):
        if _owner_attr(self, "_closing", False):
            return
        defer_until = float(getattr(self, "_post_f5_local_cache_defer_until", 0.0) or 0.0)
        if time.monotonic() < defer_until:
            self._schedule_post_f5_local_cache_load(emit_event=emit_event)
            return
        self._initial_local_cache_load_started = True
        if getattr(self, "_local_cache_loading", False):
            self._local_cache_pending_emit_event = bool(emit_event)
            return
        self._local_cache_loading = True
        generation = int(_owner_attr(self, "_local_cache_generation", 0)) + 1
        self._local_cache_generation = generation
        task_lifecycle_for(self, runner=task_manager).run_background(
            "local_cache",
            partial(_load_cache_payload, bool(emit_event)),
            task_id=_FOREIGN_BLOCK_LOCAL_CACHE_TASK,
            timeout_sec=30,
            on_success=partial(_apply_cache_if_current, self, generation),
            on_error=partial(_apply_cache_error_if_current, self, generation),
        )

    def _finish_local_cache_load(self):
        pending_emit_event = self._local_cache_pending_emit_event
        self._local_cache_pending_emit_event = None
        self._local_cache_loading = False
        if pending_emit_event is not None:
            QTimer.singleShot(0, lambda: self._load_local_cache(emit_event=pending_emit_event))

    def _schedule_post_f5_local_cache_load(self, *, emit_event: bool = True) -> bool:
        self._post_f5_local_cache_emit_event = bool(
            getattr(self, "_post_f5_local_cache_emit_event", False) or emit_event
        )
        if getattr(self, "_post_f5_local_cache_pending", False):
            return False
        self._post_f5_local_cache_pending = True
        defer_until = float(getattr(self, "_post_f5_local_cache_defer_until", 0.0) or 0.0)
        delay_ms = max(0, int((defer_until - time.monotonic()) * 1000))
        QTimer.singleShot(delay_ms, self._run_post_f5_local_cache_load)
        return True

    def _run_post_f5_local_cache_load(self) -> bool:
        defer_until = float(getattr(self, "_post_f5_local_cache_defer_until", 0.0) or 0.0)
        if time.monotonic() < defer_until:
            self._post_f5_local_cache_pending = False
            return self._schedule_post_f5_local_cache_load(
                emit_event=bool(getattr(self, "_post_f5_local_cache_emit_event", True))
            )
        self._post_f5_local_cache_pending = False
        self._post_f5_local_cache_defer_until = 0.0
        emit_event = bool(getattr(self, "_post_f5_local_cache_emit_event", True))
        self._post_f5_local_cache_emit_event = False
        self._load_local_cache(emit_event=emit_event)
        return True

    def _apply_local_cache_payload(self, payload: dict):
        try:
            payload = payload or {}
            rows = payload.get("rows", [])
            raw_count = int(payload.get("raw_count") or len(rows or []))
            latest_trade_date = str(payload.get("latest_trade_date", "")).strip()
            saved_at = str(payload.get("saved_at", "")).strip()
            try:
                self._last_success_at = datetime.datetime.fromisoformat(saved_at) if saved_at else None
            except ValueError:
                self._last_success_at = None

            QTimer.singleShot(
                0,
                lambda rows=rows, raw_count=raw_count, latest_trade_date=latest_trade_date, payload=payload: (
                    ForeignBlockTradeTab._finish_apply_local_cache_payload(
                        self,
                        rows=rows,
                        raw_count=raw_count,
                        latest_trade_date=latest_trade_date,
                        payload=payload,
                    )
                ),
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._on_local_cache_failed(str(exc))

    def _finish_apply_local_cache_payload(self, *, rows: list[dict], raw_count: int, latest_trade_date: str, payload: dict):
        try:
            if getattr(self, "_runtime_cleanup_done", False):
                return
            unique_dates, unique_branches = self._apply_row_data(
                rows,
                preserve_selection=False,
                already_filtered=True,
            )
            if rows:
                self._set_fetch_status(
                    "已加载本地缓存",
                    self._status_metric("命中 ", raw_count, "笔"),
                    self._status_metric("日期 ", len(unique_dates)),
                    self._status_metric("席位 ", len(unique_branches)),
                    freshness=f"快照 {latest_trade_date or self._latest_trade_date_text()}",
                    next_step="等待20:00自动更新",
                )
                if hasattr(self, "table_state"):
                    self.table_state.show_table()
                self._apply_latest_quotes_from_store()
                self._prime_visible_local_quote_snapshot(self.model)
            else:
                self._set_fetch_status(
                    "本地缓存为空",
                    freshness="待20:00更新",
                    next_step="等待20:00自动更新",
                )
                if hasattr(self, "table_state"):
                    self.table_state.show_empty(
                        "暂无大宗交易数据",
                        "当前本地缓存为空，等待每日20:00自动更新。",
                    )
            if bool(payload.get("emit_event", True)):
                event_bus.sig_block_trade_updated.emit()
        finally:
            self._finish_local_cache_load()

    def _on_local_cache_failed(self, error_message: str):
        try:
            log.debug(f"[外资大宗] 本地缓存不可用，跳过加载: {error_message}")
            self._set_fetch_status("等待20:00更新", freshness="待刷新", next_step="等待每日自动缓存")
        finally:
            self._finish_local_cache_load()

    def _on_block_trade_updated(self) -> None:
        self._load_local_cache(emit_event=False)

    def _cleanup_runtime_state(self):
        with suppress(TypeError, RuntimeError):
            event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
        with suppress(TypeError, RuntimeError):
            event_bus.sig_block_trade_updated.disconnect(self._on_block_trade_updated)
        super()._cleanup_runtime_state()

    def shutdown(self) -> None:
        self._closing = True
        self._local_cache_generation += 1
        self._fetch_generation += 1
        self._local_cache_loading = False
        self._is_loading = False
        lifecycle = getattr(self, "_task_lifecycle", None)
        if lifecycle is not None:
            lifecycle.shutdown(timeout_ms=1_000)
        self._cleanup_runtime_state()

    def _refresh_filter_button_text(self, button, prefix: str, all_text: str):
        text, tooltip = format_multi_select_summary(
            prefix,
            button.selected_labels(),
            all_text=all_text,
        )
        button.setText(text)
        button.setToolTip(tooltip)

    def _filter_status_text(self, button, *, all_text: str) -> str:
        labels = button.selected_labels()
        if not labels:
            return all_text
        if len(labels) <= 2:
            return " / ".join(labels)
        return f"{len(labels)}项"

    def _current_filter_summary(self) -> str:
        parts = []
        date_text = self._filter_status_text(self.cmb_filter_date, all_text="全部日期")
        if date_text != "全部日期":
            parts.append(date_text)

        branch_text = self._filter_status_text(self.cmb_filter_branch, all_text="全部监控席位")
        if branch_text != "全部监控席位":
            parts.append(branch_text)

        direction_text = self._filter_status_text(self.cmb_filter_direction, all_text="全部动作")
        if direction_text != "全部动作":
            parts.append(direction_text)

        search_text = self.search_box.text().strip()
        if search_text:
            parts.append(search_text)

        return "｜".join(parts) if parts else "全部"

    def _refresh_header_status(self):
        total = len(getattr(self.model, "row_data", []) or [])
        visible = self.proxy_model.rowCount()
        extra_segments = list(self._status_segments)
        last_success = self._format_last_success_segment()
        if last_success:
            extra_segments.append(last_success)

        self.lbl_status.setText(
            self.format_workspace_status(
                self._status_primary,
                result=f"{visible}/{total}只" if total else "0只",
                freshness=self._status_freshness or (f"快照 {self._latest_trade_date_text()}" if total else "待刷新"),
                current_filter=self._current_filter_summary(),
                next_step=self._status_next_step or "",
                extra_segments=tuple(seg for seg in extra_segments if seg),
            )
        )

    def _set_fetch_status(
        self,
        primary: str,
        *segments: str,
        freshness: str = "",
        next_step: str = "",
    ):
        self._status_primary = primary
        self._status_segments = tuple(seg for seg in segments if seg)
        self._status_freshness = str(freshness or "").strip()
        self._status_next_step = str(next_step or "").strip()
        self._refresh_header_status()

    def _should_include_row(self, buyer, seller):
        return should_include_foreign_block_row(buyer, seller)

    def _determine_direction(self, buyer, seller):
        """判断外资买卖动作"""
        return determine_foreign_block_direction(buyer, seller)

    def _load_block_trade_data(self):
        if _owner_attr(self, "_closing", False):
            return
        if self._is_loading:
            self._set_fetch_status("大宗抓取中", "上一轮任务尚未结束", freshness="快照", next_step="等待当前轮次结束")
            return
        self._is_loading = True
        self.btn_refresh.setEnabled(False)
        self._had_rows_before_refresh = bool(getattr(self.model, "row_data", []) or [])
        self._set_fetch_status(
            "正在抓取大宗交易",
            self._status_metric("窗口 ", self.days_to_fetch, "交易日"),
            freshness="快照",
            next_step="等待结果落表",
        )
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在抓取大宗交易...", "请稍候")
        # 清空上一轮的K线缓存，防止跨交易日窗口后内存只增不减
        _kline_cache.clear()

        self._fetch_generation += 1
        generation = self._fetch_generation
        task_lifecycle_for(self, runner=task_manager).run_background(
            "fetch",
            partial(_build_block_trade_fetch_payload, int(self.days_to_fetch)),
            task_id=_FOREIGN_BLOCK_TRADE_TASK.task_id,
            timeout_sec=_BLOCK_TRADE_TOTAL_TIMEOUT + 10,
            on_success=partial(_apply_fetch_if_current, self, generation),
            on_error=partial(_apply_fetch_error_if_current, self, generation),
        )

    def run_post_online_refresh(self) -> bool:
        self._load_block_trade_data()
        return True

    def schedule_post_online_refresh_after_f5(self) -> bool:
        if self._pending_f5_online_refresh:
            return False
        self._pending_f5_online_refresh = True
        QTimer.singleShot(F5_AUTO_ONLINE_REFRESH_DELAY_MS, self._run_pending_post_online_refresh_after_f5)
        return True

    def _run_pending_post_online_refresh_after_f5(self) -> bool:
        self._pending_f5_online_refresh = False
        return self.run_post_online_refresh()

    def prepare_post_f5_refresh(self) -> None:
        self._post_f5_local_cache_defer_until = max(
            float(getattr(self, "_post_f5_local_cache_defer_until", 0.0) or 0.0),
            time.monotonic() + POST_F5_LOCAL_CACHE_DEFER_MS / 1000.0,
        )

    def refresh_data_after_f5(self) -> bool:
        self.prepare_post_f5_refresh()
        self._schedule_post_f5_local_cache_load()
        self.refresh_table_from_latest_snapshot(current_model=self.model, async_local=True)
        return self.schedule_post_online_refresh_after_f5()

    def refresh_data_after_ai_industry_chain_update(self) -> bool:
        self._load_local_cache(emit_event=False)
        self.refresh_table_from_latest_snapshot(current_model=self.model, async_local=True)
        return self.run_post_online_refresh()

    @staticmethod
    def get_foreign_keywords() -> list[str]:
        return list(FOREIGN_KEYWORDS)

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        """大宗交易不向中央报价站贡献代码，避免盘中触发联网补价。"""
        return set()

    def _apply_latest_quotes_from_store(self):
        self._apply_quote_store_snapshot()

    def _on_cache_reload_completed(self):
        self._apply_latest_quotes_from_store()

    def _on_data_fetched(self, payload):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        if isinstance(payload, dict):
            data_list = payload.get("records", [])
            row_data = payload.get("row_data")
            grouped_count = int(payload.get("grouped_count") or 0)
            timeout_chunks = payload.get("timeout_chunks", [])
            failed_chunks = payload.get("failed_chunks", [])
        else:
            data_list = payload
            row_data = None
            grouped_count = 0
            timeout_chunks = []
            failed_chunks = []

        if not data_list:
            if not timeout_chunks and not failed_chunks:
                self._apply_row_data([], preserve_selection=False)
                self._save_local_cache([])
            if timeout_chunks or failed_chunks:
                if self._had_rows_before_refresh:
                    self._set_fetch_status(
                        "刷新未完成",
                        "已保留上次成功结果",
                        _format_incomplete_message(timeout_chunks, failed_chunks).lstrip("；"),
                        freshness="远端失败沿用",
                        next_step="",
                    )
                    if hasattr(self, "table_state"):
                        self.table_state.show_table()
                else:
                    self._set_fetch_status(
                        "大宗抓取未完成",
                        "本轮无有效结果",
                        _format_incomplete_message(timeout_chunks, failed_chunks).lstrip("；"),
                        freshness="待刷新",
                        next_step="点击刷新重新尝试",
                    )
                    if hasattr(self, "table_state"):
                        self.table_state.show_error(
                            "大宗交易刷新失败",
                            _BLOCK_TRADE_TIMEOUT_USER_MESSAGE,
                            meta="当前没有可展示的历史结果。",
                            action_text="重新尝试",
                            action_callback=self._load_block_trade_data,
                        )
            else:
                self._set_fetch_status(
                    "近期无命中",
                    self._status_metric("窗口 ", self.days_to_fetch, "交易日"),
                    freshness="快照待更新",
                    next_step="点击刷新扩大窗口",
                )
                if hasattr(self, "table_state"):
                    self.table_state.show_empty(
                        "暂无大宗交易数据",
                        "当前窗口内没有命中监控席位的大宗交易记录。",
                    )
            event_bus.sig_block_trade_updated.emit()
            return

        if row_data is None:
            row_data, grouped_count = build_foreign_block_trade_rows(data_list)
        unique_dates, unique_branches = self._apply_row_data(row_data)
        self._last_success_at = datetime.datetime.now()
        self._set_fetch_status(
            self._status_metric("命中 ", grouped_count or len(row_data), "笔"),
            self._status_metric("日期 ", len(unique_dates)),
            self._status_metric("席位 ", len(unique_branches)),
            self._status_metric("窗口 ", self.days_to_fetch, "交易日"),
            _format_incomplete_message(timeout_chunks, failed_chunks).lstrip("；"),
            next_step="",
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()
        if self._should_save_cache(timeout_chunks, failed_chunks):
            self._save_local_cache(row_data)
        elif timeout_chunks or failed_chunks:
            log.warning("[外资大宗] 本轮结果不完整，已跳过覆盖本地缓存")
        # 强制应用当前的筛选状态
        self._filter_table_combo()
        event_bus.sig_block_trade_updated.emit()

        self._apply_latest_quotes_from_store()
        self._prime_visible_local_quote_snapshot(self.model)

    def _on_data_fetch_failed(self, error_message: str):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        msg = str(error_message or "").strip()
        if not msg:
            msg = "大宗交易抓取失败，请稍后重试。"
        elif not msg.startswith(("抓取超时", "抓取失败")):
            msg = f"大宗交易抓取失败：{msg}"
        if getattr(self.model, "row_data", []):
            self._set_fetch_status("刷新失败", msg, "已保留上次成功结果", freshness="远端失败沿用", next_step="")
        else:
            self._set_fetch_status("刷新失败", msg, freshness="待刷新", next_step="点击刷新重新尝试")
        if hasattr(self, "table_state"):
            if getattr(self.model, "row_data", []):
                self.table_state.show_table()
            else:
                self.table_state.show_error(
                    "大宗交易刷新失败",
                    msg,
                    meta="当前没有可展示的历史结果。",
                    action_text="重新尝试",
                    action_callback=self._load_block_trade_data,
                )

    def _filter_table_combo(self):
        search_text = self.search_box.text().strip().lower()
        self.proxy_model.setFilterText(search_text)

        self.proxy_model.setExactFilters("交易日期", self.cmb_filter_date.selected_values())
        self.proxy_model.setExactFilters("交易详情", self.cmb_filter_direction.selected_values())
        self.proxy_model.setExactFilters("_branch", self.cmb_filter_branch.selected_values())
        self._refresh_filter_button_text(self.cmb_filter_date, "日期", "全部")
        self._refresh_filter_button_text(self.cmb_filter_branch, "席位", "全部")
        self._refresh_filter_button_text(self.cmb_filter_direction, "动作", "全部")
        self._refresh_header_status()

    def _on_double_click(self, index):
        _show_kline_from_proxy_index(self, index, ui_signals)

    def _show_context_menu(self, pos):
        _show_stock_context_menu_from_proxy_index(self, pos)
