from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView,
    QHeaderView, QPushButton, QLabel, QLineEdit,
    QAbstractItemView, QDialog, QComboBox, QSpinBox, QToolButton
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from ui.models.table_models import RtTableModel, RtSortFilterProxyModel
from ui.workers.rt_scan_worker import RtScanWorker
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab
from core.throttler import SignalThrottler

log = get_logger(__name__)

class RtMonitorTab(BaseStockTab):
    """
    盘中监控 独立组件 (Controller + View)
    负责独立的盘中轮询逻辑，表格渲染。
    """
    def __init__(self, data_provider, engine, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.engine = engine
        from PyQt6.QtCore import QSettings
        self._settings = QSettings("VCPHunter", "RtMonitorTab")
        self._init_ui()
        
        # 核心：实时数据流 UI 防抖拦截器 (针对未来的海量 tick 数据)
        self._rt_throttler = SignalThrottler(interval=1000, parent=self)
        self._rt_throttler.throttled_signal.connect(self._do_update_rt_table)
        
        # 盘中监控由 RtScanWorker 独立推送数据，不订阅中央广播站
        # 盘后 Worker 停止后，model 中的数据原地保留，第二天自动覆盖
        
        # 自动化监控：交易日 9:15-16:00 自动启动
        self._manually_toggled = False
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._check_auto_start_stop)
        self._auto_timer.start(30000)  # 每 30 秒检查一次

    def _check_auto_start_stop(self):
        from core.market_calendar import MarketCalendar
        is_active = MarketCalendar.is_market_active()
        is_running = hasattr(self, 'rt_worker') and self.rt_worker.isRunning()

        # 如果在活跃时间，没在运行，且用户没有手动强行关掉它 -> 自动启动
        if is_active and not is_running and not self._manually_toggled:
            log.info("[盘中监控] 交易时段到达，触发自动启动...")
            self._toggle_rt_monitor(auto=True)
        # 如果不在活跃时间，但它还在运行 -> 自动关掉静默
        elif not is_active and is_running:
            log.info("[盘中监控] 非交易时段，触发自动静默...")
            self._toggle_rt_monitor(auto=True)
            self._manually_toggled = False  # 清除人工标记，确保明早能自动启动

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Toolbar
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)
        self.btn_rt_start = QPushButton("🚀 启动盘中监控")
        self.btn_rt_start.setObjectName("primaryButton")
        self.btn_rt_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rt_start.setFixedWidth(150)
        self.btn_rt_start.clicked.connect(lambda *args: self._toggle_rt_monitor())
        tb_layout.addWidget(self.btn_rt_start)
        
        self.lbl_rt_info = QLabel("尚未启动监控")
        self.lbl_rt_info.setStyleSheet("color: #6B7280; font-size: 12px;")
        tb_layout.addWidget(self.lbl_rt_info)
        tb_layout.addStretch()
        
        # 搜索过滤
        self.rt_search = QLineEdit()
        self.rt_search.setPlaceholderText("🔍 筛选...")
        self.rt_search.setFixedWidth(150)
        self.rt_search.setFixedHeight(32)
        self.rt_search.textChanged.connect(self._on_search_text_changed)
        tb_layout.addWidget(self.rt_search)
        
        # 清空盘中记录按钮
        self.btn_rt_clear = QPushButton("🗑 清空")
        self.btn_rt_clear.setProperty("class", "secondary")
        self.btn_rt_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rt_clear.setFixedHeight(32)
        self.btn_rt_clear.clicked.connect(self._clear_table)
        tb_layout.addWidget(self.btn_rt_clear)
        
        # 盘中监控参数设置按钮
        btn_rt_settings = QToolButton()
        btn_rt_settings.setText("⚙")
        btn_rt_settings.setFixedSize(32, 32)
        btn_rt_settings.setStyleSheet("""
            QToolButton { font-size: 16px; border: none; color: #6B7280; background: transparent; margin-top: 4px; }
            QToolButton:hover { color: #A78BFA; background: rgba(139, 92, 246, 0.1); border-radius: 4px; }
            QToolButton:pressed { color: #8B5CF6; }
        """)
        btn_rt_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rt_settings.setToolTip("盘中监控参数设置")
        btn_rt_settings.clicked.connect(self._show_rt_settings)
        tb_layout.addWidget(btn_rt_settings)
        layout.addWidget(toolbar)

        # 表格控件 MVC
        self.source_model = RtTableModel()
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        
        self.table_rt = QTableView()
        self.table_rt.setModel(self.proxy_model)
        
        self.table_rt.verticalHeader().setVisible(False)
        self.table_rt.setAlternatingRowColors(True)
        self.table_rt.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_rt.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_rt.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_rt.setShowGrid(False)
        self.table_rt.setStyleSheet(self.table_rt.styleSheet() + "::item { padding: 0px 10px; }")
        
        # 自适应列宽 (匹配 headers=["代码","名称","现价","涨幅%","市值","时间","评分","RPS强度","突破状态","区间振幅","热点板块"])
        rt_weights = [0.8, 1.4, 0.8, 0.8, 0.7, 0.7, 0.6, 0.8, 1.5, 0.8, 2.0]
        header = self.table_rt.horizontalHeader()
        header.setStretchLastSection(False)
        for col_idx, w in enumerate(rt_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
        
        try:
            col_count = self.source_model.columnCount()
            # Bug#6 修复: 使用固定基准宽度，避免未定义 base_w
            base_width = 100  # 基准列宽 100px
            for col_idx in range(col_count):
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                if col_idx < len(rt_weights):
                    w = int(base_width * rt_weights[col_idx])
                    self.table_rt.setColumnWidth(col_idx, w)
        except Exception as e:
            log.warning(f"[盘中监控] 列宽初始化异常: {e}")
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        self.table_rt.verticalHeader().setDefaultSectionSize(40) # 视觉重构版行高
        self.table_rt.setSortingEnabled(True)
        self.table_rt.horizontalHeader().setSortIndicatorShown(True)
        
        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table_rt, "header_state_rt_v4")
        
        # 绑定双击事件，广播K线上下文
        self.table_rt.doubleClicked.connect(self._on_table_double_clicked)

        # 右键菜单
        self.table_rt.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_rt.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_rt)

    def _clear_table(self):
        self.source_model.update_data([])
        self.lbl_rt_info.setText("记录已清空")

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)

    def _show_rt_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 盘中监控参数")
        dlg.setFixedSize(300, 160)
        dlg.setStyleSheet("QDialog { background-color: #151820; } QLabel { color: #C9CDD4; font-size: 13pt; }")
        
        form = QVBoxLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        
        cmb_interval = QComboBox()
        cmb_interval.addItems(["30秒", "1分钟", "3分钟", "5分钟"])
        cmb_interval.setCurrentText(str(self._settings.value("interval", "30秒")))
        
        spn_rps = QSpinBox()
        spn_rps.setRange(50, 99)
        spn_rps.setValue(int(self._settings.value("rps", 80)))

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("刷新间隔:"))
        row1.addWidget(cmb_interval)
        form.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("RPS 阈值:"))
        row2.addWidget(spn_rps)
        form.addLayout(row2)
        
        form.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("class", "secondary")
        btn_ok.setFixedHeight(32)
        btn_ok.clicked.connect(dlg.accept)
        form.addWidget(btn_ok)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._settings.setValue("interval", cmb_interval.currentText())
            self._settings.setValue("rps", spn_rps.value())
            self._settings.sync()
            show_toast("盘中监控参数已保存", "success", self)

    def _toggle_rt_monitor(self, auto=False):
        if not auto: 
            self._manually_toggled = True

        if hasattr(self, 'rt_worker') and self.rt_worker.isRunning():
            self.rt_worker.stop()
            self.rt_worker.wait(3000)  # 3 秒超时，防止 worker 死锁卡死主线程
            self.btn_rt_start.setText("🚀 启动盘中监控")
            self.btn_rt_start.setStyleSheet("")
            self.lbl_rt_info.setText("监控已停止" if not auto else "监控自动休眠(非盘中)")
            event_bus.sig_task_progress.emit("rt_monitor", 0, "stop")
        else:
            # 兜底：如果内存缓存为空，先尝试从磁盘加载昨日F5的缓存
            if not self.data_provider.cache_data:
                self.lbl_rt_info.setText("正在加载磁盘缓存...")
                try:
                    cache_date = self.data_provider.load_cache_from_disk()
                    if cache_date and self.data_provider.cache_data:
                        log.info(f"[盘中监控] 缓存为空，从磁盘自动加载成功(日期: {cache_date})")
                except Exception as e:
                    log.error(f"[盘中监控] 磁盘缓存自动加载失败: {e}")
            if not self.data_provider.cache_data:
                show_toast("请先执行扫描或按 F5 加载数据后再启动", "warning", self)
                self.lbl_rt_info.setText("尚未启动监控")
                return
            
            if not self.data_provider.server_pool or not self.data_provider.is_online():
                self.lbl_rt_info.setText("正在尝试连接行情服务器...")
                self.btn_rt_start.setEnabled(False)
                def _try_connect():
                    ok = self.data_provider.test_network(timeout=5)
                    if ok:
                        self.data_provider.set_online_mode(True)
                    return ok

                def _on_connect_result(ok):
                    if ok:
                        self._on_rt_network_ready()
                    else:
                        self._on_rt_network_failed()

                def _on_connect_error(msg):
                    event_bus.sig_system_log.emit("error", f"[盘中监控] 联网异常: {msg}")
                    self._on_rt_network_failed()

                task_manager.run_in_background(
                    _try_connect,
                    on_success=_on_connect_result,
                    on_error=_on_connect_error,
                    task_id="rt_connect"
                )
                return
            self._start_rt_worker()

    def _start_rt_worker(self):
        interval_text = str(self._settings.value("interval", "30秒"))
        interval_map = {"30秒": 30, "1分钟": 60, "3分钟": 180, "5分钟": 300}
        interval_sec = interval_map.get(interval_text, 30)
        rps_threshold = int(self._settings.value("rps", 80))

        self.rt_worker = RtScanWorker(self.data_provider, self.engine, interval=interval_sec, rps_threshold=rps_threshold)
        # 拦截：工作线程的高频抛出不再直接刷新界面，只喂给 throttler
        self.rt_worker.rt_result_ready.connect(lambda data: self._rt_throttler.trigger(data))
        self.rt_worker.progress.connect(self.lbl_rt_info.setText)
        self.rt_worker.scan_count.connect(lambda n, pool: event_bus.sig_system_log.emit("info", f"[监控] 第{n}轮 | 待突破池 {pool} 只"))
        
        self.rt_worker.start()
        
        self.btn_rt_start.setText("⏹ 停止盘中监控")
        self.btn_rt_start.setStyleSheet("color: white; border: none; background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #DC2626, stop:1 #EF4444);")
        self.lbl_rt_info.setText("正在启动盘中监控...")
        # 发出事件告知主窗口左侧导航栏同步状态
        event_bus.sig_task_progress.emit("rt_monitor", 1, "start")

    @pyqtSlot()
    def _on_rt_network_ready(self):
        self.btn_rt_start.setEnabled(True)
        event_bus.sig_network_status_changed.emit(True, "Online")
        event_bus.sig_system_log.emit("info", "[盘中监控] 启动前自动联网成功")
        self._start_rt_worker()

    @pyqtSlot()
    def _on_rt_network_failed(self):
        self.btn_rt_start.setEnabled(True)
        self.lbl_rt_info.setText("联网失败,无法启动")
        show_toast("无法连接通达信行情服务器", "error", self)

    def _do_update_rt_table(self, results):
        """实际执行刷新：这部分现在由于 Throttler 保护，每秒最多只执行一次"""
        rt_only = [r for r in results if not r.get('_is_special')]
        try:
            self.source_model.update_data(rt_only)
        except Exception as e:
            log.error(f"Failed to update rt table: {e}")

        # 核心解耦点：表格自身渲染完毕后，向全系统抛出数据刷新事件！
        # 让 MainWindowQT 或者 WatchlistTab 自行拦截处理剩下的全局关联逻辑。
        event_bus.sig_rt_quotes_refreshed.emit(results)

    # ================================================================
    # 交互事件 (右键菜单 / 双击)
    # ================================================================
    def _on_table_double_clicked(self, idx):
        if not idx.isValid(): return
        proxy_row = idx.row()
        
        # 提取当前所有过滤后的结果构建上下文列表
        code_list = []
        for r in range(self.proxy_model.rowCount()):
            c_idx = self.proxy_model.index(r, 0) # 代码
            n_idx = self.proxy_model.index(r, 1) # 名称（第1列，不是第2列"现价"）
            c = str(self.proxy_model.data(c_idx))
            n = str(self.proxy_model.data(n_idx))
            code_list.append({'代码': c, '名称': n})
            
        current_code = str(self.proxy_model.data(self.proxy_model.index(proxy_row, 0)))
        event_bus.sig_show_kline_with_list.emit(current_code, code_list, proxy_row)

    def _show_context_menu(self, pos):
        """盘中监控表格右键菜单 — 委托给统一菜单工厂 (#2)"""
        index = self.table_rt.indexAt(pos)
        if not index.isValid():
            return
            
        source_index = self.proxy_model.mapToSource(index)
        row_data = self.source_model.get_row_data(source_index.row())
        if not row_data:
            return

        code = str(row_data.get('代码', ''))
        name = str(row_data.get('名称', ''))
        if not code:
            return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(
            self, code, name,
            vcp_data=row_data,
        )

    # _launch_tdx 已迁移至 BaseStockTab 基类

