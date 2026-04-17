import os

from PyQt6.QtCore import QEvent, QSettings, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QMainWindow, QToolTip, QVBoxLayout, QWidget

from core.app_config import app_config
from core.cache_manager import CacheManager
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager
from ui.components.kline_window_manager import kline_manager
from ui.components.main_window_shell import (
    DraggableTitleBar,
    MainWindowStatusBar,
    inject_standalone_tabbar,
    setup_custom_titlebar,
    setup_system_menu,
)
from ui.main_window_tables import install_table_copy_hooks
from ui.startup_loader import StartupLoader
from ui.workspaces import ClassicWorkspace
from vcp.constants import APP_VERSION, RPS_CACHE_FILE

# 核心引擎与数据层
from vcp.data_provider import TdxDataProvider
from vcp.engine import VCPEngine

log = get_logger(__name__)

__all__ = ["DraggableTitleBar", "MainWindowQT"]


class MainWindowQT(QMainWindow):
    """紫金研选主窗口 — 纯外壳控制器（Phase 2 重构后）"""
    _sig_f5_done = pyqtSignal(int, float)
    _sig_ui_call = pyqtSignal(object)

    @pyqtSlot(object)
    def _run_ui_callback(self, callback):
        try:
            callback()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
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
        self._workspace = None
        self.tabs = None

        self._splash_update(60, "正在构建主界面模块...")
        self.data_provider = TdxDataProvider(offline=True)
        self.data_provider.code2name = self.data_provider.ensure_code_name_map()
        self.engine = VCPEngine.get_instance()

        # 全局样式（动态生成，支持主题切换）
        from ui.styles.global_qss import generate_global_qss
        qss = generate_global_qss()
        self.setStyleSheet(qss)
        from PyQt6.QtGui import QColor, QPalette
        from PyQt6.QtWidgets import QApplication
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
        code_supplier = getattr(self._workspace, "get_realtime_quote_codes", None)
        self.central_quotes_svc = CentralQuotesService(self, self.data_provider, code_supplier=code_supplier)

    # 联网成功后的各 Tab 刷新逻辑由 _on_smart_startup_online_done 负责

    def _on_smart_startup_online_done(self):
        """智能启动联网成功后，触发各Tab的实时数据刷新"""
        from ui.main_window_runtime import safe_run_post_online_refresh

        safe_run_post_online_refresh(self, task_manager)

    # _check_auto_rt_monitor 已删除 — 功能已被 RtMonitorTab._check_auto_start_stop() 完全替代，0 调用方

    def _toggle_network(self):
        from ui.main_window_network import toggle_network

        toggle_network(self)

    def _update_network_ui(self, online: bool, detail: str = ""):
        from ui.main_window_network import update_network_ui

        update_network_ui(self, online, detail=detail)

    def _force_reconnect(self):
        from ui.main_window_network import force_reconnect

        force_reconnect(self)

    def _splash_update(self, value: int, status: str = ""):
        """update progress"""
        if self._splash:
            self._splash.set_progress(value, status)

    def _init_gear_menu(self):
        """在标题栏右侧注入系统菜单。"""
        setup_system_menu(self)
        self._update_last_f5_time()


    def _apply_table_density(self, mode: str, persist: bool = True):
        from ui.main_window_visuals import apply_table_density

        apply_table_density(self, mode, persist=persist)


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
                except (ImportError, RuntimeError):
                    pass
                obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)

    def _show_trade_calendar(self):
        from ui.main_window_visuals import show_trade_calendar

        show_trade_calendar(self)

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
        self._tabs_wrapper_layout = QVBoxLayout(self.tabs_wrapper)
        self._tabs_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_wrapper_layout.setSpacing(0)

        event_bus.sig_rt_quotes_refreshed.connect(self._on_rt_quotes_refreshed)
        event_bus.sig_task_progress.connect(self._on_task_progress)
        event_bus.sig_show_kline.connect(self._on_show_kline)
        event_bus.sig_show_kline_with_list.connect(self._on_show_kline_with_list)

        self._mount_workspace()
        self._init_gear_menu()
        self._inject_tabbar_into_titlebar()
        return


    # _filter_table 已删除 — 各 Tab 已自行实现 proxy_model.setFilterText()，0 调用方


    # _on_table_double_click 已移除(#3)，各 Tab 自行通过 EventBus 广播 K 线请求

    # _show_context_menu 已移除(#2)，各 Tab 使用 stock_context_menu 工厂

    # _launch_tdx / _launch_eastmoney 已移除(#1)
    # 统一由 BaseStockTab 基类提供，避免双份代码维护噩梦

    def _remember_last_active_tab(self, index: int):
        app_config.last_active_tab = index

    def _workspace_tables(self):
        from ui.main_window_runtime import workspace_tables

        return workspace_tables(self)

    def _install_table_copy_hooks(self):
        install_table_copy_hooks(self._workspace_tables())

    def _mount_workspace(self):
        workspace = ClassicWorkspace(self.data_provider, self.engine, host=self, parent=self.tabs_wrapper)

        if self._workspace is not None:
            try:
                self._workspace.shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError) as exc:
                log.error(f"[UI] 停止旧工作区失败: {exc}")
            self._tabs_wrapper_layout.removeWidget(self._workspace)
            self._workspace.deleteLater()

        self._workspace = workspace
        self.tabs = workspace.tabs
        self._tabs_wrapper_layout.addWidget(workspace, 1)
        workspace.restore_last_tab(app_config.last_active_tab)
        self._install_table_copy_hooks()

        try:
            self.tabs.currentChanged.disconnect(self._remember_last_active_tab)
        except (TypeError, RuntimeError):
            pass
        self.tabs.currentChanged.connect(self._remember_last_active_tab)

    def _save_ui_state(self):
        """Persist window geometry with version tag."""
        s = self._settings
        if self.tabs is not None:
            self._remember_last_active_tab(self.tabs.currentIndex())
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
            except (AttributeError, RuntimeError, TypeError) as e:
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
        import datetime
        import os
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
        from ui.main_window_runtime import finish_f5_reload

        finish_f5_reload(self, count=count, elapsed=elapsed, event_bus=event_bus)

    # showEvent 空覆写已删除 — 无自定义逻辑，交给 QMainWindow 默认处理

    def closeEvent(self, event):
        """应用关闭：广播信号让各组件自行保存，然后清理资源"""
        from ui.main_window_runtime import shutdown_main_window

        shutdown_main_window(self, event_bus=event_bus, task_manager=task_manager)

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
        from ui.main_window_runtime import start_f5_precompute

        start_f5_precompute(self, task_manager=task_manager)

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
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
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
        workspace = getattr(self, "_workspace", None)
        scan_res = workspace.find_scan_result(code) if workspace is not None else None
        if isinstance(scan_res, dict):
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
        from ui.main_window_visuals import apply_theme

        apply_theme(self)

