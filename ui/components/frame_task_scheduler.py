# -*- coding: utf-8 -*-
"""Small event-loop scheduler for UI work that must not run in one burst."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable

from PyQt6.QtCore import QElapsedTimer, QObject, QTimer, pyqtSignal

FrameTask = tuple[str, Callable[[], None]]


class FrameTaskScheduler(QObject):
    """Run UI-thread tasks in short batches across event-loop turns.

    This is intended for operations that must stay on the Qt GUI thread, such
    as model/view refresh hooks, but do not need to block the current frame.
    """

    finished = pyqtSignal()
    taskFailed = pyqtSignal(str, str)

    def __init__(
        self,
        parent=None,
        *,
        interval_ms: int = 0,
        frame_budget_ms: int = 6,
        max_tasks_per_frame: int = 1,
    ):
        super().__init__(parent)
        self._interval_ms = max(0, int(interval_ms or 0))
        self._frame_budget_ms = max(1, int(frame_budget_ms or 1))
        self._max_tasks_per_frame = max(1, int(max_tasks_per_frame or 1))
        self._tasks: deque[FrameTask] = deque()
        self._running = False
        self._paused = False
        self._drain_scheduled = False

    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._running = False
        self._paused = False
        self._drain_scheduled = False
        self._tasks.clear()

    def pause(self) -> bool:
        """Keep queued GUI phases intact while a foreground hold is active."""
        if not self._running or self._paused:
            return False
        self._paused = True
        return True

    def resume(self) -> bool:
        if not self._running or not self._paused:
            return False
        self._paused = False
        self._schedule_drain()
        return True

    def start(self, tasks: Iterable[FrameTask]) -> None:
        self.cancel()
        self._tasks = deque((str(label or "task"), action) for label, action in tasks if callable(action))
        if not self._tasks:
            QTimer.singleShot(0, self.finished.emit)
            return
        self._running = True
        self._paused = False
        self._schedule_drain()

    def _schedule_drain(self) -> None:
        if not self._running or self._paused or self._drain_scheduled:
            return
        self._drain_scheduled = True
        QTimer.singleShot(self._interval_ms, self._drain)

    def _drain(self) -> None:
        self._drain_scheduled = False
        if not self._running or self._paused:
            return

        timer = QElapsedTimer()
        timer.start()
        processed = 0

        while self._tasks and processed < self._max_tasks_per_frame:
            label, action = self._tasks.popleft()
            try:
                action()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                self.taskFailed.emit(label, str(exc))
            processed += 1
            if timer.elapsed() >= self._frame_budget_ms:
                break

        if self._tasks and self._running:
            self._schedule_drain()
            return

        self._running = False
        self._paused = False
        self.finished.emit()
