# -*- coding: utf-8 -*-
"""AI产业链独立 Tab 组件。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_task_service import task_registry
from core.ai_industry_chain_pool import (
    AI_CHAIN_FILE,
    PLACEHOLDER,
    cell_text,
    load_ai_industry_chain_rows,
    normalize_ai_chain_code,
)
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


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

    def __init__(self, data_provider, parent=None, workbook_path: str | Path | None = None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.workbook_path = Path(workbook_path) if workbook_path is not None else AI_CHAIN_FILE
        self._chain_codes: set[str] = set()
        self._status_primary = "等待AI产业链"
        self._status_segments: list[str] = []
        self._status_freshness = ""
        self._status_next_step = ""
        self._runtime_started = False
        self._background_prime_loading = False
        self._background_prime_done = False

        self._init_ui()
        self.subscribe_global_quotes()

    def _ensure_runtime_started(self):
        if self._runtime_started:
            return
        self._runtime_started = True
        QTimer.singleShot(350, self._load_chain_data)

    def prime_background_load(self):
        if self._runtime_started or self._background_prime_done:
            return
        self._background_prime_loading = True
        try:
            self._load_chain_data()
        finally:
            self._background_prime_loading = False
            self._background_prime_done = True

    def _should_start_runtime_on_show(self) -> bool:
        return BaseStockTab._should_start_interactive_runtime_on_show(self)

    def showEvent(self, event):
        super().showEvent(event)
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

        btn_refresh = QPushButton("刷新")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self._load_chain_data)

        btn_reset = QPushButton("解除排序")
        btn_reset.clicked.connect(self._reset_view)

        toolbar = self.build_tab_toolbar("AI产业链", self.status_label, [self.search_box], [btn_refresh, btn_reset])
        layout.addWidget(toolbar)

        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无AI产业链数据", loading_title="加载中...")

        self.model = StockTableModel(self.COLUMNS)
        self.model.set_plain_style_headers(["细分板块", "备注"])
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        widths = [52, 76, 92, 70, 70, 86, 160, 82, 82, 82, 520]
        for col_idx, width in enumerate(widths):
            if col_idx < len(self.model.headers):
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(col_idx, width)

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

    @staticmethod
    def _cell_text(value) -> str:
        return cell_text(value)

    @classmethod
    def _normalize_code(cls, value) -> str:
        return normalize_ai_chain_code(value)

    def _read_workbook_rows(self) -> list[dict]:
        return load_ai_industry_chain_rows(self.workbook_path)

    @staticmethod
    def _coerce_pandas_frame(frame):
        if frame is None:
            return None
        if hasattr(frame, "to_pandas"):
            frame = frame.to_pandas()
        if hasattr(frame, "columns"):
            return frame
        return None

    @staticmethod
    def _period_returns_from_frame(frame) -> dict[int, float | None]:
        frame = AIIndustryChainTab._coerce_pandas_frame(frame)
        if frame is None or not hasattr(frame, "columns") or len(frame) == 0:
            return {period: None for period in AIIndustryChainTab.PERIOD_COLUMNS}

        close_col = "close" if "close" in frame.columns else ("收盘" if "收盘" in frame.columns else "")
        if not close_col:
            return {period: None for period in AIIndustryChainTab.PERIOD_COLUMNS}

        try:
            if hasattr(frame, "sort_index"):
                frame = frame.sort_index()
        except (AttributeError, TypeError, ValueError):
            pass

        try:
            closes = frame[close_col].dropna()
            closes = closes.astype(float)
        except (AttributeError, TypeError, ValueError):
            return {period: None for period in AIIndustryChainTab.PERIOD_COLUMNS}

        if len(closes) == 0:
            return {period: None for period in AIIndustryChainTab.PERIOD_COLUMNS}

        latest = float(closes.iloc[-1])
        result = {}
        for period in AIIndustryChainTab.PERIOD_COLUMNS:
            if len(closes) <= period:
                result[period] = None
                continue
            base = float(closes.iloc[-period - 1])
            if base <= 0 or latest <= 0:
                result[period] = None
            else:
                result[period] = (latest / base - 1.0) * 100.0
        return result

    def _apply_period_returns(self, rows: list[dict]) -> None:
        if not rows or not self.data_provider or not hasattr(self.data_provider, "get_data"):
            return

        returns_by_code: dict[str, dict[int, float | None]] = {}
        for code in dict.fromkeys(str(row.get("代码", "")).strip() for row in rows):
            if not code:
                continue
            try:
                returns_by_code[code] = self._period_returns_from_frame(self.data_provider.get_data(code))
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[AI产业链] 计算 {code} 区间涨幅失败: {exc}")
                returns_by_code[code] = {period: None for period in self.PERIOD_COLUMNS}

        for row in rows:
            code = str(row.get("代码", "")).strip()
            period_returns = returns_by_code.get(code) or {}
            for period, column in self.PERIOD_COLUMNS.items():
                value = period_returns.get(period)
                row[column] = PLACEHOLDER if value is None else value

    def _workbook_freshness(self) -> str:
        try:
            stamp = self.workbook_path.stat().st_mtime
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return "待加载"

        from datetime import datetime

        return "Excel " + datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    def _load_chain_data(self):
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在加载AI产业链...", "读取Excel并回填本地行情")
        self._set_chain_status("AI产业链刷新中", freshness=self._workbook_freshness(), next_step="读取Excel")

        try:
            rows = self._read_workbook_rows()
            if not self._background_prime_loading:
                self._apply_period_returns(rows)
        except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
            message = str(exc)
            self.model.update_data([])
            self._chain_codes = set()
            self._set_chain_status("AI产业链加载失败", message, freshness="读取失败", next_step="检查Excel文件")
            if hasattr(self, "table_state"):
                self.table_state.show_error(
                    "AI产业链加载失败",
                    message,
                    meta=str(self.workbook_path),
                    action_text="重新尝试",
                    action_callback=self._load_chain_data,
                )
            return

        self.model.update_data(rows)
        self._chain_codes = {row.get("代码", "") for row in rows if row.get("代码")}
        if rows:
            self.table_state.show_table()
            self._set_chain_status(
                "AI产业链已就绪",
                self._status_metric("标的 ", len(self._chain_codes), "只"),
                self._status_metric("映射 ", len(rows), "条"),
                freshness=self._workbook_freshness(),
                next_step="",
            )
            if self._background_prime_loading:
                self._apply_quote_store_snapshot()
            else:
                self.refresh_table_quotes_and_market_caps(
                    quote_task_id=task_registry.quote_refresh("ai_industry_chain").task_id
                )
            event_bus.sig_ai_industry_chain_updated.emit()
        else:
            self.table_state.show_empty("暂无AI产业链数据")
            self._set_chain_status("AI产业链为空", freshness=self._workbook_freshness(), next_step="检查Excel内容")

    def refresh_table_from_latest_snapshot(self, current_model=None, *, async_local: bool = True):
        super().refresh_table_from_latest_snapshot(current_model=current_model, async_local=async_local)
        rows = getattr(self.model, "row_data", None) or []
        self._apply_period_returns(rows)
        if rows:
            self.model.update_data(list(rows))

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
