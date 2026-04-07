import os
import datetime
from vcp.constants import SPECIAL_POOL_DATA_CACHE, APP_VERSION
from ui.components.kline_window_manager import kline_manager
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QPushButton, QLabel, QLineEdit, QComboBox, QMenu,
    QTextEdit, QProgressBar, QSpinBox, QDoubleSpinBox, QFrame,
    QToolButton
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSettings
from PyQt6.QtGui import QColor, QIcon, QShortcut, QKeySequence

# 核心引擎与数据层
from vcp.data_provider import TdxDataProvider
from vcp.engine import VCPEngine
from ui.kline_window_qt import KLineChartWindow

from ui.components import AnimatedCard, PulsingDot, GlassPanel, AnimatedHoverButton
from ui.tabs.scan_tab import ScanTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.watchlist_tab import WatchlistTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.asian_market_tab import AsianMarketTab
from core.event_bus import event_bus
from core.event_types import DataEvent
from core.logger import get_logger

from core.cache_manager import CacheManager
from ui.startup_loader import StartupLoader
from core.task_manager import task_manager

log = get_logger(__name__)


class MainWindowQT(QMainWindow):
    """紫金研选主窗口 — 纯外壳控制器（Phase 2 重构后）"""
    _sig_f5_done = pyqtSignal(int, float)
    _sig_ui_call = pyqtSignal(object)

    # _merge_and_wrap_ai_diag 已删除 — AI诊断功能已移除，无调用方

    @pyqtSlot(object)
    def _run_ui_callback(self, callback):
        try:
            callback()
        except Exception as e:
            log.error(f"[UI回调] 异常: {e}")

    def _call_in_ui(self, callback):
        self._sig_ui_call.emit(callback)

    def __init__(self, splash=None):
        super().__init__()
        self._splash = splash
        self.setWindowTitle('紫金研选量化终端')

        # 记录默认逻辑工作区
        self._available_screen_geo = self._get_logical_work_area()
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bull_icon.ico")))
        # 注意：这里我们移除了 FramelessWindowHint，完全拥抱原生窗口！
        # setWindowTitle 已在上方 L63 设置过，不再重复
        # 强制接管最小尺寸，不让内部控件撑爆屏幕导致 resize 生效失败！
        self.setMinimumSize(1000, 600)
        self._sig_ui_call.connect(self._run_ui_callback)
        
        # 绑定系统级全局网络状态变更，确保所有角色的状态与UI强同步
        event_bus.sig_network_status_changed.connect(self._update_network_ui)

        self.startup_loader = StartupLoader(self)
        self.cache_manager = CacheManager()
        self._f5_cancelled = False
        self._settings = QSettings("VCPHunter", "MainWindowQT")
        
        self._splash_update(60, "正在构建主界面模块...")
        self.data_provider = TdxDataProvider(offline=True)
        self.data_provider.code2name = self.data_provider._get_codes_from_vipdoc()
        self.engine = VCPEngine.get_instance()
        self.worker = None
        self._current_results = []
        self._cache_date = None

        # 全局样式（从 ui/styles/global_qss.py 集中管理）
        from ui.styles.global_qss import GLOBAL_QSS
        self.setStyleSheet(GLOBAL_QSS)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 已彻底移除 self.custom_title_bar，使用完美原生标题栏
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
        
        self.btn_datasource = AnimatedHoverButton("离线模式")
        self.btn_datasource.setStyleSheet("font-size: 11px; color: #EF4444; font-weight: bold; padding: 2px 6px; border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; background: rgba(239, 68, 68, 0.05);")
        self.btn_datasource.clicked.connect(self._toggle_network)
        self.btn_datasource.setCursor(Qt.CursorShape.PointingHandCursor)
        status_layout.addWidget(self.btn_datasource)
        status_layout.addStretch()
        
        self.btn_reconnect = AnimatedHoverButton("强制测速")
        self.btn_reconnect.setStyleSheet("font-size: 11px; color: #93C5FD; font-weight: bold; padding: 2px 6px; border: 1px solid rgba(147, 197, 253, 0.3); border-radius: 4px; background: rgba(147, 197, 253, 0.05);")
        self.btn_reconnect.clicked.connect(self._force_reconnect)
        self.btn_reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reconnect.hide()  # 仅联机模式下显示
        status_layout.addWidget(self.btn_reconnect)
        
        cb_layout.addLayout(status_layout)
        left_layout.addWidget(card_brand)
        
        self._init_left_panel_controls(left_layout)
        left_layout.addStretch()
        
        h_split_widget = QWidget()
        h_split_layout = QHBoxLayout(h_split_widget)
        h_split_layout.setContentsMargins(0, 0, 0, 0)
        
        self._splash_update(75, "组件注册中...")
        self._init_right_panel()
        
        # 将左侧边栏和右侧 Tab 容器包装成水平布局，完全抛弃 QSplitter
        # 事实证明 QT6 在 Windows 下，多重嵌套 QTableView 和 QSplitter 有极大概率死锁或崩溃
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(left_panel, 1)
        content_layout.addWidget(self.tabs_wrapper, 5)
        
        main_layout.addLayout(content_layout, 1)
        
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
        
        self.lbl_code_count = QLabel("标的池: 0")
        self.lbl_code_count.setStyleSheet("color: #6B7280; font-size: 12px; font-weight: bold;")
        status_layout.addWidget(self.lbl_code_count)
        
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
        
        # 9. 恢复之前的界面布局、列宽、表格排序
        self._restore_ui_state()
        
        self._splash_update(90, "正在加载数据...")
        QTimer.singleShot(2500, self.startup_loader.deferred_data_load)
        QTimer.singleShot(4500, self.startup_loader.smart_startup)
        
        self._init_central_broadcaster()

    def _init_central_broadcaster(self):
        from ui.workers.central_quotes_worker import CentralQuotesService
        self.central_quotes_svc = CentralQuotesService(self, self.data_provider)

    def _get_logical_work_area(self):
        """原生窗口直接获取可用区域即可，无需魔改扣除像素"""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    # 联网成功后的各 Tab 刷新逻辑由 _on_smart_startup_online_done 负责

    def _on_smart_startup_online_done(self):
        """智能启动联网成功后，触发各Tab的实时数据刷新"""
        try:
            self._update_network_ui(True)
            # 测速完成后，主动触发各表格的独立联网实时刷新(覆盖掉此前加载的本地缓存)
            if hasattr(self, 'tab_na_daily') and hasattr(self.tab_na_daily, '_auto_refresh_realtime'):
                self.tab_na_daily._auto_refresh_realtime(force=True)
            if hasattr(self, 'tab_foreign_block') and hasattr(self.tab_foreign_block, '_auto_refresh_realtime'):
                self.tab_foreign_block._auto_refresh_realtime(force=True)
            if hasattr(self, 'tab_watchlist') and self.tab_watchlist:
                sp_codes = [str(r.get("代码")) for r in self.tab_watchlist.model.row_data if r.get("代码")]
                if sp_codes and hasattr(self.tab_watchlist, '_refresh_special_quotes'):
                    task_manager.run_in_background(
                        self.tab_watchlist._refresh_special_quotes, sp_codes,
                        on_success=lambda q: self.tab_watchlist._update_quotes_ui(q) if q else None,
                        task_id="smart_startup_watchlist"
                    )
        except Exception as e:
            log.error(f"[智能启动] 联网后Tab刷新异常: {e}")

    # _check_auto_rt_monitor 已删除 — 功能已被 RtMonitorTab._check_auto_start_stop() 完全替代，0 调用方

    def _toggle_network(self):
        """"""
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

    def _update_network_ui(self, online: bool, detail: str = ""):
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
            if hasattr(self, 'btn_reconnect'):
                self.btn_reconnect.show()
        else:
            self.btn_datasource.setText("离线模式")
            self.pulsing_dot.set_color("#EF4444")
            if hasattr(self, 'btn_reconnect'):
                self.btn_reconnect.hide()

    def _force_reconnect(self):
        """主站强制重新测速方法"""
        if not self.data_provider.is_online():
            return
            
        self.btn_reconnect.setEnabled(False)
        self.btn_datasource.setText("测速中...")
        self.btn_datasource.setStyleSheet(
             self.btn_datasource.styleSheet().replace("#22C55E", "#F59E0B")
             if "#22C55E" in self.btn_datasource.styleSheet() 
             else self.btn_datasource.styleSheet()
        )
        self.pulsing_dot.set_color("#F59E0B")
        
        def _reconnect_task():
            try:
                self.data_provider.force_reconnect_servers()
                ok = self.data_provider.test_network(timeout=2)
                return ok
            except Exception as e:
                log.error(f"强制重连异常: {e}")
                return False

        def _on_done(ok):
            self.btn_reconnect.setEnabled(True)
            self._update_network_ui(True)
            from ui.components.toast_widget import show_toast
            if ok:
                show_toast("强制测速完成，已切换至最快服务器。", "success", self, duration=2500)
            else:
                show_toast("服务器测速失败，请检查网络。", "error", self, duration=3500)

        task_manager.run_in_background(_reconnect_task, on_success=lambda res: self._call_in_ui(lambda: _on_done(res)), task_id="force_reconnect")

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
        
        # --- [1] Header: Title & Last F5 Time ---
        header_layout = QHBoxLayout()
        lbl_ops_title = QLabel("功能操作面板")
        lbl_ops_title.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: bold; letter-spacing: 1px;")
        header_layout.addWidget(lbl_ops_title)
        
        self.lbl_last_f5 = QLabel("上次: --")
        self.lbl_last_f5.setStyleSheet("color: #6B7280; font-size: 11px;")
        header_layout.addStretch()
        header_layout.addWidget(self.lbl_last_f5)
        ops_layout.addLayout(header_layout)

        # --- [2] Primary Action: Start Scan ---
        self.btn_scan = QPushButton("🚀 执行全盘选股")
        self.btn_scan.setObjectName("ctaButton")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.setFixedHeight(46)
        self.btn_scan.setStyleSheet("""
            QPushButton#ctaButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8B5CF6, stop:1 #6366F1);
                color: #FFFFFF; border: none; border-radius: 8px; font-weight: bold; font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton#ctaButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #A78BFA, stop:1 #818CF8);
            }
            QPushButton#ctaButton:pressed { background: #4F46E5; }
        """)
        self.btn_scan.clicked.connect(self._start_scan)
        ops_layout.addWidget(self.btn_scan)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._start_scan)

        # --- [3] Secondary Actions Grid ---
        from PyQt6.QtWidgets import QGridLayout
        sec_layout = QGridLayout()
        sec_layout.setSpacing(10)
        
        self.btn_f5 = QPushButton("🔄 F5 日线预计算")
        self.btn_rt_sidebar = QPushButton("⚡ 启动盘中监控")
        
        common_btn_qss = """
            QPushButton { 
                background-color: #1E293B; border: 1px solid #334155; 
                border-radius: 6px; color: #CBD5E1; font-weight: 500; font-size: 12px;
            }
            QPushButton:hover { background-color: #2D3748; border: 1px solid #8B5CF6; color: #FFFFFF; }
            QPushButton:pressed { background-color: #0F172A; }
            QPushButton:disabled { color: #475569; border: 1px solid #1E293B; }
        """
        for b in (self.btn_f5, self.btn_rt_sidebar):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(34)
            b.setStyleSheet(common_btn_qss)
            
        sec_layout.addWidget(self.btn_f5, 0, 0)
        sec_layout.addWidget(self.btn_rt_sidebar, 0, 1)
        
        ops_layout.addLayout(sec_layout)
        
        self.btn_f5.clicked.connect(self._action_refresh_f5)
        self.btn_rt_sidebar.clicked.connect(lambda *args: hasattr(self, 'tab_rt') and self.tab_rt._toggle_rt_monitor())
        self._update_last_f5_time()
        QShortcut(QKeySequence("F5"), self, activated=self._action_refresh_f5)
        
        ops_layout.addSpacing(4)
        
        # --- [4] Progress & Footer ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet("""
            QProgressBar { background-color: rgba(255,255,255,0.05); border: none; border-radius: 2px; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8B5CF6, stop:1 #A78BFA); border-radius: 2px; }
        """)
        ops_layout.addWidget(self.progress_bar)

        self.btn_cancel = QPushButton("⏹ 中止扫描与分析")
        self.btn_cancel.setProperty("class", "dangerGhost")
        self.btn_cancel.setFixedHeight(32)
        self.btn_cancel.setStyleSheet("""
            QPushButton { 
                background: rgba(239, 68, 68, 0.05); color: #FCA5A5; 
                border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 6px; font-weight: 500; font-size: 12px;
            }
            QPushButton:hover { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.6); color: #FECACA; }
            QPushButton:disabled { background: transparent; border: 1px solid rgba(255, 255, 255, 0.1); color: #4B5563; }
        """)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
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

        # 已移除原本用作中间存储状态的未挂载 QComboBox 和 QSpinBox

    def _init_right_panel(self):
        # 不使用嵌套 QSplitter——大量 QTableView 子组件在嵌套 QSplitter 中
        # 触发 Qt6 底层 access violation (Windows fatal exception)
        self.tabs_wrapper = QFrame(self)
        self.tabs_wrapper.setObjectName("tabsWrapperFrame")
        self.tabs_wrapper.setStyleSheet("""
            QFrame#tabsWrapperFrame {
                background-color: rgba(18, 20, 26, 0.95);
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 0.04);
            }
        """)
        tabs_layout = QVBoxLayout(self.tabs_wrapper)
        tabs_layout.setContentsMargins(6, 6, 6, 6)
        
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        tabs_layout.addWidget(self.tabs)
        

        self.tab_scan = ScanTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_scan, "VCP扫描")
        self.table_scan = self.tab_scan.table_scan

        # --- 右侧 AI 诊断面板(独立组件) ---

        

        self.tab_rt = RtMonitorTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_rt, "盘中监控")
        
        self.table_rt = self.tab_rt.table_rt
        self.btn_rt_start = self.tab_rt.btn_rt_start
        self.lbl_rt_info = self.tab_rt.lbl_rt_info

        # Tab 3: 关注池（独立组件）

        self.tab_watchlist = WatchlistTab(self.data_provider, self)
        self.tabs.addTab(self.tab_watchlist, '关注池')
        self.table_sp = self.tab_watchlist.table_sp  # 向下兼容引用

        # Tab 4: 北美战报（独立组件）

        self.tab_na_daily = NADailyTab(self.data_provider, self)
        self.tabs.addTab(self.tab_na_daily, "美股日报")
        self.na_daily_table = self.tab_na_daily.na_daily_table  # 向下兼容

        # Tab 5: 亚洲市场跟踪 (独立组件)

        self.tab_asian_market = AsianMarketTab(self.data_provider, self)
        self.tabs.addTab(self.tab_asian_market, "亚洲寡头")

        # Tab 6: 外资大宗交易 (独立组件)

        self.tab_foreign_block = ForeignBlockTradeTab(self.data_provider, self)
        self.tabs.addTab(self.tab_foreign_block, "大宗交易")

        # Tab 7: 业绩预告与财报爆点追踪（独立组件）
        from ui.tabs.earnings_tab import EarningsTab
        self.tab_earnings = EarningsTab(self.data_provider, self)
        self.tabs.addTab(self.tab_earnings, "业绩异动")

        from ui.tabs.log_tab import LogTab
        self.tab_log = LogTab(self)
        self.tabs.addTab(self.tab_log, "系统日志")
        
        event_bus.sig_rt_quotes_refreshed.connect(self._on_rt_quotes_refreshed)
        event_bus.sig_task_progress.connect(self._on_task_progress)
        event_bus.sig_show_kline.connect(self._on_show_kline)
        event_bus.sig_show_kline_with_list.connect(self._on_show_kline_with_list)


        # === Bug#5 修复: Ctrl+C 钩子适配 QTableView + QTableWidget 双模式 ===
        tables_to_patch = [
            getattr(self, 'table_scan', None),
            getattr(self, 'table_rt', None),
            getattr(self, 'table_sp', None), 
            getattr(self, 'na_daily_table', None), 
            getattr(self, 'ai_tracker_table', None),
            getattr(getattr(self, 'tab_foreign_block', None), 'table', None),
            getattr(getattr(self, 'tab_asian_market', None), 'asian_table', None),
            getattr(getattr(self, 'tab_earnings', None), 'table', None)
        ]
        
        from PyQt6.QtWidgets import QAbstractItemView, QApplication
        from PyQt6.QtGui import QKeySequence
        from ui.components.toast_widget import show_toast
        
        for t in tables_to_patch:
            if not t: continue
            
            # 允许点选单独的单元格，并且支持按住左键拉框多选多个格子
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
            t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            
            # 统一 Ctrl+C 钩子：兼容 QTableView（selectionModel）和 QTableWidget（selectedRanges）
            original_kp = t.keyPressEvent
            def make_kp(table, orig):
                def new_kp(event):
                    if event.matches(QKeySequence.StandardKey.Copy):
                        # 通用路径: 使用 selectionModel().selectedIndexes()，兼容 QTableView 和 QTableWidget
                        sel_model = table.selectionModel()
                        if sel_model:
                            indexes = sel_model.selectedIndexes()
                            if indexes:
                                # 按行列分组，组装制表符分隔文本（兼容 Excel 粘贴）
                                from collections import defaultdict
                                rows_dict = defaultdict(dict)
                                for idx in indexes:
                                    # 通过 model 取 DisplayRole 内容，避免依赖 .item() API
                                    display_val = table.model().data(idx, Qt.ItemDataRole.DisplayRole)
                                    rows_dict[idx.row()][idx.column()] = str(display_val) if display_val is not None else ""
                                lines = []
                                for row_key in sorted(rows_dict.keys()):
                                    cols = rows_dict[row_key]
                                    line = "\t".join(cols.get(c, "") for c in sorted(cols.keys()))
                                    lines.append(line)
                                QApplication.clipboard().setText("\n".join(lines))
                                show_toast("✅ 已复制单元格内容 (支持粘入Excel)", "success", table.window(), duration=1500)
                        event.accept()
                    else:
                        orig(event)
                return new_kp
                
            t.keyPressEvent = make_kp(t, original_kp)


    # _filter_table 已删除 — 各 Tab 已自行实现 proxy_model.setFilterText()，0 调用方


    # _on_table_double_click 已移除(#3)，各 Tab 自行通过 EventBus 广播 K 线请求

    # _show_context_menu 已移除(#2)，各 Tab 使用 stock_context_menu 工厂

    # _launch_tdx / _launch_eastmoney 已移除(#1)
    # 统一由 BaseStockTab 基类提供，避免双份代码维护噩梦

    def _save_ui_state(self):
        """Persist splitter sizes, window geometry, and table widths."""
        s = self._settings
        # 使用原生边框后，可以完美信任并且使用系统的 saveGeometry()
        s.setValue("geometry", self.saveGeometry())
        s.sync()

    def _restore_ui_state(self):
        """Restore native geometry, splitter sizes, and table widths."""
        import json
        s = self._settings
        
        # 完美的原生窗口只需让 QT 内置几何接口接管即可！
        geom_data = s.value("geometry")
        if geom_data:
            self.restoreGeometry(geom_data)
        else:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                # 动态自适应：宽度占屏幕可用区域的 80%，高度占 70%。完美适配任意 DPI 缩放比例
                w = int(avail.width() * 0.8)
                h = int(avail.height() * 0.7)
                self.resize(w, h)
                
                center = avail.center()
                geo = self.frameGeometry()
                geo.moveCenter(center)
                self.move(geo.topLeft())
            else:
                self.resize(1024, 768)

    # F5预计算 / 缓存加载 / 智能启动 / RPS缓存 / RT缓存
    # 已迁移至 core/rps_precomputer.py + ui/startup_loader.py

    def _update_last_f5_time(self):
        import os, datetime
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'Cache')
        rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
        if os.path.exists(rps_path) and hasattr(self, 'lbl_last_f5'):
            mtime = os.path.getmtime(rps_path)
            dt = datetime.datetime.fromtimestamp(mtime)
            # 例如展示格式：03-30 15:30
            self.lbl_last_f5.setText(f"上次: {dt.strftime('%m-%d %H:%M')}")
        elif hasattr(self, 'lbl_last_f5'):
            self.lbl_last_f5.setText("上次: 无")

    def _on_f5_done(self, count, elapsed):
        """Handle the completion signal from the F5 precompute workflow."""
        import gc
        gc.collect()  # 大型计算完成后主动回收内存
        self.btn_scan.setEnabled(True)
        self._update_last_f5_time()
        if count > 0:
            self.lbl_status.setText(f"F5预计算完成: {count}只 | 耗时{elapsed:.1f}s")
            self.lbl_code_count.setText(f"标的池: {count} 只")
        else:
            self.lbl_status.setText("F5预计算完成: 无新增数据")

    # showEvent 空覆写已删除 — 无自定义逻辑，交给 QMainWindow 默认处理

    def closeEvent(self, event):
        """应用关闭：广播信号让各组件自行保存，然后清理资源"""
        try:
            self._save_ui_state()
        except Exception as e:
            log.error(f"[关闭] 保存UI状态异常: {e}")

        # 保存盘中监控缓存（MVC兼容）
        try:
            if hasattr(self, 'table_rt'): self.cache_manager.save_rt_cache(self.table_rt)
        except Exception as e:
            log.error(f"[关闭] 保存盘中缓存异常: {e}")

        # 广播关闭信号，各 Tab 组件自行保存缓存
        try:
            event_bus.sig_app_closing.emit()
        except Exception as e:
            log.error(f"[关闭] 广播关闭信号异常: {e}")

        # Bug#1 修复: rt_worker 属于 tab_rt，不是 MainWindowQT 自身的属性
        if hasattr(self, 'tab_rt') and hasattr(self.tab_rt, 'rt_worker'):
            try:
                if self.tab_rt.rt_worker.isRunning():
                    self.tab_rt.rt_worker.stop()
                    self.tab_rt.rt_worker.wait(2000)
            except Exception as e:
                log.error(f"[关闭] 停止盘中监控线程异常: {e}")

        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)

        if hasattr(self, '_auto_rt_timer'):
            self._auto_rt_timer.stop()

        super().closeEvent(event)

    

    def _action_refresh_f5(self):
        """F5 盘后预计算界面触发层"""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "盘后一键预计算",
            "此操作将执行完整的盘后数据重建流程：\n\n"
            "① 从通达信本地日线(vipdoc)重新读取数据\n"
            "② 预计算全市场RPS排名(120日/250日)\n"
            "③ 预计算板块RPS排名\n"
            "④ 保存缓存供次日盘中监控使用\n\n"
            "请确保已在通达信中完成【盘后数据下载】.\n是否执行?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply != QMessageBox.StandardButton.Yes: return

        if hasattr(self, 'lbl_status'): self.lbl_status.setText("F5 盘后预计算进行中...")
        if hasattr(self, 'btn_scan'): self.btn_scan.setEnabled(False)
        if hasattr(self, 'btn_cancel'): self.btn_cancel.setEnabled(True)
        self._f5_cancelled = False

        from core.rps_precomputer import RPSPrecomputer
        
        def _set_status_cb(msg):
            self._call_in_ui(lambda: hasattr(self, 'lbl_status') and self.lbl_status.setText(msg))
            
        def _done_cb(count, elapsed):
            self._call_in_ui(lambda: self._on_f5_done(count, elapsed))

        from core.task_manager import task_manager
        task_manager.run_in_background(
            lambda: RPSPrecomputer.run_f5_pipeline(
                data_provider=self.data_provider,
                engine=self.engine,
                cancelled_checker=lambda: getattr(self, '_f5_cancelled', False),
                set_status_callback=_set_status_cb,
                done_callback=_done_cb
            ), task_id="f5_precompute")

    def _start_scan(self):
        self.tab_scan.start_scan(self.ent_start.text(), self.ent_end.text())

    def _cancel_scan(self):
        """一键中止所有后台任务"""
        stopped = []
        if self.tab_scan.cancel_scan():
            stopped.append("VCP扫描")

        if getattr(self, '_f5_cancelled', None) is not None:
            self._f5_cancelled = True

        # Bug#1 修复: rt_worker 属于 tab_rt
        if hasattr(self, 'tab_rt') and hasattr(self.tab_rt, 'rt_worker'):
            try:
                self.tab_rt.rt_worker.stop()
                stopped.append("盘中监控")
            except Exception as e:
                log.error(f"[中止] 停止盘中监控异常: {e}")

        if stopped:
            event_bus.sig_task_progress.emit("scan", 0, "已中止")
            log.info(f"[中止] 已停止: {', '.join(stopped)}")

    # =======================================================================
    # 右键菜单委托方法
    # =======================================================================
    # _toggle_special 已删除 — 关注池操作已统一由 watchlist_vm.toggle_stock() 处理，0 调用方



    # _export_current_tab 已删除 — 无菜单/快捷键指向它，各 Tab 自带独立导出按钮

    # =======================================================================
    # [Global Event Bus] 信号中转站
    # =======================================================================
    @pyqtSlot(object)
    def _on_rt_quotes_refreshed(self, payload: object):
        """响应盘中监控刷新完成"""
        try:
            count = len(payload) if payload else 0
            if hasattr(self, 'lbl_status'):
                self.lbl_status.setText(f"实时报价已刷新 ({count} 条)")
            if self.data_provider.cache_data and hasattr(self, 'lbl_code_count'):
                total = len(self.data_provider.cache_data)
                self.lbl_code_count.setText(f"标的池: {total}")
        except Exception as e:
            log.error(f"[EventBus] _on_rt_quotes_refreshed 异常: {e}")

    @pyqtSlot(str, int, str)
    def _on_task_progress(self, module: str, pct: int, msg: str):
        """处理扫描进度更新"""
        if module == "scan":
            if hasattr(self, 'progress_bar'): self.progress_bar.setValue(pct)
            if hasattr(self, 'lbl_status'): self.lbl_status.setText(msg)
            if hasattr(self, 'btn_scan'):
                if msg == "start":
                    self.btn_scan.setText("⏳ 扫描中...")
                    self.btn_scan.setEnabled(False)
                    if hasattr(self, 'btn_cancel'): self.btn_cancel.setEnabled(True)
                elif pct == 100 or pct == 0:
                    self.btn_scan.setText("执行全盘VCP扫描")
                    self.btn_scan.setEnabled(True)
                    if hasattr(self, 'btn_cancel'): self.btn_cancel.setEnabled(False)
                    import gc
                    gc.collect()
        elif module == "rt_monitor":
            if msg == "start":
                self.btn_rt_sidebar.setText("⏹ 停止盘中监控")
                # 与 Tab 内按钮一致的红色运行态样式
                self.btn_rt_sidebar.setStyleSheet(
                    "QPushButton { color: white; border: none; font-weight: 500; font-size: 12px; "
                    "background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #DC2626,stop:1 #EF4444); "
                    "border-radius: 6px; }"
                    "QPushButton:hover { background-color: #B91C1C; }"
                )
            elif msg == "stop":
                self.btn_rt_sidebar.setText("⚡ 启动盘中监控")
                # 恢复默认灰色样式
                self.btn_rt_sidebar.setStyleSheet(
                    "QPushButton { background-color: #1E293B; border: 1px solid #334155; "
                    "border-radius: 6px; color: #CBD5E1; font-weight: 500; font-size: 12px; }"
                    "QPushButton:hover { background-color: #2D3748; border: 1px solid #8B5CF6; color: #FFFFFF; }"
                    "QPushButton:pressed { background-color: #0F172A; }"
                    "QPushButton:disabled { color: #475569; border: 1px solid #1E293B; }"
                )

    # ================================================================
    # EventBus 信号处理（各 Tab 组件广播的信号）
    # ================================================================
    def _on_show_kline(self, code: str):
        """响应简单K线图请求（无上下文列表）"""
        self._on_show_kline_with_list(code, [], 0)

    def _on_show_kline_with_list(self, code: str, code_list: list, current_idx: int):
        """响应带列表上下文的 K 线图请求 — 委托给 KLineWindowManager (#1)"""
        name = getattr(self.data_provider, 'code2name', {}).get(code, code)
        if code_list and 0 <= current_idx < len(code_list):
            item_data = code_list[current_idx]
            if isinstance(item_data, dict) and item_data.get('代码') == code:
                name = item_data.get('名称', name)

        kline_manager.open_chart(
            main_window=self,
            code=code,
            name=name,
            data_provider=self.data_provider,
            vcp_data={'code': code, 'name': name},
            code_list=code_list,
            current_idx=current_idx,
        )

