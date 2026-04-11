# -*- coding: utf-8 -*-
"""
ui/components/stock_context_menu.py
股票右键菜单工厂 — 统一四处重复的右键菜单逻辑 (#2)

为什么要统一？
原先 MainWindow / ScanTab / RtMonitorTab / WatchlistTab 各写了一份
几乎一模一样的右键菜单，新增菜单项要改 4 处。
现在用工厂模式，各 Tab 只需 3 行代码调用。
"""

from PyQt6.QtWidgets import QMenu, QApplication
from PyQt6.QtGui import QCursor

from ui.styles.context_menu_qss import generate_context_menu_qss
from ui.viewmodels.watchlist_vm import watchlist_vm
from core.event_bus import event_bus


def build_stock_context_menu(
    parent,
    code: str,
    name: str,
    *,
    show_watchlist_toggle: bool = True,
    show_export: bool = False,
    export_callback=None,
    vcp_data: dict = None,
):
    """构建标准化的股票右键菜单并执行

    参数:
        parent: 菜单的父 widget
        code: 股票代码
        name: 股票名称
        show_watchlist_toggle: 是否显示关注池切换选项
        show_export: 是否显示导出选项
        export_callback: 导出回调(仅 show_export=True 时有效)
        vcp_data: VCP 策略数据(加入关注池时附带)

    返回:
        None — 菜单在内部 exec 并处理所有动作
    """
    menu = QMenu(parent)
    # 每次创建菜单时动态获取当前主题的 QSS（而非模块加载时的快照）
    menu.setStyleSheet(generate_context_menu_qss())

    # --- 查看操作 ---
    act_chart = menu.addAction("📈 查看K线图")
    act_copy = menu.addAction("📋 复制代码")
    menu.addSeparator()

    # --- 关注池操作 ---
    act_watchlist = None
    act_pin_top = None
    act_move_bottom = None
    if show_watchlist_toggle:
        is_fav = watchlist_vm.is_in_watchlist(code)
        act_watchlist = menu.addAction("⭐ 移出关注池" if is_fav else "⭐ 加入关注池")
        if is_fav:
            act_pin_top = menu.addAction("🔝 置顶")
            act_move_bottom = menu.addAction("🔽 置底")
        menu.addSeparator()

    # --- 跳转操作 ---
    act_tdx = menu.addAction("🖥️ 跳转通达信")
    act_em = menu.addAction("🖥️ 跳转东方财富")
    act_gemini = menu.addAction("🤖 跳转 Gemini")

    # --- 导出 ---
    act_export = None
    if show_export:
        menu.addSeparator()
        act_export = menu.addAction("📤 导出当前表")

    # === 执行菜单 ===
    action = menu.exec(QCursor.pos())
    if action is None:
        return

    # === 分发动作 ===
    if action == act_chart:
        if isinstance(vcp_data, dict) and vcp_data:
            kline_item = dict(vcp_data)
            kline_item.setdefault('代码', code)
            kline_item.setdefault('名称', name)
            event_bus.sig_show_kline_with_list.emit(code, [kline_item], 0)
        else:
            event_bus.sig_show_kline.emit(code)

    elif action == act_copy:
        QApplication.clipboard().setText(code)

    elif action == act_watchlist and show_watchlist_toggle:
        # 清理名称中的星标前缀
        clean_name = name.replace("⭐ ", "")
        watchlist_vm.toggle_stock(code, clean_name, vcp_data)

    elif action == act_pin_top and act_pin_top is not None:
        watchlist_vm.pin_to_top(code)
        
    elif action == act_move_bottom and act_move_bottom is not None:
        watchlist_vm.move_to_bottom(code)

    elif action == act_tdx:
        # 通过基类方法跳转(parent 需要继承 BaseStockTab)
        if hasattr(parent, '_launch_tdx'):
            parent._launch_tdx(code)

    elif action == act_em:
        if hasattr(parent, '_launch_eastmoney'):
            parent._launch_eastmoney(code)

    elif action == act_gemini:
        url = "https://gemini.google.com/u/6/app?utm_source=app_launcher&utm_medium=owned&utm_campaign=base_all&pageId=none"
        try:
            import subprocess
            subprocess.Popen(['start', 'chrome', url], shell=True)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    elif action == act_export and show_export and export_callback:
        export_callback()
