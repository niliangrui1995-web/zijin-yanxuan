# -*- coding: utf-8 -*-

import time
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QFocusEvent, QHelpEvent, QPaintEvent, QRegion, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QWidget

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
from ui.components.table_controls import _paint_region_metrics
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


def _arm_visible_shell_nav_repaint_guard(table, app, *, scope: str = "lhb"):
    table.set_targeted_flash_repaint_enabled(True, metric_scope=scope)
    table.resize(640, 360)
    table.show()
    _process_events(app)
    table.prepare_shell_nav_repaint_guard()
    table._activate_shell_nav_repaint_guard()
    assert table._shell_nav_repaint_guard is not None
    assert table.viewport().isVisible()


def _arm_visible_ai_preload_repaint_guard(table, app, *, load_reason: str = "background_prewarm"):
    table.set_targeted_flash_repaint_enabled(True, metric_scope="ai_industry_chain")
    table.resize(640, 360)
    table.show()
    _process_events(app)
    table.prepare_workspace_preload_repaint_guard(load_reason=load_reason)
    assert table._shell_nav_repaint_guard is not None
    assert table.viewport().isVisible()


def _arm_visible_lhb_staged_preload_repaint_guard(table, app):
    table.set_targeted_flash_repaint_enabled(True, metric_scope="lhb")
    table.resize(640, 360)
    table.show()
    _process_events(app)
    table.prepare_workspace_preload_repaint_guard(load_reason="background_prewarm")
    table._activate_shell_nav_repaint_guard()
    assert table._shell_nav_repaint_guard is not None
    assert table.viewport().isVisible()


def _arm_visible_generic_staged_preload_repaint_guard(table, app, *, scope: str):
    table.set_targeted_flash_repaint_enabled(False, metric_scope=scope)
    table.resize(640, 360)
    table.show()
    _process_events(app)
    table.prepare_workspace_preload_repaint_guard(load_reason="background_prewarm")
    table._activate_shell_nav_repaint_guard()
    assert table._shell_nav_repaint_guard is not None
    assert table.viewport().isVisible()


def _full_viewport_paint_event(table):
    return QPaintEvent(table.viewport().rect())


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


def test_vcp_table_view_palette_change_clears_source_presentation_cache(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    source_model.update_data(_rows(1), hydrate_latest_quotes=False)
    source_model.set_presentation_cache_enabled(True)
    source_model.data(source_model.index(0, 1), Qt.ItemDataRole.DisplayRole)
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)

    try:
        assert source_model._presentation_cache
        table.viewportEvent(QEvent(QEvent.Type.PaletteChange))
        assert source_model._presentation_cache == {}
    finally:
        table.deleteLater()


def test_vcp_table_view_restores_header_only_when_snapshot_changed(qt_application, monkeypatch):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    source_model.update_data(_rows(3))
    table.resize(420, 260)
    table.show()
    _process_events(qt_application)

    try:
        header = table.horizontalHeader()
        original_width = header.sectionSize(0)
        snapshot = {
            "v_scroll": 0,
            "h_scroll": 0,
            "current_row": -1,
            "selected_rows": [],
            "selected_codes": [],
            "header_state": header.saveState(),
            "proxy_sort_column": -1,
        }
        restore_calls = []
        original_restore = header.restoreState

        def _record_restore(state):
            restore_calls.append(state)
            return original_restore(state)

        monkeypatch.setattr(header, "restoreState", _record_restore)

        table._restore_refresh_state(dict(snapshot))
        assert restore_calls == []

        header.resizeSection(0, original_width + 37)
        table._restore_refresh_state(dict(snapshot))
        assert len(restore_calls) == 1
        assert header.sectionSize(0) == original_width
    finally:
        table.deleteLater()


