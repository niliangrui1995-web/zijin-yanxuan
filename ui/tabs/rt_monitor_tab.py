import os
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel, QLineEdit, QMenu,
    QAbstractItemView, QMessageBox, QDialog, QComboBox, QSpinBox, QToolButton
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW,
    COLOR_SUCCESS, COLOR_ERROR, apply_rise_fall_color, apply_score_color
)

from ui.components import NumericTableWidgetItem
from ui.workers import RtScanWorker
from vcp.constants import SPECIAL_LATEST_DATA
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

class RtMonitorTab(BaseStockTab):
    """
    盘中监控 独立组件 (Controller + View)
    负责独立的盘中轮询逻辑，表格渲染。
    """
    def __init__(self, data_provider, engine, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.engine = engine
        self._init_ui()
        self._init_settings_widgets()

    def _init_settings_widgets(self):
        # 初始化设置参数的内部储存控件
        self.cmb_rt_interval = QComboBox()
        self.cmb_rt_interval.addItems(["30秒", "1分钟", "3分钟", "5分钟"])
        self.spn_rt_rps = QSpinBox()
        self.spn_rt_rps.setRange(50, 99)
        self.spn_rt_rps.setValue(80)

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
        self.btn_rt_start.clicked.connect(self._toggle_rt_monitor)
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

        # 表格控件
        self.table_rt = QTableWidget()
        self.table_rt.setColumnCount(11)
        headers = ["代码", "时间", "名称", "现价", "涨幅%", "评分", "RPS强度", "突破状态", "市值", "区间振幅", "热点板块"]
        self.table_rt.setHorizontalHeaderLabels(headers)
        
        self.table_rt.verticalHeader().setVisible(False)
        self.table_rt.setAlternatingRowColors(True)
        self.table_rt.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_rt.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_rt.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_rt.setShowGrid(False)
        self.table_rt.setStyleSheet(self.table_rt.styleSheet() + "::item { padding: 0px 10px; }")
        
        # 自适应列宽
        rt_weights = [0.75, 0.65, 1.4, 0.75, 0.9, 0.55, 0.8, 1.5, 0.65, 0.8, 2.0]
        header = self.table_rt.horizontalHeader()
        header.setStretchLastSection(False)
        for col_idx, w in enumerate(rt_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_rt.setColumnWidth(col_idx, int(w * 80))
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        self.table_rt.verticalHeader().setDefaultSectionSize(40) # 视觉重构版行高
        self.table_rt.setSortingEnabled(True)
        self.table_rt.horizontalHeader().setSortIndicatorShown(True)

        # 右键菜单
        self.table_rt.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_rt.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_rt)

    def _clear_table(self):
        self.table_rt.setRowCount(0)
        self.lbl_rt_info.setText("记录已清空")

    def _on_search_text_changed(self, text):
        from ui.components import SearchFilter
        SearchFilter.filter_table(self.table_rt, text, code_col=0, name_col=2)

    def _show_rt_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 盘中监控参数")
        dlg.setFixedSize(300, 160)
        dlg.setStyleSheet("QDialog { background-color: #151820; } QLabel { color: #C9CDD4; font-size: 13pt; }")
        
        form = QVBoxLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("刷新间隔:"))
        row1.addWidget(self.cmb_rt_interval)
        form.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("RPS 阈值:"))
        row2.addWidget(self.spn_rt_rps)
        form.addLayout(row2)
        
        form.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("class", "secondary")
        btn_ok.setFixedHeight(32)
        btn_ok.clicked.connect(dlg.accept)
        form.addWidget(btn_ok)
        dlg.exec()

    def _toggle_rt_monitor(self):
        if hasattr(self, 'rt_worker') and self.rt_worker.isRunning():
            self.rt_worker.stop()
            self.rt_worker.wait()
            self.btn_rt_start.setText("🚀 启动盘中监控")
            self.btn_rt_start.setStyleSheet("")
            self.lbl_rt_info.setText("监控已停止")
            event_bus.sig_task_progress.emit("rt_monitor", 0, "stop")
        else:
            if not self.data_provider.cache_data:
                QMessageBox.warning(self, "数据未就绪", "请先执行扫描或按 F5 加载数据后再启动盘中监控.")
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
        interval_text = self.cmb_rt_interval.currentText()
        interval_map = {"30秒": 30, "1分钟": 60, "3分钟": 180, "5分钟": 300}
        interval_sec = interval_map.get(interval_text, 30)
        rps_threshold = int(self.spn_rt_rps.value())

        self.rt_worker = RtScanWorker(self.data_provider, self.engine, interval=interval_sec, rps_threshold=rps_threshold)
        self.rt_worker.rt_result_ready.connect(self._update_rt_table)
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
        QMessageBox.warning(self, "无法启动", "无法连接通达信行情服务器.请检查网络连接后重试.")

    def _update_rt_table(self, results):
        rt_only = [r for r in results if not r.get('_is_special')]
        self.table_rt.setSortingEnabled(False)
        self.table_rt.setRowCount(len(rt_only))
        for row_idx, res in enumerate(rt_only):
            row_data = [
                res.get('代码', ''), res.get('时间', ''), res.get('名称', ''),
                str(res.get('现价', '')), str(res.get('涨幅%', '')), str(res.get('评分', '--')),
                str(res.get('RPS强度', '')), str(res.get('突破状态', '')), str(res.get('市值', '')),
                str(res.get('区间振幅', '')), str(res.get('热点板块', ''))
            ]
            for col_idx, text in enumerate(row_data):
                if col_idx in (3, 4, 5, 6, 8, 9):
                    item = NumericTableWidgetItem(str(text))
                else:
                    item = QTableWidgetItem(str(text))
                item.setForeground(QColor(COLOR_FLAT))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                
                if col_idx == 4:
                    try:
                        pct = float(str(res.get('涨幅%', 0)).replace('%', '').replace('+', ''))
                        self.apply_pct_color(item, pct)
                    except: pass
                elif col_idx == 7:  
                    st = str(text)
                    if "放量突破" in st:
                        item.setText(f"🚀 {st}"); item.setForeground(QColor(COLOR_RISE_STRONG))
                        f = item.font(); f.setBold(True); item.setFont(f)
                    elif "缩量突破" in st:
                        item.setText(f"⚠️ {st}"); item.setForeground(QColor(COLOR_WARNING))
                        f = item.font(); f.setBold(True); item.setFont(f)
                    elif "临近" in st:
                        item.setText(f"⏳ {st}"); item.setForeground(QColor(STATUS_APPROACHING))
                    elif "VCP蓄力" in st:
                        item.setForeground(QColor(STATUS_VCP))
                    elif "非红盘" in st or "异常" in st or "一字" in st or "观望" in st:
                        item.setForeground(QColor(STATUS_INACTIVE))

                if col_idx in (7, 10):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table_rt.setItem(row_idx, col_idx, item)
        self.table_rt.setSortingEnabled(True)
        self.table_rt.sortByColumn(4, Qt.SortOrder.DescendingOrder)

        # 核心解耦点：表格自身渲染完毕后，向全系统抛出数据刷新事件！
        # 让 MainWindowQT 或者 WatchlistTab 自行拦截处理剩下的全局关联逻辑。
        event_bus.sig_data_updated.emit("rt_quotes_refreshed", results)

    # ================================================================
    # 右键菜单
    # ================================================================
    def _show_context_menu(self, pos):
        """盘中监控表格右键菜单"""
        item = self.table_rt.itemAt(pos)
        if not item:
            return
        row = item.row()
        code_item = self.table_rt.item(row, 0)
        name_item = self.table_rt.item(row, 2)
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

        fav_codes = set()
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    fav_codes = set(json.load(f).keys())
            except Exception:
                pass
        is_fav = code in fav_codes
        act_special = menu.addAction("⭐ 移出关注池" if is_fav else "⭐ 加入关注池")
        menu.addSeparator()
        act_tdx = menu.addAction("🖥️ 跳转通达信")
        menu.addSeparator()
        act_ai = menu.addAction("🤖 AI深度诊断")
        act_local = menu.addAction("🧪 本地技术诊断")

        action = menu.exec(self.table_rt.viewport().mapToGlobal(pos))

        if action == act_chart:
            event_bus.sig_show_kline.emit(code)
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(code)
            event_bus.sig_system_log.emit("info", f"已复制: {code}")
        elif action == act_special:
            self._toggle_special_file(code, name, is_fav)
        elif action == act_tdx:
            self._launch_tdx(code)
        elif action == act_ai:
            event_bus.sig_open_ai_diag.emit(code, 'ai')
        elif action == act_local:
            event_bus.sig_open_ai_diag.emit(code, 'local')

    def _toggle_special_file(self, code: str, name: str, is_fav: bool):
        """直接操作关注池 JSON 文件"""
        current_data = {}
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    current_data = json.load(f)
            except Exception:
                pass
        if is_fav:
            if code in current_data:
                del current_data[code]
            event_bus.sig_system_log.emit("info", f"[{name}] 已移出关注池")
        else:
            current_data[code] = {"现价": 0, "涨幅%": 0, "评分": ""}
            event_bus.sig_system_log.emit("info", f"[{name}] 已加入关注池")
        try:
            with open(SPECIAL_LATEST_DATA, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, ensure_ascii=False, indent=4)
            event_bus.sig_watchlist_changed.emit("toggle", code)
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"关注池操作异常: {e}")

    # _launch_tdx 已迁移至 BaseStockTab 基类

