# -*- coding: utf-8 -*-
"""基金持仓 Tab。"""

from __future__ import annotations

import os
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

from app.services import get_sector_manager
from app.services.ui_runtime_service import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    MarketCalendar,
    app_config,
    fund_holdings_store,
    fund_holdings_sync_service,
    task_registry,
    ui_signals,
)
from app.services.ui_runtime_service import (
    background_job_runner as task_manager,
)
from app.services.ui_runtime_service import (
    domain_events as event_bus,
)
from ui.components import (
    MultiSelectFilterButton,
    SearchFilter,
    TableStateWrapper,
    VCPTableView,
    format_multi_select_summary,
)
from ui.components.stock_context_menu import build_stock_context_menu
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_refresh import load_cached_finance_snapshot
from ui.tabs.base_stock_tab import BaseStockTab


class FundHoldingsFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._subject_names: set[str] = set()
        self._capital_attributes: set[str] = set()
        self._quarter_keys: set[str] = set()
        self._change_types: set[str] = set()
        self._latest_only = True

    def set_subject_name(self, subject_name: str):
        self.set_subject_names([subject_name] if subject_name else [])

    def set_subject_names(self, subject_names):
        self._subject_names = {
            str(subject_name or "").strip()
            for subject_name in (subject_names or [])
            if str(subject_name or "").strip()
        }
        self.invalidateFilter()

    def set_capital_attributes(self, capital_attributes):
        self._capital_attributes = {
            str(capital_attribute or "").strip()
            for capital_attribute in (capital_attributes or [])
            if str(capital_attribute or "").strip()
        }
        self.invalidateFilter()

    def set_quarter_keys(self, quarter_keys):
        self._quarter_keys = {
            str(quarter_key or "").strip()
            for quarter_key in (quarter_keys or [])
            if str(quarter_key or "").strip()
        }
        self.invalidateFilter()

    def set_change_types(self, change_types):
        self._change_types = {
            str(change_type or "").strip()
            for change_type in (change_types or [])
            if str(change_type or "").strip()
        }
        self.invalidateFilter()

    def set_latest_only(self, latest_only: bool):
        self._latest_only = bool(latest_only)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        row_data = model.row_data[source_row]

        if self._subject_names and str(row_data.get("主体", "")).strip() not in self._subject_names:
            return False

        if self._capital_attributes and str(row_data.get("_capital_attribute_value", "")).strip() not in self._capital_attributes:
            return False

        if self._change_types and str(row_data.get("变化类型", "")).strip() not in self._change_types:
            return False

        if self._quarter_keys and str(row_data.get("季度", "")).strip() not in self._quarter_keys:
            return False

        if self._latest_only and not bool(row_data.get("_is_latest_subject_quarter")):
            return False

        filter_text = getattr(self, "_filter_text", "")
        if not filter_text:
            return True

        code_text = str(row_data.get("代码", "") or "").lower()
        name_text = str(row_data.get("名称", "") or "").lower()
        subject_text = str(row_data.get("主体", "") or "").lower()
        if SearchFilter.match_pinyin_or_text(filter_text, code_text, name_text):
            return True
        if filter_text in subject_text:
            return True

        for value in row_data.values():
            if filter_text in str(value or "").lower():
                return True
        return False


