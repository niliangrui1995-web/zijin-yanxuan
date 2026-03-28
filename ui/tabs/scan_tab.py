import os
import datetime
import json
import pandas as pd
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QAbstractItemView, QMessageBox, QDialog, QFileDialog, QMenu, QToolButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW,
    COLOR_SUCCESS, COLOR_ERROR, apply_rise_fall_color, apply_score_color
)

from ui.components import NumericTableWidgetItem
from ui.workers import ScanWorker
from vcp.engine import VCPParams
from core.event_bus import event_bus
from core.task_manager import task_manager
from core.logger import get_logger
from vcp.constants import SPECIAL_LATEST_DATA
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

class ScanTab(BaseStockTab):
    """
    静态扫描 (VCP 区间扫描) 独立组件
    包含扫描渲染、策略表格、本地JSON缓存，并通过事件总线驱动进度。
    """
    def __init__(self, data_provider, engine, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.engine = engine
        self._current_results = []
        self.worker = None

        self._init_settings_widgets()
        self._init_ui()
        
        # 启动时自动加载上次缓存的扫描结果
        QTimer.singleShot(300, self._load_scan_cache)

    def _init_settings_widgets(self):
        """初始化扫描策略的内部存储控件，避免抛出 AttributeError"""
        self.spn_scan_rps = QSpinBox()
        self.spn_scan_rps.setRange(50, 99)
        self.spn_scan_rps.setValue(80)

        self.spn_scan_amp = QDoubleSpinBox()
        self.spn_scan_amp.setRange(0.1, 1.5)
        self.spn_scan_amp.setSingleStep(0.05)
        self.spn_scan_amp.setValue(0.45)

        self.spn_scan_ma_bind = QDoubleSpinBox()
        self.spn_scan_ma_bind.setRange(0.01, 0.2)
        self.spn_scan_ma_bind.setSingleStep(0.01)
        self.spn_scan_ma_bind.setValue(0.05)

        self.spn_scan_amount = QDoubleSpinBox()
        self.spn_scan_amount.setRange(0.1, 50.0)
        self.spn_scan_amount.setSingleStep(0.5)
        self.spn_scan_amount.setValue(1.5)

        self.spn_scan_high250 = QDoubleSpinBox()
        self.spn_scan_high250.setRange(0.01, 1.0)
        self.spn_scan_high250.setSingleStep(0.05)
        self.spn_scan_high250.setValue(0.10)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 搜索过滤工具栏
        scan_toolbar = QWidget()
        stb_layout = QHBoxLayout(scan_toolbar)
        stb_layout.setContentsMargins(6, 4, 6, 4)
        
        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("🔍 输入代码/名称筛选...")
        self.scan_search.setFixedHeight(32)
        self.scan_search.textChanged.connect(self._on_search_text_changed)
        stb_layout.addWidget(self.scan_search)
        
        btn_export_scan = QPushButton("📄 导出 (Ctrl+E)")
        btn_export_scan.setProperty("class", "secondary")
        btn_export_scan.setFixedHeight(32)
        btn_export_scan.clicked.connect(self.export_table_to_excel)
        stb_layout.addWidget(btn_export_scan)
        
        # 扫描参数设置按钮
        btn_scan_settings = QToolButton()
        btn_scan_settings.setText("⚙")
        btn_scan_settings.setFixedSize(32, 32)
        btn_scan_settings.setStyleSheet("""
            QToolButton { font-size: 16px; border: none; color: #6B7280; background: transparent; margin-top: 4px; }
            QToolButton:hover { color: #A78BFA; background: rgba(139, 92, 246, 0.1); border-radius: 4px; }
            QToolButton:pressed { color: #8B5CF6; }
        """)
        btn_scan_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_scan_settings.setToolTip("扫描参数设置")
        btn_scan_settings.clicked.connect(self._show_scan_settings)
        stb_layout.addWidget(btn_scan_settings)
        layout.addWidget(scan_toolbar)

        # 表格控件
        self.table_scan = QTableWidget()
        self.table_scan.setColumnCount(12)
        headers = ["序号", "代码", "日期", "名称", "收盘价", "评分", "RPS强度", "市值", "距突破", "突破状态", "区间振幅", "热门板块"]
        self.table_scan.setHorizontalHeaderLabels(headers)
        
        # 表格自适应和交互设置
        self.table_scan.verticalHeader().setVisible(False)      
        self.table_scan.setAlternatingRowColors(True)           
        self.table_scan.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows) 
        self.table_scan.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)    
        self.table_scan.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)        
        self.table_scan.setShowGrid(False)                      
        self.table_scan.setStyleSheet(self.table_scan.styleSheet() + "::item { padding: 0px 10px; }")
        
        # 绑定双击事件:广播调取K线图信号 (交由主窗口或专门的图表控制器来处理)
        self.table_scan.itemDoubleClicked.connect(
            lambda item: event_bus.sig_show_kline.emit(self.table_scan.item(item.row(), 1).text())
            if self.table_scan.item(item.row(), 1) else None
        )
        
        # 右键菜单
        self.table_scan.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_scan.customContextMenuRequested.connect(self._show_context_menu)
        
        # 挂载快捷键: 拦截回车与空格模拟双击
        original_keypress = self.table_scan.keyPressEvent
        def table_key_press(event):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                curr_item = self.table_scan.currentItem()
                if curr_item:
                    code_item = self.table_scan.item(curr_item.row(), 1)
                    if code_item:
                        event_bus.sig_show_kline.emit(code_item.text())
                event.accept()
            else:
                original_keypress(event)
        self.table_scan.keyPressEvent = table_key_press

        # 列宽策略
        scan_weights = [0.5, 0.8, 0.9, 1.2, 0.8, 1.0, 1.0, 0.7, 0.9, 1.0, 0.7, 2.5]
        header = self.table_scan.horizontalHeader()
        header.setStretchLastSection(False)
        for col_idx, w in enumerate(scan_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_scan.setColumnWidth(col_idx, int(w * 80))
        header.setSectionResizeMode(11, QHeaderView.ResizeMode.Stretch)
        
        # 行高 (加强呼吸感)
        self.table_scan.verticalHeader().setDefaultSectionSize(40)
        self.table_scan.setSortingEnabled(True)
        self.table_scan.horizontalHeader().setSortIndicatorShown(True)
        self.table_scan.horizontalHeader().sectionClicked.connect(
            lambda: QTimer.singleShot(50, lambda: self._renumber_column(self.table_scan, 0))
        )

        layout.addWidget(self.table_scan)

    def _on_search_text_changed(self, text):
        from ui.components import SearchFilter
        SearchFilter.filter_table(self.table_scan, text, code_col=1, name_col=3)

    def _renumber_column(self, table, col_idx):
        for r in range(table.rowCount()):
            item = table.item(r, col_idx)
            if item:
                item.setText(str(r + 1))

    def _show_scan_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 扫描策略参数")
        dlg.setFixedSize(360, 300)
        dlg.setStyleSheet("QDialog { background-color: #151820; } QLabel { color: #C9CDD4; font-size: 13pt; }")
        
        form = QVBoxLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(12)
        
        rows = [
            ("RPS 阈值:", self.spn_scan_rps, ""),
            ("振幅上限:", self.spn_scan_amp, "← 0.45=45%"),
            ("均线粘合:", self.spn_scan_ma_bind, "← 0.05=5%"),
            ("成交额(亿):", self.spn_scan_amount, ""),
            ("距250日高:", self.spn_scan_high250, "← 0.10=10%")
        ]
        
        for label_text, widget, hint in rows:
            r = QHBoxLayout()
            r.addWidget(QLabel(label_text))
            r.addWidget(widget)
            if hint:
                r.addWidget(QLabel(hint))
            form.addLayout(r)
            
        form.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("class", "secondary")
        btn_ok.setFixedHeight(32)
        btn_ok.clicked.connect(dlg.accept)
        form.addWidget(btn_ok)
        dlg.exec()

    # ==========================
    # 核心引擎调度与任务生命周期
    # ==========================
    def start_scan(self, sd: str, ed: str):
        if self.worker is not None and self.worker.isRunning():
            return
            
        sd = sd.replace('-', '')
        ed = ed.replace('-', '')
        if len(sd) == 8: sd = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
        if len(ed) == 8: ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

        # 广播 UI 状态更新让主窗口锁定按钮
        event_bus.sig_task_progress.emit("scan", 0, "start")
        
        self.table_scan.setSortingEnabled(False)
        self.table_scan.setRowCount(0)

        params = VCPParams(
            rps_threshold=self.spn_scan_rps.value(),
            amp_threshold=self.spn_scan_amp.value(),
            ma_bind_threshold=self.spn_scan_ma_bind.value(),
            high_250_threshold=self.spn_scan_high250.value(),
            min_amount_20d=self.spn_scan_amount.value() * 1e8,
        )
        
        self.worker = ScanWorker(self.data_provider, self.engine, sd, ed, params)
        self.worker.progress.connect(lambda p, m: event_bus.sig_task_progress.emit("scan", p, m))
        self.worker.result_ready.connect(self._on_scan_results)
        self.worker.finished_scan.connect(self._on_scan_finished)
        self.worker.start()

    def cancel_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            return True
        return False

    def _on_scan_finished(self, success, msg):
        self._save_scan_cache(self._current_results)
        event_bus.sig_task_progress.emit("scan", 100 if success else 0, msg)

    def _on_scan_results(self, results):
        if not results: return
        self._current_results = results
        self._render_scan_table(results)

    # ==========================
    # 数据渲染逻辑
    # ==========================
    def _render_scan_table(self, results):
        if not results: return
        try:
            df_res = pd.DataFrame(results).sort_values('触发日期').drop_duplicates(subset=['代码'], keep='last')
            if '评分' in df_res.columns:
                df_res['评分_tmp'] = pd.to_numeric(df_res['评分'], errors='coerce')
                df_res = df_res.sort_values(by=['触发日期', '评分_tmp'], ascending=[False, False])
                df_res = df_res.drop(columns=['评分_tmp'])
            final_list = df_res.to_dict('records')
            self._current_results = final_list
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"数据整理失败: {e}")
            final_list = results
            
        fav_codes = set()
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    fav_codes = set(json.load(f).keys())
            except Exception: pass

        try:
            self.table_scan.setSortingEnabled(False)
            self.table_scan.setRowCount(len(final_list))
            for row_idx, row_data in enumerate(final_list):
                code_str = str(row_data.get('代码', ''))
                name_str = str(row_data.get('名称', ''))
                if code_str in fav_codes:
                    name_str = f"⭐ {name_str}"
                    
                def _safe_float_str(val, fmt="{:.2f}"):
                    try: return fmt.format(float(val))
                    except (ValueError, TypeError): return str(val)

                display_row = [
                    str(row_idx + 1), code_str, str(row_data.get('触发日期', '')), name_str,
                    _safe_float_str(row_data.get('收盘', 0)), str(row_data.get('评分', '')),
                    str(row_data.get('RPS强度', '')), str(row_data.get('市值', '')),
                    str(row_data.get('距突破', '')), str(row_data.get('突破状态', '')),
                    str(row_data.get('区间振幅', '')), str(row_data.get('热点板块', '-'))
                ]
                
                for col_idx, text in enumerate(display_row):
                    if col_idx in (0, 4, 5, 6, 7, 8, 10): 
                        item = NumericTableWidgetItem(text)
                    else:
                        item = QTableWidgetItem(text)
                    item.setForeground(QColor(COLOR_FLAT))
                    
                    if col_idx == 0:
                        item.setData(Qt.ItemDataRole.UserRole, row_data)
                    
                    if col_idx in [0, 4, 5, 6, 7, 8, 10]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    elif col_idx in [3, 11]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    else:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                    
                    # 着色
                    if col_idx == 3 and text.startswith("⭐ "):
                        item.setForeground(QColor(STATUS_APPROACHING))
                    elif col_idx == 5:
                        try:
                            score = float(text)
                            if score >= 90:
                                item.setText(f"⭐ {text}"); item.setForeground(QColor(STATUS_APPROACHING))
                                font = item.font(); font.setBold(True); item.setFont(font)
                            elif score >= 80: item.setForeground(QColor(STATUS_APPROACHING))
                            elif score < 70: item.setForeground(QColor(STATUS_INACTIVE))
                        except Exception: pass
                    elif col_idx == 8:
                        try:
                            val = float(text.replace('%', '').replace('+', ''))
                            self.apply_pct_color(item, val)
                        except Exception: pass
                    elif col_idx == 9:
                        if "放量突破" in text:
                            item.setText(f"🚀 {text}"); item.setForeground(QColor(COLOR_RISE_STRONG))
                            font = item.font(); font.setBold(True); item.setFont(font)
                        elif "临近" in text:
                            item.setText(f"⏳ {text}"); item.setForeground(QColor(COLOR_WARNING))
                        elif "假突破" in text:
                            item.setText(f"⚠️ {text}"); item.setForeground(QColor(COLOR_FALL_STRONG))

                    self.table_scan.setItem(row_idx, col_idx, item)
                    
                # 突破整行暗红
                if "放量突破" in str(row_data.get('突破状态', '')):
                    for c_h in range(12):
                        cell = self.table_scan.item(row_idx, c_h)
                        if cell: cell.setBackground(QColor(232, 93, 93, 20))

            self.table_scan.setSortingEnabled(True) 
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"渲染表格错误: {e}")
            self.table_scan.setSortingEnabled(True)

    def export_table_to_excel(self):
        if self.table_scan.rowCount() == 0:
            QMessageBox.information(self, "提示", "当前表格为空,无法导出")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出扫描结果", f"扫描结果_{datetime.date.today().strftime('%Y%m%d')}.xlsx", "Excel Files (*.xlsx)")
        if not path: return
        try:
            headers = [self.table_scan.horizontalHeaderItem(c).text() for c in range(self.table_scan.columnCount())]
            rows = []
            for r in range(self.table_scan.rowCount()):
                row = []
                for c in range(self.table_scan.columnCount()):
                    item = self.table_scan.item(r, c)
                    row.append(item.text() if item else "")
                rows.append(row)
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(path, index=False, engine='openpyxl')
            event_bus.sig_system_log.emit("info", f"✅ 已导出 {len(rows)} 条扫描记录至 {path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ==========================
    # 扫描结果本地缓存
    # ==========================
    def _get_scan_cache_path(self) -> str:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, 'scan_cache.json')

    def _save_scan_cache(self, results: list):
        if not results: return
        try:
            cache_path = self._get_scan_cache_path()
            cache_data = {'saved_at': datetime.datetime.now().isoformat(), 'count': len(results), 'results': results}
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2, default=str)
            log.info(f"[扫描缓存] 已保存 {len(results)} 条结果")
        except Exception as e:
            log.error(f"[扫描缓存] 保存失败: {e}")

    def _load_scan_cache(self):
        try:
            cache_path = self._get_scan_cache_path()
            if not os.path.exists(cache_path): return
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            results = cache_data.get('results', [])
            if not results: return
            saved_at = cache_data.get('saved_at', '未知')
            self._current_results = results
            self._render_scan_table(results)
            event_bus.sig_system_log.emit("info", f"[扫描缓存] 已加载 {len(results)} 条记录 (保存于 {saved_at[:16]})")
            event_bus.sig_task_progress.emit("scan", 100, f"已加载 {len(results)} 条扫描缓存")
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[扫描缓存] 加载失败: {e}")

    # ==========================
    # 右键菜单
    # ==========================
    def _show_context_menu(self, pos):
        """扫描结果表格右键菜单"""
        item = self.table_scan.itemAt(pos)
        if not item:
            return
        row = item.row()
        code_item = self.table_scan.item(row, 1)
        name_item = self.table_scan.item(row, 3)
        if not code_item or not name_item:
            return

        code = code_item.text()
        name = name_item.text().replace("⭐ ", "")

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

        # 读取关注池状态
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
        menu.addSeparator()
        act_export = menu.addAction("📤 导出当前表")

        action = menu.exec(self.table_scan.viewport().mapToGlobal(pos))

        if action == act_chart:
            event_bus.sig_show_kline.emit(code)
        elif action == act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(code)
            event_bus.sig_system_log.emit("info", f"已复制: {code}")
        elif action == act_special:
            # 获取该行的 VCP 数据
            vcp_data = None
            seq_item = self.table_scan.item(row, 0)
            if seq_item:
                vcp_data = seq_item.data(Qt.ItemDataRole.UserRole)
            event_bus.sig_watchlist_changed.emit(
                "remove" if is_fav else "add", code
            )
            # 直接操作关注池 JSON 文件
            self._toggle_special_file(code, name, is_fav, vcp_data)
        elif action == act_tdx:
            self._launch_tdx(code)
        elif action == act_ai:
            event_bus.sig_open_ai_diag.emit(code, 'ai')
        elif action == act_local:
            event_bus.sig_open_ai_diag.emit(code, 'local')
        elif action == act_export:
            self.export_table_to_excel()

    def _toggle_special_file(self, code: str, name: str, is_fav: bool, vcp_data=None):
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
            entry = {"现价": 0, "涨幅%": 0, "评分": ""}
            if vcp_data and isinstance(vcp_data, dict):
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
            # 通知关注池 Tab 重新加载
            event_bus.sig_watchlist_changed.emit("toggle", code)
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"关注池操作异常: {e}")

    # _launch_tdx 已迁移至 BaseStockTab 基类
