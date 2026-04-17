# -*- coding: utf-8 -*-
"""基金持仓 Tab。"""

from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
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
from ui.components import SearchFilter, TableStateWrapper, VCPTableView
from ui.components.stock_context_menu import build_stock_context_menu
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab


class FundHoldingsFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._subject_code = ""
        self._quarter_key = ""
        self._change_type = ""
        self._latest_only = True

    def set_subject_code(self, subject_code: str):
        self._subject_code = str(subject_code or "").strip()
        self.invalidateFilter()

    def set_quarter_key(self, quarter_key: str):
        self._quarter_key = str(quarter_key or "").strip()
        self.invalidateFilter()

    def set_change_type(self, change_type: str):
        self._change_type = str(change_type or "").strip()
        self.invalidateFilter()

    def set_latest_only(self, latest_only: bool):
        self._latest_only = bool(latest_only)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        row_data = model.row_data[source_row]

        if self._subject_code and str(row_data.get("主体代码", "")).strip() != self._subject_code:
            return False

        if self._change_type and str(row_data.get("变化类型", "")).strip() != self._change_type:
            return False

        if self._quarter_key and str(row_data.get("季度", "")).strip() != self._quarter_key:
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

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._latest_quarter_map: dict[str, str] = {}
        self._latest_sync_map: dict[str, dict] = {}
        self._sync_task_id = ""
        self._sync_active = False

        self._init_ui()
        self._reload_from_db()

        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.lbl_status = QLabel("等待同步基金持仓数据库")

        self.cmb_subject = QComboBox()
        self.cmb_subject.setFixedWidth(180)
        self.cmb_subject.currentIndexChanged.connect(self._apply_filters)

        self.cmb_quarter = QComboBox()
        self.cmb_quarter.setFixedWidth(130)
        self.cmb_quarter.currentIndexChanged.connect(self._apply_filters)

        self.cmb_change = QComboBox()
        self.cmb_change.setFixedWidth(118)
        self.cmb_change.addItem("全部变化", "")
        for label in ("新进", "增持", "减持", "退出", "持平"):
            self.cmb_change.addItem(label, label)
        self.cmb_change.currentIndexChanged.connect(self._apply_filters)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码、名称、主体或变化...")
        self.search_box.setFixedWidth(220)
        self.search_box.textChanged.connect(self._apply_filters)

        filter_widgets = [self.cmb_subject, self.cmb_quarter, self.cmb_change, self.search_box]

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
            "本期持仓(万元)", "上期持仓(万元)", "持仓变化(万元)", "持有家数",
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
        default_widths = [70, 90, 70, 70, 75, 150, 90, 90, 80, 80, 90, 90, 90, 110, 110, 110, 118, 118, 118, 78]
        for index, width in enumerate(default_widths):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(index, width)
        self.bind_header_persistence(self.table, "fund_holdings_header_state_v1")

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
        subjects = fund_holdings_store.list_subjects()
        quarters = fund_holdings_store.list_quarters()

        current_subject = self.cmb_subject.currentData()
        current_quarter = self.cmb_quarter.currentData()

        with suppress(RuntimeError):
            self.cmb_subject.blockSignals(True)
            self.cmb_subject.clear()
            self.cmb_subject.addItem("全部主体", "")
            for subject in subjects:
                self.cmb_subject.addItem(
                    f"{subject['subject_name']}{' (' + subject['subject_code'] + ')' if subject['subject_code'] != subject['subject_name'] else ''}",
                    subject["subject_code"],
                )
            subject_index = max(0, self.cmb_subject.findData(current_subject))
            self.cmb_subject.setCurrentIndex(subject_index)
            self.cmb_subject.blockSignals(False)

        with suppress(RuntimeError):
            self.cmb_quarter.blockSignals(True)
            self.cmb_quarter.clear()
            self.cmb_quarter.addItem("最新季度", self._QUARTER_FILTER_LATEST)
            self.cmb_quarter.addItem("全部季度", self._QUARTER_FILTER_ALL)
            for quarter in quarters:
                self.cmb_quarter.addItem(quarter, quarter)
            quarter_index = self.cmb_quarter.findData(current_quarter)
            if quarter_index < 0:
                quarter_index = 0
            self.cmb_quarter.setCurrentIndex(quarter_index)
            self.cmb_quarter.blockSignals(False)

    def _apply_filters(self):
        subject_code = str(self.cmb_subject.currentData() or "").strip()
        quarter_value = str(self.cmb_quarter.currentData() or self._QUARTER_FILTER_LATEST).strip()
        change_type = str(self.cmb_change.currentData() or "").strip()

        self.proxy_model.set_subject_code(subject_code)
        self.proxy_model.set_change_type(change_type)
        self.proxy_model.setFilterText(self.search_box.text().strip())

        if quarter_value == self._QUARTER_FILTER_LATEST:
            self.proxy_model.set_latest_only(True)
            self.proxy_model.set_quarter_key("")
        elif quarter_value == self._QUARTER_FILTER_ALL:
            self.proxy_model.set_latest_only(False)
            self.proxy_model.set_quarter_key("")
        else:
            self.proxy_model.set_latest_only(False)
            self.proxy_model.set_quarter_key(quarter_value)

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
                    "本期持仓(万元)": self._format_amount(row.get("curr_hold_market_value_cny"), divisor=10000.0, show=has_curr),
                    "上期持仓(万元)": self._format_amount(row.get("prev_hold_market_value_cny"), divisor=10000.0, show=has_prev),
                    "持仓变化(万元)": self._format_amount(row.get("delta_hold_market_value_cny"), divisor=10000.0, show=has_curr or has_prev, signed=True),
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