def test_vcp_table_view_skips_restore_sort_when_proxy_state_is_unchanged(qt_application, monkeypatch):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    source_model.update_data(_rows(3))
    proxy_model.sort(1, Qt.SortOrder.AscendingOrder)
    table.resize(420, 260)
    table.show()
    _process_events(qt_application)

    try:
        snapshot = {
            "v_scroll": 0,
            "h_scroll": 0,
            "current_row": -1,
            "selected_rows": [],
            "selected_codes": [],
            "header_state": table.horizontalHeader().saveState(),
            "proxy_sort_column": 1,
            "proxy_sort_order": Qt.SortOrder.AscendingOrder,
        }
        sort_calls = []
        original_sort = table.sortByColumn

        def _record_sort(column, order):
            sort_calls.append((column, order))
            original_sort(column, order)

        monkeypatch.setattr(table, "sortByColumn", _record_sort)

        table._restore_refresh_state(dict(snapshot))
        assert sort_calls == []

        snapshot["proxy_sort_order"] = Qt.SortOrder.DescendingOrder
        table._restore_refresh_state(dict(snapshot))
        assert sort_calls == [(1, Qt.SortOrder.DescendingOrder)]
    finally:
        table.deleteLater()


def test_vcp_table_view_reuses_unchanged_screen_width_limit(monkeypatch):
    table = VCPTableView()
    header_calls = []
    width_calls = []
    geometry_calls = []
    try:
        monkeypatch.setattr(
            table.horizontalHeader(),
            "setMaximumSectionSize",
            lambda value: header_calls.append(value),
        )
        monkeypatch.setattr(table, "setMaximumWidth", lambda value: width_calls.append(value))
        monkeypatch.setattr(table, "updateGeometry", lambda: geometry_calls.append(True))

        table._apply_screen_width_limit()

        assert header_calls == []
        assert width_calls == []
        assert geometry_calls == []
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


def test_vcp_table_view_model_flash_role_starts_table_repaint_timer(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    table.setModel(source_model)
    try:
        table.show()
        _process_events(qt_application)
        source_model.update_data(_rows(1))
        assert table._flash_repaint_timer.isActive() is False

        updated_rows = _rows(1)
        updated_rows[0]["现价"] = "11.00"
        source_model.update_data(updated_rows)

        assert table._flash_repaint_timer.isActive() is True
    finally:
        table.deleteLater()


def test_vcp_table_view_legacy_flash_repaint_stops_while_hidden(qt_application):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    table.setModel(source_model)
    try:
        table.show()
        _process_events(qt_application)
        source_model.update_data(_rows(1))
        visible_rows = _rows(1)
        visible_rows[0]["现价"] = "11.00"
        source_model.update_data(visible_rows)

        assert table._flash_repaint_timer.isSingleShot() is False
        assert table._flash_repaint_timer.isActive() is True

        table.hide()
        _process_events(qt_application)

        assert table._flash_repaint_timer.isActive() is False
        assert table._flash_repaint_until == 0.0

        hidden_rows = _rows(1)
        hidden_rows[0]["现价"] = "12.00"
        source_model.update_data(hidden_rows)

        assert table._flash_repaint_timer.isActive() is False
        assert table._flash_repaint_until == 0.0
    finally:
        table.deleteLater()


def test_vcp_table_view_queued_legacy_flash_tick_skips_hidden_viewport(qt_application):
    class RecordingViewport(QWidget):
        def __init__(self):
            super().__init__()
            self.update_calls = []

        def update(self, *args):  # noqa: N802 - Qt API naming
            self.update_calls.append(args)
            super().update(*args)

    table = VCPTableView()
    viewport = RecordingViewport()
    table.setViewport(viewport)
    try:
        table.show()
        _process_events(qt_application)
        table.hide()
        _process_events(qt_application)
        viewport.update_calls.clear()

        # A timer event can already be queued when the tab becomes hidden.
        table._flash_repaint_until = float("inf")
        table._flash_repaint_timer.start()
        table._tick_flash_repaint()

        assert viewport.update_calls == []
        assert table._flash_repaint_timer.isActive() is False
        assert table._flash_repaint_until == 0.0
    finally:
        table.deleteLater()


def test_lhb_shell_nav_guard_bounds_redundant_full_paint_suppression_within_active_window(
    qt_application,
    monkeypatch,
):
    table = VCPTableView()
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application)

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._shell_nav_repaint_guard is None

        decisions = [item[2]["tags"]["decision"] for item in recorded]
        assert decisions == [
            "first_full_allowed",
            "suppress_redundant_full",
            "suppress_redundant_full",
        ]
    finally:
        table.deleteLater()


