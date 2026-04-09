import os
import datetime
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView,
    QHeaderView, QPushButton, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QAbstractItemView, QDialog, QFileDialog, QToolButton
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, QTimer
# Removed unused imports from ui.theme and PyQt6

from ui.models.table_models import StockTableModel, RtSortFilterProxyModel, StockItemDelegate
from ui.components import VCPTableView
from ui.workers.scan_worker import ScanWorker
from vcp.engine import VCPParams
from core.event_bus import event_bus
from core.logger import get_logger
from ui.viewmodels.watchlist_vm import watchlist_vm
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
        
        # 统一订阅及后台刷新
        self.subscribe_global_quotes()

    def _init_settings_widgets(self):
        """初始化扫描策略的内部存储控件，从 QSettings 恢复上次参数 (#8)"""
        from PyQt6.QtCore import QSettings
        self._settings = QSettings("VCPHunter", "ScanTab")

        self.spn_scan_rps = QSpinBox()
        self.spn_scan_rps.setRange(50, 99)
        self.spn_scan_rps.setValue(self._settings.value("rps_threshold", 80, type=int))

        self.spn_scan_amp = QDoubleSpinBox()
        self.spn_scan_amp.setRange(0.1, 1.5)
        self.spn_scan_amp.setSingleStep(0.05)
        self.spn_scan_amp.setValue(self._settings.value("amp_threshold", 0.45, type=float))

        self.spn_scan_ma_bind = QDoubleSpinBox()
        self.spn_scan_ma_bind.setRange(0.01, 0.2)
        self.spn_scan_ma_bind.setSingleStep(0.01)
        self.spn_scan_ma_bind.setValue(self._settings.value("ma_bind_threshold", 0.05, type=float))

        self.spn_scan_amount = QDoubleSpinBox()
        self.spn_scan_amount.setRange(0.1, 50.0)
        self.spn_scan_amount.setSingleStep(0.5)
        self.spn_scan_amount.setValue(self._settings.value("min_amount", 0.8, type=float))

        self.spn_scan_high250 = QDoubleSpinBox()
        self.spn_scan_high250.setRange(0.01, 1.0)
        self.spn_scan_high250.setSingleStep(0.05)
        self.spn_scan_high250.setValue(self._settings.value("high_250_threshold", 0.10, type=float))

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 搜索过滤工具栏
        scan_toolbar = QWidget()
        stb_layout = QHBoxLayout(scan_toolbar)
        stb_layout.setContentsMargins(8, 6, 8, 6)
        
        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("🔍 输入代码/名称筛选...")
        self.scan_search.setFixedHeight(28)
        self.scan_search.textChanged.connect(self._on_search_text_changed)
        stb_layout.addWidget(self.scan_search)
        
        btn_export_scan = QPushButton("📄 导出 (Ctrl+E)")
        btn_export_scan.setProperty("class", "secondary")
        btn_export_scan.setFixedHeight(28)
        btn_export_scan.clicked.connect(self.export_table_to_excel)
        stb_layout.addWidget(btn_export_scan)
        
        # 扫描参数设置按钮
        btn_scan_settings = QToolButton()
        btn_scan_settings.setText("⚙")
        btn_scan_settings.setFixedSize(32, 32)
        from ui.theme import theme_manager as _tm
        _t = _tm.current_theme
        btn_scan_settings.setStyleSheet(f"""
            QToolButton {{ font-size: 16px; border: none; color: {_t['TEXT_MUTED']}; background: transparent; margin-top: 4px; }}
            QToolButton:hover {{ color: {_t['TEXT_BRIGHT']}; background: {_t['BG_HOVER']}; border-radius: 4px; }}
            QToolButton:pressed {{ color: {_t['TEXT_PRIMARY']}; }}
        """)
        btn_scan_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_scan_settings.setToolTip("扫描参数设置")
        btn_scan_settings.clicked.connect(self._show_scan_settings)
        stb_layout.addWidget(btn_scan_settings)
        layout.addWidget(scan_toolbar)

        # 表格控件 (MVC)
        self.columns = ["代码", "名称", "现价", "涨幅%", "市值", "触发日期", "评分", "RPS强度", "距突破", "突破状态", "区间振幅", "热门板块"]
        self.source_model = StockTableModel(self.columns)
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)

        self.table_scan = VCPTableView(default_row_height=28)
        self.table_scan.setModel(self.proxy_model)
        self.table_scan.setItemDelegate(StockItemDelegate(self.table_scan))
        
        # 绑定双击事件:广播调取K线图信号，带上前后文以便K线图能够「上一只」「下一只」滑动
        self.table_scan.doubleClicked.connect(self._handle_show_kline)
        
        # 右键菜单
        self.table_scan.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_scan.customContextMenuRequested.connect(self._show_context_menu)
        
        # 挂载快捷键: 拦截回车与空格模拟双击
        original_keypress = self.table_scan.keyPressEvent
        def table_key_press(event):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                curr_idx = self.table_scan.currentIndex()
                if curr_idx.isValid():
                    self._handle_show_kline(curr_idx)
                event.accept()
            else:
                original_keypress(event)
        self.table_scan.keyPressEvent = table_key_press

        # 列宽策略 (QSettings 持久化)
        header = self.table_scan.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        self.table_scan.setSortingEnabled(True)
        
        scan_weights = [0.8, 0.9, 0.8, 0.8, 0.7, 1.2, 1.0, 1.0, 0.9, 1.0, 0.7, 2.5]
        for col_idx, w in enumerate(scan_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_scan.setColumnWidth(col_idx, int(w * 80))
        header.setSectionResizeMode(11, QHeaderView.ResizeMode.Stretch)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table_scan, "header_state_scan_v2")
        
        # 强制默认按第5列（触发日期）降序排序（由近到远），覆盖掉持久化中可能记录的其他排序列
        self.table_scan.sortByColumn(5, Qt.SortOrder.DescendingOrder)
        
        layout.addWidget(self.table_scan)

    def _handle_show_kline(self, index=None):
        if index is None or not index.isValid(): return
        model = index.model()
        row = index.row()
        code_idx = model.index(row, 0)
        current_code = model.data(code_idx, Qt.ItemDataRole.DisplayRole)
        if not current_code: return
        
        code_list = []
        visual_rows = []
        
        # 构建当前经过筛选后(未隐藏)的股票列表，支持在K线中翻页
        for r in range(model.rowCount()):
            c_code = model.data(model.index(r, 0), Qt.ItemDataRole.DisplayRole)
            c_name = model.data(model.index(r, 1), Qt.ItemDataRole.DisplayRole)
            if c_code:
                # Pull full user dict for that row
                row_data = model.data(model.index(r, 0), Qt.ItemDataRole.UserRole)
                if not isinstance(row_data, dict):
                    row_data = {'代码': c_code, '名称': c_name}
                    
                code_list.append(row_data)
                visual_rows.append(r)
                    
        try:
            current_idx = visual_rows.index(row)
            event_bus.sig_show_kline_with_list.emit(current_code, code_list, current_idx)
        except ValueError:
            event_bus.sig_show_kline.emit(current_code)

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)

    def _show_scan_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙ 扫描策略参数")
        dlg.setFixedSize(400, 390)
        from ui.theme import theme_manager as _tm
        _t = _tm.current_theme
        dlg.setStyleSheet(f"QDialog {{ background-color: {_t['BG_MENU']}; }} QLabel {{ color: {_t['TEXT_PRIMARY']}; font-size: 13pt; }}")
        
        form = QVBoxLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(12)

        # #16: 策略预设下拉框
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设方案:"))
        combo_preset = QComboBox()
        combo_preset.setFixedHeight(28)
        from ui.theme import theme_manager as _tm
        _t = _tm.current_theme
        combo_preset.setStyleSheet(f"QComboBox {{ background: {_t['BG_INPUT']}; color: {_t['TEXT_PRIMARY']}; border: 1px solid {_t['BORDER_INPUT']}; }}")

        # 内置预设
        _builtin_presets = {
            "VCP 标准": {"rps": 80, "amp": 0.45, "ma_bind": 0.05, "amount": 0.8, "high250": 0.10},
            "激进 (低门槛)": {"rps": 60, "amp": 0.60, "ma_bind": 0.08, "amount": 0.3, "high250": 0.20},
            "保守 (严筛选)": {"rps": 90, "amp": 0.30, "ma_bind": 0.03, "amount": 2.0, "high250": 0.08},
        }
        # 从 QSettings 加载用户自定义预设
        user_presets_raw = self._settings.value("user_presets", "{}")
        try:
            user_presets = json.loads(user_presets_raw) if isinstance(user_presets_raw, str) else {}
        except (json.JSONDecodeError, TypeError) as _e:
            log.debug(f"[扫描参数] 用户预设解析失败: {_e}")
            user_presets = {}
        all_presets = {**_builtin_presets, **user_presets}

        combo_preset.addItem("-- 选择预设 --")
        for preset_name in all_presets:
            combo_preset.addItem(preset_name)
        preset_row.addWidget(combo_preset)

        btn_save_preset = QPushButton("💾 保存")
        btn_save_preset.setFixedSize(60, 28)
        btn_save_preset.setProperty("class", "secondary")
        preset_row.addWidget(btn_save_preset)
        form.addLayout(preset_row)

        # 参数控件行
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

        def _on_preset_selected(idx):
            name = combo_preset.currentText()
            preset = all_presets.get(name)
            if not preset:
                return
            self.spn_scan_rps.setValue(int(preset.get("rps", 80)))
            self.spn_scan_amp.setValue(float(preset.get("amp", 0.45)))
            self.spn_scan_ma_bind.setValue(float(preset.get("ma_bind", 0.05)))
            self.spn_scan_amount.setValue(float(preset.get("amount", 0.8)))
            self.spn_scan_high250.setValue(float(preset.get("high250", 0.10)))

        combo_preset.currentIndexChanged.connect(_on_preset_selected)

        def _on_save_preset():
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(dlg, "保存预设", "预设名称:")
            if ok and name.strip():
                new_preset = {
                    "rps": self.spn_scan_rps.value(),
                    "amp": self.spn_scan_amp.value(),
                    "ma_bind": self.spn_scan_ma_bind.value(),
                    "amount": self.spn_scan_amount.value(),
                    "high250": self.spn_scan_high250.value(),
                }
                user_presets[name.strip()] = new_preset
                self._settings.setValue("user_presets", json.dumps(user_presets, ensure_ascii=False))
                # 刷新下拉列表
                if name.strip() not in [combo_preset.itemText(i) for i in range(combo_preset.count())]:
                    combo_preset.addItem(name.strip())
                all_presets[name.strip()] = new_preset

        btn_save_preset.clicked.connect(_on_save_preset)
            
        form.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("class", "secondary")
        btn_ok.setFixedHeight(28)
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
        
        self.source_model.update_data([])

        params = VCPParams(
            rps_threshold=self.spn_scan_rps.value(),
            amp_threshold=self.spn_scan_amp.value(),
            ma_bind_threshold=self.spn_scan_ma_bind.value(),
            high_250_threshold=self.spn_scan_high250.value(),
            min_amount_20d=self.spn_scan_amount.value() * 1e8,
        )

        # #8: 持久化当前参数到 QSettings，下次启动自动恢复
        self._settings.setValue("rps_threshold", self.spn_scan_rps.value())
        self._settings.setValue("amp_threshold", self.spn_scan_amp.value())
        self._settings.setValue("ma_bind_threshold", self.spn_scan_ma_bind.value())
        self._settings.setValue("min_amount", self.spn_scan_amount.value())
        self._settings.setValue("high_250_threshold", self.spn_scan_high250.value())
        
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
        import pandas as pd
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
            
        fav_codes = set(watchlist_vm.get_all_codes())
        formatted_list = []

        try:
            for row_idx, row_data in enumerate(final_list):
                code_str = str(row_data.get('代码', ''))
                name_str = str(row_data.get('名称', ''))
                if code_str in fav_codes:
                    name_str = f"⭐ {name_str}"
                    
                def _safe_float_str(val, fmt="{:.2f}"):
                    try: return fmt.format(float(val))
                    except (ValueError, TypeError): return str(val)

                status = str(row_data.get('突破状态', ''))
                row_style = ""
                if "放量突破" in status:
                    row_style = "breakout"
                elif "临近" in status:
                    row_style = "approaching"
                elif "假突破" in status or "缩量" in status:
                    row_style = "fake_breakout"
                elif "关注" in name_str or "⭐" in name_str:
                    row_style = "approaching"
                    
                # Format score cleanly
                score_str = str(row_data.get('评分', ''))
                try:
                    _ = float(score_str)
                except (ValueError, TypeError):
                    pass

                formatted_row = {
                    "代码": code_str,
                    "名称": name_str,
                    "现价": _safe_float_str(row_data.get('收盘', 0)),
                    "涨幅%": "--", # Historical static scan lacks intraday % change originally
                    "触发日期": str(row_data.get('触发日期', '')),
                    "评分": score_str,
                    "RPS强度": str(row_data.get('RPS强度', '')),
                    "市值": str(row_data.get('市值', '')),
                    "距突破": str(row_data.get('距突破', '')),
                    "突破状态": status,
                    "区间振幅": str(row_data.get('区间振幅', '')),
                    "热门板块": str(row_data.get('热点板块', '-')),
                    "_row_style": row_style, # Using background dye injected to StockTableModel
                }
                # Keep original data nested so double clicks can retrieve it
                for k, v in row_data.items():
                    if k not in formatted_row:
                        formatted_row[k] = v
                        
                formatted_list.append(formatted_row)
                
            self.source_model.update_data(formatted_list)
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"渲染表格错误: {e}")

    def export_table_to_excel(self):
        import pandas as pd  # 修复: pd 未在模块顶层导入，此处需局部导入
        proxy = self.proxy_model
        if proxy.rowCount() == 0:
            show_toast("当前表格为空,无法导出", "warning", self)
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出扫描结果", f"扫描结果_{datetime.date.today().strftime('%Y%m%d')}.xlsx", "Excel Files (*.xlsx)")
        if not path: return
        try:
            # Reconstruct table from Proxy Model
            headers = self.columns
            rows = []
            for r in range(proxy.rowCount()):
                row = []
                for c in range(len(headers)):
                    idx = proxy.index(r, c)
                    val = proxy.data(idx, Qt.ItemDataRole.DisplayRole)
                    row.append(val if val else "")
                rows.append(row)
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(path, index=False, engine='openpyxl')
            show_toast(f"✅ 已导出 {len(df)} 条扫描记录至 {path}", "success", self)
        except Exception as e:
            show_toast(f"导出失败: {str(e)}", "error", self)

    # ==========================
    # 扫描结果本地缓存 (SQLite)
    # ==========================
    def _save_scan_cache(self, results: list):
        if not results: return
        try:
            from core.data_store import DataStore
            params_snapshot = {
                'rps': self.spn_scan_rps.value(),
                'amp': self.spn_scan_amp.value(),
                'ma_bind': self.spn_scan_ma_bind.value(),
                'amount': self.spn_scan_amount.value(),
                'high250': self.spn_scan_high250.value(),
            }
            cache_data = {
                'saved_at': datetime.datetime.now().isoformat(),
                'count': len(results),
                'params': params_snapshot,
                'results': results,
            }
            DataStore().save_json("scan_cache", cache_data)
            log.info(f"[扫描缓存] 已保存 {len(results)} 条结果至 SQLite")
        except Exception as e:
            log.error(f"[扫描缓存] 保存失败: {e}")

    def _load_scan_cache(self):
        try:
            from core.data_store import DataStore
            cache_data = DataStore().load_json("scan_cache")
            
            # 如果 SQLite 没查到，尝试兼容旧的 JSON 并自动迁入
            if not cache_data:
                data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
                old_path = os.path.join(data_dir, 'scan_cache.json')
                if os.path.exists(old_path):
                    import json
                    with open(old_path, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    if isinstance(cache_data, dict) and cache_data:
                        DataStore().save_json("scan_cache", cache_data)
                        try:
                            # 迁移完成后重命名打上印记
                            os.rename(old_path, old_path + ".migrated")
                            log.info("[扫描缓存] 旧版的 scan_cache.json 已自动迁移入 SQLite")
                        except OSError as _e:
                            log.debug(f"[扫描缓存] 迁移文件重命名失败: {_e}")

            if not isinstance(cache_data, dict): return
            results = cache_data.get('results', [])
            if not results: return
            
            saved_at = cache_data.get('saved_at', '未知')
            self._current_results = results
            self._render_scan_table(results)

            # #9: 回显参数快照，让用户知道这批结果用的什么参数
            params_info = cache_data.get('params')
            params_hint = ""
            if params_info and isinstance(params_info, dict):
                params_hint = (f" | RPS≥{params_info.get('rps', '?')}"
                               f" 振幅≤{int(params_info.get('amp', 0)*100)}%"
                               f" 均线粘合≤{int(params_info.get('ma_bind', 0)*100)}%")

            event_bus.sig_system_log.emit(
                "info",
                f"[扫描缓存] 已加载 {len(results)} 条记录 (保存于 {saved_at[:16]}){params_hint}"
            )
            event_bus.sig_task_progress.emit("scan", 100, f"已加载 {len(results)} 条扫描缓存")
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[扫描缓存] 加载失败: {e}")

    # ==========================
    # 右键菜单
    # ==========================
    def _show_context_menu(self, pos):
        """扫描结果表格右键菜单 — 委托给统一菜单工厂"""
        index = self.table_scan.indexAt(pos)
        if not index.isValid():
            return
            
        model = index.model()
        row = index.row()
        code = model.data(model.index(row, 0), Qt.ItemDataRole.DisplayRole)
        name = model.data(model.index(row, 1), Qt.ItemDataRole.DisplayRole)
        if not code or not name:
            return

        name = name.replace("⭐ ", "")

        # 提取 VCP 数据用于关注池附带信息
        vcp_data = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)
        if not isinstance(vcp_data, dict):
            vcp_data = None

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(
            self, code, name,
            vcp_data=vcp_data,
            show_export=True,
            export_callback=self.export_table_to_excel,
        )

    # _launch_tdx 已迁移至 BaseStockTab 基类
