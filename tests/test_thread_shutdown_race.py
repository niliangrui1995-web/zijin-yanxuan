# -*- coding: utf-8 -*-
from __future__ import annotations

import ui.components.thread_shutdown as shutdown_module


class _Signal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self) -> None:
        for slot in tuple(self._slots):
            slot()


class _FinishesInsideStop:
    def __init__(self) -> None:
        self.finished = _Signal()
        self.running = True
        self.deleted = False

    def isRunning(self) -> bool:
        return self.running

    def stop(self) -> None:
        self.running = False
        self.finished.emit()

    def deleteLater(self) -> None:
        self.deleted = True


class _Log:
    def __init__(self) -> None:
        self.messages = []

    def warning(self, message) -> None:
        self.messages.append(message)


def test_finished_is_connected_before_a_synchronous_stop_can_emit(monkeypatch):
    thread = _FinishesInsideStop()
    monkeypatch.setattr(shutdown_module.QTimer, "singleShot", lambda *_args: None)

    try:
        accepted = shutdown_module.request_thread_shutdown(
            thread,
            label="synchronous-finish",
            stop=thread.stop,
            timeout_ms=10,
        )

        assert accepted is True
        assert thread not in shutdown_module._PENDING_THREADS
        assert thread.deleted is True
    finally:
        shutdown_module._PENDING_THREADS.discard(thread)


def test_shutdown_rejects_missing_stopped_and_deleted_threads():
    assert shutdown_module.request_thread_shutdown(None, label="missing") is False

    class _DeletedThread:
        def isRunning(self):
            raise RuntimeError("deleted")

    assert shutdown_module.request_thread_shutdown(_DeletedThread(), label="deleted") is False

    stopped = _FinishesInsideStop()
    stopped.running = False
    assert shutdown_module.request_thread_shutdown(stopped, label="stopped", stop=stopped.stop) is False


def test_shutdown_connection_failure_stops_and_retains_until_not_running(monkeypatch):
    thread = _FinishesInsideStop()
    thread.finished.connect = lambda _slot: (_ for _ in ()).throw(TypeError("broken signal"))
    thread.stop_requested = False
    callbacks = []
    monkeypatch.setattr(shutdown_module.QTimer, "singleShot", lambda _timeout, callback: callbacks.append(callback))

    def _request_stop_only():
        thread.stop_requested = True

    try:
        assert shutdown_module.request_thread_shutdown(
            thread,
            label="broken",
            stop=_request_stop_only,
        ) is True
        assert thread.stop_requested is True
        assert thread.running is True
        assert thread.deleted is False
        assert thread in shutdown_module._PENDING_THREADS

        thread.running = False
        for callback in tuple(callbacks):
            callback()
        assert thread not in shutdown_module._PENDING_THREADS
        assert thread.deleted is True
    finally:
        shutdown_module._PENDING_THREADS.discard(thread)


def test_shutdown_logs_stop_failure_and_bounded_wait_warning(monkeypatch):
    thread = _FinishesInsideStop()
    logger = _Log()
    callbacks = []
    monkeypatch.setattr(shutdown_module.QTimer, "singleShot", lambda _timeout, callback: callbacks.append(callback))

    def _failed_stop():
        raise RuntimeError("stop failed")

    try:
        assert shutdown_module.request_thread_shutdown(
            thread,
            label="slow",
            stop=_failed_stop,
            timeout_ms=10,
            logger=logger,
        ) is True
        callbacks[0]()

        assert len(logger.messages) == 2
        assert "停止请求失败" in logger.messages[0]
        assert "仍在后台退出中" in logger.messages[1]
    finally:
        thread.running = False
        thread.finished.emit()
        shutdown_module._PENDING_THREADS.discard(thread)
