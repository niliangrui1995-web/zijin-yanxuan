# -*- coding: utf-8 -*-
import time

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QWidget

from core.observability import clear_metric_history, metric_history
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.models.table_models import StockTableModel


def test_smooth_tab_widget_keeps_qtabwidget_contract_when_hidden():
    tabs = SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")

        tabs.setCurrentIndex(1)

        assert tabs.currentIndex() == 1
        assert tabs.count() == 2
        assert tabs._pending_transition is None
    finally:
        tabs.deleteLater()

def test_smooth_tab_widget_skips_expensive_snapshots_when_visible():
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)
        tabs.setMaxSnapshotPixels(1)
        tabs.show()
        app.processEvents()

        tabs._prepare_transition(1)

        assert tabs._pending_transition is None
    finally:
        tabs.close()
        tabs.deleteLater()


def test_smooth_tab_widget_disabled_transition_does_not_grab(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    grabbed = []

    class GrabWidget(QWidget):
        def grab(self):
            grabbed.append("grab")
            return super().grab()

    try:
        tabs.addTab(GrabWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)
        tabs.setTransitionEnabled(False)
        tabs.show()
        app.processEvents()

        tabs.setCurrentIndex(1)

        assert tabs.currentIndex() == 1
        assert grabbed == []
    finally:
        tabs.close()
        tabs.deleteLater()


def test_smooth_tab_widget_suspended_transition_does_not_grab(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    grabbed = []

    class GrabWidget(QWidget):
        def grab(self):
            grabbed.append("grab")
            return super().grab()

    try:
        tabs.addTab(GrabWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)
        tabs.suspendTransitionsFor(1000)
        tabs.show()
        app.processEvents()

        tabs.setCurrentIndex(1)

        assert tabs.currentIndex() == 1
        assert grabbed == []
    finally:
        tabs.close()
        tabs.deleteLater()


def test_smooth_tab_widget_configured_transition_pair_does_not_grab(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    grabbed = []
    clear_metric_history()

    class GrabWidget(QWidget):
        def grab(self):
            grabbed.append(self.workspace_key)
            return super().grab()

    try:
        source = GrabWidget()
        source.workspace_key = "watchlist"
        target = GrabWidget()
        target.workspace_key = "lhb"
        tabs.addTab(source, "Watchlist")
        tabs.addTab(target, "LHB")
        tabs.resize(240, 160)
        tabs.setSnapshotTransitionSkipPairs({("watchlist", "lhb"), ("lhb", "watchlist")})
        tabs.show()
        app.processEvents()

        tabs.setCurrentIndex(1)
        app.processEvents()
        tabs.setCurrentIndex(0)

        assert tabs.currentIndex() == 0
        assert grabbed == []
        assert tabs._pending_transition is None
        skipped = metric_history("tab_transition_snapshot_skipped")
        assert [sample.tags for sample in skipped] == [
            {"source": "watchlist", "target": "lhb"},
            {"source": "lhb", "target": "watchlist"},
        ]
        assert metric_history("tab_transition_snapshot_ms") == []
    finally:
        tabs.close()
        tabs.deleteLater()


def test_smooth_tab_widget_suspends_animation_after_slow_snapshot():
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)
        tabs.setMinimumTransitionGap(0)
        tabs.setSlowSnapshotThreshold(0)
        tabs.setSlowSnapshotSkipInterval(250)
        tabs.show()
        app.processEvents()

        tabs._prepare_transition(1)

        assert tabs._pending_transition is None
        assert tabs._consecutive_slow_snapshots == 1
        assert tabs._transition_suspended_until > time.perf_counter()
    finally:
        tabs.close()
        tabs.deleteLater()


def test_stock_table_model_same_code_update_avoids_model_reset():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.00"},
            {"代码": "000002", "名称": "B", "现价": "20.00"},
        ]
    )

    reset_spy = QSignalSpy(model.modelReset)
    change_spy = QSignalSpy(model.dataChanged)

    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.10"},
            {"代码": "000002", "名称": "B", "现价": "20.00"},
        ]
    )

    assert len(reset_spy) == 0
    assert len(change_spy) == 1
    assert model.row_data[0]["现价"] == "10.10"


def test_stock_table_model_same_code_reorder_avoids_model_reset():
    model = StockTableModel(["代码", "名称"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B"},
            {"代码": "000003", "名称": "C"},
        ]
    )

    reset_spy = QSignalSpy(model.modelReset)
    layout_spy = QSignalSpy(model.layoutChanged)
    change_spy = QSignalSpy(model.dataChanged)

    model.update_data(
        [
            {"代码": "000003", "名称": "C"},
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B+"},
        ]
    )

    assert len(reset_spy) == 0
    assert len(layout_spy) == 1
    assert len(change_spy) == 1
    assert [row["代码"] for row in model.row_data] == ["000003", "000001", "000002"]
    assert model.row_data[2]["名称"] == "B+"


def test_stock_table_model_keeps_reset_for_unscoped_membership_delta():
    model = StockTableModel(["代码", "名称"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B"},
        ]
    )
    reset_spy = QSignalSpy(model.modelReset)

    model.update_data(
        [
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B"},
            {"代码": "000003", "名称": "C"},
        ]
    )

    assert len(reset_spy) == 1


def test_stock_table_model_single_member_opt_in_rejects_multi_row_delta():
    model = StockTableModel(["代码", "名称"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B"},
        ]
    )
    reset_spy = QSignalSpy(model.modelReset)

    model.update_data(
        [
            {"代码": "000001", "名称": "A"},
            {"代码": "000002", "名称": "B"},
            {"代码": "000003", "名称": "C"},
            {"代码": "000004", "名称": "D"},
        ],
        allow_single_row_membership_delta=True,
    )

    assert len(reset_spy) == 1
