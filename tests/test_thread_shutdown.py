# -*- coding: utf-8 -*-
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

import ui.components.thread_shutdown as shutdown_module


class _PollingThread(QThread):
    def __init__(self):
        super().__init__()
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def run(self):
        while not self.stop_requested:
            self.msleep(5)


def test_request_thread_shutdown_keeps_thread_alive_until_finished():
    app = QApplication.instance() or QApplication([])
    thread = _PollingThread()
    thread.start()

    assert shutdown_module.request_thread_shutdown(thread, label="test", stop=thread.stop, timeout_ms=50) is True

    for _ in range(100):
        app.processEvents()
        if thread not in shutdown_module._PENDING_THREADS:
            break
        QThread.msleep(5)

    assert thread.stop_requested is True
    assert shutdown_module._is_thread_running(thread) is False
    assert thread not in shutdown_module._PENDING_THREADS
