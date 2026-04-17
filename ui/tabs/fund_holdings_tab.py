# -*- coding: utf-8 -*-
"""基金持仓 Tab。"""

from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
)

from core.event_bus import event_bus
from core.fund_holdings_compare import SUBJECT_QFII, SUBJECT_RUIYUAN
from core.fund_holdings_store import fund_holdings_store
from core.fund_holdings_sync import fund_holdings_sync_service
from core.task_manager import task_manager
from ui.components import (
    MultiSelectFilterButton,
    SearchFilter,
    TableStateWrapper,
    VCPTableView,
    format_multi_select_summary,
)
from ui.components.stock_context_menu import build_stock_context_menu
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab


class FundHoldingsFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._subject_names: set[str] = set()
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
    _CHANGE_TYPE_OPTIONS = ("新进", "增持", "减持", "退出", "持平")
    _VIEW_STATE_PREFIX = "fund_holdings_view_state_v1"

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._latest_quarter_map: dict[str, str] = {}
        self._latest_sync_map: dict[str, dict] = {}
        self._sync_task_id = ""
        self._sync_active = False
        self._filter_menu_updating = False
        self._quarter_actions: dict[str, QAction] = {}
        self._change_actions: dict[str, QAction] = {}
        self._settings = self._create_settings()
        self._restoring_view_state = False
        self._view_state_restored = False
        self._view_state_save_timer = QTimer(self)
        self._view_state_save_timer.setSingleShot(True)
        self._view_state_save_timer.setInterval(300)
        self._view_state_save_timer.timeout.connect(self._save_view_state)

        self._init_ui()
        self._reload_from_db()

        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)

    @staticmethod
    def _create_settings():
        return QSettings("VCPHunter", "FundHoldingsTab")

    def _view_state_key(self, name: str) -> str:
        return f"{self._VIEW_STATE_PREFIX}/{name}"

    def _selected_subject_names(self) -> set[str]:
        return self.cmb_subject.selected_values()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.lbl_status = QLabel("等待同步基金持仓数据库")

        self.cmb_subject = MultiSelectFilterButton("全部主体")
        self.cmb_subject.setFixedWidth(190)
        self.cmb_subject.selectionChanged.connect(self._on_subject_selection_changed)

        self.btn_quarter = QToolButton()
        self.btn_quarter.setFixedWidth(150)
        self.btn_quarter.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_quarter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_quarter = QMenu(self.btn_quarter)
        self.btn_quarter.setMenu(self.menu_quarter)

        self.btn_change = QToolButton()
        self.btn_change.setFixedWidth(150)
        self.btn_change.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_change.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_change = QMenu(self.btn_change)
        self.btn_change.setMenu(self.menu_change)
        self._build_change_menu()
        self._refresh_subject_button_text()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、主体或变化...")
        self.search_box.setFixedWidth(220)
        self.search_box.textChanged.connect(self._apply_filters)

        filter_widgets = [self.cmb_subject, self.btn_quarter, self.btn_change, self.search_box]

        self.btn_update = QToolButton()
        self.btn_update.setText("更新数据库")
        self.btn_update.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_update.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_update.setMenu(self._build_update_menu())

        action_widgets = [self.btn_update]
        toolbar = self.build_tab_toolbar("基金持仓", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self.columns = [
            "代码", "名称", "市价", "涨幅%", "市值",
            "主体", "季度", "对比季度", "变化类型", "占比口径",
            "本期占比", "上期占比", "占比变化",
            "本期持股(万股)", "上期持股(万股)", "持股变化(万股)",
            "持有家数",
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.model.set_plain_style_headers(["主体", "季度", "对比季度", "变化类型", "占比口径"])

        self.proxy_model = FundHoldingsFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无基金持仓数据", loading_title="同步基金持仓数据中...")

        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        default_widths = [70, 90, 70, 70, 75, 180, 90, 90, 80, 80, 90, 90, 90, 110, 110, 110, 78]
        for index, width in enumerate(default_widths):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(index, width)
        self.bind_header_persistence(self.table, "fund_holdings_header_state_v1")
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)

        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    def _build_update_menu(self) -> QMenu:
        menu = QMenu(self)

        act_qfii_current = QAction("更新 QFII 当前季度", self)
        act_qfii_current.triggered.connect(lambda: self._run_sync_action("更新 QFII 当前季度", fund_holdings_sync_service.sync_qfii))
        menu.addAction(act_qfii_current)

        act_qfii_specific = QAction("更新 QFII 指定季度", self)
        act_qfii_specific.triggered.connect(self._sync_qfii_specific)
        menu.addAction(act_qfii_specific)

        menu.addSeparator()

        act_ruiyuan_current = QAction("更新 睿远 当前季度", self)
        act_ruiyuan_current.triggered.connect(lambda: self._run_sync_action("更新 睿远 当前季度", fund_holdings_sync_service.sync_ruiyuan))
        menu.addAction(act_ruiyuan_current)

        act_ruiyuan_specific = QAction("更新 睿远 指定季度", self)
        act_ruiyuan_specific.triggered.connect(self._sync_ruiyuan_specific)
        menu.addAction(act_ruiyuan_specific)

        menu.addSeparator()

        act_all_latest = QAction("更新 全部最新可得数据", self)
        act_all_latest.triggered.connect(lambda: self._run_sync_action("更新 全部最新可得数据", fund_holdings_sync_service.sync_latest_all))
        menu.addAction(act_all_latest)

        return menu

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

    def _on_subject_selection_changed(self):
        self._refresh_subject_button_text()
        self._apply_filters()

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

    def _prompt_quarter(self, title: str) -> str | None:
        quarter_text, ok = QInputDialog.getText(self, title, "输入季度（例如 2025Q4）")
        if not ok:
            return None
        quarter_text = str(quarter_text or "").strip()
        return quarter_text or None

    def _sync_qfii_specific(self):
        quarter_key = self._prompt_quarter("更新 QFII 指定季度")
        if quarter_key:
            self._run_sync_action(
                f"更新 QFII 指定季度 {quarter_key}",
                lambda: fund_holdings_sync_service.sync_qfii(quarter_key),
            )

    def _sync_ruiyuan_specific(self):
        quarter_key = self._prompt_quarter("更新 睿远 指定季度")
        if quarter_key:
            self._run_sync_action(
                f"更新 睿远 指定季度 {quarter_key}",
                lambda: fund_holdings_sync_service.sync_ruiyuan(quarter_key),
            )

    def _set_sync_active(self, active: bool, title: str = "", subtitle: str = ""):
        self._sync_active = bool(active)
        self.btn_update.setEnabled(not self._sync_active)
        if self._sync_active:
            self.table_state.show_loading(title or "同步基金持仓中...", subtitle)

    def _run_sync_action(self, label: str, sync_callable):
        if self._sync_active:
            return

        self._sync_task_id = f"fund_holdings_sync::{label}"
        self._set_sync_active(True, "同步基金持仓中...", label)
        self.lbl_status.setText(self.format_status_summary("数据库更新中", label))

        def _on_success(result):
            self._set_sync_active(False)
            self._reload_from_db()
            message = str((result or {}).get("message") or label).strip()
            self.lbl_status.setText(self.format_status_summary("数据库已更新", message))
            event_bus.sig_system_log.emit("info", f"[基金持仓] {message}")

        def _on_error(error_message: str):
            self._set_sync_active(False)
            self._reload_from_db()
            message = str(error_message or "更新失败").strip()
            self.lbl_status.setText(self.format_status_summary("数据库更新失败", message))
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

    def _reload_from_db(self):
        self._latest_quarter_map = fund_holdings_store.get_latest_quarter_map()
        self._latest_sync_map = fund_holdings_store.get_latest_sync_map()
        change_rows = fund_holdings_store.query_change_rows()
        view_rows = self._build_view_rows(change_rows)
        self.model.update_data(view_rows)
        self._refresh_filter_options()
        self._restore_view_state()
        self._apply_filters()
        self._apply_latest_quotes_from_store()
        if view_rows:
            self.refresh_table_quotes_and_market_caps(
                current_model=self.model,
                quote_task_id="fund_holdings_quotes",
            )
        self._update_status_summary()

        if not view_rows and not self._sync_active:
            self.table_state.show_empty("暂无基金持仓数据", "请使用右上角“更新数据库”同步 QFII 或睿远持仓")

    def _refresh_filter_options(self):
        quarters = fund_holdings_store.list_quarters()

        current_subjects = self._selected_subject_names()
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
        latest_only, selected_quarters = self._quarter_filter_state()
        change_types = self._selected_change_types()

        self.proxy_model.set_subject_names(subject_names)
        self.proxy_model.set_change_types(change_types)
        self.proxy_model.setFilterText(self.search_box.text().strip())
        self.proxy_model.set_latest_only(latest_only)
        self.proxy_model.set_quarter_keys(selected_quarters)
        self._schedule_view_state_save()

        if self.model.row_data:
            self.table_state.show_table()
        self._update_status_summary()

    def _build_view_rows(self, change_rows: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for row in change_rows or []:
            subject_code = str(row.get("subject_code") or "").strip()
            quarter_key = str(row.get("quarter_key") or "").strip()
            change_type = str(row.get("change_type") or "").strip()
            has_curr = change_type != "退出"
            has_prev = change_type != "新进"

            curr_ratio = self._format_pct(row.get("curr_ratio_pct"), show=has_curr)
            prev_ratio = self._format_pct(row.get("prev_ratio_pct"), show=has_prev)
            delta_ratio = self._format_pct(row.get("delta_ratio_pct"), show=has_curr or has_prev, signed=True)

            rows.append(
                {
                    "代码": str(row.get("stock_code") or "").strip(),
                    "名称": str(row.get("stock_name") or "").strip(),
                    "市价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "主体": str(row.get("subject_name") or "").strip(),
                    "主体代码": subject_code,
                    "季度": quarter_key,
                    "对比季度": str(row.get("compare_quarter_key") or "").strip(),
                    "变化类型": change_type,
                    "占比口径": str(row.get("ratio_label") or "").strip(),
                    "本期占比": curr_ratio,
                    "上期占比": prev_ratio,
                    "占比变化": delta_ratio,
                    "本期持股(万股)": self._format_amount(row.get("curr_hold_num_shares"), divisor=10000.0, show=has_curr),
                    "上期持股(万股)": self._format_amount(row.get("prev_hold_num_shares"), divisor=10000.0, show=has_prev),
                    "持股变化(万股)": self._format_amount(row.get("delta_hold_num_shares"), divisor=10000.0, show=has_curr or has_prev, signed=True),
                    "持有家数": str(int(float(row.get("holders_count") or 0))) if row.get("holders_count") else "--",
                    "_is_latest_subject_quarter": quarter_key == self._latest_quarter_map.get(subject_code),
                }
            )
        return rows

    @staticmethod
    def _format_pct(value, *, show: bool, signed: bool = False) -> str:
        if not show:
            return "--"
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return "--"
        prefix = "+" if signed and number > 0 else ""
        return f"{prefix}{number:.2f}%"

    @staticmethod
    def _format_amount(value, *, divisor: float, show: bool, signed: bool = False) -> str:
        if not show:
            return "--"
        try:
            number = float(value or 0) / divisor
        except (TypeError, ValueError):
            return "--"
        prefix = "+" if signed and number > 0 else ""
        return f"{prefix}{number:,.2f}"

    def _apply_latest_quotes_from_store(self):
        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot = {}

        if snapshot:
            self.model.update_quotes(snapshot)

    def _on_cache_reload_completed(self):
        self._apply_latest_quotes_from_store()
        self._update_status_summary()

    def _update_status_summary(self):
        rows = list(getattr(self.model, "row_data", []) or [])
        total = len(rows)
        visible = self.proxy_model.rowCount()
        qfii_quarter = self._latest_quarter_map.get(self._SUBJECT_CODE_QFII, "--")
        ruiyuan_quarter = self._latest_quarter_map.get(self._SUBJECT_CODE_RUIYUAN, "--")
        qfii_sync = str((self._latest_sync_map.get(self._SUBJECT_CODE_QFII) or {}).get("finished_at") or "")
        ruiyuan_sync = str((self._latest_sync_map.get(self._SUBJECT_CODE_RUIYUAN) or {}).get("finished_at") or "")

        if total == 0 and not self._sync_active:
            self.lbl_status.setText(self.format_status_summary("等待同步数据库", "QFII/睿远数据尚未入库"))
            return

        segments = [
            self._status_metric("显示 ", visible, f"/{total}"),
            self._status_metric("QFII ", qfii_quarter),
            self._status_metric("睿远 ", ruiyuan_quarter),
        ]
        if qfii_sync:
            segments.append(self._status_metric("QFII更新 ", qfii_sync[-8:]))
        if ruiyuan_sync:
            segments.append(self._status_metric("睿远更新 ", ruiyuan_sync[-8:]))

        self.lbl_status.setText(self.format_status_summary("基金持仓就绪", *segments))

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
            event_bus.sig_show_kline.emit(code)

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
        self._save_view_state()
        super().closeEvent(event)
