# -*- coding: utf-8 -*-

from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent, QRegion, QStandardItem, QStandardItemModel

import ui.components.table_controls as table_controls_module
from ui.components import (
    MultiSelectFilterButton,
    PulsingDot,
    StatusGlyph,
    TableStateOverlay,
    TableStateWrapper,
    VCPTableView,
    format_multi_select_summary,
)
from ui.components.table_controls import (
    MultiSelectFilterButton as ModuleMultiSelectFilterButton,
)
from ui.components.table_controls import (
    PulsingDot as ModulePulsingDot,
)
from ui.components.table_controls import (
    StatusGlyph as ModuleStatusGlyph,
)
from ui.components.table_controls import (
    TableStateOverlay as ModuleTableStateOverlay,
)
from ui.components.table_controls import (
    TableStateWrapper as ModuleTableStateWrapper,
)
from ui.components.table_controls import (
    VCPTableView as ModuleVCPTableView,
)
from ui.components.table_controls import (
    format_multi_select_summary as module_format_multi_select_summary,
)
from ui.models.table_models import RtSortFilterProxyModel, StockTableModel


def test_table_controls_are_reexported_from_components():
    from ui.tabs.fund_holdings_tab import format_multi_select_summary as tab_format_multi_select_summary

    assert VCPTableView is ModuleVCPTableView
    assert PulsingDot is ModulePulsingDot
    assert StatusGlyph is ModuleStatusGlyph
    assert MultiSelectFilterButton is ModuleMultiSelectFilterButton
    assert TableStateOverlay is ModuleTableStateOverlay
    assert TableStateWrapper is ModuleTableStateWrapper
    assert format_multi_select_summary is module_format_multi_select_summary
    assert tab_format_multi_select_summary is format_multi_select_summary


def test_multi_select_filter_button_applies_summary_without_emitting_signal(qt_application):
    button = MultiSelectFilterButton("全看")
    emitted = []
    button.selectionChanged.connect(lambda: emitted.append(True))
    button.set_options([("a", "甲"), ("b", "乙"), ("c", "丙")], preserve_selection=False)

    try:
        for selected, expected_text, expected_tooltip in (
            (set(), "分类：全看", "全看"),
            ({"a"}, "分类：甲", "甲"),
            ({"a", "b"}, "分类：甲 / 乙", "甲、乙"),
            ({"a", "b", "c"}, "分类：3项", "甲、乙、丙"),
        ):
            button.set_selected_values(selected, emit=False)
            emitted_before = len(emitted)

            button.apply_summary("分类", all_text="全看")

            assert button.text() == expected_text
            assert button.toolTip() == expected_tooltip
            assert len(emitted) == emitted_before
    finally:
        button.deleteLater()


def test_multi_select_filter_button_applies_text_before_tooltip():
    calls = []
    target = SimpleNamespace(
        selected_labels=lambda: ["甲", "乙", "丙"],
        setText=lambda text: calls.append(("text", text)),
        setToolTip=lambda text: calls.append(("tooltip", text)),
    )

    MultiSelectFilterButton.apply_summary(target, "分类", all_text="全看")

    assert calls == [("text", "分类：3项"), ("tooltip", "甲、乙、丙")]


def test_tab_filter_summary_refresh_entrypoints_keep_their_existing_arguments():
    from ui.tabs.earnings_tab import EarningsTab
    from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
    from ui.tabs.fund_holdings_tab import FundHoldingsTab
    from ui.tabs.log_tab import LogTab

    calls = []
    button = SimpleNamespace(apply_summary=lambda prefix, *, all_text: calls.append((prefix, all_text)))

    EarningsTab._refresh_type_filter_button_text(SimpleNamespace(type_filter=button))
    ForeignBlockTradeTab._refresh_filter_button_text(object(), button, "日期", "全部")
    FundHoldingsTab._refresh_subject_button_text(SimpleNamespace(cmb_subject=button))
    FundHoldingsTab._refresh_capital_attribute_button_text(SimpleNamespace(cmb_capital_attribute=button))
    LogTab._refresh_level_filter_button_text(SimpleNamespace(level_filter=button))

    assert calls == [("分类", "全看"), ("日期", "全部"), ("主体", "全部"), ("资金属性", "全部"), ("级别", "全部")]


def test_tab_filter_status_entrypoints_keep_zero_one_two_many_text():
    from ui.tabs.earnings_tab import EarningsTab
    from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
    from ui.tabs.log_tab import LogTab

    class SelectedLabels:
        def __init__(self, labels):
            self._labels = labels

        def selected_labels(self):
            return list(self._labels)

    for labels, expected in (
        ([], None),
        (["甲"], "甲"),
        (["甲", "乙"], "甲 / 乙"),
        (["甲", "乙", "丙"], "3项"),
    ):
        filter_button = SelectedLabels(labels)
        assert EarningsTab._type_filter_status_text(SimpleNamespace(type_filter=filter_button)) == (expected or "全看")
        assert LogTab._level_filter_status_text(SimpleNamespace(level_filter=filter_button)) == (expected or "全部")
        assert ForeignBlockTradeTab._filter_status_text(object(), filter_button, all_text="全部") == (
            expected or "全部"
        )


