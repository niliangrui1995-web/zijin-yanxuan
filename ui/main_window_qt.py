import os
import datetime
from vcp.constants import SPECIAL_LATEST_DATA, SPECIAL_POOL_DATA_CACHE, APP_VERSION
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QPushButton, QLabel, QLineEdit, QComboBox, QMenu,
    QTextEdit, QProgressBar, QSpinBox, QDoubleSpinBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSettings
from PyQt6.QtGui import QColor, QIcon, QShortcut, QKeySequence

# 核心引擎与数据层
from vcp.data_provider import TdxDataProvider
from vcp.engine import VCPEngine
from vcp.ai_service import KimiAIService
from ui.kline_window_qt import KLineChartWindow
from vcp_simulator.sim_tab import SimulatorTab

from ui.components import NumericTableWidgetItem, CustomTitleBar, AnimatedCard, PulsingDot, GlassPanel, AnimatedHoverButton
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager

log = get_logger(__name__)


class MainWindowQT(QMainWindow):
    """紫金研选主窗口 — 纯外壳控制器（Phase 2 重构后）"""
    _sig_f5_done = pyqtSignal(int, float)
    _sig_ui_call = pyqtSignal(object)

    def _merge_and_wrap_ai_diag(self, text):
        """将AI诊断文本截断为表格可显示的摘要"""
        if not text:
            return ''
        # 取第一行或前80字作为摘要
        first_line = text.split('\n')[0].strip()
        return first_line[:80] if len(first_line) > 80 else first_line

    @pyqtSlot(object)
    def _run_ui_callback(self, callback):
        try:
            callback()
        except Exception as e:
            print(f"[UI回调] 异常: {e}")

    def _call_in_ui(self, callback):
        self._sig_ui_call.emit(callback)

    def __init__(self, splash=None):
        super().__init__()
        self._splash = splash
        self.setWindowTitle('紫金研选量化终端')
        self.resize(1600, 900)
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bull_icon.ico")))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        # F5 预计算完成 -> 更新 UI
        self._sig_f5_done.connect(self._on_f5_done)
        self._sig_ui_call.connect(self._run_ui_callback)
        self._settings = QSettings("VCPHunter", "MainWindowQT")
        
        self._splash_update(60, "正在构建主界面模块...")
        self.data_provider = TdxDataProvider(offline=True)
        self.data_provider.code2name = self.data_provider._get_codes_from_vipdoc()
        self.engine = VCPEngine()
        self.worker = None
        self._current_results = []
        self._ai_diag_results = {}
        self._kimi_service = KimiAIService()
        self._cache_date = None

        # 全局样式（从 ui/styles/global_qss.py 集中管理）
        from ui.styles.global_qss import GLOBAL_QSS
        self.setStyleSheet(GLOBAL_QSS)



        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.custom_title_bar = CustomTitleBar(self)
        main_layout.addWidget(self.custom_title_bar)
        
        h_split_widget = QWidget()
        h_split_layout = QHBoxLayout(h_split_widget)
        h_split_layout.setContentsMargins(0, 0, 0, 0)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        h_split_layout.addWidget(self.splitter)
        
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 20, 16, 16)
        left_layout.setSpacing(12)
        
        card_brand = AnimatedCard(delay=0)
        cb_layout = QVBoxLayout(card_brand)
        cb_layout.setContentsMargins(16, 16, 16, 16)
        cb_layout.setSpacing(6)
        
        lbl_brand = QLabel('紫金研选')
        lbl_brand.setStyleSheet("font-size: 20px; font-weight: bold; color: #93C5FD; letter-spacing: 3px;")
        cb_layout.addWidget(lbl_brand)
        
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        
        self.pulsing_dot = PulsingDot(color="#EF4444")
        status_layout.addWidget(self.pulsing_dot)

        self.btn_datasource = QPushButton("离线模式")
        self.btn_datasource.setObjectName("statusBadge")
        self.btn_datasource.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datasource.clicked.connect(self._toggle_network)
        
        self.lbl_code_count = QLabel("---")
        self.lbl_code_count.setProperty("class", "subText")
        
        status_layout.addWidget(self.btn_datasource)
        status_layout.addStretch()
        status_layout.addWidget(self.lbl_code_count)
        
        cb_layout.addLayout(status_layout)
        left_layout.addWidget(card_brand)
        
        self._init_left_panel_controls(left_layout)
        
        self.splitter.addWidget(left_panel)
        
        self._splash_update(75, "组件注册中...")
        self._init_right_panel()
        
        main_layout.addWidget(h_split_widget, 1)
        
        status_bar = QWidget()
        status_bar.setFixedHeight(32)
        status_bar.setStyleSheet("""
            background-color: #0A0C10;
            border-top: 1px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(59, 130, 246, 0.3),
                stop:0.5 rgba(147, 197, 253, 0.12),
                stop:1 rgba(59, 130, 246, 0.3));
            padding: 0px 16px;
        """)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(12, 0, 12, 0)
        status_layout.setSpacing(12)
        
        self.status_dot = PulsingDot(color="#10B981")
        status_layout.addWidget(self.status_dot)

        self.lbl_status = QLabel("---")
        self.lbl_status.setStyleSheet("color: #6B7280; font-size: 12px; font-family: 'Consolas', 'Courier New', monospace;")
        status_layout.addWidget(self.lbl_status)
        
        status_layout.addStretch()

        self.lbl_clock = QLabel()
        self.lbl_clock.setStyleSheet("color: #6B7280; font-size: 12px; font-family: 'Consolas', monospace;")
        status_layout.addWidget(self.lbl_clock)
        
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(lambda: self.lbl_clock.setText(datetime.datetime.now().strftime("%H:%M:%S")))
        self._clock_timer.start(1000)
        
        self.lbl_version = QLabel(f"v{APP_VERSION}")
        self.lbl_version.setStyleSheet("color: #3A3F4D; font-size: 11px;")
        
        status_layout.addWidget(self.lbl_version)
        
        main_layout.addWidget(status_bar, 0)
        
        self._restore_ui_state()

        self._splash_update(90, "正在加载数据...")
        QTimer.singleShot(100, self._deferred_data_load)
        QTimer.singleShot(2000, self._smart_startup)

    def _toggle_network(self):
        """"""
        import threading
        if self.data_provider._offline:
            self.btn_datasource.setText("正在切换...")
            self.btn_datasource.setEnabled(False)

            def _go_online():
                try:
                    self.data_provider.set_online_mode(True)
                    self._call_in_ui(lambda: self._update_network_ui(True))
                except Exception as e:
                    log.error(f"[网络] 切换联网失败: {e}")
                    self._call_in_ui(lambda: self._update_network_ui(False))

            task_manager.run_in_background(_go_online, task_id="go_online")
        else:
            self.data_provider.set_online_mode(False)
            self._update_network_ui(False)

    def _update_network_ui(self, online: bool):
        """"""
        self.btn_datasource.setEnabled(True)
        if online:
            self.btn_datasource.setText("联网模式")
            self.btn_datasource.setStyleSheet(
                self.btn_datasource.styleSheet().replace("#EF4444", "#22C55E")
                if "#EF4444" in self.btn_datasource.styleSheet()
                else self.btn_datasource.styleSheet()
            )
            self.pulsing_dot.set_color("#22C55E")
        else:
            self.btn_datasource.setText("离线模式")
            self.pulsing_dot.set_color("#EF4444")

    def _splash_update(self, value: int, status: str = ""):
        """update progress"""
        if self._splash:
            self._splash.set_progress(value, status)

    def _init_left_panel_controls(self, layout):
        layout.setSpacing(12)

        card_scan = AnimatedCard(delay=100)
        scan_layout = QVBoxLayout(card_scan)
        scan_layout.setContentsMargins(16, 16, 16, 16)
        scan_layout.setSpacing(12)

        self.btn_scan_title = QPushButton("➤ 扫描设置")
        self.btn_scan_title.setStyleSheet("""
            QPushButton { color: #6B7280; font-weight: bold; font-size: 15px; border: none; text-align: left; }
            QPushButton:hover { color: #C9CDD4; }
        """)
        self.btn_scan_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_collapsed = False
        def toggle_scan():
            self._scan_collapsed = not self._scan_collapsed
            self.btn_scan_title.setText("▶ 扫描参数" if self._scan_collapsed else "▼ 扫描参数")
            card_scan.toggle_collapse(self._scan_collapsed, expanded_height=430)
        self.btn_scan_title.clicked.connect(toggle_scan)
        scan_layout.addWidget(self.btn_scan_title)

        self.ent_start = QLineEdit()
        self.ent_start.setPlaceholderText("YYYYMMDD")
        self.ent_end = QLineEdit()
        self.ent_end.setPlaceholderText("YYYYMMDD")
        self._set_default_dates()

        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("起始 :"))
        date_layout.addWidget(self.ent_start)
        date_layout.addWidget(QLabel("结束 :"))
        date_layout.addWidget(self.ent_end)
        scan_layout.addLayout(date_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)
        shortcuts = [
            ("YTD", lambda: self._set_date_range(0, ytd=True)),
            ("近1月", lambda: self._set_date_range(30)),
            ("近3月", lambda: self._set_date_range(90)),
            ("1Y", lambda: self._set_date_range(365))
        ]
        
        self.segment_btns = []
        for name, func in shortcuts:
            btn = QPushButton(name)
            btn.setProperty("class", "segmentControl")
            self.segment_btns.append(btn)
            
            def on_click(checked=False, b=btn, f=func):
                for other_b in self.segment_btns:
                    other_b.setProperty("state", "")
                    other_b.style().unpolish(other_b)
                    other_b.style().polish(other_b)
                b.setProperty("state", "active")
                b.style().unpolish(b)
                b.style().polish(b)
                f()
                
            btn.clicked.connect(on_click)
            btn_layout.addWidget(btn)
            
        scan_layout.addLayout(btn_layout)
        # 默认选中第一个按钮
        if self.segment_btns:
            self.segment_btns[0].setProperty("state", "active")
        layout.addWidget(card_scan)

        card_ops = AnimatedCard(delay=200)
        ops_layout = QVBoxLayout(card_ops)
        ops_layout.setContentsMargins(16, 16, 16, 16)
        ops_layout.setSpacing(12)
        
        lbl_ops_title = QLabel("功能操作面板")
        lbl_ops_title.setStyleSheet("color: #6B7280; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;")
        ops_layout.addWidget(lbl_ops_title)

        self.btn_scan = AnimatedHoverButton("执行全盘VCP扫描")
        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_scan.set_active(True)
        ops_layout.addWidget(self.btn_scan)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._start_scan)

        self.btn_f5 = AnimatedHoverButton("运行F5预计算")
        self.btn_f5.clicked.connect(self._action_refresh)
        ops_layout.addWidget(self.btn_f5)
        QShortcut(QKeySequence("F5"), self, activated=self._action_refresh)

        self.btn_diag = AnimatedHoverButton("AI 深度诊断")
        self.btn_diag.setEnabled(False)
        self.btn_diag.setToolTip("AI 个股深度诊断")
        ops_layout.addWidget(self.btn_diag)

        self.btn_rt_sidebar = AnimatedHoverButton("⚡ 启动盘中监控")
        self.btn_rt_sidebar.clicked.connect(lambda: hasattr(self, 'tab_rt') and self.tab_rt._toggle_rt_monitor())
        ops_layout.addWidget(self.btn_rt_sidebar)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        ops_layout.addWidget(self.progress_bar)

        self.btn_cancel = QPushButton("中止扫描")
        self.btn_cancel.setProperty("class", "dangerGhost")
        self.btn_cancel.clicked.connect(self._cancel_scan)
        ops_layout.addWidget(self.btn_cancel)

        layout.addWidget(card_ops)
        layout.addStretch()

        self.spn_scan_rps = QSpinBox()
        self.spn_scan_rps.setRange(0, 100)
        self.spn_scan_rps.setValue(80)
        self.spn_scan_amp = QDoubleSpinBox()
        self.spn_scan_amp.setRange(0.10, 1.00)
        self.spn_scan_amp.setSingleStep(0.05)
        self.spn_scan_amp.setValue(0.45)
        self.spn_scan_ma_bind = QDoubleSpinBox()
        self.spn_scan_ma_bind.setRange(0.01, 0.30)
        self.spn_scan_ma_bind.setSingleStep(0.01)
        self.spn_scan_ma_bind.setValue(0.05)
        self.spn_scan_amount = QDoubleSpinBox()
        self.spn_scan_amount.setRange(0, 50)
        self.spn_scan_amount.setSingleStep(0.5)
        self.spn_scan_amount.setValue(0.8)
        self.spn_scan_high250 = QDoubleSpinBox()
        self.spn_scan_high250.setRange(0.05, 0.50)
        self.spn_scan_high250.setSingleStep(0.05)
        self.spn_scan_high250.setValue(0.10)

        self.cmb_rt_interval = QComboBox()
        self.cmb_rt_interval.addItems(["30s", "1m", "3m", "5m"])
        self.cmb_rt_interval.setCurrentIndex(0)
        self.spn_rt_rps = QSpinBox()
        self.spn_rt_rps.setRange(0, 100)
        self.spn_rt_rps.setValue(80)

    def _init_right_panel(self):
        self.right_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.tabs_wrapper = GlassPanel(self, radius=10, alpha=0.95)
        tabs_layout = QVBoxLayout(self.tabs_wrapper)
        tabs_layout.setContentsMargins(6, 6, 6, 6)
        
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        tabs_layout.addWidget(self.tabs)
        
        from ui.tabs.scan_tab import ScanTab
        self.tab_scan = ScanTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_scan, "VCP扫描")
        self.table_scan = self.tab_scan.table_scan
        self._scan_stretch_cols = {11}

        # --- 右侧 AI 诊断面板(独立组件) ---
        from ui.panels.ai_diag_panel import AIDiagPanel
        self.panel_ai = AIDiagPanel(self.data_provider, self._kimi_service, self)
        self.right_splitter.addWidget(self.panel_ai)
        self.right_splitter.setSizes([1000, 0]) # 默认隐藏AI面板
        
        from ui.tabs.rt_monitor_tab import RtMonitorTab
        self.tab_rt = RtMonitorTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_rt, "盘中监控")
        
        self.table_rt = self.tab_rt.table_rt
        self.btn_rt_start = self.tab_rt.btn_rt_start
        self.lbl_rt_info = self.tab_rt.lbl_rt_info
        self.cmb_rt_interval = self.tab_rt.cmb_rt_interval
        self.spn_rt_rps = self.tab_rt.spn_rt_rps

        # Tab 3: 关注池（独立组件）
        from ui.tabs.watchlist_tab import WatchlistTab
        self.tab_watchlist = WatchlistTab(self.data_provider, self.panel_ai, self)
        self.tabs.addTab(self.tab_watchlist, '关注池')
        self.table_sp = self.tab_watchlist.table_sp  # 向下兼容引用

        # Tab 4: 北美战报（独立组件）
        from ui.tabs.na_daily_tab import NADailyTab
        self.tab_na_daily = NADailyTab(self.data_provider, self)
        self.tabs.addTab(self.tab_na_daily, "美股日报")
        self.na_daily_table = self.tab_na_daily.na_daily_table  # 向下兼容

        # Tab 5: AI产业链跟踪（独立组件）
        from ui.tabs.ai_tracker_tab import AITrackerTab
        self.tab_ai_tracker = AITrackerTab(self.data_provider, self)
        self.tabs.addTab(self.tab_ai_tracker, "AI算力链")
        self.ai_tracker_table = self.tab_ai_tracker.ai_tracker_table  # 向下兼容

        self.tab_simulator = SimulatorTab(self.data_provider, self.engine)
        self.tab_simulator.setStyleSheet("background-color: transparent;")
        self.tabs.addTab(self.tab_simulator, "回测模拟")

        from ui.tabs.log_tab import LogTab
        self.tab_log = LogTab(self)
        self.tabs.addTab(self.tab_log, "系统日志")
        
        event_bus.sig_data_updated.connect(self._on_global_data_updated)
        event_bus.sig_task_progress.connect(self._on_task_progress)
        event_bus.sig_show_kline.connect(self._on_show_kline)
        event_bus.sig_open_ai_diag.connect(self._on_open_ai_diag)

        self.right_splitter.addWidget(self.tabs_wrapper)
        
        self.ai_panel = QFrame()
        self.ai_panel.setObjectName("moduleCard")
        self.ai_panel.hide() # 默认隐藏
        ai_layout = QVBoxLayout(self.ai_panel)
        ai_layout.setContentsMargins(12, 12, 12, 12)
        ai_layout.setSpacing(8)
        
        ai_header = QHBoxLayout()
        ai_title = QLabel("AI 诊断面板")
        ai_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #E5E7EB;")
        btn_close_ai = QPushButton("✕")
        btn_close_ai.setObjectName("iconButton")
        btn_close_ai.setFixedSize(24, 24)
        btn_close_ai.clicked.connect(self.ai_panel.hide)
        ai_header.addWidget(ai_title)
        ai_header.addStretch()
        ai_header.addWidget(btn_close_ai)
        ai_layout.addLayout(ai_header)
        
        self.ai_content = QTextEdit()
        self.ai_content.setReadOnly(True)
        self.ai_content.setStyleSheet("""
            background-color: #0A0C10;
            border: 1px solid #252A36;
            border-radius: 8px;
            padding: 12px;
            color: #C9CDD4;
            font-family: 'Consolas', 'Courier New', monospace;
        """)
        self.ai_content.setPlainText("AI模型就绪，点击个股获取诊断...")
        ai_layout.addWidget(self.ai_content)
        
        self.right_splitter.addWidget(self.ai_panel)
        self.right_splitter.setSizes([1000, 0])

        self.splitter.addWidget(self.right_splitter)
        # 左侧与右侧比例 1:4
        self.splitter.setSizes([300, 1300])

    def _renumber_column(self, table, col=0):
        for r in range(table.rowCount()):
            item = table.item(r, col)
            if item:
                item.setText(str(r + 1))
            else:
                new_item = NumericTableWidgetItem(str(r + 1))
                new_item.setForeground(QColor("#F5F5F7"))
                new_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, col, new_item)

    def _filter_table(self, table, text, code_col=0, name_col=2):
        import pypinyin
        text = text.strip().lower()
        for r in range(table.rowCount()):
            if not text:
                table.setRowHidden(r, False)
                continue
            code_item = table.item(r, code_col)
            name_item = table.item(r, name_col)
            code_text = code_item.text().lower() if code_item else ""
            name_text = name_item.text().lower() if name_item else ""
            
            try:
                py_initials = "".join(pypinyin.lazy_pinyin(name_text, style=pypinyin.Style.FIRST_LETTER)).lower()
            except Exception:
                py_initials = ""
                
            match = text in code_text or text in name_text or text in py_initials
            table.setRowHidden(r, not match)


    def _on_table_double_click(self, item):
        # Determine which table triggered the click
        sender_table = self.sender()
        if not sender_table:
            # Fallback if somehow not via trigger (like hotkeys intercept logic on table_scan)
            sender_table = self.table_scan 

        row = item.row()
        if sender_table in (self.table_rt, self.table_sp):
            code_item = sender_table.item(row, 0)
            name_item = sender_table.item(row, 2)
        elif hasattr(self, 'na_daily_table') and sender_table == self.na_daily_table:
            code_item = sender_table.item(row, 2)
            name_item = sender_table.item(row, 4)
        elif hasattr(self, 'ai_tracker_table') and sender_table == self.ai_tracker_table:
            code_item = sender_table.item(row, 1)   # AI跟踪：序号(0) 代码(1)
            name_item = sender_table.item(row, 2)
        else:
            code_item = sender_table.item(row, 1)
            name_item = sender_table.item(row, 3)
        
        if not code_item or not name_item:
            return
            
        code = code_item.text()
        name = name_item.text()
        
        vcp_data = None
        current_list = []
        if sender_table == self.table_scan:
            seq_item = sender_table.item(row, 0)
            if seq_item:
                vcp_data = seq_item.data(Qt.ItemDataRole.UserRole)
            if hasattr(self, '_current_results'):
                current_list = self._current_results

        if not current_list and sender_table.rowCount() > 0:
            if hasattr(self, 'na_daily_table') and sender_table == self.na_daily_table:
                code_col, name_col = 2, 4
            elif hasattr(self, 'ai_tracker_table') and sender_table == self.ai_tracker_table:
                code_col, name_col = 1, 2
            elif sender_table == self.table_scan:
                code_col, name_col = 1, 3
            else:
                code_col, name_col = 0, 2
            for r in range(sender_table.rowCount()):
                c_item = sender_table.item(r, code_col)
                n_item = sender_table.item(r, name_col)
                if c_item and n_item:
                    current_list.append({'代码': c_item.text(), '名称': n_item.text()})
        elif sender_table == self.table_rt:
            if hasattr(self, 'rt_worker') and hasattr(self.rt_worker, '_signal_details') and code in self.rt_worker._signal_details:
                vcp_data = self.rt_worker._signal_details[code]
                
            # fallback: build a basic block from table visual row
            if not vcp_data:
                vcp_data = {
                    'code': code,
                    'name': name
                }
            
            # Use original pool if missing structural metadata
            if hasattr(self, 'rt_worker') and hasattr(self.rt_worker, '_ready_pool') and code in self.rt_worker._ready_pool:
                pool_entry = self.rt_worker._ready_pool[code]
                if 'meta' in pool_entry and isinstance(pool_entry['meta'], dict):
                    vcp_data.update(pool_entry['meta'])

        elif sender_table == self.table_sp:
            import os, pickle
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
            pool_pkl = SPECIAL_POOL_DATA_CACHE
            if os.path.exists(pool_pkl):
                try:
                    with open(pool_pkl, 'rb') as f:
                        raw = pickle.load(f)
                    old_pool = raw.get('data', {}) if isinstance(raw, dict) else {}
                    if code in old_pool:
                        vcp_data = old_pool[code]
                except Exception as e: print(f"[关注池] 读取失败: {e}")
            
            if not vcp_data:
                vcp_data = {
                    'code': code,
                    'name': name
                }
        
        if not hasattr(self, '_charts'):
            self._charts = []

        # 清理已关闭的窗口
        self._charts = [c for c in self._charts if c.isVisible()]

        # 限制K线窗口数量，超出时关闭最旧的
        MAX_CHART_WINDOWS = 3
        while len(self._charts) >= MAX_CHART_WINDOWS:
            oldest = self._charts.pop(0)
            oldest.close()

        chart = KLineChartWindow(
            main_window=self, 
            code=code, 
            name=name, 
            data_provider=self.data_provider, 
            vcp_data=vcp_data,
            code_list=current_list,
            current_idx=row
        )
        chart.show()
        self._charts.append(chart)

    def _show_context_menu(self, pos):
        """主窗口统一右键菜单（扫描/盘中监控/北美/AI跟踪表格）"""
        try:
            sender_table = self.sender()
            if not sender_table:
                sender_table = self.table_scan

            item = sender_table.itemAt(pos)
            if not item:
                return

            row = item.row()
            if sender_table == self.table_scan:
                code_item = sender_table.item(row, 1)
                name_item = sender_table.item(row, 3)
            elif hasattr(self, 'na_daily_table') and sender_table == self.na_daily_table:
                code_item = sender_table.item(row, 2)
                name_item = sender_table.item(row, 4)
            elif hasattr(self, 'ai_tracker_table') and sender_table == self.ai_tracker_table:
                code_item = sender_table.item(row, 1)
                name_item = sender_table.item(row, 2)
            else:
                code_item = sender_table.item(row, 0)
                name_item = sender_table.item(row, 2)

            if not code_item or not name_item:
                return

            code = code_item.text()
            name = name_item.text()
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { background-color: #151820; color: #C9CDD4; border: 1px solid #252A36; border-radius: 8px; padding: 4px; }
                QMenu::item { padding: 6px 24px; }
                QMenu::item:selected { background-color: rgba(59, 130, 246, 0.2); color: white; }
                QMenu::separator { height: 1px; background: #252A36; margin: 4px 8px; }
            """)

            act_chart = menu.addAction("📈 查看K线图")
            act_copy = menu.addAction("📋 复制代码")
            menu.addSeparator()

            import json
            fav_codes = []
            if os.path.exists(SPECIAL_LATEST_DATA):
                try:
                    with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                        fav_codes = list(json.load(f).keys())
                except Exception as e:
                    print(f"[关注池] 读取失败: {e}")

            is_fav = code in fav_codes
            act_special = menu.addAction("⭐ 移出关注池" if is_fav else "⭐ 加入关注池")
            menu.addSeparator()
            act_tdx = menu.addAction("🖥️ 跳转通达信")
            menu.addSeparator()
            act_ai = menu.addAction("🤖 AI深度诊断")
            act_local_diag = menu.addAction("🧪 本地技术诊断")
            menu.addSeparator()
            act_export = menu.addAction("📤 导出当前表")

            action = menu.exec(sender_table.viewport().mapToGlobal(pos))

            if action == act_chart:
                self._on_table_double_click(item)
            elif action == act_copy:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(code)
                self.lbl_status.setText(f"已复制: {code}")
            elif action == act_special:
                vcp_data = None
                if sender_table == self.table_scan:
                    seq_item = sender_table.item(row, 0)
                    if seq_item:
                        vcp_data = seq_item.data(Qt.ItemDataRole.UserRole)
                elif sender_table == self.table_rt and hasattr(self, 'rt_worker') and hasattr(self.rt_worker, '_signal_details'):
                    if code in self.rt_worker._signal_details:
                        vcp_data = self.rt_worker._signal_details[code]
                    elif hasattr(self.rt_worker, '_ready_pool') and code in self.rt_worker._ready_pool:
                        pool_entry = self.rt_worker._ready_pool[code]
                        if 'meta' in pool_entry and isinstance(pool_entry['meta'], dict):
                            vcp_data = pool_entry['meta']
                self._toggle_special(code, name, is_fav, vcp_data=vcp_data)
            elif action == act_tdx:
                self._launch_tdx(code)
            elif action == act_ai:
                if hasattr(self, 'panel_ai'):
                    self.panel_ai.open_ai_diag(preset_code=code, auto_start='ai')
            elif action == act_local_diag:
                if hasattr(self, 'panel_ai'):
                    self.panel_ai.open_ai_diag(preset_code=code, auto_start='local')
            elif action == act_export:
                self._export_current_tab()
        except Exception as e:
            self.lbl_status.setText(f"右键菜单异常: {str(e)}")
            print(f"[右键菜单] 异常: {e}")

    def _launch_tdx(self, code):
        """Launch or focus TDX and jump to the given stock code."""
        import os, threading
        tdx_path = self.data_provider.tdx_vipdoc.replace("vipdoc", "tdxw.exe") if self.data_provider.tdx_vipdoc else ""
        if not os.path.exists(tdx_path):
            self.lbl_status.setText("未找到通达信路径")
            return
        self.lbl_status.setText("正在跳转通达信...")
        
        def _worker():
            import subprocess, time, ctypes, ctypes.wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            try:
                CREATE_NO_WINDOW = 0x08000000
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq tdxw.exe'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=CREATE_NO_WINDOW
                )
                already_running = 'tdxw.exe' in result.stdout.lower()
                
                if not already_running:
                    subprocess.Popen([tdx_path], cwd=os.path.dirname(tdx_path))
                    time.sleep(3.0)
                
                EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                tdx_hwnd = ctypes.wintypes.HWND(0)
                
                def callback(hwnd, lParam):
                    nonlocal tdx_hwnd
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        # Match TDX window title
                        if '华泰' in buf.value or '通达信' in buf.value or '交易' in buf.value or '行情' in buf.value:
                            tdx_hwnd = hwnd
                            return False
                    return True
                
                user32.EnumWindows(EnumWindowsProc(callback), 0)
                
                if not tdx_hwnd:
                    pass
                    return
                
                fore_thread = user32.GetWindowThreadProcessId(
                    user32.GetForegroundWindow(), None
                )
                cur_thread = kernel32.GetCurrentThreadId()
                
                if fore_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, fore_thread, True)
                
                if user32.IsIconic(tdx_hwnd):
                    user32.ShowWindow(tdx_hwnd, 9)  # SW_RESTORE
                
                user32.BringWindowToTop(tdx_hwnd)
                user32.SetForegroundWindow(tdx_hwnd)
                
                if fore_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, fore_thread, False)
                
                time.sleep(0.5)
                
                import pyautogui
                pyautogui.typewrite(code, interval=0.02)
                time.sleep(0.1)
                pyautogui.press('enter')
                log.info(f"[通达信] 已跳转: {code}")
                
            except ImportError:
                log.error("[通达信] 需要安装pyautogui: pip install pyautogui")
            except Exception as e:
                log.error(f"[通达信] 跳转异常: {e}")
        
        task_manager.run_in_background(_worker, task_id="launch_tdx")

    def _save_ui_state(self):
        """Persist splitter sizes, window geometry, and table widths."""
        import json
        s = self._settings
        s.setValue("geometry", self.saveGeometry())
        # splitter 比例
        s.setValue("splitter_sizes", self.splitter.sizes())
        
        for name, table, stretch_cols in [
            ("scan", self.table_scan, getattr(self, '_scan_stretch_cols', set())),
            ("rt", self.table_rt, getattr(self, '_rt_stretch_cols', set())),
            ("sp", self.table_sp, getattr(self, '_sp_stretch_cols', set())),
        ]:
            widths = []
            for c in range(table.columnCount()):
                widths.append(table.columnWidth(c) if c not in stretch_cols else -1)
            # 使用 JSON 序列化存储 避免 PyQt6 QVariant 类型转换问题
            s.setValue(f"col_widths_{name}", json.dumps(widths))
        s.sync()

    def _restore_ui_state(self):
        """Restore splitter sizes, window geometry, and table widths."""
        import json
        s = self._settings
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        # splitter 比例
        sizes = s.value("splitter_sizes")
        if sizes:
            try:
                self.splitter.setSizes([int(x) for x in sizes])
            except Exception:
                pass
        
        for name, table, stretch_cols in [
            ("scan", self.table_scan, getattr(self, '_scan_stretch_cols', set())),
            ("rt", self.table_rt, getattr(self, '_rt_stretch_cols', set())),
            ("sp", self.table_sp, getattr(self, '_sp_stretch_cols', set())),
        ]:
            raw_widths = s.value(f"col_widths_{name}")
            widths = None
            if raw_widths:
                if isinstance(raw_widths, str):
                    try:
                        parsed = json.loads(raw_widths)
                        if isinstance(parsed, list):
                            widths = parsed
                    except Exception:
                        pass
                
                if widths is None and isinstance(raw_widths, list):
                    widths = raw_widths
            
            if widths:
                try:
                    for c, w in enumerate(widths):
                        try:
                            w = int(float(w))
                        except (ValueError, TypeError):
                            continue
                        
                        if w > 0 and c not in stretch_cols:
                            if c < table.columnCount():
                                table.setColumnWidth(c, w)
                except Exception as e:
                    print(f"[UI] 恢复列宽异常: {e}")
    def _on_f5_done(self, count, elapsed):
        """Handle the completion signal from the F5 precompute workflow."""
        import gc
        gc.collect()  # 大型计算完成后主动回收内存
        self.btn_scan.setEnabled(True)
        if count > 0:
            self.lbl_status.setText(f"F5预计算完成: {count}只 | 耗时{elapsed:.1f}s")
            self.lbl_code_count.setText(f"标的池: {count} 只")
        else:
            self.lbl_status.setText("F5预计算完成: 无新增数据")

    def closeEvent(self, event):
        """应用关闭：广播信号让各组件自行保存，然后清理资源"""
        try:
            self._save_ui_state()
        except Exception as e:
            print(f"[关闭] 保存UI状态异常: {e}")

        # 广播关闭信号，各 Tab 组件自行保存缓存
        try:
            event_bus.sig_app_closing.emit()
        except Exception as e:
            print(f"[关闭] 广播关闭信号异常: {e}")

        if hasattr(self, 'rt_worker') and self.rt_worker.isRunning():
            self.rt_worker.stop()
            self.rt_worker.wait(2000)

        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)

        if hasattr(self, '_auto_rt_timer'):
            self._auto_rt_timer.stop()

        super().closeEvent(event)

    # =======================================================================
    # =======================================================================
    def _set_default_dates(self):
        import datetime
        today = datetime.date.today().strftime('%Y%m%d')
        self.ent_start.setText(today)
        self.ent_end.setText(today)

    def _set_date_range(self, days, ytd=False):
        import datetime
        today = datetime.date.today()
        ed = today.strftime('%Y%m%d')
        if ytd:
            sd = today.strftime('%Y0101')
        else:
            sd = (today - datetime.timedelta(days=days)).strftime('%Y%m%d')
        self.ent_start.setText(sd)
        self.ent_end.setText(ed)

    # ================================================================
    # CacheMixin 内联方法（F5预计算 + 延迟加载 + 智能启动）
    # ================================================================
    def _action_refresh(self):
        """F5 盘后预计算：重读vipdoc -> 重算指标 -> RPS矩阵 -> 保存缓存"""
        import pickle
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(self, "盘后一键预计算",
            "此操作将执行完整的盘后数据重建流程：\n\n"
            "① 从通达信本地日线(vipdoc)重新读取数据\n"
            "② 重算全市场技术指标(MA/MACD等)\n"
            "③ 预计算全市场RPS排名(120日/250日)\n"
            "④ 保存缓存供次日盘中监控使用\n\n"
            "请确保已在通达信中完成【盘后数据下载】.\n是否执行?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.lbl_status.setText("F5 盘后预计算进行中...")
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._f5_cancelled = False

        import pandas as pd

        def run_f5():
            import time as _time
            import traceback as _tb
            cache_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'data', 'Cache'
            )
            os.makedirs(cache_dir, exist_ok=True)
            total_start = _time.time()

            try:
                print("\n" + "=" * 60)
                print("[F5] 盘后一键预计算 -- 开始")
                print("=" * 60)

                # 阶段0: 重新解析除权除息数据
                print("[F5] 阶段0: 重新解析通达信 gbbq 除权除息数据...")
                try:
                    self.data_provider._load_local_gbbq(force=True)
                except Exception as e:
                    print(f"[F5] gbbq 解析异常(不影响后续): {e}")

                # 阶段1: 重读本地日线
                print("[F5] 阶段1/3: 清空缓存,开始从 vipdoc 重读...")
                try:
                    self.data_provider.cache_data = {}
                    was_online = self.data_provider.is_online()
                    self.data_provider.set_online_mode(False)
                    try:
                        codes_dict = self.data_provider._get_codes_from_vipdoc()
                        print(f"[F5] 阶段1/3: 从 vipdoc 扫描到 {len(codes_dict)} 只标的")

                        def _progress(done, total, eta):
                            if total > 0 and done % 500 == 0:
                                print(f"[F5] 阶段1/3: 重读本地数据 {done}/{total}")

                        self.data_provider.sync_market_data(
                            codes_dict, force_refresh=True, progress_callback=_progress
                        )
                        self.data_provider.code2name = codes_dict
                    finally:
                        if was_online:
                            self.data_provider.set_online_mode(True)
                    count = len(self.data_provider.cache_data)
                    print(f"[F5] 阶段1/3 完成 -- 共加载 {count} 只标的")
                except Exception as e:
                    print(f"[F5] ❌ 阶段1 重读本地数据异常: {e}")
                    _tb.print_exc()
                    return

                if getattr(self, '_f5_cancelled', False):
                    print("[F5] ⏹ 用户取消")
                    return

                # 阶段2: 预计算 RPS 矩阵
                print("[F5] 阶段2/3: 预计算 RPS 矩阵...")
                try:
                    all_data = {
                        c: df for c, df in self.data_provider.cache_data.items()
                        if df is not None and len(df) >= 60
                    }
                    print(f"[F5] 阶段2/3: 有效标的 {len(all_data)} 只(>=60根K线)")
                    today_str = datetime.date.today().strftime('%Y%m%d')
                    rps_matrix = self.engine.build_rps_matrix(all_data, today_str, today_str)

                    if rps_matrix:
                        d_str = list(rps_matrix.keys())[-1]
                        d_rps = rps_matrix[d_str]
                        rps120 = pd.Series(d_rps.get('rps120', {}))
                        rps250 = pd.Series(d_rps.get('rps250', {}))
                        rps_pkg = {'date': d_str, 'rps120': rps120, 'rps250': rps250}
                        rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                        with open(rps_path, 'wb') as f:
                            pickle.dump(rps_pkg, f, protocol=4)
                        self.engine.set_precomputed_rps(d_str, rps120, rps250)
                        valid_count = int(rps120.notna().sum())
                        print(f"[F5] 阶段2/3 完成 -- RPS 已计算并保存"
                              f"({valid_count} 只有效排名,基准日 {d_str})")
                    else:
                        print("[F5] ⚠ 阶段2/3: RPS 矩阵计算返回空")
                except Exception as e:
                    print(f"[F5] ❌ 阶段2 RPS 计算异常: {e}")
                    _tb.print_exc()

                if getattr(self, '_f5_cancelled', False):
                    print("[F5] ⏹ 用户取消")
                    return

                # 阶段2.5: 预计算板块 RPS
                print("[F5] 阶段2.5/3: 预计算板块 RPS...")
                try:
                    from vcp.sector import SectorManager
                    from vcp.constants import SECTOR_RPS_CACHE_FILE
                    tdx_root = (
                        os.path.dirname(self.data_provider.tdx_vipdoc)
                        if self.data_provider.tdx_vipdoc else r'D:\\HT'
                    )
                    sm = SectorManager(tdx_root)
                    all_data_f5 = {
                        c: df for c, df in self.data_provider.cache_data.items()
                        if df is not None and len(df) >= 60
                    }
                    sector_date = datetime.date.today().strftime('%Y%m%d')
                    sector_rps = sm.build_sector_rps(all_data_f5, sector_date)
                    sector_pkg = {'date': sector_date, 'sector_rps': sector_rps}
                    with open(SECTOR_RPS_CACHE_FILE, 'wb') as f:
                        pickle.dump(sector_pkg, f, protocol=4)
                    print(f"[F5] 阶段2.5/3 完成 -- 板块 RPS ({len(sector_rps)} 个板块)")
                except Exception as e:
                    print(f"[F5] ❌ 阶段2.5 板块 RPS 异常: {e}")
                    _tb.print_exc()

                elapsed = _time.time() - total_start
                log.info(f"[F5] ✅ 全部完成 -- 耗时 {elapsed:.1f} 秒")

            except Exception as e:
                log.error(f"[F5] ❌ 预计算过程发生未预期异常: {e}")
                _tb.print_exc()
            finally:
                elapsed = _time.time() - total_start
                count = len(self.data_provider.cache_data) if self.data_provider.cache_data else 0
                log.info(f"[F5] 正在恢复UI状态... (count={count}, elapsed={elapsed:.1f}s)")
                self._sig_f5_done.emit(count, elapsed)

        task_manager.run_in_background(run_f5, task_id="f5_precompute")

    def _deferred_data_load(self):
        """延迟加载缓存数据（pkl + RT缓存 + RPS缓存），避免阻塞UI线程"""
        def _load_bg():
            try:
                cache_date = self.data_provider.load_cache_from_disk()
                if cache_date:
                    self._cache_date = cache_date
                    count = len(self.data_provider.cache_data)
                    self._call_in_ui(
                        lambda: self.lbl_code_count.setText(f"标的池: {count}")
                    )
                    self._call_in_ui(lambda: self.lbl_status.setText(
                        f"已加载 {count} 只标的缓存 (日期: {cache_date})"
                    ))
            except Exception as e:
                log.error(f"[启动] 延迟加载缓存异常: {e}")

            # 在UI线程恢复RT缓存
            self._call_in_ui(self._load_rt_cache)

            # 加载 RPS 预计算缓存
            try:
                self._try_load_rps_from_disk()
            except Exception as e:
                log.error(f"[启动] RPS 缓存加载异常: {e}")

            # 通知各 Tab: 缓存数据已就绪，可以回填历史数据
            self._call_in_ui(
                lambda: event_bus.sig_data_updated.emit("cache_loaded", None)
            )

        task_manager.run_in_background(_load_bg, task_id="deferred_load")

    def _smart_startup(self):
        """智能启动：异步检测网络，联网可用则自动切换联网模式"""
        def _check_and_go_online():
            try:
                if self.data_provider.test_network(timeout=3):
                    self.data_provider.set_online_mode(True)
                    self._call_in_ui(lambda: self._update_network_ui(True))
                    log.info("[智能启动] ✅ 网络可用，已自动切换到联网模式")
                    self._call_in_ui(self._auto_start_rt_if_ready)
                else:
                    log.info("[智能启动] 网络不可用，保持离线模式")
            except Exception as e:
                log.error(f"[智能启动] 网络检测异常: {e}")

        task_manager.run_in_background(_check_and_go_online, task_id="smart_startup")

    def _auto_start_rt_if_ready(self):
        """智能启动后自动开启盘中监控（仅在交易时间且数据就绪时）"""
        try:
            from vcp.constants import MARKET_OPEN_AM, MARKET_CLOSE_PM
            now = datetime.datetime.now()
            h, m = now.hour, now.minute
            # 检查是否在交易时间内（9:25 ~ 15:00）
            in_market = (
                (h > MARKET_OPEN_AM[0] or (h == MARKET_OPEN_AM[0] and m >= MARKET_OPEN_AM[1]))
                and (h < MARKET_CLOSE_PM[0] or (h == MARKET_CLOSE_PM[0] and m <= MARKET_CLOSE_PM[1]))
            )
            if not in_market:
                print("[智能启动] 非交易时间，跳过盘中自动监控")
                return
            # 检查是否有足够的缓存数据
            if not self.data_provider.cache_data or len(self.data_provider.cache_data) < 100:
                print("[智能启动] 数据不足，跳过盘中自动监控")
                return
            # 委托给 RtMonitorTab 组件启动
            if hasattr(self, 'tab_rt') and hasattr(self.tab_rt, '_toggle_rt_monitor'):
                self.tab_rt._toggle_rt_monitor()
                print("[智能启动] ✅ 盘中监控已自动启动")
        except Exception as e:
            print(f"[智能启动] 自动监控启动异常: {e}")

    def _try_load_rps_from_disk(self):
        """尝试从磁盘加载 F5 预计算的 RPS 缓存"""
        import pickle
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
        if not os.path.exists(rps_path):
            return
        try:
            with open(rps_path, 'rb') as f:
                pkg = pickle.load(f)
            cached_date = pkg.get('date', '')
            rps120 = pkg.get('rps120')
            rps250 = pkg.get('rps250')
            if rps120 is None or rps250 is None:
                return
            self.engine.set_precomputed_rps(cached_date, rps120, rps250)
            count = int(rps120.notna().sum()) if hasattr(rps120, 'notna') else 0
            print(f"[RPS] ✓ 从磁盘加载预计算RPS(基准日 {cached_date},{count} 只有效排名)")
            self.lbl_status.setText(f"RPS缓存已加载({cached_date},{count}只)")
        except Exception as e:
            print(f"[RPS] 磁盘加载失败: {e}")

    def _save_rt_cache(self):
        """保存盘中监控当日缓存到 pkl 文件"""
        import pickle, re
        table = self.table_rt
        if table.rowCount() == 0:
            return
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        os.makedirs(cache_dir, exist_ok=True)
        try:
            rows = []
            for r in range(table.rowCount()):
                row_vals = []
                for c in range(table.columnCount()):
                    item = table.item(r, c)
                    row_vals.append(item.text() if item else '')
                rows.append(row_vals)
            if rows and rows[0]:
                first_cell = rows[0][0]
                if len(first_cell) > 10 or '(' in first_cell or ',' in first_cell:
                    print(f"[盘中缓存] 检测到异常数据,跳过保存")
                    return
            data = {
                'date': datetime.date.today().isoformat(),
                'version': 2,
                'rows': rows,
                'headers': [
                    table.horizontalHeaderItem(c).text()
                    for c in range(table.columnCount())
                ],
            }
            path = os.path.join(
                cache_dir, f"rt_monitor_{datetime.date.today().isoformat()}.pkl"
            )
            with open(path, 'wb') as f:
                pickle.dump(data, f, protocol=4)
            print(f"[盘中缓存] 已保存 {len(rows)} 条信号到 {os.path.basename(path)}")

            # 清理超过 10 天的旧缓存
            today = datetime.date.today()
            for fname in os.listdir(cache_dir):
                if fname.startswith('rt_monitor_') and fname.endswith('.pkl'):
                    m = re.search(r'rt_monitor_(\d{4}-\d{2}-\d{2})\.pkl', fname)
                    if m:
                        try:
                            fdate = datetime.datetime.strptime(
                                m.group(1), '%Y-%m-%d'
                            ).date()
                            if (today - fdate).days > 10:
                                os.remove(os.path.join(cache_dir, fname))
                        except Exception:
                            pass
        except Exception as e:
            print(f"[盘中缓存] 保存失败: {e}")

    def _load_rt_cache(self):
        """启动时加载最近的盘中监控缓存"""
        import pickle
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        path = None
        for days_ago in range(10):
            check_date = datetime.date.today() - datetime.timedelta(days=days_ago)
            candidate = os.path.join(
                cache_dir, f"rt_monitor_{check_date.isoformat()}.pkl"
            )
            if os.path.exists(candidate):
                path = candidate
                break
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            raw_rows = data.get('rows', [])
            if not raw_rows:
                return
            cache_date = data.get('date', '?')

            # 自动检测格式
            first = raw_rows[0]
            is_old_format = (
                isinstance(first, (list, tuple)) and len(first) == 2
                and isinstance(first[0], (list, tuple))
                and isinstance(first[1], (list, tuple))
            )

            table = self.table_rt
            if is_old_format:
                # 老格式: [(col_texts, col_colors), ...]
                table.setSortingEnabled(False)
                table.setRowCount(len(raw_rows))
                for r, (texts, _colors) in enumerate(raw_rows):
                    for c, text in enumerate(texts):
                        if c < table.columnCount():
                            item = NumericTableWidgetItem(str(text)) if c in (3, 4, 5) else QTableWidgetItem(str(text))
                            item.setForeground(QColor("#C9CDD4"))
                            item.setTextAlignment(
                                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                            )
                            table.setItem(r, c, item)
                table.setSortingEnabled(True)
            else:
                # 新格式 v2: [row_vals, ...]
                from PyQt6.QtWidgets import QTableWidgetItem
                table.setSortingEnabled(False)
                table.setRowCount(len(raw_rows))
                for r, row_vals in enumerate(raw_rows):
                    for c, text in enumerate(row_vals):
                        if c < table.columnCount():
                            if c in (3, 4, 5):
                                item = NumericTableWidgetItem(str(text))
                            else:
                                item = QTableWidgetItem(str(text))
                            item.setForeground(QColor("#C9CDD4"))
                            item.setTextAlignment(
                                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                            )
                            table.setItem(r, c, item)
                table.setSortingEnabled(True)
            print(f"[盘中缓存] 已恢复 {len(raw_rows)} 条信号(日期: {cache_date})")
        except Exception as e:
            print(f"[盘中缓存] 加载失败: {e}")

    def _start_scan(self):
        self.tab_scan.start_scan(self.ent_start.text(), self.ent_end.text())

    def _cancel_scan(self):
        """一键中止所有后台任务"""
        stopped = []
        if self.tab_scan.cancel_scan():
            stopped.append("VCP扫描")

        if getattr(self, '_f5_cancelled', None) is not None:
            self._f5_cancelled = True

        if hasattr(self, 'rt_worker') and self.rt_worker is not None:
            try:
                self.rt_worker.stop()
                stopped.append("盘中监控(新)")
            except Exception:
                pass

        if hasattr(self, 'tab_rt') and hasattr(self.tab_rt, 'cancel_monitor'):
            if self.tab_rt.cancel_monitor():
                stopped.append("盘中监控")

        if stopped:
            event_bus.sig_task_progress.emit("scan", 0, "已中止")
            print(f"[中止] 已停止: {', '.join(stopped)}")

    # =======================================================================
    # 右键菜单委托方法
    # =======================================================================
    def _toggle_special(self, code: str, name: str, is_fav: bool, vcp_data=None):
        """添加/移除关注池 — 委托给 WatchlistTab 组件"""
        if hasattr(self, 'tab_watchlist'):
            self.tab_watchlist.toggle_special(code, name, is_fav, vcp_data=vcp_data)
            action_text = "移出" if is_fav else "加入"
            self.lbl_status.setText(f"{name}({code}) 已{action_text}关注池")

    def _remove_from_special(self, code: str):
        """从关注池移除 — 委托给 WatchlistTab 组件"""
        if hasattr(self, 'tab_watchlist'):
            self.tab_watchlist.remove_from_special(code)

    def _export_current_tab(self):
        """导出当前激活 Tab 的表格数据"""
        current_idx = self.tabs.currentIndex()
        tab_widget = self.tabs.widget(current_idx)
        # 优先尝试调用 Tab 组件自身的导出方法
        if hasattr(tab_widget, 'export_table_to_excel'):
            tab_widget.export_table_to_excel()
            return
        if hasattr(tab_widget, '_export_to_excel'):
            tab_widget._export_to_excel()
            return
        # 兜底：通用导出
        self._export_generic_table(current_idx)

    # =======================================================================
    # [Global Event Bus] 信号中转站
    # =======================================================================
    @pyqtSlot(str, object)
    def _on_global_data_updated(self, data_type: str, payload: object):
        """响应全局数据变更信号，更新状态栏与标的池计数"""
        try:
            if data_type == "rt_quotes_refreshed":
                count = len(payload) if payload else 0
                self.lbl_status.setText(f"实时报价已刷新 ({count} 条)")
            elif data_type == "scan_results":
                count = len(payload) if payload else 0
                self.lbl_status.setText(f"扫描结果已更新 ({count} 条)")
            else:
                self.lbl_status.setText(f"数据更新: {data_type}")
            # 同步标的池计数
            if self.data_provider.cache_data:
                total = len(self.data_provider.cache_data)
                self.lbl_code_count.setText(f"标的池: {total}")
        except Exception as e:
            print(f"[EventBus] _on_global_data_updated 异常: {e}")

    @pyqtSlot(str, int, str)
    def _on_task_progress(self, module: str, pct: int, msg: str):
        """处理扫描进度更新"""
        if module == "scan":
            self.progress_bar.setValue(pct)
            self.lbl_status.setText(msg)
            if msg == "start":
                self.btn_scan.setText("⏳ 扫描中...")
                self.btn_scan.setEnabled(False)
                self.btn_cancel.setEnabled(True)
            elif pct == 100 or pct == 0:
                self.btn_scan.setText("执行全盘VCP扫描")
                self.btn_scan.setEnabled(True)
                self.btn_cancel.setEnabled(False)
                # 扫描完成后主动回收内存
                import gc
                gc.collect()

    # ================================================================
    # EventBus 信号处理（各 Tab 组件广播的信号）
    # ================================================================
    def _on_show_kline(self, code: str):
        """响应各 Tab 的 K 线图请求"""
        name = getattr(self.data_provider, 'code2name', {}).get(code, code)
        if not hasattr(self, '_charts'):
            self._charts = []

        # 清理已关闭的窗口 + 限制数量
        self._charts = [c for c in self._charts if c.isVisible()]
        MAX_CHART_WINDOWS = 3
        while len(self._charts) >= MAX_CHART_WINDOWS:
            oldest = self._charts.pop(0)
            oldest.close()

        chart = KLineChartWindow(
            main_window=self,
            code=code,
            name=name,
            data_provider=self.data_provider,
            vcp_data={'code': code, 'name': name},
            code_list=[],
            current_idx=0
        )
        chart.show()
        self._charts.append(chart)

    def _on_open_ai_diag(self, code: str, auto_start: str):
        """响应各 Tab 的 AI 诊断请求"""
        if hasattr(self, 'panel_ai'):
            self.panel_ai.open_ai_diag(preset_code=code, auto_start=auto_start)
