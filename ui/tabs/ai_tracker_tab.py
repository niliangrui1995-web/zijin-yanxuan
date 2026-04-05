# -*- coding: utf-8 -*-
"""
ui/tabs/ai_tracker_tab.py
AI产业链得分 独立 Tab 组件 — 从 AITrackerMixin 解耦重构为完全自治的 QWidget
从 AI_Data_Tracker CSV 加载得分>0 的股票
展示字段: 序号, 代码, 名称, 涨幅, 市值, 得分, 细分行业, 原因
"""
import os
import glob

import pandas as pd
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableView,
    QHeaderView, QPushButton, QLabel, QAbstractItemView, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer


from ui.models.table_models import StockTableModel, RtSortFilterProxyModel, StockItemDelegate
from core.event_bus import event_bus
from core.event_types import DataEvent
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


class AITrackerTab(BaseStockTab):
    """AI产业链得分 独立 Tab: 从最新 CSV 读取 AI含量>0 的股票并展示"""

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._ai_tracker_codes = set()
        self._cap_cache_ai_tracker = {}
        self.setStyleSheet("background-color: transparent;")

        self._init_ui()


        # 延迟加载
        QTimer.singleShot(600, self._load_ai_tracker_data)

        # 订阅全系统事件总线接收盘中广播流
        event_bus.sig_data_updated.connect(self._on_global_data)

    def _on_global_data(self, evt_type: str, data: object):
        if evt_type == DataEvent.RT_QUOTES_BROADCAST.value:
            if getattr(self, '_ai_tracker_codes', None) and getattr(self, 'source_model', None):
                self.source_model.update_quotes(data)

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部
        header_layout = QHBoxLayout()
        lbl_title = QLabel("🤖 AI产业链得分 — 非零标的")
        lbl_title.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #C9CDD4;"
        )
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        self.ai_tracker_source_label = QLabel("未加载")
        self.ai_tracker_source_label.setStyleSheet(
            "font-size: 11px; color: #6B7280;"
        )
        header_layout.addWidget(self.ai_tracker_source_label)

        # 搜索过滤
        self.ai_tracker_search = QLineEdit()
        self.ai_tracker_search.setPlaceholderText("🔍 搜索代码/名称/拼音...")
        self.ai_tracker_search.setFixedWidth(180)
        self.ai_tracker_search.setFixedHeight(32)
        self.ai_tracker_search.textChanged.connect(self._filter_table)
        header_layout.addWidget(self.ai_tracker_search)

        btn_refresh = QPushButton("🔄 刷新数据")
        btn_refresh.setObjectName("ctaButton")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setFixedWidth(120)
        btn_refresh.clicked.connect(self._load_ai_tracker_data)
        header_layout.addWidget(btn_refresh)
        layout.addLayout(header_layout)

        # 表格 MVC
        self.columns = ["代码", "名称", "现价", "涨幅%", "市值", "得分", "细分行业", "原因"]
        self.source_model = StockTableModel(self.columns)
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        
        self.ai_tracker_table = QTableView()
        self.ai_tracker_table.setModel(self.proxy_model)
        self.ai_tracker_table.setItemDelegate(StockItemDelegate(self.ai_tracker_table))
        
        self.ai_tracker_table.setAlternatingRowColors(True)
        self.ai_tracker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ai_tracker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ai_tracker_table.setSortingEnabled(True)
        self.ai_tracker_table.verticalHeader().setVisible(False)
        self.ai_tracker_table.setShowGrid(False)
        self.ai_tracker_table.setStyleSheet(self.ai_tracker_table.styleSheet() + "::item { padding: 0px 10px; }")

        # 列宽
        header = self.ai_tracker_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [70, 80, 80, 80, 80, 55, 140, 200]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.ai_tracker_table.setColumnWidth(i, w)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.ai_tracker_table.verticalHeader().setDefaultSectionSize(40)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.ai_tracker_table, "header_state_ai_tracker_v2")

        self.ai_tracker_table.doubleClicked.connect(self._on_table_double_clicked)
        # 右键菜单
        self.ai_tracker_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.ai_tracker_table.customContextMenuRequested.connect(
            self._show_context_menu
        )

        layout.addWidget(self.ai_tracker_table, 1)

    # ================================================================
    # 数据加载
    # ================================================================
    def _find_latest_ai_csv(self) -> str:
        """查找最新的 AI_Data_Tracker CSV 文件"""
        ai_chain_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "AI_Chain"
        )
        pattern = os.path.join(ai_chain_dir, "**", "AI_Data_Tracker_*.csv")
        files = sorted(glob.glob(pattern, recursive=True))
        return files[-1] if files else ""

    def _load_ai_tracker_data(self):
        """从最新 CSV 加载AI含量>0的股票数据"""
        csv_path = self._find_latest_ai_csv()
        if not csv_path:
            self.ai_tracker_source_label.setText("❌ 未找到 AI_Data_Tracker CSV")
            return

        try:
            df = pd.read_csv(csv_path, dtype={"代码": str})
        except Exception as e:
            self.ai_tracker_source_label.setText(f"❌ 读取CSV失败: {e}")
            return

        df["AI含量"] = pd.to_numeric(df["AI含量"], errors="coerce").fillna(0)
        df_filtered = df[df["AI含量"] > 0].copy()
        df_filtered = df_filtered.sort_values(
            "AI含量", ascending=False
        ).reset_index(drop=True)

        filename = os.path.basename(csv_path)
        parent_dir = os.path.basename(os.path.dirname(csv_path))
        self.ai_tracker_source_label.setText(
            f"📄 {parent_dir}/{filename} ({len(df_filtered)}只)"
        )

        self._ai_tracker_codes = set(df_filtered["代码"].tolist())

        # 填充表格
        row_dicts = []
        for row, (_, record) in enumerate(df_filtered.iterrows()):
            code = str(record.get("代码", "")).zfill(6)
            name = str(record.get("股票名称", ""))
            score = float(record.get("AI含量", 0))
            industry = str(record.get("细分行业", ""))
            reason = str(record.get("原因", ""))

            row_dicts.append({
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "得分": score,
                "细分行业": industry,
                "原因": reason if reason != "nan" else ""
            })
            
        self.source_model.update_data(row_dicts)
        self.ai_tracker_table.sortByColumn(5, Qt.SortOrder.DescendingOrder)

        # 初始只触发一次市值获取（不需要放进 3秒循环里）
        if self._ai_tracker_codes:
            self._fetch_static_caps()

    # ================================================================
    # 实时报价
    # ================================================================
    def _fetch_static_caps(self):
        def _bg_cap():
            from vcp.engine import VCPEngine
            try:
                ai_codes = list(self._ai_tracker_codes)
                # First fetch quotes to get close prices for calculating cap using daily shares
                quotes = self.data_provider.fetch_realtime_quotes_batch(ai_codes)
                close_prices = {c: float(quotes[c].get('close', 0) or 0) for c in quotes if c in quotes}
                cap_results = VCPEngine.batch_check_market_cap(ai_codes, close_prices=close_prices)
                return {c: f"{cap_results[c]/1e8:.0f}亿" for c in cap_results if cap_results[c] and cap_results[c] > 0}
            except Exception as e:
                log.error(f"[AI跟踪] 市值初始化失败: {e}")
                return {}
        def _on_cap(caps):
            if not getattr(self, "source_model", None): return
            for row, d in enumerate(self.source_model.row_data):
                c = d.get("代码")
                if c in caps:
                    self.source_model.set_cell_value(row, "市值", caps[c])
        task_manager.run_in_background(_bg_cap, task_id="ai_tracker_caps", on_success=_on_cap)

    # ================================================================
    # 定时刷新与后台抓取 (从 EventBus 彻底解耦)
    # ================================================================

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_table_double_clicked(self, idx):
        if not idx.isValid(): return
        proxy_row = idx.row()
        
        code_list = []
        for r in range(self.proxy_model.rowCount()):
            c = str(self.proxy_model.data(self.proxy_model.index(r, 0)))
            n = str(self.proxy_model.data(self.proxy_model.index(r, 1)))
            code_list.append({'代码': c, '名称': n})
            
        current_code = str(self.proxy_model.data(self.proxy_model.index(proxy_row, 0)))
        event_bus.sig_show_kline_with_list.emit(current_code, code_list, proxy_row)

    def _show_context_menu(self, pos):
        index = self.ai_tracker_table.indexAt(pos)
        if not index.isValid(): return
            
        source_index = self.proxy_model.mapToSource(index)
        row_data = self.source_model.get_row_data(source_index.row())
        if not row_data: return

        code = str(row_data.get('代码', ''))
        name = str(row_data.get('名称', ''))
        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name)

    # ================================================================
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称、拼音首字母匹配"""
        from ui.components import SearchFilter
        SearchFilter.filter_table(self.ai_tracker_table, text, code_col=1, name_col=2)

    # _launch_tdx 已迁移至 BaseStockTab 基类

