# -*- coding: utf-8 -*-
"""
ui/styles/context_menu_qss.py
右键菜单的统一 QSS 样式 — 支持主题切换

为什么要集中管理？
MainWindow / ScanTab / RtMonitorTab / WatchlistTab 四处右键菜单
原先各自硬编码了完全相同的 QSS 字符串，改一处容易漏另三处。
"""

from ui.theme import theme_manager


def generate_context_menu_qss(theme: dict = None) -> str:
    """根据当前主题动态生成右键菜单 QSS"""
    if theme is None:
        theme = theme_manager.current_theme
    t = theme

    return f"""
    QMenu {{
        background-color: {t["BG_MENU"]};
        color: {t["TEXT_SECONDARY"]};
        border: 1px solid {t["BORDER_MENU"]};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        font-size: 12px;
        padding: 6px 24px;
        min-height: 20px;
    }}
    QMenu::item:selected {{
        background-color: {t["SELECTION_BG"]};
        color: {t["TEXT_BRIGHT"]};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t["BORDER_MENU"]};
        margin: 4px 8px;
    }}
"""


# 向后兼容：保持 CONTEXT_MENU_QSS 变量名可用
CONTEXT_MENU_QSS = generate_context_menu_qss()
