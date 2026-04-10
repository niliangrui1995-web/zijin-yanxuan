# -*- coding: utf-8 -*-
"""
ui/tabs/foreign_block_trade_tab.py
大宗交易监控 Tab
展示包含指定外资/机构关键字的营业部近期大宗交易明细，并高亮对倒、互砍等特殊行为。
"""
import datetime
import os
import pandas as pd
import akshare as ak

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableView, QHeaderView, QPushButton, QLabel, QAbstractItemView, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt, QTimer

from ui.theme import (
    COLOR_RISE, COLOR_FALL, COLOR_FLAT
)
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView
from core.event_bus import event_bus

class BlockTradeFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.exact_filters = {}

    def setExactFilter(self, col_name, value):
        if value:
            self.exact_filters[col_name] = value
        else:
            self.exact_filters.pop(col_name, None)
        self.invalidateFilter()
        
    def filterAcceptsRow(self, source_row, source_parent):
        if self.exact_filters:
            model = self.sourceModel()
            row_data = model.row_data[source_row]
            for col_name, val in self.exact_filters.items():
                cell_val = str(row_data.get(col_name, ''))
                if col_name in ("买方营业部", "卖方营业部"):
                    # 席位做包含匹配（模糊搜索），匹配不上则拦截
                    if val not in cell_val:
                        return False
                elif col_name == "_branch":
                    # 特殊的席位联合逻辑
                    if val not in str(row_data.get("买方营业部", "")) and val not in str(row_data.get("卖方营业部", "")):
                        return False
                elif val != cell_val:
                    return False
                    
        return super().filterAcceptsRow(source_row, source_parent)

from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

FOREIGN_KEYWORDS = ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]
TARGET_KEYWORDS = FOREIGN_KEYWORDS + ["机构专用"]

# 模块级K线缓存：每只股票的文件只读一次，后续直接从内存取
_kline_cache: dict = {}

class ForeignBlockTradeTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._block_trade_codes = set()
        self._cap_cache = {}
        
        self.days_to_fetch = 20  # 默认拉取最近20个交易日
        self._init_ui()

        # 延迟加载缓存
        QTimer.singleShot(3200, self._load_block_trade_data)

        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 顶部工具栏
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 6, 8, 6)
        from ui.theme import theme_manager
        lbl_title = QLabel("🌐 主力/外资大宗")
        lbl_title.setObjectName("tabTitle")
        header_layout.addWidget(lbl_title)
        
        self.lbl_status = QLabel("等待加载...")
        self.lbl_status.setObjectName("tabSubtitle")
        header_layout.addWidget(self.lbl_status)
        header_layout.addStretch()

        # ── 筛选器组：按数据维度从大到小排列 ──
        self.cmb_filter_date = QComboBox()
        self.cmb_filter_date.addItem("全部日期")
        self.cmb_filter_date.setFixedWidth(128)
        self.cmb_filter_date.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_date)

        self.cmb_filter_branch = QComboBox()
        self.cmb_filter_branch.addItem("全部监控席位")
        self.cmb_filter_branch.setFixedWidth(152)
        self.cmb_filter_branch.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_branch)

        self.cmb_filter_direction = QComboBox()
        self.cmb_filter_direction.addItems(["全部动作", "外资买入", "外资卖出", "外资对倒", "机构买入", "机构卖出", "机构对倒", "机构买/外资卖", "外资买/机构卖"])
        self.cmb_filter_direction.setFixedWidth(128)
        self.cmb_filter_direction.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_direction)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 搜索代码/名称/任意词...")
        self.search_box.setFixedWidth(240)
        self.search_box.textChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.search_box)

        # ── 数据拉取范围 + 操作按钮（合并后唯一一组） ──
        self.cmb_days = QComboBox()
        self.cmb_days.addItems(["近 10 交易日", "近 20 交易日", "近 40 交易日", "近 60 交易日"])
        # 默认选中 20 交易日，与 self.days_to_fetch 初始值保持一致
        self.cmb_days.setCurrentIndex(1)
        self.cmb_days.setFixedWidth(148)
        self.cmb_days.currentIndexChanged.connect(self._on_days_changed)
        header_layout.addWidget(self.cmb_days)

        self.btn_refresh = QPushButton("🔄 抓取数据")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._load_block_trade_data)
        header_layout.addWidget(self.btn_refresh)
        layout.addLayout(header_layout)

        # 表格
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "交易日期", "交易详情", 
            "当日收盘价", "成交价格", "折/溢价率(%)", "成交数量(万股)", "成交金额(万元)", 
            "买方营业部", "卖方营业部"
        ]
        self.table = VCPTableView(default_row_height=28)
        self.model = StockTableModel(self.columns)
        self.proxy_model = BlockTradeFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        # 大宗交易默认按时间排序 由近到远
        self.table.sortByColumn(5, Qt.SortOrder.DescendingOrder)

        # 列宽
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        # 严格压缩默认列宽，总和约1200px以内，确保即使在小屏幕/高缩放比下也不会超过屏幕宽度产生滚动条
        default_widths = [60, 70, 55, 55, 55, 70, 85, 65, 65, 65, 75, 75, 140, 140]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置 (v4 强制刷新新布局)
        self.bind_header_persistence(self.table, "block_trade_header_state_v4")

        # 双击 → K线图
        self.table.doubleClicked.connect(self._on_double_click)
        # 右键菜单
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table, 1)
        
    def _on_days_changed(self, idx):
        days_map = {0: 10, 1: 20, 2: 40, 3: 60}
        self.days_to_fetch = days_map.get(idx, 10)
        self._load_block_trade_data()

    def _should_include_row(self, buyer, seller):
        buyer_str = str(buyer) if pd.notna(buyer) else ""
        seller_str = str(seller) if pd.notna(seller) else ""
        
        for kw in TARGET_KEYWORDS:
            if kw in buyer_str or kw in seller_str:
                return True
        return False

    def _determine_direction(self, buyer, seller):
        """判断是外资/机构的买卖动作"""
        buyer_str = str(buyer) if pd.notna(buyer) else ""
        seller_str = str(seller) if pd.notna(seller) else ""
        
        buy_foreign = any(kw in buyer_str for kw in FOREIGN_KEYWORDS)
        sell_foreign = any(kw in seller_str for kw in FOREIGN_KEYWORDS)
        
        buy_inst = "机构专用" in buyer_str
        sell_inst = "机构专用" in seller_str
        
        # 混合对倒
        if buy_inst and sell_foreign:
            return "机构买/外资卖", "#3B82F6"  # 混合动作标记蓝色
        if buy_foreign and sell_inst:
            return "外资买/机构卖", "#3B82F6"
            
        # 同类对倒
        if buy_foreign and sell_foreign:
            return "外资对倒", "#F59E0B"
        if buy_inst and sell_inst:
            return "机构对倒", "#F59E0B"
            
        # 单方动作
        if buy_foreign:
            return "外资买入", COLOR_RISE
        if sell_foreign:
            return "外资卖出", COLOR_FALL
        if buy_inst:
            return "机构买入", COLOR_RISE
        if sell_inst:
            return "机构卖出", COLOR_FALL
            
        return "--", COLOR_FLAT

    def _load_block_trade_data(self):
        self.lbl_status.setText("拼命拉取大宗交易数据中...")
        self.model.update_data([])
        # 清空上一轮的K线缓存，防止跨交易日窗口后内存只增不减
        _kline_cache.clear()
        
        def _fetch_task():
            end_dt = datetime.datetime.now()
            
            # 使用交易日历倒推 start_dt
            try:
                trade_cal = ak.tool_trade_date_hist_sina()
                trade_cal['trade_date'] = pd.to_datetime(trade_cal['trade_date']).dt.date
                dates = trade_cal['trade_date'].tolist()
                today_date = end_dt.date()
                past_dates = [d for d in dates if d <= today_date]
                if len(past_dates) >= self.days_to_fetch:
                    start_date_val = past_dates[-self.days_to_fetch]
                else:
                    start_date_val = past_dates[0] if past_dates else (today_date - datetime.timedelta(days=self.days_to_fetch))
                start_dt = datetime.datetime.combine(start_date_val, datetime.time())
            except Exception as e:
                log.warning(f"获取交易日历失败，使用自然日重估: {e}")
                start_dt = end_dt - datetime.timedelta(days=int(self.days_to_fetch * 1.5))

            results = []
            
            # 分段拉取：东财接口对大日期范围会截断或断连，每次只拉15天
            import time
            CHUNK_DAYS = 15
            cursor = start_dt
            while cursor < end_dt:
                chunk_end = min(cursor + datetime.timedelta(days=CHUNK_DAYS), end_dt)
                s_str = cursor.strftime("%Y%m%d")
                e_str = chunk_end.strftime("%Y%m%d")
                
                for attempt in range(3):
                    try:
                        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=s_str, end_date=e_str)
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                if self._should_include_row(row.get('买方营业部'), row.get('卖方营业部')):
                                    results.append(row.to_dict())
                        break  # 成功就跳出重试
                    except Exception as e:
                        log.warning(f"[大宗交易] {s_str}-{e_str} 第{attempt+1}次失败: {e}")
                        if attempt < 2:
                            time.sleep(1)
                
                cursor = chunk_end + datetime.timedelta(days=1)
            
            return results
            
        task_manager.run_in_background(
            _fetch_task, 
            task_id="foreign_block_trade",
            on_success=self._on_data_fetched
        )

    def _on_data_fetched(self, data_list):
        if not data_list:
            self._aggregated_records = []
            self._block_trade_codes = set()
            self.lbl_status.setText("❌ 近期未发现匹配监控席位的大宗交易。")
            event_bus.sig_block_trade_updated.emit()
            return
            
        df = pd.DataFrame(data_list)
        
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
        # 保存聚合后的记录，用于表格行匹配
        self._aggregated_records = df.to_dict('records')
        
        # 提取筛选器选项
        unique_dates = sorted(df['交易日期'].dropna().unique().tolist(), key=lambda x: str(x), reverse=True)
        # 提取相关外资席位
        raw_branches = set(df['买方营业部'].dropna().tolist() + df['卖方营业部'].dropna().tolist())
        target_branches = set()
        for b in raw_branches:
            b_str = str(b)
            if any(kw in b_str for kw in TARGET_KEYWORDS):
                target_branches.add(b_str)
        unique_branches = sorted(list(target_branches))

        self.cmb_filter_date.blockSignals(True)
        self.cmb_filter_branch.blockSignals(True)
        
        self.cmb_filter_date.clear()
        self.cmb_filter_date.addItem("全部日期")
        self.cmb_filter_date.addItems([str(x) for x in unique_dates])
        
        self.cmb_filter_branch.clear()
        self.cmb_filter_branch.addItem("全部监控席位")
        self.cmb_filter_branch.addItems(unique_branches)

        self.cmb_filter_date.blockSignals(False)
        self.cmb_filter_branch.blockSignals(False)
        
        self._block_trade_codes = set([str(x).zfill(6) for x in df['证券代码'].tolist()])
        
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
            
            direction, color = self._determine_direction(buyer, seller)

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

        self.model.update_data(row_data)
        self.lbl_status.setText(f"✅ 加载完成，发现 {len(df)} 笔监控席位大宗交易。")
        
        # 强制应用当前的筛选状态
        self._filter_table_combo()
        event_bus.sig_block_trade_updated.emit()
        
        # 统一异步刷新市值
        self.async_update_market_caps()

    def _filter_table_combo(self):
        search_text = self.search_box.text().strip().lower()
        self.proxy_model.setFilterText(search_text)
        
        filter_date = self.cmb_filter_date.currentText()
        self.proxy_model.setExactFilter("交易日期", None if filter_date == "全部日期" else filter_date)

        filter_direction = self.cmb_filter_direction.currentText()
        self.proxy_model.setExactFilter("交易详情", None if filter_direction == "全部动作" else filter_direction)
        
        filter_branch = self.cmb_filter_branch.currentText()
        self.proxy_model.setExactFilter("_branch", None if filter_branch == "全部监控席位" else filter_branch)

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        
        # 提取当前表格顺序以传递给K线窗口
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
