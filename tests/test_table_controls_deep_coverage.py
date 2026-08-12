# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QFont, QHelpEvent, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QTableView

from ui.components import table_controls as controls


def _render_widget(widget, qt_application, *, width=320, height=100):
    widget.resize(width, height)
    widget.show()
    qt_application.processEvents()
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    widget.render(pixmap)
    return pixmap


def test_table_visual_glyphs_render_all_semantic_shapes(qt_application):
    dot = controls.PulsingDot("#10B981")
    try:
        dot.opacity = 0.5
        dot.set_color("#EF4444")
        assert dot.opacity == 0.5
        assert not _render_widget(dot, qt_application, width=14, height=14).isNull()
        dot._stop_animation()
        dot.close()
    finally:
        dot.deleteLater()

    for tone in ("online", "busy", "offline", "invalid"):
        glyph = controls.StatusGlyph(tone)
        try:
            glyph.set_tone(tone)
            glyph.set_color("#123456")
            assert not _render_widget(glyph, qt_application, width=18, height=18).isNull()
            glyph.set_tone("invalid")
        finally:
            glyph.close()
            glyph.deleteLater()

    skeleton = controls.SkeletonShimmer()
    try:
        skeleton.set_running(True)
        assert not skeleton._timer.isActive()
        skeleton.show()
        qt_application.processEvents()
        assert skeleton._timer.isActive()
        skeleton.set_running(True)
        old_phase = skeleton._phase
        skeleton._tick()
        assert skeleton._phase != old_phase
        assert not _render_widget(skeleton, qt_application, width=320, height=72).isNull()
        skeleton.hide()
        qt_application.processEvents()
        assert not skeleton._timer.isActive()
        skeleton.show()
        qt_application.processEvents()
        assert skeleton._timer.isActive()
        skeleton.close()
    finally:
        skeleton.deleteLater()

    bull = controls.BullGlyph()
    try:
        assert not _render_widget(bull, qt_application, width=76, height=42).isNull()
    finally:
        bull.close()
        bull.deleteLater()


def test_multi_select_summary_and_button_all_toggle_paths(qt_application):
    assert controls.format_multi_select_summary("", []) == ("全部", "全部")
    assert controls.format_multi_select_summary("市场", ["A", "B"], inline_limit=2) == (
        "市场：A / B",
        "A、B",
    )
    assert controls.format_multi_select_summary("", ["A", "B", "C"], inline_limit=0) == (
        "3项",
        "A、B、C",
    )

    button = controls.MultiSelectFilterButton("全部")
    try:
        spy = QSignalSpy(button.selectionChanged)
        button.set_options([("a", "A"), ["b", ""], "c", ("a", "duplicate"), ""])
        assert button.option_values() == ["a", "b", "c"]
        assert button.option_labels() == ["A", "b", "c"]
        assert button.selected_values() == set()
        assert button.has_value(" a ")
        assert not button.has_value("missing")

        button.set_selected_values(["a", "missing", ""])
        assert button.selected_values() == {"a"}
        assert button.selected_labels() == ["A"]
        assert len(spy) == 1
        button.apply_summary("市场")
        assert button.text() == "市场：A"

        button.set_options([("a", "A2"), ("d", "D")], preserve_selection=True)
        assert button.selected_values() == {"a"}
        button.set_options([("x", "X")], preserve_selection=False)
        assert button.selected_values() == set()

        button._updating = True
        button._on_all_toggled(True)
        button._on_option_toggled(True)
        button._updating = False
        button._all_action.setChecked(False)
        button._on_all_toggled(False)
        assert button.selected_values() == set()
        button._actions["x"].setChecked(True)
        button._on_option_toggled(True)
        assert button.selected_values() == {"x"}
        button._on_all_toggled(True)
        assert button.selected_values() == set()
    finally:
        button.close()
        button.deleteLater()


