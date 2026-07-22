# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton, QTableView, QTabWidget, QToolButton, QWidget

from core.event_bus import event_bus
from ui.tabs import base_stock_tab as module
from ui.tabs.base_stock_tab import BaseStockTab, ToolbarStatusChipBar
from ui.workspaces.tab_registry import INTERACTIVE_TAB_LOAD_REASONS


def test_compact_status_and_direct_workspace_detection():
    assert module._compact_status_text("short", 10) == "short"
    assert module._compact_status_text("abcdefgh", 5) == "abcd…"

    owner = SimpleNamespace(parent=lambda: SimpleNamespace(tabs=None))
    assert module._is_direct_workspace_tab(owner)
    tabs = SimpleNamespace(currentWidget=lambda: owner)
    owner = SimpleNamespace(parent=lambda: SimpleNamespace(tabs=tabs))
    assert module._is_direct_workspace_tab(owner)
    other = object()
    tabs.currentWidget = lambda: other
    assert not module._is_direct_workspace_tab(owner)

    def _broken_current():
        raise RuntimeError("deleted")

    tabs.currentWidget = _broken_current
    assert not module._is_direct_workspace_tab(owner)


def test_direct_workspace_detection_uses_real_qtabwidget_stack(qt_application):
    tabs = QTabWidget()
    first = QWidget()
    second = QWidget()
    tabs.addTab(first, "first")
    tabs.addTab(second, "second")
    try:
        tabs.setCurrentWidget(first)
        qt_application.processEvents()
        assert module._is_direct_workspace_tab(first)
        assert not module._is_direct_workspace_tab(second)

        tabs.setCurrentWidget(second)
        qt_application.processEvents()
        assert not module._is_direct_workspace_tab(first)
        assert module._is_direct_workspace_tab(second)
    finally:
        tabs.deleteLater()


def test_proxy_kline_and_context_menu_edge_paths(monkeypatch):
    class _Index:
        def __init__(self, valid=True, row=0):
            self._valid = valid
            self._row = row

        def isValid(self):
            return self._valid

        def row(self):
            return self._row

    class _Proxy:
        def __init__(self, mapped_row=0, rows=1):
            self.mapped_row = mapped_row
            self.rows = rows

        def mapToSource(self, _index):
            return _Index(row=self.mapped_row)

        def rowCount(self):
            return self.rows

        def index(self, row, _column):
            return _Index(row=row)

    spy = QSignalSpy(event_bus.sig_show_kline_with_list)
    owner = SimpleNamespace(proxy_model=_Proxy(mapped_row=3), model=SimpleNamespace(row_data=[]))
    module._show_kline_from_proxy_index(owner, _Index(), event_bus)
    module._show_kline_from_proxy_index(owner, _Index(valid=False), event_bus)
    assert len(spy) == 0

    owner.proxy_model = _Proxy(mapped_row=0, rows=1)
    owner.model.row_data = [{"代码": "", "名称": "无代码"}]
    module._show_kline_from_proxy_index(owner, _Index(row=9), event_bus, require_code=True)
    module._show_kline_from_proxy_index(owner, _Index(row=9), event_bus)
    assert spy[-1][2] == 0

    calls = []
    owner.table = SimpleNamespace(indexAt=lambda _pos: _Index(valid=False))
    module._show_stock_context_menu_from_proxy_index(owner, object())
    owner.table.indexAt = lambda _pos: _Index(row=0)
    owner.proxy_model.mapped_row = 4
    module._show_stock_context_menu_from_proxy_index(owner, object())
    owner.proxy_model.mapped_row = 0
    module._show_stock_context_menu_from_proxy_index(owner, object())
    owner.model.row_data[0] = {"代码": "000001", "名称": "平安银行"}
    monkeypatch.setattr(
        "ui.components.stock_context_menu.build_stock_context_menu",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    module._show_stock_context_menu_from_proxy_index(owner, object())
    assert calls[0][1]["vcp_data"]["代码"] == "000001"


def test_status_chip_bar_overflow_and_empty_state(qt_application):
    source = QLabel("")
    bar = ToolbarStatusChipBar(source)
    try:
        bar.set_status_text("状态 | 甲 | 乙 | 丙 | 丁 | 戊 | 己 | 庚")
        visible = [chip for chip in bar._chips if not chip.isHidden()]
        assert len(visible) == 5
        assert visible[-1].text() == "+3"
        assert bar._primary.text() == "状态"

        bar.set_status_text("")
        assert bar._primary.isHidden()
        assert all(chip.isHidden() for chip in bar._chips)
    finally:
        bar.deleteLater()


def test_workspace_visibility_and_prime_snapshot_guards(monkeypatch, qt_application):
    tab = BaseStockTab()
    calls = []
    try:
        monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: False)
        tab._workspace_noninteractive_loaded = True
        assert not tab._should_start_interactive_runtime_on_show()

        monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: True)
        tab._workspace_load_reason = "background_prewarm"
        assert not tab._should_start_interactive_runtime_on_show()
        tab._workspace_load_reason = "tab_switch"
        assert tab._should_start_interactive_runtime_on_show()
        assert tab._workspace_noninteractive_loaded is False

        tab._workspace_load_reason = "background_prewarm"
        assert not tab._should_start_interactive_runtime_on_show()
        tab._workspace_load_reason = "user"
        assert tab._should_start_interactive_runtime_on_show()

        tab._runtime_cleanup_done = True
        assert not tab._prime_visible_local_quote_snapshot()
        tab._runtime_cleanup_done = False
        tab._workspace_noninteractive_loaded = True
        assert not tab._prime_visible_local_quote_snapshot()
        tab._workspace_noninteractive_loaded = False
        tab._workspace_load_reason = "background_prewarm"
        assert not tab._prime_visible_local_quote_snapshot()
        tab._workspace_load_reason = "user"
        assert not tab._prime_visible_local_quote_snapshot()
        tab.show()
        qt_application.processEvents()
        tab.refresh_table_from_latest_snapshot = lambda **kwargs: calls.append(kwargs)
        assert tab._prime_visible_local_quote_snapshot("model")
        assert calls == [{"current_model": "model", "async_local": True}]
    finally:
        tab.close()
        tab.deleteLater()


