# -*- coding: utf-8 -*-
"""
ui/tabs/na_daily_tab.py
北美战报 独立 Tab 组件 (MVC 版本重构)
"""

import os
from functools import partial

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.na_daily_service import NADailyRefreshService, build_na_daily_refresh_payload, parse_report_identity
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_task_lifecycle_service import invoke_with_cancellation, task_lifecycle_for
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab, _show_kline_from_proxy_index
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks

log = get_logger(__name__)
_NA_DAILY_REFRESH_TASK = task_registry.workspace("na_daily_refresh")


def _build_refresh_payload(output_dir, cancellation_token):
    return invoke_with_cancellation(
        build_na_daily_refresh_payload,
        cancellation_token,
        output_dir,
        limit=5,
    )


def _apply_refresh_if_current(owner, generation: int, payload) -> None:
    if getattr(owner, "_closing", False) or generation != getattr(owner, "_na_daily_refresh_generation", 0):
        return
    owner._apply_na_daily_refresh_payload(payload)


def _apply_refresh_error_if_current(owner, generation: int, error_message) -> None:
    if getattr(owner, "_closing", False) or generation != getattr(owner, "_na_daily_refresh_generation", 0):
        return
    owner._on_na_daily_refresh_failed(error_message)


def _cancel_scheduled_runtime_start(owner) -> None:
    owner._runtime_start_pending = False
    timer = getattr(owner, "_runtime_start_timer", None)
    if timer is not None:
        timer.stop()


def _run_scheduled_runtime_start(owner) -> None:
    if getattr(owner, "_closing", False) or not getattr(owner, "_runtime_start_pending", False):
        return
    _cancel_scheduled_runtime_start(owner)
    owner._load_na_daily_report()


class _NADailyBackgroundPreloadMixin:
    def prime_background_load(self):
        if self._closing or self._background_prime_done:
            return False
        _cancel_scheduled_runtime_start(self)
        self._runtime_started = True
        self._background_prime_loading = True
        scheduled = self._load_na_daily_report()
        if not scheduled:
            self._background_prime_loading = False
            self._background_prime_done = True
        return bool(scheduled)

    def is_background_preload_complete(self) -> bool:
        if self._closing or getattr(self, "_runtime_cleanup_done", False):
            return True
        return bool(
            self._background_prime_done
            and not self._background_prime_loading
            and not self._na_daily_refresh_task_active
        )

    def cancel_background_preload(self, *, reason: str):
        def _reset() -> None:
            _cancel_scheduled_runtime_start(self)
            self._na_daily_refresh_generation += 1
            self._na_daily_refresh_task_active = False
            self._background_prime_loading = False
            self._background_prime_done = False
            self._runtime_started = False

        return cancel_background_preload_tasks(
            self,
            lifecycle_names=("refresh",),
            task_ids=(_NA_DAILY_REFRESH_TASK,),
            reason=reason,
            reset_state=_reset,
            local_settled=lambda: not self._na_daily_refresh_task_active,
            runner=task_manager,
        )


