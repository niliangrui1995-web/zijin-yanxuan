# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ui_runtime_service import domain_events as event_bus
from app.services.ui_runtime_service import ui_signals
from ui.components import TableStateWrapper, VCPTableView
from ui.components.stock_detail_dialog import signal_source_label
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab
from ui.workspaces.stock_signal import StockSignal


class StockCandidateTab(BaseStockTab):
    HEADER_STATE_KEY = "header_state_stock_candidates_v2"
    REQUIRED_SOURCE_TABS = frozenset({"ai_industry_chain", "na_daily"})
    ANCHOR_SOURCE_GROUP = "ai_na_anchor"
    COLUMNS = [
        "代码",
        "名称",
        "市价",
        "涨幅%",
        "市值",
        "共振分",
        "来源数",
        "信号数",
        "来源",
        "核心信号",
        "最近时间",
    ]

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._status_primary = "等待综合候选"
        self._status_freshness = "待刷新"
        self._init_ui()
        self.subscribe_global_quotes(self.model)
        event_bus.sig_cache_reload_completed.connect(self.refresh_candidates)
        QTimer.singleShot(2600, self.refresh_candidates)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.lbl_status = QLabel("未刷新")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、来源或信号...")
        self.search_box.setMinimumWidth(180)
        self.search_box.setMaximumWidth(280)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        btn_refresh = QPushButton("刷新")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_candidates)

        toolbar = self.build_tab_toolbar("综合候选", self.lbl_status, [self.search_box], [btn_refresh])
        layout.addWidget(toolbar)

        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无综合候选", loading_title="刷新中...")

        self.model = StockTableModel(self.COLUMNS)
        self.model.set_plain_style_headers(["来源", "核心信号", "最近时间"])
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.table.setItemDelegate(StockItemDelegate(self.table))

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [52, 72, 76, 70, 72, 78, 70, 58, 58, 150, 430, 92]
        for i, width in enumerate(default_widths):
            if i < len(self.model.headers):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(i, width)
        try:
            header.setSectionResizeMode(self.model.headers.index("核心信号"), QHeaderView.ResizeMode.Stretch)
        except ValueError:
            pass

        restored_sort = self.bind_header_persistence(self.table, self.HEADER_STATE_KEY)
        if not restored_sort:
            try:
                score_col = self.model.headers.index("共振分")
                self.table.sortByColumn(score_col, Qt.SortOrder.DescendingOrder)
            except ValueError:
                pass

        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)
        self._refresh_status()

    def _workspace(self):
        cursor = self.parent()
        while cursor is not None:
            if hasattr(cursor, "collect_stock_context"):
                return cursor
            cursor = cursor.parent() if hasattr(cursor, "parent") else None
        return None

    @staticmethod
    def _signal_time(signal: StockSignal) -> str:
        return str(signal.observed_at or signal.refreshed_at or "").strip()

    @staticmethod
    def _signal_name(signal: StockSignal) -> str:
        name = str(signal.name or "").strip()
        if name:
            return name
        payload = dict(signal.payload or {})
        return str(payload.get("名称") or payload.get("name") or "").strip()

    @staticmethod
    def _is_quote_value(value) -> bool:
        text = str(value if value is not None else "").strip()
        return text not in {"", "--", "-", "None", "nan", "NaN"}

    @staticmethod
    def _first_payload_value(signals: list[StockSignal], keys: tuple[str, ...]) -> str:
        for signal in signals:
            payload = dict(signal.payload or {})
            for key in keys:
                value = payload.get(key)
                if StockCandidateTab._is_quote_value(value):
                    return str(value).strip()
        return "--"

    @staticmethod
    def _candidate_summary(signal: StockSignal) -> str:
        signal_type = str(signal.signal_type or "").strip()
        source_tab = str(signal.source_tab or "").strip()
        if signal_type == "vcp_scan" or source_tab == "scan":
            payload = dict(signal.payload or {})
            trigger_date = str(payload.get("触发日期") or signal.observed_at or "").strip()
            rps = str(payload.get("RPS强度") or "").strip()
            parts = []
            if trigger_date:
                parts.append(f"触发日期 {trigger_date}")
            if rps:
                parts.append(f"RPS {rps}")
            return " | ".join(parts) or "VCP扫描命中"
        return str(signal.summary or "").strip()

    @staticmethod
    def _source_group_key(signal: StockSignal) -> str:
        source_tab = str(signal.source_tab or "").strip()
        if source_tab in StockCandidateTab.REQUIRED_SOURCE_TABS:
            return StockCandidateTab.ANCHOR_SOURCE_GROUP
        return source_tab

    @staticmethod
    def _effective_signal_count(signals: list[StockSignal]) -> int:
        count = 0
        anchor_seen = False
        for signal in signals:
            source_tab = str(signal.source_tab or "").strip()
            if source_tab in StockCandidateTab.REQUIRED_SOURCE_TABS:
                if anchor_seen:
                    continue
                anchor_seen = True
            count += 1
        return count

    @staticmethod
    def _effective_type_count(signals: list[StockSignal]) -> int:
        type_keys = []
        for signal in signals:
            source_tab = str(signal.source_tab or "").strip()
            if source_tab in StockCandidateTab.REQUIRED_SOURCE_TABS:
                type_key = StockCandidateTab.ANCHOR_SOURCE_GROUP
            else:
                type_key = str(signal.signal_type or "").strip()
            if type_key and type_key not in type_keys:
                type_keys.append(type_key)
        return len(type_keys)

    def _build_candidate_rows(self, context: dict[str, list[StockSignal]]) -> list[dict]:
        rows = []
        workspace = self._workspace()
        tab_titles = {}
        if workspace is not None and hasattr(workspace, "tab_specs"):
            tab_titles = {
                str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip()
                for spec in workspace.tab_specs()
            }

        for code, signals in sorted((context or {}).items()):
            clean_signals = [signal for signal in signals or [] if isinstance(signal, StockSignal)]
            if not clean_signals:
                continue
            if not any(
                str(signal.source_tab or "").strip() in StockCandidateTab.REQUIRED_SOURCE_TABS
                for signal in clean_signals
            ):
                continue

            sources = []
            for signal in clean_signals:
                label = signal_source_label(signal, tab_titles)
                if label and label not in sources:
                    sources.append(label)

            source_groups = []
            for signal in clean_signals:
                group_key = StockCandidateTab._source_group_key(signal)
                if group_key and group_key not in source_groups:
                    source_groups.append(group_key)

            if len(source_groups) < 2:
                continue

            name = next(
                (
                    StockCandidateTab._signal_name(signal)
                    for signal in clean_signals
                    if StockCandidateTab._signal_name(signal)
                ),
                "",
            )
            source_text = "｜".join(sources)
            summaries = []
            for signal in clean_signals:
                text = StockCandidateTab._candidate_summary(signal)
                if text and text not in summaries:
                    summaries.append(text)
                if len(summaries) >= 3:
                    break
            latest_time = max((StockCandidateTab._signal_time(signal) for signal in clean_signals), default="")
            effective_source_count = len(source_groups)
            effective_signal_count = StockCandidateTab._effective_signal_count(clean_signals)
            effective_type_count = StockCandidateTab._effective_type_count(clean_signals)
            score = effective_source_count * 10 + effective_signal_count + effective_type_count

            rows.append(
                {
                    "代码": code,
                    "名称": name or code,
                    "市价": StockCandidateTab._first_payload_value(
                        clean_signals,
                        ("市价", "现价", "最新价", "最新", "收盘"),
                    ),
                    "涨幅%": StockCandidateTab._first_payload_value(
                        clean_signals,
                        ("涨幅%", "涨幅", "涨跌%", "涨跌"),
                    ),
                    "市值": StockCandidateTab._first_payload_value(
                        clean_signals,
                        ("市值", "总市值"),
                    ),
                    "共振分": score,
                    "来源数": effective_source_count,
                    "信号数": effective_signal_count,
                    "来源": source_text,
                    "核心信号": "；".join(summaries),
                    "最近时间": latest_time,
                    "_signals": clean_signals,
                }
            )

        rows.sort(key=lambda row: (int(row.get("共振分", 0) or 0), int(row.get("来源数", 0) or 0)), reverse=True)
        return rows

    def refresh_candidates(self):
        workspace = self._workspace()
        context_reader = getattr(workspace, "collect_stock_context", None)
        context = context_reader() if callable(context_reader) else {}
        rows = self._build_candidate_rows(context)
        self.model.update_data(rows)
        self.refresh_table_from_latest_snapshot(self.model)
        if rows:
            self.table_state.show_table()
            self._status_primary = "综合候选已刷新"
            self._status_freshness = "内存上下文"
        else:
            self.table_state.show_empty("暂无综合候选")
            self._status_primary = "暂无综合候选"
            self._status_freshness = "等待其他Tab刷新"
        self._refresh_status()

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)
        self._refresh_status()

    def _refresh_status(self):
        total = len(getattr(self.model, "row_data", None) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        keyword = str(self.search_box.text() or "").strip() if hasattr(self, "search_box") else ""
        self.lbl_status.setText(
            self.format_workspace_status(
                self._status_primary,
                result=self._status_metric("结果 ", f"{visible}/{total}", "只") if total else "",
                freshness=self._status_freshness,
                current_filter=keyword or "全部",
                next_step="右键查看股票全景" if total else "刷新其他Tab后再汇总",
            )
        )

    def _row_from_proxy_index(self, index):
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row < 0 or row >= len(self.model.row_data):
            return None
        return self.model.row_data[row]

    def _visible_code_list(self, clicked_visual_row: int) -> tuple[list[dict], int]:
        code_list = []
        current_idx = 0
        for visual_row in range(self.proxy_model.rowCount()):
            source_idx = self.proxy_model.mapToSource(self.proxy_model.index(visual_row, 0))
            if source_idx.row() >= len(self.model.row_data):
                continue
            row_dict = dict(self.model.row_data[source_idx.row()] or {})
            code_list.append(row_dict)
            if visual_row == clicked_visual_row:
                current_idx = len(code_list) - 1
        return code_list, current_idx

    def _on_double_click(self, index):
        row = self._row_from_proxy_index(index)
        if not row:
            return
        code = str(row.get("代码") or "").strip()
        if not code:
            return
        code_list, current_idx = self._visible_code_list(index.row())
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

        build_stock_context_menu(self, code, name, vcp_data=row)

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        return super().get_realtime_quote_codes(current_model=current_model or self.model)
