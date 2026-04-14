# -*- coding: utf-8 -*-
import os
import json
import datetime
from PyQt6.QtWidgets import (
    QVBoxLayout, QHeaderView, QPushButton, QLabel, QCheckBox, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView, TableStateWrapper

from ui.tabs.base_stock_tab import BaseStockTab
from ui.tabs.asian_market_meta import (
    format_market_display,
    get_ch_names_mapping,
    get_market_status,
    get_role_mapping,
)
from ui.tabs.asian_market_workers import (
    GLOBAL_ASIAN_RT_CACHE,
    JSON_CACHE,
    RT_JSON_CACHE,
    AsianCacheFetcherThread,
    AsianMarketWorker,
    is_cf_proxy_enabled,
    set_cf_proxy_enabled,
)
from core.event_bus import event_bus
from core.logger import get_logger

log = get_logger(__name__)

class AsianMarketTab(BaseStockTab):
    """亚洲寡头行情面板"""
    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self._init_ui()
        
        # 1. 冷开机瞬间加载本地 JSON (asian_klines_latest.json)
        self._load_local_cache()
        
        # 2. 启动后台 Worker, 进行 60 秒常态轮询
        codes = [item['代码'] for item in self.row_data]
        self.worker = AsianMarketWorker(codes)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.result_ready.connect(self._on_rt_update)
        # 等界面加载完稍微延后一点启动后台
        QTimer.singleShot(1000, self.worker.start)
        
        # 3. 监听全局数据更新事件 (如被 deferred_load 静默更新完毕)
        event_bus.sig_asian_klines_ready.connect(self._on_asian_klines_ready)
        
        # 4. 自动缓存校验器：每分钟检查本地缓存是否需要更新
        self.auto_cache_timer = QTimer(self)
        self.auto_cache_timer.timeout.connect(self._check_auto_cache)
        self.auto_cache_timer.start(60000)
        QTimer.singleShot(2000, self._check_auto_cache)

    def _schedule_fit_columns(self):
        if hasattr(self, "_fit_columns_timer"):
            self._fit_columns_timer.start()

    def _fit_asian_columns_to_viewport(self):
        if not hasattr(self, "asian_table"):
            return

        header = self.asian_table.horizontalHeader()
        column_count = header.count()
        if column_count <= 0:
            return

        viewport_width = self.asian_table.viewport().width()
        if viewport_width <= 0:
            return

        current_widths = [max(1, self.asian_table.columnWidth(i)) for i in range(column_count)]
        total_width = sum(current_widths)
        if total_width <= 0:
            return

        # 预留一点边缘冗余，避免 rounding 导致最后一列被横向滚动条挤爆。
        target_width = max(column_count * 40, viewport_width - 2)
        if abs(total_width - target_width) <= column_count:
            return

        scale = target_width / total_width
        scaled_widths = [max(40, int(round(width * scale))) for width in current_widths]
        diff = target_width - sum(scaled_widths)

        if diff != 0:
            step = 1 if diff > 0 else -1
            remaining = abs(diff)
            ordered_columns = sorted(
                range(column_count),
                key=lambda idx: current_widths[idx],
                reverse=(diff > 0),
            )
            while remaining > 0 and ordered_columns:
                changed = False
                for column in ordered_columns:
                    next_width = scaled_widths[column] + step
                    if next_width < 40:
                        continue
                    scaled_widths[column] = next_width
                    remaining -= 1
                    changed = True
                    if remaining == 0:
                        break
                if not changed:
                    break

        for column, width in enumerate(scaled_widths):
            self.asian_table.setColumnWidth(column, width)

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_fit_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_fit_columns()

    def _get_cache_latest_trade_date(self):
        try:
            if not os.path.exists(JSON_CACHE):
                return None
            with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            latest_date = None
            for item in raw.get('stocks', []):
                klines = item.get('klines', [])
                if not klines:
                    continue
                last_date_raw = str(klines[-1].get('date', '')).strip()
                if not last_date_raw:
                    continue
                try:
                    last_date = datetime.datetime.strptime(last_date_raw[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                if latest_date is None or last_date > latest_date:
                    latest_date = last_date
            return latest_date
        except Exception as e:
            log.warning(f"[AsianTab] parse cache latest trade date failed: {e}")
            return None

    def _get_expected_latest_trade_date(self):
        try:
            from core.market_calendar import MarketCalendar
            from datetime import timedelta
            markets = set()
            for row in getattr(self, 'row_data', []) or []:
                code = str(row.get("代码", "")).strip()
                if "." in code:
                    markets.add(code.split(".")[-1].upper())
            if not markets:
                markets = {"TW", "TWO", "T", "KS", "HK"}

            # 收盘缓冲时间：只有超过该时间，才把“当日”视作应落地到本地缓存的目标交易日。
            close_cutoff_hhmm = {
                "TW": 1400,
                "TWO": 1400,
                "HK": 1630,
                "T": 1530,
                "KS": 1600,
            }

            latest_expected = None
            for mkt in markets:
                now_mkt = MarketCalendar._get_market_now(mkt)
                today_mkt = now_mkt.date()
                hhmm = now_mkt.hour * 100 + now_mkt.minute
                cutoff = close_cutoff_hhmm.get(mkt, 1630)

                # 交易日但仍处于盘前/盘中：期望缓存仍是“上一交易日”。
                if MarketCalendar.is_trade_day(today_mkt, market=mkt) and hhmm < cutoff:
                    ref_date = today_mkt - timedelta(days=1)
                else:
                    ref_date = today_mkt

                trade_date = MarketCalendar.get_latest_trade_date(market=mkt, ref_date=ref_date)
                if trade_date is not None and (latest_expected is None or trade_date > latest_expected):
                    latest_expected = trade_date
            return latest_expected
        except Exception as e:
            log.warning(f"[AsianTab] calc expected trade date failed: {e}")
            return None

    def _check_auto_cache(self):
        import os
        from datetime import datetime, timedelta
        if getattr(self, '_is_fetching_cache', False):
            return

        now = datetime.now()

        target_dt = now.replace(hour=16, minute=30, second=0, microsecond=0)
        if now < target_dt:
            target_dt -= timedelta(days=1)
        while target_dt.weekday() >= 5:
            target_dt -= timedelta(days=1)

        mtime = 0
        if os.path.exists(JSON_CACHE):
            mtime = os.path.getmtime(JSON_CACHE)
        cache_dt = datetime.fromtimestamp(mtime) if mtime else datetime.min

        cache_latest_trade_date = self._get_cache_latest_trade_date()
        expected_latest_trade_date = self._get_expected_latest_trade_date()

        stale_by_mtime = cache_dt < target_dt
        stale_by_trade_date = (
            expected_latest_trade_date is not None and (
                cache_latest_trade_date is None or cache_latest_trade_date < expected_latest_trade_date
            )
        )

        if stale_by_mtime or stale_by_trade_date:
            self._is_fetching_cache = True
            reason = []
            if stale_by_mtime:
                reason.append(f"mtime<{target_dt.strftime('%m-%d %H:%M')}")
            if stale_by_trade_date:
                reason.append(f"trade_date {cache_latest_trade_date} < {expected_latest_trade_date}")
            reason_txt = ", ".join(reason) if reason else "unknown"
            log.info(
                f"[AsianTab] stale cache detected ({reason_txt}), "
                f"cache_mtime={cache_dt.strftime('%m-%d %H:%M')}, "
                f"cache_last_trade_date={cache_latest_trade_date}, "
                f"expected_last_trade_date={expected_latest_trade_date}"
            )
            self.lbl_status.setText("⏳ 正在自动同步 16:30 收盘后最新K线缓存...")
            self.cache_thread = AsianCacheFetcherThread()
            self.cache_thread.finished_sig.connect(self._on_auto_cache_finished)
            self.cache_thread.start()
    def _on_auto_cache_finished(self, success, msg):
        self._is_fetching_cache = False
        self.lbl_status.setText(msg)
        if success:
            self._load_local_cache()
            log.info("[AsianTab] 自动离线更新完成，已重载本地 K 线数据")

    def _on_asian_klines_ready(self):
        self._load_local_cache()
        self.lbl_status.setText("亚洲市场后台静默更新已就绪，K线已应用最新数据")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("系统初始化...")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        self.chk_cf_proxy = QCheckBox("启用直连通道 (CF隧道)")
        self.chk_cf_proxy.setToolTip("打勾：关闭VPN彻底裸连；不打勾：走您的VPN全局模式直连")
        self.chk_cf_proxy.setObjectName("successStatus")
        self.chk_cf_proxy.setChecked(is_cf_proxy_enabled())
        self.chk_cf_proxy.toggled.connect(self._on_cf_proxy_toggled)
        
        self.btn_refresh = QPushButton("网络检查与手动刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setToolTip("强制跳过等待，立刻请求外网(Yahoo Finance)测速并获取最新价格")
        self.btn_refresh.clicked.connect(self._on_manual_refresh)

        filter_widgets = [self.search_box, self.chk_cf_proxy]
        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("亚洲寡头核心资产监控", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self.asian_table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.asian_table, empty_title="暂无亚洲数据", loading_title="加载中...")
        layout.addWidget(self.table_state)
        
        self.header_labels = ["代码", "名称", "现价", "涨幅%", "市场", "状态", "赛道", "角色定位", "货币", "5日涨跌%", "10日涨跌%", "20日涨跌%"]
        
        self.model = StockTableModel(self.header_labels)
        self.model.set_plain_style_headers(["状态"])
        self.proxy_model = RtSortFilterProxyModel(self.asian_table)
        self.proxy_model.setSourceModel(self.model)
        self.asian_table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.asian_table)
        self.asian_table.setItemDelegate(self.delegate)

        # Context menu
        self.asian_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.asian_table.customContextMenuRequested.connect(self._show_context_menu)
        
        # Double click to open Kline
        self.asian_table.doubleClicked.connect(self._on_double_click)

        # 列宽自定义并持久化
        header_view = self.asian_table.horizontalHeader()
        header_view.setStretchLastSection(False)

        self._fit_columns_timer = QTimer(self)
        self._fit_columns_timer.setSingleShot(True)
        self._fit_columns_timer.setInterval(0)
        self._fit_columns_timer.timeout.connect(self._fit_asian_columns_to_viewport)

        default_widths = [52, 70, 140, 90, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        for i, w in enumerate(default_widths):
            header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.asian_table.setColumnWidth(i, w)
            
        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.asian_table, "header_state_asian_v3")
        self._schedule_fit_columns()

    def _show_context_menu(self, pos):
        index = self.asian_table.indexAt(pos)
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu
            build_stock_context_menu(self, code, name)

    def _on_cf_proxy_toggled(self, checked):
        set_cf_proxy_enabled(checked)
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setText(f"🔌 {'已切换为 CF 免翻墙专线' if checked else '已切换为 VPN 本地直连'}，下次刷新生效")

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)

    def _on_manual_refresh(self):
        """手动触发外网数据更新"""
        # 先重载本地缓存并补齐缺失标的，再触发实时刷新，确保 worker 不会长期只盯着旧的 33 只
        self._load_local_cache()
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.lbl_status.setText("⏳ 强制唤醒：正在请求海外接口测速与重载...")
            self.worker.trigger_refresh()
        else:
            self.lbl_status.setText("⚠ 后台网关未连接或已断开")

    def _load_local_cache(self):
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在加载本地缓存...", "请稍候")
        self.row_data = []
        if os.path.exists(JSON_CACHE):
            try:
                with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                    
                roles_map = get_role_mapping()
                ch_names_map = get_ch_names_mapping()
                stocks_list = raw.get('stocks', [])
                    
                for item in stocks_list:
                    code = item.get('ticker')
                    if not code: continue
                    data_points = item.get('klines', [])
                    close_val = 0.0
                    pct_val = 0.0
                    
                    if len(data_points) >= 2:
                        close_val = float(data_points[-1].get('close', 0))
                        prev_close = float(data_points[-2].get('close', 0))
                        if prev_close > 0:
                            pct_val = ((close_val / prev_close) - 1.0) * 100.0
                        
                    # 计算 5/10/20 日涨幅时防御除零（停牌或数据异常时 close 可能为 0）
                    def _safe_pct(cur, ref_val):
                        return ((cur / ref_val) - 1.0) * 100.0 if ref_val > 0 and cur > 0 else 0.0

                    if len(data_points) >= 6: pct_5 = _safe_pct(close_val, float(data_points[-6].get('close', 0)))
                    else: pct_5 = 0.0
                    if len(data_points) >= 11: pct_10 = _safe_pct(close_val, float(data_points[-11].get('close', 0)))
                    else: pct_10 = 0.0
                    if len(data_points) >= 21: pct_20 = _safe_pct(close_val, float(data_points[-21].get('close', 0)))
                    else: pct_20 = 0.0
                    
                    role_desc = roles_map.get(code, item.get('name', ''))
                    
                    mkt_str = item.get('market', code.split('.')[-1] if '.' in code else '')
                    # 禁用 Emoji 避免 Qt Windows C++ Access Violation 崩溃
                    mkt_val = format_market_display(mkt_str, code)
                    
                    # 取最后一天所在的 df 填入 globals (为了让它打开 K 线图时不会空)
                    if code not in GLOBAL_ASIAN_RT_CACHE:
                        GLOBAL_ASIAN_RT_CACHE[code] = {
                            "date": data_points[-1].get('date') if data_points else None,
                            "close": close_val,
                            "pct": pct_val,
                            "pct_5": pct_5,
                            "pct_10": pct_10,
                            "pct_20": pct_20,
                            "currency": item.get('currency', ''),
                            "df_today": None # 历史回切时先无细节 K 线
                        }
                    
                    # 初始化时直接计算当前确切的盘中/休市状态
                    real_status = get_market_status(code.split('.')[-1] if '.' in code else '')
                    
                    cv = float(close_val) if close_val else 0.0
                    fmt_close = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                    
                    row_obj = {
                        "代码": code,
                        "名称": f"{item.get('name', '')}  ({ch_names_map.get(code, '未录入')})" if ch_names_map.get(code) else item.get('name', ''),
                        "现价": fmt_close,
                        "涨幅%": pct_val,
                        "市场": mkt_val,
                        "状态": real_status, 
                        "赛道": item.get('track', ''),
                        "角色定位": role_desc,
                        "货币": item.get('currency', '---'),
                        "5日涨跌%": pct_5,
                        "10日涨跌%": pct_10,
                        "20日涨跌%": pct_20
                    }
                    self.row_data.append(row_obj)

                # 兜底保护：latest 若是“部分成功快照”（如 33/36），补齐缺失标的占位行，避免面板缩水
                try:
                    from vcp.fetchers.asian_kline_fetcher import filter_asian_tickers
                    target_map = filter_asian_tickers() or {}
                except Exception as _e:
                    target_map = {}
                    log.warning(f"[AsianTab] 读取亚洲目标池失败，跳过缺失补齐: {_e}")

                if target_map:
                    existing_codes = {
                        str(r.get("代码", "")).strip()
                        for r in self.row_data
                        if str(r.get("代码", "")).strip()
                    }
                    missing_codes = []

                    for en_name, tk in target_map.items():
                        tk = str(tk).strip()
                        if not tk or tk in existing_codes:
                            continue
                        missing_codes.append(tk)

                        mkt_str = tk.split('.')[-1] if '.' in tk else ''
                        mkt_val = format_market_display(mkt_str, tk)
                        role_desc = roles_map.get(tk, en_name)
                        real_status = get_market_status(mkt_str)

                        row_obj = {
                            "代码": tk,
                            "名称": f"{en_name}  ({ch_names_map.get(tk, '未录入')})" if ch_names_map.get(tk) else en_name,
                            "现价": "--",
                            "涨幅%": 0.0,
                            "市场": mkt_val,
                            "状态": real_status,
                            "赛道": "",
                            "角色定位": role_desc,
                            "货币": "---",
                            "5日涨跌%": 0.0,
                            "10日涨跌%": 0.0,
                            "20日涨跌%": 0.0,
                        }
                        self.row_data.append(row_obj)

                        if tk not in GLOBAL_ASIAN_RT_CACHE:
                            GLOBAL_ASIAN_RT_CACHE[tk] = {
                                "date": None,
                                "close": 0.0,
                                "pct": 0.0,
                                "pct_5": 0.0,
                                "pct_10": 0.0,
                                "pct_20": 0.0,
                                "currency": "",
                                "df_today": None,
                            }

                    if missing_codes:
                        log.warning(
                            f"[AsianTab] 本地缓存缺失 {len(missing_codes)} 只，已补齐占位行: {sorted(missing_codes)}"
                        )
            except Exception as e:
                log.error(f"[AsianTab] JSON 历史缓存加载失败: {e}")

        # --- 恢复退出时的最后一次盘口实时缓存 ---
        if os.path.exists(RT_JSON_CACHE):
            try:
                with open(RT_JSON_CACHE, 'r', encoding='utf-8') as f:
                    rt_cache = json.load(f)
                for row_dict in self.row_data:
                    code = row_dict.get("代码")
                    if code in rt_cache:
                        info = rt_cache[code]
                        cv = float(info.get('close', 0.0))
                        row_dict["现价"] = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                        row_dict["涨幅%"] = info.get('pct', 0.0)
                        row_dict["5日涨跌%"] = info.get('pct_5', 0.0)
                        row_dict["10日涨跌%"] = info.get('pct_10', 0.0)
                        row_dict["20日涨跌%"] = info.get('pct_20', 0.0)
                        if info.get('currency'):
                            row_dict["货币"] = info['currency']
                        
                        if code not in GLOBAL_ASIAN_RT_CACHE:
                            GLOBAL_ASIAN_RT_CACHE[code] = {}
                        GLOBAL_ASIAN_RT_CACHE[code].update(info)
            except Exception as e:
                log.error(f"[AsianTab] 恢复 RT 盘口缓存失败: {e}")

        self._sync_worker_codes()
        self.update_table_ui()

    def _sync_worker_codes(self):
        """让后台 worker 的轮询列表始终跟随当前表格数据，避免长期停留在旧数量。"""
        if hasattr(self, 'worker') and self.worker is not None:
            try:
                self.worker.codes = [
                    str(r.get("代码", "")).strip()
                    for r in (self.row_data or [])
                    if str(r.get("代码", "")).strip()
                ]
            except Exception as e:
                log.warning(f"[AsianTab] 同步 worker 股票池失败: {e}")

    def update_table_ui(self):
        self.model.update_data(self.row_data)
        if hasattr(self, "table_state"):
            if self.row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_empty("暂无亚洲数据")

    def _on_rt_update(self, updates: dict):
        """Worker 传回最新报价时无损更新界面，保持排序和滚动条位置"""
        for row_idx, row_dict in enumerate(self.model.row_data):
            code = row_dict.get("代码")
            if code in updates:
                info = updates[code]
                mkt = code.split('.')[-1] if '.' in code else ''
                row_dict["状态"] = get_market_status(mkt)
                
                cv = float(info['close']) if info['close'] else 0.0
                row_dict["现价"] = f"{cv:.3f}" if 0 < cv < 10 else (f"{cv:.2f}" if cv > 0 else "--")
                
                row_dict["涨幅%"] = info['pct']
                row_dict["5日涨跌%"] = info.get('pct_5', 0.0)
                row_dict["10日涨跌%"] = info.get('pct_10', 0.0)
                row_dict["20日涨跌%"] = info.get('pct_20', 0.0)
                row_dict["货币"] = info['currency']
                
                # trigger row update
                self.model.dataChanged.emit(
                    self.model.index(row_idx, 0),
                    self.model.index(row_idx, len(self.model._headers)-1)
                )
        
        self._save_rt_cache()

    def _save_rt_cache(self):
        try:
            cache_friendly = {}
            for k, v in GLOBAL_ASIAN_RT_CACHE.items():
                cache_friendly[k] = {
                    "date": v.get("date", ""),
                    "close": v.get("close", 0.0),
                    "pct": v.get("pct", 0.0),
                    "pct_5": v.get("pct_5", 0.0),
                    "pct_10": v.get("pct_10", 0.0),
                    "pct_20": v.get("pct_20", 0.0),
                    "currency": v.get("currency", "")
                }
            with open(RT_JSON_CACHE, 'w', encoding='utf-8') as f:
                json.dump(cache_friendly, f, ensure_ascii=False)
        except Exception as e:
            log.error(f"[AsianTab] 持久化 RT 缓存失败: {e}")

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return
        
        code = self.model.row_data[row].get("代码", "")
        # 按当前表格视觉排序顺序构建列表，让 K 线窗口的"上一只/下一只"跟随用户排序
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
                
        # 触发全局画图事件
        event_bus.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def closeEvent(self, event):
        if hasattr(self, 'worker'):
            self.worker.stop()
            self.worker.wait(3000)  # 3 秒超时，防止卡死
        super().closeEvent(event)
