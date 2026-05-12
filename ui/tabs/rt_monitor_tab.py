import re
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
)

from app.services.tab_data_lineage_service import TabDataLineageService
from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from core.throttler import SignalThrottler
from ui.components import TableStateWrapper, VCPTableView
from ui.components.thread_shutdown import request_thread_shutdown
from ui.components.toast_widget import show_toast
from ui.models.table_models import RtSortFilterProxyModel, RtTableModel, StockItemDelegate
from ui.tabs.base_stock_tab import BaseStockTab
from ui.workers.rt_scan_worker import RtScanWorker

log = get_logger(__name__)

class RtMonitorTab(BaseStockTab):
    """
    盘中监控 独立组件 (Controller + View)
    负责独立的盘中轮询逻辑，表格渲染。
    """
    _STATUS_LABELS = {
        "idle": "静默",
        "realtime": "实时",
        "cache": "本地缓存",
        "fallback": "远端失败沿用",
        "error": "错误",
        "working": "处理中",
    }

    def __init__(self, data_provider, engine, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self.engine = engine
        self._settings = app_config.section("rt", legacy_scope="RtMonitorTab")
        self._rt_lineage_service = TabDataLineageService(
            key="rt_monitor",
            source="rt_scan_worker + local_cache + global_store.quotes",
            provider="rt_scan_worker",
            cache_refs=(
                "data/Cache/vcp_rps_precomputed.json",
                "global_store.quotes",
                "local_tdx_cache",
            ),
            provider_status_reader=self._read_provider_status,
        )
        self._last_rt_result = None
        self._last_rt_signature = ""
        self._init_ui()
        self.subscribe_global_quotes(self.source_model)

        # 核心：实时数据流 UI 防抖拦截器 (针对未来的海量 tick 数据)
        self._rt_throttler = SignalThrottler(interval=1000, parent=self)
        self._rt_throttler.throttled_signal.connect(self._do_update_rt_table)

        # 盘中监控由 RtScanWorker 独立推送数据，同时接收中央报价广播补齐同步刷新
        # 盘后 Worker 停止后，model 中的数据原地保留，第二天自动覆盖

        # 自动化监控：按 A 股交易活跃状态自动启停，午休/盘后保持静默
        self._manual_stop_requested = False
        self._manual_stop_trade_date = ""
        self._rt_stop_requested = False
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._check_auto_start_stop)
        self._auto_timer.start(30000)  # 每 30 秒检查一次
        self._rt_status_state = "idle"
        self._rt_status_detail = "未启动"
        self._rt_status_next_step = "点“开始监控”开始"
        self._rt_pool_size = 0
        self._rt_last_update = ""
        self._refresh_rt_header_summary()
        self._set_rt_button_state(False)

    def _is_rt_running(self) -> bool:
        return hasattr(self, 'rt_worker') and self.rt_worker is not None and self.rt_worker.isRunning()

    def _get_interval_seconds(self) -> int:
        interval_text = str(self._settings.value("interval", "30秒"))
        interval_map = {"30秒": 30, "1分钟": 60, "3分钟": 180, "5分钟": 300}
        return interval_map.get(interval_text, 30)

    @staticmethod
    def _now_hhmm() -> str:
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def _manual_stop_reference_date() -> str:
        try:
            from app.services.ui_market_calendar_service import MarketCalendar

            trade_date = MarketCalendar.get_latest_trade_date("CN")
            if trade_date is not None:
                return trade_date.isoformat()
            return MarketCalendar.today("CN").isoformat()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return datetime.now().date().isoformat()

    def _clear_expired_manual_stop(self) -> bool:
        if not self._manual_stop_requested:
            self._manual_stop_trade_date = ""
            return False
        current_trade_date = self._manual_stop_reference_date()
        if self._manual_stop_trade_date and self._manual_stop_trade_date == current_trade_date:
            return False
        self._manual_stop_requested = False
        self._manual_stop_trade_date = ""
        log.info("[盘中监控] 手动停止标记已跨交易日失效，恢复自动启动")
        return True

    def _touch_last_update(self, time_text: str | None = None) -> bool:
        stamp = str(time_text or "").strip() or self._now_hhmm()
        if not stamp or stamp == self._rt_last_update:
            return False
        self._rt_last_update = stamp
        return True

    def _read_provider_status(self) -> dict:
        provider = getattr(self, "data_provider", None)
        request_stats = {}
        runtime_stats = {}

        request_getter = getattr(provider, "get_quote_request_stats", None)
        if callable(request_getter):
            try:
                request_stats = request_getter() or {}
            except (AttributeError, RuntimeError, TypeError, ValueError):
                request_stats = {}

        runtime_getter = getattr(provider, "get_realtime_runtime_stats", None)
        if callable(runtime_getter):
            try:
                runtime_stats = runtime_getter() or {}
            except (AttributeError, RuntimeError, TypeError, ValueError):
                runtime_stats = {}

        return {
            "request_stats": request_stats,
            "runtime_stats": runtime_stats,
            "eastmoney_cooldown_until": float(getattr(provider, "_rt_eastmoney_cooldown_until", 0.0) or 0.0),
            "eastmoney_last_error": str(getattr(provider, "_rt_eastmoney_last_error", "") or ""),
        }

    @staticmethod
    def _latest_trade_date_text() -> str:
        try:
            from app.services.ui_market_calendar_service import MarketCalendar

            trade_date = MarketCalendar.get_latest_trade_date("CN")
            if trade_date is not None:
                return trade_date.isoformat()
            return MarketCalendar.today("CN").isoformat()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return ""

    def _describe_rt_rows(self, rows: list[dict]):
        warnings = []
        if not rows:
            warnings.append("rt_monitor_rows_empty")
        return self._rt_lineage_service.describe(
            rows,
            trade_date=self._latest_trade_date_text(),
            triggered_network=self._is_rt_running(),
            warnings=warnings,
            extra={
                "status_state": self._rt_status_state,
                "status_detail": self._rt_status_detail,
                "last_table_update": self._rt_last_update,
                "pool_size": self._rt_pool_size,
            },
        )

    def get_data_lineage(self) -> dict:
        result = self._last_rt_result
        if result is None:
            rows = list(getattr(self.source_model, "row_data", None) or [])
            result = self._describe_rt_rows(rows)
            self._last_rt_result = result
            self._last_rt_signature = result.signature
        return result.lineage.as_dict()

    def _status_label_text(self) -> str:
        return self._STATUS_LABELS.get(str(self._rt_status_state or "").strip(), "处理中")

    def _current_result_count(self) -> int:
        return len(getattr(self.source_model, "row_data", []) or [])

    def _current_visible_count(self) -> int:
        if not hasattr(self, "proxy_model"):
            return 0
        return self.proxy_model.rowCount()

    def _compose_rt_header_summary(self) -> str:
        total = self._current_result_count()
        visible = self._current_visible_count()
        search_text = self.rt_search.text().strip() if hasattr(self, "rt_search") else ""

        if total > 0:
            primary = "盘中监控已就绪"
        elif self._rt_status_state == "fallback":
            primary = "等待数据"
        elif self._rt_status_state == "error":
            primary = "监控异常"
        elif self._rt_status_state == "idle" and "清空" in self._rt_status_detail:
            primary = "监控记录已清空"
        elif self._is_rt_running():
            primary = "监控运行中"
        else:
            primary = "盘中监控未启动"

        freshness = f"最近 {self._rt_last_update}" if self._rt_last_update else self._status_label_text()
        next_step = str(self._rt_status_next_step or "").strip()

        extra_segments = [f"数据 {self._status_label_text()}"]
        detail_text = str(self._rt_status_detail or "").strip()
        if detail_text:
            extra_segments.append(f"说明 {detail_text}")
        if self._rt_pool_size > 0:
            extra_segments.append(self._status_metric("待突破池 ", self._rt_pool_size, "只"))

        return self.format_workspace_status(
            primary,
            result=f"{visible}/{total}只" if total else "0只",
            freshness=freshness,
            current_filter=search_text or "全部",
            next_step=next_step,
            extra_segments=extra_segments,
        )

    def _refresh_rt_header_summary(self):
        if hasattr(self, "lbl_rt_info"):
            self.lbl_rt_info.setText(self._compose_rt_header_summary())

    def _set_status(self, state: str, detail: str = "", next_step: str = "", *, touch: bool = True):
        self._rt_status_state = str(state or "").strip() or "working"
        self._rt_status_detail = str(detail or "").strip()
        self._rt_status_next_step = str(next_step or "").strip()
        if touch:
            self._touch_last_update()
        self._refresh_rt_header_summary()

    def _on_scan_count_updated(self, round_no: int, pool_size: int):
        self._rt_pool_size = max(0, int(pool_size or 0))
        if round_no:
            self._touch_last_update()
        self._refresh_rt_header_summary()

    def _on_worker_progress(self, raw_msg: str):
        msg = str(raw_msg or "").strip()
        if not msg:
            return

        interval_sec = self._get_interval_seconds()

        m_fetch = re.search(r"第(\d+)轮:拉取\s*(\d+)\s*只报价", msg)
        if m_fetch:
            round_no, cnt = m_fetch.groups()
            self._set_status(
                "realtime",
                f"第{round_no}轮 拉取{cnt}只报价",
                "检测突破并刷新"
            )
            return

        m_done = re.search(r"第(\d+)轮完成\(耗时\s*([0-9\.]+)s\),等待下轮", msg)
        if m_done:
            round_no = int(m_done.group(1))
            elapsed = m_done.group(2)
            self._set_status(
                "realtime",
                f"第{round_no}轮 完成({elapsed}s)",
                f"{interval_sec}s后第{round_no + 1}轮"
            )
            return

        if "加载历史日线数据" in msg:
            self._set_status("cache", "初始化历史数据", "计算RPS并建池")
            return

        if "计算全市场 RPS 排名" in msg:
            self._set_status("working", "计算RPS", "建池并拉取报价")
            return

        if msg.startswith("补全") and "市值" in msg:
            self._set_status("working", msg.replace("...", ""), "收尾后进入下一轮")
            return

        if "实时报价获取失败" in msg:
            self._set_status("error", "拉取报价失败", f"{interval_sec}s后重试")
            return

        if msg.startswith("盘中扫描异常"):
            self._set_status("error", "本轮扫描异常", f"{interval_sec}s后重试")
            return

        if "无历史数据" in msg:
            self._set_status("fallback", "缺少历史数据", "先执行F5或扫描")
            return

        self._set_status("working", msg.replace("...", ""), "处理中")

    def _set_rt_button_state(self, running: bool, info_text: str | None = None, emit_progress: bool = False):
        """统一维护盘中监控按钮显示状态，避免 UI 与真实运行状态漂移。"""
        if running:
            self.btn_rt_start.setText("停止监控")
            self.btn_rt_start.setProperty("monitoring", True)
            self.btn_rt_start.setProperty("monitoring_state", "running")
            self.btn_rt_start.setEnabled(True)
            self.btn_rt_start.style().unpolish(self.btn_rt_start)
            self.btn_rt_start.style().polish(self.btn_rt_start)
        else:
            self.btn_rt_start.setText("开始监控")
            self.btn_rt_start.setProperty("monitoring", False)
            self.btn_rt_start.setProperty("monitoring_state", "idle")
            self.btn_rt_start.setEnabled(True)
            self.btn_rt_start.style().unpolish(self.btn_rt_start)
            self.btn_rt_start.style().polish(self.btn_rt_start)

        if info_text:
            self.lbl_rt_info.setText(info_text)

        if emit_progress:
            ui_signals.sig_task_progress.emit("rt_monitor", 1 if running else 0, "start" if running else "stop")

    def _set_rt_button_stopping(self, info_text: str | None = None):
        self.btn_rt_start.setText("正在停止...")
        self.btn_rt_start.setProperty("monitoring", True)
        self.btn_rt_start.setProperty("monitoring_state", "stopping")
        self.btn_rt_start.setEnabled(False)
        self.btn_rt_start.style().unpolish(self.btn_rt_start)
        self.btn_rt_start.style().polish(self.btn_rt_start)
        if info_text:
            self.lbl_rt_info.setText(info_text)

    def _check_auto_start_stop(self):
        from app.services.ui_market_calendar_service import MarketCalendar
        self._clear_expired_manual_stop()
        is_active = MarketCalendar.is_market_active()
        is_running = self._is_rt_running()

        # 如果在活跃时间，没在运行，且用户没有手动强行关掉它 -> 自动启动
        if is_active and not is_running and not self._manual_stop_requested and not self._rt_stop_requested:
            log.info("[盘中监控] 交易时段到达，触发自动启动...")
            self._toggle_rt_monitor(auto=True)
        # 如果不在活跃时间，但它还在运行 -> 自动关掉静默
        elif not is_active and is_running and not self._rt_stop_requested:
            log.info("[盘中监控] 非交易时段，触发自动静默...")
            self._toggle_rt_monitor(auto=True)
            self._manual_stop_requested = False  # 清除人工停止标记，确保明早能自动启动

        # 兜底：无论何种路径，按钮状态都与真实线程状态保持一致
        if self._rt_stop_requested and self._is_rt_running():
            if self.btn_rt_start.text() != "正在停止...":
                self._set_rt_button_stopping()
        elif self._is_rt_running():
            if self.btn_rt_start.text() != "停止监控":
                self._set_rt_button_state(True)
        else:
            if self.btn_rt_start.text() != "开始监控":
                self._set_rt_button_state(False)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_rt_info = QLabel("未启动")
        self.lbl_rt_info.setWordWrap(True)

        self.rt_search = QLineEdit()
        self.rt_search.setPlaceholderText("筛选代码或名称...")
        self.rt_search.setAccessibleName("盘中监控筛选")
        self.rt_search.setAccessibleDescription("按代码或名称筛选盘中监控结果")
        self.rt_search.setMinimumWidth(180)
        self.rt_search.setMaximumWidth(260)
        self.rt_search.textChanged.connect(self._on_search_text_changed)

        filter_widgets = [self.rt_search]

        self.btn_rt_start = QPushButton("开始监控")
        self.btn_rt_start.setObjectName("primaryButton")
        self.btn_rt_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rt_start.setProperty("toolbarWidthHints", ["开始监控", "停止监控", "正在停止..."])
        self.btn_rt_start.clicked.connect(lambda *args: self.toggle_rt_monitor())

        # 清空盘中记录按钮
        self.btn_rt_clear = QPushButton("清空记录")
        self.btn_rt_clear.setProperty("class", "secondary")
        self.btn_rt_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rt_clear.clicked.connect(self._clear_table)

        # 盘中监控参数设置按钮
        btn_rt_settings = QToolButton()
        btn_rt_settings.setText("参数")
        btn_rt_settings.setAccessibleName("盘中监控参数设置")
        btn_rt_settings.setProperty("class", "toolbarGhost")
        btn_rt_settings.setProperty("toolbarOverflow", True)
        btn_rt_settings.setMinimumWidth(56)
        btn_rt_settings.setAutoRaise(False)
        btn_rt_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rt_settings.setToolTip("盘中监控参数设置")
        btn_rt_settings.clicked.connect(self._show_rt_settings)

        action_widgets = [self.btn_rt_start, self.btn_rt_clear, btn_rt_settings]
        toolbar = self.build_tab_toolbar("盘中监控", self.lbl_rt_info, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格控件 MVC
        self.source_model = RtTableModel()
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)

        self.table_rt = VCPTableView(default_row_height=30)
        self.table_rt.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table_rt)
        self.table_rt.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table_rt, empty_title="暂无监控记录", loading_title="加载中...")

        header = self.table_rt.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(52)

        try:
            width_map = {
                "序号": 58,
                "代码": 84,
                "名称": 132,
                "现价": 90,
                "涨幅%": 90,
                "市值": 90,
                "时间": 76,
                "评分": 78,
                "RPS强度": 98,
                "突破状态": 168,
                "区间振幅": 96,
                "热点板块": 240,
            }
            for col_idx, header_name in enumerate(self.source_model.headers):
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.apply_table_column_preset(
                self.table_rt,
                [width_map.get(header_name, 86) for header_name in self.source_model.headers],
                stretch_last=True,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.warning(f"[盘中监控] 列宽初始化异常: {e}")
        self.table_rt.setSortingEnabled(True)
        self.table_rt.horizontalHeader().setSortIndicatorShown(True)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.table_rt, "header_state_rt_v5")

        # 绑定双击事件，广播K线上下文
        self.table_rt.doubleClicked.connect(self._on_table_double_clicked)

        # 右键菜单
        self.table_rt.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_rt.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state)

    def _clear_table(self):
        result = self._describe_rt_rows([])
        self._last_rt_result = result
        if result.signature != self._last_rt_signature:
            self.source_model.update_data(result.rows)
            self._last_rt_signature = result.signature
        self._touch_last_update()
        self._set_status("idle", "已清空记录", "点“开始监控”恢复轮询")
        if hasattr(self, "table_state"):
            self.table_state.show_empty("暂无监控记录")

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_rt_header_summary()

    def _show_rt_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("盘中监控参数")
        dlg.setObjectName("settingsDialog")
        dlg.setFixedSize(300, 160)
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
        btn_ok.clicked.connect(dlg.accept)
        form.addWidget(btn_ok)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._settings.setValue("interval", cmb_interval.currentText())
            self._settings.setValue("rps", spn_rps.value())
            self._settings.sync()
            show_toast("盘中监控参数已保存", "success", self)

    def _toggle_rt_monitor(self, auto=False):
        if self._is_rt_running():
            self._manual_stop_requested = not auto
            self._manual_stop_trade_date = self._manual_stop_reference_date() if self._manual_stop_requested else ""
            self._rt_stop_requested = True
            self.rt_worker.stop()
            if auto:
                self._set_status("working", "自动停止中", "下个交易时段会自动启动")
            else:
                self._set_status("working", "正在停止", "稍后可重新启动")
            self._set_rt_button_stopping()
            return
        else:
            if auto and self._manual_stop_requested and not self._clear_expired_manual_stop():
                self._set_status("idle", "今日已手动停止", "下一交易日会自动启动", touch=False)
                self._set_rt_button_state(False)
                return
            if not auto:
                self._manual_stop_requested = False
                self._manual_stop_trade_date = ""
            self._rt_stop_requested = False
            # 兜底：如果内存缓存为空，先尝试从磁盘加载昨日F5的缓存
            if not self.data_provider.cache_data:
                self._set_status("cache", "加载本地缓存", "就绪后连接行情")
                try:
                    cache_date = self.data_provider.load_cache_from_disk()
                    if cache_date and self.data_provider.cache_data:
                        log.info(f"[盘中监控] 缓存为空，从磁盘自动加载成功(日期: {cache_date})")
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[盘中监控] 磁盘缓存自动加载失败: {e}")
            if not self.data_provider.cache_data:
                show_toast("请先执行扫描或按 F5 加载数据后再启动", "warning", self)
                self._set_status("fallback", "无可用缓存", "先执行扫描或F5")
                return

            if not self.data_provider.is_online():
                self._set_status("working", "连接行情服务器", "成功后自动启动")
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
                    task_id=task_registry.workspace("rt_connect").task_id
                )
                return
            self._start_rt_worker()

    def is_rt_running(self) -> bool:
        return self._is_rt_running()

    def toggle_rt_monitor(self, auto: bool = False) -> bool:
        self._toggle_rt_monitor(auto=auto)
        return True

    def shutdown(self) -> None:
        auto_timer = getattr(self, "_auto_timer", None)
        if auto_timer is not None:
            auto_timer.stop()
        worker = getattr(self, "rt_worker", None)
        if worker is not None:
            self._manual_stop_requested = False
            self._rt_stop_requested = True
            request_thread_shutdown(
                worker,
                label="RT monitor worker",
                stop=getattr(worker, "stop", None),
                timeout_ms=2000,
                logger=log,
            )

    def _start_rt_worker(self):
        interval_sec = self._get_interval_seconds()
        rps_threshold = int(self._settings.value("rps", 80))

        self.rt_worker = RtScanWorker(self.data_provider, self.engine, interval=interval_sec, rps_threshold=rps_threshold)
        # 拦截：工作线程的高频抛出不再直接刷新界面，只喂给 throttler
        self.rt_worker.rt_result_ready.connect(lambda data: self._rt_throttler.trigger(data))
        self.rt_worker.progress.connect(self._on_worker_progress)
        self.rt_worker.scan_count.connect(self._on_scan_count_updated)
        self.rt_worker.scan_count.connect(lambda n, pool: event_bus.sig_system_log.emit("info", f"[监控] 第{n}轮 | 待突破池 {pool} 只"))
        self.rt_worker.finished.connect(self._on_rt_worker_finished)

        self._rt_stop_requested = False
        self.rt_worker.start()
        self._set_status("realtime", "已启动", "正在执行首轮任务")
        self._set_rt_button_state(True, emit_progress=True)

    @pyqtSlot()
    def _on_rt_worker_finished(self):
        self._rt_stop_requested = False
        self.btn_rt_start.setEnabled(True)

        if self.rt_worker is not None:
            self.rt_worker.deleteLater()
            self.rt_worker = None

        try:
            from app.services.ui_market_calendar_service import MarketCalendar
            active = MarketCalendar.is_market_active()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            active = False

        if self._manual_stop_requested:
            next_step = "点“启动盘中监控”可恢复"
        elif not active:
            next_step = "下个交易时段会自动启动"
        else:
            next_step = "可手动重新启动"

        self._set_status("idle", "已停止", next_step)
        self._set_rt_button_state(False, emit_progress=True)

    @pyqtSlot()
    def _on_rt_network_ready(self):
        self.btn_rt_start.setEnabled(True)
        event_bus.sig_network_status_changed.emit(True, "Online")
        event_bus.sig_system_log.emit("info", "[盘中监控] 启动前自动联网成功")
        self._set_status("realtime", "联网成功", "正在启动")
        self._start_rt_worker()

    @pyqtSlot()
    def _on_rt_network_failed(self):
        self.btn_rt_start.setEnabled(True)
        self._set_status("error", "联网失败", "检查网络后重试")
        self._set_rt_button_state(False)
        show_toast("无法连接东方财富实时报价", "error", self)

    def _do_update_rt_table(self, results):
        """实际执行刷新：这部分现在由于 Throttler 保护，每秒最多只执行一次"""
        rt_only = [r for r in results if not r.get('_is_special')]
        try:
            latest_time = ""
            for row in rt_only:
                row_time = str((row or {}).get("时间", "")).strip()
                if row_time and row_time > latest_time:
                    latest_time = row_time
            if latest_time:
                self._touch_last_update(latest_time)
            result = self._describe_rt_rows(rt_only)
            self._last_rt_result = result
            if result.signature != self._last_rt_signature:
                self.source_model.update_rows_incremental(result.rows)
                self._last_rt_signature = result.signature
            self._refresh_rt_header_summary()
            if hasattr(self, "table_state"):
                if rt_only:
                    self.table_state.show_table()
                else:
                    self.table_state.show_empty("暂无监控记录")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"Failed to update rt table: {e}")

        # 核心解耦点：表格自身渲染完毕后，向全系统抛出数据刷新事件！
        # 让 MainWindowQT 或者 WatchlistTab 自行拦截处理剩下的全局关联逻辑。
        event_bus.sig_rt_quotes_refreshed.emit(results)

    # ================================================================
    # 交互事件 (右键菜单 / 双击)
    # ================================================================
    def _on_table_double_clicked(self, idx):
        if not idx.isValid():
            return

        source_current = self.proxy_model.mapToSource(idx)
        if not source_current.isValid():
            return

        current_row_data = self.source_model.get_row_data(source_current.row()) or {}
        current_code = str(current_row_data.get("代码", "")).strip()
        code_col = self.source_model.headers.index("代码")
        name_col = self.source_model.headers.index("名称")
        if not current_code:
            current_code = str(
                self.proxy_model.data(
                    self.proxy_model.index(idx.row(), code_col),
                    Qt.ItemDataRole.DisplayRole
                ) or ""
            ).strip()
        if not current_code:
            return

        # 提取当前所有过滤后的结果构建完整上下文（包含区间字段）
        code_list = []
        current_idx = -1
        for r in range(self.proxy_model.rowCount()):
            source_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, code_col))
            if not source_idx.isValid():
                continue

            row_data = self.source_model.get_row_data(source_idx.row()) or {}
            if not isinstance(row_data, dict):
                row_data = {}

            code = str(row_data.get("代码", "")).strip()
            if not code:
                code = str(
                    self.proxy_model.data(
                        self.proxy_model.index(r, code_col),
                        Qt.ItemDataRole.DisplayRole
                    ) or ""
                ).strip()
            if not code:
                continue

            row_dict = dict(row_data)
            row_dict["代码"] = code
            if not row_dict.get("名称"):
                row_dict["名称"] = str(
                    self.proxy_model.data(
                        self.proxy_model.index(r, name_col),
                        Qt.ItemDataRole.DisplayRole
                    ) or ""
                )
            code_list.append(row_dict)

            if current_idx == -1 and code == current_code:
                current_idx = len(code_list) - 1

        if code_list and current_idx != -1:
            ui_signals.sig_show_kline_with_list.emit(current_code, code_list, current_idx)
        else:
            ui_signals.sig_show_kline.emit(current_code)

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
