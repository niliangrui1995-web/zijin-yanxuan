# -*- coding: utf-8 -*-
"""Low-overhead animated tab container for the workspace."""

from __future__ import annotations

import time
from collections.abc import Iterable

from PyQt6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QTabWidget, QWidget

from core.logger import get_logger
from core.observability import record_metric
from ui.theme_tokens import build_ui_tokens

log = get_logger(__name__)


class SmoothTabWidget(QTabWidget):
    """QTabWidget with a snapshot-based transition between pages.

    The old page is captured before the index switch, then a lightweight pixmap
    overlay fades and slides away after the new page is shown. This avoids
    animating large live table widgets directly.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transition_enabled = True
        self._transition_distance = 18
        self._max_snapshot_pixels = 3_000_000
        self._min_transition_gap_ms = 32
        self._last_transition_at = 0.0
        self._last_snapshot_ms = 0.0
        self._slow_snapshot_threshold_ms = 12.0
        self._adaptive_transition_enabled = True
        self._slow_snapshot_skip_ms = 450
        self._transition_suspended_until = 0.0
        self._consecutive_slow_snapshots = 0
        self._snapshot_transition_skip_pairs: set[tuple[str, str]] = set()
        self._pending_transition: tuple[QWidget, QPixmap, int] | None = None
        self._transition_overlay: QLabel | None = None
        self._transition_group: QParallelAnimationGroup | None = None
        self.currentChanged.connect(self._run_pending_transition)

    def setTransitionEnabled(self, enabled: bool) -> None:
        self._transition_enabled = bool(enabled)

    def setTransitionDistance(self, distance: int) -> None:
        self._transition_distance = max(0, int(distance or 0))

    def setMaxSnapshotPixels(self, pixels: int) -> None:  # noqa: N802 - Qt API naming
        self._max_snapshot_pixels = max(0, int(pixels or 0))

    def setMinimumTransitionGap(self, gap_ms: int) -> None:  # noqa: N802 - Qt API naming
        self._min_transition_gap_ms = max(0, int(gap_ms or 0))

    def setSlowSnapshotThreshold(self, threshold_ms: int) -> None:  # noqa: N802 - Qt API naming
        self._slow_snapshot_threshold_ms = max(0.0, float(threshold_ms or 0))

    def setAdaptiveTransitionEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt API naming
        self._adaptive_transition_enabled = bool(enabled)

    def setSlowSnapshotSkipInterval(self, interval_ms: int) -> None:  # noqa: N802 - Qt API naming
        self._slow_snapshot_skip_ms = max(0, int(interval_ms or 0))

    def setSnapshotTransitionSkipPairs(self, pairs: Iterable[tuple[str, str]]) -> None:  # noqa: N802
        normalized: set[tuple[str, str]] = set()
        for pair in pairs or ():
            try:
                source, target = pair
            except (TypeError, ValueError):
                continue
            source_key = str(source or "").strip()
            target_key = str(target or "").strip()
            if source_key and target_key:
                normalized.add((source_key, target_key))
        self._snapshot_transition_skip_pairs = normalized

    def suspendTransitionsFor(self, interval_ms: int) -> None:  # noqa: N802 - Qt API naming
        try:
            interval = max(0, int(interval_ms or 0))
        except (TypeError, ValueError):
            interval = 0
        if interval <= 0:
            return
        self._pending_transition = None
        self._transition_suspended_until = max(
            self._transition_suspended_until,
            time.perf_counter() + (interval / 1000.0),
        )

    def addTab(self, widget, *args):  # noqa: N802 - Qt API naming
        index = super().addTab(widget, *args)
        QTimer.singleShot(0, widget.ensurePolished)
        return index

    def insertTab(self, index, widget, *args):  # noqa: N802 - Qt API naming
        inserted = super().insertTab(index, widget, *args)
        QTimer.singleShot(0, widget.ensurePolished)
        return inserted

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt API naming
        target_index = int(index)
        self._prepare_transition(target_index)
        target_widget = self.widget(target_index) if 0 <= target_index < self.count() else None
        begin_reveal_batch = getattr(target_widget, "begin_workspace_reveal_batch", None)
        finish_reveal_batch = getattr(target_widget, "finish_workspace_reveal_batch", None)
        reveal_batch_started = False
        if callable(begin_reveal_batch):
            try:
                reveal_batch_started = bool(begin_reveal_batch())
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug("tab reveal batch skipped: %s", exc)
        try:
            super().setCurrentIndex(target_index)
        finally:
            if reveal_batch_started and callable(finish_reveal_batch):
                def _finish_reveal_batch() -> None:
                    try:
                        finish_reveal_batch()
                    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                        log.debug("tab reveal batch release skipped: %s", exc)

                # QStackedLayout completes show/layout synchronously, then
                # posts its coalescible update requests.  Releasing on the
                # next event-loop turn lets those requests collapse into the
                # single update implicitly queued by setUpdatesEnabled(True).
                QTimer.singleShot(0, _finish_reveal_batch)

    def prewarm_pages(self) -> None:
        for idx in range(self.count()):
            widget = self.widget(idx)
            if widget is not None:
                QTimer.singleShot(0, widget.ensurePolished)

    def _motion_duration(self) -> int:
        try:
            return int(build_ui_tokens()["motion"]["base"])
        except (KeyError, TypeError, ValueError):
            return 180

    def _prepare_transition(self, target_index: int) -> None:
        self._pending_transition = None
        if not self._transition_enabled or not self.isVisible():
            return
        if self._transition_group is not None:
            return
        now = time.perf_counter()
        if (now - self._last_transition_at) * 1000.0 < self._min_transition_gap_ms:
            return
        if self._adaptive_transition_enabled and now < self._transition_suspended_until:
            return

        old_index = self.currentIndex()
        if target_index == old_index or target_index < 0 or target_index >= self.count():
            return

        old_widget = self.currentWidget()
        if old_widget is None or old_widget.width() <= 0 or old_widget.height() <= 0:
            return

        stack_host = old_widget.parentWidget()
        if stack_host is None:
            return

        if self._should_skip_snapshot_transition(old_widget, target_index):
            return

        pixel_count = old_widget.width() * old_widget.height()
        if self._max_snapshot_pixels and pixel_count > self._max_snapshot_pixels:
            return

        snapshot_started_at = time.perf_counter()
        pixmap = old_widget.grab()
        self._last_snapshot_ms = (time.perf_counter() - snapshot_started_at) * 1000.0
        target_widget = self.widget(target_index)
        record_metric(
            "tab_transition_snapshot_ms",
            self._last_snapshot_ms,
            unit="ms",
            tags={
                "pixels": str(pixel_count),
                "source": str(getattr(old_widget, "workspace_key", "") or old_widget.__class__.__name__),
                "target": str(
                    getattr(target_widget, "workspace_key", "")
                    or (target_widget.__class__.__name__ if target_widget is not None else "unknown")
                ),
            },
        )
        if self._last_snapshot_ms >= self._slow_snapshot_threshold_ms:
            log.debug(
                "tab transition snapshot %.1fms widget=%s size=%sx%s pixels=%s",
                self._last_snapshot_ms,
                old_widget.__class__.__name__,
                old_widget.width(),
                old_widget.height(),
                pixel_count,
            )
            if self._adaptive_transition_enabled:
                self._consecutive_slow_snapshots += 1
                skip_ms = self._slow_snapshot_skip_ms * min(3, self._consecutive_slow_snapshots)
                self._transition_suspended_until = time.perf_counter() + (skip_ms / 1000.0)
                self._last_transition_at = now
                return
        else:
            self._consecutive_slow_snapshots = 0
        if pixmap.isNull():
            return

        direction = 1 if target_index > old_index else -1
        self._pending_transition = (stack_host, pixmap, direction)
        self._last_transition_at = now

    def _widget_transition_ids(self, widget: QWidget | None) -> set[str]:
        if widget is None:
            return set()
        identifiers = {widget.__class__.__name__}
        object_name = str(widget.objectName() or "").strip()
        if object_name:
            identifiers.add(object_name)
        workspace_key = str(getattr(widget, "workspace_key", "") or "").strip()
        if workspace_key:
            identifiers.add(workspace_key)
        return identifiers

    def _should_skip_snapshot_transition(self, old_widget: QWidget, target_index: int) -> bool:
        if not self._snapshot_transition_skip_pairs:
            return False
        target_widget = self.widget(target_index)
        source_ids = self._widget_transition_ids(old_widget)
        target_ids = self._widget_transition_ids(target_widget)
        for source_id in source_ids:
            for target_id in target_ids:
                if (source_id, target_id) in self._snapshot_transition_skip_pairs:
                    log.debug(
                        "tab transition snapshot skipped source=%s target=%s",
                        source_id,
                        target_id,
                    )
                    record_metric(
                        "tab_transition_snapshot_skipped",
                        1,
                        unit="count",
                        tags={"source": source_id, "target": target_id},
                    )
                    return True
        return False

    def _run_pending_transition(self, _index: int) -> None:
        pending = self._pending_transition
        self._pending_transition = None
        if pending is None or not self._transition_enabled or not self.isVisible():
            return

        stack_host, pixmap, direction = pending
        if stack_host.width() <= 0 or stack_host.height() <= 0:
            return

        self._clear_transition()

        overlay = QLabel(stack_host)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setPixmap(pixmap)
        overlay.setScaledContents(True)
        overlay.setGeometry(stack_host.rect())
        overlay.raise_()
        overlay.show()

        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(1.0)
        overlay.setGraphicsEffect(effect)

        duration = self._motion_duration()
        fade = QPropertyAnimation(effect, b"opacity", overlay)
        fade.setDuration(duration)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        start_rect = QRect(stack_host.rect())
        end_rect = QRect(start_rect)
        end_rect.translate(-direction * self._transition_distance, 0)
        slide = QPropertyAnimation(overlay, b"geometry", overlay)
        slide.setDuration(duration)
        slide.setStartValue(start_rect)
        slide.setEndValue(end_rect)
        slide.setEasingCurve(QEasingCurve.Type.OutQuart)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self._clear_transition)

        self._transition_overlay = overlay
        self._transition_group = group
        group.start()

    def resizeEvent(self, event):  # noqa: N802 - Qt API naming
        super().resizeEvent(event)
        overlay = self._transition_overlay
        if overlay is not None:
            parent = overlay.parentWidget()
            if parent is not None:
                overlay.setGeometry(parent.rect())

    def _clear_transition(self) -> None:
        group = self._transition_group
        self._transition_group = None
        if group is not None:
            try:
                group.stop()
            except RuntimeError:
                pass
            group.deleteLater()

        overlay = self._transition_overlay
        self._transition_overlay = None
        if overlay is not None:
            try:
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                pass
