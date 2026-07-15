# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget

from ui.components import smooth_tab_widget as smooth_module


def test_smooth_tab_configuration_normalizes_values_and_pairs(qt_application):
    tabs = smooth_module.SmoothTabWidget()
    try:
        tabs.setTransitionEnabled(False)
        tabs.setTransitionEnabled(True)
        tabs.setTransitionDistance(-1)
        tabs.setMaxSnapshotPixels(-2)
        tabs.setMinimumTransitionGap(-3)
        tabs.setSlowSnapshotThreshold(-4)
        tabs.setSlowSnapshotSkipInterval(-5)
        tabs.setAdaptiveTransitionEnabled(False)
        tabs.setSnapshotTransitionSkipPairs(
            [
                (" source ", " target "),
                ("", "bad"),
                ("only-one",),
                None,
            ]
        )
        assert tabs._transition_distance == 0
        assert tabs._max_snapshot_pixels == 0
        assert tabs._min_transition_gap_ms == 0
        assert tabs._slow_snapshot_threshold_ms == 0.0
        assert tabs._slow_snapshot_skip_ms == 0
        assert not tabs._adaptive_transition_enabled
        assert tabs._snapshot_transition_skip_pairs == {("source", "target")}

        tabs._pending_transition = (None, None, 0)
        tabs.suspendTransitionsFor("bad")
        assert tabs._pending_transition is not None
        tabs.suspendTransitionsFor(0)
        assert tabs._pending_transition is not None
        tabs.suspendTransitionsFor(100)
        assert tabs._pending_transition is None
        assert tabs._transition_suspended_until > 0
    finally:
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_add_insert_prewarm_and_motion_fallback(monkeypatch, qt_application):
    polished = []

    class _Page(QWidget):
        def ensurePolished(self):
            polished.append(self.objectName())
            super().ensurePolished()

    tabs = smooth_module.SmoothTabWidget()
    try:
        first = _Page()
        first.setObjectName("first")
        second = _Page()
        second.setObjectName("second")
        assert tabs.addTab(first, "A") == 0
        assert tabs.insertTab(0, second, "B") == 0
        tabs.prewarm_pages()
        qt_application.processEvents()
        assert set(polished) == {"first", "second"}

        monkeypatch.setattr(smooth_module, "build_ui_tokens", lambda: {"motion": {"base": "220"}})
        assert tabs._motion_duration() == 220
        monkeypatch.setattr(smooth_module, "build_ui_tokens", lambda: {})
        assert tabs._motion_duration() == 180
    finally:
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_transition_identifiers_and_pair_matching(qt_application):
    tabs = smooth_module.SmoothTabWidget()
    try:
        source = QWidget()
        source.setObjectName("sourceObject")
        source.workspace_key = "sourceKey"
        target = QWidget()
        target.setObjectName("targetObject")
        target.workspace_key = "targetKey"
        tabs.addTab(source, "A")
        tabs.addTab(target, "B")

        assert tabs._widget_transition_ids(None) == set()
        assert tabs._widget_transition_ids(source) == {"QWidget", "sourceObject", "sourceKey"}
        assert not tabs._should_skip_snapshot_transition(source, 1)
        tabs.setSnapshotTransitionSkipPairs({("sourceKey", "targetKey")})
        assert tabs._should_skip_snapshot_transition(source, 1)
        tabs.setSnapshotTransitionSkipPairs({("none", "targetKey")})
        assert not tabs._should_skip_snapshot_transition(source, 1)
    finally:
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_real_snapshot_transition_and_resize(qt_application):
    tabs = smooth_module.SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(260, 180)
        tabs.setMinimumTransitionGap(0)
        tabs.setSlowSnapshotThreshold(10_000)
        tabs.setMaxSnapshotPixels(0)
        tabs.show()
        qt_application.processEvents()

        tabs.setCurrentIndex(1)
        assert tabs.currentIndex() == 1
        assert tabs._transition_group is not None
        assert tabs._transition_overlay is not None
        assert tabs._consecutive_slow_snapshots == 0

        overlay = tabs._transition_overlay
        tabs.resize(300, 200)
        qt_application.processEvents()
        assert overlay.geometry() == overlay.parentWidget().rect()

        tabs._clear_transition()
        assert tabs._transition_group is None
        assert tabs._transition_overlay is None
    finally:
        tabs.close()
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_null_snapshot_and_nonadaptive_slow_snapshot(qt_application):
    class _NullPage(QWidget):
        def grab(self):
            return QPixmap()

    tabs = smooth_module.SmoothTabWidget()
    try:
        tabs.addTab(_NullPage(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)
        tabs.setMinimumTransitionGap(0)
        tabs.setSlowSnapshotThreshold(10_000)
        tabs.show()
        qt_application.processEvents()
        tabs._prepare_transition(1)
        assert tabs._pending_transition is None

        tabs.removeTab(0)
        tabs.insertTab(0, QWidget(), "A")
        tabs.setCurrentIndex(0)
        tabs._clear_transition()
        tabs.setAdaptiveTransitionEnabled(False)
        tabs.setSlowSnapshotThreshold(0)
        tabs._prepare_transition(1)
        assert tabs._pending_transition is not None
    finally:
        tabs._pending_transition = None
        tabs._clear_transition()
        tabs.close()
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_prepare_and_run_guard_branches(monkeypatch, qt_application):
    tabs = smooth_module.SmoothTabWidget()
    try:
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tabs.resize(240, 160)

        tabs._prepare_transition(1)
        assert tabs._pending_transition is None
        tabs._run_pending_transition(1)

        tabs.show()
        qt_application.processEvents()
        tabs._transition_group = object()
        tabs._prepare_transition(1)
        tabs._transition_group = None
        tabs._last_transition_at = smooth_module.time.perf_counter()
        tabs.setMinimumTransitionGap(10_000)
        tabs._prepare_transition(1)
        tabs.setMinimumTransitionGap(0)
        tabs.setAdaptiveTransitionEnabled(True)
        tabs._transition_suspended_until = smooth_module.time.perf_counter() + 10
        tabs._prepare_transition(1)
        tabs._transition_suspended_until = 0
        tabs._prepare_transition(0)
        tabs._prepare_transition(-1)
        tabs._prepare_transition(99)

        standalone = QWidget()
        standalone.resize(10, 10)
        monkeypatch.setattr(tabs, "currentWidget", lambda: standalone)
        tabs._prepare_transition(1)
        standalone.resize(0, 0)
        tabs._prepare_transition(1)
        monkeypatch.undo()

        source = tabs.currentWidget()
        source.workspace_key = "source"
        tabs.widget(1).workspace_key = "target"
        tabs.setSnapshotTransitionSkipPairs({("source", "target")})
        tabs._prepare_transition(1)
        tabs.setSnapshotTransitionSkipPairs(set())
        tabs.setMaxSnapshotPixels(1)
        tabs._prepare_transition(1)

        tabs._pending_transition = (QWidget(), QPixmap(10, 10), 1)
        tabs._run_pending_transition(1)
        assert tabs._pending_transition is None
    finally:
        tabs._clear_transition()
        tabs.close()
        tabs.deleteLater()
        qt_application.processEvents()


def test_smooth_tab_clear_tolerates_deleted_qt_like_objects(qt_application):
    calls = []

    class _DeletedGroup:
        def stop(self):
            raise RuntimeError("deleted")

        def deleteLater(self):
            calls.append("group-delete")

    class _DeletedOverlay:
        def hide(self):
            raise RuntimeError("deleted")

        def deleteLater(self):
            calls.append("overlay-delete")

    tabs = smooth_module.SmoothTabWidget()
    tabs._transition_group = _DeletedGroup()
    tabs._transition_overlay = _DeletedOverlay()
    tabs._clear_transition()
    assert calls == ["group-delete"]
    tabs.deleteLater()
    qt_application.processEvents()
