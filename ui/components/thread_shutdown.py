# -*- coding: utf-8 -*-
"""Non-blocking QThread shutdown helpers."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer

_PENDING_THREADS = set()


def request_thread_shutdown(
    thread,
    *,
    label: str,
    stop: Callable[[], None] | None = None,
    timeout_ms: int = 3000,
    logger=None,
) -> bool:
    """Ask a QThread to stop without blocking the GUI thread.

    The thread object is kept alive until ``finished`` so closing the window does
    not destroy a still-running QThread. This avoids the UI freeze caused by
    synchronous ``wait()`` calls during application shutdown.
    """

    if thread is None:
        return False

    label_text = str(label or thread.__class__.__name__)
    try:
        running = bool(thread.isRunning())
    except RuntimeError:
        return False

    try:
        if callable(stop):
            stop()
        elif hasattr(thread, "requestInterruption"):
            thread.requestInterruption()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if logger is not None:
            logger.warning(f"[关闭] {label_text} 停止请求失败: {exc}")

    if not running:
        return False

    _PENDING_THREADS.add(thread)

    def _cleanup():
        _PENDING_THREADS.discard(thread)
        try:
            thread.deleteLater()
        except (AttributeError, RuntimeError):
            pass

    try:
        thread.finished.connect(_cleanup)
    except (AttributeError, RuntimeError, TypeError):
        _cleanup()
        return False

    def _warn_if_still_running():
        try:
            still_running = bool(thread.isRunning())
        except RuntimeError:
            still_running = False
        if still_running and logger is not None:
            logger.warning(f"[关闭] {label_text} 仍在后台退出中，已跳过 UI 线程等待")

    QTimer.singleShot(max(0, int(timeout_ms or 0)), _warn_if_still_running)
    return True


def pending_thread_count() -> int:
    return len(_PENDING_THREADS)
