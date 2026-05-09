# -*- coding: utf-8 -*-
"""
tests/conftest.py — pytest 通用 Fixture

为什么需要这个：
    PyQt6 应用的测试需要一个 QApplication 实例存在，否则任何 QWidget/QSettings
    都会直接 segfault。这里统一创建一次，所有测试共享。
"""
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """整个测试会话只创建一次 QApplication，防止重复创建导致崩溃"""
    import sys

    from PyQt6.QtWidgets import QApplication

    # 如果已经有 QApplication 实例（比如在 IDE 中跑），直接复用
    existing_app = QApplication.instance()
    if existing_app is not None:
        yield existing_app
        return

    app = QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def reset_global_runtime_state():
    """隔离跨测试的全局行情快照，避免排序和表格用例互相污染。"""
    try:
        from core.global_store import global_store

        global_store.reset_runtime_state()
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        global_store = None

    yield

    if global_store is not None:
        global_store.reset_runtime_state()
    try:
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for _ in range(3):
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        pass
