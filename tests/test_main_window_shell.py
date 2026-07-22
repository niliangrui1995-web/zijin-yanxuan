# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QMainWindow, QSizePolicy, QTabWidget, QVBoxLayout, QWidget

from app.services.runtime_constants import APP_VERSION
from ui.components.main_window_shell import (
    MainWindowStatusBar,
    ShellNavigationWidget,
    StatusFlowStrip,
    apply_chrome_theme,
    inject_standalone_tabbar,
    setup_custom_titlebar,
    setup_system_menu,
)
from ui.components.table_controls import StatusGlyph
from ui.window_flags import (
    DWMNCRP_ENABLED,
    DWMSBT_MAINWINDOW,
    DWMWA_NCRENDERING_POLICY,
    DWMWA_SYSTEMBACKDROP_TYPE,
    DWMWA_USE_IMMERSIVE_DARK_MODE,
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
    enable_windows_native_shadow,
    enable_windows_system_backdrop,
)


class DummyShellWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.last_density = None
        self.launch_at_login_enabled = False
        self.trade_calendar_open_count = 0

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
        self.trade_calendar_open_count += 1

    def _toggle_network(self):
        pass

    def _force_reconnect(self):
        pass

    def _open_runtime_health(self):
        pass

    def _is_launch_at_login_supported(self):
        return True

    def _is_launch_at_login_enabled(self):
        return self.launch_at_login_enabled

    def _toggle_launch_at_login(self, checked):
        self.launch_at_login_enabled = bool(checked)

    def _apply_table_density(self, density, persist=True):
        self.last_density = (density, persist)


def test_main_window_status_bar_applies_theme(qt_application):
    bar = MainWindowStatusBar("vtest")
    try:
        assert bar.lbl_version.text() == "vtest"
        assert ":" in bar.lbl_clock.text()
        assert "statusBarWidget" in bar.styleSheet()
        assert "color:" in bar.lbl_status.styleSheet()
        assert isinstance(bar.status_flow, StatusFlowStrip)
        assert isinstance(bar.status_dot, StatusGlyph)
        assert bar.status_flow.objectName() == "statusFlowStrip"
        assert bar.status_flow.height() == 2
        assert bar._clock_timer.isActive() is False
        assert bar.status_flow._timer.isActive() is False
        bar.show_sync_feedback("working")
        assert bar.status_flow._mode == "working"
        assert bar.status_flow._timer.isActive() is False
        bar.show()
        qt_application.processEvents()
        assert bar._clock_timer.isActive() is True
        assert bar.status_flow._timer.isActive() is True
        bar.show_sync_feedback("cache")
        assert bar.status_flow._mode == "cache"
        bar.show_sync_feedback("idle")
        assert bar.status_flow._mode == "neutral"
        assert bar.status_flow._timer.isActive() is False
        bar.set_status_tone("busy")
        assert bar.status_flow._mode == "working"
        assert bar.status_flow._timer.isActive() is True
        bar.set_status_tone("online", animate=False)
        assert bar.status_flow._mode == "success"
        assert bar.status_flow._timer.isActive() is False
    finally:
        bar.deleteLater()


def test_app_version_is_v188_for_shell_surfaces():
    assert APP_VERSION == "1.8.8"