def _rows(count: int):
    return [{"代码": f"{idx:06d}", "名称": f"N{idx}", "现价": f"{10 + idx:.2f}"} for idx in range(count)]


def _process_events(app, rounds: int = 4):
    for _ in range(rounds):
        app.processEvents()


def test_vcp_table_view_restores_current_row_selection_after_model_reset(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    table.resize(420, 260)
    table.show()

    try:
        source_model.update_data(_rows(120))
        _process_events(qt_application)
        original_scroll = min(180, table.verticalScrollBar().maximum())
        table.setCurrentIndex(proxy_model.index(70, 2))
        table.selectRow(70)
        table.verticalScrollBar().setValue(original_scroll)

        source_model.update_data(
            [{"代码": "999999", "名称": "插入行", "现价": "1.00"}]
            + [{"代码": row["代码"], "名称": row["名称"], "现价": "88.88"} for row in _rows(120)]
        )
        _process_events(qt_application)

        current = table.currentIndex()
        selected_rows = [index.row() for index in table.selectionModel().selectedRows()]

        assert current.isValid()
        assert table.verticalScrollBar().value() == original_scroll
        assert proxy_model.data(proxy_model.index(current.row(), 1), Qt.ItemDataRole.DisplayRole) == "000070"
        assert selected_rows == [71]
    finally:
        table.deleteLater()


def test_vcp_table_view_skips_restore_snapshot_for_initial_empty_model(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)

    try:
        source_model.update_data(_rows(3))
        _process_events(qt_application)

        assert table._refresh_state_snapshot is None
        assert table._pending_refresh_state_restore is None
        assert table._refresh_state_restore_timer.isActive() is False
    finally:
        table.deleteLater()


def test_vcp_table_view_elides_long_cell_text():
    table = VCPTableView()
    try:
        assert table.textElideMode() == Qt.TextElideMode.ElideRight
    finally:
        table.deleteLater()


def test_vcp_table_view_suppresses_tooltip_event_errors(qt_application):
    table = VCPTableView()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("truncated cell text")
    item.setToolTip("tooltip text")
    model.setItem(0, 0, item)
    table.setModel(model)
    table.resize(220, 120)
    table.show()
    _process_events(qt_application)

    try:
        index = model.index(0, 0)
        rect = table.visualRect(index)
        pos = rect.center() if rect.isValid() else QPoint(5, 5)

        def _raise_for_deleted_wrapper(_index):
            raise RuntimeError("wrapped C/C++ object of type QTableView has been deleted")

        table._should_show_tooltip_for_index = _raise_for_deleted_wrapper
        event = QHelpEvent(QEvent.Type.ToolTip, pos, table.viewport().mapToGlobal(pos))

        assert table.viewportEvent(event) is True
    finally:
        table.deleteLater()


def test_vcp_table_view_uses_floating_tooltip_for_truncated_cells(qt_application, monkeypatch):
    table = VCPTableView()
    model = QStandardItemModel(1, 1)
    item = QStandardItem("truncated cell text")
    item.setToolTip("tooltip text")
    model.setItem(0, 0, item)
    table.setModel(model)
    table.resize(140, 80)
    table.show()
    _process_events(qt_application)

    calls = []

    def _record_tooltip(text, global_pos, *, owner=None, rich_text=None):
        calls.append((text, bool(global_pos), owner is table.viewport(), rich_text))
        return True

    monkeypatch.setattr(table_controls_module, "show_floating_tooltip", _record_tooltip)
    table._should_show_tooltip_for_index = lambda _index: True

    try:
        index = model.index(0, 0)
        rect = table.visualRect(index)
        pos = rect.center() if rect.isValid() else QPoint(5, 5)
        event = QHelpEvent(QEvent.Type.ToolTip, pos, table.viewport().mapToGlobal(pos))

        assert table.viewportEvent(event) is True
        assert calls == [("tooltip text", True, True, False)]
    finally:
        table.deleteLater()


def test_vcp_table_view_delete_later_stops_deferred_restores():
    table = VCPTableView()
    try:
        table._refresh_state_snapshot = {"v_scroll": 0, "h_scroll": 0}
        table._schedule_refresh_state_restore()
        table._pending_scrollbar_restore = (1, 2)
        table._scrollbar_restore_timer.start(0)
        table._flash_repaint_timer.start()

        assert table._refresh_state_restore_timer.isActive() is True
        assert table._scrollbar_restore_timer.isActive() is True
        assert table._flash_repaint_timer.isActive() is True

        table.deleteLater()

        assert table._refresh_state_restore_timer.isActive() is False
        assert table._scrollbar_restore_timer.isActive() is False
        assert table._flash_repaint_timer.isActive() is False
        assert table._pending_refresh_state_restore is None
        assert table._pending_scrollbar_restore is None
        table = None
    finally:
        if table is not None:
            table.deleteLater()


def test_vcp_table_view_model_flash_role_starts_table_repaint_timer():
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    table.setModel(source_model)
    try:
        source_model.update_data(_rows(1))
        assert table._flash_repaint_timer.isActive() is False

        updated_rows = _rows(1)
        updated_rows[0]["现价"] = "11.00"
        source_model.update_data(updated_rows)

        assert table._flash_repaint_timer.isActive() is True
    finally:
        table.deleteLater()


def test_vcp_table_view_coalesced_flash_repaint_skips_hidden_table(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    table.setModel(source_model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    try:
        source_model.update_data(_rows(1))
        hidden_rows = _rows(1)
        hidden_rows[0]["现价"] = "11.00"
        source_model.update_data(hidden_rows)
        assert table._flash_repaint_timer.isActive() is False
        assert table._flash_dirty_indexes == set()
        assert table._pending_paint_metric is None

        table.show()
        _process_events(qt_application)
        visible_rows = _rows(1)
        visible_rows[0]["现价"] = "12.00"
        source_model.update_data(visible_rows)

        assert table._flash_repaint_timer.isSingleShot() is True
        assert table._flash_repaint_timer.isActive() is True
        table.hide()
        _process_events(qt_application)
        assert table._flash_repaint_timer.isActive() is False
        hidden_again_rows = _rows(1)
        hidden_again_rows[0]["现价"] = "13.00"
        source_model.update_data(hidden_again_rows)
        assert table._flash_dirty_indexes == set()
        assert table._pending_paint_metric is None
    finally:
        table.deleteLater()


def test_vcp_table_view_targeted_flash_expiry_updates_only_dirty_region(qt_application, monkeypatch):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True)
    rows = [
        {
            "代码": f"0000{row:02d}",
            "名称": f"股票{row}",
            "现价": f"{10 + row:.2f}",
            "涨幅%": 0.0,
            "市值": "--",
            "_zongguben": 1_000_000_000,
        }
        for row in range(41)
    ]
    source_model.update_data(rows)
    table.resize(900, 720)
    table.show()
    _process_events(qt_application)

    try:
        changed_rows = (0, 8, 16, 24, 32, 40)
        source_model.update_quotes(
            {
                rows[row]["代码"]: {"close": 11 + row, "last_close": 10 + row}
                for row in changed_rows
            }
        )
        price_column = source_model.headers.index("现价")
        proxy_model.sort(price_column, Qt.SortOrder.DescendingOrder)
        proxy_model.setFilterText("股票40")
        _process_events(qt_application)
        region, dirty_cells, visible_dirty_cells = table._flash_repaint_region()

        assert dirty_cells >= len(changed_rows)
        assert visible_dirty_cells > 0
        assert not region.isEmpty()
        assert region != QRegion(table.viewport().rect())

        updates = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: updates.append(args))
        table._tick_flash_repaint()

        assert len(updates) == 1
        assert len(updates[0]) == 1
        assert isinstance(updates[0][0], QRegion)
        assert updates[0][0] == region
        assert table._flash_repaint_timer.isActive() is False
    finally:
        table.deleteLater()


def test_pulsing_dot_delete_later_stops_deferred_animation_start():
    dot = PulsingDot()
    try:
        assert dot._start_timer.isActive() is False

        dot.show()
        assert dot._start_timer.isActive() is True

        dot.deleteLater()

        assert dot._start_timer.isActive() is False
        assert dot.anim.state() == dot.anim.State.Stopped
        dot = None
    finally:
        if dot is not None:
            dot.deleteLater()


def test_table_state_overlay_uses_compact_responsive_card(qt_application):
    table = VCPTableView()
    wrapper = TableStateWrapper(table)
    try:
        wrapper.resize(280, 180)
        wrapper.show_empty("Empty", "A long empty-state subtitle should stay inside the panel.")
        wrapper._overlay.resize(280, 180)
        _process_events(qt_application)

        card = wrapper._overlay._card
        assert card.minimumWidth() <= card.maximumWidth()
        assert card.maximumWidth() <= 280
        assert "border-radius: 14px;" in card.styleSheet()
    finally:
        wrapper.deleteLater()


def test_table_state_overlay_loading_skeleton_runs_only_while_visible(qt_application):
    overlay = TableStateOverlay()
    try:
        overlay.set_state("loading", "Loading")

        assert overlay._skeleton.isVisibleTo(overlay) is True
        assert overlay._skeleton._timer.isActive() is False
        overlay.show()
        _process_events(qt_application)
        assert overlay._skeleton._timer.isActive() is True
        assert overlay._dot.isVisible() is False

        overlay.hide()
        _process_events(qt_application)
        assert overlay._skeleton._timer.isActive() is False

        overlay.show()
        _process_events(qt_application)
        assert overlay._skeleton._timer.isActive() is True

        overlay.deleteLater()

        assert overlay._skeleton._timer.isActive() is False
        overlay = None
    finally:
        if overlay is not None:
            overlay.deleteLater()


def test_table_state_overlay_empty_state_uses_warm_glyph():
    overlay = TableStateOverlay()
    try:
        overlay.set_state("empty", "Empty")

        assert overlay._bull.isVisibleTo(overlay) is True
        assert overlay._skeleton.isVisible() is False
        assert "积蓄力量" in overlay._subtitle.text()
    finally:
        overlay.deleteLater()