@pytest.mark.parametrize(
    "reason",
    sorted(INTERACTIVE_TAB_LOAD_REASONS),
)
def test_all_interactive_workspace_load_reasons_start_runtime(reason):
    tab = SimpleNamespace(
        _workspace_load_reason=reason,
        _workspace_noninteractive_loaded=True,
        _is_current_workspace_tab=lambda: True,
    )

    assert BaseStockTab._should_start_interactive_runtime_on_show(tab)
    assert tab._workspace_noninteractive_loaded is False


def test_quote_store_snapshot_rows_and_realtime_codes(monkeypatch):
    tab = BaseStockTab()
    applied = []
    try:
        tab._resolve_active_quote_model = lambda: None
        tab._apply_quote_store_snapshot()

        no_rows = object()
        tab._apply_quote_store_snapshot(no_rows)
        model = SimpleNamespace(row_data=[])
        tab._collect_table_codes = lambda _model: []
        tab._apply_quote_store_snapshot(model)

        model.row_data = [None, {"代码": "000001"}, {"代码": "AAPL"}, {"代码": "123"}, {"代码": "600000"}]
        tab._collect_table_codes = lambda _model: ["000001", "600000"]
        monkeypatch.setattr(
            module,
            "latest_quote_snapshot",
            lambda: {"000001": {"close": 10}, "300001": {"close": 20}},
        )
        monkeypatch.setattr(tab, "_apply_quote_snapshot", lambda payload: applied.append(payload))
        tab._apply_quote_store_snapshot(model)
        assert applied == [{"000001": {"close": 10}}]
        assert tab.get_row_data(model) == model.row_data[1:]
        assert tab.get_realtime_quote_codes(model) == {"000001", "600000"}
    finally:
        tab.deleteLater()


def test_table_discovery_selection_and_code_lookup(qt_application):
    tab = BaseStockTab()
    table = QTableView(tab)
    model = QStandardItemModel(2, 2, table)
    model.setHorizontalHeaderLabels(["名称", "代码"])
    model.setItem(0, 0, QStandardItem("平安银行"))
    model.setItem(0, 1, QStandardItem("000001"))
    model.setItem(1, 0, QStandardItem("万科A"))
    model.setItem(1, 1, QStandardItem("000002"))
    table.setModel(model)
    tab.table = table
    tab.table_scan = table
    try:
        assert tab.iter_tables() == [table]
        assert tab.get_primary_table() is table
        assert not tab.select_primary_row(-1)
        assert tab.select_primary_row(1)
        assert BaseStockTab._find_code_column(None) == -1
        assert BaseStockTab._find_code_column(model) == 1
        assert not tab.select_code_row("")
        assert tab.select_code_row("000002")
        assert table.currentIndex().row() == 1
        assert not tab.select_code_row("999999")
    finally:
        tab.deleteLater()


