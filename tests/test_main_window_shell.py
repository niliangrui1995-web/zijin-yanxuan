# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QFrame, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from ui.components.main_window_shell import (
    MainWindowStatusBar,
    apply_chrome_theme,
    inject_standalone_tabbar,
    setup_custom_titlebar,
    setup_system_menu,
)


class DummyShellWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.last_density = None

        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(central)

        refs = setup_custom_titlebar(self, root_layout)
        self._custom_titlebar = refs.titlebar
        self._titlebar_layout = refs.layout
        self._titlebar_tab_placeholder = refs.placeholder
        self._btn_minimize = refs.btn_minimize
        self._btn_maximize = refs.btn_maximize
        self._btn_close = refs.btn_close

        self.tabs_wrapper = QFrame(self)
        self.tabs_wrapper.setObjectName("tabsWrapperFrame")
        tabs_layout = QVBoxLayout(self.tabs_wrapper)
        tabs_layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "扫描")
        self.tabs.addTab(QWidget(), "观察")
        tabs_layout.addWidget(self.tabs)
        root_layout.addWidget(self.tabs_wrapper, 1)

        self._standalone_tabbar = inject_standalone_tabbar(self)
        setup_system_menu(self)

    def _toggle_maximize(self):
        pass

    def _action_refresh_f5(self):
        pass

    def _show_trade_calendar(self):
        pass

    def _toggle_network(self):
        pass

    def _force_reconnect(self):
        pass

    def _apply_table_density(self, density, persist=True):
        self.last_density = (density, persist)


def test_main_window_status_bar_applies_theme():
    bar = MainWindowStatusBar("vtest")
    try:
        assert bar.lbl_version.text() == "vtest"
        assert ":" in bar.lbl_clock.text()
        assert "statusBarWidget" in bar.styleSheet()
        assert "color:" in bar.lbl_status.styleSheet()
    finally:
        bar.deleteLater()


def test_main_window_shell_builders_wire_titlebar_menu_and_tabs():
    window = DummyShellWindow()
    try:
        assert window._standalone_tabbar.count() == 2
        assert window.tabs.tabBar().isVisible() is False
        assert window.btn_sys_menu.menu() is window._sys_menu
        assert window.btn_sys_menu.toolTip() == "系统菜单"
        assert window.btn_sys_menu.text() == "⚙️"
        assert window._standalone_tabbar.toolTip() == ""
        assert window._theme_menu.title().startswith("界面主题：")
        assert window.last_density is not None
        assert window.last_density[1] is False
        tabbar_style = window._standalone_tabbar.styleSheet()
        assert "margin: 0 0px 0 0;" in tabbar_style
        assert "QTabBar::tab:selected" in tabbar_style
        assert "border-top: 2px solid transparent;" in tabbar_style

        window.tabs.setCurrentIndex(1)
        assert window._standalone_tabbar.currentIndex() == 1

        window._standalone_tabbar.setCurrentIndex(0)
        assert window.tabs.currentIndex() == 0

        apply_chrome_theme(window)
        assert "customTitleBar" in window._custom_titlebar.styleSheet()
        assert "titleBarSeparator" in window._custom_titlebar.styleSheet()
        assert "tabsWrapperFrame" in window.tabs_wrapper.styleSheet()
        assert window._btn_close.text() == "✕"
    finally:
        window.deleteLater()