def test_watchlist_shell_nav_does_not_arm_viewport_paint_guard(qt_application):
    """Watchlist 的 StackOne 首帧必须由 Qt 正常完成，不能在 viewport 吞掉。"""
    table = VCPTableView()
    try:
        table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
        table.resize(640, 360)
        table.show()
        _process_events(qt_application)

        table.prepare_shell_nav_repaint_guard()

        assert table._shell_nav_repaint_guard is None
    finally:
        table.deleteLater()


def test_watchlist_reveal_batch_requests_one_complete_viewport_frame():
    """切回关注池必须解除更新门并请求 viewport 的完整首帧。"""
    class RecordingViewport(QWidget):
        def __init__(self):
            super().__init__()
            self.update_calls = []

        def update(self, *args):  # noqa: N802 - Qt API naming
            self.update_calls.append(args)
            return super().update(*args)

    table = VCPTableView()
    viewport = RecordingViewport()
    table.setViewport(viewport)
    try:
        table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
        assert table.begin_workspace_reveal_batch() is True
        viewport.update_calls.clear()

        table.finish_workspace_reveal_batch()

        assert table.updatesEnabled() is True
        assert table._workspace_reveal_batch_active is False
        assert viewport.update_calls == [()]
        assert table._shell_nav_repaint_guard is None
    finally:
        table.deleteLater()


def test_watchlist_prewarm_never_arms_a_viewport_paint_suppression_guard(qt_application):
    """后台缓存可以常驻，但可见关注池不得以吞 PaintEvent 来消除尾帧。"""
    table = VCPTableView()
    try:
        table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
        table.resize(640, 360)
        table.show()
        _process_events(qt_application)

        table.prepare_workspace_preload_repaint_guard(load_reason="background_prewarm")

        assert table._shell_nav_repaint_guard is None
    finally:
        table.deleteLater()


def test_watchlist_complete_viewport_paint_reaches_qtableview_even_if_a_stale_guard_would_defer(
    qt_application,
):
    """真实完整 PaintEvent 必须绘制所有可见行，不能等鼠标或滚动补画。"""
    class RecordingTable(VCPTableView):
        def __init__(self):
            super().__init__()
            self.actual_paint_calls = 0
            self.suppression_attempts = []

        def paintEvent(self, event):  # noqa: N802 - Qt API naming
            self.actual_paint_calls += 1
            return super().paintEvent(event)

        def _maybe_defer_shell_nav_full_paint(self, event):
            self.suppression_attempts.append("shell_nav")
            return True

        def _maybe_defer_inactive_window_full_paint(self, event):
            self.suppression_attempts.append("inactive_window")
            return True

    table = RecordingTable()
    model = QStandardItemModel(12, 2)
    for row in range(model.rowCount()):
        for column in range(model.columnCount()):
            model.setItem(row, column, QStandardItem(f"{row}-{column}"))
    table.setModel(model)
    try:
        table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
        table.resize(640, 360)
        table.show()
        _process_events(qt_application)
        table.actual_paint_calls = 0
        table.suppression_attempts.clear()

        QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))

        assert table.actual_paint_calls == 1
        assert table.suppression_attempts == []
    finally:
        table.deleteLater()


