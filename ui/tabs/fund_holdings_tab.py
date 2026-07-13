# -*- coding: utf-8 -*-
"""基金持仓 Tab。"""

from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
)

from app.services.tab_data_lineage_service import TabDataLineageService
from app.services.ui_config_service import app_config
from app.services.ui_diagnostics_service import ui_stall_span
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_fund_holdings_service import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    fund_holdings_store,
    fund_holdings_sync_service,
)
from app.services.ui_industry_chain_service import (
    load_cached_ai_industry_chain_context_map,
    load_cached_ai_industry_chain_stock_codes,
)
from app.services.ui_task_lifecycle_service import invoke_with_cancellation, task_lifecycle_for
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from ui.components import (
    MultiSelectFilterButton,
    TableStateWrapper,
    VCPTableView,
)
from ui.components import format_multi_select_summary as format_multi_select_summary
from ui.models.table_models import StockItemDelegate, StockTableModel
from ui.tabs.base_stock_refresh import load_cached_finance_snapshot
from ui.tabs.base_stock_tab import BaseStockTab, _is_direct_workspace_tab
from ui.tabs.fund_holdings_filter_menu import build_change_filter_menu, build_quarter_filter_menu
from ui.tabs.fund_holdings_filter_proxy import FundHoldingsFilterProxyModel
from ui.tabs.fund_holdings_filter_state import (
    build_current_filter_summary,
    extract_capital_attribute_filter_options,
    extract_subject_filter_options,
    format_change_filter_button_text,
    format_quarter_filter_button_text,
    quarter_scope_loaded,
    resolve_quarter_query_scope,
)
from ui.tabs.fund_holdings_payload import (
    build_fund_holdings_view_rows,
    load_ai_chain_context_map_safely,
    load_fund_holdings_view_payload,
    query_change_rows_for_scope,
    resolve_query_quarters,
)
from ui.tabs.fund_holdings_rules import (
    FUND_CHANGE_TYPE_OPTIONS,
    FUND_DISPLAY_PLACEHOLDER,
    capital_attribute_label,
    filter_ai_related_concepts,
    is_ai_related_concept,
)
from ui.tabs.fund_holdings_view_state import (
    FundHoldingsViewState,
    quarter_mode_from_filter,
    read_fund_holdings_view_state,
    sort_order_to_int,
    write_fund_holdings_view_state,
)


