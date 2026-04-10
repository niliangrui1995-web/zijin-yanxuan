# -*- coding: utf-8 -*-
"""
ui/tabs/lhb_tab.py
资金共振 (龙虎榜) Tab
监控每日龙虎榜单中的机构与外资(QFII/陆股通)资金动向，抓取它们的共振交集（🔥）。
"""
import datetime
import pandas as pd
import akshare as ak
import os
import json

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QDateEdit, QCheckBox
)
from PyQt6.QtCore import Qt, QDate

from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager

from ui.tabs.base_stock_tab import BaseStockTab
from ui.workers.lhb_worker import fetch_lhb_data_for_date

log = get_logger(__name__)

class LhbFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.only_resonance = False

    def set_resonance_filter(self, enabled: bool):
        self.only_resonance = enabled
        self.invalidateFilter()
        
    def filterAcceptsRow(self, source_row, source_parent):
        if self.only_resonance:
            model = self.sourceModel()
            row_data = model.row_data[source_row]
            # 如果只看共振，过滤掉“资金共振”单元格为空的行
            if row_data.get("资金共振", "") != "🔥":
                return False
                
        return super().filterAcceptsRow(source_row, source_parent)


class LhbTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        
        self.cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'Cache', 'lhb_cache.json'
        )
        self._init_ui()
        self._load_local_cache()
        
        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 顶部工具栏
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 6, 8, 6)
        
        lbl_title = QLabel("🔥 龙虎榜 (内资与外资共振)")
        lbl_title.setObjectName("tabTitle")
        header_layout.addWidget(lbl_title)
        
        self.lbl_status = QLabel("等待抓取...")
        self.lbl_status.setObjectName("tabSubtitle")
        header_layout.addWidget(self.lbl_status)
        header_layout.addStretch()

        # 日期选择器
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        # 默认设为今天
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setFixedWidth(130)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        header_layout.addWidget(self.date_edit)

        # 搜索框
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 搜索代码/名称/任意词...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._filter_table)
        header_layout.addWidget(self.search_box)
        
        # 只看共振 Checkbox
        self.chk_resonance = QCheckBox("只看 🔥内外资共振")
        self.chk_resonance.setChecked(False)
        self.chk_resonance.stateChanged.connect(self._on_resonance_checked)
        header_layout.addWidget(self.chk_resonance)

        self.btn_refresh = QPushButton("⚡ 抓取榜单")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._load_lhb_data)
        header_layout.addWidget(self.btn_refresh)
        
        layout.addLayout(header_layout)

        # 表格列配置
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值",
            "资金共振", "上榜日期", "上榜净买额(万)", 
            "机构净买(万)", "外资潜伏池", "机构家数", "换手率%", "上榜原因"
        ]
        self.table = VCPTableView(default_row_height=28)
        self.model = StockTableModel(self.columns)
        self.proxy_model = LhbFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

        # 列宽配置
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        default_widths = [60, 70, 60, 65, 80, 70, 90, 100, 90, 180, 80, 70, 200]
        for i, w in enumerate(default_widths):
            self.table.setColumnWidth(i, w)

        # 持久化表头
        self.bind_header_persistence(self.table, "lhb_header_state_v1")
        
        # 强制清除 UI 表头在恢复时可能带来的默认物理排序干扰，将排序权交还给底层的物理排序逻辑
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        # 交互配置：双击K线，右键菜单
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table, 1)

    def _filter_table(self):
        search_text = self.search_box.text().strip().lower()
        self.proxy_model.setFilterText(search_text)

    def _on_resonance_checked(self, state):
        self.proxy_model.set_resonance_filter(self.chk_resonance.isChecked())

    def _load_lhb_data(self):
        date_str = self.date_edit.date().toString("yyyyMMdd")
        self.lbl_status.setText(f"正在穿透 {date_str} 机构与外资席位底牌...")
        self.btn_refresh.setEnabled(False)
        self.model.update_data([])
        
        def _fetch_task():
            return fetch_lhb_data_for_date(date_str)
            
        task_manager.run_in_background(
            _fetch_task, 
            task_id="lhb_fetch_task",
            on_success=self._on_data_fetched
        )

    def _on_data_fetched(self, records):
        self.btn_refresh.setEnabled(True)
        if not records:
            self.lbl_status.setText("❌ 该日期暂无龙虎榜数据，请确认是否为交易日，或数据暂未更新。")
            return
            
        # 首先对 records 进行多维排序：优先共振(True排前面)，再按涨幅%降序排列
        def _sort_key(x):
            is_res = 1 if x.get("资金共振") else 0
            pct_val = 0.0
            try:
                pct = x.get("涨幅%")
                if pct is not None and pct != "--":
                    pct_val = float(str(pct).replace('%', ''))
            except:
                pass
            return (is_res, pct_val)
            
        records.sort(key=_sort_key, reverse=True)
            
        # 格式化 UI 展示需求
        row_data = []
        res_count = 0
        for rec in records:
            # 深拷贝以便加UI专属的表情包
            row_dict = dict(rec)
            
            if row_dict.get("资金共振"):
                row_dict["资金共振"] = "🔥"
                res_count += 1
            else:
                row_dict["资金共振"] = ""
            
            # 使用 base tab 的占位符（避免现价未更新被当成0），依赖大一统全局刷新
            row_dict["现价"] = "--"
            row_dict["涨幅%"] = "--"
            row_data.append(row_dict)

        self.model.update_data(row_data)
        
        date_str = self.date_edit.date().toString("yyyyMMdd")
        self._save_local_cache(row_data, date_str, res_count)
        
        # 提取完数据后，触发一次全局通知，让关注池能自动扫描到并固化新数据
        from core.event_bus import event_bus
        event_bus.sig_cache_loaded.emit()
        
        self.lbl_status.setText(f"✅ {date_str} 抓取完毕：共 {len(row_data)} 条上榜记录，其中 {res_count} 只个股达成机构外资资金共振。")
        
        # 统一异步刷新市值与现价
        self.async_update_market_caps()

    def _save_local_cache(self, row_data, date_str, res_count):
        """将当前抓取的数据落盘缓存"""
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            cache_obj = {
                "date_str": date_str,
                "res_count": res_count,
                "rows": row_data
            }
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_obj, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"[龙虎榜] 写入本地缓存失败: {e}")

    def _load_local_cache(self):
        """启动时加载本地缓存"""
        if not os.path.exists(self.cache_path):
            return
            
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                cache_obj = json.load(f)
            
            date_str = cache_obj.get("date_str", "")
            res_count = cache_obj.get("res_count", 0)
            rows = cache_obj.get("rows", [])
            
            if rows:
                self.model.update_data(rows)
                self.lbl_status.setText(f"📂 缓存加载完成：{date_str} 共 {len(rows)} 条上榜记录，{res_count} 只共振。")
                
                if date_str and len(date_str) == 8:
                    qdate = QDate.fromString(date_str, "yyyyMMdd")
                    if qdate.isValid():
                        self.date_edit.setDate(qdate)
                        
        except Exception as e:
            log.warning(f"[龙虎榜] 加载本地缓存失败: {e}")

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