class NADailyTab(_NADailyBackgroundPreloadMixin, BaseStockTab):
    def __init__(self, data_provider, parent=None, *, runtime_start_delay_ms: int = 350):
        super().__init__(data_provider=data_provider, parent=parent)
        try:
            self._runtime_start_delay_ms = max(0, int(runtime_start_delay_ms))
        except (TypeError, ValueError):
            self._runtime_start_delay_ms = 350
        self._na_daily_codes = set()
        self._status_primary = "等待北美战报"
        self._status_segments: list[str] = []
        self._status_freshness = ""
        self._status_next_step = ""
        self._current_report_files: list[str] = []
        self._runtime_started = False
        self._background_prime_loading = False
        self._background_prime_done = False
        self._handling_na_daily_event = False
        self._na_daily_refresh_task_active = False
        self._na_daily_refresh_generation = 0
        self._closing = False
        self._runtime_start_pending = False
        self._runtime_start_timer = QTimer(self)
        self._runtime_start_timer.setSingleShot(True)
        self._runtime_start_timer.timeout.connect(partial(_run_scheduled_runtime_start, self))
        self._init_ui()
        # 首次显示后再拉取/巡逻，避免冷启动阶段抢占首屏。
        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()

        self._na_daily_service = self._resolve_na_daily_service()
        event_bus.sig_na_daily_updated.connect(self._on_na_daily_updated)
        self._render_service_cache()

    def _ensure_runtime_started(self):
        if self._closing or self._runtime_started:
            return
        self._runtime_started = True
        self._runtime_start_pending = True
        self._runtime_start_timer.start(self._runtime_start_delay_ms)

    def showEvent(self, event):
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()

    def _resolve_na_daily_service(self):
        parent = self.parent()
        host = None
        try:
            host = parent.window() if parent is not None else self.window()
        except RuntimeError:
            host = None
        service = getattr(host, "na_daily_service", None)
        if isinstance(service, NADailyRefreshService):
            return service
        return NADailyRefreshService(parent=self)

    def _render_service_cache(self):
        service = getattr(self, "_na_daily_service", None)
        if service is None:
            return
        service.load_cache()
        if service.rows or service.report_files:
            self._apply_na_daily_rows(
                service.rows,
                service.report_files,
                service.report_signature,
                emit_event=False,
                refresh_quotes=False,
            )

    def _on_na_daily_updated(self):
        if self._handling_na_daily_event:
            return
        self._render_service_cache()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.na_daily_source_label = QLabel("未加载")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(160)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self._load_na_daily_report)

        filter_widgets = [self.search_box]
        action_widgets = [btn_refresh]
        toolbar = self.build_tab_toolbar("北美战报", self.na_daily_source_label, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        columns = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "日报时间",
            "细分板块",
            "股价弹性",
            "催化剂",
            "风控",
            "评级",
        ]
        self.na_daily_table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.na_daily_table, empty_title="暂无战报数据", loading_title="加载中...")

        self.model = StockTableModel(columns)
        self.model.set_plain_style_headers(["日报时间"])
        self.model.set_muted_text_headers(["日报时间"])
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.na_daily_table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.na_daily_table)
        self.na_daily_table.setItemDelegate(self.delegate)

        header = self.na_daily_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [52, 60, 70, 60, 60, 70, 78, 100, 80, 120, 50, 60]
        for i, w in enumerate(default_widths):
            if i < len(self.model.headers):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.na_daily_table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置；首开默认按日报时间降序
        restored_sort = self.bind_header_persistence(self.na_daily_table, "header_state_na_daily_v4")
        if not restored_sort:
            try:
                report_col = self.model.headers.index("日报时间")
                self.na_daily_table.sortByColumn(report_col, Qt.SortOrder.DescendingOrder)
            except ValueError:
                pass

        self.na_daily_table.doubleClicked.connect(self._on_double_click)
        self.na_daily_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.na_daily_table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)
        self._set_report_status("等待北美战报", freshness="待加载", next_step="点击刷新载入最新战报")

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_report_status()

    def _latest_report_freshness(self) -> str:
        report_files = getattr(self, "_current_report_files", None) or []
        if not report_files:
            return self._status_freshness or "待加载"
        newest_file = max(report_files, key=lambda path: parse_report_identity(path)[1])
        report_date, _, _ = parse_report_identity(newest_file)
        return f"快照 {report_date}"

    def _current_filter_summary(self) -> str:
        keyword = str(self.search_box.text() or "").strip()
        return keyword or "全部"

    def _refresh_report_status(self):
        total = len(getattr(self.model, "row_data", None) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        self.na_daily_source_label.setText(
            self.format_workspace_status(
                self._status_primary or ("北美战报已就绪" if total else "等待北美战报"),
                result=self._status_metric("结果 ", f"{visible}/{total}", "只") if total else "",
                freshness=self._status_freshness or self._latest_report_freshness(),
                current_filter=self._current_filter_summary(),
                next_step=self._status_next_step or ("" if total else "点击刷新载入最新战报"),
                extra_segments=tuple(seg for seg in self._status_segments if seg),
            )
        )

    def _set_report_status(self, primary: str, *segments: str, freshness: str = "", next_step: str = ""):
        self._status_primary = str(primary or "").strip()
        self._status_segments = [str(seg or "").strip() for seg in segments if str(seg or "").strip()]
        self._status_freshness = str(freshness or "").strip()
        self._status_next_step = str(next_step or "").strip()
        self._refresh_report_status()

    def _apply_na_daily_rows(
        self,
        final_list,
        report_files,
        report_signature,
        *,
        emit_event: bool = True,
        refresh_quotes: bool = True,
    ):
        self._current_report_files = list(report_files or [])

        if not report_files:
            self._set_report_status(
                "等待北美战报", "最近窗口为空", freshness="待加载", next_step="点击刷新载入最新战报"
            )
            self.model.update_data([])
            self._na_daily_codes = set()
            if emit_event:
                event_bus.sig_na_daily_updated.emit()
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无战报数据")
            return

        newest_file = max(report_files, key=lambda path: parse_report_identity(path)[1])
        newest_name = os.path.basename(newest_file)
        report_date, _, _ = parse_report_identity(newest_file)
        if len(report_files) == 1:
            self._set_report_status(
                "北美战报已就绪",
                newest_name,
                self._status_metric("合并 ", len(final_list), "只"),
                freshness=f"快照 {report_date}",
                next_step="",
            )
        else:
            self._set_report_status(
                "北美战报已就绪",
                newest_name,
                self._status_metric("覆盖 ", len(report_files), "份"),
                self._status_metric("合并 ", len(final_list), "只"),
                freshness=f"快照 {report_date}",
                next_step="",
            )

        self._na_daily_codes = {row.get("代码", "") for row in final_list if row.get("代码")}
        self.model.update_data(final_list)
        if hasattr(self, "table_state"):
            if final_list:
                self.table_state.show_table()
            else:
                self.table_state.show_empty("暂无战报数据")

        if self._na_daily_codes:
            if self._background_prime_loading or not refresh_quotes:
                self._apply_quote_store_snapshot()
                if emit_event:
                    event_bus.sig_na_daily_updated.emit()
                return
            self.refresh_table_quotes_and_market_caps(quote_task_id=task_registry.quote_refresh("na_daily").task_id)

        if emit_event:
            event_bus.sig_na_daily_updated.emit()

    def _apply_na_daily_refresh_payload(self, payload: dict):
        self._na_daily_refresh_task_active = False
        service = getattr(self, "_na_daily_service", None)
        if service is None:
            self._background_prime_loading = False
            self._background_prime_done = True
            return
        try:
            service.apply_refresh_payload(payload or {}, emit_event=False)
            self._apply_na_daily_rows(
                service.rows,
                service.report_files,
                service.report_signature,
                emit_event=not self._background_prime_loading,
            )
        finally:
            if self._background_prime_loading:
                self._background_prime_loading = False
                self._background_prime_done = True

    def _on_na_daily_refresh_failed(self, error_message: str):
        self._na_daily_refresh_task_active = False
        self._background_prime_loading = False
        self._background_prime_done = True
        msg = str(error_message or "").strip() or "战报加载失败"
        self._set_report_status("北美战报刷新失败", msg, freshness=self._latest_report_freshness(), next_step="点击刷新重试")
        if hasattr(self, "table_state"):
            if getattr(self.model, "row_data", []):
                self.table_state.show_table()
            else:
                self.table_state.show_error("北美战报加载失败", msg, action_text="重新加载", action_callback=self._load_na_daily_report)

    def _load_na_daily_report(self, *, run_in_background: bool = True):
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在加载战报...", "请稍候")
        self._set_report_status(
            "北美战报刷新中", freshness=self._latest_report_freshness(), next_step="等待战报文件合并"
        )
        service = getattr(self, "_na_daily_service", None)
        if service is not None:
            if run_in_background:
                if self._na_daily_refresh_task_active:
                    self._set_report_status(
                        "北美战报刷新中",
                        freshness=self._latest_report_freshness(),
                        next_step="上一轮刷新尚未结束",
                    )
                    return False
                output_dir = service._get_na_daily_output_dir()
                self._na_daily_refresh_task_active = True
                self._na_daily_refresh_generation += 1
                generation = self._na_daily_refresh_generation
                task_lifecycle_for(self, runner=task_manager).run_background(
                    "refresh",
                    partial(_build_refresh_payload, output_dir),
                    task_id=_NA_DAILY_REFRESH_TASK,
                    timeout_sec=90,
                    on_success=partial(_apply_refresh_if_current, self, generation),
                    on_error=partial(_apply_refresh_error_if_current, self, generation),
                )
                return True
            service.refresh_full(emit_event=False)
            self._apply_na_daily_rows(service.rows, service.report_files, service.report_signature)
            return True
        return False

    def run_post_online_refresh(self) -> bool:
        self._load_na_daily_report()
        return True

    def shutdown(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        _cancel_scheduled_runtime_start(self)
        self._na_daily_refresh_generation += 1
        self._na_daily_refresh_task_active = False
        lifecycle = getattr(self, "_task_lifecycle", None)
        if lifecycle is not None:
            lifecycle.shutdown(timeout_ms=750)
        try:
            event_bus.sig_na_daily_updated.disconnect(self._on_na_daily_updated)
        except (TypeError, RuntimeError):
            pass

    def _cleanup_runtime_state(self):
        self.shutdown()
        super()._cleanup_runtime_state()

    def _on_double_click(self, index):
        _show_kline_from_proxy_index(self, index, ui_signals, require_code=True)

    def _show_context_menu(self, pos):
        index = self.na_daily_table.indexAt(pos)
        if not index.isValid():
            return

        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if not code or not name:
            return

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(self, code, name, vcp_data=row_data)
