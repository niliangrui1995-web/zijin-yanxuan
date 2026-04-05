# -*- coding: utf-8 -*-
import os
import pandas as pd
from datetime import datetime
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableView, QHeaderView, QWidget, QPushButton, QLabel, QLineEdit, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSlot
from ui.tabs.base_stock_tab import BaseStockTab
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from core.event_bus import event_bus
from core.logger import get_logger

from earnings.scheduler import EarningsScheduler

log = get_logger(__name__)


class EarningsTab(BaseStockTab):
    """业绩断层与预告高增监控面板"""
    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self.row_data = []
        self._init_ui()
        
        # 挂载后台总调度器
        self.scheduler = EarningsScheduler(self)
        self.scheduler.sig_new_surprises_found.connect(self._on_new_data_found)
        
        # 界面初始化完成后，立刻命令调度器开机暴走（吐缓存 + 追扫重连）
        self.scheduler.start_patrol()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- 头部控制栏 ---
        header = QHBoxLayout()
        header.setContentsMargins(12, 12, 12, 12)
        
        title = QLabel("🚀 超预期金矿：业绩预告与财报环比高增追踪")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #E5E7EB;")
        
        self.lbl_status = QLabel("监控挂机中...")
        self.lbl_status.setStyleSheet("color: #10B981; font-weight: bold; font-size: 13px;")
        
        # 时光机雷达
        self.ent_target_date = QLineEdit()
        self.ent_target_date.setPlaceholderText("YYYY-MM-DD")
        self.ent_target_date.setText(datetime.now().strftime("%Y-%m-%d"))
        self.ent_target_date.setFixedWidth(100)
        self.ent_target_date.setStyleSheet("background: #1F2937; color: #E5E7EB; border: 1px solid #4B5563; border-radius: 4px; padding: 2px 4px;")
        
        self.btn_manual_fetch = QPushButton("⏳ 时光机探测")
        self.btn_manual_fetch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_fetch.setToolTip("手动填入日期，强行追击并抓取那一天的绝世好票连同底稿算出环比！")
        self.btn_manual_fetch.setStyleSheet("""
            QPushButton {
                background-color: #374151; color: #FBBF24; 
                border: 1px solid #4B5563; border-radius: 4px; 
                padding: 4px 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4B5563; border-color: #F59E0B; }
            QPushButton:pressed { background-color: #1F2937; }
        """)
        self.btn_manual_fetch.clicked.connect(self._on_manual_fetch)
        
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.lbl_status)
        header.addSpacing(20)
        header.addWidget(QLabel("回溯日:"))
        header.addWidget(self.ent_target_date)
        header.addWidget(self.btn_manual_fetch)
        
        layout.addLayout(header)

        # --- 表格显示区 ---
        self.table = QTableView()
        layout.addWidget(self.table)
        
        # 字段映射表：前四列必须是标准列（代码/名称/现价/涨幅%），以便接收盘中广播
        self.header_labels = [
            "代码", "名称", "现价", "涨幅%", "市值",
            "环比%", "单季利润(新)", "单季利润(旧)", 
            "报告期", "类型", "揭晓日", "基调"
        ]
        
        self.model = StockTableModel(self.header_labels)
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)

        # 右键菜单与双击看K线
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        # 持久化列宽
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(False)
        default_widths = [70, 80, 70, 70, 70, 80, 120, 120, 80, 70, 90, 80]
        for i, w in enumerate(default_widths):
            header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)
            
        self.bind_header_persistence(self.table, "earnings_header_state")

    def _on_manual_fetch(self):
        target_str = self.ent_target_date.text().strip()
        if not target_str:
            return
        self.lbl_status.setText(f"🚀 时空跳跃启动：正在强行撕开 {target_str} 的历史数据...")
        self.scheduler.force_manual_scan(target_str)

    @pyqtSlot(object)
    def _on_new_data_found(self, df: pd.DataFrame):
        """当底层推上来新的 DataFrame 时，转成本地字典并无缝合并展示"""
        if df.empty:
            self.lbl_status.setText("✅ 抓取侦测完成跑通，无可推送的新增高增股")
            return
            
        self.lbl_status.setText(f"🎉 轰炸警报：本次扫描捕获 {len(df)} 只环比高增股！")
        
        for _, row in df.iterrows():
            code = str(row.get('股票代码', '')).zfill(6)
            name = str(row.get('股票名称', ''))
            pct = float(row.get('环比增速_百分比', 0.0))
            cur_profit = float(row.get('单季净利润_新增', 0.0))
            last_profit = float(row.get('单季净利润_上期', 0.0))
            
            # 格式化一下大额单位
            def fmt_money(v):
                if pd.isna(v): return "--"
                if abs(v) >= 1e8: return f"{v/1e8:.2f}亿"
                if abs(v) >= 1e4: return f"{v/1e4:.0f}万"
                return f"{v:.0f}"
            
            row_obj = {
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "环比%": pct,
                "单季利润(新)": fmt_money(cur_profit),
                "单季利润(旧)": fmt_money(last_profit),
                "报告期": str(row.get("报告期", "")),
                "类型": str(row.get("数据类型", "")),
                "揭晓日": str(row.get("公告日期", "")),
                "基调": str(row.get("基调", ""))
            }
            
            # 校验防重（由于底层传上来的可能有开机追存的旧数据，需要在 UI 层做字典级去重更新）
            exists = False
            for r in self.row_data:
                if r.get("代码") == code and r.get("报告期") == row_obj["报告期"] and r.get("揭晓日") == row_obj["揭晓日"]:
                    r.update(row_obj)
                    exists = True
                    break
            if not exists:
                self.row_data.append(row_obj)
                
        # 刷新视图
        self.model.update_data(self.row_data)

    def _show_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu
            build_stock_context_menu(self.table, code, name)

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        current_list = [{'代码': r.get("代码", ""), '名称': r.get("名称", "")} 
                        for r in self.model.row_data]
        
        event_bus.sig_show_kline_with_list.emit(code, current_list, row)

    def closeEvent(self, event):
        self.scheduler.stop_patrol()
        super().closeEvent(event)
