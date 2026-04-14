# -*- coding: utf-8 -*-
"""
ui/tabs/foreign_block_trade_tab.py
大宗交易监控 Tab
展示包含指定外资/机构关键字的营业部近期大宗交易明细，并高亮对倒、互砍等特殊行为。
"""
import datetime
import json
import os
import subprocess
import sys
import pandas as pd

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QHeaderView, QPushButton, QLabel, QLineEdit, QComboBox
)
from PyQt6.QtCore import Qt, QTimer

from ui.theme import (
    COLOR_RISE, COLOR_FALL, COLOR_FLAT
)
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView, TableStateWrapper
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
from core.task_manager import UserFacingTaskError, task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

FOREIGN_KEYWORDS = ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]
TARGET_KEYWORDS = FOREIGN_KEYWORDS + ["机构专用"]

# 模块级K线缓存：每只股票的文件只读一次，后续直接从内存取
_kline_cache: dict = {}
_PROXY_ENV_KEYS = [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
]
_BLOCK_TRADE_CHUNK_TIMEOUT = 15
_BLOCK_TRADE_CALENDAR_TIMEOUT = 10
_BLOCK_TRADE_MAX_RETRIES = 2
_BLOCK_TRADE_TOTAL_TIMEOUT = 45
_BLOCK_TRADE_TIMEOUT_USER_MESSAGE = (
    "抓取超时：45秒内未拿到完整结果。通常是当前网络较慢，"
    "或 VPN/代理影响了国内数据源；可稍后重试，必要时临时关闭 VPN 后再刷新。"
)
_AKSHARE_FETCH_SNIPPET = r"""
import json
import sys
import pandas as pd
import akshare as ak

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

mode = sys.argv[1]
if mode == "calendar":
    df = ak.tool_trade_date_hist_sina()
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    print(json.dumps(df['trade_date'].tolist(), ensure_ascii=False))
elif mode == "block_trade":
    start_date = sys.argv[2]
    end_date = sys.argv[3]
    df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        print("[]")
    else:
        print(df.to_json(orient="records", force_ascii=False, date_format="iso"))
"""


def _run_domestic_akshare(mode: str, *args, timeout: int = 15):
    env = os.environ.copy()
    for key in _PROXY_ENV_KEYS:
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0x08000000 if os.name == "nt" else 0
    cmd = [sys.executable, "-c", _AKSHARE_FETCH_SNIPPET, mode, *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        env=env,
        creationflags=creationflags,
        check=True,
    )
    payload = (proc.stdout or "").strip()
    if not payload:
        return []
    return json.loads(payload)


def _raise_block_trade_timeout(stage: str, detail: str = ""):
    extra = f"（{detail}）" if detail else ""
    raise UserFacingTaskError(
        _BLOCK_TRADE_TIMEOUT_USER_MESSAGE,
        f"大宗交易抓取超时：{stage}{extra}，45秒内未完成全部请求，"
        "可能是国内数据源响应慢或网络代理影响。",
    )


def _format_incomplete_message(timeout_chunks, failed_chunks):
    parts = []
    if timeout_chunks:
        parts.append(f"{len(timeout_chunks)} 个区间超时")
    if failed_chunks:
        parts.append(f"{len(failed_chunks)} 个区间失败")
    if not parts:
        return ""
    return "；" + "，".join(parts) + "，结果可能不完整"


def _normalize_trade_date_value(value) -> str:
    if value is None or pd.isna(value):
        return ""

    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""

    if text.isdigit():
        parsed = pd.NaT
        if len(text) == 13:
            parsed = pd.to_datetime(int(text), unit="ms", errors="coerce")
        elif len(text) == 10:
            parsed = pd.to_datetime(int(text), unit="s", errors="coerce")
        elif len(text) == 8:
            parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
        return text

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%Y-%m-%d")
    return text


def _normalize_trade_date_series(series: pd.Series) -> pd.Series:
    return series.apply(_normalize_trade_date_value)

class ForeignBlockTradeTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._block_trade_codes = set()
        self._cap_cache = {}
        self._is_loading = False
        
        self.days_to_fetch = 20  # 默认拉取最近20个交易日
        self._init_ui()

        # 延迟加载缓存
        QTimer.singleShot(3200, self._load_block_trade_data)

        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("等待加载...")

        # ── 筛选器组：按数据维度从大到小排列 ──
        self.cmb_filter_date = QComboBox()
        self.cmb_filter_date.addItem("全部日期")
        self.cmb_filter_date.setFixedWidth(128)
        self.cmb_filter_date.currentIndexChanged.connect(self._filter_table_combo)

        self.cmb_filter_branch = QComboBox()
        self.cmb_filter_branch.addItem("全部监控席位")
        self.cmb_filter_branch.setFixedWidth(152)
        self.cmb_filter_branch.currentIndexChanged.connect(self._filter_table_combo)

        self.cmb_filter_direction = QComboBox()
        self.cmb_filter_direction.addItems(["全部动作", "外资买入", "外资卖出", "外资对倒", "机构买入", "机构卖出", "机构对倒", "机构买/外资卖", "外资买/机构卖"])
        self.cmb_filter_direction.setFixedWidth(128)
        self.cmb_filter_direction.currentIndexChanged.connect(self._filter_table_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称或关键词...")
        self.search_box.setFixedWidth(240)
        self.search_box.textChanged.connect(self._filter_table_combo)

        # ── 数据拉取范围 ──
        self.cmb_days = QComboBox()
        self.cmb_days.addItems(["近 10 交易日", "近 20 交易日", "近 40 交易日", "近 60 交易日"])
        # 默认选中 20 交易日，与 self.days_to_fetch 初始值保持一致
        self.cmb_days.setCurrentIndex(1)
        self.cmb_days.setFixedWidth(148)
        self.cmb_days.currentIndexChanged.connect(self._on_days_changed)

        filter_widgets = [
            self.cmb_filter_date, self.cmb_filter_branch,
            self.cmb_filter_direction, self.search_box, self.cmb_days
        ]

        self.btn_refresh = QPushButton("刷新数据")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._load_block_trade_data)

        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("主力/外资大宗", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "交易日期", "交易详情", 
            "当日收盘价", "成交价格", "折/溢价率(%)", "成交数量(万股)", "成交金额(万元)", 
            "买方营业部", "卖方营业部"
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.model.set_plain_style_headers(["交易日期"])
        self.proxy_model = BlockTradeFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无大宗交易数据", loading_title="抓取中...")

        # 大宗交易默认按时间排序 由近到远
        self.table.sortByColumn(self.model.headers.index("交易日期"), Qt.SortOrder.DescendingOrder)

        # 列宽
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        # 严格压缩默认列宽，总和约1200px以内，确保即使在小屏幕/高缩放比下也不会超过屏幕宽度产生滚动条
        default_widths = [52, 60, 70, 55, 55, 55, 70, 85, 65, 65, 65, 75, 75, 140, 140]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置 (v4 强制刷新新布局)
        self.bind_header_persistence(self.table, "block_trade_header_state_v5")

        # 双击 → K线图
        self.table.doubleClicked.connect(self._on_double_click)
        # 右键菜单
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)
        
    def _on_days_changed(self, idx):
        days_map = {0: 10, 1: 20, 2: 40, 3: 60}
        self.days_to_fetch = days_map.get(idx, 10)
        self._load_block_trade_data()

    def _set_fetch_status(self, primary: str, *segments: str):
        self.lbl_status.setText(self.format_status_summary(primary, *segments))

    def _apply_cached_quotes_once(self):
        if not self._block_trade_codes:
            return

        try:
            from core.global_store import global_store
            snapshot = global_store.get_latest_quotes() or {}
        except Exception as exc:
            log.debug(f"[大宗交易] 读取全局报价快照失败: {exc}")
            return

        if not snapshot:
            return

        quote_subset = {
            code: dict(snapshot[code])
            for code in self._block_trade_codes
            if code in snapshot
        }
        if quote_subset:
            self._apply_quote_snapshot(quote_subset)

    def _collect_blank_quote_codes(self):
        blank_codes = []
        for row_dict in getattr(self.model, "row_data", []) or []:
            code = str(row_dict.get("代码", "")).strip().zfill(6)
            price_text = str(row_dict.get("现价", "")).strip()
            if code and price_text in {"", "--", "0", "0.0", "0.00"}:
                blank_codes.append(code)
        return list(dict.fromkeys(blank_codes))

    def _auto_refresh_realtime(self, force=False):
        if not self.data_provider:
            return

        codes = sorted(self._block_trade_codes)
        if not codes:
            return

        self._apply_cached_quotes_once()
        target_codes = codes if force else self._collect_blank_quote_codes()
        if not target_codes or task_manager.is_active_task("foreign_block_trade_quotes"):
            return

        def _bg_task():
            return self.data_provider.fetch_realtime_quotes_batch(target_codes)

        def _on_success(quotes):
            if quotes:
                event_bus.sig_rt_quotes.emit(quotes)

        def _on_error(error_message: str):
            if error_message:
                log.warning(f"[大宗交易] 单次补价失败: {error_message}")

        task_manager.run_in_background(
            _bg_task,
            on_success=_on_success,
            on_error=_on_error,
            task_id="foreign_block_trade_quotes",
        )

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
        if self._is_loading or task_manager.is_active_task("foreign_block_trade"):
            self._set_fetch_status("大宗抓取中", "上一轮任务尚未结束")
            return
        self._is_loading = True
        self.btn_refresh.setEnabled(False)
        self._set_fetch_status("正在抓取大宗交易", self._status_metric("窗口 ", self.days_to_fetch, "交易日"))
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在抓取大宗交易...", "请稍候")
        self.model.update_data([])
        # 清空上一轮的K线缓存，防止跨交易日窗口后内存只增不减
        _kline_cache.clear()
        
        def _fetch_task():
            import time

            end_dt = datetime.datetime.now()
            deadline = time.monotonic() + _BLOCK_TRADE_TOTAL_TIMEOUT
            
            # 使用交易日历倒推 start_dt
            try:
                remaining = max(5, int(deadline - time.monotonic()))
                dates = [
                    pd.to_datetime(d).date()
                    for d in _run_domestic_akshare(
                        "calendar",
                        timeout=min(_BLOCK_TRADE_CALENDAR_TIMEOUT, remaining)
                    )
                ]
                today_date = end_dt.date()
                past_dates = [d for d in dates if d <= today_date]
                if len(past_dates) >= self.days_to_fetch:
                    start_date_val = past_dates[-self.days_to_fetch]
                else:
                    start_date_val = past_dates[0] if past_dates else (today_date - datetime.timedelta(days=self.days_to_fetch))
                start_dt = datetime.datetime.combine(start_date_val, datetime.time())
            except subprocess.TimeoutExpired:
                log.warning("[大宗交易] 获取交易日历超时，回退到自然日估算")
                start_dt = end_dt - datetime.timedelta(days=int(self.days_to_fetch * 1.5))
            except Exception as e:
                log.warning(f"获取交易日历失败，使用自然日重估: {e}")
                start_dt = end_dt - datetime.timedelta(days=int(self.days_to_fetch * 1.5))

            results = []
            timeout_chunks = []
            failed_chunks = []
            finished_chunks = 0
            
            # 分段拉取：东财接口对大日期范围会截断或断连，每次只拉15天
            CHUNK_DAYS = 15
            cursor = start_dt
            while cursor < end_dt:
                if time.monotonic() >= deadline:
                    _raise_block_trade_timeout("总耗时超限")

                chunk_end = min(cursor + datetime.timedelta(days=CHUNK_DAYS), end_dt)
                s_str = cursor.strftime("%Y%m%d")
                e_str = chunk_end.strftime("%Y%m%d")
                chunk_key = f"{s_str}-{e_str}"
                chunk_done = False
                chunk_timed_out = False
                
                for attempt in range(_BLOCK_TRADE_MAX_RETRIES):
                    remaining = int(deadline - time.monotonic())
                    if remaining <= 0:
                        _raise_block_trade_timeout("分段抓取", chunk_key)
                    try:
                        records = _run_domestic_akshare(
                            "block_trade",
                            s_str,
                            e_str,
                            timeout=min(_BLOCK_TRADE_CHUNK_TIMEOUT, remaining),
                        )
                        df = pd.DataFrame(records) if records else pd.DataFrame()
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                if self._should_include_row(row.get('买方营业部'), row.get('卖方营业部')):
                                    results.append(row.to_dict())
                        chunk_done = True
                        finished_chunks += 1
                        break
                    except subprocess.TimeoutExpired:
                        chunk_timed_out = True
                        log.warning(f"[大宗交易] {chunk_key} 请求超时，可能是国内数据源响应慢或当前网络代理影响")
                        if attempt < _BLOCK_TRADE_MAX_RETRIES - 1:
                            time.sleep(1)
                    except Exception as e:
                        log.warning(f"[大宗交易] {chunk_key} 第{attempt+1}次失败: {e}")
                        if attempt < _BLOCK_TRADE_MAX_RETRIES - 1:
                            time.sleep(1)

                if not chunk_done:
                    if time.monotonic() >= deadline:
                        _raise_block_trade_timeout("分段重试后仍未完成", chunk_key)
                    if chunk_timed_out:
                        timeout_chunks.append(chunk_key)
                    else:
                        failed_chunks.append(chunk_key)

                cursor = chunk_end + datetime.timedelta(days=1)

            if not results and failed_chunks and finished_chunks == 0:
                raise UserFacingTaskError(
                    "抓取失败：本轮未拿到有效结果。通常是国内数据源响应慢或网络较差；可稍后重试。",
                    "大宗交易抓取失败：所有分段均未返回有效结果。",
                )

            return {
                "records": results,
                "timeout_chunks": timeout_chunks,
                "failed_chunks": failed_chunks,
            }
            
        task_manager.run_in_background(
            _fetch_task, 
            task_id="foreign_block_trade",
            on_success=self._on_data_fetched,
            on_error=self._on_data_fetch_failed
        )

    def _on_data_fetched(self, payload):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        if isinstance(payload, dict):
            data_list = payload.get("records", [])
            timeout_chunks = payload.get("timeout_chunks", [])
            failed_chunks = payload.get("failed_chunks", [])
        else:
            data_list = payload
            timeout_chunks = []
            failed_chunks = []

        if not data_list:
            self._block_trade_codes = set()
            if timeout_chunks or failed_chunks:
                self._set_fetch_status(
                    "大宗抓取未完成",
                    "本轮无有效结果",
                    _format_incomplete_message(timeout_chunks, failed_chunks).lstrip("；"),
                )
            else:
                self._set_fetch_status("近期无命中", self._status_metric("窗口 ", self.days_to_fetch, "交易日"))
            event_bus.sig_block_trade_updated.emit()
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无大宗交易数据")
            return
            
        df = pd.DataFrame(data_list)
        if '交易日期' in df.columns:
            df['交易日期'] = _normalize_trade_date_series(df['交易日期'])
        
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
        self._set_fetch_status(
            self._status_metric("命中 ", len(df), "笔"),
            self._status_metric("日期 ", len(unique_dates)),
            self._status_metric("席位 ", len(unique_branches)),
            self._status_metric("窗口 ", self.days_to_fetch, "交易日"),
            _format_incomplete_message(timeout_chunks, failed_chunks).lstrip("；"),
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()
        
        # 强制应用当前的筛选状态
        self._filter_table_combo()
        event_bus.sig_block_trade_updated.emit()
        
        # 统一异步刷新市值
        self.async_update_market_caps()
        self._auto_refresh_realtime()

    def _on_data_fetch_failed(self, error_message: str):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        msg = str(error_message or "").strip()
        if not msg:
            msg = "大宗交易抓取失败，请稍后重试。"
        elif not msg.startswith(("抓取超时", "抓取失败")):
            msg = f"大宗交易抓取失败：{msg}"
        self._set_fetch_status(msg)
        if hasattr(self, "table_state"):
            self.table_state.show_empty("暂无大宗交易数据")
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
