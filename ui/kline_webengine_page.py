# -*- coding: utf-8 -*-
"""QWebEnginePage 专用的轻量清理辅助。"""

from __future__ import annotations


def stop_webengine_page(page) -> bool:
    """停止页面加载；QWebEnginePage 不提供 QWebEngineView.stop()。"""

    if page is None:
        return True
    try:
        trigger_action = getattr(page, "triggerAction", None)
    except (AttributeError, RuntimeError, TypeError):
        return False
    if callable(trigger_action):
        try:
            from PyQt6.QtWebEngineCore import QWebEnginePage

            trigger_action(QWebEnginePage.WebAction.Stop)
            return True
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError):
            return False
    try:
        page.stop()
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True
