# -*- coding: utf-8 -*-

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent, QStandardItem, QStandardItemModel

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
    assert VCPTableView is ModuleVCPTableView
    assert PulsingDot is ModulePulsingDot
    assert StatusGlyph is ModuleStatusGlyph
    assert MultiSelectFilterButton is ModuleMultiSelectFilterButton
    assert TableStateOverlay is ModuleTableStateOverlay
    assert TableStateWrapper is ModuleTableStateWrapper
    assert format_multi_select_summary is module_format_multi_select_summary


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


def test_pulsing_dot_delete_later_stops_deferred_animation_start():
    dot = PulsingDot()
    try:
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
        assert "border-radius: 12px;" in card.styleSheet()
    finally:
        wrapper.deleteLater()


def test_table_state_overlay_loading_skeleton_timer_stops_on_delete():
    overlay = TableStateOverlay()
    try:
        overlay.set_state("loading", "Loading")

        assert overlay._skeleton.isVisibleTo(overlay) is True
        assert overlay._skeleton._timer.isActive() is True
        assert overlay._dot.isVisible() is False

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
