# -*- coding: utf-8 -*-
# ui/tabs/watchlist_tab.py
# 关注池独立组件 — 从 WatchlistMixin 解耦重构为完全自治的 QWidget
import os
import json
import time
import pickle
import datetime
import threading

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLineEdit, QAbstractItemView, QMenu,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW,
    COLOR_SUCCESS, COLOR_ERROR, apply_rise_fall_color, apply_score_color
)

from vcp.constants import SPECIAL_LATEST_DATA, SPECIAL_POOL_DATA_CACHE
from ui.components import NumericTableWidgetItem
from core.event_bus import event_bus
from core.logger import get_logger
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
        self._sp_stretch_cols = set()

        self._init_ui()

        # 挂载全局事件总线
        event_bus.sig_data_updated.connect(self._on_data_updated)
        event_bus.sig_watchlist_changed.connect(self._on_watchlist_changed)
        event_bus.sig_app_closing.connect(self._on_app_closing)
        self._cache_backfill_done = False

        # 延迟加载数据
        QTimer.singleShot(500, self._load_special_data)

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
        self.table_sp = QTableWidget()
        self.table_sp.setColumnCount(12)
        headers = [
            "代码", "时间", "名称", "现价", "涨幅%", "市值",
            "RPS强度", "突破状态",
            "AI结论", "热点板块", "前低", "区间振幅"
        ]
        self.table_sp.setHorizontalHeaderLabels(headers)

        # 表格交互设置
        self.table_sp.verticalHeader().setVisible(False)
        self.table_sp.setAlternatingRowColors(True)
        self.table_sp.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_sp.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_sp.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_sp.setShowGrid(False)
        self.table_sp.setStyleSheet(
            self.table_sp.styleSheet() + "::item { padding: 0px 10px; }"
        )

        # 自适应列宽
        sp_weights = [0.75, 0.65, 1.4, 0.75, 0.7, 0.9, 0.8, 1.2, 2.6, 1.4, 0.55, 0.7]
        header = self.table_sp.horizontalHeader()
        header.setStretchLastSection(True)
        for col_idx, w in enumerate(sp_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_sp.setColumnWidth(col_idx, int(w * 80))
        self.table_sp.verticalHeader().setDefaultSectionSize(32)
        self.table_sp.setSortingEnabled(True)
        self.table_sp.horizontalHeader().setSortIndicatorShown(True)

        # 双击 → 查看K线图（通过 EventBus 广播）
        self.table_sp.itemDoubleClicked.connect(self._on_double_click)

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

        # 1. 新UI 数据
        data_dict = {}
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    data_dict = json.load(f)
            except Exception as e:
                log.error(f"[关注池] 加载异常: {e}")

        # 2. 老UI pkl 兼容迁移
        old_pool = {}
        if not data_dict:
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

        # 1) 先用历史缓存回填现价/涨幅（非交易时段也能显示）
        if all_codes:
            self._backfill_from_cache(all_codes)

        # 2) 再尝试拉取实时报价覆盖（交易时段会用最新价）
        if all_codes:
            threading.Thread(
                target=self._refresh_special_quotes,
                args=(list(all_codes),),
                daemon=True
            ).start()

    def _backfill_from_cache(self, codes):
        """从 cache_data 历史数据回填现价/涨幅，确保非交易时段也有数据"""
        for r in range(self.table_sp.rowCount()):
            code_item = self.table_sp.item(r, 0)
            if not code_item:
                continue
            code = code_item.text()
            df = self.data_provider.get_data(code)
            if df is None or len(df) < 2:
                continue

            try:
                last_close = float(df.iloc[-1]['close'])
                prev_close = float(df.iloc[-2]['close'])
                if last_close <= 0 or prev_close <= 0:
                    continue
                pct = ((last_close / prev_close) - 1) * 100

                # 列4=涨幅%
                for col_idx, text in [(3, f"{last_close:.2f}"), (4, f"{pct:+.2f}%")]:
                    existing = self.table_sp.item(r, col_idx)
                    if existing and existing.text() != '--':
                        continue  # 已有有效数据，不覆盖
                    if existing:
                        existing.setText(text)
                    else:
                        if col_idx in (3, 4):
                            item = NumericTableWidgetItem(text)
                        else:
                            item = QTableWidgetItem(text)
                        item.setForeground(QColor(COLOR_FLAT))
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                        self.table_sp.setItem(r, col_idx, item)

                    # 涨幅着色
                    if col_idx == 4:
                        cell = self.table_sp.item(r, col_idx)
                        if cell:
                            if pct > 0:
                                cell.setForeground(QColor(COLOR_RISE_STRONG if pct > 5 else COLOR_RISE))
                            elif pct < 0:
                                cell.setForeground(QColor(COLOR_FALL_STRONG if pct < -5 else COLOR_FALL))
                            else:
                                cell.setForeground(QColor(COLOR_FLAT))
            except Exception:
                continue

    def _render_table(self, all_codes, data_dict, old_pool, ai_cache):
        """渲染关注池表格"""
        self.table_sp.setSortingEnabled(False)
        self.table_sp.setRowCount(len(all_codes))

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
            status = info_new.get("突破状态", '--')
            ts = info_new.get('时间', time.strftime("%H:%M:%S"))
            cap_display = cap if cap and cap != '--' else ''
            amp = info_new.get("区间振幅", '--')
            low_ref = info_new.get("前低", '--')

            row_data = [
                code, ts, name, str(cur_price), str(pct_str), str(cap_display),
                str(rps), str(status),
                self._merge_and_wrap_ai_diag(ai_text), str(sector),
                str(low_ref), str(amp)
            ]

            for col_idx, text in enumerate(row_data):
                # 数值列: 3=现价 4=涨幅% 5=市值 6=RPS 10=前低 11=振幅
                if col_idx in (3, 4, 5, 6, 10, 11):
                    item = NumericTableWidgetItem(str(text))
                else:
                    item = QTableWidgetItem(str(text))
                item.setForeground(QColor(COLOR_FLAT))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )

                # AI结论列（序号8）：Tooltip 显示全文
                if col_idx == 8 and ai_text:
                    tip_html = (
                        f'<div style="max-width:450px; white-space:pre-wrap;">'
                        f'{ai_text}</div>'
                    )
                    item.setToolTip(tip_html)
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                if col_idx in (7, 9):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )

                # 涨幅% 着色（新列序号4）
                if col_idx == 4:
                    try:
                        pct = float(str(pct_str).replace('%', '').replace('+', ''))
                        if pct > 0:
                            item.setForeground(
                                QColor(COLOR_RISE_STRONG) if pct > 5 else QColor(COLOR_RISE)
                            )
                        elif pct < 0:
                            item.setForeground(
                                QColor(COLOR_FALL_STRONG) if pct < -5 else QColor(COLOR_FALL)
                            )
                    except Exception:
                        pass
                # 突破状态着色（列序号7）
                elif col_idx == 7:
                    status_text = str(text)
                    if "突破" in status_text:
                        item.setText(f"🚀 {status_text}")
                        item.setForeground(QColor(COLOR_RISE_STRONG))
                        f = item.font()
                        f.setBold(True)
                        item.setFont(f)
                    elif "临近" in status_text:
                        item.setText(f"⚠️ {status_text}")
                        item.setForeground(QColor(COLOR_WARNING))
                        f = item.font()
                        f.setBold(True)
                        item.setFont(f)
                    elif "蓄力" in status_text:
                        item.setText(f"⏳ {status_text}")
                        item.setForeground(QColor(STATUS_APPROACHING))
                    elif "潜伏" in status_text:
                        item.setForeground(QColor(STATUS_VCP))

                self.table_sp.setItem(row_idx, col_idx, item)

            # 突破整行暗红高亮
            if "突破" in str(status):
                for c_h in range(12):
                    cell = self.table_sp.item(row_idx, c_h)
                    if cell:
                        cell.setBackground(QColor(232, 93, 93, 20))

        self.table_sp.setSortingEnabled(True)
        self.table_sp.sortByColumn(4, Qt.SortOrder.DescendingOrder)
        log.info(
            f"[关注池] 加载完成: {len(all_codes)} 只标的"
            f"(新UI={len(data_dict)}, 老UI缓存={len(old_pool)})"
        )

    # ================================================================
    # 实时报价刷新
    # ================================================================
    def _refresh_special_quotes(self, codes):
        """后台线程：拉取实时报价并更新表格"""
        try:
            if not self.data_provider.is_online():
                try:
                    ok = self.data_provider.test_network(timeout=5)
                    if ok:
                        self.data_provider.set_online_mode(True)
                        log.info("[关注池] 已自动切换到联网模式")
                    else:
                        log.info("[关注池] 网络不可用，跳过实时报价")
                        return
                except Exception as e:
                    log.error(f"[关注池] 联网失败: {e}")
                    return

            quotes = self.data_provider.fetch_realtime_quotes_batch(codes)
            if not quotes:
                log.info("[关注池] 批量报价返回为空")
                return

            # 使用 QTimer.singleShot(0) 安全地切回主线程更新 UI
            QTimer.singleShot(0, lambda q=quotes: self._apply_quotes_ui(q))
        except Exception as e:
            log.error(f"[关注池] 实时报价刷新异常: {e}")
            import traceback
            traceback.print_exc()

    def _apply_quotes_ui(self, quotes):
        """主线程：将实时报价数据写入表格，并触发 VCP 指标刷新"""
        self.table_sp.setSortingEnabled(False)

        # 收集需要计算 VCP 指标的代码
        codes_to_eval = []

        for r in range(self.table_sp.rowCount()):
            code_item = self.table_sp.item(r, 0)
            if not code_item:
                continue
            code = code_item.text()
            q = quotes.get(code)
            if not q:
                continue

            rt_close = float(q.get('close', 0) or 0)
            last_close = float(q.get('last_close', 0) or 0)

            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                hist_df = self.data_provider.get_data(code)
                if hist_df is not None and len(hist_df) > 1:
                    prev_close = float(hist_df.iloc[-1]['close'])
                    pct = ((rt_close / prev_close) - 1) * 100 if prev_close > 0 else 0
                else:
                    pct = 0

            # 列序号: 1=时间 3=现价 4=涨幅%
            update_map = {
                1: datetime.datetime.now().strftime('%H:%M:%S'),
                3: f"{rt_close:.2f}" if rt_close > 0 else '--',
                4: f"{pct:+.2f}%" if rt_close > 0 else '--',
            }
            for col_idx, text in update_map.items():
                existing = self.table_sp.item(r, col_idx)
                if existing:
                    existing.setText(text)
                else:
                    if col_idx in (3, 4):
                        item = NumericTableWidgetItem(text)
                    else:
                        item = QTableWidgetItem(text)
                    item.setForeground(QColor(COLOR_FLAT))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    )
                    self.table_sp.setItem(r, col_idx, item)

                # 涨幅% 着色（列4）
                if col_idx == 4:
                    cell = self.table_sp.item(r, col_idx)
                    if cell:
                        try:
                            pct_val = float(text.replace('%', '').replace('+', ''))
                            if pct_val > 0:
                                cell.setForeground(
                                    QColor(COLOR_RISE_STRONG) if pct_val > 5 else QColor(COLOR_RISE)
                                )
                            elif pct_val < 0:
                                cell.setForeground(
                                    QColor(COLOR_FALL_STRONG) if pct_val < -5 else QColor(COLOR_FALL)
                                )
                            else:
                                cell.setForeground(QColor(COLOR_FLAT))
                        except Exception:
                            pass

            codes_to_eval.append((r, code))

        self.table_sp.setSortingEnabled(True)
        event_bus.sig_system_log.emit("info", "[关注池] 实时报价已刷新")
        self._save_special_cache_from_table()

        # 后台计算 VCP 评分/RPS/突破状态
        if codes_to_eval:
            threading.Thread(
                target=self._refresh_vcp_indicators,
                args=(codes_to_eval,),
                daemon=True
            ).start()

    def _refresh_vcp_indicators(self, codes_with_rows):
        """后台线程：计算关注池标的的 VCP 评分、RPS、突破状态、市值"""
        try:
            from vcp.engine import VCPEngine
            from vcp.models import VCPParams
            import pickle

            log.info(f"[关注池-VCP] 开始计算 {len(codes_with_rows)} 只标的...")

            # 从主窗口引擎获取预计算 RPS
            rps_bundle = None
            try:
                main_win = self.window()  # 返回顶级窗口 MainWindowQT
                if main_win and hasattr(main_win, 'engine'):
                    rps_bundle = main_win.engine.get_precomputed_rps()
                    if rps_bundle:
                        log.info(f"[关注池-VCP] ✓ 从主窗口引擎获取到 RPS")
            except Exception as e:
                log.error(f"[关注池-VCP] 主窗口引擎获取失败: {e}")

            # 回退：从磁盘加载 RPS 缓存
            if not rps_bundle:
                cache_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__))
                    ))),
                    'data', 'Cache'
                )
                rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                log.info(f"[关注池-VCP] 尝试从磁盘读 RPS: {rps_path}")
                if os.path.exists(rps_path):
                    try:
                        with open(rps_path, 'rb') as f:
                            rps_bundle = pickle.load(f)
                        log.info(f"[关注池-VCP] ✓ 从磁盘加载 RPS 成功")
                    except Exception as e:
                        log.error(f"[关注池-VCP] 磁盘 RPS 加载失败: {e}")
                else:
                    log.info(f"[关注池-VCP] ✗ RPS 缓存文件不存在（需先执行 F5 预计算）")

            rps120_series = rps_bundle.get('rps120') if rps_bundle else None
            rps250_series = rps_bundle.get('rps250') if rps_bundle else None

            # 回退：无预计算 RPS → 从缓存实时计算
            if rps120_series is None or rps250_series is None:
                try:
                    import pandas as pd
                    log.info("[关注池-VCP] 正在从缓存数据实时计算 RPS...")
                    cache_data = getattr(self.data_provider, 'cache_data', {})
                    if cache_data and len(cache_data) > 100:
                        # 构建收盘价矩阵
                        close_dict = {}
                        for c, cdf in cache_data.items():
                            if cdf is not None and len(cdf) > 250 and 'close' in cdf.columns:
                                close_dict[c] = cdf['close']
                        if close_dict:
                            prices = pd.DataFrame(close_dict)
                            rps120_series = (prices.pct_change(120).iloc[-1]
                                             .rank(pct=True) * 100)
                            rps250_series = (prices.pct_change(250).iloc[-1]
                                             .rank(pct=True) * 100)
                            log.info(f"[关注池-VCP] ✓ 实时 RPS 计算完成: {len(close_dict)} 只参与排名")
                except Exception as e:
                    log.error(f"[关注池-VCP] 实时 RPS 计算失败: {e}")

            # 热点板块查询
            sector_info = {}
            try:
                tdx_vipdoc = getattr(self.data_provider, 'tdx_vipdoc', '')
                tdx_root = os.path.dirname(tdx_vipdoc) if tdx_vipdoc else r'D:\HT'
                from vcp.sector import SectorManager
                sm = SectorManager(tdx_root)
                for _, code in codes_with_rows:
                    sectors = sm.get_sectors(code)
                    if sectors:
                        # 取前2个板块，简化显示
                        short = [s.replace('GN_', '').replace('行业_', '')[:6] for s in sectors[:2]]
                        sector_info[code] = ' | '.join(short)
                log.info(f"[关注池-VCP] ✓ 热点板块查询完成: {len(sector_info)} 只")
            except Exception as e:
                log.error(f"[关注池-VCP] 热点板块查询失败: {e}")

            params = VCPParams()
            params.rps_threshold = 0
            params.amp_threshold = 2.0
            params.ma_bind_threshold = 0.30
            params.high_250_threshold = 0.50
            params.min_amount_20d = 0
            params.min_history_days = 60

            results = {}

            # 批量计算市值（总股本 × 收盘价）
            all_codes = [code for _, code in codes_with_rows]
            close_prices = {}
            for _, code in codes_with_rows:
                df_tmp = self.data_provider.get_data(code)
                if df_tmp is not None and len(df_tmp) > 0:
                    close_prices[code] = float(df_tmp.iloc[-1]['close'])
            cap_results = {}
            try:
                cap_results = VCPEngine.batch_check_market_cap(
                    all_codes, close_prices=close_prices
                )
                log.info(f"[关注池-VCP] ✓ 市值计算完成: {len(cap_results)} 只")
            except Exception as e:
                log.error(f"[关注池-VCP] 市值计算异常（可能需联网）: {e}")

            ok_count = 0
            err_count = 0
            for row_idx, code in codes_with_rows:
                try:
                    df = self.data_provider.get_data(code)
                    if df is None or len(df) < 60:
                        # 数据不足，只回填市值
                        cap = cap_results.get(code)
                        if cap and cap > 0:
                            results[row_idx] = {
                                'rps': '--', 'status': '--',
                                'amp': '--', 'low_ref': '--',
                                'cap': f"{cap / 1e8:.0f}亿",
                                'sector': sector_info.get(code, '--'),
                            }
                        continue

                    df = VCPEngine.calculate_indicators(df, include_chart=False)
                    curr_dt = df.index[-1]

                    rps120_val = float(rps120_series.get(code, 0)) if rps120_series is not None else 0
                    rps250_val = float(rps250_series.get(code, 0)) if rps250_series is not None else 0

                    ok, reason, metadata = VCPEngine.evaluate_conditions(
                        df, curr_dt, rps120_val, rps250_val, None, params, skip_red_check=True
                    )

                    rps_display = '--'
                    if rps250_val > 0:
                        rps_display = f"{rps250_val:.0f}"
                        if rps120_val > 0:
                            rps_display += f"/{rps120_val:.0f}"

                    # 从 metadata 提取可用指标
                    if metadata:
                        amp = metadata.get('区间振幅', '--')
                        low_ref = metadata.get('区间最低点', '--')
                    else:
                        amp = '--'
                        low_ref = '--'

                    if ok and metadata:
                        status = metadata.get('突破状态', '蓄力中')
                    else:
                        status = reason[:8] if reason else '未达标'

                    cap = cap_results.get(code)
                    cap_str = f"{cap / 1e8:.0f}亿" if cap and cap > 0 else '--'

                    results[row_idx] = {
                        'rps': rps_display,
                        'status': str(status),
                        'amp': str(amp) if amp != '--' else '--',
                        'low_ref': f"{float(low_ref):.2f}" if low_ref != '--' and low_ref else '--',
                        'cap': cap_str,
                        'sector': sector_info.get(code, '--'),
                    }
                    ok_count += 1
                except Exception as e:
                    log.error(f"[关注池-VCP] {code} 计算异常: {e}")
                    err_count += 1
                    continue

            log.error(f"[关注池-VCP] 计算结束: 成功={ok_count} 失败={err_count} 结果={len(results)}")
            if results:
                # 通过线程安全的 Qt 信号传递结果到主线程
                event_bus.sig_data_updated.emit("vcp_watchlist_ready", results)
            else:
                log.warning("[关注池-VCP] ⚠ 无有效结果，跳过UI更新")
        except Exception as e:
            log.error(f"[关注池-VCP] 批量计算顶层异常: {e}")
            import traceback
            traceback.print_exc()

    def _apply_vcp_indicators_ui(self, results):
        """主线程：将 VCP 指标写入表格"""
        self.table_sp.setSortingEnabled(False)
        for row_idx, data in results.items():
            # 列序号: 5=市值 6=RPS强度 7=突破状态 9=热点板块 10=前低 11=区间振幅
            col_map = {
                5: data.get('cap', '--'),
                6: data['rps'],
                7: data['status'],
                9: data.get('sector', '--'),
                10: data['low_ref'],
                11: data['amp'],
            }
            for col_idx, text in col_map.items():
                existing = self.table_sp.item(row_idx, col_idx)
                if existing:
                    existing.setText(text)
                else:
                    if col_idx in (6, 10, 11):
                        item = NumericTableWidgetItem(text)
                    else:
                        item = QTableWidgetItem(text)
                    item.setForeground(QColor(COLOR_FLAT))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    )
                    self.table_sp.setItem(row_idx, col_idx, item)

            # 突破状态着色（列7）
            status_text = data['status']
            cell = self.table_sp.item(row_idx, 7)
            if cell:
                if "突破" in status_text:
                    cell.setText(f"🚀 {status_text}")
                    cell.setForeground(QColor(COLOR_RISE_STRONG))
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                elif "临近" in status_text:
                    cell.setText(f"⚠️ {status_text}")
                    cell.setForeground(QColor(COLOR_WARNING))
                elif "蓄力" in status_text:
                    cell.setText(f"⏳ {status_text}")
                    cell.setForeground(QColor(STATUS_APPROACHING))

        self.table_sp.setSortingEnabled(True)
        event_bus.sig_system_log.emit("info", "[关注池] VCP指标已刷新")

    # ================================================================
    # 缓存保存
    # ================================================================
    def _save_special_cache_from_table(self):
        """从表格当前状态序列化保存到磁盘"""
        try:
            if self.table_sp.rowCount() == 0:
                log.info("[关注池] 缓存为空，跳过保存")
                return
            cache = {}
            for r in range(self.table_sp.rowCount()):
                code_item = self.table_sp.item(r, 0)
                if not code_item:
                    continue
                code = code_item.text().strip()
                if not code:
                    continue
                row_data = {}
                col_map = {1: 'time', 3: 'price', 4: 'pct', 5: 'cap', 6: 'rps'}
                for col_idx, key in col_map.items():
                    item = self.table_sp.item(r, col_idx)
                    row_data[key] = item.text() if item else ''
                row_data["AI结论"] = self._ai_diag_results.get(code, '')
                cache[code] = row_data

            if not cache and os.path.exists(SPECIAL_LATEST_DATA):
                return
            with open(SPECIAL_LATEST_DATA, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            log.info(f"[关注池] 缓存已保存: {len(cache)} 只")
        except Exception as e:
            log.error(f"[关注池] 保存缓存异常: {e}")

    # ================================================================
    # 关注池增删操作
    # ================================================================
    def toggle_special(self, code, name, is_fav, vcp_data=None):
        """添加/移除关注池标的（公共 API，供外部右键菜单调用）"""
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data'
        )
        os.makedirs(data_dir, exist_ok=True)

        current_data = {}
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    current_data = json.load(f)
            except Exception as e:
                log.error(f"[关注池] 异常: {e}")

        if is_fav:
            if code in current_data:
                del current_data[code]
            event_bus.sig_system_log.emit("info", f"[{name}] 已移出关注池")
        else:
            entry = {"现价": 0, "涨幅%": 0, "评分": ""}
            if vcp_data:
                for k, v in vcp_data.items():
                    if hasattr(v, 'item'):
                        entry[k] = v.item()
                    elif isinstance(v, (str, int, float, bool, list, dict, type(None))):
                        entry[k] = v
                    else:
                        entry[k] = str(v)
            current_data[code] = entry
            event_bus.sig_system_log.emit("info", f"[{name}] 已加入关注池")

        try:
            with open(SPECIAL_LATEST_DATA, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, ensure_ascii=False, indent=4)
            self._load_special_data()
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"关注池操作异常: {e}")

    def remove_from_special(self, code):
        """从关注池移除指定股票"""
        current_data = {}
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    current_data = json.load(f)
            except Exception as e:
                log.error(f"[关注池] 异常: {e}")
        if code in current_data:
            del current_data[code]
            with open(SPECIAL_LATEST_DATA, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, ensure_ascii=False, indent=4)
            self._load_special_data()
            event_bus.sig_system_log.emit("info", f"[{code}] 已移出关注池")

    # ================================================================
    # EventBus 信号处理
    # ================================================================
    def _on_data_updated(self, channel: str, payload: object):
        """监听盘中监控的实时行情数据，同步更新关注池现价/市值/涨幅"""
        # 缓存加载完成 → 回填历史数据 + 计算VCP指标 + 拉实时报价
        if channel == "cache_loaded" and not self._cache_backfill_done:
            self._cache_backfill_done = True
            codes = []
            for r in range(self.table_sp.rowCount()):
                c = self.table_sp.item(r, 0)
                if c:
                    codes.append(c.text())
            if codes:
                self._backfill_from_cache(codes)
                # 直接用缓存数据计算 VCP 评分/RPS/突破状态
                codes_with_rows = [(r, self.table_sp.item(r, 0).text())
                                   for r in range(self.table_sp.rowCount())
                                   if self.table_sp.item(r, 0)]
                if codes_with_rows:
                    threading.Thread(
                        target=self._refresh_vcp_indicators,
                        args=(codes_with_rows,), daemon=True
                    ).start()
                # 同时尝试拉实时报价覆盖
                threading.Thread(
                    target=self._refresh_special_quotes,
                    args=(codes,), daemon=True
                ).start()
            return
        # VCP 指标计算完成 → 主线程安全更新 UI
        if channel == "vcp_watchlist_ready" and payload:
            log.info(f"[关注池-VCP] 收到信号，正在写入 {len(payload)} 条结果到表格...")
            self._apply_vcp_indicators_ui(payload)
            return
        if channel != "rt_quotes_refreshed":
            return
        results = payload
        if not results:
            return

        # 仅收集关注池中的代码数据
        sp_codes = set()
        for r in range(self.table_sp.rowCount()):
            code_item = self.table_sp.item(r, 0)
            if code_item:
                sp_codes.add(code_item.text())

        if not sp_codes:
            return

        rt_map = {r['代码']: r for r in results if r.get('代码') in sp_codes}
        if not rt_map:
            return

        self.table_sp.setSortingEnabled(False)
        for r in range(self.table_sp.rowCount()):
            code_item = self.table_sp.item(r, 0)
            if not code_item:
                continue
            sig = rt_map.get(code_item.text())
            if not sig:
                continue
            try:
                # 列序号: 3=现价 4=涨幅% 5=市值
                price_str = str(sig.get("现价", ''))
                cap_str = str(sig.get("市值", ''))
                pct_str = str(sig.get("涨幅%", ''))

                if self.table_sp.item(r, 3):
                    self.table_sp.item(r, 3).setText(price_str)
                if self.table_sp.item(r, 4):
                    self.table_sp.item(r, 4).setText(pct_str)
                if self.table_sp.item(r, 5):
                    self.table_sp.item(r, 5).setText(cap_str)
                    # 涨幅着色：红涨绿跌
                    try:
                        pct_val = float(pct_str.replace('%', '').replace('+', ''))
                        cell = self.table_sp.item(r, 4)
                        if pct_val > 0:
                            cell.setForeground(
                                QColor(COLOR_RISE_STRONG) if pct_val > 5 else QColor(COLOR_RISE)
                            )
                        elif pct_val < 0:
                            cell.setForeground(
                                QColor(COLOR_FALL_STRONG) if pct_val < -5 else QColor(COLOR_FALL)
                            )
                        else:
                            cell.setForeground(QColor(COLOR_FLAT))
                    except (ValueError, TypeError):
                        pass
            except Exception:
                pass
        self.table_sp.setSortingEnabled(True)

    def _on_watchlist_changed(self, action: str, code: str):
        """外部请求关注池变更时重新加载"""
        self._load_special_data()

    def _on_app_closing(self):
        """应用关闭前保存缓存"""
        if self.table_sp.rowCount() > 0:
            self._save_special_cache_from_table()

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, item):
        """双击行 → 通过 EventBus 广播 K 线图请求"""
        row = item.row()
        code_item = self.table_sp.item(row, 0)
        if code_item:
            event_bus.sig_show_kline.emit(code_item.text())

    def _show_context_menu(self, pos):
        """右键菜单"""
        item = self.table_sp.itemAt(pos)
        if not item:
            return

        row = item.row()
        code_item = self.table_sp.item(row, 0)
        name_item = self.table_sp.item(row, 2)
        if not code_item or not name_item:
            return

        code = code_item.text()
        name = name_item.text()

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #151820; color: #C9CDD4;
                    border: 1px solid #252A36; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 24px; }
            QMenu::item:selected { background-color: rgba(59, 130, 246, 0.2); color: white; }
            QMenu::separator { height: 1px; background: #252A36; margin: 4px 8px; }
        """)

        act_chart = menu.addAction("📈 查看K线图")
        act_copy = menu.addAction("📋 复制代码")
        menu.addSeparator()
        act_remove = menu.addAction("⭐ 移出关注池")
        menu.addSeparator()
        act_tdx = menu.addAction("🖥️ 跳转通达信")
        menu.addSeparator()
        act_ai = menu.addAction("🤖 AI深度诊断")
        act_local = menu.addAction("🧪 本地技术诊断")

        action = menu.exec(self.table_sp.viewport().mapToGlobal(pos))

        if action == act_chart:
            event_bus.sig_show_kline.emit(code)
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(code)
            event_bus.sig_system_log.emit("info", f"已复制: {code}")
        elif action == act_remove:
            self.remove_from_special(code)
        elif action == act_tdx:
            self._launch_tdx(code)
        elif action == act_ai:
            event_bus.sig_open_ai_diag.emit(code, 'ai')
        elif action == act_local:
            event_bus.sig_open_ai_diag.emit(code, 'local')

    def _run_batch_ai_diag(self):
        """一键AI诊断：委托给 AIDiagPanel"""
        if self.ai_panel and hasattr(self.ai_panel, 'run_special_pool_ai_diag_all'):
            self.ai_panel.run_special_pool_ai_diag_all()

    def _export_to_excel(self):
        """导出关注池表格到 Excel"""
        if self.table_sp.rowCount() == 0:
            QMessageBox.information(self, "提示", "关注池为空，无法导出")
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
            headers = [
                self.table_sp.horizontalHeaderItem(c).text()
                for c in range(self.table_sp.columnCount())
            ]
            rows = []
            for r in range(self.table_sp.rowCount()):
                row = []
                for c in range(self.table_sp.columnCount()):
                    item = self.table_sp.item(r, c)
                    row.append(item.text() if item else "")
                rows.append(row)
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(path, index=False, engine='openpyxl')
            event_bus.sig_system_log.emit(
                "info", f"✅ 已导出 {len(rows)} 条关注池记录至 {path}"
            )
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ================================================================
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称、拼音首字母"""
        try:
            import pypinyin
        except ImportError:
            pypinyin = None
        text = text.strip().lower()
        for r in range(self.table_sp.rowCount()):
            if not text:
                self.table_sp.setRowHidden(r, False)
                continue
            code_item = self.table_sp.item(r, 0)
            name_item = self.table_sp.item(r, 2)
            code_text = code_item.text().lower() if code_item else ""
            name_text = name_item.text().lower() if name_item else ""
            py_initials = ""
            if pypinyin:
                try:
                    py_initials = "".join(
                        pypinyin.lazy_pinyin(name_text, style=pypinyin.Style.FIRST_LETTER)
                    ).lower()
                except Exception:
                    pass
            match = text in code_text or text in name_text or text in py_initials
            self.table_sp.setRowHidden(r, not match)

    @staticmethod
    def _merge_and_wrap_ai_diag(text):
        """将AI诊断文本截断为表格可显示的摘要"""
        if not text or text == '--':
            return ""
        text = str(text).replace('\n', ' ')
        if len(text) > 25:
            return text[:25] + "..."
        return text

    # _launch_tdx 已迁移至 BaseStockTab 基类