class FundHoldingsTab(BaseStockTab):
    _F5_AUTO_SYNC_DELAY_MS = 18000
    _QUOTE_SNAPSHOT_REFRESH_DELAY_MS = 120
    _SUBJECT_CODE_QFII = SUBJECT_QFII["subject_code"]
    _SUBJECT_CODE_RUIYUAN = SUBJECT_RUIYUAN["subject_code"]
    _QUARTER_FILTER_LATEST = "__LATEST__"
    _QUARTER_FILTER_ALL = "__ALL__"
    _CHANGE_FILTER_ALL = "__ALL__"
    _QUERY_SCOPE_LATEST = "latest"
    _QUERY_SCOPE_ALL = "all"
    _QUERY_SCOPE_SELECTED = "selected"
    _DISPLAY_PLACEHOLDER = FUND_DISPLAY_PLACEHOLDER
    _CHANGE_TYPE_OPTIONS = FUND_CHANGE_TYPE_OPTIONS
    _CAPITAL_ATTRIBUTE_OPTIONS = (
        QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
        QFII_CAPITAL_ATTRIBUTE_CLIENT,
        QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    )
    _CAPITAL_ATTRIBUTE_LABELS = {
        QFII_CAPITAL_ATTRIBUTE_UNMARKED: _DISPLAY_PLACEHOLDER,
    }
    _VIEW_STATE_PREFIX = "fund_holdings_view_state_v2"
    _stock_universe_provider = staticmethod(load_cached_ai_industry_chain_stock_codes)
    _chain_context_provider = staticmethod(load_cached_ai_industry_chain_context_map)
    VIEW_LOAD_TIMEOUT_SEC = 90.0
    SYNC_TIMEOUT_SEC = 15 * 60.0

    def __init__(
        self,
        data_provider,
        parent=None,
        autoload: bool = True,
        initial_load_delay_ms: int = 0,
    ):
        super().__init__(data_provider=data_provider, parent=parent)
        try:
            self._initial_load_delay_ms = max(0, int(initial_load_delay_ms))
        except (TypeError, ValueError):
            self._initial_load_delay_ms = 0
        self._autoload = bool(autoload)
        self._initial_load_started = False
        self._initial_load_task_id = self._build_workspace_task_id(f"initial_load_{id(self)}")
        self._latest_quarter_map: dict[str, str] = {}
        self._latest_sync_map: dict[str, dict] = {}
        self._sync_task_id = ""
        self._sync_active = False
        self._loaded_quarter_scope = ""
        self._loaded_quarter_keys: set[str] = set()
        self._filter_menu_updating = False
        self._quarter_actions: dict[str, QAction] = {}
        self._change_actions: dict[str, QAction] = {}
        self._change_menu_built = False
        self._concept_sector_cache: dict[str, str] = {}
        self._ai_chain_context_map: dict[str, str] | None = None
        self._fund_holdings_lineage_service = TabDataLineageService(
            key="fund_holdings",
            source="fund_holdings_store + local_quote_snapshot",
            provider="ui_fund_holdings_service",
            cache_refs=(
                "data/vcp_hunter.db:fund holdings tables",
                "global_store.quotes",
                "local_tdx_cache",
            ),
            provider_status_reader=self._read_provider_status,
        )
        self._last_fund_holdings_result = None
        self._settings = self._create_settings()
        self._pending_f5_auto_sync = False
        self._cache_reload_refresh_pending = False
        self._quote_snapshot_refresh_pending = False
        self._pending_quote_snapshot_model = None
        self._restoring_view_state = False
        self._view_state_restored = False
        self._view_state_save_timer = QTimer(self)
        self._view_state_save_timer.setSingleShot(True)
        self._view_state_save_timer.setInterval(300)
        self._view_state_save_timer.timeout.connect(self._save_view_state)

        self._init_ui()
        if self._autoload:
            self._reload_from_db(quarter_scope=self._QUERY_SCOPE_LATEST)
            self._initial_load_started = True
        else:
            self._set_initial_loading_state("基金持仓待加载", "首次进入时自动读取本地数据库")

        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)
        event_bus.sig_app_closing.connect(self._save_view_state)
        event_bus.sig_fund_holdings_updated.connect(self._on_fund_holdings_updated)

    def showEvent(self, event):
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._ensure_initial_load_started()

    def prime_background_load(self):
        self._ensure_initial_load_started()

    def _is_current_workspace_tab(self) -> bool:
        return _is_direct_workspace_tab(self)

    @staticmethod
    def _create_settings():
        return app_config.section(
            "tabs/FundHoldingsTab",
            legacy_scope="FundHoldingsTab",
        )

    def _view_state_key(self, name: str) -> str:
        return f"{self._VIEW_STATE_PREFIX}/{name}"

    @staticmethod
    def _build_workspace_task_id(name: str) -> str:
        normalized = str(name or "").strip().replace("::", "_")
        if not normalized:
            normalized = "task"
        return task_registry.workspace(f"fund_holdings_{normalized}").task_id

    def _selected_subject_names(self) -> set[str]:
        return self.cmb_subject.selected_values()

    def _selected_capital_attributes(self) -> set[str]:
        return self.cmb_capital_attribute.selected_values()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        filter_widgets = self._init_filter_controls()
        action_widgets = self._init_action_controls()
        toolbar = self.build_tab_toolbar("基金持仓", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self._init_table()
        layout.addWidget(self.table_state, 1)

    def _init_filter_controls(self):
        self.lbl_status = QLabel("等待同步基金持仓数据库")

        self.cmb_subject = MultiSelectFilterButton("全部主体")
        self.cmb_subject.setAccessibleName("主体筛选")
        self.cmb_subject.setMinimumWidth(190)
        self.cmb_subject.setMaximumWidth(280)
        self.cmb_subject.selectionChanged.connect(self._on_subject_selection_changed)

        self.cmb_capital_attribute = MultiSelectFilterButton("全部资金属性")
        self.cmb_capital_attribute.setAccessibleName("资金属性筛选")
        self.cmb_capital_attribute.setMinimumWidth(150)
        self.cmb_capital_attribute.setMaximumWidth(220)
        self.cmb_capital_attribute.selectionChanged.connect(self._on_capital_attribute_selection_changed)

        self.btn_quarter = QToolButton()
        self.btn_quarter.setAccessibleName("季度筛选")
        self.btn_quarter.setMinimumWidth(140)
        self.btn_quarter.setMaximumWidth(180)
        self.btn_quarter.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_quarter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_quarter = QMenu(self.btn_quarter)
        self.btn_quarter.setMenu(self.menu_quarter)

        self.btn_change = QToolButton()
        self.btn_change.setAccessibleName("变动类型筛选")
        self.btn_change.setMinimumWidth(140)
        self.btn_change.setMaximumWidth(180)
        self.btn_change.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_change.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_change = QMenu(self.btn_change)
        self.btn_change.setMenu(self.menu_change)
        self.menu_change.aboutToShow.connect(self._ensure_change_menu_built)
        self._refresh_change_button_text()
        self._refresh_subject_button_text()
        self._refresh_capital_attribute_button_text()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、主体、资金属性、概念或变化...")
        self.search_box.setAccessibleName("基金持仓筛选")
        self.search_box.setAccessibleDescription("按代码、名称、主体、资金属性、概念或变化筛选基金持仓")
        self.search_box.setMinimumWidth(200)
        self.search_box.setMaximumWidth(320)
        self.search_box.textChanged.connect(self._apply_filters)

        return [
            self.cmb_subject,
            self.cmb_capital_attribute,
            self.btn_quarter,
            self.btn_change,
            self.search_box,
        ]

    def _init_action_controls(self):
        self.btn_update = QToolButton()
        self.btn_update.setText("全部更新")
        self.btn_update.setAccessibleName("更新基金持仓数据库")
        self.btn_update.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_update.clicked.connect(self.run_full_sync)
        return [self.btn_update]

    def _init_table(self):
        self.columns = [
            "代码",
            "名称",
            "市价",
            "涨幅%",
            "市值",
            "主体",
            "资金属性",
            "季度",
            "变化类型",
            "本期占比",
            "本期持股",
            "上期持股",
            "持股变化",
            "概念板块",
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.model.set_plain_style_headers(["主体", "资金属性", "季度", "变化类型", "概念板块"])
        self.model.set_muted_text_headers(
            ["主体", "资金属性", "季度", "本期占比", "本期持股", "上期持股", "持股变化", "概念板块"]
        )

        self.proxy_model = FundHoldingsFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(
            self.table, empty_title="暂无基金持仓数据", loading_title="同步基金持仓数据中..."
        )
        self._configure_table_header()
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _configure_table_header(self):
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        default_widths = [70, 90, 70, 70, 75, 180, 96, 90, 80, 90, 110, 110, 110, 240]
        for index, width in enumerate(default_widths):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(index, width)
        self.bind_header_persistence(self.table, "fund_holdings_header_state_v3")
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)

    def _set_initial_loading_state(self, title: str, subtitle: str = ""):
        self.lbl_status.setText(title)
        self.table_state.show_loading(title, subtitle)

    def _ensure_initial_load_started(self):
        if self._autoload or self._initial_load_started:
            return
        self._initial_load_started = True
        self._set_initial_loading_state("正在加载基金持仓数据...", "首次进入时后台构建持仓视图")
        if self._initial_load_delay_ms > 0:
            QTimer.singleShot(self._initial_load_delay_ms, self._reload_from_db_async)
        else:
            self._reload_from_db_async()

    @classmethod
    def _load_ai_chain_context_map(cls) -> dict[str, str]:
        return load_ai_chain_context_map_safely(cls._chain_context_provider)

    @classmethod
    def _resolve_query_quarters(
        cls,
        latest_quarter_map: dict[str, str],
        *,
        quarter_scope: str,
        quarter_keys=None,
    ) -> set[str] | None:
        return resolve_query_quarters(
            latest_quarter_map,
            quarter_scope=quarter_scope,
            quarter_keys=quarter_keys,
            latest_scope=cls._QUERY_SCOPE_LATEST,
            all_scope=cls._QUERY_SCOPE_ALL,
            selected_scope=cls._QUERY_SCOPE_SELECTED,
        )

    @classmethod
    def _query_change_rows_for_scope(cls, quarter_keys: set[str] | None) -> list[dict]:
        return query_change_rows_for_scope(quarter_keys, stock_universe_provider=cls._stock_universe_provider)

    @classmethod
    def _load_view_payload(
        cls,
        data_provider,
        *,
        quarter_scope: str = _QUERY_SCOPE_LATEST,
        quarter_keys=None,
        cancellation_token=None,
    ) -> dict:
        return load_fund_holdings_view_payload(
            quarter_scope=quarter_scope,
            quarter_keys=quarter_keys,
            stock_universe_provider=cls._stock_universe_provider,
            chain_context_provider=cls._chain_context_provider,
            capital_attribute_labels=cls._CAPITAL_ATTRIBUTE_LABELS,
            latest_scope=cls._QUERY_SCOPE_LATEST,
            all_scope=cls._QUERY_SCOPE_ALL,
            selected_scope=cls._QUERY_SCOPE_SELECTED,
            cancellation_token=cancellation_token,
        )

    def _apply_view_payload(self, payload: dict):
        with ui_stall_span(
            "FundHoldingsTab._apply_view_payload",
            tab="fund_holdings",
            signal=str(payload.get("loaded_quarter_scope") or ""),
        ):
            self._latest_quarter_map = dict(payload.get("latest_quarter_map") or {})
            self._latest_sync_map = dict(payload.get("latest_sync_map") or {})
            self._concept_sector_cache = dict(payload.get("concept_sector_cache") or {})
            self._loaded_quarter_scope = str(payload.get("loaded_quarter_scope") or "").strip()
            self._loaded_quarter_keys = {
                str(quarter_key or "").strip()
                for quarter_key in (payload.get("loaded_quarter_keys") or [])
                if str(quarter_key or "").strip()
            }
            view_rows = list(payload.get("view_rows") or [])
            should_defer = getattr(self, "_should_defer_view_payload_finish", None)
            defer_update = callable(should_defer) and should_defer()
            if defer_update:
                QTimer.singleShot(
                    0,
                    lambda view_rows=view_rows: FundHoldingsTab._apply_view_rows_and_finish(
                        self,
                        view_rows,
                        defer_finish=True,
                    ),
                )
            else:
                FundHoldingsTab._apply_view_rows_and_finish(self, view_rows, defer_finish=False)

    def _apply_view_rows_and_finish(self, view_rows: list[dict], *, defer_finish: bool) -> None:
        if getattr(self, "_runtime_cleanup_done", False):
            return
        with ui_stall_span(
            "FundHoldingsTab._apply_view_rows_and_finish",
            tab="fund_holdings",
            signal="deferred" if defer_finish else "sync",
        ):
            self.model.update_data(view_rows, hydrate_latest_quotes=False)

        def finish(rows=view_rows):
            if getattr(self, "_runtime_cleanup_done", False):
                return
            skip_empty_state = FundHoldingsTab._finish_apply_view_payload(self, rows)
            if not skip_empty_state:
                FundHoldingsTab._show_empty_view_payload_if_needed(self, rows)

        if defer_finish:
            QTimer.singleShot(0, finish)
        else:
            finish()

    def _should_defer_view_payload_finish(self) -> bool:
        return callable(getattr(self, "deleteLater", None)) and not bool(getattr(self, "_autoload", True))

    def _finish_apply_view_payload(self, view_rows: list[dict]) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return True
        self._refresh_filter_options()
        self._restore_view_state()
        ensure_scope_loaded = getattr(self, "_ensure_current_quarter_scope_loaded", None)
        if callable(ensure_scope_loaded) and ensure_scope_loaded(async_load=False):
            return True
        self._apply_filters()
        self._schedule_visible_quote_snapshot_refresh(self.model)
        self._update_status_summary()
        lineage_updater = getattr(self, "_refresh_fund_holdings_lineage", None)
        if callable(lineage_updater):
            lineage_updater(view_rows)
        return False

    def _show_empty_view_payload_if_needed(self, view_rows: list[dict]) -> None:
        if not view_rows and not getattr(self, "_sync_active", False):
            self.table_state.show_empty("暂无基金持仓数据", "请使用右上角“刷新”同步 QFII 或睿远持仓")

    def _reload_from_db_async(
        self,
        *,
        quarter_scope: str | None = None,
        quarter_keys=None,
    ):
        if quarter_scope is None:
            scope, keys = self._current_quarter_query_scope()
        else:
            scope = str(quarter_scope or self._QUERY_SCOPE_LATEST).strip().lower()
            keys = set(quarter_keys or [])

        def _load_bg(cancellation_token):
            return self._load_view_payload(
                self.data_provider,
                quarter_scope=scope,
                quarter_keys=keys,
                cancellation_token=cancellation_token,
            )

        def _on_success(payload):
            if getattr(self, "_runtime_cleanup_done", False):
                return
            self._apply_view_payload(payload)

        def _on_error(message: str):
            if getattr(self, "_runtime_cleanup_done", False):
                return
            self._initial_load_started = False
            detail = str(message or "").strip() or "未知异常"
            self.lbl_status.setText(f"基金持仓加载失败：{detail}")
            self.table_state.show_error(
                "基金持仓加载失败",
                detail,
                action_text="重试",
                action_callback=self._ensure_initial_load_started,
            )

        task_lifecycle_for(self, runner=task_manager).run_background(
            "view-load",
            _load_bg,
            on_success=_on_success,
            on_error=_on_error,
            task_id=self._build_workspace_task_id(f"load_{scope}_{id(self)}"),
            timeout_sec=self.VIEW_LOAD_TIMEOUT_SEC,
        )

    def _ensure_change_menu_built(self) -> None:
        if self._change_menu_built and self._change_actions:
            return
        self._build_change_menu()

    def _build_change_menu(self):
        self._change_actions = build_change_filter_menu(
            self.menu_change,
            self,
            all_key=self._CHANGE_FILTER_ALL,
            options=self._CHANGE_TYPE_OPTIONS,
            toggled_callback=self._on_change_selection_toggled,
        )
        self._change_menu_built = True
        self._filter_menu_updating = True
        try:
            self._change_actions[self._CHANGE_FILTER_ALL].setChecked(True)
        finally:
            self._filter_menu_updating = False
        self._refresh_change_button_text()

    def _build_quarter_menu(self, quarters: list[str]):
        self._quarter_actions = build_quarter_filter_menu(
            self.menu_quarter,
            self,
            latest_key=self._QUARTER_FILTER_LATEST,
            all_key=self._QUARTER_FILTER_ALL,
            quarters=quarters,
            toggled_callback=self._on_quarter_selection_toggled,
        )

    def _selected_change_types(self) -> set[str]:
        if not self._change_actions:
            return set()
        if (
            self._change_actions.get(self._CHANGE_FILTER_ALL, None)
            and self._change_actions[self._CHANGE_FILTER_ALL].isChecked()
        ):
            return set()
        return {
            label
            for label in self._CHANGE_TYPE_OPTIONS
            if self._change_actions.get(label) and self._change_actions[label].isChecked()
        }

    def _quarter_filter_state(self) -> tuple[bool, set[str]]:
        if (
            self._quarter_actions.get(self._QUARTER_FILTER_LATEST)
            and self._quarter_actions[self._QUARTER_FILTER_LATEST].isChecked()
        ):
            return True, set()
        if (
            self._quarter_actions.get(self._QUARTER_FILTER_ALL)
            and self._quarter_actions[self._QUARTER_FILTER_ALL].isChecked()
        ):
            return False, set()
        selected = {
            key
            for key, action in self._quarter_actions.items()
            if key not in {self._QUARTER_FILTER_LATEST, self._QUARTER_FILTER_ALL} and action.isChecked()
        }
        if not selected:
            return True, set()
        return False, selected

    def _current_quarter_query_scope(self) -> tuple[str, set[str]]:
        latest_only, selected_quarters = self._quarter_filter_state()
        return resolve_quarter_query_scope(
            latest_only,
            selected_quarters,
            latest_scope=self._QUERY_SCOPE_LATEST,
            all_scope=self._QUERY_SCOPE_ALL,
            selected_scope=self._QUERY_SCOPE_SELECTED,
        )

    def _quarter_scope_loaded(self, scope: str, quarter_keys: set[str]) -> bool:
        return quarter_scope_loaded(
            scope,
            quarter_keys,
            loaded_scope=getattr(self, "_loaded_quarter_scope", ""),
            loaded_keys=getattr(self, "_loaded_quarter_keys", set()) or set(),
            latest_scope=self._QUERY_SCOPE_LATEST,
            all_scope=self._QUERY_SCOPE_ALL,
            selected_scope=self._QUERY_SCOPE_SELECTED,
        )

    def _ensure_current_quarter_scope_loaded(self, *, async_load: bool) -> bool:
        scope, quarter_keys = self._current_quarter_query_scope()
        if self._quarter_scope_loaded(scope, quarter_keys):
            return False
        if async_load:
            self._set_initial_loading_state(
                "Loading fund holding quarters...",
                "Reading the selected local quarter scope",
            )
        if async_load or scope == self._QUERY_SCOPE_LATEST:
            self._reload_from_db_async(quarter_scope=scope, quarter_keys=quarter_keys)
        else:
            self._reload_from_db(quarter_scope=scope, quarter_keys=quarter_keys)
        return True

    def _set_change_filter_values(self, values: set[str] | list[str], *, apply: bool = True):
        self._ensure_change_menu_built()
        selected = {
            str(value or "").strip()
            for value in (values or [])
            if str(value or "").strip() in self._CHANGE_TYPE_OPTIONS
        }

        self._filter_menu_updating = True
        try:
            self._change_actions[self._CHANGE_FILTER_ALL].setChecked(not selected)
            for label in self._CHANGE_TYPE_OPTIONS:
                self._change_actions[label].setChecked(label in selected)
        finally:
            self._filter_menu_updating = False

        self._refresh_change_button_text()
        if apply:
            self._apply_filters()

    def _set_quarter_filter_state(
        self,
        *,
        latest_only: bool = False,
        all_quarters: bool = False,
        selected_quarters: set[str] | list[str] | None = None,
        apply: bool = True,
    ):
        selected = {
            str(value or "").strip()
            for value in (selected_quarters or [])
            if str(value or "").strip() in self._quarter_actions
            and str(value or "").strip() not in {self._QUARTER_FILTER_LATEST, self._QUARTER_FILTER_ALL}
        }

        if not latest_only and not all_quarters and not selected:
            latest_only = True

        self._filter_menu_updating = True
        try:
            self._quarter_actions[self._QUARTER_FILTER_LATEST].setChecked(bool(latest_only))
            self._quarter_actions[self._QUARTER_FILTER_ALL].setChecked(bool(all_quarters))
            for key, action in self._quarter_actions.items():
                if key in {self._QUARTER_FILTER_LATEST, self._QUARTER_FILTER_ALL}:
                    continue
                action.setChecked(key in selected)
        finally:
            self._filter_menu_updating = False

        self._refresh_quarter_button_text()
        if apply:
            if self._ensure_current_quarter_scope_loaded(async_load=False):
                return
            self._apply_filters()

    def _on_change_selection_toggled(self, _checked: bool):
        if self._filter_menu_updating:
            return

        if self.sender() is self._change_actions.get(self._CHANGE_FILTER_ALL):
            self._set_change_filter_values(set(), apply=True)
            return

        selected = {label for label in self._CHANGE_TYPE_OPTIONS if self._change_actions[label].isChecked()}
        self._set_change_filter_values(selected, apply=True)

    def _on_quarter_selection_toggled(self, _checked: bool):
        if self._filter_menu_updating:
            return

        sender = self.sender()
        if sender is self._quarter_actions.get(self._QUARTER_FILTER_LATEST):
            self._set_quarter_filter_state(latest_only=True, apply=True)
            return
        if sender is self._quarter_actions.get(self._QUARTER_FILTER_ALL):
            self._set_quarter_filter_state(all_quarters=True, apply=True)
            return

        selected_quarters = {
            key
            for key, action in self._quarter_actions.items()
            if key not in {self._QUARTER_FILTER_LATEST, self._QUARTER_FILTER_ALL} and action.isChecked()
        }
        self._set_quarter_filter_state(selected_quarters=selected_quarters, apply=True)

    def _refresh_change_button_text(self):
        text, tooltip = format_change_filter_button_text(
            self._selected_change_types(),
            self._CHANGE_TYPE_OPTIONS,
        )
        self.btn_change.setText(text)
        self.btn_change.setToolTip(tooltip)

    def _refresh_quarter_button_text(self):
        latest_only, selected = self._quarter_filter_state()
        text, tooltip = format_quarter_filter_button_text(latest_only, selected)
        self.btn_quarter.setText(text)
        self.btn_quarter.setToolTip(tooltip)

    def _schedule_view_state_save(self):
        if self._restoring_view_state:
            return
        self._view_state_save_timer.start()

    def _refresh_subject_button_text(self):
        self.cmb_subject.apply_summary("主体", all_text="全部")

    def _refresh_capital_attribute_button_text(self):
        self.cmb_capital_attribute.apply_summary("资金属性", all_text="全部")

    def _on_subject_selection_changed(self):
        self._refresh_subject_button_text()
        self._apply_filters()

    def _on_capital_attribute_selection_changed(self):
        self._refresh_capital_attribute_button_text()
        self._apply_filters()

    @classmethod
    def _capital_attribute_label(cls, value: str) -> str:
        return capital_attribute_label(
            value,
            cls._CAPITAL_ATTRIBUTE_LABELS,
            placeholder=cls._DISPLAY_PLACEHOLDER,
        )

    def _latest_sync_freshness_text(self) -> str:
        sync_times = []
        for subject_code in (self._SUBJECT_CODE_QFII, self._SUBJECT_CODE_RUIYUAN):
            finished_at = str((self._latest_sync_map.get(subject_code) or {}).get("finished_at") or "").strip()
            if finished_at:
                sync_times.append(finished_at)
        if not sync_times:
            return "快照待同步"
        latest_sync = max(sync_times)
        return f"快照 {latest_sync[-8:]}"

    def _latest_sync_updated_at(self) -> str:
        sync_times = []
        for sync_state in self._latest_sync_map.values():
            if not isinstance(sync_state, dict):
                continue
            finished_at = str(sync_state.get("finished_at") or "").strip()
            if finished_at:
                sync_times.append(finished_at)
        return max(sync_times) if sync_times else ""

    def _latest_loaded_quarter(self) -> str:
        quarters = [str(item or "").strip() for item in self._latest_quarter_map.values()]
        quarters = [item for item in quarters if item and item != self._DISPLAY_PLACEHOLDER]
        return max(quarters) if quarters else ""

    def _fund_holdings_lineage_status(self, rows: list[dict]) -> str:
        if self._sync_active:
            return "syncing"
        if rows:
            return "loaded"
        if not self._initial_load_started and not self._autoload:
            return "deferred"
        return "empty"

    def _describe_fund_holdings_rows(self, rows: list[dict]):
        warnings = []
        status = self._fund_holdings_lineage_status(rows)
        if not rows:
            warnings.append("fund_holdings_rows_deferred" if status == "deferred" else "fund_holdings_rows_empty")
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else len(rows)
        return self._fund_holdings_lineage_service.describe(
            rows,
            updated_at=self._latest_sync_updated_at(),
            triggered_network=bool(self._sync_active),
            warnings=warnings,
            extra={
                "status": status,
                "visible_row_count": visible,
                "loaded_quarter_scope": self._loaded_quarter_scope,
                "loaded_quarter_keys": sorted(self._loaded_quarter_keys),
                "latest_quarter": self._latest_loaded_quarter(),
                "current_filter": self._current_filter_summary(),
                "sync_active": self._sync_active,
                "sync_task_id": self._sync_task_id,
            },
        )

    def _refresh_fund_holdings_lineage(self, rows: list[dict] | None = None):
        row_list = list(rows if rows is not None else self.get_row_data(current_model=getattr(self, "model", None)))
        result = self._describe_fund_holdings_rows(row_list)
        self._last_fund_holdings_result = result
        return result

    def get_data_lineage(self) -> dict:
        result = self._last_fund_holdings_result
        if result is None:
            result = self._refresh_fund_holdings_lineage()
        return result.lineage.as_dict()

    def _current_filter_summary(self) -> str:
        latest_only, selected_quarters = self._quarter_filter_state()
        return build_current_filter_summary(
            subject_names=self._selected_subject_names(),
            capital_attributes=self._selected_capital_attributes(),
            capital_label=self._capital_attribute_label,
            latest_only=latest_only,
            selected_quarters=selected_quarters,
            change_types=self._selected_change_types(),
            search_text=self.search_box.text(),
        )

    def _save_view_state(self):
        if self._restoring_view_state:
            return
        try:
            latest_only, selected_quarters = self._quarter_filter_state()
            state = FundHoldingsViewState(
                subject_names=self._selected_subject_names(),
                capital_attributes=self._selected_capital_attributes(),
                search_text=self.search_box.text().strip(),
                quarter_mode=quarter_mode_from_filter(latest_only, selected_quarters),
                quarter_values=set(selected_quarters),
                change_types=self._selected_change_types(),
                sort_column=-1,
                sort_order=Qt.SortOrder.AscendingOrder.value,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

        try:
            sort_column = int(self.table.sorted_column()) if hasattr(self.table, "sorted_column") else -1
        except (AttributeError, RuntimeError, TypeError, ValueError):
            sort_column = -1
        try:
            sort_order = sort_order_to_int(
                self.table.horizontalHeader().sortIndicatorOrder(),
                default=Qt.SortOrder.AscendingOrder.value,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            sort_order = Qt.SortOrder.AscendingOrder.value
        state = FundHoldingsViewState(
            subject_names=state.subject_names,
            capital_attributes=state.capital_attributes,
            search_text=state.search_text,
            quarter_mode=state.quarter_mode,
            quarter_values=state.quarter_values,
            change_types=state.change_types,
            sort_column=sort_column,
            sort_order=sort_order,
        )

        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            write_fund_holdings_view_state(self._settings, self._view_state_key, state)

    def _restore_view_state(self):
        if self._view_state_restored:
            return

        self._restoring_view_state = True
        try:
            state = read_fund_holdings_view_state(
                self._settings,
                self._view_state_key,
                default_sort_order=Qt.SortOrder.AscendingOrder.value,
            )

            self.cmb_subject.set_selected_values(state.subject_names, emit=False)
            self._refresh_subject_button_text()
            self.cmb_capital_attribute.set_selected_values(state.capital_attributes, emit=False)
            self._refresh_capital_attribute_button_text()

            search_was_blocked = self.search_box.blockSignals(True)
            try:
                self.search_box.setText(state.search_text)
            finally:
                self.search_box.blockSignals(search_was_blocked)

            self._set_change_filter_values(state.change_types, apply=False)
            self._set_quarter_filter_state(
                latest_only=state.quarter_mode == "latest",
                all_quarters=state.quarter_mode == "all",
                selected_quarters=state.quarter_values,
                apply=False,
            )

            if 0 <= state.sort_column < self.model.columnCount():
                try:
                    sort_order = Qt.SortOrder(state.sort_order)
                except (TypeError, ValueError):
                    sort_order = Qt.SortOrder.AscendingOrder
                self.table.sortByColumn(state.sort_column, sort_order)
        finally:
            self._restoring_view_state = False
            self._view_state_restored = True

    def _on_sort_indicator_changed(self, _section: int, _order: Qt.SortOrder):
        self._schedule_view_state_save()

    def _set_sync_active(self, active: bool, title: str = "", subtitle: str = ""):
        self._sync_active = bool(active)
        self.btn_update.setEnabled(not self._sync_active)
        self._last_fund_holdings_result = None
        if self._sync_active:
            self.table_state.show_loading(title or "同步基金持仓中...", subtitle)

    def _current_visible_row_counts(self) -> tuple[int, int]:
        total = len(getattr(self.model, "row_data", []) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        return total, visible

    def _set_sync_start_status(self, label: str) -> None:
        total, visible = self._current_visible_row_counts()
        self.lbl_status.setText(
            self.format_workspace_status(
                "基金持仓刷新中",
                result=f"{visible}/{total}只" if total else "0只",
                freshness="本地缓存",
                current_filter=self._current_filter_summary(),
                next_step="等待数据库写入",
                extra_segments=(label,),
            )
        )

    def _handle_sync_success(self, result, label: str) -> None:
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._set_sync_active(False)
        self._reload_from_db_async()
        message = str((result or {}).get("message") or label).strip()
        total, visible = self._current_visible_row_counts()
        self.lbl_status.setText(
            self.format_workspace_status(
                "基金持仓已刷新",
                result=f"{visible}/{total}只" if total else "0只",
                freshness=self._latest_sync_freshness_text(),
                current_filter=self._current_filter_summary(),
                next_step="",
                extra_segments=(message,),
            )
        )
        event_bus.sig_system_log.emit("info", f"[基金持仓] {message}")
        event_bus.sig_fund_holdings_updated.emit()

    def _handle_sync_error(self, error_message: str, label: str, sync_callable) -> None:
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._set_sync_active(False)
        self._reload_from_db_async()
        message = str(error_message or "更新失败").strip()
        total, visible = self._current_visible_row_counts()
        self.lbl_status.setText(
            self.format_workspace_status(
                "基金持仓刷新失败",
                result=f"{visible}/{total}只" if total else "0只",
                freshness="远端失败沿用" if total else "待同步",
                current_filter=self._current_filter_summary(),
                next_step="请稍后重试",
                extra_segments=(message,),
            )
        )
        self.table_state.show_error(
            title="基金持仓更新失败",
            subtitle=message,
            action_text="重试",
            action_callback=lambda: self._run_sync_action(label, sync_callable),
        )
        event_bus.sig_system_log.emit("error", f"[基金持仓] {message}")

    def _run_sync_action(self, label: str, sync_callable):
        if self._sync_active:
            return

        callable_name = getattr(sync_callable, "__name__", "task")
        self._sync_task_id = self._build_workspace_task_id(f"sync_{callable_name}")
        self._set_sync_active(True, "同步基金持仓中...", label)
        self._set_sync_start_status(label)

        task_lifecycle_for(self, runner=task_manager).run_background(
            "sync",
            lambda cancellation_token: invoke_with_cancellation(sync_callable, cancellation_token),
            on_success=lambda result: self._handle_sync_success(result, label),
            on_error=lambda error_message: self._handle_sync_error(error_message, label, sync_callable),
            task_id=self._sync_task_id,
            timeout_sec=self.SYNC_TIMEOUT_SEC,
        )

    def run_auto_sync_after_f5(self) -> bool:
        if self._sync_active:
            return False
        self._run_sync_action("F5后自动更新", fund_holdings_sync_service.sync_latest_all)
        return True

    def schedule_auto_sync_after_f5(self) -> bool:
        if self._pending_f5_auto_sync:
            return False
        self._pending_f5_auto_sync = True
        QTimer.singleShot(self._F5_AUTO_SYNC_DELAY_MS, self._run_pending_auto_sync_after_f5)
        return True

    def _run_pending_auto_sync_after_f5(self) -> bool:
        self._pending_f5_auto_sync = False
        return self.run_auto_sync_after_f5()

    def refresh_data_after_f5(self) -> bool:
        self._schedule_cache_reload_refresh()
        return self.schedule_auto_sync_after_f5()

    def refresh_data_after_ai_industry_chain_update(self) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        self._concept_sector_cache.clear()
        self._ai_chain_context_map = None
        if not self._initial_load_started:
            return False
        self._reload_from_db_async()
        return True

    def run_full_sync(self) -> bool:
        if self._sync_active:
            return False
        self._run_sync_action("全部更新", fund_holdings_sync_service.sync_latest_all)
        return True

    def _reload_from_db(
        self,
        *,
        quarter_scope: str | None = None,
        quarter_keys=None,
    ):
        if quarter_scope is None:
            quarter_scope, quarter_keys = self._current_quarter_query_scope()
        with ui_stall_span(
            "FundHoldingsTab._reload_from_db",
            tab="fund_holdings",
            signal=str(quarter_scope or ""),
        ):
            self._latest_quarter_map = fund_holdings_store.get_latest_quarter_map()
            self._latest_sync_map = fund_holdings_store.get_latest_sync_map()
            self._concept_sector_cache.clear()
            self._ai_chain_context_map = None
            scope = str(quarter_scope or self._QUERY_SCOPE_LATEST).strip().lower()
            query_quarters = self._resolve_query_quarters(
                self._latest_quarter_map,
                quarter_scope=scope,
                quarter_keys=quarter_keys,
            )
            change_rows = self._query_change_rows_for_scope(query_quarters)
            view_rows = self._build_view_rows(change_rows)
            payload = {
                "latest_quarter_map": self._latest_quarter_map,
                "latest_sync_map": self._latest_sync_map,
                "concept_sector_cache": self._concept_sector_cache,
                "view_rows": view_rows,
                "loaded_quarter_scope": scope,
                "loaded_quarter_keys": sorted(query_quarters or []),
            }
            self._apply_view_payload(payload)

    def _refresh_filter_options(self):
        quarters = fund_holdings_store.list_quarters()

        current_subjects = self._selected_subject_names()
        current_capital_attributes = self._selected_capital_attributes()
        latest_only, selected_quarters = self._quarter_filter_state()
        all_quarters = bool(
            self._quarter_actions.get(self._QUARTER_FILTER_ALL)
            and self._quarter_actions[self._QUARTER_FILTER_ALL].isChecked()
        )

        with suppress(RuntimeError):
            subject_names = extract_subject_filter_options(self.model.row_data or [])
            valid_subjects = set(subject_names)
            self.cmb_subject.set_options(subject_names, preserve_selection=False)
            self.cmb_subject.set_selected_values(
                [subject_name for subject_name in current_subjects if subject_name in valid_subjects],
                emit=False,
            )
            self._refresh_subject_button_text()

            capital_attributes = extract_capital_attribute_filter_options(
                self.model.row_data or [],
                self._CAPITAL_ATTRIBUTE_OPTIONS,
            )
            valid_capital_attributes = set(capital_attributes)
            self.cmb_capital_attribute.set_options(
                [
                    (capital_attribute, self._capital_attribute_label(capital_attribute))
                    for capital_attribute in capital_attributes
                ],
                preserve_selection=False,
            )
            self.cmb_capital_attribute.set_selected_values(
                [
                    capital_attribute
                    for capital_attribute in current_capital_attributes
                    if capital_attribute in valid_capital_attributes
                ],
                emit=False,
            )
            self._refresh_capital_attribute_button_text()

        with suppress(RuntimeError):
            self._build_quarter_menu(quarters)
            self._set_quarter_filter_state(
                latest_only=latest_only,
                all_quarters=all_quarters,
                selected_quarters=[quarter for quarter in selected_quarters if quarter in set(quarters)],
                apply=False,
            )

    def _apply_filters(self):
        subject_names = self._selected_subject_names()
        capital_attributes = self._selected_capital_attributes()
        latest_only, selected_quarters = self._quarter_filter_state()
        change_types = self._selected_change_types()

        self.proxy_model.set_filter_state(
            subject_names=subject_names,
            capital_attributes=capital_attributes,
            change_types=change_types,
            filter_text=self.search_box.text().strip(),
            latest_only=latest_only,
            quarter_keys=selected_quarters,
        )
        self._schedule_view_state_save()

        if self.model.row_data:
            self.table_state.show_table()
        self._update_status_summary()

    @classmethod
    def _is_ai_related_concept(cls, concept_name: str) -> bool:
        return is_ai_related_concept(concept_name)

    @classmethod
    def _filter_ai_related_concepts(cls, concepts) -> list[str]:
        return filter_ai_related_concepts(concepts)

    def _build_view_rows(self, change_rows: list[dict]) -> list[dict]:
        if self._ai_chain_context_map is None:
            self._ai_chain_context_map = self._load_ai_chain_context_map()
        return build_fund_holdings_view_rows(
            change_rows,
            latest_quarter_map=self._latest_quarter_map,
            chain_context_map=self._ai_chain_context_map,
            concept_sector_cache=self._concept_sector_cache,
            capital_attribute_labels=self._CAPITAL_ATTRIBUTE_LABELS,
            placeholder=self._DISPLAY_PLACEHOLDER,
            subject_code_qfii=self._SUBJECT_CODE_QFII,
        )

    @staticmethod
    def _load_cached_finance_snapshot(codes) -> dict[str, dict]:
        return load_cached_finance_snapshot(codes)

    def _apply_latest_quotes_from_store(self):
        self._apply_quote_store_snapshot()

    def _schedule_visible_quote_snapshot_refresh(self, current_model=None) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        if current_model is not None:
            self._pending_quote_snapshot_model = current_model
        if self._quote_snapshot_refresh_pending:
            return True
        self._quote_snapshot_refresh_pending = True
        QTimer.singleShot(self._QUOTE_SNAPSHOT_REFRESH_DELAY_MS, self._run_visible_quote_snapshot_refresh)
        return True

    def _run_visible_quote_snapshot_refresh(self) -> bool:
        self._quote_snapshot_refresh_pending = False
        current_model = self._pending_quote_snapshot_model
        self._pending_quote_snapshot_model = None
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        return self._prime_visible_local_quote_snapshot(current_model)

    def _on_cache_reload_completed(self):
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._schedule_cache_reload_refresh()

    def _schedule_cache_reload_refresh(self) -> None:
        if getattr(self, "_runtime_cleanup_done", False):
            return
        if self._cache_reload_refresh_pending:
            return
        self._cache_reload_refresh_pending = True
        QTimer.singleShot(self._QUOTE_SNAPSHOT_REFRESH_DELAY_MS, self._run_cache_reload_refresh)

    def _run_cache_reload_refresh(self) -> None:
        self._cache_reload_refresh_pending = False
        if getattr(self, "_runtime_cleanup_done", False):
            return
        with ui_stall_span(
            "FundHoldingsTab._run_cache_reload_refresh",
            tab="fund_holdings",
            signal="cache_reload",
        ):
            self.refresh_table_from_latest_snapshot(current_model=self.model, async_local=True)
            self._update_status_summary()

    def _on_fund_holdings_updated(self):
        if getattr(self, "_runtime_cleanup_done", False):
            return
        if not self._initial_load_started:
            return
        if self._sync_active:
            return
        self._reload_from_db_async()

    def _update_status_summary(self):
        rows = list(getattr(self.model, "row_data", []) or [])
        total = len(rows)
        visible = self.proxy_model.rowCount()
        qfii_quarter = self._latest_quarter_map.get(self._SUBJECT_CODE_QFII, self._DISPLAY_PLACEHOLDER)
        ruiyuan_quarter = self._latest_quarter_map.get(self._SUBJECT_CODE_RUIYUAN, self._DISPLAY_PLACEHOLDER)
        qfii_sync = str((self._latest_sync_map.get(self._SUBJECT_CODE_QFII) or {}).get("finished_at") or "")
        ruiyuan_sync = str((self._latest_sync_map.get(self._SUBJECT_CODE_RUIYUAN) or {}).get("finished_at") or "")

        if total == 0 and not self._sync_active:
            self.lbl_status.setText(
                self.format_workspace_status(
                    "等待基金持仓同步",
                    result="0只",
                    freshness="待同步",
                    current_filter=self._current_filter_summary(),
                    next_step="点击刷新同步数据库",
                    extra_segments=("QFII/睿远数据尚未入库",),
                )
            )
            return

        segments = [self._status_metric("QFII ", qfii_quarter), self._status_metric("睿远 ", ruiyuan_quarter)]
        if qfii_sync:
            segments.append(self._status_metric("QFII更新 ", qfii_sync[-8:]))
        if ruiyuan_sync:
            segments.append(self._status_metric("睿远更新 ", ruiyuan_sync[-8:]))

        self.lbl_status.setText(
            self.format_workspace_status(
                "基金持仓已就绪",
                result=f"{visible}/{total}只",
                freshness=self._latest_sync_freshness_text(),
                current_filter=self._current_filter_summary(),
                next_step="",
                extra_segments=segments,
            )
        )

    def _row_dict_from_index(self, proxy_index):
        if not proxy_index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(proxy_index)
        return self.model.get_row_data(source_index.row())

    def _on_double_click(self, proxy_index):
        row = self._row_dict_from_index(proxy_index)
        if not row:
            return
        code = str(row.get("代码") or "").strip()
        if len(code) == 6 and code.isdigit():
            code_list = []
            current_idx = 0
            clicked_visual_row = proxy_index.row()
            for visual_row in range(self.proxy_model.rowCount()):
                visual_index = self.proxy_model.index(visual_row, 0)
                visual_row_dict = self._row_dict_from_index(visual_index)
                if not isinstance(visual_row_dict, dict):
                    continue
                row_dict = dict(visual_row_dict)
                row_code = str(row_dict.get("代码") or "").strip()
                if not row_code:
                    continue
                row_dict.setdefault("代码", row_code)
                row_dict.setdefault("名称", str(row_dict.get("名称") or "").strip())
                code_list.append(row_dict)
                if visual_row == clicked_visual_row:
                    current_idx = len(code_list) - 1

            if code_list:
                ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)
            else:
                ui_signals.sig_show_kline.emit(code)

    def _show_context_menu(self, pos):
        proxy_index = self.table.indexAt(pos)
        row = self._row_dict_from_index(proxy_index)
        if not row:
            return
        code = str(row.get("代码") or "").strip()
        if len(code) == 6 and code.isdigit():
            from ui.components.stock_context_menu import build_stock_context_menu

            build_stock_context_menu(
                self,
                code,
                str(row.get("名称") or "").strip(),
                show_watchlist_toggle=True,
                vcp_data={"代码": code, "名称": str(row.get("名称") or "").strip()},
            )

    def _cleanup_runtime_state(self):
        if not getattr(self, "_fund_holdings_cleanup_done", False):
            self._fund_holdings_cleanup_done = True
            lifecycle = getattr(self, "_task_lifecycle", None)
            if lifecycle is not None:
                lifecycle.shutdown(timeout_ms=1000)
            view_state_timer = getattr(self, "_view_state_save_timer", None)
            if view_state_timer is not None:
                view_state_timer.stop()
            self._save_view_state()
            with suppress(TypeError, RuntimeError):
                event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
            with suppress(TypeError, RuntimeError):
                event_bus.sig_app_closing.disconnect(self._save_view_state)
            with suppress(TypeError, RuntimeError):
                event_bus.sig_fund_holdings_updated.disconnect(self._on_fund_holdings_updated)
        super()._cleanup_runtime_state()

    def shutdown(self) -> None:
        self._cleanup_runtime_state()
