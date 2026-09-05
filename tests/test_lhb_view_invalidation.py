"""LHB restore must preserve Qt frames while keeping cosmetic updates local."""

from PyQt6.QtCore import QItemSelection, Qt
from PyQt6.QtGui import QRegion, QStandardItem, QStandardItemModel

from infra.settings.table_view_state_store import TableViewStateStore
from ui.components.table_controls import VCPTableView


def _table(app):
    table = VCPTableView()
    model = QStandardItemModel(table)
    model.setHorizontalHeaderLabels(["代码", "名称", "现价", "涨幅%", "市值"])
    for row in range(42):
        model.appendRow([QStandardItem(str(row + column)) for column in range(5)])
    table.setModel(model)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="lhb")
    table.resize(800, 420)
    table.show()
    for _ in range(4):
        app.processEvents()
    return table


def test_sort_indicator_repaints_only_previous_and_new_columns(qt_application, monkeypatch):
    table = _table(qt_application)
    try:
        table._on_sort_indicator_changed(2, Qt.SortOrder.AscendingOrder)
        requested = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: requested.append(args))
        table._on_sort_indicator_changed(3, Qt.SortOrder.DescendingOrder)

        assert len(requested) == 1 and len(requested[0]) == 1
        region = requested[0][0]
        assert isinstance(region, QRegion)
        assert region.contains(table.visualRect(table.model().index(0, 2)))
        assert region.contains(table.visualRect(table.model().index(0, 3)))
        assert not region.contains(table.visualRect(table.model().index(0, 1)))
        assert table.sorted_column() == 3
    finally:
        table.deleteLater()


def test_sort_direction_does_not_queue_cosmetic_full_update(qt_application, monkeypatch):
    table = _table(qt_application)
    try:
        table.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        for _ in range(3):
            qt_application.processEvents()
        layouts = []
        table.model().layoutChanged.connect(lambda *_args: layouts.append(True))
        requested = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: requested.append(args))
        table.sortByColumn(2, Qt.SortOrder.DescendingOrder)

        assert layouts
        assert table.model().item(0, 2).text() == "9"
        assert requested == []
        assert table.sorted_column() == 2
    finally:
        table.deleteLater()


def test_sort_indicator_region_tracks_moved_hidden_and_cleared_columns(qt_application, monkeypatch):
    table = _table(qt_application)
    try:
        header = table.horizontalHeader()
        header.moveSection(header.visualIndex(2), 0)
        table.setColumnHidden(3, True)
        table._on_sort_indicator_changed(2, Qt.SortOrder.AscendingOrder)
        requested = []
        monkeypatch.setattr(table.viewport(), "update", lambda *args: requested.append(args))
        table._on_sort_indicator_changed(3, Qt.SortOrder.AscendingOrder)
        expected = table.visualRegionForSelection(
            QItemSelection(table.model().index(0, 2), table.model().index(41, 2))
        ).intersected(QRegion(table.viewport().rect()))
        assert requested == [(expected,)]
        requested.clear()
        table._on_sort_indicator_changed(-1, Qt.SortOrder.AscendingOrder)
        assert requested == []
        assert table.sorted_column() == -1
    finally:
        table.deleteLater()


def test_restore_geometry_without_replaying_saved_sort(qt_application):
    table = _table(qt_application)
    try:
        header = table.horizontalHeader()
        table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        for _ in range(4):
            qt_application.processEvents()
        header.resizeSection(1, 173)
        header.moveSection(4, 1)
        saved = header.saveState()
        table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        for _ in range(4):
            qt_application.processEvents()
        header.resizeSection(1, 99)
        header.moveSection(header.visualIndex(4), 4)
        changes = []
        layouts = []
        header.sortIndicatorChanged.connect(lambda *args: changes.append(args))
        table.model().layoutChanged.connect(lambda *_args: layouts.append(True))

        class Settings:
            values = {"grid": saved, "grid/sort_column": 2, "grid/sort_order": 1}

            def contains(self, key):
                return key in self.values

            def value(self, key, default=None):
                return self.values.get(key, default)

        restored = TableViewStateStore(Settings(), settings_key="grid").bind(
            table, table, [], restore_sort=False
        )
        for _ in range(4):
            qt_application.processEvents()
        assert restored is False
        assert header.sectionSize(1) == 173
        assert header.visualIndex(4) == 1
        assert header.sortIndicatorSection() == -1
        assert table.sorted_column() == -1
        assert changes == []
        assert layouts == []
    finally:
        table.deleteLater()


