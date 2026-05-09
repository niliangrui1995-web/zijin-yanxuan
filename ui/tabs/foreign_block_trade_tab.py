# -*- coding: utf-8 -*-
"""
ui/tabs/foreign_block_trade_tab.py
大宗交易监控 Tab
展示包含指定外资关键字的营业部近期大宗交易明细，并高亮对倒等特殊行为。
"""
import datetime
import json
import os
import sys

import pandas as pd
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QComboBox, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ui_runtime_service import (
    MarketCalendar,
    ProcessExecutionError,
    ProcessTimeoutError,
    build_domestic_process_env,
    run_process,
    task_registry,
    ui_signals,
    windows_no_window_creationflags,
)
from app.services.ui_runtime_service import (
    background_job_runner as task_manager,
)
from app.services.ui_runtime_service import (
    domain_events as event_bus,
)
from core.exceptions import CacheIOError, DataFormatError
from core.json_cache import load_json_file, save_json_file
from core.task_errors import UserFacingTaskError
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
        normalized = {
            str(value or "").strip()
            for value in (values or [])
            if str(value or "").strip()
        }
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
                cell_val = str(row_data.get(col_name, ''))
                candidate_values = values if isinstance(values, set) else {str(values or '').strip()}
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
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

FOREIGN_KEYWORDS = ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BLOCK_TRADE_CACHE_FILE = os.path.join(_PROJECT_ROOT, "data", "Cache", "foreign_block_trade_latest.json")
_FOREIGN_BLOCK_TRADE_TASK = task_registry.workspace("foreign_block_trade")

# 模块级K线缓存：每只股票的文件只读一次，后续直接从内存取
_kline_cache: dict = {}
_BLOCK_TRADE_CHUNK_TIMEOUT = 15
_BLOCK_TRADE_CALENDAR_TIMEOUT = 10
_BLOCK_TRADE_MAX_RETRIES = 2
_BLOCK_TRADE_TOTAL_TIMEOUT = 45
_BLOCK_TRADE_TIMEOUT_USER_MESSAGE = (
    "抓取超时：45秒内未拿到完整结果。通常是当前网络较慢，"
    "或 VPN/代理影响了国内数据源；可稍后重试，必要时临时关闭 VPN 后再刷新。"
)
_AKSHARE_FETCH_SNIPPET = r"""
import json
import sys
import pandas as pd
import akshare as ak

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

mode = sys.argv[1]
if mode == "calendar":
    df = ak.tool_trade_date_hist_sina()
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    print(json.dumps(df['trade_date'].tolist(), ensure_ascii=False))
elif mode == "block_trade":
    start_date = sys.argv[2]
    end_date = sys.argv[3]
    df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        print("[]")
    else:
        print(df.to_json(orient="records", force_ascii=False, date_format="iso"))
"""


def _run_domestic_akshare(mode: str, *args, timeout: int = 15):
    env = build_domestic_process_env(extra={"PYTHONIOENCODING": "utf-8"})
    cmd = [sys.executable, "-c", _AKSHARE_FETCH_SNIPPET, mode, *args]
    proc = run_process(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        env=env,
        creationflags=windows_no_window_creationflags(),
        check=True,
    )
    payload = (proc.stdout or "").strip()
    if not payload:
        return []
    return json.loads(payload)


def _raise_block_trade_timeout(stage: str, detail: str = ""):
    extra = f"（{detail}）" if detail else ""
    raise UserFacingTaskError(
        _BLOCK_TRADE_TIMEOUT_USER_MESSAGE,
        f"大宗交易抓取超时：{stage}{extra}，45秒内未完成全部请求，"
        "可能是国内数据源响应慢或网络代理影响。",
    )


def _format_incomplete_message(timeout_chunks, failed_chunks):
    parts = []
    if timeout_chunks:
        parts.append(f"{len(timeout_chunks)} 个区间超时")
    if failed_chunks:
        parts.append(f"{len(failed_chunks)} 个区间失败")
    if not parts:
        return ""
    return "；" + "，".join(parts) + "，结果可能不完整"


def _normalize_trade_date_value(value) -> str:
    if value is None or pd.isna(value):
        return ""

    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""

    if text.isdigit():
        parsed = pd.NaT
        if len(text) == 13:
            parsed = pd.to_datetime(int(text), unit="ms", errors="coerce")
        elif len(text) == 10:
            parsed = pd.to_datetime(int(text), unit="s", errors="coerce")
        elif len(text) == 8:
            parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
        return text

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%Y-%m-%d")
    return text