def test_toolbar_widget_escape_widths_labels_and_overflow(qt_application):
    tab = BaseStockTab()
    edit = QLineEdit()
    edit.setFixedWidth(120)
    button_a = QPushButton("短")
    button_b = QPushButton("较长操作")
    button_a.setProperty("toolbarWidthHints", ["短", "另一个文本"])
    button_b.setProperty("toolbarWidthHints", "较长操作|备用")
    tool = QToolButton()
    try:
        BaseStockTab._prepare_toolbar_widget(None)
        BaseStockTab._prepare_toolbar_widget(edit)
        assert edit.minimumWidth() >= 150
        assert edit.maximumWidth() >= 260
        BaseStockTab._install_search_escape_behavior(edit)
        BaseStockTab._install_search_escape_behavior(edit)
        edit.setText("keyword")
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_Escape)
        assert edit.text() == ""
        assert not edit.hasFocus()

        assert BaseStockTab._toolbar_button_texts(button_a) == ["短", "另一个文本"]
        assert BaseStockTab._toolbar_button_texts(button_b) == ["较长操作", "备用"]
        BaseStockTab._equalize_toolbar_action_widths([QLabel("skip"), button_a, button_b])
        assert button_a.minimumWidth() == button_b.minimumWidth()

        tool.setToolTip("工具提示")
        assert BaseStockTab._toolbar_action_label(tool) == "工具提示"
        tool.setToolTip("")
        tool.setAccessibleName("无障碍名称")
        assert BaseStockTab._toolbar_action_label(tool) == "无障碍名称"
        tool.setAccessibleName("")
        assert BaseStockTab._toolbar_action_label(tool) == "操作"

        clicked = []
        button_a.clicked.connect(lambda: clicked.append("a"))
        overflow = tab._build_toolbar_overflow_button([button_a, button_b])
        assert overflow is not None
        overflow.menu().actions()[0].trigger()
        assert clicked == ["a"]
        button_b.setText("更新后")
        button_b.setEnabled(False)
        overflow.menu().aboutToShow.emit()
        assert overflow.menu().actions()[1].text() == "更新后"
        assert not overflow.menu().actions()[1].isEnabled()
        assert tab._build_toolbar_overflow_button([button_a]) is None
    finally:
        tab.deleteLater()


def test_toolbar_split_column_preset_and_status_metrics(qt_application):
    tab = BaseStockTab()
    search = QLineEdit()
    primary = QPushButton("主操作")
    primary.setObjectName("primaryButton")
    normal_a = QPushButton("A")
    normal_b = QPushButton("B")
    normal_c = QPushButton("C")
    explicit = QPushButton("显式更多")
    explicit.setProperty("toolbarOverflow", True)
    try:
        widgets = tab._split_toolbar_actions([search, primary, normal_a, normal_b, normal_c, explicit])
        assert search in widgets and primary in widgets
        overflow_buttons = [item for item in widgets if isinstance(item, QToolButton) and item.menu() is not None]
        assert len(overflow_buttons) == 1

        assert tab._build_toolbar_flow_group("empty", []) is None
        group = tab._build_toolbar_flow_group("group", [QLabel("标签"), search], h_spacing=3)
        assert group.objectName() == "group"
        assert group.layout().spacing() == 3

        table = QTableView()
        table.setModel(QStandardItemModel(1, 2, table))
        tab.apply_table_column_preset(table, [10, 120, 300], stretch_last=False, min_width=60)
        assert table.horizontalHeader().minimumSectionSize() == 60
        assert table.columnWidth(0) >= 60
        assert not table.horizontalHeader().stretchLastSection()
        table.deleteLater()

        assert BaseStockTab._status_metric("数量 ", None) == ""
        assert BaseStockTab._status_metric("数量 ", "  ") == ""
        assert BaseStockTab._status_metric("数量 ", 3, "只") == "数量 3只"
    finally:
        tab.deleteLater()


def test_cleanup_flushes_savers_timers_and_is_idempotent(monkeypatch):
    tab = BaseStockTab()
    calls = []

    class _Timer:
        def stop(self):
            calls.append("stop")

    def _broken_saver():
        calls.append("save")
        raise OSError("disk")

    try:
        tab._header_state_savers = [lambda: calls.append("save-ok"), _broken_saver]
        tab._header_save_timers = [_Timer()]
        tab._proxy_filter_timers = {1: _Timer()}
        tab._quote_signal_connected = True
        monkeypatch.setattr(module, "shutdown_task_lifecycle_for_owner", lambda owner, timeout_ms: calls.append(timeout_ms))
        tab._cleanup_runtime_state()
        tab._cleanup_runtime_state()
        assert calls.count(750) == 1
        assert calls.count("stop") == 2
        assert tab._quote_signal_connected is False
    finally:
        tab.deleteLater()
