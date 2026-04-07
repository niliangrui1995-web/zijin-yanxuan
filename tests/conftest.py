# -*- coding: utf-8 -*-
"""
tests/conftest.py — pytest 通用 Fixture

为什么需要这个：
    PyQt6 应用的测试需要一个 QApplication 实例存在，否则任何 QWidget/QSettings 
    都会直接 segfault。这里统一创建一次，所有测试共享。
"""
import pytest


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """整个测试会话只创建一次 QApplication，防止重复创建导致崩溃"""
    from PyQt6.QtWidgets import QApplication
    import sys

    # 如果已经有 QApplication 实例（比如在 IDE 中跑），直接复用
    existing_app = QApplication.instance()
    if existing_app is not None:
        yield existing_app
        return

    app = QApplication(sys.argv)
    yield app
