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
from ui.components import VCPTableView, TableStateWrapper
from ui.components.scan_dialogs import VCPScanRangeDialog, VCPScanSettingsDialog
from ui.workers.scan_worker import ScanWorker
from vcp.engine import VCPParams
from core.market_calendar import MarketCalendar
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
        self._scan_mode = "full"
        self._scan_target_date = ""
        self._last_incremental_stats = None

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

    def _scan_param_segments(self) -> tuple[str, str, str]:
        params = self._get_scan_params()
        return (
            f"RPS≥{params['rps']}",
            f"振幅≤{int(params['amp'] * 100)}%",
            f"均线≤{int(params['ma_bind'] * 100)}%",
        )

    def _latest_scan_trigger_date(self) -> str:
        dates = [
            str(item.get("触发日期", "")).strip()
            for item in (self._current_results or [])
            if isinstance(item, dict)
        ]
        dates = [item for item in dates if item]
        return max(dates) if dates else ""

    @staticmethod
    def _normalize_scan_date(value) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "")
        return text[:8] if len(text) >= 8 else text

    def _infer_cache_latest_trade_date(self) -> str:
        latest_date = ""
        cache_data = getattr(self.data_provider, "cache_data", {}) or {}
        for df in cache_data.values():
            if df is None:
                continue
            try:
                if len(df) <= 0:
                    continue
            except Exception:
                continue

            try:
                index = getattr(df, "index", None)
                if index is None or len(index) <= 0:
                    continue
                last_value = index[-1]
            except Exception:
                continue

            try:
                if hasattr(last_value, "strftime"):
                    date_str = last_value.strftime("%Y%m%d")
                else:
                    date_str = str(last_value).strip().replace("-", "")[:8]
                if len(date_str) == 8 and date_str.isdigit() and date_str > latest_date:
                    latest_date = date_str
            except Exception:
                continue
        return latest_date

    def _resolve_incremental_scan_date(self) -> str:
        latest_cache_date = self._infer_cache_latest_trade_date()
        if latest_cache_date:
            return f"{latest_cache_date[:4]}-{latest_cache_date[4:6]}-{latest_cache_date[6:]}"

        trade_date = MarketCalendar.get_latest_trade_date("CN")
        if trade_date is None:
            trade_date = MarketCalendar.today("CN")
        return trade_date.isoformat()

    def _merge_scan_results(self, base_results: list, incoming_results: list) -> tuple[list, dict]:
        merged: dict[str, dict] = {}
        for row in (base_results or []):
            if not isinstance(row, dict):
                continue
            code = str(row.get("代码", "")).strip()
            if code:
                merged[code] = dict(row)

        stats = {"原始命中": len(incoming_results or []), "新增": 0, "更新": 0, "刷新": 0, "忽略": 0}
        for row in (incoming_results or []):
            if not isinstance(row, dict):
                continue
            code = str(row.get("代码", "")).strip()
            if not code:
                continue

            candidate = dict(row)
            current = merged.get(code)
            if current is None:
                merged[code] = candidate
                stats["新增"] += 1
                continue

            current_date = self._normalize_scan_date(current.get("触发日期", ""))
            candidate_date = self._normalize_scan_date(candidate.get("触发日期", ""))
            if candidate_date >= current_date:
                merged[code] = candidate
                if candidate_date > current_date:
                    stats["更新"] += 1
                else:
                    stats["刷新"] += 1
            else:
                stats["忽略"] += 1

        return list(merged.values()), stats

    def _build_incremental_finish_message(self) -> str:
        stats = self._last_incremental_stats or {}
        target_date = self._scan_target_date or self._resolve_incremental_scan_date()
        current_count = len(self._pending_scan_results or [])
        raw_hits = int(stats.get("原始命中", 0) or 0)
        if raw_hits <= 0:
            return f"新增扫描完成: {target_date} 无新增信号，当前 {current_count} 只"

        updated = int(stats.get("更新", 0) or 0) + int(stats.get("刷新", 0) or 0)
        return (
            f"新增扫描完成: {target_date} 命中 {raw_hits} 条，"
            f"新增 {int(stats.get('新增', 0) or 0)} 只，更新 {updated} 只，当前 {current_count} 只"
        )

    def _refresh_scan_status(self, primary: str | None = None):
        if self._current_results:
            latest_date = self._latest_scan_trigger_date()
            self.lbl_scan_status.setText(
                self.format_status_summary(
                    primary or f"结果 {len(self._current_results)}只",
                    self._status_metric("最近 ", latest_date),
                )
            )
            return

        self.lbl_scan_status.setText(
            self.format_status_summary(primary or "待扫描", *self._scan_param_segments())
        )

    def _set_scan_action_state(self, state: str):
        is_incremental = self._scan_mode == "incremental"
        if state == "running":
            self.btn_scan_action.setText("终止新增扫描" if is_incremental else "终止VCP扫描")
            self.btn_scan_action.setEnabled(True)
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setEnabled(False)
            self.lbl_scan_status.setText(
                self.format_status_summary(
                    "新增扫描中" if is_incremental else "正在扫描",
                    self._status_metric("目标 ", self._scan_target_date) if is_incremental else "",
                    *self._scan_param_segments(),
                )
            )
            if hasattr(self, "table_state"):
                if is_incremental and self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_loading(
                        "新增扫描中..." if is_incremental else "扫描中...",
                        f"正在补扫 {self._scan_target_date}" if is_incremental and self._scan_target_date else "正在计算候选信号",
                    )
        elif state == "stopping":
            self.btn_scan_action.setText("正在终止新增..." if is_incremental else "正在终止...")
            self.btn_scan_action.setEnabled(False)
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setEnabled(False)
            self.lbl_scan_status.setText(
                self.format_status_summary("正在终止", "保留已完成结果", self._status_metric("目标 ", self._scan_target_date))
            )
            if hasattr(self, "table_state"):
                if is_incremental and self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_loading("正在终止...", "正在收尾")
        else:
            self.btn_scan_action.setText("区间扫描")
            self.btn_scan_action.setEnabled(True)
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setText("新增扫描")
                self.btn_scan_increment.setEnabled(True)
            self._refresh_scan_status()
            if hasattr(self, "table_state"):
                if self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_empty("暂无扫描结果")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_scan_status = QLabel()

        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("筛选代码或名称...")
        self.scan_search.setFixedWidth(200)
        self.scan_search.textChanged.connect(self._on_search_text_changed)

        filter_widgets = [self.scan_search]

        self.btn_scan_action = QPushButton("区间扫描")
        self.btn_scan_action.setObjectName("primaryButton")
        self.btn_scan_action.setProperty("toolbarWidthHints", ["区间扫描", "终止VCP扫描", "终止新增扫描", "正在终止...", "正在终止新增..."])
        self.btn_scan_action.clicked.connect(self._on_scan_action_clicked)

        self.btn_scan_increment = QPushButton("新增扫描")
        self.btn_scan_increment.setProperty("class", "secondary")
        self.btn_scan_increment.setProperty("toolbarWidthHints", ["新增扫描"])
        self.btn_scan_increment.setToolTip("只扫描最近可用交易日，并将结果追加/刷新到当前表格")
        self.btn_scan_increment.clicked.connect(self._on_incremental_scan_clicked)

        # 扫描参数设置按钮
        self.btn_scan_settings = QToolButton()
        self.btn_scan_settings.setText("设置")
        self.btn_scan_settings.setProperty("class", "toolbarGhost")
        self.btn_scan_settings.setFixedHeight(32)
        self.btn_scan_settings.setMinimumWidth(56)
        self.btn_scan_settings.setAutoRaise(False)
        self.btn_scan_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_settings.setToolTip("VCP扫描参数设置")
        self.btn_scan_settings.clicked.connect(self._show_scan_settings)

        action_widgets = [self.btn_scan_action, self.btn_scan_increment, self.btn_scan_settings]
        toolbar = self.build_tab_toolbar("VCP 扫描", self.lbl_scan_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)
        self._refresh_scan_status()

        # 表格控件 (MVC)
        self.columns = ["代码", "名称", "现价", "涨幅%", "市值", "触发日期", "评分", "RPS强度", "距突破", "突破状态", "区间振幅", "热门板块"]
        self.source_model = StockTableModel(self.columns)
        self.source_model.set_plain_style_headers(["触发日期"])
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)

        self.table_scan = VCPTableView(default_row_height=30)
        self.table_scan.setModel(self.proxy_model)
        self.table_scan.setItemDelegate(StockItemDelegate(self.table_scan))
        self.table_state = TableStateWrapper(self.table_scan, empty_title="暂无扫描结果", loading_title="扫描中...")

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
        
        scan_weights = [0.55, 0.8, 0.9, 0.8, 0.8, 0.7, 1.2, 1.0, 1.0, 0.9, 1.0, 0.7, 2.5]
        for col_idx, w in enumerate(scan_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_scan.setColumnWidth(col_idx, int(w * 80))
        header.setSectionResizeMode(self.source_model.columnCount() - 1, QHeaderView.ResizeMode.Stretch)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table_scan, "header_state_scan_v3")
        
        # 强制默认按第5列（触发日期）降序排序（由近到远），覆盖掉持久化中可能记录的其他排序列
        self.table_scan.sortByColumn(self.source_model.headers.index("触发日期"), Qt.SortOrder.DescendingOrder)
        
        layout.addWidget(self.table_state)

    def _handle_show_kline(self, index=None):
        if index is None or not index.isValid(): return
        model = index.model()
        row = index.row()
        code_col = self.source_model.headers.index("代码")
        name_col = self.source_model.headers.index("名称")
        code_idx = model.index(row, code_col)
        current_code = model.data(code_idx, Qt.ItemDataRole.DisplayRole)
        if not current_code: return
        
        code_list = []
        visual_rows = []
        
        # 构建当前经过筛选后(未隐藏)的股票列表，支持在K线中翻页
        for r in range(model.rowCount()):
            c_code = model.data(model.index(r, code_col), Qt.ItemDataRole.DisplayRole)
            c_name = model.data(model.index(r, name_col), Qt.ItemDataRole.DisplayRole)
            if c_code:
                # Pull full user dict for that row
                source_idx = self.proxy_model.mapToSource(model.index(r, code_col))
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
        self.start_scan(start_date, end_date, merge_mode=False)

    def _on_incremental_scan_clicked(self):
        if self.worker and self.worker.isRunning():
            self.cancel_scan()
            return

        target_date = self._resolve_incremental_scan_date()
        self.start_scan(target_date, target_date, merge_mode=True)

    def _show_scan_settings(self):
        dlg = VCPScanSettingsDialog(self._get_scan_params(), self._load_user_presets(), self)
        if dlg.exec() != VCPScanSettingsDialog.DialogCode.Accepted:
            return

        self._apply_scan_params(dlg.values())
        self._save_scan_params()
        self._save_user_presets(dlg.user_presets())
        show_toast("VCP 扫描参数已保存", "success", self)
        if not (self.worker and self.worker.isRunning()):
            self._refresh_scan_status()

    # ==========================
    # 核心引擎调度与任务生命周期
    # ==========================
    def start_scan(self, sd: str, ed: str, merge_mode: bool = False):
        if self.worker is not None and self.worker.isRunning():
            return

        sd = sd.replace('-', '')
        ed = ed.replace('-', '')
        if len(sd) == 8: sd = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
        if len(ed) == 8: ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

        self._scan_mode = "incremental" if merge_mode else "full"
        self._scan_target_date = ed if merge_mode else f"{sd} ~ {ed}"
        self._last_incremental_stats = None
        self._scan_cancel_requested = False
        self._set_scan_action_state("running")
        event_bus.sig_task_progress.emit("scan", 1, "准备新增扫描..." if merge_mode else "准备扫描...")
        self._pending_scan_results = None

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
            self._save_scan_cache(self._pending_scan_results or [])
        final_msg = self._build_incremental_finish_message() if success and self._scan_mode == "incremental" else msg
        event_bus.sig_task_progress.emit("scan", 100 if success else 0, final_msg)

    def _on_worker_thread_finished(self):
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self._scan_cancel_requested = False
        self._set_scan_action_state("idle")
        self._scan_mode = "full"
        self._scan_target_date = ""

    def _on_scan_results(self, results):
        incoming_results = results or []
        if self._scan_mode == "incremental":
            merged_results, merge_stats = self._merge_scan_results(self._current_results, incoming_results)
            self._last_incremental_stats = merge_stats
            self._pending_scan_results = merged_results
        else:
            self._pending_scan_results = incoming_results
        self._current_results = self._pending_scan_results
        self._render_scan_table(self._pending_scan_results)


    # ==========================
    # 数据渲染逻辑
    # ==========================
    def _render_scan_table(self, results):
        if not results:
            self._current_results = []
            self.source_model.update_data([])
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无扫描结果")
            self._refresh_scan_status("本次无结果")
            return
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
            if hasattr(self, "table_state"):
                self.table_state.show_table()
            self._refresh_scan_status()
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"渲染表格错误: {e}")

    # ==========================
    # 扫描结果本地缓存 (SQLite)
    # ==========================
    def _save_scan_cache(self, results: list):
        try:
            from core.data_store import DataStore
            store = DataStore()
            if not results:
                store.delete_key("scan_cache")
                log.info("[扫描缓存] 本次扫描无结果，已清空旧缓存")
                return
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
            store.save_json("scan_cache", cache_data)
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
        code_col = self.source_model.headers.index("代码")
        name_col = self.source_model.headers.index("名称")
        code = model.data(model.index(row, code_col), Qt.ItemDataRole.DisplayRole)
        name = model.data(model.index(row, name_col), Qt.ItemDataRole.DisplayRole)
        if not code or not name:
            return

        # 提取 VCP 数据用于关注池附带信息
        source_idx = self.proxy_model.mapToSource(model.index(row, code_col))
        vcp_data = self.source_model.get_row_data(source_idx.row()) if source_idx.isValid() else None
        if not isinstance(vcp_data, dict):
            vcp_data = None

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(
            self, code, name,
            vcp_data=vcp_data,
        )

    # _launch_tdx 已迁移至 BaseStockTab 基类
