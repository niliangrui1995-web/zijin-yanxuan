# -*- coding: utf-8 -*-
"""
ui/tabs/ai_tracker_tab.py
AI产业链得分 独立 Tab 组件 — 从 AITrackerMixin 解耦重构为完全自治的 QWidget
从 AI_Data_Tracker CSV 加载得分>0 的股票
展示字段: 序号, 代码, 名称, 涨幅, 市值, 得分, 细分行业, 原因
"""
import os
import glob
import threading
import datetime

import pandas as pd
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel, QAbstractItemView, QLineEdit, QMenu
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


class AITrackerTab(BaseStockTab):
    """AI产业链得分 独立 Tab: 从最新 CSV 读取 AI含量>0 的股票并展示"""

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._ai_tracker_codes = set()
        self._cap_cache_ai_tracker = {}
        self.setStyleSheet("background-color: transparent;")

        self._init_ui()

        # 挂载 EventBus
        event_bus.sig_data_updated.connect(self._on_data_updated)

        # 延迟加载
        QTimer.singleShot(600, self._load_ai_tracker_data)

        # 独立定时器: 每30秒自动刷新涨幅、市值
        self._ai_tracker_timer = QTimer(self)
        self._ai_tracker_timer.timeout.connect(self._auto_refresh_ai_tracker)
        self._ai_tracker_timer.start(30 * 1000)

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

        # 表格
        columns = ["序号", "代码", "名称", "涨幅", "市值", "得分", "细分行业", "原因"]
        self.ai_tracker_table = QTableWidget()
        self.ai_tracker_table.setColumnCount(len(columns))
        self.ai_tracker_table.setHorizontalHeaderLabels(columns)
        self.ai_tracker_table.setAlternatingRowColors(True)
        self.ai_tracker_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.ai_tracker_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.ai_tracker_table.setSortingEnabled(True)
        self.ai_tracker_table.verticalHeader().setVisible(False)
        self.ai_tracker_table.setShowGrid(False)
        self.ai_tracker_table.setStyleSheet(
            self.ai_tracker_table.styleSheet() + "::item { padding: 0px 10px; }"
        )

        # 列宽
        header = self.ai_tracker_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [40, 70, 80, 70, 70, 55, 140, 200]
        for i, w in enumerate(default_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.ai_tracker_table.setColumnWidth(i, w)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.ai_tracker_table.verticalHeader().setDefaultSectionSize(40)

        # 恢复列宽
        settings = QSettings("VCPHunter", "MainWindowQT")
        saved = settings.value("ai_tracker_col_widths_v1")
        if saved and isinstance(saved, list) and len(saved) == len(default_widths):
            for i, w in enumerate(saved):
                try:
                    self.ai_tracker_table.setColumnWidth(i, int(w))
                except (ValueError, TypeError):
                    pass
        header.sectionResized.connect(self._save_ai_tracker_col_widths)

        # 双击 → K线图
        self.ai_tracker_table.itemDoubleClicked.connect(self._on_double_click)
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
        self.ai_tracker_table.setSortingEnabled(False)
        self.ai_tracker_table.setRowCount(len(df_filtered))

        for row, (_, record) in enumerate(df_filtered.iterrows()):
            code = str(record.get("代码", "")).zfill(6)
            name = str(record.get("股票名称", ""))
            score = float(record.get("AI含量", 0))
            industry = str(record.get("细分行业", ""))
            reason = str(record.get("原因", ""))

            items_data = [
                str(row + 1), code, name, "--", "--",
                f"{score:.1f}", industry, reason
            ]

            for col, text in enumerate(items_data):
                if col in (0, 3, 4, 5):
                    item = NumericTableWidgetItem(str(text))
                else:
                    item = QTableWidgetItem(str(text))
                item.setForeground(QColor(COLOR_FLAT))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )

                if col == 0:
                    item.setForeground(QColor("#F5F5F7"))
                if col == 1:
                    item.setForeground(QColor("#F5F5F7"))
                if col == 5:
                    if score >= 50:
                        item.setForeground(QColor(SCORE_EXCELLENT))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    elif score >= 20:
                        item.setForeground(QColor("#F59E0B"))
                    elif score >= 10:
                        item.setForeground(QColor("#60A5FA"))
                    else:
                        item.setForeground(QColor("#9CA3AF"))
                if col == 6:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                if col == 7:
                    item.setForeground(QColor("#93C5FD"))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    if reason and reason != "nan":
                        display_text = (
                            reason[:60] + "..."
                            if len(reason) > 60
                            else reason
                        )
                        item.setText(display_text)
                        item.setToolTip(
                            f'<div style="max-width:500px; line-height:1.5;">'
                            f'{reason}</div>'
                        )

                self.ai_tracker_table.setItem(row, col, item)

        self.ai_tracker_table.setSortingEnabled(True)
        self.ai_tracker_table.sortByColumn(5, Qt.SortOrder.DescendingOrder)

        # 1) 先用历史缓存回填涨幅（非交易时段也能显示）
        if self._ai_tracker_codes:
            self._backfill_from_cache()

        # 2) 立即尝试拉取实时报价覆盖
        if self._ai_tracker_codes:
            self._auto_refresh_ai_tracker(force=True)

    def _backfill_from_cache(self):
        """从 cache_data 历史数据回填涨幅/市值，确保非交易时段也有数据"""
        # 先批量计算市值
        codes = []
        close_prices = {}
        for row in range(self.ai_tracker_table.rowCount()):
            code_item = self.ai_tracker_table.item(row, 1)
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
                log.error(f"[AI算力链] 市值批量计算异常: {e}")

        for row in range(self.ai_tracker_table.rowCount()):
            code_item = self.ai_tracker_table.item(row, 1)
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

                # 列序号: 3=涨幅 4=市值
                # 涨幅
                existing = self.ai_tracker_table.item(row, 3)
                if not (existing and existing.text() != '--'):
                    if existing:
                        existing.setText(pct_str)
                    else:
                        item = NumericTableWidgetItem(pct_str)
                        item.setForeground(QColor(COLOR_FLAT))
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                        self.ai_tracker_table.setItem(row, 3, item)

                # 涨幅着色
                cell = self.ai_tracker_table.item(row, 3)
                if cell:
                    if pct > 0:
                        cell.setForeground(QColor(COLOR_RISE_STRONG if pct > 5 else COLOR_RISE))
                    elif pct < 0:
                        cell.setForeground(QColor(COLOR_FALL_STRONG if pct < -5 else COLOR_FALL))
                    else:
                        cell.setForeground(QColor(COLOR_FLAT))

                # 市值
                if cap_str:
                    existing_cap = self.ai_tracker_table.item(row, 4)
                    if not (existing_cap and existing_cap.text() != '--'):
                        if existing_cap:
                            existing_cap.setText(cap_str)
                        else:
                            cap_item = NumericTableWidgetItem(cap_str)
                            cap_item.setForeground(QColor(COLOR_FLAT))
                            cap_item.setTextAlignment(
                                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                            )
                            self.ai_tracker_table.setItem(row, 4, cap_item)
            except Exception:
                continue

    # ================================================================
    # 实时报价
    # ================================================================
    def _update_ai_tracker_realtime(self, quotes: dict):
        """用实时报价更新涨幅和市值"""
        if not self._ai_tracker_codes or self.ai_tracker_table.rowCount() == 0:
            return

        self.ai_tracker_table.setSortingEnabled(False)

        for row in range(self.ai_tracker_table.rowCount()):
            code_item = self.ai_tracker_table.item(row, 1)
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

            cap_str = self._cap_cache_ai_tracker.get(code, "--")

            updates = {3: pct_str, 4: cap_str}
            for col_idx, text in updates.items():
                existing = self.ai_tracker_table.item(row, col_idx)
                if existing:
                    existing.setText(text)
                else:
                    item = NumericTableWidgetItem(text)
                    item.setForeground(QColor(COLOR_FLAT))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    )
                    self.ai_tracker_table.setItem(row, col_idx, item)

                if col_idx == 3:
                    cell = self.ai_tracker_table.item(row, col_idx)
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

        self.ai_tracker_table.setSortingEnabled(True)

    def _auto_refresh_ai_tracker(self, force=False):
        """独立定时器驱动的AI跟踪Tab实时刷新"""
        # 条件判断：联网 + 有数据
        if not self.data_provider or self.data_provider._offline:
            return
        if not self._ai_tracker_codes:
            return

        # 非强制模式下检查交易时段 (9:25-15:00)
        if not force:
            now = datetime.datetime.now()
            hour_min = now.hour * 100 + now.minute
            if not (925 <= hour_min <= 1500):
                return
            if now.weekday() >= 5:
                return

        def _bg_fetch():
            try:
                ai_codes = list(self._ai_tracker_codes)
                ai_quotes = self.data_provider.fetch_realtime_quotes_batch(ai_codes)
                if not ai_quotes:
                    return
                # 补全市值缓存
                codes_need_cap = [
                    c for c in ai_codes if c not in self._cap_cache_ai_tracker
                ]
                if codes_need_cap:
                    try:
                        from vcp.engine import VCPEngine
                        close_prices = {
                            c: float(ai_quotes[c].get('close', 0) or 0)
                            for c in codes_need_cap if c in ai_quotes
                        }
                        cap_results = VCPEngine.batch_check_market_cap(
                            codes_need_cap, close_prices=close_prices
                        )
                        for c in codes_need_cap:
                            cap = cap_results.get(c)
                            if cap and cap > 0:
                                self._cap_cache_ai_tracker[c] = f"{cap / 1e8:.0f}亿"
                            else:
                                self._cap_cache_ai_tracker[c] = '--'
                    except Exception as _e:
                        log.error(f"[AI跟踪-独立刷新] 市值补全异常: {_e}")

                # 安全切回主线程
                QTimer.singleShot(
                    0,
                    lambda q=ai_quotes: self._update_ai_tracker_realtime(q)
                )
            except Exception as _e:
                log.error(f"[AI跟踪-独立刷新] 异常: {_e}")

        threading.Thread(target=_bg_fetch, daemon=True).start()

    # ================================================================
    # EventBus 事件
    # ================================================================
    def _on_data_updated(self, channel: str, payload: object):
        """监听盘中监控数据，同步刷新涨幅和市值"""
        # 缓存加载完成 → 回填历史数据并尝试拉实时报价
        if channel == "cache_loaded":
            if self._ai_tracker_codes and self.ai_tracker_table.rowCount() > 0:
                self._backfill_from_cache()
                self._auto_refresh_ai_tracker(force=True)
            return
        if channel != "rt_quotes_refreshed" or not self._ai_tracker_codes:
            return
        if not payload:
            return
        # payload 是 all_signals 列表，转换为 quotes dict 格式
        ai_codes = self._ai_tracker_codes
        quotes = {}
        for sig in payload:
            code = sig.get('代码', '')
            if code not in ai_codes:
                continue
            price = sig.get('现价', 0)
            pct_str = str(sig.get('涨幅%', '0')).replace('%', '').replace('+', '')
            try:
                pct_val = float(pct_str)
                rt_close = float(price) if price else 0
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
                self._cap_cache_ai_tracker[code] = str(cap)
        if quotes:
            QTimer.singleShot(0, lambda q=quotes: self._update_ai_tracker_realtime(q))

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, item):
        row = item.row()
        code_item = self.ai_tracker_table.item(row, 1)
        if code_item:
            event_bus.sig_show_kline.emit(code_item.text())

    def _show_context_menu(self, pos):
        item = self.ai_tracker_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        code_item = self.ai_tracker_table.item(row, 1)
        name_item = self.ai_tracker_table.item(row, 2)
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

        action = menu.exec(self.ai_tracker_table.viewport().mapToGlobal(pos))
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
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称匹配"""
        text = text.strip().lower()
        for r in range(self.ai_tracker_table.rowCount()):
            if not text:
                self.ai_tracker_table.setRowHidden(r, False)
                continue
            code_item = self.ai_tracker_table.item(r, 1)
            name_item = self.ai_tracker_table.item(r, 2)
            code_text = code_item.text().lower() if code_item else ""
            name_text = name_item.text().lower() if name_item else ""
            match = text in code_text or text in name_text
            self.ai_tracker_table.setRowHidden(r, not match)

    def _save_ai_tracker_col_widths(self):
        """列宽变化时自动保存"""
        settings = QSettings("VCPHunter", "MainWindowQT")
        widths = [
            self.ai_tracker_table.columnWidth(i)
            for i in range(self.ai_tracker_table.columnCount())
        ]
        settings.setValue("ai_tracker_col_widths_v1", widths)

    # _launch_tdx 已迁移至 BaseStockTab 基类