def test_table_state_overlay_and_wrapper_all_modes(qt_application):
    actions = []
    overlay = controls.TableStateOverlay()
    try:
        overlay.resize(180, 240)
        overlay._handle_action()
        modes = ("empty", "loading", "offline", "cached", "error", "success", "info", "warning")
        for mode in modes:
            overlay.set_state(
                mode,
                mode,
                "",
                meta="meta" if mode == "error" else "",
                action_text="retry" if mode == "error" else "",
                action_callback=lambda: actions.append("retry") if mode == "error" else None,
            )
            assert overlay._mode == mode
            if mode == "error":
                overlay._action.click()
        assert actions == ["retry"]
        overlay._sync_card_width()
        _render_widget(overlay, qt_application, width=640, height=300)
        overlay.close()
    finally:
        overlay.deleteLater()

    table = QTableView()
    wrapper = controls.TableStateWrapper(table, empty_title="none", loading_title="wait")
    try:
        assert wrapper.table is table
        wrapper.sizeHint()
        wrapper.minimumSizeHint()
        wrapper.show_empty()
        wrapper.show_loading()
        wrapper.show_offline()
        wrapper.show_error(meta="x", action_text="retry", action_callback=lambda: actions.append("wrapper"))
        wrapper._overlay._action.click()
        wrapper.show_cached(meta="cache")
        wrapper.show_success(meta="ok")
        wrapper.show_info(meta="info")
        wrapper.show_table()
        assert actions[-1] == "wrapper"

        wrapper.resize(500, 300)
        wrapper.show()
        qt_application.processEvents()
        wrapper.show_loading()
        assert wrapper._state_animation is not None
        previous = wrapper._state_animation
        wrapper.show_table()
        assert wrapper._state_animation is None
        assert wrapper._overlay.graphicsEffect() is None
        assert table.graphicsEffect() is None
        wrapper.show_error()
        assert wrapper._state_animation is not previous
        qt_application.processEvents()
    finally:
        wrapper.close()
        wrapper.deleteLater()


def test_vcp_table_view_model_state_density_and_flash_paths(monkeypatch, qt_application):
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["代码", "名称"])
    model.appendRow([QStandardItem("300308"), QStandardItem("中际旭创")])
    model.appendRow([QStandardItem("000001"), QStandardItem("平安银行")])

    table = controls.VCPTableView(default_row_height=28)
    try:
        table.setModel(model)
        assert table._row_identity(0) == "300308"
        assert table._row_identity(-1) == ""
        assert table._find_row_by_identity("000001") == 1
        assert table._find_row_by_identity("") == -1
        assert table._find_row_by_identity("missing") == -1
        table.apply_density("紧凑")
        table.apply_density("舒适")
        table._on_sort_indicator_changed(1, Qt.SortOrder.DescendingOrder)
        assert table.sorted_column() == 1
        table.sizeHint()
        table.minimumSizeHint()

        table.setCurrentIndex(model.index(1, 1))
        table.selectRow(1)
        table._capture_refresh_state()
        assert table._refresh_state_snapshot["current_code"] == "000001"
        table._schedule_refresh_state_restore()
        table._restore_pending_refresh_state()
        table._restore_pending_scrollbars()
        table._restore_pending_scrollbars()

        table._restoring_refresh_state = True
        table._capture_refresh_state()
        table._schedule_refresh_state_restore()
        table._restoring_refresh_state = False
        table._refresh_state_snapshot = None
        table._schedule_refresh_state_restore()

        table.set_coalesced_flash_repaint_enabled(True)
        table.hide()
        table.schedule_flash_repaint_until(controls.time.time() + 10)
        assert not table._flash_repaint_timer.isActive()
        table.show()
        qt_application.processEvents()
        table.schedule_flash_repaint_until(controls.time.time() + 10)
        assert table._flash_repaint_timer.isActive()
        table._tick_flash_repaint()
        assert table._flash_repaint_until == 0.0

        table.set_coalesced_flash_repaint_enabled(False)
        monkeypatch.setattr(controls.time, "time", lambda: 100.0)
        table.schedule_flash_repaint_until(200.0)
        table._tick_flash_repaint()
        assert table._flash_repaint_timer.isActive()
        monkeypatch.setattr(controls.time, "time", lambda: 300.0)
        table._tick_flash_repaint()
        assert not table._flash_repaint_timer.isActive()

        table.set_ambient_repaint_enabled(True)
        assert table._ambient_repaint_timer.isActive()
        table.hide()
        qt_application.processEvents()
        assert not table._ambient_repaint_timer.isActive()
        table.set_ambient_repaint_enabled(False)

        table._on_theme_changed("dark")
        table._closing = True
        table._on_theme_changed("light")
        table._tick_flash_repaint()
        assert not table._flash_repaint_timer.isActive()
        table._closing = False
    finally:
        table.close()
        table.deleteLater()