def test_vcp_table_view_shell_nav_guard_blocks_post_budget_full_paint_event(qt_application, monkeypatch):
    class RecordingTable(VCPTableView):
        def __init__(self):
            super().__init__()
            self.actual_paint_calls = 0

        def paintEvent(self, event):  # noqa: N802 - Qt API naming
            self.actual_paint_calls += 1
            return super().paintEvent(event)

    table = RecordingTable()
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application)
        table.actual_paint_calls = 0
        recorded.clear()

        for _ in range(4):
            QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))

        assert table.actual_paint_calls == 2
        decisions = [item[2]["tags"]["decision"] for item in recorded]
        assert decisions == [
            "first_full_allowed",
            "suppress_redundant_full",
            "suppress_redundant_full",
        ]
    finally:
        table.deleteLater()


def test_watchlist_focus_transition_updates_only_current_row(qt_application):
    """K 线抢走焦点时，关注池只失效当前行而非整个 viewport。"""
    class RecordingViewport(QWidget):
        def __init__(self):
            super().__init__()
            self.update_calls = []

        def update(self, *args):  # noqa: N802 - Qt API naming
            self.update_calls.append(args)
            return super().update(*args)

    window = QWidget()
    table = VCPTableView(window)
    viewport = RecordingViewport()
    table.setViewport(viewport)
    model = QStandardItemModel(3, 2)
    for row in range(model.rowCount()):
        for column in range(model.columnCount()):
            model.setItem(row, column, QStandardItem(f"{row}-{column}"))
    table.setModel(model)
    table.set_focus_transition_repaint_enabled(False)
    try:
        window.resize(640, 360)
        table.resize(640, 360)
        window.show()
        table.show()
        _process_events(qt_application)
        table.setCurrentIndex(model.index(1, 0))
        viewport.update_calls.clear()

        QCoreApplication.sendEvent(
            table,
            QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason),
        )
        QCoreApplication.sendEvent(
            table,
            QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason),
        )

        assert viewport.update_calls
        assert all(len(args) == 1 for args in viewport.update_calls)
        dirty_regions = [args[0] for args in viewport.update_calls]
        assert all(isinstance(region, QRegion) and region.boundingRect() != viewport.rect() for region in dirty_regions)
    finally:
        table.deleteLater()
        window.deleteLater()


def test_vcp_table_view_ai_preload_guard_skips_visible_redundant_full_paints(
    qt_application,
    monkeypatch,
):
    class RecordingTable(VCPTableView):
        def __init__(self):
            super().__init__()
            self.actual_paint_calls = 0

        def paintEvent(self, event):  # noqa: N802 - Qt API naming
            self.actual_paint_calls += 1
            return super().paintEvent(event)

    table = RecordingTable()
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        _arm_visible_ai_preload_repaint_guard(table, qt_application)
        table.actual_paint_calls = 0
        recorded.clear()

        for _ in range(4):
            QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))

        assert table.actual_paint_calls == 0
        assert [item[0] for item in recorded] == [
            "ai_industry_chain_preload_repaint_guard",
        ] * 4
        assert [item[2]["tags"]["decision"] for item in recorded] == [
            "suppress_redundant_full",
            "suppress_redundant_full",
            "suppress_redundant_full_after_budget",
            "suppress_redundant_full_after_budget",
        ]
        assert {item[2]["tags"]["workspace_load_reason"] for item in recorded} == {
            "background_prewarm"
        }
    finally:
        table.deleteLater()


def test_vcp_table_view_ai_preload_guard_keeps_real_content_and_structure_paints(
    qt_application,
    monkeypatch,
):
    table = VCPTableView()
    model = QStandardItemModel(3, 2)
    for row in range(model.rowCount()):
        for column in range(model.columnCount()):
            model.setItem(row, column, QStandardItem(f"{row}-{column}"))
    table.setModel(model)
    try:
        _arm_visible_ai_preload_repaint_guard(table, qt_application)
        assert model.setData(model.index(0, 0), "changed") is True
        updates = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: updates.append(args))

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert len(updates) == 1
        assert isinstance(updates[0][0], QRegion)
        assert updates[0][0] != QRegion(table.viewport().rect())

        table._on_model_reset()
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        table._pending_paint_metric = None
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
    finally:
        table.deleteLater()


