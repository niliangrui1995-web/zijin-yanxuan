import os
import datetime
from vcp.constants import APP_VERSION, RPS_CACHE_FILE
from ui.components.kline_window_manager import kline_manager
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QFrame,
    QTabWidget, QToolTip
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSettings, QEvent
from PyQt6.QtGui import QIcon

# 核心引擎与数据层
from vcp.data_provider import TdxDataProvider
from vcp.engine import VCPEngine

from ui.tabs.scan_tab import ScanTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.watchlist_tab import WatchlistTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.asian_market_tab import AsianMarketTab
from ui.tabs.lhb_tab import LhbTab
from core.event_bus import event_bus
from core.logger import get_logger
from ui.components.main_window_shell import (
    DraggableTitleBar,
    MainWindowStatusBar,
    apply_chrome_theme,
    inject_standalone_tabbar,
    refresh_system_menu_theme,
    setup_custom_titlebar,
    setup_system_menu,
)

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
        if getattr(self, '_is_closing', False):
            return
        self._sig_ui_call.emit(callback)

    # nativeEvent 已移除：PyQt6 的 sip.voidptr 与 ctypes 内存布局不兼容，
    # 会导致 Windows 段错误。边缘缩放改用纯 Python 鼠标事件实现。

    def __init__(self, splash=None):
        super().__init__()
        self._is_closing = False
        self._splash = splash
        self.setWindowTitle('紫金研选量化终端')

        # 记录默认逻辑工作区
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bull_icon.ico")))
        # 无边框改造：去掉原生标题栏，由自定义标题栏接管
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMinimumSize(1000, 600)
        self._sig_ui_call.connect(self._run_ui_callback)
        
        # 拖拽相关状态
        self._drag_pos = None
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

        # 全局样式（动态生成，支持主题切换）
        from ui.styles.global_qss import generate_global_qss
        qss = generate_global_qss()
        self.setStyleSheet(qss)
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPalette, QColor
        if QApplication.instance():
            pal = QApplication.style().standardPalette()
            from ui.theme import theme_manager
            t = theme_manager.current_theme
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                pal.setColor(group, QPalette.ColorRole.ToolTipBase, QColor(t['BG_ELEVATED']))
                pal.setColor(group, QPalette.ColorRole.ToolTipText, QColor(t['TEXT_PRIMARY']))
            QApplication.instance().setPalette(pal)
            QApplication.instance().setStyleSheet(qss)
            QToolTip.setPalette(pal)
        # 监听主题切换信号，实时刷新全局样式
        from ui.theme import theme_manager
        theme_manager.sig_theme_changed.connect(self._apply_theme)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 自定义标题栏：品牌文字 + Tab导航 + 窗口控制按钮 合并一行
        self._init_custom_titlebar(main_layout)
        
        self._splash_update(75, "组件注册中...")

        self._init_right_panel()
        main_layout.addWidget(self.tabs_wrapper, 1)

        self._status_bar_widget = MainWindowStatusBar(f"v{APP_VERSION}", self)
        self.status_dot = self._status_bar_widget.status_dot
        self.lbl_status = self._status_bar_widget.lbl_status
        self.lbl_code_count = self._status_bar_widget.lbl_code_count
        self.lbl_clock = self._status_bar_widget.lbl_clock
        self.lbl_version = self._status_bar_widget.lbl_version
        main_layout.addWidget(self._status_bar_widget, 0)
        
        # 9. 恢复之前的界面布局、列宽、表格排序
        self._restore_ui_state()
        
        self._splash_update(90, "正在加载数据...")
        self.startup_loader.schedule_startup()
        
        self._init_central_broadcaster()
        self._update_last_f5_time()

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
        if hasattr(self, 'act_network'):
            if online:
                self.act_network.setText("网络状态：在线")
                if hasattr(self, 'status_dot'): self.status_dot.set_color("#22C55E")
            else:
                self.act_network.setText("网络状态：离线")
                if hasattr(self, 'status_dot'): self.status_dot.set_color("#EF4444")

    def _force_reconnect(self):
        """主站强制重新测速方法"""
        if not self.data_provider.is_online():
            return
        if hasattr(self, 'status_dot'): self.status_dot.set_color("#F59E0B")
        
        def _reconnect_task():
            try:
                self.data_provider.force_reconnect_servers()
                ok = self.data_provider.test_network(timeout=2)
                return ok
            except Exception as e:
                log.error(f"强制重连异常: {e}")
                return False

        def _on_done(ok):
            self._update_network_ui(True)
            from ui.components.toast_widget import show_toast
            if ok:
                show_toast("强制测速完成，已切换至最快服务器。", "success", self, duration=2500)
            else:
                show_toast("服务器测速失败，请检查网络。", "error", self, duration=3500)

        task_manager.run_in_background(_reconnect_task, on_success=lambda res: self._call_in_ui(lambda: _on_done(res)), task_id="force_reconnect")

    def _splash_update(self, value: int, status: str = ""):
        """update progress"""
        if self._splash:
            self._splash.set_progress(value, status)

    def _refresh_gear_menu_theme(self):
        """同步刷新齿轮按钮与设置菜单的主题样式。"""
        refresh_system_menu_theme(self)

    def _init_gear_menu(self):
        """在标题栏右侧注入系统菜单。"""
        setup_system_menu(self)
        self._update_last_f5_time()


    def _apply_table_density(self, mode: str, persist: bool = True):
        from core.app_config import app_config
        from PyQt6.QtWidgets import QApplication
        from ui.components import VCPTableView
        from ui.styles.global_qss import generate_global_qss

        if mode not in ("紧凑", "舒适"):
            mode = "舒适"

        if persist:
            app_config.table_density = mode
            app_config.sync()

        if hasattr(self, "_act_density_compact"):
            self._act_density_compact.setChecked(mode == "紧凑")
        if hasattr(self, "_act_density_comfort"):
            self._act_density_comfort.setChecked(mode == "舒适")

        for widget in QApplication.allWidgets():
            if isinstance(widget, VCPTableView):
                widget.apply_density(mode)

        app = QApplication.instance()
        if app:
            qss = generate_global_qss(density=mode)
            self.setStyleSheet(qss)
            app.setStyleSheet(qss)

        apply_chrome_theme(self)
        if hasattr(self, '_status_bar_widget') and self._status_bar_widget:
            self._status_bar_widget.apply_theme()


    def eventFilter(self, obj, event):
        target_objects = (
            getattr(self, 'btn_sys_menu', None),
            getattr(self, '_sys_menu', None),
            getattr(self, '_density_menu', None),
            getattr(self, '_theme_menu', None),
        )
        if obj in target_objects:
            if event.type() in (
                QEvent.Type.Enter,
                QEvent.Type.HoverEnter,
                QEvent.Type.HoverMove,
                QEvent.Type.MouseMove,
            ):
                try:
                    from PyQt6.QtWidgets import QApplication
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
                obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)

    def _show_trade_calendar(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QFrame, QHBoxLayout, QToolButton
        from PyQt6.QtCore import Qt
        from ui.components.trade_calendar import TradeCalendarWidget
        from ui.theme import theme_manager as _tm
        
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.resize(400, 360)
        
        # 外层防锯齿透明容器
        main_layout = QVBoxLayout(dlg)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        container = QFrame()
        container.setObjectName("dialogContainer")
        container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {_tm.get('BG_BASE')};
                border: 1px solid {_tm.get('BORDER_DEFAULT')};
                border-radius: 8px;
            }}
        """)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(1, 1, 1, 14)
        container_layout.setSpacing(0)
        
        # 顶部自定义可拖拽标题栏
        title_bar = DraggableTitleBar(dlg)
        title_bar.setObjectName("calendarTitleBar")
        title_bar.setFixedHeight(38)
        title_bar.setStyleSheet(f"""
            QWidget#calendarTitleBar {{
                background-color: {_tm.get('BG_TITLEBAR')};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid {_tm.get('TITLEBAR_BORDER')};
            }}
        """)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(14, 0, 8, 0)
        
        title_lbl = QLabel("A股交易休市日历")
        title_lbl.setStyleSheet(f"color: {_tm.get('TEXT_PRIMARY')}; font-weight: bold; background: transparent;")
        tb_layout.addWidget(title_lbl)
        tb_layout.addStretch()
        
        btn_close = QToolButton()
        btn_close.setText("✕")
        btn_close.setFixedSize(32, 28)
        btn_close.clicked.connect(dlg.reject)
        btn_close.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {_tm.get('TEXT_MUTED')};
            }}
            QToolButton:hover {{
                background-color: #E81123;
                color: white;
                border-radius: 4px;
            }}
        """)
        tb_layout.addWidget(btn_close)
        container_layout.addWidget(title_bar)
        
        # 内容区
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(14, 14, 14, 0)
        
        cal = TradeCalendarWidget()
        content_layout.addWidget(cal)
        
        container_layout.addLayout(content_layout)
        main_layout.addWidget(container)
        
        dlg.exec()

    # =====================================================================
    # 自定义标题栏：品牌 + Tab 导航 + 窗口控制，合并成一行
    # =====================================================================
    def _init_custom_titlebar(self, parent_layout):
        """构建无边框窗口的自定义标题栏。"""
        refs = setup_custom_titlebar(self, parent_layout)
        self._custom_titlebar = refs.titlebar
        self._titlebar_layout = refs.layout
        self._titlebar_tab_placeholder = refs.placeholder
        self._btn_minimize = refs.btn_minimize
        self._btn_maximize = refs.btn_maximize
        self._btn_close = refs.btn_close

    def _inject_tabbar_into_titlebar(self):
        """在标题栏创建独立 TabBar 并与 QTabWidget 双向同步。"""
        self._standalone_tabbar = inject_standalone_tabbar(self)

    def _toggle_maximize(self):
        """切换最大化/还原"""
        if self.isMaximized():
            self.showNormal()
            self._btn_maximize.setText("□")
        else:
            self.showMaximized()
            self._btn_maximize.setText("❐")

    def changeEvent(self, event):
        """窗口状态变化时同步最大化按钮图标"""
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if hasattr(self, '_btn_maximize'):
                if self.isMaximized():
                    self._btn_maximize.setText("❐")
                else:
                    self._btn_maximize.setText("□")

    def _init_right_panel(self):
        # 不使用嵌套 QSplitter——大量 QTableView 子组件在嵌套 QSplitter 中
        # 触发 Qt6 底层 access violation (Windows fatal exception)
        self.tabs_wrapper = QFrame(self)
        self.tabs_wrapper.setObjectName("tabsWrapperFrame")
        from ui.theme import theme_manager as _twm
        _tw = _twm.current_theme
        self.tabs_wrapper.setStyleSheet(f"""
            QFrame#tabsWrapperFrame {{
                background-color: {_tw['BG_GLASS']};
                border: none;
            }}
        """)
        tabs_layout = QVBoxLayout(self.tabs_wrapper)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        tabs_layout.addWidget(self.tabs)
        

        # --- 右侧 AI 诊断面板(独立组件) ---

        
        # Tab 3: 关注池（独立组件）

        self.tab_watchlist = WatchlistTab(self.data_provider, self)
        self.tabs.addTab(self.tab_watchlist, '关注池')
        self.table_sp = self.tab_watchlist.table_sp  # 向下兼容引用

        # Tab 3.5: 龙虎榜（紧跟关注池）
        self.tab_lhb = LhbTab(self.data_provider, self)
        self.tabs.addTab(self.tab_lhb, "龙虎榜")

        # Tab 4: 北美战报（独立组件）

        self.tab_na_daily = NADailyTab(self.data_provider, self)
        self.tabs.addTab(self.tab_na_daily, "美股日报")
        self.na_daily_table = self.tab_na_daily.na_daily_table  # 向下兼容

        # Tab 5: 亚洲市场跟踪 (独立组件)

        self.tab_asian_market = AsianMarketTab(self.data_provider, self)
        self.tabs.addTab(self.tab_asian_market, "亚洲寡头")

        self.tab_rt = RtMonitorTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_rt, "盘中监控")
        
        self.table_rt = self.tab_rt.table_rt
        self.btn_rt_start = self.tab_rt.btn_rt_start
        self.lbl_rt_info = self.tab_rt.lbl_rt_info

        # Tab 6: 外资大宗交易 (独立组件)

        self.tab_foreign_block = ForeignBlockTradeTab(self.data_provider, self)
        self.tabs.addTab(self.tab_foreign_block, "大宗交易")

        # (龙虎榜已移至关注池后方)

        # Tab 7: 业绩预告与财报爆点追踪（独立组件）
        from ui.tabs.earnings_tab import EarningsTab
        self.tab_earnings = EarningsTab(self.data_provider, self)
        self.tabs.addTab(self.tab_earnings, "业绩异动")

        self.tab_scan = ScanTab(self.data_provider, self.engine, self)
        self.tabs.addTab(self.tab_scan, "VCP扫描")
        self.table_scan = self.tab_scan.table_scan

        from ui.tabs.log_tab import LogTab
        self.tab_log = LogTab(self)
        self.tabs.addTab(self.tab_log, "系统日志")
        
        event_bus.sig_rt_quotes_refreshed.connect(self._on_rt_quotes_refreshed)
        event_bus.sig_task_progress.connect(self._on_task_progress)
        event_bus.sig_show_kline.connect(self._on_show_kline)
        event_bus.sig_show_kline_with_list.connect(self._on_show_kline_with_list)
        
        # 激活齿轮菜单
        self._init_gear_menu()
        
        # 把 TabBar 从 QTabWidget 拉到标题栏中，完成"品牌+导航+控制"一行布局
        self._inject_tabbar_into_titlebar()


        # === Bug#5 修复: Ctrl+C 钩子适配 QTableView + QTableWidget 双模式 ===
        tables_to_patch = [
            getattr(self, 'table_scan', None),
            getattr(self, 'table_rt', None),
            getattr(self, 'table_sp', None), 
            getattr(self, 'na_daily_table', None), 
            getattr(self, 'ai_tracker_table', None),
            getattr(getattr(self, 'tab_lhb', None), 'table', None),
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
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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
                                current_idx = sel_model.currentIndex()
                                # 智能剥离模式：如果因为 SelectRows 导致单行全亮，但用户只想复制自己点的那个格子，则过滤
                                unique_rows = set(idx.row() for idx in indexes)
                                if len(unique_rows) == 1 and current_idx.isValid():
                                    indexes = [current_idx]
                                    
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
                                show_toast("已复制单元格内容，可直接粘贴到 Excel。", "success", table.window(), duration=1500)
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
        """Persist window geometry with version tag."""
        s = self._settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("geometry_version", 2)  # 无边框版本标记，防止旧缓存导致崩溃
        s.sync()

    def _restore_ui_state(self):
        """Restore window geometry. Frameless window needs special handling."""
        s = self._settings
        
        # 无边框改造后，旧的 geometry 缓存可能不兼容，需要版本化
        geometry_version = s.value("geometry_version", 0, type=int)
        geom_data = s.value("geometry")
        
        if geom_data and geometry_version >= 2:
            # 版本匹配，安全恢复
            try:
                self.restoreGeometry(geom_data)
                return
            except Exception as e:
                log.warning(f"[UI] restoreGeometry 失败，使用默认布局: {e}")
        
        # 版本不匹配或无缓存：使用自适应默认布局
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
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
        if os.path.exists(RPS_CACHE_FILE):
            mtime = os.path.getmtime(RPS_CACHE_FILE)
            dt = datetime.datetime.fromtimestamp(mtime)
            if hasattr(self, 'act_f5'):
                self.act_f5.setText(f"全局数据同步 (F5) [{dt.strftime('%m-%d')}]")
        else:
            if hasattr(self, 'act_f5'):
                self.act_f5.setText("全局数据同步 (F5) [暂无]")

    def _on_f5_done(self, count, elapsed):
        """Handle the completion signal from the F5 precompute workflow."""
        # 将倒垃圾赶出主车道：延迟 2000ms 在事件循环空闲时自动回收，完全错开 QTableView 的爆量重绘期
        import gc
        QTimer.singleShot(2000, lambda: gc.collect())
        self._update_last_f5_time()
        if count > 0:
            self.lbl_status.setText(f"F5预计算完成: {count}只 | 耗时{elapsed:.1f}s")
            self.lbl_code_count.setText(f"标的池: {count} 只")
        else:
            self.lbl_status.setText("F5预计算完成: 无新增数据")

    # showEvent 空覆写已删除 — 无自定义逻辑，交给 QMainWindow 默认处理

    def closeEvent(self, event):
        """应用关闭：广播信号让各组件自行保存，然后清理资源"""
        self._is_closing = True
        self._f5_cancelled = True
        if hasattr(self, 'startup_loader'):
            self.startup_loader.shutdown()
        if hasattr(self, 'central_quotes_svc'):
            try:
                self.central_quotes_svc.shutdown()
            except Exception as e:
                log.error(f"[关闭] 停止中央报价服务异常: {e}")

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

        if hasattr(self, 'tab_scan') and getattr(self.tab_scan, 'worker', None):
            try:
                if self.tab_scan.worker.isRunning():
                    self.tab_scan.cancel_scan()
                    self.tab_scan.worker.wait(2000)
            except Exception as e:
                log.error(f"[关闭] 停止VCP扫描线程异常: {e}")

        if hasattr(self, '_auto_rt_timer'):
            self._auto_rt_timer.stop()

        try:
            task_manager.shutdown()
        except Exception as e:
            log.error(f"[关闭] TaskManager 关停异常: {e}")

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
            if pct == 100 or pct == 0:
                import gc
                QTimer.singleShot(3000, lambda: gc.collect())

    # ================================================================
    # EventBus 信号处理（各 Tab 组件广播的信号）
    # ================================================================
    def _on_show_kline(self, code: str):
        """响应简单K线图请求（无上下文列表）"""
        self._on_show_kline_with_list(code, [], 0)

    def _on_show_kline_with_list(self, code: str, code_list: list, current_idx: int):
        """响应带列表上下文的 K 线图请求 — 委托给 KLineWindowManager (#1)"""
        name = getattr(self.data_provider, 'code2name', {}).get(code, code)
        vcp_data = {'code': code, 'name': name}
        if code_list and 0 <= current_idx < len(code_list):
            item_data = code_list[current_idx]
            if isinstance(item_data, dict):
                if item_data.get('代码') == code:
                    name = item_data.get('名称', name)
                vcp_data = dict(item_data)

        # 核心需求：全局 VCP 状态穿透投影
        # 如果从其他 Tab (如龙虎榜、美股) 打开 K 线，只要它在当前的 VCP 扫描结果中
        # 就自动把它在 VCP 表格中的突破点、箱体等画线数据合并进来
        if getattr(self, 'tab_scan', None) and hasattr(self.tab_scan, '_current_results'):
            for scan_res in self.tab_scan._current_results:
                if isinstance(scan_res, dict) and scan_res.get('代码') == code:
                    for k, v in scan_res.items():
                        if k not in vcp_data or not vcp_data.get(k):
                            vcp_data[k] = v
                    break

        kline_manager.open_chart(
            main_window=self,
            code=code,
            name=name,
            data_provider=self.data_provider,
            vcp_data=vcp_data,
            code_list=code_list,
            current_idx=current_idx,
        )

    # ================================================================
    # 主题切换系统
    # ================================================================
    def _apply_theme(self, _theme_name: str = ""):
        """主题切换时的全局刷新回调"""
        from ui.styles.global_qss import generate_global_qss
        from ui.theme import theme_manager

        t = theme_manager.current_theme

        # 1. 重新生成并应用全局 QSS
        qss = generate_global_qss()
        self.setStyleSheet(qss)
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPalette, QColor
        if QApplication.instance():
            pal = QApplication.style().standardPalette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                pal.setColor(group, QPalette.ColorRole.ToolTipBase, QColor(t['BG_ELEVATED']))
                pal.setColor(group, QPalette.ColorRole.ToolTipText, QColor(t['TEXT_PRIMARY']))
            QApplication.instance().setPalette(pal)
            QApplication.instance().setStyleSheet(qss)
            QToolTip.hideText()
            QToolTip.setPalette(pal)

        # 2. 刷新壳层与状态栏
        apply_chrome_theme(self)
        if hasattr(self, '_status_bar_widget') and self._status_bar_widget:
            self._status_bar_widget.apply_theme()

        for widget in (
            self,
            getattr(self, '_custom_titlebar', None),
            getattr(self, '_status_bar_widget', None),
            getattr(self, '_standalone_tabbar', None),
            getattr(self, 'tabs_wrapper', None),
            getattr(self, 'btn_sys_menu', None),
        ):
            if widget:
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

        # 3. 通知用户
        from ui.components.toast_widget import show_toast
        show_toast(f"已切换至「{theme_manager.current_theme_name}」主题", "success", self, duration=2000)

