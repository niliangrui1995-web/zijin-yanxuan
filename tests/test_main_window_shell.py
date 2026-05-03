# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from ui.components.main_window_shell import (
    MainWindowStatusBar,
    ShellNavigationWidget,
    apply_chrome_theme,
    inject_standalone_tabbar,
    setup_custom_titlebar,
    setup_system_menu,
)
from ui.window_flags import (
    GWL_STYLE,
    SWP_FRAMECHANGED,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_NOZORDER,
    WS_CAPTION,
    WS_MAXIMIZEBOX,
    WS_MINIMIZEBOX,
    WS_SYSMENU,
    apply_windows_frameless_taskbar_fix,
    build_frameless_main_window_flags,
)
from vcp.constants import APP_VERSION


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
        self._market_pulse_strip = refs.pulse_strip
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


def test_app_version_is_v8_for_shell_surfaces():
    assert APP_VERSION == "8.0.0"


def test_main_window_shell_builders_wire_titlebar_menu_and_tabs():
    window = DummyShellWindow()
    try:
        assert window._standalone_tabbar.count() == 2
        assert window.tabs.tabBar().isVisible() is False
        assert window.btn_sys_menu.menu() is window._sys_menu
        assert window.btn_sys_menu.toolTip() == "系统菜单"
        assert window.btn_sys_menu.text() == "⚙️"
        assert window._standalone_tabbar.toolTip() == ""
        assert window._standalone_tabbar.usesScrollButtons() is True
        assert window._standalone_tabbar.elideMode() == Qt.TextElideMode.ElideRight
        assert window._market_pulse_strip.height() == 3
        assert window._theme_menu.title().startswith("界面主题：")
        assert window.last_density is not None
        assert window.last_density[1] is False
        tabbar_style = window._standalone_tabbar.styleSheet()
        assert "max-width:" in tabbar_style
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
        assert window._btn_minimize.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert window._btn_maximize.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert window._btn_close.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert window._btn_close.text() == "✕"
    finally:
        window.deleteLater()


class DummyGroupedWorkspace:
    @staticmethod
    def tab_indices_by_group():
        return {
            "主工作台": [0, 1],
            "情报源": [2, 3],
        }


def test_shell_navigation_widget_restores_last_subtab_per_group():
    tabs = QTabWidget()
    nav = ShellNavigationWidget()
    try:
        tabs.addTab(QWidget(), "扫描")
        tabs.addTab(QWidget(), "观察")
        tabs.addTab(QWidget(), "北美")
        tabs.addTab(QWidget(), "亚洲")

        nav.bind_workspace(DummyGroupedWorkspace(), tabs)

        tabs.setCurrentIndex(1)
        assert tabs.currentIndex() == 1

        nav._switch_group("情报源")
        assert tabs.currentIndex() == 2

        tabs.setCurrentIndex(3)
        assert tabs.currentIndex() == 3

        nav._switch_group("主工作台")
        assert tabs.currentIndex() == 1

        nav._switch_group("情报源")
        assert tabs.currentIndex() == 3
    finally:
        nav.deleteLater()
        tabs.deleteLater()


def test_shell_navigation_widget_does_not_rebuild_tabbar_inside_same_group():
    tabs = QTabWidget()
    nav = ShellNavigationWidget()
    try:
        tabs.addTab(QWidget(), "Scan")
        tabs.addTab(QWidget(), "Watch")
        tabs.addTab(QWidget(), "NA")
        tabs.addTab(QWidget(), "Asia")

        nav.bind_workspace(DummyGroupedWorkspace(), tabs)
        first_group, second_group = list(DummyGroupedWorkspace.tab_indices_by_group())
        initial_rebuild_count = nav._tabbar_rebuild_count

        tabs.setCurrentIndex(1)

        assert tabs.currentIndex() == 1
        assert nav.tabbar.currentIndex() == 1
        assert nav._tabbar_rebuild_count == initial_rebuild_count

        nav._switch_group(second_group)

        assert nav._tabbar_rebuild_count == initial_rebuild_count + 1
        assert nav.tabbar.count() == 2
    finally:
        nav.deleteLater()
        tabs.deleteLater()


def test_build_frameless_main_window_flags_preserves_native_window_controls():
    flags = build_frameless_main_window_flags()

    assert flags & Qt.WindowType.Window
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not flags & Qt.WindowType.CustomizeWindowHint
    assert not flags & Qt.WindowType.WindowSystemMenuHint


class DummyNativeWindow:
    @staticmethod
    def winId():
        return 9527


class DummyUser32:
    def __init__(self, style):
        self.style = style
        self.set_window_long_calls = []
        self.set_window_pos_calls = []

    def GetWindowLongPtrW(self, hwnd, index):
        assert hwnd == 9527
        assert index == GWL_STYLE
        return self.style

    def SetWindowLongPtrW(self, hwnd, index, style):
        self.set_window_long_calls.append((hwnd, index, style))
        self.style = style
        return style

    def SetWindowPos(self, hwnd, insert_after, x, y, cx, cy, flags):
        self.set_window_pos_calls.append((hwnd, insert_after, x, y, cx, cy, flags))
        return 1


def test_apply_windows_frameless_taskbar_fix_updates_native_styles(monkeypatch):
    monkeypatch.setattr("ui.window_flags.os.name", "nt")
    user32 = DummyUser32(WS_CAPTION)

    changed = apply_windows_frameless_taskbar_fix(DummyNativeWindow(), user32=user32)

    assert changed is True
    assert user32.style & WS_SYSMENU
    assert user32.style & WS_MINIMIZEBOX
    assert user32.style & WS_MAXIMIZEBOX
    assert not user32.style & WS_CAPTION
    assert user32.set_window_long_calls == [(9527, GWL_STYLE, user32.style)]
    assert user32.set_window_pos_calls == [
        (
            9527,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    ]