class FundHoldingsTab(BaseStockTab):
    _SUBJECT_CODE_QFII = SUBJECT_QFII["subject_code"]
    _SUBJECT_CODE_RUIYUAN = SUBJECT_RUIYUAN["subject_code"]
    _QUARTER_FILTER_LATEST = "__LATEST__"
    _QUARTER_FILTER_ALL = "__ALL__"
    _CHANGE_FILTER_ALL = "__ALL__"
    _DISPLAY_PLACEHOLDER = "--"
    _CHANGE_TYPE_OPTIONS = ("新进", "增持", "减持", "退出", "持平")
    _DAILY_AUTO_SYNC_HOUR = 20
    _DAILY_AUTO_SYNC_MINUTE = 30
    _DAILY_AUTO_SYNC_DATE_KEY = "daily_auto_sync_2030_last_date"
    _CAPITAL_ATTRIBUTE_OPTIONS = (
        QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
        QFII_CAPITAL_ATTRIBUTE_CLIENT,
        QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    )
    _CAPITAL_ATTRIBUTE_LABELS = {
        QFII_CAPITAL_ATTRIBUTE_UNMARKED: _DISPLAY_PLACEHOLDER,
    }
    _AI_CONCEPT_EXCLUDE_NAMES = {
        "AI营销",
    }
    _AI_CONCEPT_INCLUDE_NAMES = {
        "AIGC概念",
        "AI医疗",
        "AI手机PC",
        "AI智能体",
        "AI眼镜",
        "ChatGPT",
        "DeepSeek",
        "EDA概念",
        "东数西算",
        "云计算",
        "人工智能",
        "人形机器",
        "先进封装",
        "光刻机",
        "光通信",
        "华为海思",
        "华为算力",
        "国资云",
        "多模态AI",
        "存储芯片",
        "数据中心",
        "智谱AI",
        "智能机器",
        "机器视觉",
        "液冷服务",
        "算力租赁",
        "英伟达",
        "边缘计算",
    }
    _AI_CONCEPT_DISPLAY_ALIASES = {
        "\u6db2\u51b7\u670d\u52a1": "\u6db2\u51b7",
        "CPO\u6982\u5ff5": "CPO",
    }
    _AI_CONCEPT_INCLUDE_KEYWORDS = (
        "AI",
        "AIGC",
        "GPT",
        "DeepSeek",
        "智谱",
        "人工智能",
        "算力",
        "CPO",
        "光通信",
        "铜缆",
        "液冷",
        "芯片",
        "半导",
        "存储",
        "封装",
        "EDA",
        "PCB",
        "光刻机",
    )
    _VIEW_STATE_PREFIX = "fund_holdings_view_state_v2"

    def __init__(self, data_provider, parent=None, autoload: bool = True):
        super().__init__(data_provider=data_provider, parent=parent)
        self._autoload = bool(autoload)
        self._initial_load_started = False
        self._initial_load_task_id = self._build_workspace_task_id(
            f"initial_load_{id(self)}"
        )
        self._latest_quarter_map: dict[str, str] = {}
        self._latest_sync_map: dict[str, dict] = {}
        self._sync_task_id = ""
        self._sync_active = False
        self._filter_menu_updating = False
        self._quarter_actions: dict[str, QAction] = {}
        self._change_actions: dict[str, QAction] = {}
        self._sector_manager = None
        self._sector_manager_initialized = False
        self._concept_sector_cache: dict[str, str] = {}
        self._settings = self._create_settings()
        self._pending_daily_auto_sync_date = ""
        self._restoring_view_state = False
        self._view_state_restored = False
        self._view_state_save_timer = QTimer(self)
        self._view_state_save_timer.setSingleShot(True)
        self._view_state_save_timer.setInterval(300)
        self._view_state_save_timer.timeout.connect(self._save_view_state)
        self._daily_auto_sync_timer = QTimer(self)
        self._daily_auto_sync_timer.timeout.connect(self._check_daily_auto_sync)
        self._daily_auto_sync_initial_check_timer = QTimer(self)
        self._daily_auto_sync_initial_check_timer.setSingleShot(True)
        self._daily_auto_sync_initial_check_timer.timeout.connect(self._check_daily_auto_sync)

        self._init_ui()
        if self._autoload:
            self._reload_from_db()
            self._initial_load_started = True
        else:
            self._set_initial_loading_state("基金持仓待加载", "首次进入时自动读取本地数据库")

        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)
        event_bus.sig_app_closing.connect(self._save_view_state)
        self._start_daily_auto_sync_timer()

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_initial_load_started()

    def prime_background_load(self):
        self._ensure_initial_load_started()

    @staticmethod
    def _create_settings():
        return app_config.section(
            "tabs/FundHoldingsTab",
            legacy_scope="FundHoldingsTab",
        )

    def _view_state_key(self, name: str) -> str:
        return f"{self._VIEW_STATE_PREFIX}/{name}"

    @staticmethod
    def _normalize_auto_sync_date(value) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "")
        return text[:8] if len(text) >= 8 else text

    @classmethod
    def _should_trigger_daily_auto_sync(
        cls,
        now,
        *,
        last_auto_sync_date: str,
        pending_auto_sync_date: str,
    ) -> bool:
        today_compact = now.strftime("%Y%m%d")
        if pending_auto_sync_date == today_compact:
            return False
        if cls._normalize_auto_sync_date(last_auto_sync_date) == today_compact:
            return False
        if (now.hour, now.minute) < (cls._DAILY_AUTO_SYNC_HOUR, cls._DAILY_AUTO_SYNC_MINUTE):
            return False
        return True

    def _start_daily_auto_sync_timer(self) -> None:
        self._daily_auto_sync_timer.start(5 * 60 * 1000)
        self._daily_auto_sync_initial_check_timer.start(10_000)

    def _stop_daily_auto_sync_timer(self) -> None:
        timer = getattr(self, "_daily_auto_sync_timer", None)
        if timer is not None:
            timer.stop()
        initial_timer = getattr(self, "_daily_auto_sync_initial_check_timer", None)
        if initial_timer is not None:
            initial_timer.stop()
        view_state_timer = getattr(self, "_view_state_save_timer", None)
        if view_state_timer is not None:
            view_state_timer.stop()

    def _check_daily_auto_sync(self) -> bool:
        if self._sync_active:
            return False

        now = MarketCalendar.now("CN")
        today_compact = now.strftime("%Y%m%d")
        if not self._should_trigger_daily_auto_sync(
            now,
            last_auto_sync_date=self._settings.value(self._DAILY_AUTO_SYNC_DATE_KEY, ""),
            pending_auto_sync_date=self._pending_daily_auto_sync_date,
        ):
            return False

        self._pending_daily_auto_sync_date = today_compact
        event_bus.sig_system_log.emit("info", f"[基金持仓] 触发每日20:30自动刷新: {today_compact}")
        started = self.run_daily_auto_sync(today_compact)
        if not started:
            self._pending_daily_auto_sync_date = ""
        return started

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
        self._build_change_menu()
        self._refresh_subject_button_text()
        self._refresh_capital_attribute_button_text()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、主体、资金属性、概念或变化...")
        self.search_box.setAccessibleName("基金持仓筛选")
        self.search_box.setAccessibleDescription("按代码、名称、主体、资金属性、概念或变化筛选基金持仓")
        self.search_box.setMinimumWidth(200)
        self.search_box.setMaximumWidth(320)
        self.search_box.textChanged.connect(self._apply_filters)

        filter_widgets = [self.cmb_subject, self.cmb_capital_attribute, self.btn_quarter, self.btn_change, self.search_box]

        self.btn_update = QToolButton()
        self.btn_update.setText("全部更新")
        self.btn_update.setAccessibleName("更新基金持仓数据库")
        self.btn_update.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_update.clicked.connect(self.run_full_sync)

        action_widgets = [self.btn_update]
        toolbar = self.build_tab_toolbar("基金持仓", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self.columns = [
            "代码", "名称", "市价", "涨幅%", "市值",
            "主体", "资金属性", "季度", "变化类型",
            "本期占比",
            "本期持股", "上期持股", "持股变化",
            "概念板块",
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.model.set_plain_style_headers(["主体", "资金属性", "季度", "变化类型", "概念板块"])

        self.proxy_model = FundHoldingsFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无基金持仓数据", loading_title="同步基金持仓数据中...")

        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        default_widths = [70, 90, 70, 70, 75, 180, 96, 90, 80, 90, 110, 110, 110, 240]
        for index, width in enumerate(default_widths):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(index, width)
        self.bind_header_persistence(self.table, "fund_holdings_header_state_v3")
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)

        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    def _set_initial_loading_state(self, title: str, subtitle: str = ""):
        self.lbl_status.setText(title)
        self.table_state.show_loading(title, subtitle)

    def _ensure_initial_load_started(self):
        if self._autoload or self._initial_load_started:
            return
        self._initial_load_started = True
        self._set_initial_loading_state("正在加载基金持仓数据...", "首次进入时后台构建持仓视图")
        self._reload_from_db_async()

    @staticmethod
    def _resolve_tdx_root(data_provider) -> str | None:
        tdx_vipdoc = str(getattr(data_provider, "tdx_vipdoc", "") or "").strip()
        return os.path.dirname(tdx_vipdoc) if tdx_vipdoc else None

    @staticmethod
    def _build_sector_manager(tdx_root: str | None):
        try:
            return get_sector_manager(tdx_root)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return None

    @classmethod
    def _build_concept_sector_text_with_manager(
        cls,
        stock_code: str,
        manager,
        concept_sector_cache: dict[str, str],
    ) -> str:
        code = str(stock_code or "").strip()
        if not code:
            return cls._DISPLAY_PLACEHOLDER

        cached = concept_sector_cache.get(code)
        if cached is not None:
            return cached

        concept_text = cls._DISPLAY_PLACEHOLDER
        if manager is not None:
            try:
                concepts = []
                for sector_name in manager.get_sectors(code) or []:
                    sector_text = str(sector_name or "").strip()
                    if not sector_text.startswith("GN_"):
                        continue
                    concept_name = sector_text.replace("GN_", "", 1).strip()
                    if concept_name:
                        concepts.append(concept_name)
                filtered_concepts = cls._filter_ai_related_concepts(concepts)
                if filtered_concepts:
                    concept_text = " | ".join(filtered_concepts)
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                concept_text = cls._DISPLAY_PLACEHOLDER

        concept_sector_cache[code] = concept_text
        return concept_text

    @classmethod
    def _load_view_payload(cls, data_provider) -> dict:
        latest_quarter_map = fund_holdings_store.get_latest_quarter_map()
        latest_sync_map = fund_holdings_store.get_latest_sync_map()
        change_rows = fund_holdings_store.query_change_rows()
        concept_sector_cache: dict[str, str] = {}
        sector_manager = cls._build_sector_manager(cls._resolve_tdx_root(data_provider))
        view_rows = []
        for row in change_rows or []:
            stock_code = str(row.get("stock_code") or "").strip()
            subject_code = str(row.get("subject_code") or "").strip()
            quarter_key = str(row.get("quarter_key") or "").strip()
            change_type = str(row.get("change_type") or "").strip()
            capital_attribute = str(row.get("capital_attribute") or "").strip()
            if subject_code == cls._SUBJECT_CODE_QFII and not capital_attribute:
                capital_attribute = QFII_CAPITAL_ATTRIBUTE_UNMARKED
            capital_attribute_text = cls._capital_attribute_label(capital_attribute)
            has_curr = change_type != "退出"
            has_prev = change_type != "新进"

            view_rows.append(
                {
                    "代码": stock_code,
                    "名称": str(row.get("stock_name") or "").strip(),
                    "市价": cls._DISPLAY_PLACEHOLDER,
                    "涨幅%": cls._DISPLAY_PLACEHOLDER,
                    "市值": cls._DISPLAY_PLACEHOLDER,
                    "主体": str(row.get("subject_name") or "").strip(),
                    "资金属性": capital_attribute_text,
                    "主体代码": subject_code,
                    "季度": quarter_key,
                    "变化类型": change_type,
                    "本期占比": cls._format_pct(row.get("curr_ratio_pct"), show=has_curr),
                    "本期持股": cls._format_amount(row.get("curr_hold_num_shares"), divisor=10000.0, show=has_curr),
                    "上期持股": cls._format_amount(row.get("prev_hold_num_shares"), divisor=10000.0, show=has_prev),
                    "持股变化": cls._format_amount(
                        row.get("delta_hold_num_shares"),
                        divisor=10000.0,
                        show=has_curr or has_prev,
                        signed=True,
                    ),
                    "概念板块": cls._build_concept_sector_text_with_manager(
                        stock_code,
                        sector_manager,
                        concept_sector_cache,
                    ),
                    "_capital_attribute_value": capital_attribute,
                    "_is_latest_subject_quarter": quarter_key == latest_quarter_map.get(subject_code),
                }
            )
        return {
            "latest_quarter_map": latest_quarter_map,
            "latest_sync_map": latest_sync_map,
            "concept_sector_cache": concept_sector_cache,
            "view_rows": view_rows,
        }

    def _apply_view_payload(self, payload: dict):
        self._latest_quarter_map = dict(payload.get("latest_quarter_map") or {})
        self._latest_sync_map = dict(payload.get("latest_sync_map") or {})
        self._concept_sector_cache = dict(payload.get("concept_sector_cache") or {})
        view_rows = list(payload.get("view_rows") or [])
        self.model.update_data(view_rows)
        self._refresh_filter_options()
        self._restore_view_state()
        self._apply_filters()
        self._apply_latest_quotes_from_store()
        self._prime_visible_local_quote_snapshot(self.model)
        self._update_status_summary()

        if not view_rows and not self._sync_active:
            self.table_state.show_empty("暂无基金持仓数据", "请使用右上角“刷新”同步 QFII 或睿远持仓")

    def _reload_from_db_async(self):
        def _load_bg():
            return self._load_view_payload(self.data_provider)

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

        task_manager.run_in_background(
            _load_bg,
            on_success=_on_success,
            on_error=_on_error,
            task_id=self._initial_load_task_id,
        )

    def _build_change_menu(self):
        self.menu_change.clear()
        self._change_actions.clear()

        act_all = QAction("全部变化", self)
        act_all.setCheckable(True)
        act_all.toggled.connect(self._on_change_selection_toggled)
        self._change_actions[self._CHANGE_FILTER_ALL] = act_all
        self.menu_change.addAction(act_all)
        self.menu_change.addSeparator()

        for label in self._CHANGE_TYPE_OPTIONS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.toggled.connect(self._on_change_selection_toggled)
            self._change_actions[label] = action
            self.menu_change.addAction(action)

        self._set_change_filter_values(set(), apply=False)

    def _build_quarter_menu(self, quarters: list[str]):
        self.menu_quarter.clear()
        self._quarter_actions.clear()

        for key, label in (
            (self._QUARTER_FILTER_LATEST, "最新季度"),
            (self._QUARTER_FILTER_ALL, "全部季度"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.toggled.connect(self._on_quarter_selection_toggled)
            self._quarter_actions[key] = action
            self.menu_quarter.addAction(action)

        if quarters:
            self.menu_quarter.addSeparator()

        for quarter in quarters:
            action = QAction(quarter, self)
            action.setCheckable(True)
            action.toggled.connect(self._on_quarter_selection_toggled)
            self._quarter_actions[quarter] = action
            self.menu_quarter.addAction(action)

    def _selected_change_types(self) -> set[str]:
        if self._change_actions.get(self._CHANGE_FILTER_ALL, None) and self._change_actions[self._CHANGE_FILTER_ALL].isChecked():
            return set()
        return {
            label
            for label in self._CHANGE_TYPE_OPTIONS
            if self._change_actions.get(label) and self._change_actions[label].isChecked()
        }

    def _quarter_filter_state(self) -> tuple[bool, set[str]]:
        if self._quarter_actions.get(self._QUARTER_FILTER_LATEST) and self._quarter_actions[self._QUARTER_FILTER_LATEST].isChecked():
            return True, set()
        if self._quarter_actions.get(self._QUARTER_FILTER_ALL) and self._quarter_actions[self._QUARTER_FILTER_ALL].isChecked():
            return False, set()
        selected = {
            key
            for key, action in self._quarter_actions.items()
            if key not in {self._QUARTER_FILTER_LATEST, self._QUARTER_FILTER_ALL} and action.isChecked()
        }
        if not selected:
            return True, set()
        return False, selected

    def _set_change_filter_values(self, values: set[str] | list[str], *, apply: bool = True):
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
            self._apply_filters()

    def _on_change_selection_toggled(self, _checked: bool):
        if self._filter_menu_updating:
            return

        if self.sender() is self._change_actions.get(self._CHANGE_FILTER_ALL):
            self._set_change_filter_values(set(), apply=True)
            return

        selected = {
            label
            for label in self._CHANGE_TYPE_OPTIONS
            if self._change_actions[label].isChecked()
        }
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
        selected = sorted(self._selected_change_types(), key=self._CHANGE_TYPE_OPTIONS.index)
        if not selected:
            text = "变化：全部"
            tooltip = "全部变化"
        elif len(selected) <= 2:
            text = f"变化：{' / '.join(selected)}"
            tooltip = "、".join(selected)
        else:
            text = f"变化：{len(selected)}项"
            tooltip = "、".join(selected)
        self.btn_change.setText(text)
        self.btn_change.setToolTip(tooltip)

    def _refresh_quarter_button_text(self):
        latest_only, selected = self._quarter_filter_state()
        if latest_only:
            text = "季度：最新"
            tooltip = "仅显示各主体最新季度"
        elif not selected:
            text = "季度：全部"
            tooltip = "显示全部季度"
        elif len(selected) <= 2:
            ordered = sorted(selected, reverse=True)
            text = f"季度：{' / '.join(ordered)}"
            tooltip = "、".join(ordered)
        else:
            text = f"季度：{len(selected)}项"
            tooltip = "、".join(sorted(selected, reverse=True))
        self.btn_quarter.setText(text)
        self.btn_quarter.setToolTip(tooltip)

    @staticmethod
    def _normalize_settings_values(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    def _schedule_view_state_save(self):
        if self._restoring_view_state:
            return
        self._view_state_save_timer.start()

    def _refresh_subject_button_text(self):
        text, tooltip = format_multi_select_summary(
            "主体",
            self.cmb_subject.selected_labels(),
            all_text="全部",
        )
        self.cmb_subject.setText(text)
        self.cmb_subject.setToolTip(tooltip)

    def _refresh_capital_attribute_button_text(self):
        text, tooltip = format_multi_select_summary(
            "资金属性",
            self.cmb_capital_attribute.selected_labels(),
            all_text="全部",
        )
        self.cmb_capital_attribute.setText(text)
        self.cmb_capital_attribute.setToolTip(tooltip)

    def _on_subject_selection_changed(self):
        self._refresh_subject_button_text()
        self._apply_filters()

    def _on_capital_attribute_selection_changed(self):
        self._refresh_capital_attribute_button_text()
        self._apply_filters()

    @classmethod
    def _capital_attribute_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return cls._DISPLAY_PLACEHOLDER
        return cls._CAPITAL_ATTRIBUTE_LABELS.get(text, text)

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

    def _current_filter_summary(self) -> str:
        parts = []
        subject_text = " / ".join(sorted(self._selected_subject_names()))
        if subject_text:
            parts.append(subject_text)

        capital_text = " / ".join(
            self._capital_attribute_label(item)
            for item in sorted(self._selected_capital_attributes())
        )
        if capital_text:
            parts.append(capital_text)

        latest_only, selected_quarters = self._quarter_filter_state()
        if latest_only:
            parts.append("最新季度")
        elif selected_quarters:
            parts.append(" / ".join(sorted(selected_quarters, reverse=True)))

        change_text = " / ".join(sorted(self._selected_change_types()))
        if change_text:
            parts.append(change_text)

        search_text = self.search_box.text().strip()
        if search_text:
            parts.append(search_text)

        return "｜".join(parts) if parts else "全部"

    @staticmethod
    def _sort_order_to_int(order) -> int:
        value = getattr(order, "value", order)
        try:
            return int(value)
        except (TypeError, ValueError):
            return Qt.SortOrder.AscendingOrder.value

    def _save_view_state(self):
        if self._restoring_view_state:
            return
        try:
            latest_only, selected_quarters = self._quarter_filter_state()
            quarter_mode = "latest" if latest_only else ("all" if not selected_quarters else "selected")
            change_types = list(self._selected_change_types())
            subject_names = sorted(self._selected_subject_names())
            capital_attributes = sorted(self._selected_capital_attributes())
            search_text = self.search_box.text().strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

        try:
            sort_column = int(self.table.sorted_column()) if hasattr(self.table, "sorted_column") else -1
        except (AttributeError, RuntimeError, TypeError, ValueError):
            sort_column = -1
        try:
            sort_order = self._sort_order_to_int(self.table.horizontalHeader().sortIndicatorOrder())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            sort_order = Qt.SortOrder.AscendingOrder.value

        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            self._settings.setValue(self._view_state_key("subject_names"), subject_names)
            self._settings.setValue(self._view_state_key("subject_name"), subject_names[0] if len(subject_names) == 1 else "")
            self._settings.setValue(self._view_state_key("capital_attributes"), capital_attributes)
            self._settings.setValue(self._view_state_key("search_text"), search_text)
            self._settings.setValue(self._view_state_key("quarter_mode"), quarter_mode)
            self._settings.setValue(self._view_state_key("quarter_values"), sorted(selected_quarters, reverse=True))
            self._settings.setValue(self._view_state_key("change_types"), change_types)
            self._settings.setValue(self._view_state_key("sort_column"), sort_column)
            self._settings.setValue(self._view_state_key("sort_order"), sort_order)
            self._settings.sync()

    def _restore_view_state(self):
        if self._view_state_restored:
            return

        self._restoring_view_state = True
        try:
            subject_names = set(self._normalize_settings_values(self._settings.value(self._view_state_key("subject_names"), [])))
            if not subject_names:
                legacy_subject_name = str(self._settings.value(self._view_state_key("subject_name"), "") or "").strip()
                if legacy_subject_name:
                    subject_names = {legacy_subject_name}
            capital_attributes = set(
                self._normalize_settings_values(self._settings.value(self._view_state_key("capital_attributes"), []))
            )
            search_text = str(self._settings.value(self._view_state_key("search_text"), "") or "")
            quarter_mode = str(self._settings.value(self._view_state_key("quarter_mode"), "latest") or "latest").strip().lower()
            quarter_values = set(self._normalize_settings_values(self._settings.value(self._view_state_key("quarter_values"), [])))
            change_types = set(self._normalize_settings_values(self._settings.value(self._view_state_key("change_types"), [])))

            try:
                sort_column = int(self._settings.value(self._view_state_key("sort_column"), -1) or -1)
            except (TypeError, ValueError):
                sort_column = -1
            try:
                sort_order_value = self._settings.value(
                    self._view_state_key("sort_order"),
                    Qt.SortOrder.AscendingOrder.value,
                )
                sort_order = Qt.SortOrder(self._sort_order_to_int(sort_order_value))
            except (TypeError, ValueError):
                sort_order = Qt.SortOrder.AscendingOrder

            self.cmb_subject.set_selected_values(subject_names, emit=False)
            self._refresh_subject_button_text()
            self.cmb_capital_attribute.set_selected_values(capital_attributes, emit=False)
            self._refresh_capital_attribute_button_text()

            search_was_blocked = self.search_box.blockSignals(True)
            try:
                self.search_box.setText(search_text)
            finally:
                self.search_box.blockSignals(search_was_blocked)

            self._set_change_filter_values(change_types, apply=False)
            self._set_quarter_filter_state(
                latest_only=quarter_mode == "latest",
                all_quarters=quarter_mode == "all",
                selected_quarters=quarter_values,
                apply=False,
            )

            if 0 <= sort_column < self.model.columnCount():
                self.table.sortByColumn(sort_column, sort_order)
        finally:
            self._restoring_view_state = False
            self._view_state_restored = True

    def _on_sort_indicator_changed(self, _section: int, _order: Qt.SortOrder):
        self._schedule_view_state_save()

    def _set_sync_active(self, active: bool, title: str = "", subtitle: str = ""):
        self._sync_active = bool(active)
        self.btn_update.setEnabled(not self._sync_active)
        if self._sync_active:
            self.table_state.show_loading(title or "同步基金持仓中...", subtitle)

    def _run_sync_action(self, label: str, sync_callable):
        if self._sync_active:
            return

        callable_name = getattr(sync_callable, "__name__", "task")
        self._sync_task_id = self._build_workspace_task_id(
            f"sync_{callable_name}"
        )
        self._set_sync_active(True, "同步基金持仓中...", label)
        total = len(getattr(self.model, "row_data", []) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
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

        def _on_success(result):
            if getattr(self, "_runtime_cleanup_done", False):
                return
            self._set_sync_active(False)
            self._reload_from_db()
            message = str((result or {}).get("message") or label).strip()
            total = len(getattr(self.model, "row_data", []) or [])
            visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
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

        def _on_error(error_message: str):
            if getattr(self, "_runtime_cleanup_done", False):
                return
            self._set_sync_active(False)
            self._reload_from_db()
            message = str(error_message or "更新失败").strip()
            total = len(getattr(self.model, "row_data", []) or [])
            visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
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

        task_manager.run_in_background(
            sync_callable,
            on_success=_on_success,
            on_error=_on_error,
            task_id=self._sync_task_id,
        )

    def run_auto_sync_after_f5(self) -> bool:
        if self._sync_active:
            return False
        self._run_sync_action("F5后自动更新", fund_holdings_sync_service.sync_latest_all)
        return True

    def refresh_data_after_f5(self) -> bool:
        self.refresh_table_from_latest_snapshot(current_model=self.model, async_local=True)
        return self.run_auto_sync_after_f5()

    def run_daily_auto_sync(self, auto_sync_date: str | None = None) -> bool:
        if self._sync_active:
            return False
        today_compact = self._normalize_auto_sync_date(auto_sync_date) or MarketCalendar.now("CN").strftime("%Y%m%d")
        self._settings.setValue(self._DAILY_AUTO_SYNC_DATE_KEY, today_compact)
        self._settings.sync()
        self._run_sync_action("20:30自动更新", fund_holdings_sync_service.sync_latest_all)
        return True

    def run_full_sync(self) -> bool:
        if self._sync_active:
            return False
        self._run_sync_action("全部更新", fund_holdings_sync_service.sync_latest_all)
        return True

    def _reload_from_db(self):
        self._latest_quarter_map = fund_holdings_store.get_latest_quarter_map()
        self._latest_sync_map = fund_holdings_store.get_latest_sync_map()
        self._concept_sector_cache.clear()
        change_rows = fund_holdings_store.query_change_rows()
        view_rows = self._build_view_rows(change_rows)
        self.model.update_data(view_rows)
        self._refresh_filter_options()
        self._restore_view_state()
        self._apply_filters()
        self._apply_latest_quotes_from_store()
        self._prime_visible_local_quote_snapshot(self.model)
        self._update_status_summary()

        if not view_rows and not self._sync_active:
            self.table_state.show_empty("暂无基金持仓数据", "请使用右上角“刷新”同步 QFII 或睿远持仓")

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
            subject_names = list(
                dict.fromkeys(
                    str(row.get("主体") or "").strip()
                    for row in (self.model.row_data or [])
                    if str(row.get("主体") or "").strip()
                )
            )
            valid_subjects = set(subject_names)
            self.cmb_subject.set_options(subject_names, preserve_selection=False)
            self.cmb_subject.set_selected_values(
                [subject_name for subject_name in current_subjects if subject_name in valid_subjects],
                emit=False,
            )
            self._refresh_subject_button_text()

            capital_attributes = [
                capital_attribute
                for capital_attribute in self._CAPITAL_ATTRIBUTE_OPTIONS
                if any(
                    str(row.get("_capital_attribute_value") or "").strip() == capital_attribute
                    for row in (self.model.row_data or [])
                )
            ]
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

        self.proxy_model.set_subject_names(subject_names)
        self.proxy_model.set_capital_attributes(capital_attributes)
        self.proxy_model.set_change_types(change_types)
        self.proxy_model.setFilterText(self.search_box.text().strip())
        self.proxy_model.set_latest_only(latest_only)
        self.proxy_model.set_quarter_keys(selected_quarters)
        self._schedule_view_state_save()

        if self.model.row_data:
            self.table_state.show_table()
        self._update_status_summary()

    def _get_sector_manager(self):
        if self._sector_manager_initialized:
            return self._sector_manager

        self._sector_manager_initialized = True
        tdx_vipdoc = str(getattr(self.data_provider, "tdx_vipdoc", "") or "").strip()
        tdx_root = os.path.dirname(tdx_vipdoc) if tdx_vipdoc else None
        try:
            self._sector_manager = get_sector_manager(tdx_root)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            self._sector_manager = None
        return self._sector_manager

    def _get_concept_sector_text(self, stock_code: str) -> str:
        code = str(stock_code or "").strip()
        if not code:
            return self._DISPLAY_PLACEHOLDER

        cached = self._concept_sector_cache.get(code)
        if cached is not None:
            return cached

        concept_text = self._DISPLAY_PLACEHOLDER
        manager = self._get_sector_manager()
        if manager is not None:
            try:
                concepts = []
                for sector_name in manager.get_sectors(code) or []:
                    sector_text = str(sector_name or "").strip()
                    if not sector_text.startswith("GN_"):
                        continue
                    concept_name = sector_text.replace("GN_", "", 1).strip()
                    if concept_name:
                        concepts.append(concept_name)
                filtered_concepts = self._filter_ai_related_concepts(concepts)
                if filtered_concepts:
                    concept_text = " | ".join(filtered_concepts)
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                concept_text = self._DISPLAY_PLACEHOLDER

        self._concept_sector_cache[code] = concept_text
        return concept_text

    @classmethod
    def _is_ai_related_concept(cls, concept_name: str) -> bool:
        text = str(concept_name or "").strip()
        if not text or text in cls._AI_CONCEPT_EXCLUDE_NAMES:
            return False
        if text in cls._AI_CONCEPT_INCLUDE_NAMES:
            return True
        return any(keyword in text for keyword in cls._AI_CONCEPT_INCLUDE_KEYWORDS)

    @classmethod
    def _normalize_ai_concept_display(cls, concept_name: str) -> str:
        text = str(concept_name or "").strip()
        if not text:
            return ""
        return cls._AI_CONCEPT_DISPLAY_ALIASES.get(text, text)

    @classmethod
    def _filter_ai_related_concepts(cls, concepts) -> list[str]:
        filtered = []
        for concept_name in concepts or []:
            text = str(concept_name or "").strip()
            if not cls._is_ai_related_concept(text):
                continue
            filtered.append(cls._normalize_ai_concept_display(text))
        return list(dict.fromkeys(filtered))

    def _build_view_rows(self, change_rows: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for row in change_rows or []:
            stock_code = str(row.get("stock_code") or "").strip()
            subject_code = str(row.get("subject_code") or "").strip()
            quarter_key = str(row.get("quarter_key") or "").strip()
            change_type = str(row.get("change_type") or "").strip()
            capital_attribute = str(row.get("capital_attribute") or "").strip()
            if subject_code == self._SUBJECT_CODE_QFII and not capital_attribute:
                capital_attribute = QFII_CAPITAL_ATTRIBUTE_UNMARKED
            capital_attribute_text = self._capital_attribute_label(capital_attribute)
            has_curr = change_type != "退出"
            has_prev = change_type != "新进"

            curr_ratio = self._format_pct(row.get("curr_ratio_pct"), show=has_curr)

            rows.append(
                {
                    "代码": stock_code,
                    "名称": str(row.get("stock_name") or "").strip(),
                    "市价": self._DISPLAY_PLACEHOLDER,
                    "涨幅%": self._DISPLAY_PLACEHOLDER,
                    "市值": self._DISPLAY_PLACEHOLDER,
                    "主体": str(row.get("subject_name") or "").strip(),
                    "资金属性": capital_attribute_text,
                    "主体代码": subject_code,
                    "季度": quarter_key,
                    "变化类型": change_type,
                    "本期占比": curr_ratio,
                    "本期持股": self._format_amount(row.get("curr_hold_num_shares"), divisor=10000.0, show=has_curr),
                    "上期持股": self._format_amount(row.get("prev_hold_num_shares"), divisor=10000.0, show=has_prev),
                    "持股变化": self._format_amount(row.get("delta_hold_num_shares"), divisor=10000.0, show=has_curr or has_prev, signed=True),
                    "概念板块": self._get_concept_sector_text(stock_code),
                    "_capital_attribute_value": capital_attribute,
                    "_is_latest_subject_quarter": quarter_key == self._latest_quarter_map.get(subject_code),
                }
            )
        return rows

    @staticmethod
    def _load_cached_finance_snapshot(codes) -> dict[str, dict]:
        return load_cached_finance_snapshot(codes)

    def _prime_local_quote_snapshot(self):
        self.prime_local_quote_snapshot()

    @staticmethod
    def _format_pct(value, *, show: bool, signed: bool = False) -> str:
        if not show:
            return FundHoldingsTab._DISPLAY_PLACEHOLDER
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return FundHoldingsTab._DISPLAY_PLACEHOLDER
        prefix = "+" if signed and number > 0 else ""
        return f"{prefix}{number:.2f}%"

    @staticmethod
    def _format_amount(value, *, divisor: float, show: bool, signed: bool = False) -> str:
        if not show:
            return FundHoldingsTab._DISPLAY_PLACEHOLDER
        try:
            number = float(value or 0) / divisor
        except (TypeError, ValueError):
            return FundHoldingsTab._DISPLAY_PLACEHOLDER
        prefix = "+" if signed and number > 0 else ""
        return f"{prefix}{number:,.2f}"

    def _apply_latest_quotes_from_store(self):
        self._apply_quote_store_snapshot()

    def _on_cache_reload_completed(self):
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._apply_latest_quotes_from_store()
        self._update_status_summary()

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
            build_stock_context_menu(
                self,
                code,
                str(row.get("名称") or "").strip(),
                show_watchlist_toggle=True,
                vcp_data={"代码": code, "名称": str(row.get("名称") or "").strip()},
            )

    def closeEvent(self, event):
        self._cleanup_runtime_state()
        super().closeEvent(event)

    def _cleanup_runtime_state(self):
        if not getattr(self, "_fund_holdings_cleanup_done", False):
            self._fund_holdings_cleanup_done = True
            self._stop_daily_auto_sync_timer()
            self._save_view_state()
            with suppress(TypeError, RuntimeError):
                event_bus.sig_cache_reload_completed.disconnect(self._on_cache_reload_completed)
            with suppress(TypeError, RuntimeError):
                event_bus.sig_app_closing.disconnect(self._save_view_state)
        super()._cleanup_runtime_state()

    def shutdown(self) -> None:
        self._cleanup_runtime_state()
