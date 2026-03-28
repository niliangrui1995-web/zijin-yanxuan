"""
SimulatorTab — VCP 图表眼训练模拟器 (PyQt6 完整移植版)

功能对标 vcp_simulator.pyw 100%：
  ✅ 四态状态机 (READY/OBSERVING/HOLDING/FINISHED)
  ✅ 多次交易（卖出后回 OBSERVING 可继续推演）
  ✅ 空仓观察天数上限检测
  ✅ 智能跳至下一信号 (evaluate_conditions)
  ✅ 放弃时持仓先强平
  ✅ 摩擦成本与旧版一致
  ✅ 最大回撤统计
  ✅ RPS120/250 + 成交额动态显示
  ✅ VCP结构矩形/峰位/R标签/MACD (由 sim_chart 实现)
  ✅ 重新推演清空该股票记录
  ✅ 训练进度列表 + 双击重训
  ✅ 右键撤销最后一笔交易
  ✅ Ctrl+E 导出 Excel / Ctrl+D 盲盒 / Ctrl+Shift+R 揭盅
  ✅ Home/End 跳转 / F5/F9 辅助
  ✅ 再次生成题库按钮
  ✅ 心跳动画
"""
import datetime
import numpy as np
import pandas as pd
import os
import json
import random

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QFrame, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QMessageBox, QGroupBox, QMenu, QFileDialog, QComboBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QShortcut, QKeySequence

from vcp_simulator.sim_engine import (
    SimBankWorker, TradeRecord,
    STATE_READY, STATE_OBSERVING, STATE_HOLDING, STATE_FINISHED,
    FRICTION_COST, DATE_FMT
)
from vcp_simulator.sim_chart import SimulatorChartWidget
from vcp.models import VCPParams

# 兼容：如果主程序定义了 NumericTableWidgetItem 就用，否则 fallback
try:
    from ui.components import NumericTableWidgetItem
except ImportError:
    class NumericTableWidgetItem(QTableWidgetItem):
        def __lt__(self, other):
            try:
                return float(self.text().replace('%', '').replace('+', '')) < \
                       float(other.text().replace('%', '').replace('+', ''))
            except ValueError:
                return super().__lt__(other)


