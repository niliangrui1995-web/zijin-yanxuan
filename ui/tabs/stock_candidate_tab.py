# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.stock_candidate_builder_service import build_stock_candidate_rows
from app.services.stock_candidates_service import StockCandidatesDataService
from app.services.stock_context_model_service import StockContextReadPolicy, StockContextSnapshot, StockSignal
from app.services.stock_context_query_service import StockContextQueryService
from app.services.ui_diagnostics_service import ui_stall_span
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import task_lifecycle_for
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from ui.components import TableStateWrapper, VCPTableView
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab, _is_direct_workspace_tab
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks
from ui.workspaces.stock_context_widget_adapter import capture_workspace_stock_context

_STOCK_CONTEXT_FUND_SNAPSHOT_TASK = task_registry.workspace("stock_context_fund_rows_snapshot")
_STOCK_CONTEXT_LHB_SNAPSHOT_TASK = task_registry.workspace("stock_context_lhb_rows_snapshot")


def _candidate_tab_titles(owner) -> dict[str, str]:
    reader = getattr(owner, "_tab_titles", None)
    if callable(reader):
        return reader()
    workspace_reader = getattr(owner, "_workspace", None)
    workspace = workspace_reader() if callable(workspace_reader) else None
    specs_reader = getattr(workspace, "tab_specs", None)
    if not callable(specs_reader):
        return {}
    return {
        str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip()
        for spec in specs_reader()
    }


def _immutable_candidate_snapshot(context: dict[str, list[StockSignal]]) -> StockContextSnapshot:
    signals = tuple(
        signal
        for source_signals in (context or {}).values()
        for signal in (source_signals or [])
        if isinstance(signal, StockSignal)
    )
    return StockContextSnapshot(
        direct_source_keys=frozenset(signal.source_tab for signal in signals if signal.source_tab),
        direct_signals=signals,
    )