def test_main_window_shell_builders_wire_titlebar_menu_and_tabs(qt_application):
    window = DummyShellWindow()
    try:
        assert window._standalone_tabbar.count() == 2
        assert not window._standalone_tabbar.tabIcon(0).isNull()
        assert not window._standalone_tabbar.tabIcon(1).isNull()
        assert window.tabs.tabBar().isVisible() is False
        assert window.btn_sys_menu.menu() is window._sys_menu
        assert window.btn_sys_menu.toolTip() == "系统菜单"
        assert window.btn_sys_menu.text() == ""
        assert not window.btn_sys_menu.icon().isNull()
        assert window.btn_sys_menu.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert window._standalone_tabbar.toolTip() == ""
        assert window._standalone_tabbar.expanding() is False
        assert window._standalone_tabbar.usesScrollButtons() is True
        assert window._standalone_tabbar.elideMode() == Qt.TextElideMode.ElideNone
        assert window._standalone_tabbar.minimumWidth() >= 420
        assert window._standalone_tabbar.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        nav_idx = window._titlebar_layout.indexOf(window._titlebar_nav_host)
        assert window._titlebar_layout.stretch(nav_idx) >= 20
        assert window._titlebar_sync_widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Maximum
        assert window._titlebar_sync_widget.btn_trade_calendar.text() == "交易日历"
        assert window._titlebar_sync_widget.btn_trade_calendar.accessibleName() == "交易日历"
        assert "outline: none;" in window._titlebar_sync_widget.btn_trade_calendar.styleSheet()
        assert window._titlebar_sync_widget.quote_pulse_dot.toolTip() == "quotes 同步心跳"
        assert window._titlebar_sync_widget.lbl_quote.text() == "行情 --:--:--"
        window._titlebar_sync_widget.set_quote_status(
            {
                "000001": {
                    "quote_time": "2026-07-22T10:24:06+08:00",
                    "quote_freshness": "network",
                },
                "000002": {
                    "quote_time": "2026-07-22T10:23:36+08:00",
                    "quote_freshness": "cache",
                },
                "000003": {
                    "quote_time": "2026-07-21",
                    "quote_freshness": "stale",
                },
                "000004": {"market_cap": 1_000_000_000},
            }
        )
        assert window._titlebar_sync_widget.lbl_quote.text().startswith("行情 10:24:06")
        assert "N/C/S 1/1/1" in window._titlebar_sync_widget.lbl_quote.text()
        assert "network 1 / cache 1 / stale 1" in window._titlebar_sync_widget.lbl_quote.toolTip()
        quote_status = window._titlebar_sync_widget.lbl_quote.text()
        window._titlebar_sync_widget.set_quote_status({"000004": {"market_cap": 1_000_000_000}})
        assert window._titlebar_sync_widget.lbl_quote.text() == quote_status
        window._titlebar_sync_widget.pulse_quotes()
        assert window._titlebar_sync_widget.quote_pulse_dot._timer.isActive() is False
        window.show()
        qt_application.processEvents()
        assert window._titlebar_sync_widget.quote_pulse_dot._timer.isActive() is True
        window._titlebar_sync_widget.btn_trade_calendar.click()
        assert window.trade_calendar_open_count == 1
        menu_action_texts = [action.text() for action in window._sys_menu.actions()]
        assert "交易日历" not in menu_action_texts
        assert "运行时健康" in menu_action_texts
        assert window._titlebar_sync_widget.lbl_meta.isHidden() is False
        assert window._titlebar_sync_widget.lbl_meta.minimumWidth() >= 220
        assert window._titlebar_sync_widget.lbl_meta.maximumWidth() >= 420
        window._titlebar_sync_widget.set_state("cache", "cache loaded", "snapshot 20260430")
        assert window._titlebar_sync_widget.lbl_meta.text() == "cache loaded｜snapshot 20260430"
        assert window._titlebar_sync_widget.lbl_state.toolTip() == window._titlebar_sync_widget.lbl_meta.text()
        assert window._market_pulse_strip.height() == 3
        assert window._theme_menu.title().startswith("界面主题：")
        assert not hasattr(window, "_workspace_mode_menu")
        assert not hasattr(window, "_act_workspace_classic")
        assert not hasattr(window, "_act_workspace_research")
        assert "工作区模式" not in [action.text() for action in window._sys_menu.actions()]
        assert window.last_density is None
        assert window._act_density_compact.isChecked() != window._act_density_comfort.isChecked()
        tabbar_style = window._standalone_tabbar.styleSheet()
        assert "max-width: 104px;" not in tabbar_style
        assert "max-width: 132px;" not in tabbar_style
        assert "min-width: 44px;" in tabbar_style
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
        assert window._btn_minimize.text() == ""
        assert window._btn_maximize.text() == ""
        assert window._btn_close.text() == ""
        assert not window._btn_minimize.icon().isNull()
        assert not window._btn_maximize.icon().isNull()
        assert not window._btn_close.icon().isNull()
    finally:
        window.deleteLater()


class DummyGroupedWorkspace:
    @staticmethod
    def tab_indices_by_group():
        return {
            "主工作台": [0, 1],
            "情报源": [2, 3],
        }


class ActivatingGroupedWorkspace(DummyGroupedWorkspace):
    def __init__(self, tabs):
        self.tabs = tabs
        self.calls = []

    def activate_tab(self, tab_index: int, *, reason: str = "user") -> bool:
        self.calls.append((tab_index, reason))
        self.tabs.setCurrentIndex(tab_index)
        return True


class RebuildAwareWorkspace(DummyGroupedWorkspace):
    def __init__(self):
        self.prepared = []

    def prepare_shell_group_rebuild_navigation(self, *, interval_ms: int = 0) -> None:
        self.prepared.append(interval_ms)


class TransitionAwareTabs(QTabWidget):
    def __init__(self):
        super().__init__()
        self.suspended = []

    def suspendTransitionsFor(self, interval_ms: int) -> None:  # noqa: N802 - Qt API naming
        self.suspended.append(interval_ms)