def test_lhb_show_starts_visible_delay_epoch_and_tracks_native_parent(qt_application):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QWidget

    host = QWidget()
    table = VCPTableView(host)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="lhb")
    try:
        table._mark_pending_paint_metric("model_reset", model_rows=42)
        table._pending_paint_metric["scheduled_at"] -= 8 * 3600
        host.show()
        pending = table._pending_paint_metric
        assert pending["hidden_pending_age_ms"] >= 8 * 3600 * 1000
        assert pending["reason"] == "model_reset"
        assert table._native_window_event_source is host
        table._record_native_window_event(QEvent(QEvent.Type.Resize))
        assert table._native_window_paint_provenance()["signal"] == "window_resize"
        host.hide()
        assert table._native_window_event_source is None
        assert table._pending_paint_metric is None
    finally:
        host.deleteLater()


def test_lhb_layout_keeps_native_persistent_selection_without_scroll_round_trip(qt_application, monkeypatch):
    from ui.models.table_models import RtSortFilterProxyModel, StockTableModel

    table = VCPTableView()
    model = StockTableModel(["代码", "名称", "现价"])
    rows = [{"代码": f"{row:06d}", "名称": str(row), "现价": str(row + 10)} for row in range(42)]
    proxy = RtSortFilterProxyModel(table)
    proxy.setSourceModel(model)
    table.setModel(proxy)
    table.set_targeted_flash_repaint_enabled(True, metric_scope="lhb")
    table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
    model.update_data(rows, hydrate_latest_quotes=False)
    table.resize(800, 400)
    table.show()
    try:
        for _ in range(4):
            qt_application.processEvents()
        code_column = model.headers.index("代码")
        table.setCurrentIndex(proxy.index(0, code_column))
        current_calls = []
        clear_calls = []
        original_current = table.setCurrentIndex
        selection = table.selectionModel()
        original_clear = selection.clearSelection

        def set_current(index):
            current_calls.append(index.row())
            original_current(index)

        def clear_selection():
            clear_calls.append(True)
            original_clear()

        monkeypatch.setattr(table, "setCurrentIndex", set_current)
        monkeypatch.setattr(selection, "clearSelection", clear_selection)
        model.update_data(list(reversed(rows)), hydrate_latest_quotes=False)
        for _ in range(5):
            qt_application.processEvents()

        assert table.currentIndex().data() == "000000"
        assert table.currentIndex().row() == 41
        assert [index.data() for index in selection.selectedRows(code_column)] == ["000000"]
        assert table.verticalScrollBar().value() == 0
        assert current_calls == []
        assert clear_calls == []
    finally:
        table.deleteLater()


def test_lhb_restore_still_restores_changed_column_geometry(qt_application):
    table = _table(qt_application)
    try:
        header = table.horizontalHeader()
        original_width = header.sectionSize(1)
        table._capture_refresh_state()
        snapshot = dict(table._refresh_state_snapshot)
        header.resizeSection(1, original_width + 40)
        header.moveSection(header.visualIndex(3), 1)
        table.setColumnHidden(4, True)

        table._restore_refresh_state(snapshot)

        assert header.sectionSize(1) == original_width
        assert header.visualIndex(3) == 3
        assert not table.isColumnHidden(4)
    finally:
        table.deleteLater()