def test_vcp_table_view_shell_nav_guard_accepts_lhb_scope(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application, scope="lhb")

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
    finally:
        table.deleteLater()


def test_lhb_shell_nav_guard_retains_post_budget_fail_open(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application, scope="lhb")

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._shell_nav_repaint_guard is None
    finally:
        table.deleteLater()


def test_lhb_staged_preload_guard_preserves_first_real_frame_after_empty_paint_and_defers_tail(
    qt_application,
    monkeypatch,
):
    """空区域事件不能耗掉首帧；后台暂存揭示后的无内容尾帧保持短窗口抑制。"""
    class RecordingTable(VCPTableView):
        def __init__(self):
            super().__init__()
            self.actual_paint_calls = 0

        def paintEvent(self, event):  # noqa: N802 - Qt API naming
            self.actual_paint_calls += 1
            return super().paintEvent(event)

    table = RecordingTable()
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        _arm_visible_lhb_staged_preload_repaint_guard(table, qt_application)
        table.actual_paint_calls = 0
        recorded.clear()

        empty_paint = QPaintEvent(QRegion())
        assert empty_paint.region().rectCount() == 0
        assert table._maybe_defer_shell_nav_full_paint(empty_paint) is False
        assert table._shell_nav_repaint_guard["first_full_seen"] is False

        QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))
        for _ in range(4):
            QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))

        assert table.actual_paint_calls == 1
        guard_metrics = [
            item for item in recorded if item[0] == "lhb_staged_preload_reveal_tail_guard"
        ]
        assert [item[2]["tags"]["decision"] for item in guard_metrics] == [
            "first_full_allowed",
            "suppress_redundant_full",
            "suppress_redundant_full",
            "suppress_redundant_full_after_budget",
            "suppress_redundant_full_after_budget",
        ]
        assert table._shell_nav_repaint_guard is not None
    finally:
        table.deleteLater()


def test_lhb_staged_preload_guard_keeps_required_model_structure_frame(qt_application):
    for structural_change in ("_on_model_reset", "_on_model_layout_changed"):
        table = VCPTableView()
        try:
            _arm_visible_lhb_staged_preload_repaint_guard(table, qt_application)
            assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False

            getattr(table, structural_change)()

            assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
            assert table._shell_nav_repaint_guard is not None
        finally:
            table.deleteLater()


def test_lhb_staged_preload_guard_is_not_overwritten_by_shell_nav_prepare(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_lhb_staged_preload_repaint_guard(table, qt_application)
        table.prepare_shell_nav_repaint_guard()

        guard = table._shell_nav_repaint_guard
        assert guard is not None
        assert guard["metric_name"] == "lhb_staged_preload_reveal_tail_guard"
        assert guard["retain_after_budget"] is True
    finally:
        table.deleteLater()


def test_lhb_staged_preload_guard_fails_open_for_geometry_and_flash_expiry(qt_application):
    """The reveal-tail guard must never defer a viewport resize or flash cleanup."""
    table = VCPTableView()
    try:
        _arm_visible_lhb_staged_preload_repaint_guard(table, qt_application)
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False

        table.resize(641, 361)
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._shell_nav_repaint_guard is None

        _arm_visible_lhb_staged_preload_repaint_guard(table, qt_application)
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        table._mark_pending_paint_metric("flash_expiry")

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        assert table._shell_nav_repaint_guard is None
    finally:
        table.deleteLater()


def test_generic_staged_preload_guard_preserves_first_frame_and_defers_redundant_tail(
    qt_application,
    monkeypatch,
):
    """基金/扫描/候选等 hidden staging 页共用同一首帧与尾帧边界。"""
    class RecordingTable(VCPTableView):
        def __init__(self):
            super().__init__()
            self.actual_paint_calls = 0

        def paintEvent(self, event):  # noqa: N802 - Qt API naming
            self.actual_paint_calls += 1
            return super().paintEvent(event)

    table = RecordingTable()
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        _arm_visible_generic_staged_preload_repaint_guard(
            table,
            qt_application,
            scope="fund_holdings",
        )
        table.actual_paint_calls = 0
        recorded.clear()

        QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))
        for _ in range(3):
            QCoreApplication.sendEvent(table.viewport(), _full_viewport_paint_event(table))

        assert table.actual_paint_calls == 1
        guard_metrics = [
            item for item in recorded if item[0] == "fund_holdings_staged_preload_reveal_tail_guard"
        ]
        assert [item[2]["tags"]["decision"] for item in guard_metrics] == [
            "first_full_allowed",
            "suppress_redundant_full",
            "suppress_redundant_full",
            "suppress_redundant_full_after_budget",
        ]
    finally:
        table.deleteLater()


