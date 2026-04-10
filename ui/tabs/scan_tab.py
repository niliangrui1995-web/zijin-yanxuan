import os
import datetime
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox, QToolButton
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, QTimer
# Removed unused imports from ui.theme and PyQt6

from ui.models.table_models import StockTableModel, RtSortFilterProxyModel, StockItemDelegate
from ui.components import VCPTableView
from ui.components.scan_dialogs import VCPScanRangeDialog, VCPScanSettingsDialog
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
        self._scan_cancel_requested = False

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

    def _get_scan_params(self) -> dict:
        return {
            "rps": int(self.spn_scan_rps.value()),
            "amp": float(self.spn_scan_amp.value()),
            "ma_bind": float(self.spn_scan_ma_bind.value()),
            "amount": float(self.spn_scan_amount.value()),
            "high250": float(self.spn_scan_high250.value()),
        }

    def _apply_scan_params(self, params: dict):
        self.spn_scan_rps.setValue(int(params.get("rps", 80)))
        self.spn_scan_amp.setValue(float(params.get("amp", 0.45)))
        self.spn_scan_ma_bind.setValue(float(params.get("ma_bind", 0.05)))
        self.spn_scan_amount.setValue(float(params.get("amount", 0.8)))
        self.spn_scan_high250.setValue(float(params.get("high250", 0.10)))

    def _save_scan_params(self):
        params = self._get_scan_params()
        self._settings.setValue("rps_threshold", params["rps"])
        self._settings.setValue("amp_threshold", params["amp"])
        self._settings.setValue("ma_bind_threshold", params["ma_bind"])
        self._settings.setValue("min_amount", params["amount"])
        self._settings.setValue("high_250_threshold", params["high250"])
        self._settings.sync()

    def _load_user_presets(self) -> dict:
        user_presets_raw = self._settings.value("user_presets", "{}")
        try:
            return json.loads(user_presets_raw) if isinstance(user_presets_raw, str) else {}
        except (json.JSONDecodeError, TypeError) as exc:
            log.debug(f"[扫描参数] 用户预设解析失败: {exc}")
            return {}

    def _save_user_presets(self, user_presets: dict):
        self._settings.setValue("user_presets", json.dumps(user_presets, ensure_ascii=False))
        self._settings.sync()

    def _set_scan_action_state(self, state: str):
        if state == "running":
            self.btn_scan_action.setText("终止VCP扫描")
            self.btn_scan_action.setEnabled(True)
            self.lbl_scan_status.setText("正在扫描...")
        elif state == "stopping":
            self.btn_scan_action.setText("正在终止...")
            self.btn_scan_action.setEnabled(False)
            self.lbl_scan_status.setText("正在终止...")
        else:
            self.btn_scan_action.setText("执行VCP扫描")
            self.btn_scan_action.setEnabled(True)
            self.lbl_scan_status.setText("")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 搜索过滤工具栏
        scan_toolbar = QWidget()
        stb_layout = QHBoxLayout(scan_toolbar)
        stb_layout.setContentsMargins(8, 6, 8, 6)
        
        lbl_title = QLabel("🎯 VCP 扫描")
        lbl_title.setObjectName("tabTitle")
        stb_layout.addWidget(lbl_title)

        self.lbl_scan_status = QLabel("")
        self.lbl_scan_status.setObjectName("tabSubtitle")
        stb_layout.addWidget(self.lbl_scan_status)

        stb_layout.addStretch()

        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("🔍 输入代码/名称筛选...")
        self.scan_search.setFixedWidth(200)
        self.scan_search.textChanged.connect(self._on_search_text_changed)
        stb_layout.addWidget(self.scan_search)

        self.btn_scan_action = QPushButton("执行VCP扫描")
        self.btn_scan_action.setObjectName("primaryButton")
        self.btn_scan_action.clicked.connect(self._on_scan_action_clicked)
        stb_layout.addWidget(self.btn_scan_action)

        # 扫描参数设置按钮
        self.btn_scan_settings = QToolButton()
        self.btn_scan_settings.setText("⚙")
        self.btn_scan_settings.setFixedSize(32, 32)
        self.btn_scan_settings.setObjectName("btnSysMenu")
        self.btn_scan_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_settings.setToolTip("VCP扫描参数设置")
        self.btn_scan_settings.clicked.connect(self._show_scan_settings)
        stb_layout.addWidget(self.btn_scan_settings)
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
                source_idx = self.proxy_model.mapToSource(model.index(r, 0))
                row_data = self.source_model.get_row_data(source_idx.row()) if source_idx.isValid() else None
                if isinstance(row_data, dict):
                    row_data = dict(row_data)
                    row_data.setdefault('代码', c_code)
                    row_data.setdefault('名称', c_name)
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

    def _on_scan_action_clicked(self):
        if self.worker and self.worker.isRunning():
            self.cancel_scan()
            return

        dlg = VCPScanRangeDialog(self)
        if dlg.exec() != VCPScanRangeDialog.DialogCode.Accepted:
            return

        start_date, end_date = dlg.selected_range()
        self.start_scan(start_date, end_date)

    def _show_scan_settings(self):
        dlg = VCPScanSettingsDialog(self._get_scan_params(), self._load_user_presets(), self)
        if dlg.exec() != VCPScanSettingsDialog.DialogCode.Accepted:
            return

        self._apply_scan_params(dlg.values())
        self._save_scan_params()
        self._save_user_presets(dlg.user_presets())
        show_toast("VCP 扫描参数已保存", "success", self)

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

        self._scan_cancel_requested = False
        self._set_scan_action_state("running")
        event_bus.sig_task_progress.emit("scan", 1, "准备扫描...")

        self._current_results = []
        self.source_model.update_data([])

        params = VCPParams(
            rps_threshold=self.spn_scan_rps.value(),
            amp_threshold=self.spn_scan_amp.value(),
            ma_bind_threshold=self.spn_scan_ma_bind.value(),
            high_250_threshold=self.spn_scan_high250.value(),
            min_amount_20d=self.spn_scan_amount.value() * 1e8,
        )

        self._save_scan_params()

        self.worker = ScanWorker(self.data_provider, self.engine, sd, ed, params)
        self.worker.progress.connect(lambda p, m: event_bus.sig_task_progress.emit("scan", p, m))
        self.worker.result_ready.connect(self._on_scan_results)
        self.worker.finished_scan.connect(self._on_scan_finished)
        self.worker.finished.connect(self._on_worker_thread_finished)
        self.worker.start()

    def cancel_scan(self):
        if self.worker and self.worker.isRunning() and not self._scan_cancel_requested:
            self._scan_cancel_requested = True
            self._set_scan_action_state("stopping")
            self.worker.cancel()
            return True
        return False

    def _on_scan_finished(self, success, msg):
        if success:
            self._save_scan_cache(self._current_results)
        event_bus.sig_task_progress.emit("scan", 100 if success else 0, msg)

    def _on_worker_thread_finished(self):
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self._scan_cancel_requested = False
        self._set_scan_action_state("idle")

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
                elif "关注" in name_str or code_str in fav_codes:
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

        # 提取 VCP 数据用于关注池附带信息
        vcp_data = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)
        if not isinstance(vcp_data, dict):
            vcp_data = None

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(
            self, code, name,
            vcp_data=vcp_data,
        )

    # _launch_tdx 已迁移至 BaseStockTab 基类
