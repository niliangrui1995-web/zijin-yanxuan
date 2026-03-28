# -*- coding: utf-8 -*-
"""
ui/tabs/na_daily_tab.py
北美战报 独立 Tab 组件 — 从 NADailyMixin 解耦重构为完全自治的 QWidget
负责从 L4 战报 markdown 解析标的狙击表数据展示为表格
实时数据（现价/涨幅/市值）由盘中监控的 RtScanWorker 同步刷新
"""
import os
import re
import glob
import datetime
import threading

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QColor
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW,
    COLOR_SUCCESS, COLOR_ERROR, apply_rise_fall_color, apply_score_color
)

from ui.components import NumericTableWidgetItem
from core.event_bus import event_bus
from core.logger import get_logger
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


class NADailyTab(BaseStockTab):
    """北美战报 独立 Tab: 解析最新 L4 战报 → 按行业展示标的狙击表 + 实时数据"""

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._na_daily_codes = set()
        self._cap_cache_na = {}
        self.setStyleSheet("background-color: transparent;")

        self._init_ui()

        # 挂载 EventBus
        event_bus.sig_data_updated.connect(self._on_data_updated)

        # 延迟加载
        QTimer.singleShot(500, self._load_na_daily_report)

        # 定时刷新（交易日 9:20 / 14:40）
        self._na_daily_schedule_timer = QTimer(self)
        self._na_daily_schedule_timer.timeout.connect(self._check_na_daily_schedule)
        self._na_daily_schedule_timer.start(30 * 1000)
        self._na_daily_fired_today = set()

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部：标题 + 刷新按钮 + 状态标签
        header_layout = QHBoxLayout()
        lbl_title = QLabel("🌎 北美战报 — L4 战报标的")
        lbl_title.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #C9CDD4;"
        )
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        self.na_daily_source_label = QLabel("未加载")
        self.na_daily_source_label.setStyleSheet(
            "font-size: 11px; color: #6B7280;"
        )
        header_layout.addWidget(self.na_daily_source_label)

        btn_refresh = QPushButton("🔄 刷新战报")
        btn_refresh.setObjectName("ctaButton")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setFixedWidth(120)
        btn_refresh.clicked.connect(self._load_na_daily_report)
        header_layout.addWidget(btn_refresh)
        layout.addLayout(header_layout)

        # 表格
        columns = [
            "序号", "细分行业", "代码", "时间", "名称", "现价", "涨幅",
            "市值", "弹性", "爆发力", "风控", "今日推荐", "推荐理由"
        ]
        self.na_daily_table = QTableWidget()
        self.na_daily_table.setColumnCount(len(columns))
        self.na_daily_table.setHorizontalHeaderLabels(columns)
        self.na_daily_table.setAlternatingRowColors(True)
        self.na_daily_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.na_daily_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.na_daily_table.setSortingEnabled(True)
        self.na_daily_table.verticalHeader().setVisible(False)
        self.na_daily_table.setShowGrid(False)
        self.na_daily_table.setStyleSheet(
            self.na_daily_table.styleSheet() + "::item { padding: 0px 10px; }"
        )

        # 列宽设置
        header = self.na_daily_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [40, 120, 70, 55, 80, 70, 70, 70, 55, 55, 45, 60, 200]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.na_daily_table.setColumnWidth(i, w)
        header.setSectionResizeMode(12, QHeaderView.ResizeMode.Stretch)
        self.na_daily_table.verticalHeader().setDefaultSectionSize(32)

        # 恢复列宽
        settings = QSettings("VCPHunter", "MainWindowQT")
        saved = settings.value("na_daily_col_widths_v3")
        if saved and isinstance(saved, list) and len(saved) == len(default_widths):
            for i, w in enumerate(saved):
                try:
                    self.na_daily_table.setColumnWidth(i, int(w))
                except (ValueError, TypeError):
                    pass
        header.sectionResized.connect(self._save_na_daily_col_widths)

        # 双击 → K线图
        self.na_daily_table.itemDoubleClicked.connect(self._on_double_click)
        # 右键菜单
        self.na_daily_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.na_daily_table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.na_daily_table, 1)

    # ================================================================
    # 数据加载
    # ================================================================
    def _load_na_daily_report(self):
        """加载今日所有战报文件并合并展示"""
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "每日战报", "每日热点输出"
        )
        pattern = os.path.join(output_dir, "**", "战报_*.md")
        files = sorted(glob.glob(pattern, recursive=True))

        if not files:
            self.na_daily_source_label.setText("❌ 未找到战报文件")
            return

        today_prefix = "战报_" + datetime.datetime.now().strftime("%Y%m%d")
        today_files = [f for f in files if os.path.basename(f).startswith(today_prefix)]
        if not today_files:
            today_files = [files[-1]]

        _SLOT_COLORS = {0: "#C9CDD4", 1: "#32D7E0"}

        all_stocks = []
        seen_codes = set()
        all_recommended = {}
        rec_count = {}

        for file_idx, fpath in enumerate(today_files):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            stocks = self._parse_battle_report(content)
            recommended_map = self._parse_recommendations(content)
            slot_color = _SLOT_COLORS.get(file_idx, "#32D7E0")

            for s in stocks:
                code = s.get("代码", "")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    s["_slot_color"] = slot_color
                    all_stocks.append(s)

            for code, reason in recommended_map.items():
                rec_count[code] = rec_count.get(code, 0) + 1
                all_recommended[code] = reason

        stocks = all_stocks
        recommended_map = all_recommended

        # 更新数据源标签
        fnames = [os.path.basename(f) for f in today_files]
        if len(fnames) == 1:
            self.na_daily_source_label.setText(f"📄 {fnames[0]}")
        else:
            self.na_daily_source_label.setText(
                f"📄 {fnames[0]} +{len(fnames)-1}份增量 ({len(stocks)}只)"
            )

        self._na_daily_codes = {s["代码"] for s in stocks}

        # 填充表格
        self.na_daily_table.setSortingEnabled(False)
        self.na_daily_table.setRowCount(len(stocks))

        for row, stock in enumerate(stocks):
            code = stock.get("代码", "")
            slot_color = stock.get("_slot_color", "#C9CDD4")
            n_rec = rec_count.get(code, 0)
            reason = recommended_map.get(code, "")
            items = [
                str(row + 1), stock.get("行业", ""), code, "--",
                stock.get("名称", ""), "--", "--", "--",
                stock.get("弹性", ""), stock.get("爆发力", ""),
                stock.get("风控", ""),
                "✓" * n_rec if n_rec else "", reason
            ]

            for col, text in enumerate(items):
                if col in (0, 5, 6, 7):
                    item = NumericTableWidgetItem(str(text))
                else:
                    item = QTableWidgetItem(str(text))
                item.setForeground(QColor(COLOR_FLAT))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )

                if col == 0:
                    item.setForeground(QColor(slot_color))
                if col == 9 and text:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setToolTip(
                        f'<div style="max-width:500px;">{text}</div>'
                    )
                elif col == 10:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    if "🟢" in text:
                        item.setForeground(QColor("#22C55E"))
                    elif "🟡" in text:
                        item.setForeground(QColor("#EAB308"))
                    elif "🔴" in text:
                        item.setForeground(QColor("#EF4444"))
                    if text:
                        item.setToolTip(
                            f'<div style="max-width:500px;">{text}</div>'
                        )
                elif col == 11 and "✓" in text:
                    item.setForeground(QColor("#F59E0B"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif col == 12 and text:
                    item.setForeground(QColor("#93C5FD"))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setToolTip(
                        f'<div style="max-width:400px;">{text}</div>'
                    )
                elif col == 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )

                self.na_daily_table.setItem(row, col, item)

        self.na_daily_table.setSortingEnabled(True)

        # 1) 先用历史缓存回填现价/涨幅（非交易时段也能显示）
        if self._na_daily_codes:
            self._backfill_from_cache()

        # 2) 再尝试拉取实时报价覆盖
        if self._na_daily_codes:
            self._fetch_na_quotes_independently()

    def _backfill_from_cache(self):
        """从 cache_data 历史数据回填现价/涨幅/市值，确保非交易时段也有数据"""
        # 先批量计算市值
        codes = []
        close_prices = {}
        for row in range(self.na_daily_table.rowCount()):
            code_item = self.na_daily_table.item(row, 2)
            if code_item:
                code = code_item.text()
                codes.append(code)
                df = self.data_provider.get_data(code)
                if df is not None and len(df) > 0:
                    close_prices[code] = float(df.iloc[-1]['close'])

        cap_results = {}
        if codes:
            try:
                from vcp.engine import VCPEngine
                cap_results = VCPEngine.batch_check_market_cap(codes, close_prices=close_prices)
            except Exception as e:
                print(f"[北美战报] 市值批量计算异常: {e}")

        for row in range(self.na_daily_table.rowCount()):
            code_item = self.na_daily_table.item(row, 2)
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
                pct_str = f"{pct:+.2f}%"

                # 市值
                cap = cap_results.get(code)
                cap_str = f"{cap / 1e8:.0f}亿" if cap and cap > 0 else ''

                # 列序号: 5=现价 6=涨幅 7=市值
                updates = {
                    5: f"{last_close:.2f}",
                    6: pct_str,
                }
                if cap_str:
                    updates[7] = cap_str

                for col_idx, text in updates.items():
                    existing = self.na_daily_table.item(row, col_idx)
                    if existing and existing.text() != '--':
                        continue  # 已有有效数据，不覆盖
                    if existing:
                        existing.setText(text)
                    else:
                        item = NumericTableWidgetItem(text)
                        item.setForeground(QColor(COLOR_FLAT))
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                        self.na_daily_table.setItem(row, col_idx, item)

                    # 涨幅着色
                    if col_idx == 6:
                        cell = self.na_daily_table.item(row, col_idx)
                        if cell:
                            if pct > 0:
                                cell.setForeground(QColor(COLOR_RISE_STRONG if pct > 5 else COLOR_RISE))
                            elif pct < 0:
                                cell.setForeground(QColor(COLOR_FALL_STRONG if pct < -5 else COLOR_FALL))
                            else:
                                cell.setForeground(QColor(COLOR_FLAT))
            except Exception:
                continue

    def _load_na_daily_incremental(self, tag_color="#FF9F0A"):
        """增量刷新：只在表格末尾追加新股票"""
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "每日战报", "每日热点输出"
        )
        pattern = os.path.join(output_dir, "**", "战报_*.md")
        files = sorted(glob.glob(pattern, recursive=True))
        if not files:
            return

        latest_file = files[-1]
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        stocks = self._parse_battle_report(content)
        recommended_map = self._parse_recommendations(content)

        existing_codes = set()
        for row in range(self.na_daily_table.rowCount()):
            code_item = self.na_daily_table.item(row, 2)
            if code_item:
                existing_codes.add(code_item.text())

        new_stocks = [s for s in stocks if s.get("代码", "") not in existing_codes]
        if not new_stocks:
            print("[北美战报] 增量刷新：无新增股票")
            return

        print(f"[北美战报] 增量刷新：新增 {len(new_stocks)} 只股票")
        filename = os.path.basename(latest_file)
        self.na_daily_source_label.setText(
            f"📄 {filename}（+{len(new_stocks)}新增）"
        )

        self.na_daily_table.setSortingEnabled(False)
        start_row = self.na_daily_table.rowCount()
        self.na_daily_table.setRowCount(start_row + len(new_stocks))

        for i, stock in enumerate(new_stocks):
            row = start_row + i
            code = stock.get("代码", "")
            is_recommended = code in recommended_map
            reason = recommended_map.get(code, "")
            items = [
                str(row + 1), stock.get("行业", ""), code, "--",
                stock.get("名称", ""), "--", "--", "--",
                stock.get("弹性", ""), stock.get("爆发力", ""),
                stock.get("风控", ""),
                "✓" if is_recommended else "", reason
            ]

            for col, text in enumerate(items):
                if col in (0, 5, 6, 7):
                    item = NumericTableWidgetItem(str(text))
                else:
                    item = QTableWidgetItem(str(text))
                item.setForeground(QColor(COLOR_FLAT))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )
                if col == 0:
                    item.setForeground(QColor(tag_color))
                if col == 9 and text:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setToolTip(
                        f'<div style="max-width:500px;">{text}</div>'
                    )
                elif col == 10:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    if "🟢" in text:
                        item.setForeground(QColor("#22C55E"))
                    elif "🟡" in text:
                        item.setForeground(QColor("#EAB308"))
                    elif "🔴" in text:
                        item.setForeground(QColor("#EF4444"))
                    if text:
                        item.setToolTip(
                            f'<div style="max-width:500px;">{text}</div>'
                        )
                elif col == 11 and "✓" in text:
                    item.setForeground(QColor("#F59E0B"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif col == 12 and text:
                    item.setForeground(QColor("#93C5FD"))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setToolTip(
                        f'<div style="max-width:400px;">{text}</div>'
                    )
                elif col == 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                self.na_daily_table.setItem(row, col, item)

        for s in new_stocks:
            self._na_daily_codes.add(s.get("代码", ""))
        self.na_daily_table.setSortingEnabled(True)

        if self._na_daily_codes:
            self._fetch_na_quotes_independently()

    # ================================================================
    # 实时报价
    # ================================================================
    def _fetch_na_quotes_independently(self):
        """独立拉取北美战报股票的实时行情（后台线程）"""
        import time as _time

        def _bg_fetch():
            max_retries = 3
            retry_delay = 5

            for attempt in range(1, max_retries + 1):
                try:
                    na_codes = list(self._na_daily_codes)
                    if not na_codes:
                        return

                    if not self.data_provider:
                        return

                    if not self.data_provider.is_online():
                        try:
                            if self.data_provider.test_network(timeout=3):
                                self.data_provider.set_online_mode(True)
                            else:
                                if attempt < max_retries:
                                    print(f"[北美战报] 独立刷新第{attempt}次: "
                                          f"服务器未就绪，{retry_delay}秒后重试...")
                                    _time.sleep(retry_delay)
                                    continue
                                return
                        except Exception:
                            if attempt < max_retries:
                                _time.sleep(retry_delay)
                                continue
                            return

                    if not self.data_provider.server_pool:
                        if attempt < max_retries:
                            _time.sleep(retry_delay)
                            continue
                        return

                    na_quotes = self.data_provider.fetch_realtime_quotes_batch(na_codes)
                    if not na_quotes:
                        if attempt < max_retries:
                            _time.sleep(retry_delay)
                            continue
                        return

                    # 构建市值缓存
                    codes_need_cap = [
                        c for c in na_codes if c not in self._cap_cache_na
                    ]
                    if codes_need_cap:
                        try:
                            from vcp.engine import VCPEngine
                            close_prices = {
                                c: float(na_quotes[c].get('close', 0) or 0)
                                for c in codes_need_cap if c in na_quotes
                            }
                            cap_results = VCPEngine.batch_check_market_cap(
                                codes_need_cap, close_prices=close_prices
                            )
                            for c in codes_need_cap:
                                cap = cap_results.get(c)
                                if cap and cap > 0:
                                    self._cap_cache_na[c] = f"{cap / 1e8:.0f}亿"
                                else:
                                    self._cap_cache_na[c] = '--'
                        except Exception as _e:
                            print(f"[北美战报] 独立刷新-市值补全异常: {_e}")

                    # 安全切回主线程更新 UI
                    QTimer.singleShot(
                        0,
                        lambda q=na_quotes: self._update_na_daily_realtime(q)
                    )
                    print(
                        f"[北美战报] 独立刷新完成: "
                        f"{len(na_quotes)}/{len(na_codes)} 只股票"
                    )
                    return

                except Exception as _e:
                    print(f"[北美战报] 独立刷新异常(第{attempt}次): {_e}")
                    if attempt < max_retries:
                        _time.sleep(retry_delay)

        threading.Thread(target=_bg_fetch, daemon=True).start()

    def _update_na_daily_realtime(self, quotes: dict):
        """用实时报价更新北美战报表格"""
        if not self._na_daily_codes or self.na_daily_table.rowCount() == 0:
            return

        now_str = datetime.datetime.now().strftime("%H:%M")
        self.na_daily_table.setSortingEnabled(False)

        for row in range(self.na_daily_table.rowCount()):
            code_item = self.na_daily_table.item(row, 2)
            if not code_item:
                continue
            code = code_item.text()
            quote = quotes.get(code)
            if not quote:
                continue

            rt_close = float(quote.get('close', 0) or 0)
            last_close = float(quote.get('last_close', 0) or 0)
            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
                pct_str = f"{pct:+.2f}%"
            else:
                pct = 0
                pct_str = "--"

            cap_str = self._cap_cache_na.get(code, "--")

            updates = {
                3: now_str,
                5: f"{rt_close:.2f}" if rt_close > 0 else "--",
                6: pct_str,
                7: cap_str,
            }

            for col_idx, text in updates.items():
                existing = self.na_daily_table.item(row, col_idx)
                if existing:
                    existing.setText(text)
                else:
                    if col_idx in (5, 6, 7):
                        item = NumericTableWidgetItem(text)
                    else:
                        item = QTableWidgetItem(text)
                    item.setForeground(QColor(COLOR_FLAT))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    )
                    self.na_daily_table.setItem(row, col_idx, item)

                if col_idx == 6:
                    cell = self.na_daily_table.item(row, col_idx)
                    if cell:
                        try:
                            if pct > 0:
                                cell.setForeground(QColor(COLOR_RISE_STRONG if pct > 5 else COLOR_RISE))
                            elif pct < 0:
                                cell.setForeground(QColor(COLOR_FALL_STRONG if pct < -5 else COLOR_FALL))
                            else:
                                cell.setForeground(QColor(COLOR_FLAT))
                        except Exception:
                            pass

        self.na_daily_table.setSortingEnabled(True)

    # ================================================================
    # EventBus 事件
    # ================================================================
    def _on_data_updated(self, channel: str, payload: object):
        """监听盘中监控数据：同步刷新北美战报的实时报价"""
        # 缓存加载完成 → 回填历史数据并尝试拉实时报价
        if channel == "cache_loaded":
            if self._na_daily_codes and self.na_daily_table.rowCount() > 0:
                self._backfill_from_cache()
                self._fetch_na_quotes_independently()
            return
        if channel != "rt_quotes_refreshed" or not self._na_daily_codes:
            return
        if not payload:
            return
        # payload 是 all_signals 列表 [{代码, 现价, 涨幅%, 市值, ...}, ...]
        # 转换为 {code: {close, last_close, ...}} 格式供 _update_na_daily_realtime 使用
        na_codes = self._na_daily_codes
        quotes = {}
        for sig in payload:
            code = sig.get('代码', '')
            if code not in na_codes:
                continue
            # 从信号数据提取现价并构造 quotes 兼容结构
            price = sig.get('现价', 0)
            pct_str = str(sig.get('涨幅%', '0')).replace('%', '').replace('+', '')
            try:
                pct_val = float(pct_str)
                rt_close = float(price) if price else 0
                # 反推昨收: last_close = close / (1 + pct/100)
                if rt_close > 0 and pct_val != 0:
                    last_close = rt_close / (1 + pct_val / 100)
                else:
                    last_close = rt_close
            except (ValueError, TypeError):
                rt_close = 0
                last_close = 0
            quotes[code] = {'close': rt_close, 'last_close': last_close}
            # 同步市值缓存
            cap = sig.get('市值', '')
            if cap and str(cap) != '--':
                self._cap_cache_na[code] = str(cap)
        if quotes:
            QTimer.singleShot(0, lambda q=quotes: self._update_na_daily_realtime(q))

    # ================================================================
    # 定时刷新
    # ================================================================
    def _check_na_daily_schedule(self):
        """每 30 秒检测定时刷新"""
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return
        refresh_times = [
            (9, 20, 'full'),
            (14, 40, 'incremental'),
        ]
        today_str = now.strftime('%Y%m%d')
        for h, m, mode in refresh_times:
            key = f"{today_str}_{h:02d}{m:02d}"
            if key in self._na_daily_fired_today:
                continue
            target_minutes = h * 60 + m
            now_minutes = now.hour * 60 + now.minute
            if 0 <= now_minutes - target_minutes <= 1:
                self._na_daily_fired_today.add(key)
                if mode == 'full':
                    print(f"[北美战报] 全量刷新触发 {h:02d}:{m:02d}")
                    self._load_na_daily_report()
                else:
                    print(f"[北美战报] 增量刷新触发 {h:02d}:{m:02d}")
                    self._load_na_daily_incremental("#32D7E0")
                break

    def _save_na_daily_col_widths(self):
        """列宽变化时自动保存"""
        settings = QSettings("VCPHunter", "MainWindowQT")
        widths = [
            self.na_daily_table.columnWidth(i)
            for i in range(self.na_daily_table.columnCount())
        ]
        settings.setValue("na_daily_col_widths_v3", widths)

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, item):
        row = item.row()
        code_item = self.na_daily_table.item(row, 2)
        if code_item:
            event_bus.sig_show_kline.emit(code_item.text())

    def _show_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        item = self.na_daily_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        code_item = self.na_daily_table.item(row, 2)
        name_item = self.na_daily_table.item(row, 4)
        if not code_item or not name_item:
            return
        code = code_item.text()

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
        act_fav = menu.addAction("⭐ 加入关注池")
        menu.addSeparator()
        act_tdx = menu.addAction("🖥️ 跳转通达信")
        menu.addSeparator()
        act_ai = menu.addAction("🤖 AI深度诊断")

        action = menu.exec(self.na_daily_table.viewport().mapToGlobal(pos))
        if action == act_chart:
            event_bus.sig_show_kline.emit(code)
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(code)
            event_bus.sig_system_log.emit("info", f"已复制: {code}")
        elif action == act_fav:
            event_bus.sig_watchlist_changed.emit("add", code)
        elif action == act_tdx:
            self._launch_tdx(code)
        elif action == act_ai:
            event_bus.sig_open_ai_diag.emit(code, 'ai')

    # ================================================================
    # 战报解析
    # ================================================================
    def _parse_battle_report(self, content: str) -> list:
        """解析战报"二、标的狙击表"中的 markdown 表格"""
        stocks = []
        section_match = re.search(
            r'##\s*二、标的狙击表(.*?)(?=##\s*三、|$)',
            content, re.DOTALL
        )
        if not section_match:
            return stocks

        section = section_match.group(1)
        industry_pattern = re.compile(
            r'###\s+(?:🔴\s*|🟢\s*|🟡\s*)?(.+?)[\n\r]'
        )
        industry_matches = list(industry_pattern.finditer(section))

        for i, ind_match in enumerate(industry_matches):
            raw_industry = ind_match.group(1).strip()
            industry = re.split(r'[（(]', raw_industry)[0].strip()
            start = ind_match.end()
            end = (
                industry_matches[i + 1].start()
                if i + 1 < len(industry_matches)
                else len(section)
            )
            block = section[start:end]
            table_rows = re.findall(r'^\|(.+)\|$', block, re.MULTILINE)

            for row_text in table_rows:
                cells = [c.strip() for c in row_text.split('|')]
                if any(kw in cells for kw in ('标的', '名称')):
                    break

            col_elasticity = -3
            col_explosive = -2
            col_risk = -1

            for row_text in table_rows:
                cells = [c.strip() for c in row_text.split('|')]
                if not cells or len(cells) < 5:
                    continue
                if cells[0] in ('标的', '名称', '---', '') or '---' in cells[1]:
                    continue
                name = cells[0].replace('**', '').strip()
                code = cells[1].replace('**', '').strip()
                if not re.match(r'^\d{6}$', code):
                    continue
                stocks.append({
                    "行业": industry,
                    "名称": name,
                    "代码": code,
                    "弹性": cells[col_elasticity].strip(),
                    "爆发力": cells[col_explosive].strip(),
                    "风控": cells[col_risk].strip(),
                })
        return stocks

    def _parse_recommendations(self, content: str) -> dict:
        """解析战报"四、今日操作建议"中推荐表格"""
        result = {}
        section_match = re.search(
            r'##\s*四、今日操作建议(.*?)(?=##\s*[一二三四五六七八九十]|$)',
            content, re.DOTALL
        )
        if not section_match:
            return result
        section = section_match.group(1)

        in_rec_table = False
        found_separator = False

        for line in section.split('\n'):
            stripped = line.strip()
            if not in_rec_table and stripped.startswith('|') and '优先级' in stripped:
                in_rec_table = True
                found_separator = False
                continue
            if in_rec_table:
                if '---' in stripped and stripped.startswith('|'):
                    found_separator = True
                    continue
                if not stripped.startswith('|') or not stripped:
                    break
                if found_separator:
                    code_match = re.search(r'(\d{6})', stripped)
                    if code_match:
                        cells = [c.strip() for c in stripped.split('|')]
                        if len(cells) >= 4:
                            code = code_match.group(1)
                            reason = (
                                cells[-2].replace('**', '').strip()
                                if cells[-1] == ''
                                else cells[-1].replace('**', '').strip()
                            )
                            result[code] = reason
        return result

    # _launch_tdx 已迁移至 BaseStockTab 基类

