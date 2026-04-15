# -*- coding: utf-8 -*-
from datetime import datetime
import pandas as pd
from PyQt6.QtWidgets import (
    QVBoxLayout, QHeaderView, QPushButton, QLabel, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from ui.tabs.base_stock_tab import BaseStockTab
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView, TableStateWrapper
from core.event_bus import event_bus
from core.logger import get_logger

from earnings.scheduler import EarningsScheduler

log = get_logger(__name__)
EARNINGS_DISPLAY_TRADE_DAYS = 21


class EarningsTab(BaseStockTab):
    """业绩断层与预告高增监控面板"""
    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self.row_data = []
        self._init_ui()
        
        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()
        
        # 挂载后台总调度器
        self.scheduler = EarningsScheduler(self)
        self.scheduler.sig_new_surprises_found.connect(self._on_new_data_found)
        
        # 延后到事件循环空闲时再启动巡逻，避免构造阶段阻塞 UI。
        QTimer.singleShot(0, self.scheduler.start_patrol)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("监控挂机中...")

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(160)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        # 时光机雷达
        self.ent_start_date = QLineEdit()
        self.ent_start_date.setPlaceholderText("起点(如2024-01-01)")
        self.ent_start_date.setText(datetime.now().strftime("%Y-%m-%d"))
        self.ent_start_date.setFixedWidth(100)
        
        self.ent_end_date = QLineEdit()
        self.ent_end_date.setPlaceholderText("终点(如2024-01-15)")
        self.ent_end_date.setText(datetime.now().strftime("%Y-%m-%d"))
        self.ent_end_date.setFixedWidth(100)
        
        self.btn_manual_fetch = QPushButton("历史更新")
        self.btn_manual_fetch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_fetch.setToolTip("手动填入日期区间，强行进行数据扫描并在本地进行去重和升级！")
        self.btn_manual_fetch.clicked.connect(self._on_manual_fetch)
        
        # 新增 Excel 风格的分类筛选下拉框
        self.combo_type_filter = QComboBox()
        self.combo_type_filter.addItems(["全看", "仅看预告", "仅看快报", "仅看财报"])
        # Removed hardcoded inline stylesheet to rely on global responsive QSS
        self.combo_type_filter.currentTextChanged.connect(self._on_type_filter_changed)

        filter_widgets = [
            self.search_box, QLabel("分类筛选:"), self.combo_type_filter,
            QLabel("更新区间倒推:"), self.ent_start_date, QLabel("-"), self.ent_end_date
        ]
        action_widgets = [self.btn_manual_fetch]
        toolbar = self.build_tab_toolbar("业绩高增追踪", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # --- 表格显示区 ---
        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无业绩数据", loading_title="加载中...")
        layout.addWidget(self.table_state)
        
        # 字段映射表：前四列必须是标准列（代码/名称/现价/涨幅%），以便接收盘中广播
        self.header_labels = [
            "代码", "名称", "现价", "涨幅%", "市值", "PE(TTM)",
            "环比%", "同比%", "当季利润", "上季利润", 
            "报告期", "类型", "揭晓日", "基调", "所属行业与概念"
        ]
        
        self.model = StockTableModel(self.header_labels)
        self.model.set_plain_style_headers(["揭晓日"])
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        # 默认按第12列（“揭晓日”）由近到远（降序）排列，让最新鲜的情报自动顶在最上面
        self.table.sortByColumn(self.model.headers.index("揭晓日"), Qt.SortOrder.DescendingOrder)

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
            
        self.bind_header_persistence(self.table, "earnings_header_state_v5")

    def _set_window_status(self, primary: str, *segments: str):
        self.lbl_status.setText(self.format_status_summary(primary, *segments))

    def _on_manual_fetch(self):
        start_str = self.ent_start_date.text().strip()
        end_str = self.ent_end_date.text().strip()
        if not start_str or not end_str:
            return
            
        try:
            from datetime import timedelta
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
            
        self._set_window_status("正在拉取业绩数据", f"{start_str}→{end_str}", self._status_metric("区间 ", len(date_list), "天"))
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在拉取业绩数据...", "请稍候")
        log.info(f"[业绩监控] 手动扫描: {start_str} ~ {end_str}")
        self.scheduler.force_manual_scan(date_list)

    def _on_type_filter_changed(self, text):
        """联动到底层 Proxy Model 进行实时列过滤"""
        if text == "全看":
            self.proxy_model.setColumnFilter("类型", "")
        else:
            # 截断提取真实关键字，比如 “仅看预告” -> “预告”
            keyword = text.replace("仅看", "")
            self.proxy_model.setColumnFilter("类型", keyword)
            
        # 记录下操作
        log.debug(f"[业绩监控] 筛选切换: {text}")

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)

    @staticmethod
    def _recent_trade_window_start(trade_days: int = EARNINGS_DISPLAY_TRADE_DAYS) -> str | None:
        """返回展示窗口起点（yyyy-MM-dd），按交易日而不是自然日滚动。"""
        if trade_days <= 0:
            return None
        try:
            from core.market_calendar import MarketCalendar

            recent_trade_dates = MarketCalendar.get_recent_trade_dates(trade_days)
        except Exception as _e:
            log.debug(f"[业绩监控] 获取交易日窗口失败: {_e}")
            return None

        if not recent_trade_dates:
            return None

        oldest_trade_date = str(recent_trade_dates[-1])
        if len(oldest_trade_date) != 8:
            return None
        return f"{oldest_trade_date[:4]}-{oldest_trade_date[4:6]}-{oldest_trade_date[6:8]}"

    @classmethod
    def _prune_rows_to_recent_trade_window(cls, rows: list[dict], trade_days: int = EARNINGS_DISPLAY_TRADE_DAYS) -> list[dict]:
        """只保留近 N 个交易日窗口内的公告记录。

        注意这里用“最老交易日”作为窗口左边界，因此窗口内周末/节假日公告也会保留，
        但不会让自然日把展示窗口越拉越长。
        """
        start_date = cls._recent_trade_window_start(trade_days)
        if not start_date:
            return list(rows or [])

        pruned_rows = []
        for row in rows or []:
            reveal_date = str(row.get("揭晓日") or row.get("公告日期") or "").strip()[:10]
            if not reveal_date or reveal_date >= start_date:
                pruned_rows.append(row)
        return pruned_rows

    def _apply_display_trade_window(self, force_refresh: bool = False) -> bool:
        """将展示数据裁剪到近 21 个交易日，并按需刷新表格。"""
        pruned_rows = self._prune_rows_to_recent_trade_window(self.row_data)
        changed = force_refresh or len(pruned_rows) != len(self.row_data)
        self.row_data = pruned_rows

        if changed:
            self.model.update_data(self.row_data)
            self.refresh_table_quotes_and_market_caps(quote_task_id="earnings_quotes")

        if hasattr(self, "table_state"):
            if self.row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_empty(f"仅展示近 {EARNINGS_DISPLAY_TRADE_DAYS} 个交易日数据")
        return changed

    @pyqtSlot(object, str)
    def _on_new_data_found(self, df: "pd.DataFrame", mode: str = "routine"):
        """当底层推上来新的 DataFrame 时，转成本地字典并无缝合并展示"""
        if df.empty:
            rows_changed = self._apply_display_trade_window(force_refresh=False)
            if mode == "warm_cache":
                if self.row_data:
                    self._set_window_status(
                        "缓存恢复完成",
                        self._status_metric("展示 ", len(self.row_data), "只"),
                        self._status_metric("窗口 ", EARNINGS_DISPLAY_TRADE_DAYS, "交易日"),
                    )
                else:
                    self._set_window_status("缓存恢复完成", "当前无可展示数据")
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
            self._set_window_status("缓存恢复中", self._status_metric("新增 ", len(df), "只"))
        else:
            self._set_window_status("本次扫描命中", self._status_metric("新增 ", len(df), "只"))
        
        for _, row in df.iterrows():
            code = str(row.get('股票代码', '')).zfill(6)
            name = str(row.get('股票名称', ''))
            pct = float(row.get('环比增速_百分比', 0.0))
            cur_profit = float(row.get('单季净利润_新增', 0.0))
            last_profit = float(row.get('单季净利润_上期', 0.0))
            
            # 格式化一下大额单位
            def fmt_money(v):
                if v != v or v is None: return "--"
                if abs(v) >= 1e8: return f"{v/1e8:.2f}亿"
                if abs(v) >= 1e4: return f"{v/1e4:.0f}万"
                return f"{v:.0f}"
            
            row_obj = {
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "PE(TTM)": "--",
                "环比%": pct,
                "同比%": float(row.get('同比增速_百分比', 0.0)),
                "当季利润": fmt_money(cur_profit),
                "上季利润": fmt_money(last_profit),
                "_raw_profit": float(row.get('单季净利润_新增', 0.0)),  # 用于计算PE的隐含原始数值
                "报告期": str(row.get("报告期", "")),
                "类型": str(row.get("数据类型", "")),
                "揭晓日": str(row.get("公告日期", "")),
                "基调": str(row.get("基调", "")),
                "所属行业与概念": str(row.get("所属行业与概念", ""))
            }
            
            # 校验与层级更替（预告 -> 财报）去重
            exists = False
            for r in self.row_data:
                # 只要 代码 和 报告期 相同，就证明这是同一份财报的不同进度版，必须走去重覆盖！
                if r.get("代码") == code and r.get("报告期") == row_obj["报告期"]:
                    exists = True
                    old_date = r.get("揭晓日", "")
                    new_date = row_obj["揭晓日"]
                    
                    # 只有更晚发布的权威版（包括同日不同类型），才允许覆盖界面上的旧版（如财报覆盖旧预告）
                    if new_date >= old_date:
                        row_obj["现价"] = r.get("现价", "--")
                        row_obj["涨幅%"] = r.get("涨幅%", "--")
                        row_obj["市值"] = r.get("市值", "--")
                        row_obj["PE(TTM)"] = r.get("PE(TTM)", "--")
                        if "_raw_profit" not in row_obj and "_raw_profit" in r:
                            row_obj["_raw_profit"] = r["_raw_profit"]
                        
                        # 确保如果有老的字段，也被合并到新数据上（防止旧属性丢失）
                        r.update(row_obj)
                        log.debug(f"[业绩监控] {code} {row_obj['报告期']} 已覆盖为 {row_obj['类型']}")
                    else:
                        log.debug(f"[业绩监控] {code} {row_obj['类型']} 已存在更新版，跳过")
                    break
                    
            if not exists:
                self.row_data.append(row_obj)
                
        # 只展示近 21 个交易日窗口，避免自然日累计导致表格越来越重。
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
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu
            build_stock_context_menu(self, code, name, vcp_data=row_data)

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        code_list = []
        for r in range(self.proxy_model.rowCount()):
            s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            if s_idx.row() < len(self.model.row_data):
                rd = self.model.row_data[s_idx.row()]
                code_list.append({'代码': rd.get("代码", ""), '名称': rd.get("名称", "")})
        
        current_idx = 0
        for i, c in enumerate(code_list):
            if c['代码'] == code:
                current_idx = i
                break
                
        event_bus.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def closeEvent(self, event):
        self.scheduler.stop_patrol()
        super().closeEvent(event)

    def showEvent(self, event):
        """隐藏页首次打开时，父类会补现价/市值快照，这里紧跟着补算 PE。"""
        super().showEvent(event)
        if self.row_data:
            QTimer.singleShot(0, self._recalc_pe_ttm)

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
