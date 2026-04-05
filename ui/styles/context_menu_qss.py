# -*- coding: utf-8 -*-
"""
ui/styles/context_menu_qss.py
右键菜单的统一 QSS 样式 — 消除四处复制粘贴的维护噩梦
"""

# 为什么要集中管理？
# MainWindow / ScanTab / RtMonitorTab / WatchlistTab 四处右键菜单
# 原先各自硬编码了完全相同的 QSS 字符串，改一处容易漏另三处。
CONTEXT_MENU_QSS = """
    QMenu {
        background-color: #151820;
        color: #C9CDD4;
        border: 1px solid #252A36;
        border-radius: 8px;
        padding: 4px;
    }
    QMenu::item {
        padding: 6px 24px;
    }
    QMenu::item:selected {
        background-color: rgba(59, 130, 246, 0.2);
        color: white;
    }
    QMenu::separator {
        height: 1px;
        background: #252A36;
        margin: 4px 8px;
    }
"""