def test_lhb_shell_nav_guard_rearms_after_required_structure_paint(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application, scope="lhb")

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        table._on_model_layout_changed()
        assert table._shell_nav_repaint_guard is not None
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        table._pending_paint_metric = None
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
    finally:
        table.deleteLater()


def test_lhb_shell_nav_guard_ignores_internal_state_restore_selection_change(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application, scope="lhb")
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False

        table._restoring_refresh_state = True
        try:
            table._on_shell_nav_guard_selection_changed()
        finally:
            table._restoring_refresh_state = False

        assert table._shell_nav_repaint_guard is not None
    finally:
        table.deleteLater()


def test_vcp_table_view_shell_nav_guard_turns_data_changed_into_visible_partial_update(
    qt_application,
    monkeypatch,
):
    table = VCPTableView()
    model = QStandardItemModel(3, 2)
    for row in range(model.rowCount()):
        for column in range(model.columnCount()):
            model.setItem(row, column, QStandardItem(f"{row}-{column}"))
    table.setModel(model)
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application)
        assert not table.visualRect(model.index(0, 0)).isEmpty()
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False

        assert model.setData(model.index(0, 0), "changed") is True
        updates = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: updates.append(args))

        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is True
        assert len(updates) == 1
        assert len(updates[0]) == 1
        dirty_region = updates[0][0]
        assert isinstance(dirty_region, QRegion)
        assert not dirty_region.isEmpty()
        assert dirty_region != QRegion(table.viewport().rect())

        assert table._maybe_defer_shell_nav_full_paint(QPaintEvent(dirty_region)) is False
        guard = table._shell_nav_repaint_guard
        assert guard is not None
        assert guard["partial_update_pending"] is False
        assert guard["rendered_content_epoch"] == guard["content_epoch"]
    finally:
        table.deleteLater()


def test_lhb_shell_nav_guard_rearms_after_structure_change_and_clears_when_hidden(qt_application):
    table = VCPTableView()
    try:
        _arm_visible_shell_nav_repaint_guard(table, qt_application)
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
        table._on_model_layout_changed()
        assert table._shell_nav_repaint_guard is not None
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False

        _arm_visible_shell_nav_repaint_guard(table, qt_application)
        table.hide()
        _process_events(qt_application)
        assert table._shell_nav_repaint_guard is None
        assert table._maybe_defer_shell_nav_full_paint(_full_viewport_paint_event(table)) is False
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
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
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
        # The test invokes the expiry callback explicitly below.  Stop the
        # real 500 ms timer so a slow offscreen paint cannot consume the dirty
        # indexes before the region assertions run.
        table._flash_repaint_timer.stop()
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
        assert float(table._pending_paint_metric["requested_dirty_bounding_area_ratio"]) < 1.0
        assert table._pending_paint_metric["requested_dirty_region_rects"] == region.rectCount()
        assert table._flash_repaint_timer.isActive() is False
    finally:
        table.deleteLater()