class SimulatorTab(QWidget):
    def __init__(self, data_provider, engine):
        super().__init__()
        self.data_provider = data_provider
        self.vcp_engine = engine

        # 题库管理
        self.signal_list = []
        self.current_signal_index = -1
        self.completed_indices = set()
        self.rps_matrix = {}  # 缓存全市场每日 RPS

        # 当前推演数据
        self.current_df = None
        self.current_code = None
        self.current_name = None
        self.current_signal_details = {}
        self.trigger_loc = None
        self.current_loc = None
        self.pivot = 0.0

        # 持仓信息
        self.state = STATE_READY
        self.entry_loc = None
        self.entry_price = None
        self.exit_loc = None
        self.exit_price = None
        self.max_hold_days = 60

        # 战绩
        self.trades = []

        # 后台任务标记
        self._busy = False
        self.worker = None

        self._build_ui()
        self._bind_shortcuts()
        self._set_state(STATE_READY, False)

        # 心跳动画定时器
        self._heartbeat_idx = 0
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._heartbeat_tick)
        self._heartbeat_timer.start(1000)

    # ================================================================
    # UI 构建
    # ================================================================
    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ═══════════════════════════════════════════
        # 左侧控制面板 (Left Control Panel)
        # ═══════════════════════════════════════════
        left_panel = QFrame()
        left_panel.setObjectName("moduleCard")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 10, 12, 10)
        left_layout.setSpacing(8)

        # ── 标题区 ─────────────────────────────
        title_frame = QFrame()
        title_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(37,99,235,0.15), stop:1 rgba(59,130,246,0.05));
                border-radius: 8px;
                border: 1px solid rgba(59,130,246,0.15);
            }
        """)
        title_l = QHBoxLayout(title_frame)
        title_l.setContentsMargins(12, 8, 12, 8)

        title_text_l = QVBoxLayout()
        title = QLabel("VCP 图表眼训练")
        title.setStyleSheet("color: #E5E7EB; font-size: 15px; font-weight: bold; background: transparent;")
        subtitle = QLabel("模拟推演 · 盲盒训练 · 实战复盘")
        subtitle.setStyleSheet("color: #6B7280; font-size: 11px; background: transparent;")
        title_text_l.addWidget(title)
        title_text_l.addWidget(subtitle)
        title_text_l.setSpacing(2)
        title_l.addLayout(title_text_l)
        title_l.addStretch()

        self.lbl_heartbeat = QLabel("|")
        self.lbl_heartbeat.setStyleSheet("color: #3B82F6; font-family: Consolas; font-size: 14px; background: transparent;")
        self.lbl_heartbeat.setFixedWidth(16)
        title_l.addWidget(self.lbl_heartbeat)
        left_layout.addWidget(title_frame)

        # ── 状态栏 ─────────────────────────────
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("""
            color: #60A5FA; font-weight: 600; font-size: 12px;
            padding: 6px 10px;
            background-color: rgba(37, 99, 235, 0.08);
            border-radius: 6px;
            border: 1px solid rgba(59, 130, 246, 0.12);
        """)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(32)
        left_layout.addWidget(self.lbl_status)

        # ── 1. 训练参数 ────────────────────────
        param_group = QGroupBox("训练参数")
        pl = QVBoxLayout(param_group)
        pl.setSpacing(5)
        pl.setContentsMargins(8, 6, 8, 6)

        def add_row(parent, label_text, widget):
            r = QHBoxLayout()
            r.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(78)
            lbl.setStyleSheet("color: #6B7280; font-size: 12px;")
            r.addWidget(lbl)
            r.addWidget(widget)
            parent.addLayout(r)

        self.ent_sd = QLineEdit((datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y%m%d"))
        self.ent_ed = QLineEdit(datetime.datetime.now().strftime("%Y%m%d"))
        self.ent_rps = QLineEdit("80")
        self.ent_hold = QLineEdit("60")

        add_row(pl, "开始日期", self.ent_sd)
        add_row(pl, "结束日期", self.ent_ed)
        add_row(pl, "RPS 阈值", self.ent_rps)
        add_row(pl, "观察天数", self.ent_hold)

        self.chk_blind = QCheckBox("盲盒模式 (隐藏代码/日期)")
        self.chk_blind.stateChanged.connect(lambda: self._render_chart())
        pl.addWidget(self.chk_blind)
        left_layout.addWidget(param_group)

        # ── 2. 题库操作 ────────────────────────
        # 出题源选择
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        src_lbl = QLabel("出题源")
        src_lbl.setFixedWidth(78)
        src_lbl.setStyleSheet("color: #6B7280; font-size: 12px;")
        self.cmb_source = QComboBox()
        self.cmb_source.addItems(["全市场扫描", "历史扫描缓存"])
        self.cmb_source.setToolTip(
            "全市场扫描：实时构建题库（耗时较长）\n"
            "历史扫描缓存：从上次扫描结果快速加载（秒开）"
        )
        src_row.addWidget(src_lbl)
        src_row.addWidget(self.cmb_source)
        left_layout.addLayout(src_row)

        self.btn_load = QPushButton("  生成训练题库")
        self.btn_load.setProperty("class", "ctaPrimary")
        self.btn_load.clicked.connect(self._build_bank)
        left_layout.addWidget(self.btn_load)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_reload = QPushButton("重新生成")
        self.btn_reload.setProperty("class", "ctaSecondary")
        self.btn_reload.clicked.connect(self._build_bank)
        self.btn_reload.setEnabled(False)
        btn_row.addWidget(self.btn_reload)

        self.btn_next_q = QPushButton("▶  抽取下一题  [N]")
        self.btn_next_q.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #059669, stop:1 #10B981);
                color: #FFFFFF; border: none; border-radius: 7px;
                font-weight: bold; font-size: 13px; min-height: 32px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #10B981, stop:1 #34D399);
            }
            QPushButton:disabled {
                background-color: #0F2922; color: #2D6B5A;
            }
        """)
        self.btn_next_q.clicked.connect(self.next_stock)
        self.btn_next_q.setEnabled(False)
        btn_row.addWidget(self.btn_next_q)
        left_layout.addLayout(btn_row)

        # ── 3. 训练进度 ───────────────────────
        prog_group = QGroupBox("训练进度")
        prog_group.setStyleSheet("""
            QGroupBox {
                border-left: 3px solid #8B5CF6;
            }
        """)
        prog_l = QVBoxLayout(prog_group)
        prog_l.setContentsMargins(8, 6, 8, 6)

        self.lbl_progress = QLabel("进度: 0 / 0 题")
        self.lbl_progress.setStyleSheet("color: #A78BFA; font-weight: bold; font-size: 12px;")
        prog_l.addWidget(self.lbl_progress)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(3)
        self.history_table.setHorizontalHeaderLabels(["#", "代码", "名称"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setMaximumHeight(100)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.doubleClicked.connect(self._retrain_selected)
        prog_l.addWidget(self.history_table)

        self.btn_retrain = QPushButton("重新训练选中题")
        self.btn_retrain.setProperty("class", "ctaSecondary")
        self.btn_retrain.clicked.connect(self._retrain_selected)
        prog_l.addWidget(self.btn_retrain)
        left_layout.addWidget(prog_group)

        # ── 4. 动态行情与持仓 ──────────────────
        pos_group = QGroupBox("行情与持仓")
        pos_group.setStyleSheet("""
            QGroupBox {
                border-left: 3px solid #F59E0B;
            }
        """)
        pos_l = QVBoxLayout(pos_group)
        pos_l.setSpacing(4)
        pos_l.setContentsMargins(8, 6, 8, 6)

        # 持仓状态指示器 (带圆点)
        state_row = QHBoxLayout()
        self.lbl_state_dot = QLabel("●")
        self.lbl_state_dot.setStyleSheet("color: #6B7280; font-size: 10px;")
        self.lbl_state_dot.setFixedWidth(14)
        self.lbl_pos_state = QLabel("空仓")
        self.lbl_pos_state.setStyleSheet("color: #6B7280; font-weight: bold; font-size: 12px;")
        state_row.addWidget(self.lbl_state_dot)
        state_row.addWidget(self.lbl_pos_state)
        state_row.addStretch()
        pos_l.addLayout(state_row)

        # 浮盈大字显示
        self.lbl_pos_pnl = QLabel("—")
        self.lbl_pos_pnl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pos_pnl.setStyleSheet("""
            font-size: 22px; font-weight: bold; color: #6B7280;
            padding: 4px 0;
            background-color: rgba(255,255,255,0.02);
            border-radius: 6px;
        """)
        pos_l.addWidget(self.lbl_pos_pnl)

        # 信息网格
        self.lbl_pos_date = QLabel("—")
        self.lbl_pos_price = QLabel("—")
        self.lbl_pos_turnover = QLabel("—")
        self.lbl_pos_rps = QLabel("—")
        self.lbl_pos_cost = QLabel("—")
        self.lbl_pos_hold = QLabel("—")

        info_pairs = [
            ("日期", self.lbl_pos_date),
            ("收盘", self.lbl_pos_price),
            ("成交额", self.lbl_pos_turnover),
            ("RPS", self.lbl_pos_rps),
            ("成本", self.lbl_pos_cost),
            ("持仓", self.lbl_pos_hold),
        ]
        for label_text, widget in info_pairs:
            r = QHBoxLayout()
            r.setSpacing(6)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(40)
            lbl.setStyleSheet("color: #4B5563; font-size: 11px;")
            widget.setStyleSheet("color: #9CA3AF; font-size: 12px;")
            r.addWidget(lbl)
            r.addWidget(widget)
            pos_l.addLayout(r)
        left_layout.addWidget(pos_group)

        # ── 5. 交易控制 ───────────────────────
        trade_group = QGroupBox("交易操作")
        trade_group.setStyleSheet("""
            QGroupBox {
                border-left: 3px solid #EF4444;
            }
        """)
        tl = QVBoxLayout(trade_group)
        tl.setSpacing(5)
        tl.setContentsMargins(8, 6, 8, 6)

        r1 = QHBoxLayout()
        r1.setSpacing(6)
        self.btn_buy = QPushButton("买入 [B]")
        self.btn_buy.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #B91C1C, stop:1 #DC2626);
                color: #FCA5A5; border: none; border-radius: 7px;
                font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #EF4444; color: #FFFFFF; }
            QPushButton:disabled { background-color: #1A1215; color: #4B2C2C; }
        """)
        self.btn_buy.clicked.connect(self._action_buy)

        self.btn_sell = QPushButton("卖出 [S]")
        self.btn_sell.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #047857, stop:1 #059669);
                color: #6EE7B7; border: none; border-radius: 7px;
                font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #10B981; color: #FFFFFF; }
            QPushButton:disabled { background-color: #0F1A15; color: #2D4B3A; }
        """)
        self.btn_sell.clicked.connect(self._action_sell)
        r1.addWidget(self.btn_buy)
        r1.addWidget(self.btn_sell)
        tl.addLayout(r1)

        self.btn_next_day = QPushButton("下一天 K线 / 持有观察  [Space]")
        self.btn_next_day.setProperty("class", "ctaSecondary")
        self.btn_next_day.clicked.connect(self._action_next_day)
        tl.addWidget(self.btn_next_day)

        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.btn_skip = QPushButton("跳至下一信号")
        self.btn_skip.setStyleSheet("""
            QPushButton {
                background-color: #1E3A5F; color: #60A5FA;
                border: 1px solid #2563EB; border-radius: 7px;
                font-weight: 600; font-size: 12px;
            }
            QPushButton:hover { background-color: #1D4ED8; color: #FFFFFF; border-color: #3B82F6; }
            QPushButton:disabled { background-color: #111827; color: #1E3A5F; border-color: #1A1E28; }
        """)
        self.btn_skip.clicked.connect(self._action_skip_to_next_signal)

        self.btn_abandon = QPushButton("放弃此股")
        self.btn_abandon.setProperty("class", "ctaSecondary")
        self.btn_abandon.clicked.connect(self._action_abandon)
        r2.addWidget(self.btn_skip)
        r2.addWidget(self.btn_abandon)
        tl.addLayout(r2)

        self.btn_rebuy = QPushButton("清空记录 · 重新推演")
        self.btn_rebuy.setStyleSheet("""
            QPushButton {
                background-color: #422006; color: #FBBF24;
                border: 1px solid #92400E; border-radius: 7px;
                font-weight: 600; font-size: 12px;
            }
            QPushButton:hover { background-color: #78350F; color: #FDE68A; border-color: #B45309; }
            QPushButton:disabled { background-color: #111318; color: #3A2D1A; border-color: #1A1E28; }
        """)
        self.btn_rebuy.clicked.connect(self._action_rebuy)
        tl.addWidget(self.btn_rebuy)

        left_layout.addWidget(trade_group)
        left_layout.addStretch()

        self.splitter.addWidget(left_panel)

        # ═══════════════════════════════════════════
        # 右侧面板 (图表 + 战绩)
        # ═══════════════════════════════════════════
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        self.chart = SimulatorChartWidget(self)
        right_layout.addWidget(self.chart, 3)

        # 战绩表
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels(["#", "代码", "名称", "触发日", "买入", "卖出", "天数", "收益%"])
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.trade_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.trade_table.verticalHeader().setVisible(False)
        self.trade_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trade_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trade_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.trade_table.customContextMenuRequested.connect(self._trade_context_menu)
        right_layout.addWidget(self.trade_table, 1)

        # 战绩摘要
        self.lbl_summary = QLabel("实战复盘: 0 笔 | 客观胜率: 0.0% | 平均收益: 0.00% | 最大回撤: 0.00%")
        self.lbl_summary.setStyleSheet("""
            color: #C9CDD4; font-size: 12px; font-weight: 600;
            padding: 6px 12px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(37,99,235,0.08), stop:1 rgba(139,92,246,0.05));
            border-radius: 6px;
            border: 1px solid rgba(59,130,246,0.1);
        """)
        right_layout.addWidget(self.lbl_summary)

        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([300, 1000])

    # ================================================================
    # 快捷键系统
    # ================================================================
    def _bind_shortcuts(self):
        def _space():
            if self.state in (STATE_READY, STATE_OBSERVING, STATE_HOLDING) and not self._busy:
                self._action_next_day()

        def _buy():
            if self.state in (STATE_READY, STATE_OBSERVING) and not self._busy:
                self._action_buy()

        def _sell():
            if self.state == STATE_HOLDING and not self._busy:
                if self.current_loc is not None and self.entry_loc is not None and self.current_loc >= self.entry_loc:
                    self._action_sell()

        def _next():
            if not self._busy and self.signal_list:
                self.next_stock()

        # 基础快捷键
        QShortcut(QKeySequence("Space"), self, activated=_space)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=_space)
        QShortcut(QKeySequence("B"), self, activated=_buy)
        QShortcut(QKeySequence("S"), self, activated=_sell)
        QShortcut(QKeySequence("N"), self, activated=_next)

        # 增强快捷键
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self._chart_nav(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self._chart_nav(1))
        QShortcut(QKeySequence("Home"), self, activated=self._chart_jump_trigger)
        QShortcut(QKeySequence("End"), self, activated=self._chart_jump_end)
        QShortcut(QKeySequence("Delete"), self, activated=self._action_abandon)
        QShortcut(QKeySequence("Ctrl+Delete"), self, activated=self._action_rebuy)
        QShortcut(QKeySequence("F9"), self, activated=self._action_skip_to_next_signal)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._build_bank)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=_next)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=_buy)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=_sell)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._export_trades)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self._toggle_blind)
        QShortcut(QKeySequence("Ctrl+Shift+R"), self, activated=self._temp_reveal_code)

    # ================================================================
    # 心跳
    # ================================================================
    def _heartbeat_tick(self):
        self._heartbeat_idx = (self._heartbeat_idx + 1) % 4
        self.lbl_heartbeat.setText(['|', '/', '-', '\\'][self._heartbeat_idx])

    # ================================================================
    # 状态机
    # ================================================================
    def _set_state(self, state, buttons_enabled=True):
        self.state = state
        all_btns = [self.btn_buy, self.btn_sell, self.btn_next_day,
                    self.btn_skip, self.btn_abandon, self.btn_rebuy]
        for b in all_btns:
            b.setEnabled(False)

        if not buttons_enabled:
            return

        if state in (STATE_READY, STATE_OBSERVING):
            self.btn_buy.setEnabled(True)
            self.btn_sell.setEnabled(False)
            self.btn_next_day.setEnabled(True)
            self.btn_skip.setEnabled(True)
            self.btn_abandon.setEnabled(True)
            self.btn_rebuy.setEnabled(False)
        elif state == STATE_HOLDING:
            self.btn_buy.setEnabled(False)
            can_sell = (self.current_loc is not None and self.entry_loc is not None
                        and self.current_loc >= self.entry_loc)
            self.btn_sell.setEnabled(can_sell)
            self.btn_next_day.setEnabled(True)
            self.btn_skip.setEnabled(False)
            self.btn_abandon.setEnabled(True)
            self.btn_rebuy.setEnabled(False)
        elif state == STATE_FINISHED:
            self.btn_rebuy.setEnabled(True)

    def _set_status(self, msg):
        self.lbl_status.setText(msg)

    # ================================================================
    # 题库构建
    # ================================================================
    def _build_bank(self):
        if self._busy:
            return

        # 根据出题源选择不同的构建方式
        source = self.cmb_source.currentText()
        if source == "历史扫描缓存":
            self._build_bank_from_scan_cache()
            return

        # 原始的全市场扫描模式
        try:
            sd = self.ent_sd.text().strip()
            ed = self.ent_ed.text().strip()
            rps = int(self.ent_rps.text())
            hold = int(self.ent_hold.text())
            self.max_hold_days = hold
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请检查参数是否为整数。")
            return

        self._busy = True
        self._set_state(STATE_READY, False)
        self.btn_load.setEnabled(False)
        self.btn_reload.setEnabled(False)
        self.btn_next_q.setEnabled(False)

        self.worker = SimBankWorker(self.data_provider, self.vcp_engine, sd, ed, rps, hold)
        self.worker.progress.connect(self._set_status)
        self.worker.bank_ready.connect(self._on_bank_built)
        self.worker.start()

    def _build_bank_from_scan_cache(self):
        """从历史扫描结果缓存快速构建题库（秒开）"""
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'Cache', 'scan_results_cache.json'
        )
        if not os.path.exists(cache_path):
            QMessageBox.warning(self, "无缓存",
                "未找到扫描结果缓存文件。\n"
                "请先在主终端执行一次区间扫描，或切换为‘全市场扫描’模式。")
            return

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"扫描缓存文件解析失败：{e}")
            return

        results = data.get('results', [])
        cache_date = data.get('date', '未知')
        if not results:
            QMessageBox.information(self, "提示", "扫描缓存中无有效结果。")
            return

        try:
            hold = int(self.ent_hold.text())
            self.max_hold_days = hold
        except ValueError:
            self.max_hold_days = 60

        # 从扫描结果构建 signal_list
        hits = []
        for r in results:
            code = r.get('code', r.get('代码', ''))
            name = r.get('name', r.get('名称', ''))
            trigger = r.get('trigger_date', r.get('触发日期', ''))
            if not code or not trigger:
                continue
            hits.append({
                'code': code,
                'name': name,
                'trigger_date': trigger,
                'details': {
                    '评分': r.get('score', r.get('评分', 0)),
                    '区间最高价': r.get('pivot', r.get('区间最高价', 0)),
                    'rps120': r.get('rps120', 0),
                    'rps250': r.get('rps250', 0),
                }
            })

        if not hits:
            QMessageBox.information(self, "提示", "扫描缓存中无有效标的。")
            return

        # 随机打乱
        random.shuffle(hits)

        # 直接调用 _on_bank_built 复用现有逻辑
        msg = f"✅ 从扫描缓存加载完成！共 {len(hits)} 个实战样本（缓存日期: {cache_date}）"
        self._on_bank_built(True, hits, {}, msg)

    def _on_bank_built(self, ok, data, rps_matrix, msg):
        self._set_status(msg)
        self._busy = False
        self.btn_load.setEnabled(True)

        if ok and data:
            self.signal_list = data
            self.rps_matrix = rps_matrix
            self.current_signal_index = -1
            self.completed_indices.clear()

            # 清空历史
            self.history_table.setRowCount(0)
            # 清空战绩
            self.trades.clear()
            self.trade_table.setRowCount(0)
            self._update_summary()

            self.btn_next_q.setEnabled(True)
            self.btn_reload.setEnabled(True)
            self._set_state(STATE_READY, False)
            self._update_progress()
            self._update_position_text()
        else:
            self.btn_reload.setEnabled(bool(self.signal_list))
            self.btn_next_q.setEnabled(bool(self.signal_list))

    # ================================================================
    # 题目调度
    # ================================================================
    def _update_progress(self):
        t = len(self.signal_list)
        c = len(self.completed_indices)
        if self.chk_blind.isChecked() and t > 0:
            cur = self.current_signal_index + 1 if self.current_signal_index >= 0 else 0
            self.lbl_progress.setText(f"当前第 {cur} / 共 {t} 题  (已完成 {c} 题)")
        else:
            self.lbl_progress.setText(f"进度: {c} / {t} 题")

    def _mark_current_completed(self):
        if self.current_signal_index >= 0 and self.current_signal_index not in self.completed_indices:
            self.completed_indices.add(self.current_signal_index)
            sig = self.signal_list[self.current_signal_index]
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(str(self.current_signal_index + 1)))
            self.history_table.setItem(row, 1, QTableWidgetItem(sig['code']))
            self.history_table.setItem(row, 2, QTableWidgetItem(sig['name']))
            self._update_progress()

    def next_stock(self):
        if not self.signal_list:
            QMessageBox.information(self, "提示", "题库为空，请先生成。")
            return

        self._mark_current_completed()

        self.current_signal_index += 1
        if self.current_signal_index >= len(self.signal_list):
            QMessageBox.information(self, "训练结束", "真棒！你已经刷完了所有盲盒题目。")
            self.current_signal_index = len(self.signal_list) - 1
            return

        self._load_stock_by_index(self.current_signal_index)

    def _retrain_selected(self):
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先在列表中选中一只已训练过的股票。")
            return
        row = rows[0].row()
        item = self.history_table.item(row, 0)
        if not item:
            return
        try:
            target_idx = int(item.text()) - 1
            self._mark_current_completed()
            self.current_signal_index = target_idx
            self._load_stock_by_index(target_idx)
        except Exception:
            pass

    def _load_stock_by_index(self, idx):
        while idx < len(self.signal_list):
            sig = self.signal_list[idx]
            code, name, trigger_str = sig["code"], sig["name"], sig["trigger_date"]
            self.current_signal_details = sig.get("details", {})

            df = self.data_provider.get_data(code)
            if df is None or len(df) < 260:
                self._set_status(f"{code} 数据不足跳过。")
                self.current_signal_index = idx
                self._mark_current_completed()
                idx += 1
                self.current_signal_index = idx
                continue

            # 计算技术指标（MACD等）
            if 'MACD' not in df.columns or df['MACD'].isna().all():
                try:
                    df = self.vcp_engine.calculate_indicators(df)
                except Exception:
                    pass

            try:
                trigger_ts = pd.to_datetime(trigger_str)
                loc = df.index.get_loc(trigger_ts)
            except (KeyError, Exception):
                self._set_status(f"{code} 触发日异常跳过。")
                self.current_signal_index = idx
                self._mark_current_completed()
                idx += 1
                self.current_signal_index = idx
                continue

            self.current_df = df
            self.current_code = code
            self.current_name = name
            self.trigger_loc = loc
            self.current_loc = loc
            self.pivot = self.current_signal_details.get('区间最高价', 0.0)

            self.entry_loc = None
            self.entry_price = None
            self.exit_loc = None
            self.exit_price = None

            sname = "***" if self.chk_blind.isChecked() else f"{name} ({code})"
            self._set_status(f"当前题目 {idx + 1}/{len(self.signal_list)}  |  触发基准日 {trigger_str}")
            self._set_state(STATE_READY, True)
            self._update_position_text()
            self._update_progress()
            self._render_chart(show_full_future=False)
            return

        QMessageBox.information(self, "训练结束", "所有剩余题目数据异常，已全部跳过。")
        self.current_signal_index = len(self.signal_list) - 1
        self._set_state(STATE_FINISHED, False)
        self._update_progress()

    # ================================================================
    # 核心状态跳跃（支持多次交易）
    # ================================================================

    def _action_buy(self):
        """T 天点击买入 → 成交价为 T+1 日开盘价"""
        if self.current_df is None or self.current_loc is None:
            return
        next_loc = self.current_loc + 1
        if next_loc >= len(self.current_df):
            QMessageBox.warning(self, "无法买入",
                                "已是数据最后一天，无 T+1 日数据，无法成交。")
            self._set_status("⚠ 数据末端，无法买入（需T+1开盘价）")
            return

        row_t1 = self.current_df.iloc[next_loc]
        if pd.isna(row_t1.get("open", float("nan"))):
            self._set_status("⚠ T+1 日开盘价为 NaN，无法买入")
            return

        buy_p = float(row_t1["open"])
        self.entry_loc = next_loc
        self.entry_price = buy_p
        self.exit_loc = None
        self.exit_price = None

        # 前进到买入日
        self.current_loc = next_loc
        self._set_state(STATE_HOLDING, True)
        self._update_position_text()
        self._render_chart(show_full_future=False)

    def _action_next_day(self):
        """空仓或持仓时：仅前进一根K线"""
        if self.state in (STATE_READY, STATE_OBSERVING):
            if self.current_df is None or self.current_loc is None or self.trigger_loc is None:
                return
            # 空仓观察天数上限检测
            limit_loc = min(self.trigger_loc + self.max_hold_days, len(self.current_df) - 1)
            if self.current_loc >= limit_loc:
                self._set_status("已达最长观察天数，未出现新买点，本题结束。")
                self._close_question_completely()
                return
            self.current_loc += 1
            self._update_position_text()
            self._render_chart(show_full_future=False)
        elif self.state == STATE_HOLDING:
            self._action_hold_next_day()

    def _action_hold_next_day(self):
        """持仓时步进一天；数据末端自动强平"""
        if self.current_df is None or self.current_loc is None or self.entry_loc is None:
            return
        if self.current_loc + 1 >= len(self.current_df):
            self._set_status("数据已到末端，自动以最后一天收盘价强平。")
            self._action_sell(force_end_question=True)
            return
        self.current_loc += 1
        self._update_position_text()
        self._render_chart(show_full_future=False)
        self._set_state(STATE_HOLDING, True)

    def _action_sell(self, force_end_question=False):
        """T 天点击卖出 → 成交价为 T+1 日开盘价；数据末端强制用当日收盘"""
        if self.current_df is None or self.current_loc is None:
            return
        if self.entry_loc is None or self.entry_price is None:
            return

        buy_p = float(self.entry_price)
        next_loc = self.current_loc + 1

        if next_loc < len(self.current_df):
            row_t1 = self.current_df.iloc[next_loc]
            if pd.isna(row_t1.get("open", float("nan"))):
                self._set_status("⚠ T+1 日开盘价为 NaN，无法卖出")
                return
            sell_p = float(row_t1["open"])
            exit_loc = next_loc
        else:
            row_last = self.current_df.iloc[self.current_loc]
            if pd.isna(row_last.get("close", float("nan"))):
                self._set_status("⚠ 当日收盘价为 NaN，无法强平")
                return
            sell_p = float(row_last["close"])
            exit_loc = self.current_loc

        # 摩擦成本计算（与旧版完全一致）
        h_days = max(0, exit_loc - self.entry_loc)
        cost_buy = buy_p * (1.0 + FRICTION_COST)
        cost_sell = sell_p * (1.0 - FRICTION_COST)
        ret = (cost_sell - cost_buy) / cost_buy if cost_buy > 0 else 0.0

        tr = TradeRecord(
            code=self.current_code, name=self.current_name,
            trigger_date=self.current_df.index[self.trigger_loc].strftime(DATE_FMT),
            buy_price=buy_p, sell_price=sell_p, hold_days=h_days, ret=ret,
            entry_loc=self.entry_loc, exit_loc=exit_loc
        )
        self.trades.append(tr)
        self._rebuild_trade_table()
        self._update_summary()

        self.entry_loc = None
        self.entry_price = None
        self.exit_loc = exit_loc
        self.exit_price = sell_p

        if force_end_question:
            self._set_status(f"已强平 (收益 {ret*100:.2f}%)，结束本题推演。")
            self._close_question_completely()
        else:
            # 卖出后回到 OBSERVING，可继续做多次交易
            self._set_status(f"平仓完成 (收益 {ret*100:.2f}%)，资金已回笼，可继续推演。")
            self.current_loc = exit_loc
            self._set_state(STATE_OBSERVING, True)
            self._update_position_text()
            self._render_chart(show_full_future=False)

    def _action_skip_to_next_signal(self):
        """空仓状态下：智能快进至下一个合理买点，或自动结束"""
        if self.current_df is None or self.current_loc is None or self.trigger_loc is None:
            return

        found = False
        limit_loc = min(self.trigger_loc + self.max_hold_days, len(self.current_df) - 1)

        try:
            rps_th = int(self.ent_rps.text())
        except Exception:
            rps_th = 80
        rt_params = VCPParams(rps_threshold=rps_th)

        for loc in range(self.current_loc + 1, limit_loc + 1):
            offset = loc - self.trigger_loc

            # 条件1: 处于爆发点后5天内的延续突破
            if 1 <= offset <= 5 and self.pivot > 0:
                recent_high = self.current_df['high'].iloc[self.trigger_loc:loc + 1].max()
                if recent_high > self.pivot:
                    self.current_loc = loc
                    found = True
                    break

            # 条件2: 使用 evaluate_conditions 检测新的 VCP 信号
            r120, r250 = 80.0, 80.0
            if self.rps_matrix:
                date_str = pd.Timestamp(self.current_df.index[loc]).strftime(DATE_FMT)
                d_rps = self.rps_matrix.get(date_str)
                if d_rps:
                    r120 = float(d_rps.get("rps120", {}).get(self.current_code, 80) or 80)
                    r250 = float(d_rps.get("rps250", {}).get(self.current_code, 80) or 80)
                    if pd.isna(r120): r120 = 80.0
                    if pd.isna(r250): r250 = 80.0

            try:
                ok, _, new_m = self.vcp_engine.evaluate_conditions(
                    self.current_df, self.current_df.index[loc], r120, r250, None, rt_params)
                if ok:
                    self.current_loc = loc
                    self.current_signal_details = new_m
                    self.pivot = new_m.get('区间最高价', 0)
                    found = True
                    break
            except Exception:
                continue

        if found:
            self._set_state(STATE_OBSERVING, True)
            self._update_position_text()
            self._render_chart(show_full_future=False)
        else:
            self._set_status(f"在接下来的 {self.max_hold_days} 天内未再见有效信号，自动结算。")
            self._close_question_completely()

    def _action_abandon(self):
        """放弃：如果持仓中则先强平"""
        if self.entry_loc is not None:
            self._action_sell(force_end_question=True)
        else:
            self._set_status("已放弃此股票，结束推演。")
            self._close_question_completely()

    def _close_question_completely(self):
        """底层终结动作：进入上帝视角"""
        self._mark_current_completed()
        self._set_state(STATE_FINISHED, True)
        self._update_position_text()
        self._render_chart(show_full_future=True)

    def _action_rebuy(self):
        """清空本股票全部交易记录，回到触发日重新推演"""
        if self.state != STATE_FINISHED:
            return
        if self.current_df is None or self.current_code is None:
            return

        # 从 trades 中剔除该股票所有记录
        self.trades = [t for t in self.trades if t.code != self.current_code]
        self._rebuild_trade_table()
        self._update_summary()

        # 重置推演状态
        self.current_loc = self.trigger_loc
        self.exit_loc = None
        self.exit_price = None
        self.entry_loc = None
        self.entry_price = None
        self._set_status("已清空本股票记录并回到触发日，重新开始推演。")
        self._set_state(STATE_READY, True)
        self._update_position_text()
        self._render_chart(show_full_future=False)

    # ================================================================
    # 图表导航
    # ================================================================
    def _chart_nav(self, direction):
        if self.state != STATE_FINISHED:
            return
        if self.current_df is None or self.current_loc is None:
            return
        new_loc = self.current_loc + direction
        if 0 <= new_loc < len(self.current_df):
            self.current_loc = new_loc
            self._update_position_text()
            self._render_chart(show_full_future=False)

    def _chart_jump_trigger(self):
        if self.state != STATE_FINISHED:
            return
        if self.trigger_loc is not None:
            self.current_loc = self.trigger_loc
            self._update_position_text()
            self._render_chart(show_full_future=False)

    def _chart_jump_end(self):
        if self.state != STATE_FINISHED:
            return
        if self.current_df is not None:
            self.current_loc = len(self.current_df) - 1
            self._update_position_text()
            self._render_chart(show_full_future=False)

    # ================================================================
    # 辅助快捷键
    # ================================================================
    def _toggle_blind(self):
        self.chk_blind.setChecked(not self.chk_blind.isChecked())
        self._render_chart()
        self._set_status(f"盲盒模式已 {'开启' if self.chk_blind.isChecked() else '关闭'}")

    def _temp_reveal_code(self):
        if not self.chk_blind.isChecked():
            return
        if self.current_code:
            self._set_status(f"临时显示: {self.current_code} {self.current_name}（5秒后隐藏）")
            QTimer.singleShot(5000, lambda: self._set_status("盲盒模式运行中..."))

    def _export_trades(self):
        if not self.trades:
            QMessageBox.information(self, "无数据", "当前无交易记录可导出。")
            return
        df = pd.DataFrame([{
            "序号": i + 1, "代码": t.code, "名称": t.name, "触发日": t.trigger_date,
            "买入价": t.buy_price, "卖出价": t.sell_price, "持仓天数": t.hold_days,
            "收益率%": round(t.ret * 100, 2)
        } for i, t in enumerate(self.trades)])
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        default_path = f"训练战绩导出_{stamp}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出战绩", default_path, "Excel Files (*.xlsx)")
        if path:
            df.to_excel(path, index=False)
            QMessageBox.information(self, "导出成功", f"已保存至：{path}\n共 {len(self.trades)} 笔交易")

    # ================================================================
    # 持仓信息面板（含 RPS + 成交额）
    # ================================================================
    def _update_position_text(self):
        if self.current_df is None or self.current_loc is None:
            for lbl in [self.lbl_pos_date, self.lbl_pos_price, self.lbl_pos_turnover,
                        self.lbl_pos_rps, self.lbl_pos_cost, self.lbl_pos_hold]:
                lbl.setText("—")
                lbl.setStyleSheet("color: #4B5563; font-size: 12px;")
            self.lbl_pos_state.setText("空仓")
            self.lbl_pos_state.setStyleSheet("color: #6B7280; font-weight: bold; font-size: 12px;")
            self.lbl_state_dot.setStyleSheet("color: #6B7280; font-size: 10px;")
            self.lbl_pos_pnl.setText("—")
            self.lbl_pos_pnl.setStyleSheet("""
                font-size: 22px; font-weight: bold; color: #4B5563;
                padding: 4px 0; background-color: rgba(255,255,255,0.02); border-radius: 6px;
            """)
            return

        loc = self.current_loc
        row = self.current_df.iloc[loc]
        price = float(row["close"])
        date_str = self.current_df.index[loc].strftime("%Y-%m-%d")
        rps_date_str = self.current_df.index[loc].strftime(DATE_FMT)

        # RPS 查询
        r120, r250 = 0, 0
        if self.rps_matrix:
            d_rps = self.rps_matrix.get(rps_date_str, {})
            r120 = d_rps.get("rps120", {}).get(self.current_code, 0) or 0
            r250 = d_rps.get("rps250", {}).get(self.current_code, 0) or 0
            if pd.isna(r120): r120 = 0
            if pd.isna(r250): r250 = 0

        # 成交额
        turnover = 0
        try:
            turnover = float(row["volume"]) * float(row["close"]) * 100 / 10000
        except Exception:
            pass

        self.lbl_pos_date.setText(date_str if not self.chk_blind.isChecked() else "****-**-**")
        self.lbl_pos_price.setText(f"{price:.2f}")
        self.lbl_pos_price.setStyleSheet("color: #E5E7EB; font-size: 12px; font-weight: bold;")
        self.lbl_pos_turnover.setText(f"{turnover / 10000:.2f} 亿")

        # RPS 颜色编码
        rps_color = "#FBBF24" if (r120 >= 90 or r250 >= 90) else "#60A5FA" if (r120 >= 80 or r250 >= 80) else "#6B7280"
        self.lbl_pos_rps.setText(f"{r120:.0f} / {r250:.0f}")
        self.lbl_pos_rps.setStyleSheet(f"color: {rps_color}; font-size: 12px; font-weight: bold;")

        if self.state == STATE_FINISHED:
            self.lbl_pos_state.setText("上帝视角")
            self.lbl_pos_state.setStyleSheet("color: #8B92A5; font-weight: bold; font-size: 12px;")
            self.lbl_state_dot.setStyleSheet("color: #8B92A5; font-size: 10px;")
            self.lbl_pos_cost.setText("—")
            self.lbl_pos_pnl.setText("—")
            self.lbl_pos_pnl.setStyleSheet("""
                font-size: 22px; font-weight: bold; color: #4B5563;
                padding: 4px 0; background-color: rgba(255,255,255,0.02); border-radius: 6px;
            """)
            self.lbl_pos_hold.setText("—")
        elif self.entry_price is not None and self.entry_loc is not None:
            hold_days = max(0, loc - self.entry_loc)
            pnl = (price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0.0

            self.lbl_pos_state.setText("满仓持有中")
            self.lbl_pos_state.setStyleSheet("color: #EF4444; font-weight: bold; font-size: 12px;")
            self.lbl_state_dot.setStyleSheet("color: #EF4444; font-size: 10px;")
            self.lbl_pos_cost.setText(f"{self.entry_price:.2f}")
            self.lbl_pos_cost.setStyleSheet("color: #FBBF24; font-size: 12px; font-weight: bold;")

            pnl_color = '#EF4444' if pnl >= 0 else '#10B981'
            self.lbl_pos_pnl.setText(f"{pnl * 100:+.2f}%")
            self.lbl_pos_pnl.setStyleSheet(f"""
                font-size: 22px; font-weight: bold; color: {pnl_color};
                padding: 4px 0; background-color: rgba(255,255,255,0.02); border-radius: 6px;
            """)
            self.lbl_pos_hold.setText(f"{hold_days} 天")
            self.lbl_pos_hold.setStyleSheet("color: #C9CDD4; font-size: 12px;")
        else:
            self.lbl_pos_state.setText("空仓盯盘中")
            self.lbl_pos_state.setStyleSheet("color: #6B7280; font-weight: bold; font-size: 12px;")
            self.lbl_state_dot.setStyleSheet("color: #F59E0B; font-size: 10px;")
            self.lbl_pos_cost.setText("—")
            self.lbl_pos_pnl.setText("—")
            self.lbl_pos_pnl.setStyleSheet("""
                font-size: 22px; font-weight: bold; color: #4B5563;
                padding: 4px 0; background-color: rgba(255,255,255,0.02); border-radius: 6px;
            """)
            self.lbl_pos_hold.setText("—")

    # ================================================================
    # 图表渲染
    # ================================================================
    def _render_chart(self, show_full_future=False):
        blind = self.chk_blind.isChecked() and self.state != STATE_FINISHED
        self.chart.update_chart(
            full_df=self.current_df,
            current_loc=self.current_loc if self.current_loc is not None else 0,
            trigger_loc=self.trigger_loc,
            entry_loc=self.entry_loc,
            entry_price=self.entry_price,
            pivot=self.pivot,
            trades=self.trades,
            current_code=self.current_code,
            signal_details=self.current_signal_details,
            show_full_future=show_full_future,
            blind_mode=blind
        )

    # ================================================================
    # 战绩统计
    # ================================================================
    def _rebuild_trade_table(self):
        self.trade_table.setRowCount(0)
        for i, t in enumerate(self.trades):
            self.trade_table.insertRow(i)
            self.trade_table.setItem(i, 0, NumericTableWidgetItem(str(i + 1)))
            self.trade_table.setItem(i, 1, QTableWidgetItem(t.code))
            self.trade_table.setItem(i, 2, QTableWidgetItem(t.name))
            self.trade_table.setItem(i, 3, QTableWidgetItem(t.trigger_date))
            self.trade_table.setItem(i, 4, NumericTableWidgetItem(f"{t.buy_price:.2f}"))
            self.trade_table.setItem(i, 5, NumericTableWidgetItem(f"{t.sell_price:.2f}"))
            self.trade_table.setItem(i, 6, NumericTableWidgetItem(str(t.hold_days)))

            pnl_item = NumericTableWidgetItem(f"{t.ret * 100:+.2f}%")
            if t.ret >= 0:
                pnl_item.setForeground(QColor('#90EE90'))
            else:
                pnl_item.setForeground(QColor('#FF8080'))
            self.trade_table.setItem(i, 7, pnl_item)

        # 自动滚到底部
        if self.trade_table.rowCount() > 0:
            self.trade_table.scrollToBottom()

    def _update_summary(self):
        if not self.trades:
            self.lbl_summary.setText("实战复盘: 0 笔 | 客观胜率: 0.0% | 平均收益: 0.00% | 最大回撤: 0.00%")
            return

        rets = np.array([t.ret for t in self.trades], dtype=float)
        wins = int((rets > 0).sum())
        total = len(rets)
        win_rate = wins / total if total > 0 else 0.0
        avg_ret = float(rets.mean()) if total > 0 else 0.0

        # 复利资金曲线与真实最大回撤
        equity = np.cumprod(1.0 + rets)
        peak = np.maximum.accumulate(equity)
        drawdowns = (equity - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(drawdowns.min()) if len(drawdowns) else 0.0

        self.lbl_summary.setText(
            f"实战复盘: {total} 笔 | 客观胜率: {win_rate * 100:.1f}% | "
            f"平均收益: {avg_ret * 100:.2f}% | 最大回撤: {max_dd * 100:.2f}%"
        )

    # ================================================================
    # 右键菜单：撤销最后一笔交易
    # ================================================================
    def _trade_context_menu(self, pos):
        menu = QMenu(self)
        action = menu.addAction("删除最后一笔交易 (Undo)")
        action.triggered.connect(self._undo_last_trade)
        menu.exec(self.trade_table.mapToGlobal(pos))

    def _undo_last_trade(self):
        if not self.trades:
            return
        last_tr = self.trades.pop()
        self._rebuild_trade_table()
        self._update_summary()

        # 回滚状态机
        if last_tr.code == self.current_code and self.current_df is not None:
            self.current_loc = max(self.trigger_loc, last_tr.entry_loc - 1) \
                if last_tr.entry_loc >= 1 else self.trigger_loc
            self.entry_loc = None
            self.entry_price = None
            self.exit_loc = None
            self.exit_price = None
            self._set_state(STATE_OBSERVING, True)
            self._update_position_text()
            self._render_chart(show_full_future=False)