def test_vcp_table_view_keeps_opt_in_base_viewport_background_after_theme_restyle(qt_application):
    table = controls.VCPTableView()
    try:
        viewport = table.viewport()
        table.set_viewport_base_background_enabled(True)

        assert table.property("vcpViewportBaseBackground") is True
        assert viewport.autoFillBackground() is True
        assert viewport.backgroundRole() == QPalette.ColorRole.Base

        viewport.setAutoFillBackground(False)
        table._on_theme_changed("dark")

        assert viewport.autoFillBackground() is True
        assert viewport.backgroundRole() == QPalette.ColorRole.Base

        table.set_viewport_base_background_enabled(False)
        assert table.property("vcpViewportBaseBackground") is False
        assert viewport.autoFillBackground() is False
    finally:
        table.close()
        table.deleteLater()


def test_vcp_table_tooltip_decision_and_event_error_paths(monkeypatch, qt_application):
    class _Index:
        def __init__(self, *, valid=True, tooltip="tip", display="display", width=200, pill=None, font=None):
            self.valid = valid
            self.tooltip = tooltip
            self.display = display
            self.width = width
            self.pill = pill
            self.font = font

        def isValid(self):
            return self.valid

        def data(self, role):
            return {
                Qt.ItemDataRole.ToolTipRole: self.tooltip,
                Qt.ItemDataRole.DisplayRole: self.display,
                Qt.ItemDataRole.FontRole: self.font,
                Qt.ItemDataRole.UserRole + 2: self.pill,
            }.get(role)

        def column(self):
            return 0

    table = controls.VCPTableView()
    try:
        monkeypatch.setattr(table, "columnWidth", lambda _column: 100)
        monkeypatch.setattr(
            table, "visualRect", lambda index: SimpleNamespace(isValid=lambda: True, width=lambda: index.width)
        )
        assert not table._should_show_tooltip_for_index(_Index(valid=False))
        assert not table._should_show_tooltip_for_index(_Index(tooltip=""))
        assert not table._should_show_tooltip_for_index(_Index(display=""))
        assert not table._should_show_tooltip_for_index(_Index(display="x", width=10))
        assert not table._should_show_tooltip_for_index(_Index(tooltip="普通提示", display="10.90", width=100))
        assert table._should_show_tooltip_for_index(
            _Index(
                tooltip="现价：10.90\n报价时间：2026-07-22 10:45:06\n新鲜度：network（sina）",
                display="10.90",
                width=100,
            )
        )
        assert table._should_show_tooltip_for_index(_Index(display="x" * 100, pill="#fff", font=QFont()))
        assert table._should_show_tooltip_for_index(_Index(display="x" * 100, font=QFont()))

        hidden = []
        monkeypatch.setattr(controls, "hide_floating_tooltip", lambda: hidden.append(True))
        event = QHelpEvent(QEvent.Type.ToolTip, QPoint(0, 0), QPoint(0, 0))
        table._closing = True
        assert table.viewportEvent(event)
        assert hidden
        table._closing = False
        monkeypatch.setattr(table, "indexAt", lambda _pos: (_ for _ in ()).throw(RuntimeError("bad index")))
        assert table.viewportEvent(event)
    finally:
        table.close()
        table.deleteLater()
