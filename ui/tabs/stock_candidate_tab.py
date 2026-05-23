# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.stock_candidates_service import StockCandidatesDataService
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from ui.components import TableStateWrapper, VCPTableView
from ui.components.stock_detail_dialog import signal_source_label
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab
from ui.workspaces.stock_signal import StockSignal


class StockCandidateTab(BaseStockTab):
    HEADER_STATE_KEY = "header_state_stock_candidates_v2"
    AUTO_REFRESH_DEBOUNCE_MS = 500
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
        self._candidate_service = StockCandidatesDataService(
            context_reader=self._read_stock_context,
            row_builder=self._build_candidate_rows,
            provider_status_reader=self._read_provider_status,
        )
        self._last_candidate_result = None
        self._last_candidate_signature = ""
        self._init_ui()
        self.subscribe_global_quotes(self.model)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.setInterval(self.AUTO_REFRESH_DEBOUNCE_MS)
        self._auto_refresh_timer.timeout.connect(self.refresh_candidates)
        self._connect_auto_refresh_events()
        self._initial_refresh_started = False

    def _ensure_runtime_started(self) -> None:
        if self._initial_refresh_started:
            return
        self._initial_refresh_started = True
        QTimer.singleShot(350, self.refresh_candidates)

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

    def _should_start_runtime_on_show(self) -> bool:
        return BaseStockTab._should_start_interactive_runtime_on_show(self)

    def showEvent(self, event):  # noqa: N802 - Qt API naming
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()

    def prime_background_load(self) -> None:
        workspace = self._workspace()
        self._prime_anchor_source_tabs(workspace)
        prime_snapshots = getattr(workspace, "prime_stock_context_snapshots", None)
        if callable(prime_snapshots):
            prime_snapshots()

    def _prime_anchor_source_tabs(self, workspace) -> None:
        if workspace is None:
            return
        for key in ("na_daily", "ai_industry_chain"):
            tab = self._load_anchor_source_tab(workspace, key)
            self._prime_anchor_source_tab(key, tab)

    @staticmethod
    def _load_anchor_source_tab(workspace, key: str):
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        tab = get_loaded_tab(key) if callable(get_loaded_tab) else None
        if tab is not None:
            return tab

        ensure_tab_loaded = getattr(workspace, "ensure_tab_loaded", None)
        if callable(ensure_tab_loaded):
            try:
                return ensure_tab_loaded(key, reason="stock_candidates_anchor")
            except TypeError:
                return ensure_tab_loaded(key)

        get_tab = getattr(workspace, "get_tab", None)
        return get_tab(key) if callable(get_tab) else None

    @staticmethod
    def _prime_anchor_source_tab(key: str, tab) -> None:
        if tab is None:
            return
        method_names = {
            "na_daily": (
                "prime_background_load",
                "run_post_online_refresh",
                "_load_na_daily_report",
                "_ensure_runtime_started",
            ),
            "ai_industry_chain": ("prime_background_load", "_load_chain_data", "_ensure_runtime_started"),
        }.get(key, ("prime_background_load", "_ensure_runtime_started"))
        for method_name in method_names:
            method = getattr(tab, method_name, None)
            if not callable(method):
                continue
            method()
            return

    def _connect_auto_refresh_events(self) -> None:
        for signal in (
            event_bus.sig_cache_reload_completed,
            event_bus.sig_na_daily_updated,
            event_bus.sig_ai_industry_chain_updated,
            event_bus.sig_block_trade_updated,
            event_bus.sig_earnings_updated,
            event_bus.sig_lhb_pool_updated,
            event_bus.sig_scan_updated,
            event_bus.sig_fund_holdings_updated,
            event_bus.sig_stock_context_snapshot_updated,
            event_bus.sig_watchlist_changed,
        ):
            signal.connect(self._schedule_context_refresh)

    def _schedule_context_refresh(self, *_args) -> None:
        if getattr(self, "_workspace_noninteractive_loaded", False):
            return
        self._status_primary = "等待综合候选自动刷新"
        self._status_freshness = "数据源已更新"
        self._refresh_status()
        self._auto_refresh_timer.start(self.AUTO_REFRESH_DEBOUNCE_MS)

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
        self.model.set_muted_text_headers(["共振分", "来源数", "信号数", "来源", "核心信号", "最近时间"])
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

    def _read_stock_context(self):
        workspace = self._workspace()
        context_reader = getattr(workspace, "collect_stock_context", None)
        return context_reader() if callable(context_reader) else {}

    def _read_provider_status(self) -> dict:
        provider = getattr(self, "data_provider", None)
        request_stats = {}
        runtime_stats = {}

        request_getter = getattr(provider, "get_quote_request_stats", None)
        if callable(request_getter):
            try:
                request_stats = request_getter() or {}
            except (AttributeError, RuntimeError, TypeError, ValueError):
                request_stats = {}

        runtime_getter = getattr(provider, "get_realtime_runtime_stats", None)
        if callable(runtime_getter):
            try:
                runtime_stats = runtime_getter() or {}
            except (AttributeError, RuntimeError, TypeError, ValueError):
                runtime_stats = {}

        return {
            "request_stats": request_stats,
            "runtime_stats": runtime_stats,
            "eastmoney_cooldown_until": float(getattr(provider, "_rt_eastmoney_cooldown_until", 0.0) or 0.0),
            "eastmoney_last_error": str(getattr(provider, "_rt_eastmoney_last_error", "") or ""),
        }

    def get_data_lineage(self) -> dict:
        result = self._last_candidate_result
        if result is None:
            return self._candidate_service.empty_lineage(
                row_count=len(getattr(self.model, "row_data", None) or [])
            ).as_dict()
        return result.lineage.as_dict()

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
    def _signal_sector(signal: StockSignal) -> str:
        payload = dict(signal.payload or {})
        for key in ("细分板块", "细分环节", "行业", "板块", "热门板块", "热点板块", "subsector"):
            value = payload.get(key)
            if StockCandidateTab._is_quote_value(value):
                return str(value).strip()
        if str(signal.signal_type or "").strip() == "subsector" and StockCandidateTab._is_quote_value(signal.summary):
            return str(signal.summary).strip()
        return ""

    @staticmethod
    def _candidate_sector(signals: list[StockSignal]) -> str:
        for source_tab in ("ai_industry_chain", "na_daily"):
            for signal in signals:
                if str(signal.source_tab or "").strip() != source_tab:
                    continue
                sector = StockCandidateTab._signal_sector(signal)
                if sector:
                    return sector
        return ""

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
            sector_text = StockCandidateTab._candidate_sector(clean_signals)
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
            score_type_count = len(
                {
                    str(signal.signal_type or "").strip()
                    for signal in clean_signals
                    if str(signal.signal_type or "").strip()
                }
            )
            score = len(sources) * 10 + len(clean_signals) + score_type_count

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
                    "细分板块": sector_text,
                    "_signals": clean_signals,
                }
            )

        rows.sort(key=lambda row: (int(row.get("共振分", 0) or 0), int(row.get("来源数", 0) or 0)), reverse=True)
        return rows

    def refresh_candidates(self):
        result = self._candidate_service.load()
        self._last_candidate_result = result
        rows = result.rows
        if result.signature != self._last_candidate_signature:
            self.model.update_data(rows)
            self._last_candidate_signature = result.signature
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
        self.set_proxy_filter_text(self.proxy_model, text)
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