class StockCandidateTab(BaseStockTab):
    HEADER_STATE_KEY = "header_state_stock_candidates_v2"
    AUTO_REFRESH_DEBOUNCE_MS = 1500
    REFRESH_TASK_ID = "stock_candidates_context_refresh"
    BACKGROUND_DEPENDENCY_POLL_MS = 50
    BACKGROUND_REFRESH_TIMEOUT_SECONDS = 60.0
    REQUIRED_SOURCE_TABS = frozenset({"ai_industry_chain", "na_daily"})
    SNAPSHOT_SOURCE_TABS = ("fund_holdings", "lhb")
    ANCHOR_SOURCE_GROUP = "ai_na_anchor"
    COLUMNS = [
        "代码",
        "名称",
        "市价",
        "涨幅%",
        "市值",
        "共振分",
        "来源数",
        "信号数",
        "来源",
        "核心信号",
        "最近时间",
    ]

    def __init__(self, data_provider, parent=None, *, runtime_start_delay_ms: int = 1500):
        super().__init__(data_provider=data_provider, parent=parent)
        try:
            self._runtime_start_delay_ms = max(0, int(runtime_start_delay_ms))
        except (TypeError, ValueError):
            self._runtime_start_delay_ms = 1500
        self._status_primary = "等待综合候选"
        self._status_freshness = "待刷新"
        self._candidate_service = StockCandidatesDataService(
            context_reader=self._read_stock_context,
            row_builder=build_stock_candidate_rows,
            provider_status_reader=self._read_provider_status,
        )
        self._last_candidate_result = None
        self._last_candidate_signature = ""
        self._context_refresh_pending = False
        self._candidate_refresh_running = False
        self._candidate_refresh_pending = False
        self._candidate_refresh_followup_scheduled = False
        self._background_preload_requested = False
        self._background_preload_done = False
        self._background_preload_error = ""
        self._background_preload_waiting_snapshots = False
        self._background_preload_rebuild_started = False
        self._background_preload_retry_pending = False
        self._background_preload_reuses_ready_sources = False
        self._auto_refresh_connections = []
        self._init_ui()
        self.subscribe_global_quotes(self.model)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.setInterval(self.AUTO_REFRESH_DEBOUNCE_MS)
        self._auto_refresh_timer.timeout.connect(self._refresh_candidates_if_current)
        self._background_dependency_timer = QTimer(self)
        self._background_dependency_timer.setSingleShot(True)
        self._background_dependency_timer.setInterval(self.BACKGROUND_DEPENDENCY_POLL_MS)
        self._background_dependency_timer.timeout.connect(self._poll_background_preload_dependencies)
        self._connect_auto_refresh_events()
        self._initial_refresh_started = False

    def _ensure_runtime_started(self) -> None:
        if self._initial_refresh_started:
            return
        self._initial_refresh_started = True
        QTimer.singleShot(self._runtime_start_delay_ms, self._refresh_candidates_if_current)

    def _refresh_candidates_if_current(self) -> None:
        if not self._is_current_visible_workspace_tab():
            self._context_refresh_pending = True
            return
        if not self._should_start_runtime_on_show():
            self._context_refresh_pending = True
            return
        self.refresh_candidates()

    def _is_current_workspace_tab(self) -> bool:
        return _is_direct_workspace_tab(self)

    def _is_current_visible_workspace_tab(self) -> bool:
        try:
            return bool(self.isVisible() and self._is_current_workspace_tab())
        except RuntimeError:
            return False

    def showEvent(self, event):  # noqa: N802 - Qt API naming
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            if self._context_refresh_pending:
                self._queue_context_refresh()
            else:
                self._ensure_runtime_started()

    def hideEvent(self, event):  # noqa: N802 - Qt API naming
        super().hideEvent(event)
        timer = getattr(self, "_auto_refresh_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            self._context_refresh_pending = True

    def prime_background_load(self) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        if self._background_preload_done:
            return False
        if not self._background_preload_requested:
            self._background_preload_requested = True
            self._background_preload_error = ""
            self._background_preload_reuses_ready_sources = self._preloaded_snapshot_sources_ready()
            if not self._background_preload_reuses_ready_sources:
                self._prime_stock_context_snapshots(self._workspace())
        self._initial_refresh_started = True
        self._context_refresh_pending = False
        self._maybe_start_background_candidate_rebuild()
        return True

    def is_background_preload_complete(self) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return True
        self._maybe_start_background_candidate_rebuild()
        return bool(
            self._background_preload_requested
            and self._background_preload_done
            and self._background_preload_rebuild_started
            and not self._background_preload_waiting_snapshots
            and not self._candidate_refresh_running
            and not self._candidate_refresh_pending
            and not self._candidate_refresh_followup_scheduled
        )

    def _stock_context_snapshots_settled(self) -> bool:
        if self._background_preload_reuses_ready_sources:
            return True
        reader = getattr(self._workspace(), "stock_context_snapshots_settled", None)
        if not callable(reader):
            return True
        try:
            return bool(reader())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    def _preloaded_snapshot_sources_ready(self) -> bool:
        workspace = self._workspace()
        status_reader = getattr(workspace, "background_preload_status", None)
        if not callable(status_reader):
            return False
        try:
            status = status_reader()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        if not isinstance(status, dict):
            return False
        ready_keys = status.get("ready_keys")
        if not isinstance(ready_keys, (list, tuple, set, frozenset)):
            return False
        ready = {str(key or "").strip() for key in ready_keys}
        return set(self.SNAPSHOT_SOURCE_TABS).issubset(ready)

    def _schedule_background_dependency_poll(self) -> None:
        timer = self._background_dependency_timer
        if not timer.isActive() and not getattr(self, "_runtime_cleanup_done", False):
            timer.start()

    def _maybe_start_background_candidate_rebuild(self) -> bool:
        if (
            not self._background_preload_requested
            or self._background_preload_done
            or self._background_preload_rebuild_started
            or getattr(self, "_runtime_cleanup_done", False)
        ):
            return False
        if not self._stock_context_snapshots_settled():
            self._background_preload_waiting_snapshots = True
            self._schedule_background_dependency_poll()
            return False
        self._background_dependency_timer.stop()
        self._background_preload_waiting_snapshots = False
        self._background_preload_rebuild_started = True
        self._start_candidate_refresh_async()
        return True

    def _poll_background_preload_dependencies(self) -> None:
        if self._maybe_start_background_candidate_rebuild():
            return
        if self._background_preload_waiting_snapshots:
            self._schedule_background_dependency_poll()

    def cancel_background_preload(self, *, reason: str):
        workspace = self._workspace()
        reuses_ready_sources = self._background_preload_reuses_ready_sources
        cancel_snapshots = getattr(workspace, "cancel_stock_context_snapshots", None)
        if not reuses_ready_sources and callable(cancel_snapshots):
            try:
                cancel_snapshots(reason=reason)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

        def _reset() -> None:
            self._background_dependency_timer.stop()
            self._candidate_refresh_running = False
            self._candidate_refresh_pending = False
            self._candidate_refresh_followup_scheduled = False
            self._background_preload_requested = False
            self._background_preload_done = False
            self._background_preload_error = ""
            self._background_preload_waiting_snapshots = False
            self._background_preload_rebuild_started = False
            self._background_preload_retry_pending = True
            self._background_preload_reuses_ready_sources = False
            self._initial_refresh_started = False
            self._context_refresh_pending = False

        return cancel_background_preload_tasks(
            self,
            lifecycle_names=("candidate_refresh",),
            task_ids=(task_registry.workspace(self.REFRESH_TASK_ID),)
            + (
                ()
                if reuses_ready_sources
                else (_STOCK_CONTEXT_FUND_SNAPSHOT_TASK, _STOCK_CONTEXT_LHB_SNAPSHOT_TASK)
            ),
            reason=reason,
            reset_state=_reset,
            local_settled=lambda: not self._candidate_refresh_running
            and not self._candidate_refresh_pending
            and not self._candidate_refresh_followup_scheduled
            and (reuses_ready_sources or self._stock_context_snapshots_settled()),
            runner=task_manager,
        )

    def on_workspace_tab_activated(self) -> None:
        if self._background_preload_retry_pending:
            self._background_preload_retry_pending = False
            self.prime_background_load()
            return
        if self._context_refresh_pending:
            self._queue_context_refresh()
        else:
            self._ensure_runtime_started()

    @staticmethod
    def _prime_stock_context_snapshots(
        workspace,
        *,
        force: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> None:
        prime_snapshots = getattr(workspace, "prime_stock_context_snapshots", None)
        if callable(prime_snapshots):
            try:
                prime_snapshots(force=force, include_fund=include_fund, include_lhb=include_lhb)
            except TypeError:
                try:
                    prime_snapshots()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

    @staticmethod
    def _auto_refresh_signal_specs():
        return (
            (event_bus.sig_cache_reload_completed, {"include_lhb": False}),
            (event_bus.sig_na_daily_updated, {"include_lhb": False}),
            (event_bus.sig_ai_industry_chain_updated, {"include_lhb": False}),
            (event_bus.sig_block_trade_updated, {"include_lhb": False}),
            (event_bus.sig_earnings_updated, {"include_lhb": False}),
            (event_bus.sig_lhb_pool_updated, {"include_fund": False, "include_lhb": False}),
            (event_bus.sig_scan_updated, {"include_lhb": False}),
            (event_bus.sig_fund_holdings_updated, {"force_snapshots": True, "include_lhb": False}),
            (
                event_bus.sig_stock_context_snapshot_updated,
                {"prime_snapshots": False, "include_fund": False, "include_lhb": False},
            ),
            (event_bus.sig_watchlist_changed, {"include_lhb": False}),
        )

    @staticmethod
    def _allow_context_snapshot_refresh() -> bool:
        try:
            return bool(MarketCalendar.is_quote_refresh_time())
        except (RuntimeError, TypeError, ValueError):
            return True

    def _connect_auto_refresh_events(self) -> None:
        self._auto_refresh_connections = []
        for signal, options in self._auto_refresh_signal_specs():
            _slot = self._make_auto_refresh_slot(options)
            signal.connect(_slot)
            self._auto_refresh_connections.append((signal, _slot))

    def _make_auto_refresh_slot(self, options: dict):
        refresh_options = dict(options)

        def _slot(*args):
            self._schedule_context_refresh(*args, **refresh_options)

        return _slot

    def _cleanup_runtime_state(self):
        self._context_refresh_pending = False
        self._candidate_refresh_pending = False
        self._candidate_refresh_followup_scheduled = False
        dependency_timer = getattr(self, "_background_dependency_timer", None)
        if dependency_timer is not None:
            dependency_timer.stop()
        timer = getattr(self, "_auto_refresh_timer", None)
        if timer is not None:
            with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                timer.stop()
        for signal, slot in list(getattr(self, "_auto_refresh_connections", []) or []):
            with suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._auto_refresh_connections = []
        super()._cleanup_runtime_state()

    def _schedule_context_refresh(
        self,
        *_args,
        prime_snapshots: bool = True,
        force_snapshots: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> None:
        if self._background_preload_requested and not self._background_preload_done:
            self._maybe_start_background_candidate_rebuild()
            return
        is_current = self._is_current_workspace_tab()
        allow_snapshot_refresh = self._allow_context_snapshot_refresh()
        if prime_snapshots and allow_snapshot_refresh:
            self._prime_stock_context_snapshots(
                self._workspace(),
                force=force_snapshots,
                include_fund=include_fund,
                include_lhb=include_lhb and is_current,
            )
        if getattr(self, "_workspace_noninteractive_loaded", False):
            self._context_refresh_pending = True
            return
        if not is_current:
            self._context_refresh_pending = True
            return
        self._context_refresh_pending = False
        self._status_primary = "等待综合候选自动刷新"
        self._status_freshness = "数据源已更新"
        self._refresh_status()
        self._auto_refresh_timer.start(self.AUTO_REFRESH_DEBOUNCE_MS)

    def _queue_context_refresh(self) -> None:
        self._context_refresh_pending = False
        self._auto_refresh_timer.start(self.AUTO_REFRESH_DEBOUNCE_MS)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.lbl_status = QLabel("未刷新")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、来源或信号...")
        self.search_box.setMinimumWidth(180)
        self.search_box.setMaximumWidth(280)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        btn_refresh = QPushButton("刷新")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_candidates)

        toolbar = self.build_tab_toolbar("综合候选", self.lbl_status, [self.search_box], [btn_refresh])
        layout.addWidget(toolbar)

        self.table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无综合候选", loading_title="刷新中...")

        self.model = StockTableModel(self.COLUMNS)
        self.model.set_plain_style_headers(["来源", "核心信号", "最近时间"])
        self.model.set_muted_text_headers(["共振分", "来源数", "信号数", "来源", "核心信号", "最近时间"])
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.table.setItemDelegate(StockItemDelegate(self.table))

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [52, 72, 76, 70, 72, 78, 70, 58, 58, 150, 430, 92]
        for i, width in enumerate(default_widths):
            if i < len(self.model.headers):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(i, width)
        with suppress(ValueError):
            header.setSectionResizeMode(self.model.headers.index("核心信号"), QHeaderView.ResizeMode.Stretch)

        restored_sort = self.bind_header_persistence(self.table, self.HEADER_STATE_KEY)
        if not restored_sort:
            with suppress(ValueError):
                score_col = self.model.headers.index("共振分")
                self.table.sortByColumn(score_col, Qt.SortOrder.DescendingOrder)

        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)
        self._refresh_status()

    def _workspace(self):
        cursor = self.parent()
        while cursor is not None:
            if hasattr(cursor, "collect_stock_context"):
                return cursor
            cursor = cursor.parent() if hasattr(cursor, "parent") else None
        return None

    def _read_stock_context(self):
        workspace = self._workspace()
        context_reader = getattr(workspace, "collect_stock_context", None)
        if not callable(context_reader):
            return {}
        try:
            return context_reader(
                allow_lhb_cache_compute=False,
                allow_async_snapshot_refresh=self._allow_context_snapshot_refresh(),
            )
        except TypeError:
            return context_reader()

    def get_data_lineage(self) -> dict:
        result = self._last_candidate_result
        if result is None:
            return self._candidate_service.empty_lineage(
                row_count=len(getattr(self.model, "row_data", None) or [])
            ).as_dict()
        return result.lineage.as_dict()

    def _tab_titles(self) -> dict[str, str]:
        workspace = self._workspace()
        if workspace is None or not hasattr(workspace, "tab_specs"):
            return {}
        return {
            str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip()
            for spec in workspace.tab_specs()
        }

    def _build_candidate_rows(
        self,
        context: dict[str, list[StockSignal]],
        tab_titles: dict[str, str] | None = None,
    ) -> list[dict]:
        return build_stock_candidate_rows(
            context,
            tab_titles=tab_titles if tab_titles is not None else _candidate_tab_titles(self),
        )

    def refresh_candidates(self):
        with ui_stall_span("StockCandidateTab.refresh_candidates", tab="stock_candidates", signal="context_refresh"):
            self._start_candidate_refresh_async()

    def _start_candidate_refresh_async(self) -> None:
        if self._candidate_refresh_running or self._candidate_refresh_followup_scheduled:
            self._candidate_refresh_pending = True
            return
        self._candidate_refresh_running = True
        self._candidate_refresh_pending = False
        tab_titles = self._tab_titles()
        provider_status = self._read_provider_status()
        workspace = self._workspace()
        snapshot = capture_workspace_stock_context(workspace)
        if snapshot is None:
            snapshot = _immutable_candidate_snapshot(self._read_stock_context())
        read_policy = StockContextReadPolicy.build(allow_lhb_cache_compute=False)

        def _load_bg(_cancellation_token):
            context = StockContextQueryService(snapshot).query_by_code(read_policy)
            return StockCandidatesDataService(
                context_reader=lambda: context,
                row_builder=lambda context: build_stock_candidate_rows(context, tab_titles=tab_titles),
                provider_status_reader=lambda: provider_status,
            ).load()

        task_lifecycle_for(self, runner=task_manager).run_background(
            "candidate_refresh",
            _load_bg,
            on_success=self._on_candidate_refresh_success,
            on_error=self._on_candidate_refresh_error,
            task_id=task_registry.workspace(self.REFRESH_TASK_ID),
            timeout_sec=self.BACKGROUND_REFRESH_TIMEOUT_SECONDS,
            runner=task_manager,
        )

    def _schedule_candidate_refresh_followup(self) -> None:
        self._candidate_refresh_pending = False
        if self._candidate_refresh_followup_scheduled:
            return
        self._candidate_refresh_followup_scheduled = True
        QTimer.singleShot(0, self._run_candidate_refresh_followup)

    def _run_candidate_refresh_followup(self) -> None:
        if not self._candidate_refresh_followup_scheduled:
            return
        self._candidate_refresh_followup_scheduled = False
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._candidate_refresh_pending = False
        self.refresh_candidates()

    def _on_candidate_refresh_success(self, result) -> None:
        self._candidate_refresh_running = False
        if getattr(self, "_runtime_cleanup_done", False):
            return
        workspace = self._workspace()
        publisher = getattr(workspace, "publish_stock_context_signal_index", None)
        if callable(publisher):
            with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                publisher(result.signal_index)
        self._apply_candidate_result(result)
        if self._candidate_refresh_pending:
            self._schedule_candidate_refresh_followup()
            return
        if self._background_preload_requested:
            self._background_preload_done = True
            self._background_preload_error = ""

    def _on_candidate_refresh_error(self, message: str) -> None:
        self._candidate_refresh_running = False
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._status_primary = "综合候选加载失败"
        self._status_freshness = str(message or "").strip() or "后台刷新异常"
        self._refresh_status()
        if self._candidate_refresh_pending:
            self._schedule_candidate_refresh_followup()
            return
        if self._background_preload_requested:
            self._background_preload_done = True
            self._background_preload_error = str(message or "").strip() or "后台刷新异常"

    def _apply_candidate_result(self, result) -> None:
        self._last_candidate_result = result
        rows = result.rows
        rows_changed = result.signature != self._last_candidate_signature
        if rows_changed:
            self.model.update_data(rows, hydrate_latest_quotes=False)
            self._last_candidate_signature = result.signature
            self.refresh_table_from_latest_snapshot(self.model)
        if rows:
            self.table_state.show_table()
            self._status_primary = "综合候选已刷新"
            self._status_freshness = "内存上下文"
        else:
            self.table_state.show_empty("暂无综合候选")
            self._status_primary = "暂无综合候选"
            self._status_freshness = "等待其他Tab刷新"
        self._refresh_status()

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_status()

    def _refresh_status(self):
        total = len(getattr(self.model, "row_data", None) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        keyword = str(self.search_box.text() or "").strip() if hasattr(self, "search_box") else ""
        self.lbl_status.setText(
            self.format_workspace_status(
                self._status_primary,
                result=self._status_metric("结果 ", f"{visible}/{total}", "只") if total else "",
                freshness=self._status_freshness,
                current_filter=keyword or "全部",
                next_step="右键查看股票全景" if total else "刷新其他Tab后再汇总",
            )
        )

    def _row_from_proxy_index(self, index):
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row < 0 or row >= len(self.model.row_data):
            return None
        return self.model.row_data[row]

    def _visible_code_list(self, clicked_visual_row: int) -> tuple[list[dict], int]:
        code_list = []
        current_idx = 0
        for visual_row in range(self.proxy_model.rowCount()):
            source_idx = self.proxy_model.mapToSource(self.proxy_model.index(visual_row, 0))
            if source_idx.row() >= len(self.model.row_data):
                continue
            row_dict = dict(self.model.row_data[source_idx.row()] or {})
            code_list.append(row_dict)
            if visual_row == clicked_visual_row:
                current_idx = len(code_list) - 1
        return code_list, current_idx

    def _on_double_click(self, index):
        row = self._row_from_proxy_index(index)
        if not row:
            return
        code = str(row.get("代码") or "").strip()
        if not code:
            return
        code_list, current_idx = self._visible_code_list(index.row())
        ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        row = self._row_from_proxy_index(self.table.indexAt(pos))
        if not row:
            return
        code = str(row.get("代码") or "").strip()
        name = str(row.get("名称") or "").strip()
        if not code:
            return

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(self, code, name, vcp_data=row)

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        return super().get_realtime_quote_codes(current_model=current_model or self.model)
