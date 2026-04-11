# -*- coding: utf-8 -*-
from datetime import datetime
import pandas as pd
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QHeaderView, QPushButton, QLabel, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from ui.tabs.base_stock_tab import BaseStockTab
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView
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
        
        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()
        
        # 挂载后台总调度器
        self.scheduler = EarningsScheduler(self)
        self.scheduler.sig_new_surprises_found.connect(self._on_new_data_found)
        
        # 延后到事件循环空闲时再启动巡逻，避免构造阶段阻塞 UI。
        QTimer.singleShot(0, self.scheduler.start_patrol)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- 头部控制栏 ---
        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 8, 6)
        

        title = QLabel("业绩高增追踪")
        title.setObjectName("tabTitle")
        
        self.lbl_status = QLabel("监控挂机中...")
        self.lbl_status.setObjectName("tabSubtitle")
        
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
        
        header.addWidget(title)
        header.addWidget(self.lbl_status)
        header.addStretch()
        header.addWidget(QLabel("分类筛选:"))
        header.addWidget(self.combo_type_filter)
        header.addWidget(QLabel("更新区间倒推:"))
        header.addWidget(self.ent_start_date)
        header.addWidget(QLabel("-"))
        header.addWidget(self.ent_end_date)
        header.addWidget(self.btn_manual_fetch)
        
        layout.addLayout(header)

        # --- 表格显示区 ---
        self.table = VCPTableView(default_row_height=30)
        layout.addWidget(self.table)
        
        # 字段映射表：前四列必须是标准列（代码/名称/现价/涨幅%），以便接收盘中广播
        self.header_labels = [
            "代码", "名称", "现价", "涨幅%", "市值", "PE(TTM)",
            "环比%", "同比%", "当季利润", "上季利润", 
            "报告期", "类型", "揭晓日", "基调", "所属行业与概念"
        ]
        
        self.model = StockTableModel(self.header_labels)
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
            self.lbl_status.setText("日期格式错误，请使用 YYYY-MM-DD，例如 2024-03-01")
            return
            
        self.lbl_status.setText(f"正在拉取 {start_str} ~ {end_str} ({len(date_list)}天)...")
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

    @pyqtSlot(object, str)
    def _on_new_data_found(self, df: "pd.DataFrame", mode: str = "routine"):
        """当底层推上来新的 DataFrame 时，转成本地字典并无缝合并展示"""
        if df.empty:
            if mode == "warm_cache":
                self.lbl_status.setText("已恢复缓存，但当前没有可展示的高增股")
            else:
                self.lbl_status.setText("抓取完成，本轮无新增高增股")
            return

        if mode == "warm_cache":
            self.lbl_status.setText(f"已恢复缓存 {len(df)} 只高增股")
        else:
            self.lbl_status.setText(f"本次扫描新增 {len(df)} 只高增股")
        
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
                
        # 刷新视图
        self.model.update_data(self.row_data)
        
        # 通知关注池：业绩数据已就绪，重新拉取"业绩异动"列
        # 旧事件枚举链路已废弃，这里走专属刷新通道。
        event_bus.sig_earnings_updated.emit()
        
        # 统一异步刷新市值
        self.async_update_market_caps()

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

    def _on_rt_quotes_direct(self, quotes: dict):
        """重写基类的直达信号接收，在刷新行情后补充计算 PE(TTM)"""
        super()._on_rt_quotes_direct(quotes)
        self._recalc_pe_ttm()

    def _recalc_pe_ttm(self):
        """PE(TTM) = 市值 / (最新单季扣非利润 × 4)，两个数据表里都有，直接算"""
        for row_idx, r in enumerate(self.row_data):
            cap_str = str(r.get("市值", "--"))
            raw_profit = r.get("_raw_profit", 0)

            if cap_str == "--" or not raw_profit or raw_profit <= 0:
                continue

            try:
                # 解析市值字符串："150亿" → 150e8，"3200万" → 3200e4
                if "亿" in cap_str:
                    cap = float(cap_str.replace("亿", "")) * 1e8
                elif "万" in cap_str:
                    cap = float(cap_str.replace("万", "")) * 1e4
                else:
                    cap = float(cap_str)

                pe = cap / (raw_profit * 4.0)
                new_pe = f"{pe:.1f}"
                if r.get("PE(TTM)") != new_pe:
                    r["PE(TTM)"] = new_pe
                    if hasattr(self.model, 'dataChanged'):
                        self.model.dataChanged.emit(
                            self.model.index(row_idx, 5),
                            self.model.index(row_idx, 5)
                        )
            except (ValueError, ZeroDivisionError) as _e:
                log.debug(f"[业绩监控] PE 计算异常({cap_str}): {_e}")
