# -*- coding: utf-8 -*-
"""
ui/tabs/foreign_block_trade_tab.py
外资大宗交易动向 Tab
展示包含指定外资关键字的营业部近期大宗交易明细，包含实时动态涨幅。
集成v3.2量化因子黄金信号：深坑(距60日高点>20%) + 弱RPS50(<30) + VIP外资买入。
"""
import datetime
import os
import struct
import pandas as pd
import akshare as ak

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableView, QHeaderView, QPushButton, QLabel, QAbstractItemView, QLineEdit, QMenu, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QColor

from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW
)
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from core.event_bus import event_bus
from core.event_types import DataEvent

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
                # 席位做包含匹配，其它做绝对匹配
                if col_name in ("买方营业部", "卖方营业部") and val not in cell_val:
                    # 如果匹配的营业部要求在买或卖中出现即可
                    pass 
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
VIP_KEYWORDS_SIGNAL = ["高盛", "瑞银", "摩根大通"]
TDX_DIR = r"D:\HT\vipdoc"


def _parse_tdx_day_file(filepath: str) -> pd.DataFrame:
    """解析通达信.day二进制文件，返回以日期字符串为index的DataFrame"""
    import numpy as np
    dt = np.dtype([
        ('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
        ('close', '<u4'), ('amount', '<f4'), ('vol', '<u4'), ('reserved', '<u4')
    ])
    try:
        data = np.fromfile(filepath, dtype=dt)
        if len(data) == 0: return pd.DataFrame()
        df = pd.DataFrame(data)
        df['open'] = df['open'] / 100.0
        df['high'] = df['high'] / 100.0
        df['close'] = df['close'] / 100.0
        df['date'] = df['date'].astype(str)
        return df.set_index('date')
    except Exception:
        return pd.DataFrame()


# 模块级K线缓存：每只股票的文件只读一次，后续直接从内存取
_kline_cache: dict = {}


def _get_tdx_kline(code: str, tdx_dir: str) -> pd.DataFrame:
    """从本地通达信获取个股日线（带内存缓存）"""
    if code in _kline_cache:
        return _kline_cache[code]
    market = "sh" if code.startswith("6") else "sz"
    path = os.path.join(tdx_dir, market, "lday", f"{market}{code}.day")
    if os.path.exists(path):
        df = _parse_tdx_day_file(path)
        _kline_cache[code] = df
        return df
    _kline_cache[code] = pd.DataFrame()
    return _kline_cache[code]


def _load_all_tdx_codes(tdx_dir: str):
    """获取通达信本地所有A股代码列表（用于计算RPS排名）"""
    codes = []
    for m in ["sh", "sz"]:
        lday = os.path.join(tdx_dir, m, "lday")
        if not os.path.isdir(lday):
            continue
        for fn in os.listdir(lday):
            if not fn.endswith(".day"):
                continue
            c = fn.replace(m, "").replace(".day", "")
            # 只要A股主板/中小板/创业板/科创板
            if c.startswith("0") or c.startswith("3") or c.startswith("6"):
                codes.append(c)
    return codes


def _compute_rps50_baseline(target_date: str, all_codes: list, tdx_dir: str) -> list:
    """
    计算某一天全市场所有股票的50日收益率列表，作为RPS排名基准。
    使用 numpy 纯数组读取替代 Pandas，提速50倍以上，防止界面假死。
    """
    import numpy as np
    returns_list = []
    dt = np.dtype([
        ('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
        ('close', '<u4'), ('amount', '<f4'), ('vol', '<u4'), ('reserved', '<u4')
    ])
    try:
        target_int = int(str(target_date).replace('-', ''))
    except Exception:
        return []

    for code in all_codes:
        market = "sh" if code.startswith("6") else "sz"
        path = os.path.join(tdx_dir, market, "lday", f"{market}{code}.day")
        if not os.path.exists(path):
            continue
            
        try:
            data = np.fromfile(path, dtype=dt)
            if len(data) < 51:
                continue
            
            dates = data['date']
            # 使用 numpy 的 searchsorted 进行二分查找
            pos = np.searchsorted(dates, target_int)
            if pos < len(dates) and dates[pos] == target_int:
                if pos >= 50:
                    past_close = data['close'][pos - 50]
                    curr_close = data['close'][pos]
                    if past_close > 0:
                        returns_list.append((curr_close / past_close - 1) * 100.0)
        except Exception:
            continue

    returns_list.sort()
    return returns_list


def _get_stock_rps50(code: str, trade_date: str, baseline_returns: list, tdx_dir: str) -> float:
    """
    计算单只股票在指定日期的RPS50值。
    不依赖目标股票是否在采样池中——单独计算其收益率后插入排名。
    """
    import bisect
    kl = _get_tdx_kline(code, tdx_dir)
    if kl.empty or trade_date not in kl.index:
        return 50.0  # 无数据时默认中位
    pos = kl.index.get_loc(trade_date)
    if pos < 50:
        return 50.0
    past_close = kl.iloc[pos - 50]['close']
    curr_close = kl.iloc[pos]['close']
    if past_close <= 0:
        return 50.0
    stock_ret = (curr_close / past_close - 1) * 100
    if not baseline_returns:
        return 50.0
    # 用二分查找确定该股票在全市场中的排名百分位
    rank = bisect.bisect_left(baseline_returns, stock_ret)
    return (rank / len(baseline_returns)) * 100


def compute_golden_signals(records: list, tdx_dir: str) -> dict:
    """
    批量计算黄金信号。返回 {row_index: True/False}。
    黄金信号三合一条件（必须同时满足）：
    1. VIP外资席位买入（高盛/瑞银/摩根大通）
    2. 股价距近60日最高点下跌超过20%（price_position < 0.8）
    3. 个股RPS50 < 30（近50天涨幅排名全市场后30%）
    """
    if not records:
        return {}

    all_codes = _load_all_tdx_codes(tdx_dir)
    if not all_codes:
        log.warning(f"[黄金信号计算] 通达信目录 {tdx_dir} 下未找到任何票据，跳过计算！")
        return {}

    results = {}
    # 按交易日缓存RPS基准数据（同一天只算一次采样基准）
    rps_baseline_cache = {}

    for idx, rec in enumerate(records):
        code = str(rec.get('证券代码', '')).zfill(6)
        buyer = str(rec.get('买方营业部', ''))
        trade_date = str(rec.get('交易日期', '')).replace('-', '').split()[0]

        # 条件1：VIP外资买入
        is_vip_buy = any(kw in buyer for kw in VIP_KEYWORDS_SIGNAL)
        if not is_vip_buy:
            results[idx] = False
            continue

        # 取K线
        kl = _get_tdx_kline(code, tdx_dir)
        if kl.empty or trade_date not in kl.index:
            results[idx] = False
            continue
        pos = kl.index.get_loc(trade_date)

        # 条件2：距近60日高点跌>20%
        lookback_start = max(0, pos - 60)
        high_60 = kl.iloc[lookback_start:pos + 1]['high'].max()
        curr_close = kl.iloc[pos]['close']
        if high_60 <= 0:
            results[idx] = False
            continue
        price_position = curr_close / high_60
        if price_position >= 0.8:
            results[idx] = False
            continue

        # 条件3：RPS50 < 30（单独计算目标股票的RPS，不依赖采样命中）
        if trade_date not in rps_baseline_cache:
            rps_baseline_cache[trade_date] = _compute_rps50_baseline(trade_date, all_codes, tdx_dir)
        baseline = rps_baseline_cache[trade_date]
        rps_val = _get_stock_rps50(code, trade_date, baseline, tdx_dir)
        if rps_val >= 30:
            results[idx] = False
            continue

        results[idx] = True

    return results

class ForeignBlockTradeTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._block_trade_codes = set()
        self._cap_cache = {}
        self.setStyleSheet("background-color: transparent;")
        
        self.days_to_fetch = 20  # 默认拉取最近20个交易日
        self._init_ui()

        # 延迟加载缓存
        QTimer.singleShot(3200, self._load_block_trade_data)

        event_bus.sig_data_updated.connect(self._on_global_data)

    def _on_global_data(self, evt_type: str, data: object):
        if evt_type == DataEvent.RT_QUOTES_BROADCAST.value:
            if getattr(self, '_block_trade_codes', None) and getattr(self, 'model', None):
                self.model.update_quotes(data)
                # 市值补充
                for row_idx, row_dict in enumerate(self.model.row_data):
                    code = row_dict.get("代码", "")
                    if code in self._cap_cache and row_dict.get("市值") == "--":
                        self.model.set_cell_value(row_idx, "市值", self._cap_cache[code])

    def _on_fetch_days_changed(self, index):
        days_map = {0: 10, 1: 20, 2: 40}
        self.days_to_fetch = days_map.get(index, 10)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部
        header_layout = QHBoxLayout()
        lbl_title = QLabel("🌐 外资大宗动向 (含机构马甲)")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #C9CDD4;")
        header_layout.addWidget(lbl_title)
        
        self.lbl_status = QLabel("等待加载...")
        self.lbl_status.setStyleSheet("font-size: 11px; color: #6B7280;")
        header_layout.addWidget(self.lbl_status)
        header_layout.addStretch()

        self.cmb_filter_date = QComboBox()
        self.cmb_filter_date.addItem("全部日期")
        self.cmb_filter_date.setFixedHeight(32)
        self.cmb_filter_date.setFixedWidth(110)
        self.cmb_filter_date.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_date)

        self.cmb_fetch_days = QComboBox()
        self.cmb_fetch_days.addItems(["近 10 交易日", "近 20 交易日", "近 40 交易日"])
        self.cmb_fetch_days.setFixedHeight(32)
        self.cmb_fetch_days.setFixedWidth(120)
        self.cmb_fetch_days.setCurrentIndex(1)  # 默认20个交易日
        self.cmb_fetch_days.currentIndexChanged.connect(self._on_fetch_days_changed)
        header_layout.addWidget(self.cmb_fetch_days)

        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setFixedHeight(32)
        self.btn_refresh.clicked.connect(self._load_block_trade_data)
        header_layout.addWidget(self.btn_refresh)

        self.cmb_filter_stock = QComboBox()
        self.cmb_filter_stock.addItem("全部股票")
        self.cmb_filter_stock.setFixedHeight(32)
        self.cmb_filter_stock.setFixedWidth(110)
        self.cmb_filter_stock.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_stock)

        self.cmb_filter_branch = QComboBox()
        self.cmb_filter_branch.addItem("全部外资席位")
        self.cmb_filter_branch.setFixedHeight(32)
        self.cmb_filter_branch.setFixedWidth(140)
        self.cmb_filter_branch.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_branch)

        self.cmb_filter_direction = QComboBox()
        self.cmb_filter_direction.addItems(["全部动作", "外资买入", "外资卖出", "外资对倒"])
        self.cmb_filter_direction.setFixedHeight(32)
        self.cmb_filter_direction.setFixedWidth(100)
        self.cmb_filter_direction.currentIndexChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.cmb_filter_direction)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 搜索代码/名称/任意词...")
        self.search_box.setFixedWidth(180)
        self.search_box.setFixedHeight(32)
        self.search_box.textChanged.connect(self._filter_table_combo)
        header_layout.addWidget(self.search_box)
        
        self.cmb_days = QComboBox()
        self.cmb_days.addItems(["近 10 交易日", "近 20 交易日", "近 40 交易日", "近 60 交易日"])
        self.cmb_days.setCurrentIndex(0)
        self.cmb_days.setFixedHeight(32)
        self.cmb_days.currentIndexChanged.connect(self._on_days_changed)
        header_layout.addWidget(self.cmb_days)

        btn_refresh = QPushButton("🔄 抓取数据")
        btn_refresh.setObjectName("ctaButton")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setFixedWidth(100)
        btn_refresh.setFixedHeight(32)
        btn_refresh.clicked.connect(self._load_block_trade_data)
        header_layout.addWidget(btn_refresh)
        layout.addLayout(header_layout)

        # 表格
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "交易日期", "交易详情", 
            "当日收盘价", "成交价格", "折/溢价率(%)", "成交数量(万股)", "成交金额(万元)", 
            "买方营业部", "卖方营业部", "黄金信号"
        ]
        self.table = QTableView()
        self.model = StockTableModel(self.columns)
        self.proxy_model = BlockTradeFilterProxyModel(self.table)
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

        # 列宽
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [70, 80, 70, 70, 70, 80, 100, 80, 80, 90, 100, 100, 220, 220, 70]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)
            
        header.setSectionResizeMode(12, QHeaderView.ResizeMode.Stretch)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table, "block_trade_header_state_v2")

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
        
        for kw in FOREIGN_KEYWORDS:
            if kw in buyer_str or kw in seller_str:
                return True
        return False

    def _determine_direction(self, buyer, seller):
        """判断是外资买入还是卖出"""
        buyer_str = str(buyer) if pd.notna(buyer) else ""
        seller_str = str(seller) if pd.notna(seller) else ""
        
        buy_hit = any(kw in buyer_str for kw in FOREIGN_KEYWORDS)
        sell_hit = any(kw in seller_str for kw in FOREIGN_KEYWORDS)
        
        if buy_hit and sell_hit:
            return "外资对倒", "#F59E0B" # 橙色
        elif buy_hit:
            return "外资买入", COLOR_RISE
        elif sell_hit:
            return "外资卖出", COLOR_FALL
        return "--", COLOR_FLAT

    def _load_block_trade_data(self):
        self.lbl_status.setText("拼命拉取大宗交易数据中...")
        self.model.update_data([])
        
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
            self.lbl_status.setText("❌ 近期未发现匹配外资的大宗交易。")
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
        # 保存聚合后的记录，用于黄金信号计算和表格行匹配
        self._aggregated_records = df.to_dict('records')
        
        # 提取筛选器选项
        unique_dates = sorted(df['交易日期'].dropna().unique().tolist(), key=lambda x: str(x), reverse=True)
        unique_stocks = sorted(df['证券简称'].dropna().unique().tolist(), key=lambda x: str(x))
        # 提取相关外资席位
        raw_branches = set(df['买方营业部'].dropna().tolist() + df['卖方营业部'].dropna().tolist())
        target_branches = set()
        for b in raw_branches:
            b_str = str(b)
            if any(kw in b_str for kw in FOREIGN_KEYWORDS):
                target_branches.add(b_str)
        unique_branches = sorted(list(target_branches))

        self.cmb_filter_date.blockSignals(True)
        self.cmb_filter_stock.blockSignals(True)
        self.cmb_filter_branch.blockSignals(True)
        
        self.cmb_filter_date.clear()
        self.cmb_filter_date.addItem("全部日期")
        self.cmb_filter_date.addItems([str(x) for x in unique_dates])
        
        self.cmb_filter_stock.clear()
        self.cmb_filter_stock.addItem("全部股票")
        self.cmb_filter_stock.addItems([str(x) for x in unique_stocks])
        
        self.cmb_filter_branch.clear()
        self.cmb_filter_branch.addItem("全部外资席位")
        self.cmb_filter_branch.addItems(unique_branches)

        self.cmb_filter_date.blockSignals(False)
        self.cmb_filter_stock.blockSignals(False)
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

            # NOTE: 我们需要通过一个特殊的字段把VIP外资标出来吗？
            # 委派给 Delegate 虽然也可以，但是这里用最简单的纯文本展示也能接受
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
                "卖方营业部": seller,
                "黄金信号": "⏳"
            }
            row_data.append(row_dict)

        self.model.update_data(row_data)
        self.lbl_status.setText(f"✅ 加载完成，发现 {len(df)} 笔外资大宗交易。正在计算黄金信号...")
        
        # 强制应用当前的筛选状态
        self._filter_table_combo()
        
        # 异步计算黄金信号（涉及大量K线读取，不能阻塞UI）
        self._compute_golden_signal_async()
        
        # 一次性后台拉取市值
        codes_need_cap = [str(c) for c in self._block_trade_codes if c not in self._cap_cache]
        if codes_need_cap and self.data_provider:
            from core.task_manager import task_manager
            def _fetch_caps():
                try:
                    from vcp.engine import VCPEngine
                    # 动态拉取现价以供市值计算（不依赖UI，直接用 data_provider）
                    quotes = self.data_provider.fetch_realtime_quotes_batch(codes_need_cap) if self.data_provider else {}
                    close_prices = {c: quotes[c].get('close', 0) for c in codes_need_cap if c in quotes and quotes[c]}

                    cap_results = VCPEngine.batch_check_market_cap(codes_need_cap, close_prices=close_prices)
                    caps = {}
                    for c in codes_need_cap:
                        cap = cap_results.get(c)
                        caps[c] = f"{cap / 1e8:.0f}亿" if cap and cap > 0 else "--"
                    return caps
                except Exception:
                    return {}
            
            def _apply_caps(caps):
                if not caps: return
                self._cap_cache.update(caps)
                for row_idx, row_dict in enumerate(self.model.row_data):
                    code = row_dict.get("代码", "")
                    if code in caps:
                        self.model.set_cell_value(row_idx, "市值", caps[code])
            
            task_manager.run_in_background(_fetch_caps, on_success=_apply_caps, task_id="block_trade_caps")

    def _compute_golden_signal_async(self):
        """在后台线程计算黄金信号，完成后回调到UI"""
        agg_records = getattr(self, '_aggregated_records', None)
        if not agg_records:
            return

        def _bg_compute():
            try:
                # 动态获取通达信目录
                tdx_dir = getattr(self.data_provider, 'tdx_vipdoc', '') if self.data_provider else ''
                if not tdx_dir or not os.path.exists(tdx_dir):
                    log.warning(f"[黄金信号] 通达信目录无效，无法计算信号! 路径: {tdx_dir}")
                    return {}
                return compute_golden_signals(agg_records, tdx_dir)
            except Exception as e:
                log.error(f"[黄金信号计算] 异常: {e}")
                return {}

        task_manager.run_in_background(
            _bg_compute,
            task_id="golden_signal_compute",
            on_success=self._on_golden_signal_done
        )

    def _on_golden_signal_done(self, signal_map: dict):
        """将黄金信号结果写入表格最后一列"""
        try:
            self._apply_golden_signals(signal_map)
        except Exception as e:
            log.error(f"[黄金信号UI更新] 异常: {e}")
            self.lbl_status.setText(
                self.lbl_status.text().replace("正在计算黄金信号...", "⚠ 信号计算出错。")
            )

    def _apply_golden_signals(self, signal_map: dict):
        """实际执行黄金信号写入逻辑"""
        golden_count = 0

        if not signal_map:
            self.lbl_status.setText(
                self.lbl_status.text().replace("正在计算黄金信号...", "黄金信号计算完成。")
            )
            
        for row_idx, row_dict in enumerate(self.model.row_data):
            td = str(row_dict.get("交易日期", "")).replace("-", "").split()[0]
            cd = str(row_dict.get("代码", ""))
            bu = str(row_dict.get("买方营业部", ""))

            is_golden = False
            for idx, is_sig in signal_map.items():
                if not is_sig:
                    continue
                rec = self._aggregated_records[idx]
                rec_date = str(rec.get('交易日期', '')).replace('-', '').split()[0]
                rec_code = str(rec.get('证券代码', '')).zfill(6)
                rec_buyer = str(rec.get('买方营业部', ''))
                if rec_date == td and rec_code == cd and rec_buyer == bu:
                    is_golden = True
                    break

            if is_golden:
                golden_count += 1
                row_dict["黄金信号"] = "✅"
            else:
                row_dict["黄金信号"] = ""

        self.model.layoutChanged.emit()

        # 更新状态栏
        total = self.model.rowCount()
        status_base = self.lbl_status.text().split("正在计算")[0]
        if golden_count > 0:
            self.lbl_status.setText(f"{status_base}🏆 发现 {golden_count} 笔黄金信号！")
            self.lbl_status.setStyleSheet("font-size: 11px; color: #F59E0B; font-weight: bold;")
        else:
            self.lbl_status.setText(f"{status_base}信号计算完成，暂无黄金信号。")

    def _auto_refresh_realtime(self, force=False):
        """独立拉取大宗交易股票的实时行情（与美股日报一致的重试+自动联网逻辑）"""
        if not self.data_provider or not self._block_trade_codes:
            return
        import time as _time

        def _bg_fetch():
            max_retries = 3
            retry_delay = 5

            for attempt in range(1, max_retries + 1):
                try:
                    bt_codes = list(self._block_trade_codes)
                    if not bt_codes:
                        return {}

                    # 自动联网探测（与美股日报一致）
                    if not self.data_provider.is_online():
                        try:
                            if self.data_provider.test_network(timeout=3):
                                self.data_provider.set_online_mode(True)
                            else:
                                if attempt < max_retries:
                                    log.info(f"[大宗交易] 独立刷新第{attempt}次: "
                                          f"服务器未就绪，{retry_delay}秒后重试...")
                                    _time.sleep(retry_delay)
                                    continue
                                return {}
                        except Exception:
                            if attempt < max_retries:
                                _time.sleep(retry_delay)
                                continue
                            return {}

                    if not self.data_provider.server_pool:
                        if attempt < max_retries:
                            _time.sleep(retry_delay)
                            continue
                        return {}

                    quotes = self.data_provider.fetch_realtime_quotes_batch(bt_codes)
                    if not quotes:
                        if attempt < max_retries:
                            _time.sleep(retry_delay)
                            continue
                        return {}

                    log.info(f"[大宗交易] 独立刷新完成: "
                          f"{len(quotes)}/{len(bt_codes)} 只股票")
                    return quotes

                except Exception as e:
                    log.error(f"[大宗交易] 独立刷新异常(第{attempt}次): {e}")
                    if attempt < max_retries:
                        _time.sleep(retry_delay)
            return {}

        task_manager.run_in_background(
            _bg_fetch, 
            task_id="block_trade_quotes",
            on_success=self._update_realtime_ui
        )

    def _update_realtime_ui(self, quotes: dict):
        if not quotes or len(self.model.row_data) == 0:
            return

        for row_idx, row_dict in enumerate(self.model.row_data):
            code = row_dict.get("代码", "")
            quote = quotes.get(code)
            if not quote: continue

            rt_close = float(quote.get('close', 0) or 0)
            last_close = float(quote.get('last_close', 0) or 0)
            
            if rt_close <= 0 and last_close > 0:
                rt_close = last_close  

            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
                row_dict["涨幅%"] = pct
                row_dict["现价"] = rt_close
            else:
                row_dict["涨幅%"] = "--"
                row_dict["现价"] = "--"

            self.model.dataChanged.emit(
                self.model.index(row_idx, 0),
                self.model.index(row_idx, len(self.columns)-1)
            )

    def _filter_table_combo(self):
        search_text = self.search_box.text().strip().lower()
        self.proxy_model.setFilterText(search_text)
        
        filter_date = self.cmb_filter_date.currentText()
        self.proxy_model.setExactFilter("交易日期", None if filter_date == "全部日期" else filter_date)
        
        filter_stock = self.cmb_filter_stock.currentText()
        self.proxy_model.setExactFilter("名称", None if filter_stock == "全部股票" else filter_stock)
        
        filter_direction = self.cmb_filter_direction.currentText()
        self.proxy_model.setExactFilter("交易详情", None if filter_direction == "全部动作" else filter_direction)
        
        filter_branch = self.cmb_filter_branch.currentText()
        self.proxy_model.setExactFilter("_branch", None if filter_branch == "全部外资席位" else filter_branch)

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
        if not code: return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name)