def test_shell_navigation_widget_restores_last_subtab_per_group():
    tabs = QTabWidget()
    nav = ShellNavigationWidget()
    try:
        tabs.addTab(QWidget(), "扫描")
        tabs.addTab(QWidget(), "观察")
        tabs.addTab(QWidget(), "北美")
        tabs.addTab(QWidget(), "亚洲")

        nav.bind_workspace(DummyGroupedWorkspace(), tabs)
        assert all("outline: none;" in button.styleSheet() for button in nav._group_buttons.values())

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


def test_shell_navigation_widget_quiets_workspace_during_group_rebuild():
    tabs = TransitionAwareTabs()
    nav = ShellNavigationWidget()
    workspace = RebuildAwareWorkspace()
    try:
        tabs.addTab(QWidget(), "Scan")
        tabs.addTab(QWidget(), "Watch")
        tabs.addTab(QWidget(), "NA")
        tabs.addTab(QWidget(), "Asia")

        nav.bind_workspace(workspace, tabs)
        _, second_group = list(DummyGroupedWorkspace.tab_indices_by_group())
        nav._switch_group(second_group)

        assert tabs.suspended[-1] == ShellNavigationWidget.GROUP_REBUILD_TRANSITION_SUSPEND_MS
        assert workspace.prepared[-1] == ShellNavigationWidget.GROUP_REBUILD_TRANSITION_SUSPEND_MS
        assert tabs.currentIndex() == 2
    finally:
        nav.deleteLater()
        tabs.deleteLater()


def test_shell_navigation_widget_marks_workspace_activation_reason():
    tabs = QTabWidget()
    nav = ShellNavigationWidget()
    workspace = ActivatingGroupedWorkspace(tabs)
    try:
        tabs.addTab(QWidget(), "Scan")
        tabs.addTab(QWidget(), "Watch")
        tabs.addTab(QWidget(), "NA")
        tabs.addTab(QWidget(), "Asia")

        nav.bind_workspace(workspace, tabs)
        _, second_group = list(DummyGroupedWorkspace.tab_indices_by_group())
        nav._switch_group(second_group)

        assert workspace.calls[-1] == (2, "shell_nav")

        nav.tabbar.setCurrentIndex(1)

        assert workspace.calls[-1] == (3, "shell_nav")
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


class DummyDwmApi:
    def __init__(self):
        self.extend_calls = []
        self.attribute_calls = []

    def DwmExtendFrameIntoClientArea(self, hwnd, margins):
        self.extend_calls.append((hwnd, margins))
        return 0

    def DwmSetWindowAttribute(self, hwnd, attr, value, size):
        self.attribute_calls.append((hwnd, attr, value, size))
        return 0


def test_enable_windows_native_shadow_sets_dwm_attributes_once(monkeypatch):
    monkeypatch.setattr("ui.window_flags.os.name", "nt")
    dwmapi = DummyDwmApi()

    changed = enable_windows_native_shadow(DummyNativeWindow(), dwmapi=dwmapi)

    assert changed is True
    assert len(dwmapi.extend_calls) == 1
    assert dwmapi.extend_calls[0][0] == 9527
    assert len(dwmapi.attribute_calls) == 1
    hwnd, attr, value, size = dwmapi.attribute_calls[0]
    assert hwnd == 9527
    assert attr == DWMWA_NCRENDERING_POLICY
    assert value._obj.value == DWMNCRP_ENABLED
    assert size > 0


def test_enable_windows_native_shadow_fails_silently(monkeypatch):
    monkeypatch.setattr("ui.window_flags.os.name", "nt")

    changed = enable_windows_native_shadow(DummyNativeWindow(), dwmapi=object())

    assert changed is False


def test_enable_windows_system_backdrop_sets_mica_and_dark_mode(monkeypatch):
    monkeypatch.setattr("ui.window_flags.os.name", "nt")
    dwmapi = DummyDwmApi()

    changed = enable_windows_system_backdrop(DummyNativeWindow(), dwmapi=dwmapi, backdrop="mica", dark=True)

    assert changed is True
    assert len(dwmapi.attribute_calls) == 2
    _, dark_attr, dark_value, _ = dwmapi.attribute_calls[0]
    _, backdrop_attr, backdrop_value, _ = dwmapi.attribute_calls[1]
    assert dark_attr == DWMWA_USE_IMMERSIVE_DARK_MODE
    assert dark_value._obj.value == 1
    assert backdrop_attr == DWMWA_SYSTEMBACKDROP_TYPE
    assert backdrop_value._obj.value == DWMSBT_MAINWINDOW