def test_vcp_table_view_reports_full_region_after_targeted_request(qt_application, monkeypatch):
    table = VCPTableView()
    model = StockTableModel(["代码", "名称"])
    table.setModel(model)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    model.update_data([{"代码": "600519", "名称": "贵州茅台"}])
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        table._mark_pending_paint_metric(
            "flash_expiry",
            requested_dirty_bounding_area_ratio="0.1452",
            requested_dirty_region_rects=3,
            requested_full_viewport=False,
        )
        table.viewport().update(QRegion(QRect(0, 0, 20, 20)))
        table.viewport().update()
        _process_events(qt_application)

        paint = next(item for item in recorded if item[0] == "watchlist_table_paint_ms")
        tags = paint[2]["tags"]
        assert tags["requested_dirty_bounding_area_ratio"] == "0.1452"
        assert tags["delivered_dirty_bounding_area_ratio"] == "1.0000"
        assert tags["delivered_full_viewport"] == "true"
        assert tags["reason"] == "flash_expiry"
        assert tags["targeted_request_reason"] == "flash_expiry"
        assert tags["region_expanded"] == "true"
        assert tags["delivery_kind"] == "full_after_targeted_request"
    finally:
        table.deleteLater()


def test_watchlist_full_viewport_paint_is_observable_without_pending_metric(
    qt_application,
    monkeypatch,
):
    """完整关注池帧必须留下实际 paintEvent 证据，不能只靠待处理业务指标。"""
    table = VCPTableView()
    model = StockTableModel(["代码", "名称"])
    table.setModel(model)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    model.update_data([{"代码": "600519", "名称": "贵州茅台"}])
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    monkeypatch.setattr("ui.components.table_controls.time.perf_counter", lambda: 100.0)
    try:
        table._pending_paint_metric = None
        table.viewport().update()
        _process_events(qt_application)

        paints = [item for item in recorded if item[0] == "watchlist_table_paint_ms"]
        assert len(paints) == 1
        assert paints[0][2]["tags"]["delivered_full_viewport"] == "true"
        assert paints[0][2]["tags"]["reason"] == "other"
    finally:
        table.deleteLater()


