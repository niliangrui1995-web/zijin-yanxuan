# -*- coding: utf-8 -*-
# ui/tabs/watchlist_tab.py
# 关注池独立组件 — 从 WatchlistMixin 解耦重构为完全自治的 QWidget
import os
import json
import time
import pickle
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView,
    QHeaderView, QPushButton, QLineEdit, QAbstractItemView, QMenu,
    QFileDialog
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW
)

from ui.viewmodels.watchlist_vm import watchlist_vm
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import SvgIconBuilder
from core.event_bus import event_bus
from core.event_types import DataEvent
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


class WatchlistTab(BaseStockTab):
    """
    关注池 独立 Tab 组件 (Controller + View)
    全权负责关注池的增删查改、实时报价、AI诊断结果展示。
    通过 EventBus 与外部通信，不直接依赖 MainWindowQT。
    """

    def __init__(self, data_provider, ai_panel=None, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.ai_panel = ai_panel  # AIDiagPanel 引用（用于一键诊断）
        self._ai_diag_results = {}
        self.setStyleSheet("background-color: transparent;")

        self._init_ui()

        # 挂载全局事件总线
        event_bus.sig_data_updated.connect(self._on_data_updated)
        event_bus.sig_watchlist_changed.connect(self._on_watchlist_changed)
        event_bus.sig_app_closing.connect(self._on_app_closing)
        self._cache_backfill_done = False

        # 延迟加载数据
        QTimer.singleShot(3500, self._load_special_data)

        # 盘中每30秒独立实时拉取股价，与全局监控分离
        self._rt_fetch_timer = QTimer(self)
        self._rt_fetch_timer.timeout.connect(self._on_rt_fetch_timer)
        self._rt_fetch_timer.start(30 * 1000)

    def _on_rt_fetch_timer(self):
        from vcp.constants import MARKET_OPEN_AM, MARKET_CLOSE_PM
        import datetime
        now = datetime.datetime.now()
        h, m = now.hour, now.minute
        in_market = (
            (h > MARKET_OPEN_AM[0] or (h == MARKET_OPEN_AM[0] and m >= MARKET_OPEN_AM[1]))
            and (h < MARKET_CLOSE_PM[0] or (h == MARKET_CLOSE_PM[0] and m <= 5))
        )
        if in_market and now.weekday() < 5:
            sp_codes = [str(r.get("代码")) for r in self.model.row_data if r.get("代码")]
            if sp_codes:
                task_manager.run_in_background(
                    self._refresh_special_quotes, sp_codes,
                    on_success=lambda q: self._update_quotes_ui(q) if q else None,
                    task_id="watchlist_quotes_indie"
                )

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)

        self.btn_special_diag = QPushButton("🤖 一键AI诊断全部")
        self.btn_special_diag.setObjectName("outlineButton")
        self.btn_special_diag.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_special_diag.setFixedWidth(160)
        self.btn_special_diag.clicked.connect(self._run_batch_ai_diag)
        tb_layout.addWidget(self.btn_special_diag)
        tb_layout.addStretch()

        # 搜索过滤
        self.sp_search = QLineEdit()
        self.sp_search.setPlaceholderText("🔍 搜索关注池...")
        self.sp_search.setFixedWidth(150)
        self.sp_search.setFixedHeight(32)
        self.sp_search.textChanged.connect(
            lambda t: self._filter_table(t)
        )
        tb_layout.addWidget(self.sp_search)

        btn_export_sp = QPushButton("📄 导出")
        btn_export_sp.setProperty("class", "secondary")
        btn_export_sp.setFixedHeight(32)
        btn_export_sp.clicked.connect(self._export_to_excel)
        tb_layout.addWidget(btn_export_sp)
        layout.addWidget(toolbar)

        # 表格控件
        self.table_sp = QTableView()
        
        # 表格交互设置
        self.table_sp.verticalHeader().setVisible(False)
        self.table_sp.setAlternatingRowColors(True)
        self.table_sp.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_sp.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_sp.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_sp.setShowGrid(False)
        self.table_sp.setSortingEnabled(True)
        
        # 绑定 Model 与 Delegate
        headers = [
            "代码", "名称", "现价", "涨幅%", "时间", "市值",
            "RPS强度", "热点板块", "AI结论", "备注"
        ]
        self.model = StockTableModel(headers)
        self.proxy_model = RtSortFilterProxyModel(self.table_sp)
        self.proxy_model.setSourceModel(self.model)
        self.table_sp.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.table_sp)
        self.table_sp.setItemDelegate(self.delegate)

        # 自适应列宽
        sp_weights = [0.75, 0.65, 1.4, 0.75, 0.7, 0.9, 0.8, 1.4, 2.2, 1.5]
        header = self.table_sp.horizontalHeader()
        header.setStretchLastSection(False)
        for col_idx, w in enumerate(sp_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_sp.setColumnWidth(col_idx, int(w * 80))
        self.table_sp.verticalHeader().setDefaultSectionSize(32)
        
        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table_sp, "header_state_watchlist")

        # 双击 → 查看K线图（通过 EventBus 广播）
        self.table_sp.doubleClicked.connect(self._on_double_click)

        # 右键菜单
        self.table_sp.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_sp.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_sp)

    # ================================================================
    # 数据加载
    # ================================================================
    def _load_special_data(self):
        """加载关注池数据：新UI JSON + 老UI pkl 兼容 + AI诊断缓存"""
        data_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

        # 1. 统一由 ViewModel 管理的数据
        data_dict = watchlist_vm.get_watchlist_data()

        # 2. 老UI pkl 兼容迁移
        old_pool = {}
        if not data_dict:
            from vcp.constants import SPECIAL_POOL_DATA_CACHE
            pool_pkl = SPECIAL_POOL_DATA_CACHE
            if os.path.exists(pool_pkl):
                try:
                    with open(pool_pkl, 'rb') as f:
                        raw = pickle.load(f)
                    old_pool = raw.get('data', {}) if isinstance(raw, dict) else {}
                except Exception as e:
                    log.error(f"[关注池] 老UI缓存读取异常: {e}")

        # 3. AI 诊断缓存
        ai_cache_path = os.path.join(data_dir, 'data', 'Cache', 'ai_diag_special.json')
        ai_cache = {}
        if os.path.exists(ai_cache_path):
            try:
                with open(ai_cache_path, 'r', encoding='utf-8') as f:
                    ai_raw = json.load(f)
                ai_cache = ai_raw.get('results', {}) if isinstance(ai_raw, dict) else {}
            except Exception as e:
                log.error(f"[关注池] AI缓存读取异常: {e}")

        # 合并代码列表
        all_codes = list(data_dict.keys())
        for code in old_pool:
            if code not in data_dict:
                all_codes.append(code)

        # 渲染表格
        self._render_table(all_codes, data_dict, old_pool, ai_cache)

        # 抛弃本地缓存回填，直接拉取实时报价覆盖（非交易时段 PyTdx 自动返回收盘快照）
        if all_codes:
            def _on_quotes_ready(q):
                if q: self._update_quotes_ui(q)
            task_manager.run_in_background(
                self._refresh_special_quotes, list(all_codes),
                on_success=_on_quotes_ready,
                task_id="watchlist_quotes"
            )

    def _render_table(self, all_codes, data_dict, old_pool, ai_cache):
        """渲染关注池表格"""
        final_list = []
        for row_idx, code in enumerate(all_codes):
            info_new = data_dict.get(code, {})
            info_old = old_pool.get(code, {})
            name = getattr(self.data_provider, 'code2name', {}).get(code, code)

            # AI诊断文本提取（多源优先级合并）
            def _extract_ai_text(val):
                if isinstance(val, dict):
                    return val.get('text', '') or val.get('content', '') or ''
                return str(val) if val else ''

            ai_text = (
                _extract_ai_text(self._ai_diag_results.get(code, ''))
                or _extract_ai_text(ai_cache.get(code, ''))
                or _extract_ai_text(info_old.get("AI结论", ''))
                or _extract_ai_text(info_new.get("AI结论", ''))
            )
            if ai_text:
                self._ai_diag_results[code] = ai_text

            cur_price = info_new.get("现价", '--')
            pct_str = info_new.get("涨幅%", '--')
            rps = info_new.get("RPS强度", '--')
            cap = info_new.get("市值", '--')
            sector = info_new.get("热点板块", '--')
            ts = info_new.get('时间', time.strftime("%H:%M:%S"))
            cap_display = cap if cap and cap != '--' else ''

            row_data = {
                "代码": code,
                "名称": name,
                "现价": str(cur_price),
                "涨幅%": str(pct_str),
                "时间": ts,
                "市值": str(cap_display),
                "RPS强度": str(rps),
                "热点板块": str(sector),
                "AI结论": self._merge_and_wrap_ai_diag(ai_text),
                "备注": info_new.get("备注", ""),
            }
            final_list.append(row_data)

        self.model.update_data(final_list)

    def _refresh_special_quotes(self, codes):
        """后台拉取实时行情，具备重试和离线恢复机制"""
        import time as _time
        max_retries = 3
        retry_delay = 5

        for attempt in range(1, max_retries + 1):
            try:
                if not self.data_provider:
                    return None

                if not self.data_provider.is_online():
                    try:
                        if self.data_provider.test_network(timeout=3):
                            self.data_provider.set_online_mode(True)
                        else:
                            if attempt < max_retries:
                                log.info(f"[关注池] 独立刷新第{attempt}次: 服务器未就绪，{retry_delay}秒后重试...")
                                _time.sleep(retry_delay)
                                continue
                            return None
                    except Exception:
                        if attempt < max_retries:
                            _time.sleep(retry_delay)
                            continue
                        return None

                if not getattr(self.data_provider, 'server_pool', None):
                    if attempt < max_retries:
                        _time.sleep(retry_delay)
                        continue
                    return None

                raw_quotes = self.data_provider.fetch_realtime_quotes_batch(codes)
                if not raw_quotes:
                    if attempt < max_retries:
                        _time.sleep(retry_delay)
                        continue
                    return None

                quotes = {}
                for c, q in raw_quotes.items():
                    quotes[c] = {'price': float(q.get('close', 0) or 0), 'pre_close': float(q.get('last_close', 0) or 0)}
                return quotes
            except Exception as e:
                log.error(f"[关注池] 实时行情刷新异常(第{attempt}次): {e}")
                if attempt < max_retries:
                    _time.sleep(retry_delay)
        return None
            
    def _update_quotes_ui(self, quotes):
        """将最新的行情批量应用到 Model"""
        final_list = list(self.model.row_data)
        dirty = False
        
        for row_data in final_list:
            code = str(row_data.get('代码', ''))
            if code in quotes:
                q = quotes[code]
                c_price = q.get('price', 0)
                p_close = q.get('pre_close', 0)

                # --- 零值纠正 ---                        
                if c_price <= 0 and p_close > 0:
                    c_price = p_close

                if p_close > 0:
                    pct = ((c_price - p_close) / p_close) * 100
                    row_data["现价"] = f"{c_price:.2f}"
                    row_data["涨幅%"] = f"{pct:+.2f}%"
                    dirty = True

        if dirty:
            self.model.update_data(final_list)

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, index):
        """双击行 → 备注列弹编辑框(#11)，其他列跳 K 线图"""
        if not index.isValid():
            return
        proxy_row = index.row()
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        col = source_index.column()
        if row >= len(self.model.row_data):
            return

        # #11: 如果双击的是备注列(第9列)，弹出编辑框
        notes_col_idx = self.model.headers.index("备注") if "备注" in self.model.headers else -1
        if col == notes_col_idx:
            code = self.model.row_data[row].get("代码", "")
            old_note = self.model.row_data[row].get("备注", "")
            from PyQt6.QtWidgets import QInputDialog
            new_note, ok = QInputDialog.getText(
                self, "编辑备注",
                f"请输入 {code} 的备注:",
                text=old_note
            )
            if ok:
                # 更新内存
                self.model.row_data[row]["备注"] = new_note
                # 触发表格刷新
                idx = self.model.index(row, notes_col_idx)
                self.model.dataChanged.emit(idx, idx)
                # 持久化到 WatchlistVM JSON
                vm_data = watchlist_vm.get_watchlist_data()
                if code in vm_data:
                    vm_data[code]["备注"] = new_note
                    watchlist_vm._cache[code] = vm_data[code]
                    watchlist_vm._save_data()
            return

        # 非备注列 → K 线图
        code = self.model.row_data[row].get("代码")
        if code:
            code_list = [{'代码': r.get("代码", ""), '名称': r.get("名称", "")} for r in self.model.row_data]
            event_bus.sig_show_kline_with_list.emit(code, code_list, proxy_row)

    def _show_context_menu(self, pos):
        """关注池右键菜单 — 委托给统一菜单工厂 (#2)"""
        index = self.table_sp.indexAt(pos)
        if not index.isValid():
            return

        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return
            
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if not code or not name:
            return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name)

    def _run_batch_ai_diag(self):
        """一键AI诊断：委托给 AIDiagPanel"""
        if self.ai_panel and hasattr(self.ai_panel, 'run_special_pool_ai_diag_all'):
            self.ai_panel.run_special_pool_ai_diag_all()

    def _export_to_excel(self):
        """导出关注池表格到 Excel"""
        if self.model.rowCount() == 0:
            show_toast("关注池为空，无法导出", "warning", self)
            return
        import pandas as pd
        path, _ = QFileDialog.getSaveFileName(
            self, "导出关注池",
            f"关注池_{datetime.date.today().strftime('%Y%m%d')}.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            headers = self.model.headers
            rows = []
            for row_dict in self.model.row_data:
                row = []
                for header in headers:
                    row.append(str(row_dict.get(header, "")))
                rows.append(row)
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(path, index=False, engine='openpyxl')
            event_bus.sig_system_log.emit(
                "info", f"✅ 已导出 {len(rows)} 条关注池记录至 {path}"
            )
            show_toast("自选股导出成功!", "success", self)
        except Exception as e:
            show_toast(f"导出失败: {str(e)}", "error", self)

    # ================================================================
    # EventBus 事件监听及同步更新
    # ================================================================
    def _on_watchlist_changed(self, action: str, code: str):
        """外部请求关注池变更时重新加载"""
        self._load_special_data()
        
        # 自动触发重新计算所有个股的 RPS 和 版块信息
        if self.model and self.model.row_data:
            codes_with_rows = [(idx, str(r.get("代码"))) for idx, r in enumerate(self.model.row_data) if r.get("代码")]
            if codes_with_rows:
                task_manager.run_in_background(
                    self._refresh_vcp_indicators, codes_with_rows,
                    task_id="watchlist_vcp_refresh"
                )

    def _refresh_vcp_indicators(self, codes_with_rows):
        """后台线程：计算关注池标的的 VCP 评分、RPS、突破状态、市值"""
        try:
            from vcp.engine import VCPEngine
            from vcp.models import VCPParams
            import pickle, os

            log.info(f"[关注池-VCP] 开始计算 {len(codes_with_rows)} 只标的...")

            # 1. 尝试从引擎获取RPS
            rps_bundle = None
            try:
                main_win = self.window()
                if main_win and hasattr(main_win, 'engine'):
                    rps_bundle = main_win.engine.get_precomputed_rps()
            except Exception as e:
                log.error(f"[关注池-VCP] 获取RPS异常: {e}")

            if not rps_bundle:
                cache_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'data', 'Cache'
                )
                rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                if os.path.exists(rps_path):
                    with open(rps_path, 'rb') as f:
                        rps_bundle = pickle.load(f)

            rps120_series = rps_bundle.get('rps120') if rps_bundle else None
            rps250_series = rps_bundle.get('rps250') if rps_bundle else None

            # 2. 从本地缓存获取数据并处理
            sector_info = {}
            try:
                tdx_vipdoc = getattr(self.data_provider, 'tdx_vipdoc', '')
                tdx_root = os.path.dirname(tdx_vipdoc) if tdx_vipdoc else r'D:\HT'
                from vcp.sector import SectorManager
                sm = SectorManager.get_instance(tdx_root)
                for _, code in codes_with_rows:
                    sectors = sm.get_sectors(code)
                    if sectors:
                        short = [s.replace('GN_', '').replace('行业_', '')[:6] for s in sectors[:2]]
                        sector_info[code] = ' | '.join(short)
            except Exception as e:
                pass

            params = VCPParams()
            params.rps_threshold = 0
            params.amp_threshold = 2.0
            params.ma_bind_threshold = 0.30
            params.high_250_threshold = 0.50
            params.min_amount_20d = 0
            params.min_history_days = 60

            results = {}

            # 计算市值
            all_codes = [code for _, code in codes_with_rows]
            close_prices = {}
            for _, code in codes_with_rows:
                df_tmp = self.data_provider.get_data(code)
                if df_tmp is not None and len(df_tmp) > 0:
                    close_prices[code] = float(df_tmp.iloc[-1]['close'])
            cap_results = {}
            try:
                cap_results = VCPEngine.batch_check_market_cap(all_codes, close_prices=close_prices)
            except Exception as e:
                log.error(f"[关注池-VCP] 市值计算失败: {e}")

            ok_count = err_count = 0
            for row_idx, code in codes_with_rows:
                try:
                    rps120_val = float(rps120_series.get(code, 0)) if rps120_series is not None and code in rps120_series else 0
                    rps250_val = float(rps250_series.get(code, 0)) if rps250_series is not None and code in rps250_series else 0
                    
                    rps_display = '--'
                    if rps250_val > 0:
                        rps_display = f"{rps250_val:.0f}"
                        if rps120_val > 0:
                            rps_display += f"/{rps120_val:.0f}"
                            
                    cap = cap_results.get(code)
                    cap_str = f"{cap / 1e8:.0f}亿" if cap and cap > 0 else '--'

                    results[row_idx] = {
                        'rps': rps_display,
                        'cap': cap_str,
                        'sector': sector_info.get(code, '--')
                    }
                    ok_count += 1
                except Exception as e:
                    err_count += 1
                    continue
            
            if results:
                event_bus.sig_data_updated.emit(DataEvent.VCP_WATCHLIST_READY.value, results)

        except Exception as e:
            log.error(f"[关注池-VCP] 批量计算顶层异常: {e}")

    def _apply_vcp_indicators_ui(self, results: dict):
        """主线程：将 VCP 指标更新到 Model"""
        if not results: return
        
        for row_idx, data in results.items():
            if row_idx < 0 or row_idx >= len(self.model.row_data): continue
            
            row_dict = self.model.row_data[row_idx]
            row_dict['市值'] = data.get('cap', '--')
            row_dict['RPS强度'] = data.get('rps', '--')
            row_dict['热点板块'] = data.get('sector', '--')
            
            # trigger row update
            self.model.dataChanged.emit(
                self.model.index(row_idx, 0),
                self.model.index(row_idx, len(self.model._headers)-1)
            )
            
        event_bus.sig_system_log.emit("info", "[关注池] VCP指标已刷新")

    def _on_app_closing(self):
        """应用关闭前保存缓存"""
        if self.model.row_data:
            self._save_special_cache_from_table()

    def _save_special_cache_from_table(self):
        """应用关闭前将表格最新数据更新回 ViewModel"""
        try:
            # ViewModel 自己管理全量状态，我们只需更新每个 code 的诊断结果和指标即可
            current_cache = watchlist_vm.get_watchlist_data()
            dirty = False
            for row in self.model.row_data:
                code = str(row.get("代码", ""))
                if not code or code not in current_cache:
                    continue
                
                # 更新最新的结构化指标到 ViewModel
                entry = current_cache[code]
                entry["RPS强度"] = str(row.get("RPS强度", ""))
                entry["AI结论"] = str(row.get("AI结论", ""))
                entry["热点板块"] = str(row.get("热点板块", ""))
                dirty = True
                
            if dirty:
                watchlist_vm._cache = current_cache
                watchlist_vm._save_data()
        except Exception as e:
            log.error(f"[关注池] 同步缓存到 ViewModel 失败: {e}")

    def _on_data_updated(self, channel: str, payload: object):
        """监听盘中监控的数据完成状态（与本地缓存解耦不读取现价，仅同步VCP指标）"""
        if channel == DataEvent.CACHE_LOADED.value and not getattr(self, "_vcp_computed", False):
            self._vcp_computed = True
            codes = [str(r.get("代码")) for r in self.model.row_data if r.get("代码")]
            if codes:
                codes_with_rows = [(r_idx, str(r.get("代码"))) 
                                   for r_idx, r in enumerate(self.model.row_data) if r.get("代码")]
                if codes_with_rows:
                    task_manager.run_in_background(
                        self._refresh_vcp_indicators, codes_with_rows,
                        task_id="watchlist_vcp_2"
                    )
            return

        if channel == DataEvent.VCP_WATCHLIST_READY.value and payload:
            log.info(f"[关注池-VCP] 收到信号，正在写入 {len(payload)} 条结果到表格...")
            self._apply_vcp_indicators_ui(payload)
            return
        
        # 已与盘中监控解耦，不再响应 rt_quotes_refreshed

    # ================================================================
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称、拼音首字母"""
        self.proxy_model.setFilterText(text)

    @staticmethod
    def _merge_and_wrap_ai_diag(text):
        """保留AI诊断完整文本供Tooltip使用，显示截断和格式由Model与View处理"""
        if not text or text == '--':
            return ""
        return str(text).strip()

    # _launch_tdx 已迁移至 BaseStockTab 基类

