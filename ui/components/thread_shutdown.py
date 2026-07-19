# -*- coding: utf-8 -*-
"""Non-blocking QThread shutdown helpers."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer

_PENDING_THREADS = set()


def _is_thread_running(thread) -> bool:
    try:
        return bool(thread.isRunning())
    except RuntimeError:
        return False


def _delete_thread_later(thread) -> None:
    try:
        thread.deleteLater()
    except (AttributeError, RuntimeError):
        pass


def _retain_until_finished(thread):
    cleaned = False

    def _cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        _PENDING_THREADS.discard(thread)
        _delete_thread_later(thread)

    _PENDING_THREADS.add(thread)
    try:
        thread.finished.connect(_cleanup)
    except (AttributeError, RuntimeError, TypeError):
        return _cleanup, False
    return _cleanup, True


def _request_stop(thread, stop, *, label: str, logger) -> None:
    try:
        if callable(stop):
            stop()
        if hasattr(thread, "requestInterruption"):
            thread.requestInterruption()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if logger is not None:
            logger.warning(f"[关闭] {label} 停止请求失败: {exc}")


def _warn_if_running(thread, *, label: str, logger) -> None:
    if _is_thread_running(thread) and logger is not None:
        logger.warning(f"[关闭] {label} 仍在后台退出中，已跳过 UI 线程等待")


def _poll_until_stopped(thread, cleanup, interval_ms: int = 100) -> None:
    if not _is_thread_running(thread):
        cleanup()
        return
    QTimer.singleShot(interval_ms, lambda: _poll_until_stopped(thread, cleanup, interval_ms))


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

    if thread is None or not _is_thread_running(thread):
        return False

    label_text = str(label or thread.__class__.__name__)
    cleanup, finished_connected = _retain_until_finished(thread)

    _request_stop(thread, stop, label=label_text, logger=logger)
    if not _is_thread_running(thread):
        cleanup()
    elif not finished_connected:
        _poll_until_stopped(thread, cleanup)

    QTimer.singleShot(
        max(0, int(timeout_ms or 0)),
        lambda: _warn_if_running(thread, label=label_text, logger=logger),
    )
    return True


def pending_thread_count() -> int:
    return len(_PENDING_THREADS)