def test_vcp_table_view_records_full_quote_paint_provenance_through_proxy(
    qt_application,
    monkeypatch,
):
    """报价局部区与同轮全刷合并时，保留 proxy 与原生窗口归因。"""
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    proxy_model = RtSortFilterProxyModel(table)
    proxy_model.setSourceModel(source_model)
    table.setModel(proxy_model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    source_model.update_data(
        [{"代码": "600519", "名称": "贵州茅台", "现价": "10.00", "涨幅%": 0.0, "市值": "--"}]
    )
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        assert source_model.update_quotes({"600519": {"close": 11.0, "last_close": 10.0}}) == 1
        table.viewport().update()
        _process_events(qt_application)

        paints = [item for item in recorded if item[0] == "watchlist_table_paint_ms"]
        assert len(paints) == 1
        tags = paints[0][2]["tags"]
        assert tags["reason"] == "quote_data_changed"
        assert tags["delivered_full_viewport"] == "true"
        assert tags["delivery_kind"] == "full_viewport"
        assert float(tags["quote_dirty_bounding_area_ratio"]) < 1.0
        assert tags["quote_dirty_region_full"] == "false"
        assert "native_window_signal" in tags
        assert "native_window_last_event" in tags
    finally:
        table.deleteLater()


def test_vcp_table_view_keeps_full_quote_paint_over_update_threshold(
    qt_application,
    monkeypatch,
):
    """超过 Qt updateThreshold 的报价更新仍允许整表重绘。"""
    table = VCPTableView()
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    table.setModel(model)
    table.setUpdateThreshold(0)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    model.update_data(
        [{"代码": "600519", "名称": "贵州茅台", "现价": "10.00", "涨幅%": 0.0, "市值": "--"}]
    )
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        assert model.update_quotes({"600519": {"close": 11.0, "last_close": 10.0}}) == 1
        table.viewport().update()
        _process_events(qt_application)

        paints = [item for item in recorded if item[0] == "watchlist_table_paint_ms"]
        assert len(paints) == 1
        tags = paints[0][2]["tags"]
        assert tags["reason"] == "quote_data_changed"
        assert tags["threshold_exceeded"] == "true"
        assert tags["delivered_full_viewport"] == "true"
        assert tags["delivery_kind"] == "full_viewport"
    finally:
        table.deleteLater()


def test_vcp_table_view_keeps_explicit_viewport_refresh_over_quote_region(
    qt_application,
    monkeypatch,
):
    """排序等显式 viewport 刷新不能被报价局部回放吞掉。"""
    table = VCPTableView()
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值"])
    table.setModel(model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    model.update_data(
        [{"代码": "600519", "名称": "贵州茅台", "现价": "10.00", "涨幅%": 0.0, "市值": "--"}]
    )
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        assert model.update_quotes({"600519": {"close": 11.0, "last_close": 10.0}}) == 1
        table._on_sort_indicator_changed(2, Qt.SortOrder.AscendingOrder)
        _process_events(qt_application)

        paints = [item for item in recorded if item[0] == "watchlist_table_paint_ms"]
        assert len(paints) == 1
        tags = paints[0][2]["tags"]
        assert tags["reason"] == "viewport_refresh"
        assert tags["viewport_refresh_source"] == "sort_indicator"
        assert tags["delivered_full_viewport"] == "true"
        assert tags["delivery_kind"] == "full_viewport"
    finally:
        table.deleteLater()


def test_paint_region_full_viewport_uses_coverage_not_bounding_span():
    viewport_rect = QRect(0, 0, 100, 100)
    sparse_corners = QRegion(QRect(0, 0, 8, 8)).united(QRegion(QRect(92, 92, 8, 8)))

    ratio, rect_count, full_viewport = _paint_region_metrics(sparse_corners, viewport_rect)

    assert ratio == 1.0
    assert rect_count == 2
    assert full_viewport is False


def test_vcp_table_view_preserves_model_reset_when_flash_request_coalesces(
    qt_application,
    monkeypatch,
):
    table = VCPTableView()
    model = StockTableModel(["代码", "现价"])
    table.setModel(model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
    model.update_data([{"代码": "600519", "现价": "10.00"}])
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)

    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )
    try:
        model.update_data(
            [
                {"代码": "600519", "现价": "10.00"},
                {"代码": "000001", "现价": "8.00"},
            ]
        )
        assert model.set_cell_value(0, "现价", "10.10") is True
        table._flash_repaint_timer.stop()
        table._tick_flash_repaint()
        _process_events(qt_application)

        paint = next(item for item in recorded if item[0] == "watchlist_table_paint_ms")
        tags = paint[2]["tags"]
        assert tags["reason"] == "model_reset"
        assert tags["structural_reason"] == "model_reset"
        assert tags["pending_reasons"].split(",") == [
            "model_reset",
            "quote_data_changed",
            "flash_expiry",
        ]
        assert tags["targeted_request_reason"] == "flash_expiry"
        assert tags["delivered_full_viewport"] == "true"
        assert tags["delivery_kind"] == "structural_full_viewport"
        assert tags["targeted_request_coalesced_with_structural"] == "true"
        assert "region_expanded" not in tags
    finally:
        table.deleteLater()


def test_vcp_table_view_targeted_flash_without_metric_scope_keeps_region_update(qt_application, monkeypatch):
    table = VCPTableView()
    source_model = StockTableModel(["代码", "名称", "现价"])
    table.setModel(source_model)
    table.set_coalesced_flash_repaint_enabled(True)
    table.set_targeted_flash_repaint_enabled(True)
    source_model.update_data([{"代码": "600519", "名称": "贵州茅台", "现价": "1500.00"}])
    table.resize(640, 360)
    table.show()
    _process_events(qt_application)
    try:
        source_model.update_quotes({"600519": {"close": 1510.0, "last_close": 1500.0}})
        table._flash_repaint_timer.stop()
        updates = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: updates.append(args))

        table._tick_flash_repaint()

        assert len(updates) == 1
        assert len(updates[0]) == 1
        assert isinstance(updates[0][0], QRegion)
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