def _normalize_trade_date_series(series: pd.Series) -> pd.Series:
    return series.apply(_normalize_trade_date_value)

class ForeignBlockTradeTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._cap_cache = {}
        self._is_loading = False
        self._block_trade_codes = []
        self._last_success_at = None
        self._status_primary = "等待加载"
        self._status_segments = ()
        self._status_freshness = ""
        self._status_next_step = ""
        self._had_rows_before_refresh = False
        self._last_auto_refresh_date = ""
        self._pending_auto_refresh_date = ""

        self.days_to_fetch = 20  # 默认拉取最近20个交易日
        self._init_ui()
        self._load_local_cache()
        self._start_auto_scheduler()

        # 大宗交易页只消费 F5/本地快照，不加入盘中实时行情轮询。
        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

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
        self.cmb_days.addItems(["近 10 交易日", "近 20 交易日", "近 40 交易日", "近 60 交易日"])
        # 默认选中 20 交易日，与 self.days_to_fetch 初始值保持一致
        self.cmb_days.setCurrentIndex(1)
        self.cmb_days.setFixedWidth(148)
        self.cmb_days.currentIndexChanged.connect(self._on_days_changed)

        self._refresh_filter_button_text(self.cmb_filter_date, "日期", "全部")
        self._refresh_filter_button_text(self.cmb_filter_branch, "席位", "全部")
        self._refresh_filter_button_text(self.cmb_filter_direction, "动作", "全部")

        filter_widgets = [
            self.cmb_filter_date, self.cmb_filter_branch,
            self.cmb_filter_direction, self.search_box, self.cmb_days
        ]

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.run_post_online_refresh)

        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("外资大宗", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "交易日期", "交易详情",
            "当日收盘价", "成交价格", "折/溢价率(%)", "成交数量(万股)", "成交金额(万元)",
            "买方营业部", "卖方营业部"
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
        days_map = {0: 10, 1: 20, 2: 40, 3: 60}
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
        if (
            last_success_at is not None
            and last_success_at.date() == now.date()
            and last_success_at.hour >= 20
        ):
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
        for row in (row_data or []):
            if not isinstance(row, dict):
                continue
            raw_branches.add(str(row.get("买方营业部", "") or "").strip())
            raw_branches.add(str(row.get("卖方营业部", "") or "").strip())
        branches = sorted(
            branch
            for branch in raw_branches
            if branch and any(keyword in branch for keyword in FOREIGN_KEYWORDS)
        )
        return dates, branches

    def _apply_row_data(self, row_data: list[dict], *, preserve_selection: bool = True):
        unique_dates, unique_branches = self._extract_cache_filter_options(row_data)
        self.cmb_filter_date.set_options(unique_dates, preserve_selection=preserve_selection)
        self.cmb_filter_branch.set_options(unique_branches, preserve_selection=preserve_selection)
        self._refresh_filter_button_text(self.cmb_filter_date, "日期", "全部")
        self._refresh_filter_button_text(self.cmb_filter_branch, "席位", "全部")
        self._block_trade_codes = list(
            dict.fromkeys(
                row.get("代码", "")
                for row in (row_data or [])
                if isinstance(row, dict) and str(row.get("代码", "")).strip()
            )
        )
        self.model.update_data(row_data or [])
        return unique_dates, unique_branches

    def _build_cache_payload(self, row_data: list[dict]) -> dict:
        latest_trade_date = ""
        if row_data:
            latest_trade_date = max(
                [
                    str(row.get("交易日期", "")).strip()
                    for row in row_data
                    if isinstance(row, dict) and str(row.get("交易日期", "")).strip()
                ],
                default="",
            )
        return {
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "days_to_fetch": int(self.days_to_fetch),
            "latest_trade_date": latest_trade_date,
            "rows": row_data or [],
        }

    def _save_local_cache(self, row_data: list[dict]) -> bool:
        try:
            os.makedirs(os.path.dirname(_BLOCK_TRADE_CACHE_FILE), exist_ok=True)
            save_json_file(_BLOCK_TRADE_CACHE_FILE, self._build_cache_payload(row_data))
            log.info(
                f"[外资大宗] 已保存本地缓存: {os.path.basename(_BLOCK_TRADE_CACHE_FILE)} "
                f"(rows={len(row_data or [])}, latest={self._latest_trade_date_text() or '-'})"
            )
            return True
        except CacheIOError as exc:
            log.warning(f"[外资大宗] 保存本地缓存失败: {exc}")
            return False

    def _load_local_cache(self):
        try:
            payload = load_json_file(_BLOCK_TRADE_CACHE_FILE)
            rows = payload.get("rows", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise DataFormatError("block trade cache rows invalid")
            latest_trade_date = str(payload.get("latest_trade_date", "")).strip() if isinstance(payload, dict) else ""
            saved_at = str(payload.get("saved_at", "")).strip() if isinstance(payload, dict) else ""
            try:
                self._last_success_at = datetime.datetime.fromisoformat(saved_at) if saved_at else None
            except ValueError:
                self._last_success_at = None

            unique_dates, unique_branches = self._apply_row_data(rows, preserve_selection=False)
            if rows:
                self._set_fetch_status(
                    "已加载本地缓存",
                    self._status_metric("命中 ", len(rows), "笔"),
                    self._status_metric("日期 ", len(unique_dates)),
                    self._status_metric("席位 ", len(unique_branches)),
                    freshness=f"快照 {latest_trade_date or self._latest_trade_date_text()}",
                    next_step="等待20:00自动更新",
                )
                if hasattr(self, "table_state"):
                    self.table_state.show_table()
                self._apply_latest_quotes_from_store()
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
            event_bus.sig_block_trade_updated.emit()
        except (CacheIOError, DataFormatError) as exc:
            log.debug(f"[外资大宗] 本地缓存不可用，跳过加载: {exc}")
            self._set_fetch_status("等待20:00更新", freshness="待刷新", next_step="等待每日自动缓存")

    def _start_auto_scheduler(self):
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._check_auto_refresh)
        self._auto_timer.start(5 * 60 * 1000)
        self._auto_initial_check_timer = QTimer(self)
        self._auto_initial_check_timer.setSingleShot(True)
        self._auto_initial_check_timer.timeout.connect(self._check_auto_refresh)
        self._auto_initial_check_timer.start(10_000)

    def _cleanup_runtime_state(self):
        auto_timer = getattr(self, "_auto_timer", None)
        if auto_timer is not None:
            auto_timer.stop()
        initial_timer = getattr(self, "_auto_initial_check_timer", None)
        if initial_timer is not None:
            initial_timer.stop()
        try:
            event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
        except (TypeError, RuntimeError):
            pass
        super()._cleanup_runtime_state()

    def shutdown(self) -> None:
        self._cleanup_runtime_state()

    def _check_auto_refresh(self):
        if self._is_loading or task_manager.is_active_task(_FOREIGN_BLOCK_TRADE_TASK):
            return

        now = MarketCalendar.now("CN")
        today_compact = now.strftime("%Y%m%d")
        if (
            self._last_success_at is not None
            and self._last_success_at.date() == now.date()
            and self._last_success_at.hour >= 20
        ):
            self._last_auto_refresh_date = today_compact

        is_trade_day = MarketCalendar.is_trade_day(now.date(), market="CN")
        if not self._should_trigger_auto_refresh(
            now,
            is_trade_day=is_trade_day,
            last_auto_refresh_date=self._last_auto_refresh_date,
            last_success_at=self._last_success_at,
            pending_auto_refresh_date=self._pending_auto_refresh_date,
        ):
            return

        self._pending_auto_refresh_date = today_compact
        event_bus.sig_system_log.emit("info", self._ensure_log_line(f"[外资大宗] 触发每日20:00自动刷新: {today_compact}"))
        self._load_block_trade_data()

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
        buyer_str = str(buyer) if pd.notna(buyer) else ""
        seller_str = str(seller) if pd.notna(seller) else ""

        return any(kw in buyer_str or kw in seller_str for kw in FOREIGN_KEYWORDS)

    def _determine_direction(self, buyer, seller):
        """判断外资买卖动作"""
        buyer_str = str(buyer) if pd.notna(buyer) else ""
        seller_str = str(seller) if pd.notna(seller) else ""

        buy_foreign = any(kw in buyer_str for kw in FOREIGN_KEYWORDS)
        sell_foreign = any(kw in seller_str for kw in FOREIGN_KEYWORDS)

        if buy_foreign and sell_foreign:
            return "外资对倒", "#F59E0B"

        if buy_foreign:
            return "外资买入", COLOR_RISE
        if sell_foreign:
            return "外资卖出", COLOR_FALL

        return "--", COLOR_FLAT

    def _load_block_trade_data(self):
        if self._is_loading or task_manager.is_active_task(_FOREIGN_BLOCK_TRADE_TASK):
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
        self._block_trade_codes = []
        # 清空上一轮的K线缓存，防止跨交易日窗口后内存只增不减
        _kline_cache.clear()

        def _fetch_task():
            import time

            end_dt = datetime.datetime.now()
            deadline = time.monotonic() + _BLOCK_TRADE_TOTAL_TIMEOUT

            # 使用交易日历倒推 start_dt
            try:
                remaining = max(5, int(deadline - time.monotonic()))
                dates = [
                    pd.to_datetime(d).date()
                    for d in _run_domestic_akshare(
                        "calendar",
                        timeout=min(_BLOCK_TRADE_CALENDAR_TIMEOUT, remaining)
                    )
                ]
                today_date = end_dt.date()
                past_dates = [d for d in dates if d <= today_date]
                if len(past_dates) >= self.days_to_fetch:
                    start_date_val = past_dates[-self.days_to_fetch]
                else:
                    start_date_val = past_dates[0] if past_dates else (today_date - datetime.timedelta(days=self.days_to_fetch))
                start_dt = datetime.datetime.combine(start_date_val, datetime.time())
            except ProcessTimeoutError:
                log.warning("[大宗交易] 获取交易日历超时，回退到自然日估算")
                start_dt = end_dt - datetime.timedelta(days=int(self.days_to_fetch * 1.5))
            except (json.JSONDecodeError, OSError, RuntimeError, ProcessExecutionError, TypeError, ValueError) as e:
                log.warning(f"获取交易日历失败，使用自然日重估: {e}")
                start_dt = end_dt - datetime.timedelta(days=int(self.days_to_fetch * 1.5))

            results = []
            timeout_chunks = []
            failed_chunks = []
            finished_chunks = 0

            # 分段拉取：东财接口对大日期范围会截断或断连，每次只拉15天
            CHUNK_DAYS = 15
            cursor = start_dt
            while cursor < end_dt:
                if time.monotonic() >= deadline:
                    _raise_block_trade_timeout("总耗时超限")

                chunk_end = min(cursor + datetime.timedelta(days=CHUNK_DAYS), end_dt)
                s_str = cursor.strftime("%Y%m%d")
                e_str = chunk_end.strftime("%Y%m%d")
                chunk_key = f"{s_str}-{e_str}"
                chunk_done = False
                chunk_timed_out = False

                for attempt in range(_BLOCK_TRADE_MAX_RETRIES):
                    remaining = int(deadline - time.monotonic())
                    if remaining <= 0:
                        _raise_block_trade_timeout("分段抓取", chunk_key)
                    try:
                        records = _run_domestic_akshare(
                            "block_trade",
                            s_str,
                            e_str,
                            timeout=min(_BLOCK_TRADE_CHUNK_TIMEOUT, remaining),
                        )
                        df = pd.DataFrame(records) if records else pd.DataFrame()
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                if self._should_include_row(row.get('买方营业部'), row.get('卖方营业部')):
                                    results.append(row.to_dict())
                        chunk_done = True
                        finished_chunks += 1
                        break
                    except ProcessTimeoutError:
                        chunk_timed_out = True
                        log.warning(f"[大宗交易] {chunk_key} 请求超时，可能是国内数据源响应慢或当前网络代理影响")
                        if attempt < _BLOCK_TRADE_MAX_RETRIES - 1:
                            time.sleep(1)
                    except (json.JSONDecodeError, OSError, RuntimeError, ProcessExecutionError, TypeError, ValueError) as e:
                        log.warning(f"[大宗交易] {chunk_key} 第{attempt+1}次失败: {e}")
                        if attempt < _BLOCK_TRADE_MAX_RETRIES - 1:
                            time.sleep(1)

                if not chunk_done:
                    if time.monotonic() >= deadline:
                        _raise_block_trade_timeout("分段重试后仍未完成", chunk_key)
                    if chunk_timed_out:
                        timeout_chunks.append(chunk_key)
                    else:
                        failed_chunks.append(chunk_key)

                cursor = chunk_end + datetime.timedelta(days=1)

            if not results and failed_chunks and finished_chunks == 0:
                raise UserFacingTaskError(
                    "抓取失败：本轮未拿到有效结果。通常是国内数据源响应慢或网络较差；可稍后重试。",
                    "大宗交易抓取失败：所有分段均未返回有效结果。",
                )

            return {
                "records": results,
                "timeout_chunks": timeout_chunks,
                "failed_chunks": failed_chunks,
            }

        task_manager.run_in_background(
            _fetch_task,
            task_id=_FOREIGN_BLOCK_TRADE_TASK.task_id,
            on_success=self._on_data_fetched,
            on_error=self._on_data_fetch_failed
        )

    def run_post_online_refresh(self) -> bool:
        self._load_block_trade_data()
        return True

    def refresh_data_after_f5(self) -> bool:
        self._load_local_cache()
        return False

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
            timeout_chunks = payload.get("timeout_chunks", [])
            failed_chunks = payload.get("failed_chunks", [])
        else:
            data_list = payload
            timeout_chunks = []
            failed_chunks = []

        if not data_list:
            self._block_trade_codes = []
            if self._pending_auto_refresh_date and not timeout_chunks and not failed_chunks:
                self._last_auto_refresh_date = self._pending_auto_refresh_date
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
                if self._pending_auto_refresh_date:
                    self._last_auto_refresh_date = self._pending_auto_refresh_date
            event_bus.sig_block_trade_updated.emit()
            self._pending_auto_refresh_date = ""
            return

        df = pd.DataFrame(data_list)
        if '交易日期' in df.columns:
            df['交易日期'] = _normalize_trade_date_series(df['交易日期'])

        # 按照 (交易日期, 股票代码, 买方营业部, 卖方营业部) 分组汇总，合并拆单金额和数量
        # 对于数值类型去求和或者均值，字符去第一条
        df = df.groupby(['交易日期', '证券代码', '买方营业部', '卖方营业部', '证券简称'], as_index=False).agg({
            '收盘价': 'first',
            '成交价': 'mean',
            '折溢率': 'mean',
            '成交量': 'sum',
            '成交额': 'sum'
        })
        df = df.sort_values(by=['交易日期', '证券代码'], ascending=[False, True])
        row_data = []
        for row, (_, record) in enumerate(df.iterrows()):
            trade_date = str(record.get("交易日期", ""))
            code = str(record.get("证券代码", "")).zfill(6)
            name = str(record.get("证券简称", ""))

            close_price = record.get("收盘价", 0)
            trade_price = record.get("成交价", 0)
            premium = record.get("折溢率", 0)
            vol = record.get("成交量", 0)
            amt = record.get("成交额", 0)

            close_price = 0 if pd.isna(close_price) else close_price
            trade_price = 0 if pd.isna(trade_price) else trade_price
            premium = 0 if pd.isna(premium) else premium
            vol = 0 if pd.isna(vol) else vol
            amt = 0 if pd.isna(amt) else amt

            premium_pct = premium * 100
            vol_wan = vol / 10000.0
            amt_wan = amt / 10000.0

            buyer = str(record.get("买方营业部", ""))
            seller = str(record.get("卖方营业部", ""))

            direction, _ = self._determine_direction(buyer, seller)

            row_dict = {
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "交易日期": trade_date,
                "交易详情": direction,
                "当日收盘价": f"{close_price:.2f}" if close_price else "--",
                "成交价格": f"{trade_price:.2f}" if trade_price else "--",
                "折/溢价率(%)": f"{premium_pct:.2f}%" if not pd.isna(premium_pct) else "--",
                "成交数量(万股)": f"{vol_wan:.2f}",
                "成交金额(万元)": f"{amt_wan:.2f}",
                "买方营业部": buyer,
                "卖方营业部": seller
            }
            row_data.append(row_dict)

        unique_dates, unique_branches = self._apply_row_data(row_data)
        self._last_success_at = datetime.datetime.now()
        self._set_fetch_status(
            self._status_metric("命中 ", len(df), "笔"),
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
            if self._pending_auto_refresh_date:
                self._last_auto_refresh_date = self._pending_auto_refresh_date
        elif timeout_chunks or failed_chunks:
            log.warning("[外资大宗] 本轮结果不完整，已跳过覆盖本地缓存")
        self._pending_auto_refresh_date = ""

        # 强制应用当前的筛选状态
        self._filter_table_combo()
        event_bus.sig_block_trade_updated.emit()

        self._apply_latest_quotes_from_store()

    def _on_data_fetch_failed(self, error_message: str):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        self._pending_auto_refresh_date = ""
        msg = str(error_message or "").strip()
        if not msg:
            msg = "大宗交易抓取失败，请稍后重试。"
        elif not msg.startswith(("抓取超时", "抓取失败")):
            msg = f"大宗交易抓取失败：{msg}"
        self._block_trade_codes = []
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
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return

        code = self.model.row_data[row].get("代码", "")

        # 提取当前表格顺序以传递给K线窗口
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
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if not code: return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name, vcp_data=row_data)
