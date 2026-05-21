# -*- coding: utf-8 -*-
from datetime import datetime

import pandas as pd
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import QDialog, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from core.logger import get_logger
from ui.components import MultiSelectFilterButton, TableStateWrapper, VCPTableView, format_multi_select_summary
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.services.earnings_refresh_service import EarningsRefreshService
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)
EARNINGS_DISPLAY_TRADE_DAYS = 10
EARNINGS_TYPE_OPTIONS = ("预告", "快报", "财报")
EarningsScheduler = EarningsRefreshService


class EarningsTab(BaseStockTab):
    """业绩断层与预告高增监控面板"""

    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self.row_data = []
        self._manual_fetch_range: tuple[str, str] | None = None
        self._init_ui()
        self._recalc_pe_timer = QTimer(self)
        self._recalc_pe_timer.setSingleShot(True)
        self._recalc_pe_timer.timeout.connect(self._recalc_pe_ttm)

        # 业绩页只消费 F5/本地快照，不加入盘中实时行情轮询。
        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)

        # 调度器按需创建，避免隐藏页签挂载时加载业绩缓存。
        self.scheduler = None
        self._owns_earnings_service = False

        # 延后到页面首次显示时再加载视图缓存；业务巡检由全局调度器负责。
        self._patrol_started = False

    def _ensure_scheduler(self):
        if self.scheduler is None:
            parent = self.parent()
            host = None
            try:
                host = parent.window() if parent is not None else self.window()
            except RuntimeError:
                host = None
            service = getattr(host, "earnings_refresh_service", None)
            if isinstance(service, EarningsRefreshService):
                self.scheduler = service
            else:
                self.scheduler = EarningsScheduler(parent=self)
            parent_getter = getattr(self.scheduler, "parent", None)
            self._owns_earnings_service = callable(parent_getter) and parent_getter() is self
            self.scheduler.sig_new_surprises_found.connect(self._on_new_data_found)
            self.scheduler.sig_fetch_failed.connect(self._on_fetch_failed)
        return self.scheduler

    def _ensure_runtime_started(self) -> None:
        if self._patrol_started:
            return
        self._patrol_started = True
        scheduler = self._ensure_scheduler()
        if not self.row_data:
            load_cached = getattr(scheduler, "load_cached_records_async", None)
            if callable(load_cached):
                load_cached()

    def _start_scheduler_patrol(self) -> None:
        return None

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("监控挂机中...")

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(160)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        self.btn_manual_fetch = QPushButton("历史回补")
        self.btn_manual_fetch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_fetch.setToolTip("打开区间选择窗口，按日期范围执行历史回补。")
        self.btn_manual_fetch.clicked.connect(self._on_manual_fetch)

        self.type_filter = MultiSelectFilterButton("全看")
        self.type_filter.setFixedWidth(126)
        self.type_filter.set_options(EARNINGS_TYPE_OPTIONS, preserve_selection=False)
        self.type_filter.selectionChanged.connect(self._on_type_filter_changed)
        self._refresh_type_filter_button_text()

        filter_widgets = [self.search_box, self.type_filter]
        action_widgets = [self.btn_manual_fetch]
        toolbar = self.build_tab_toolbar("业绩高增追踪", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # --- 表格显示区 ---
        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无业绩数据", loading_title="加载中...")
        layout.addWidget(self.table_state)

        # 字段映射表：前四列必须是标准列（代码/名称/现价/涨幅%），以便接收盘中广播
        self.header_labels = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "PE(TTM)",
            "环比%",
            "同比%",
            "当季利润",
            "上季利润",
            "报告期",
            "类型",
            "揭晓日",
            "基调",
            "所属行业与概念",
        ]

        self.model = StockTableModel(self.header_labels)
        self.model.set_plain_style_headers(["揭晓日"])
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

        # 右键菜单与双击看K线
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        # 持久化列宽
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        default_widths = [52, 70, 80, 70, 70, 70, 70, 80, 80, 120, 120, 80, 70, 90, 80, 250]
        for i, w in enumerate(default_widths):
            header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)

        restored_sort = self.bind_header_persistence(self.table, "earnings_header_state_v5")
        if not restored_sort:
            self.table.sortByColumn(self.model.headers.index("揭晓日"), Qt.SortOrder.DescendingOrder)

    def _set_window_status(self, primary: str, *segments: str):
        self._status_primary = primary
        self._status_segments = tuple(seg for seg in segments if seg)
        self._refresh_window_status()

    def _latest_disclosure_date(self) -> str:
        dates = [str(row.get("揭晓日", "")).strip() for row in (self.row_data or []) if isinstance(row, dict)]
        dates = [date for date in dates if date]
        return max(dates) if dates else ""

    def _current_filter_summary(self) -> str:
        parts = []
        search_text = self.search_box.text().strip()
        if search_text:
            parts.append(search_text)

        type_text = self._type_filter_status_text()
        if type_text != "全看":
            parts.append(type_text)

        return "｜".join(parts) if parts else "全部"

    def _refresh_window_status(self):
        total = len(getattr(self.model, "row_data", []) or [])
        visible = self.proxy_model.rowCount()
        freshness = f"快照 {self._latest_disclosure_date()}" if total else "待回补"
        self.lbl_status.setText(
            self.format_workspace_status(
                getattr(self, "_status_primary", "业绩监控"),
                result=f"{visible}/{total}只" if total else "0只",
                freshness=freshness,
                current_filter=self._current_filter_summary(),
                next_step="",
                extra_segments=getattr(self, "_status_segments", ()),
            )
        )

    def _on_manual_fetch(self):
        from datetime import timedelta

        from ui.components.scan_dialogs import TradeDateRangeDialog

        default_start = None
        default_end = None
        if self._manual_fetch_range:
            try:
                default_start = datetime.strptime(self._manual_fetch_range[0], "%Y-%m-%d").date()
                default_end = datetime.strptime(self._manual_fetch_range[1], "%Y-%m-%d").date()
            except ValueError:
                default_start = None
                default_end = None

        dialog = TradeDateRangeDialog(
            window_title="业绩历史回补",
            headline="选择历史回补区间",
            hint="按自然日区间补齐业绩公告数据，日历样式与全局交易日历保持一致。",
            confirm_text="开始回补",
            default_start=default_start,
            default_end=default_end,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        start_str, end_str = dialog.selected_range()
        self._manual_fetch_range = (start_str, end_str)

        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")

            # 自动调整起止顺序防呆
            if start_dt > end_dt:
                start_dt, end_dt = end_dt, start_dt

            delta_days = (end_dt - start_dt).days
            date_list = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta_days + 1)]
        except (ValueError, OverflowError) as _e:
            log.debug(f"[业绩监控] 日期解析失败: {_e}")
            self._set_window_status("日期格式错误", "请使用 YYYY-MM-DD")
            return

        self._set_window_status(
            "正在历史回补", f"{start_str}→{end_str}", self._status_metric("区间 ", len(date_list), "天")
        )
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在拉取业绩数据...", "请稍候")
        log.info(f"[业绩监控] 手动扫描: {start_str} ~ {end_str}")
        self._ensure_scheduler().force_manual_scan(date_list)

    def _refresh_type_filter_button_text(self):
        text, tooltip = format_multi_select_summary(
            "分类",
            self.type_filter.selected_labels(),
            all_text="全看",
        )
        self.type_filter.setText(text)
        self.type_filter.setToolTip(tooltip)

    def _type_filter_status_text(self) -> str:
        labels = self.type_filter.selected_labels()
        if not labels:
            return "全看"
        if len(labels) <= 2:
            return " / ".join(labels)
        return f"{len(labels)}项"

    def _on_type_filter_changed(self):
        """联动到底层 Proxy Model 进行实时列过滤"""
        selected_types = self.type_filter.selected_values()
        self.proxy_model.setColumnFilters("类型", selected_types)
        self._refresh_type_filter_button_text()
        log.debug(f"[业绩监控] 分类筛选切换: {sorted(selected_types) if selected_types else '全看'}")
        self._refresh_window_status()

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_window_status()

    @staticmethod
    def _is_st_stock_name(name: str) -> bool:
        return "ST" in str(name or "").strip().upper()

    @classmethod
    def _filter_out_st_dataframe(cls, df: "pd.DataFrame") -> "pd.DataFrame":
        if df is None or df.empty:
            return df if df is not None else pd.DataFrame()

        name_col = None
        for candidate in ("股票名称", "股票简称", "名称"):
            if candidate in df.columns:
                name_col = candidate
                break
        if not name_col:
            return df

        keep_mask = ~df[name_col].apply(cls._is_st_stock_name)
        return df.loc[keep_mask].copy()

    @staticmethod
    def _recent_trade_window_start(trade_days: int = EARNINGS_DISPLAY_TRADE_DAYS) -> str | None:
        """返回展示窗口起点（yyyy-MM-dd），按交易日而不是自然日滚动。"""
        if trade_days <= 0:
            return None
        try:
            from app.services.ui_market_calendar_service import MarketCalendar

            recent_trade_dates = MarketCalendar.get_recent_trade_dates(trade_days)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as _e:
            log.debug(f"[业绩监控] 获取交易日窗口失败: {_e}")
            return None

        if not recent_trade_dates:
            return None

        oldest_trade_date = str(recent_trade_dates[-1])
        if len(oldest_trade_date) != 8:
            return None
        return f"{oldest_trade_date[:4]}-{oldest_trade_date[4:6]}-{oldest_trade_date[6:8]}"

    @classmethod
    def _prune_rows_to_recent_trade_window(
        cls, rows: list[dict], trade_days: int = EARNINGS_DISPLAY_TRADE_DAYS
    ) -> list[dict]:
        """只保留近 N 个交易日窗口内的公告记录。

        注意这里用“最老交易日”作为窗口左边界，因此窗口内周末/节假日公告也会保留，
        但不会让自然日把展示窗口越拉越长。
        """
        start_date = cls._recent_trade_window_start(trade_days)
        if not start_date:
            return list(rows or [])

        pruned_rows = []
        for row in rows or []:
            stock_name = row.get("名称") or row.get("股票名称") or row.get("股票简称") or ""
            if cls._is_st_stock_name(stock_name):
                continue
            reveal_date = str(row.get("揭晓日") or row.get("公告日期") or "").strip()[:10]
            if not reveal_date or reveal_date >= start_date:
                pruned_rows.append(row)
        return pruned_rows

    def _apply_display_trade_window(self, force_refresh: bool = False) -> bool:
        """将展示数据裁剪到近 N 个交易日，并按需刷新表格。"""
        pruned_rows = self._prune_rows_to_recent_trade_window(self.row_data)
        changed = force_refresh or len(pruned_rows) != len(self.row_data)
        self.row_data = pruned_rows

        if changed:
            self.model.update_data(self.row_data, hydrate_latest_quotes=False)
            self._apply_latest_quotes_from_store()
            self._prime_visible_local_quote_snapshot(self.model)

        if hasattr(self, "table_state"):
            if self.row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_empty(f"仅展示近 {EARNINGS_DISPLAY_TRADE_DAYS} 个交易日数据")
        return changed

    @pyqtSlot(str, str)
    def _on_fetch_failed(self, mode: str, error_text: str):
        error_text = str(error_text or "未知错误").strip() or "未知错误"
        short_error = error_text if len(error_text) <= 120 else f"{error_text[:117]}..."
        mode_text = {
            "warm_cache": "本地缓存",
            "gap_fill": "历史回补",
            "single": "单日扫描",
            "routine": "定时扫描",
        }.get(mode, mode or "未知任务")
        log.warning(f"[业绩监控] 后台抓取失败({mode_text}): {error_text}")

        if hasattr(self, "table_state"):
            if self.row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_error("业绩抓取失败", short_error)

        self._set_window_status("业绩抓取失败", mode_text, short_error)

    @pyqtSlot(object, str)
    def _on_new_data_found(self, df: "pd.DataFrame", mode: str = "routine"):
        """当底层推上来新的 DataFrame 时，转成本地字典并无缝合并展示"""
        df = self._filter_out_st_dataframe(df)
        if df.empty:
            rows_changed = self._apply_display_trade_window(force_refresh=False)
            if mode == "warm_cache":
                if self.row_data:
                    self._set_window_status(
                        "本地缓存已加载",
                        self._status_metric("展示 ", len(self.row_data), "只"),
                        self._status_metric("窗口 ", EARNINGS_DISPLAY_TRADE_DAYS, "交易日"),
                    )
                else:
                    self._set_window_status("本地缓存为空", "当前无可展示数据")
            else:
                if self.row_data:
                    self._set_window_status(
                        "本轮无新增高增股",
                        self._status_metric("展示 ", len(self.row_data), "只"),
                        self._status_metric("窗口 ", EARNINGS_DISPLAY_TRADE_DAYS, "交易日"),
                    )
                else:
                    self._set_window_status("本轮无新增高增股")
            if rows_changed:
                event_bus.sig_earnings_updated.emit()
            return

        if mode == "warm_cache":
            self._set_window_status("本地缓存加载中", self._status_metric("新增 ", len(df), "只"))
        else:
            self._set_window_status("本次扫描命中", self._status_metric("新增 ", len(df), "只"))

        # 缓存回放可能有上千行；先建立索引，让去重覆盖保持 O(n)，避免逐行扫描 row_data。
        existing_rows_by_key = {
            (str(r.get("代码", "")).strip(), str(r.get("报告期", "")).strip()): r
            for r in self.row_data
            if isinstance(r, dict)
        }

        def fmt_money(v):
            if v != v or v is None:
                return "--"
            if abs(v) >= 1e8:
                return f"{v / 1e8:.2f}亿"
            if abs(v) >= 1e4:
                return f"{v / 1e4:.0f}万"
            return f"{v:.0f}"

        for row in df.to_dict("records"):
            code = str(row.get("股票代码", "")).zfill(6)
            name = str(row.get("股票名称", ""))
            pct = float(row.get("环比增速_百分比", 0.0))
            cur_profit = float(row.get("单季净利润_新增", 0.0))
            last_profit = float(row.get("单季净利润_上期", 0.0))

            row_obj = {
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "PE(TTM)": "--",
                "环比%": pct,
                "同比%": float(row.get("同比增速_百分比", 0.0)),
                "当季利润": fmt_money(cur_profit),
                "上季利润": fmt_money(last_profit),
                "_raw_profit": float(row.get("单季净利润_新增", 0.0)),  # 用于计算PE的隐含原始数值
                "报告期": str(row.get("报告期", "")),
                "类型": str(row.get("数据类型", "")),
                "揭晓日": str(row.get("公告日期", "")),
                "基调": str(row.get("基调", "")),
                "所属行业与概念": str(row.get("所属行业与概念", "")),
            }

            # 校验与层级更替（预告 -> 财报）去重
            # 只要 代码 和 报告期 相同，就证明这是同一份财报的不同进度版，必须走去重覆盖！
            existing_key = (code, str(row_obj["报告期"]).strip())
            existing_row = existing_rows_by_key.get(existing_key)
            if existing_row is not None:
                old_date = existing_row.get("揭晓日", "")
                new_date = row_obj["揭晓日"]

                # 只有更晚发布的权威版（包括同日不同类型），才允许覆盖界面上的旧版（如财报覆盖旧预告）
                if new_date >= old_date:
                    row_obj["现价"] = existing_row.get("现价", "--")
                    row_obj["涨幅%"] = existing_row.get("涨幅%", "--")
                    row_obj["市值"] = existing_row.get("市值", "--")
                    row_obj["PE(TTM)"] = existing_row.get("PE(TTM)", "--")
                    if "_raw_profit" not in row_obj and "_raw_profit" in existing_row:
                        row_obj["_raw_profit"] = existing_row["_raw_profit"]

                    # 确保如果有老的字段，也被合并到新数据上（防止旧属性丢失）
                    existing_row.update(row_obj)
                    log.debug(f"[业绩监控] {code} {row_obj['报告期']} 已覆盖为 {row_obj['类型']}")
                else:
                    log.debug(f"[业绩监控] {code} {row_obj['类型']} 已存在更新版，跳过")
            else:
                self.row_data.append(row_obj)
                existing_rows_by_key[existing_key] = row_obj

        # 只展示近 N 个交易日窗口，避免自然日累计导致表格越来越重。
        self._apply_display_trade_window(force_refresh=True)
        self._set_window_status(
            "业绩异动已刷新",
            self._status_metric("展示 ", len(self.row_data), "只"),
            self._status_metric("窗口 ", EARNINGS_DISPLAY_TRADE_DAYS, "交易日"),
        )

        # 通知关注池：业绩数据已就绪，重新拉取"业绩异动"列
        # 旧事件枚举链路已废弃，这里走专属刷新通道。
        event_bus.sig_earnings_updated.emit()

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
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu

            build_stock_context_menu(self, code, name, vcp_data=row_data)

    def _on_double_click(self, index):
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
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

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        """业绩异动不向中央报价站贡献代码，避免盘中触发联网补价。"""
        return set()

    def refresh_data_after_f5(self) -> bool:
        self._apply_latest_quotes_from_store()
        self._apply_display_trade_window(force_refresh=True)
        self.refresh_table_from_latest_snapshot(current_model=self.model, async_local=True)
        scheduler = self._ensure_scheduler()
        trigger = getattr(scheduler, "trigger_routine_scan", None)
        if callable(trigger):
            return bool(trigger(reason="f5"))
        return False

    def _apply_latest_quotes_from_store(self):
        self._apply_quote_store_snapshot()
        self._recalc_pe_ttm()

    def _on_cache_reload_completed(self):
        self._apply_latest_quotes_from_store()

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

    def _cleanup_runtime_state(self):
        recalc_timer = getattr(self, "_recalc_pe_timer", None)
        if recalc_timer is not None:
            recalc_timer.stop()
        if self.scheduler is not None:
            try:
                disconnect = getattr(self.scheduler.sig_new_surprises_found, "disconnect", None)
                if callable(disconnect):
                    disconnect(self._on_new_data_found)
            except (TypeError, RuntimeError):
                pass
            try:
                disconnect = getattr(self.scheduler.sig_fetch_failed, "disconnect", None)
                if callable(disconnect):
                    disconnect(self._on_fetch_failed)
            except (TypeError, RuntimeError):
                pass
            if getattr(self, "_owns_earnings_service", False):
                shutdown = getattr(self.scheduler, "shutdown", None)
                if callable(shutdown):
                    shutdown()
        try:
            event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
        except (TypeError, RuntimeError):
            pass
        super()._cleanup_runtime_state()

    def shutdown(self) -> None:
        self._cleanup_runtime_state()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def showEvent(self, event):
        """隐藏页首次打开时，父类会补现价/市值快照，这里紧跟着补算 PE。"""
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()
        if self.row_data:
            self._recalc_pe_timer.start(0)

    def _on_rt_quotes_direct(self, quotes: dict):
        """重写基类的直达信号接收，在刷新行情后补充计算 PE(TTM)"""
        super()._on_rt_quotes_direct(quotes)
        self._recalc_pe_ttm()

    def _after_market_caps_updated(self):
        """总股本/市值异步回填完成后，立刻重算 PE，避免等待下一轮行情广播。"""
        self._recalc_pe_ttm()

    def _recalc_pe_ttm(self):
        """PE(TTM) = 市值 / (最新单季扣非利润 × 4)，两个数据表里都有，直接算"""
        model_rows = getattr(self.model, "row_data", None) or self.row_data
        updated = 0

        for row_idx, row_dict in enumerate(model_rows):
            cap_str = str(row_dict.get("市值", "--")).strip()
            raw_profit = row_dict.get("_raw_profit", 0)

            try:
                raw_profit_val = float(raw_profit or 0)
            except (ValueError, TypeError):
                continue

            if cap_str in ("", "--") or raw_profit_val <= 0:
                continue

            try:
                # 解析市值字符串："150亿" → 150e8，"3200万" → 3200e4
                if "亿" in cap_str:
                    cap = float(cap_str.replace("亿", "")) * 1e8
                elif "万" in cap_str:
                    cap = float(cap_str.replace("万", "")) * 1e4
                else:
                    cap = float(cap_str)

                pe = cap / (raw_profit_val * 4.0)
                new_pe = f"{pe:.1f}"
                if row_dict.get("PE(TTM)") != new_pe:
                    if hasattr(self.model, "set_cell_value"):
                        self.model.set_cell_value(row_idx, "PE(TTM)", new_pe)
                    else:
                        row_dict["PE(TTM)"] = new_pe
                    updated += 1
            except (ValueError, ZeroDivisionError) as _e:
                log.debug(f"[业绩监控] PE 计算异常({cap_str}): {_e}")

        if updated > 0:
            log.debug(f"[业绩监控] PE(TTM) 已刷新 {updated} 行")
