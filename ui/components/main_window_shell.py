# -*- coding: utf-8 -*-
"""Shell UI helpers for MainWindowQT."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.components import PulsingDot
from ui.theme_tokens import build_ui_tokens


def _titlebar_shell_style(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    return f"""
        QWidget#customTitleBar {{
            background-color: {theme['BG_TITLEBAR']};
            border-bottom: 1px solid {theme['TITLEBAR_BORDER']};
        }}
        QLabel#titleBarBrand {{
            color: {theme['BRAND_PRIMARY']};
            font-size: {tokens['font']['size_md']}px;
            font-weight: {tokens['font']['weight_bold']};
            font-family: {tokens['font']['family']};
            background: transparent;
            padding-right: 6px;
        }}
        QFrame#titleBarSeparator {{
            color: {theme['BORDER_STRONG']};
        }}
    """


def _titlebar_button_style(theme: dict, color: str, hover_bg: str, *, font_size: int | None = None) -> str:
    tokens = build_ui_tokens(theme)
    font_size = font_size or tokens["font"]["size_sm"]
    return f"""
        QPushButton {{
            background: transparent;
            color: {color};
            border: none;
            font-size: {font_size}px;
            font-weight: bold;
            padding: 0 {tokens['space']['xl']}px;
            min-height: {tokens['shell']['titlebar_height']}px;
            max-height: {tokens['shell']['titlebar_height']}px;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
        }}
    """


def _system_button_style(theme: dict, text_color: str, hover_bg: str) -> str:
    tokens = build_ui_tokens(theme)
    return f"""
        QToolButton {{
            background: transparent;
            color: {text_color};
            border: none;
            font-size: {max(16, tokens['font']['size_md'])}px;
            font-family: "Segoe UI Emoji", "Segoe UI Symbol", "Microsoft YaHei UI";
            font-weight: {tokens['font']['weight_medium']};
            padding: 0;
            min-height: {tokens['shell']['titlebar_height']}px;
            max-height: {tokens['shell']['titlebar_height']}px;
        }}
        QToolButton:hover {{
            background-color: {hover_bg};
        }}
        QToolButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
    """


def _standalone_tabbar_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    tab_gap = 0
    tab_padding_x = max(7, tokens["control"]["tab_padding_x"] - 2)
    tab_radius = max(8, tokens["radius"]["lg"] - 2)
    return f"""
        QTabBar {{
            background: transparent;
            border: none;
        }}
        QTabBar::tab {{
            background: {theme['BG_BUTTON']};
            color: {theme['TAB_TEXT']};
            padding: {tokens['control']['tab_padding_y']}px {tab_padding_x}px;
            margin: 0 {tab_gap}px 0 0;
            border: 1px solid {theme['BORDER_DEFAULT']};
            border-top: 2px solid transparent;
            font-size: {tokens['font']['size_sm']}px;
            font-weight: {tokens['font']['weight_semibold']};
            min-height: {tokens['shell']['tabbar_height']}px;
            border-radius: {tab_radius}px;
            font-family: {tokens['font']['family']};
        }}
        QTabBar::tab:selected {{
            color: {theme['TEXT_PRIMARY']};
            background: {theme['BRAND_SUBTLE']};
            border-color: {theme['BORDER_BRAND']};
            border-top: 2px solid transparent;
        }}
        QTabBar::tab:hover:!selected {{
            color: {theme['TAB_TEXT_HOVER']};
            background: {theme['TAB_HOVER_BG']};
            border-color: {theme['BORDER_STRONG']};
        }}
    """


class DraggableTitleBar(QWidget):
    """空白区域可拖拽的标题栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                ratio = event.position().x() / win.width()
                win.showNormal()
                new_x = int(event.globalPosition().x() - win.width() * ratio)
                new_y = int(event.globalPosition().y() - self.height() // 2)
                win.move(new_x, new_y)
                self._drag_pos = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
            else:
                win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class MainWindowStatusBar(QFrame):
    """底部状态栏：统一维护指示灯、状态文本和时钟。"""

    def __init__(self, version_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBarWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        self.status_dot = PulsingDot(color="#10B981")
        layout.addWidget(self.status_dot)

        self.lbl_status = QLabel("---")
        self.lbl_status.setMinimumWidth(140)
        layout.addWidget(self.lbl_status)

        self.lbl_code_count = QLabel("标的池: 0")
        self.lbl_code_count.setMinimumWidth(96)
        layout.addWidget(self.lbl_code_count)

        layout.addStretch()

        self.lbl_clock = QLabel()
        self.lbl_clock.setMinimumWidth(76)
        layout.addWidget(self.lbl_clock)

        self.lbl_version = QLabel(version_text)
        layout.addWidget(self.lbl_version)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self.refresh_clock)
        self._clock_timer.start(1000)
        self.refresh_clock()

        self.apply_theme()

    def refresh_clock(self):
        import datetime

        self.lbl_clock.setText(datetime.datetime.now().strftime("%H:%M:%S"))

    def apply_theme(self):
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        tokens = build_ui_tokens(theme)
        neutral_bg = tokens["state"]["neutral"]["bg"]
        neutral_border = tokens["state"]["neutral"]["border"]
        pill_radius = tokens["radius"]["pill"]
        pill_padding = tokens["shell"]["status_pill_padding_x"]
        pill_height = tokens["shell"]["status_pill_min_height"]
        self.setFixedHeight(tokens["shell"]["status_height"])
        self.setStyleSheet(f"""
            QFrame#statusBarWidget {{
                background-color: {theme['BG_STATUSBAR']};
                border-top: 1px solid {theme['STATUSBAR_BORDER']};
            }}
        """)
        self.lbl_status.setStyleSheet(
            f"background-color: {neutral_bg}; color: {theme['TEXT_PRIMARY']};"
            f" border: 1px solid {neutral_border}; border-radius: {pill_radius}px;"
            f" padding: 0 {pill_padding}px; min-height: {pill_height}px;"
            f" font-size: {tokens['font']['size_sm']}px; font-weight: {tokens['font']['weight_semibold']};"
            f" font-family: {tokens['font']['mono_family']};"
        )
        self.lbl_code_count.setStyleSheet(
            f"background-color: {neutral_bg}; color: {theme['TEXT_SECONDARY']};"
            f" border: 1px solid {neutral_border}; border-radius: {pill_radius}px;"
            f" padding: 0 {pill_padding}px; min-height: {pill_height}px;"
            f" font-size: {tokens['font']['size_sm']}px; font-weight: {tokens['font']['weight_semibold']};"
        )
        self.lbl_clock.setStyleSheet(
            f"background-color: {neutral_bg}; color: {theme['TEXT_MUTED']};"
            f" border: 1px solid {neutral_border}; border-radius: {pill_radius}px;"
            f" padding: 0 {pill_padding}px; min-height: {pill_height}px;"
            f" font-size: {tokens['font']['size_sm']}px; font-family: {tokens['font']['mono_family']};"
        )
        self.lbl_version.setStyleSheet(
            f"color: {theme['TEXT_DISABLED']}; font-size: {tokens['font']['size_xs']}px;"
        )


@dataclass
class TitleBarRefs:
    titlebar: DraggableTitleBar
    layout: QHBoxLayout
    placeholder: QWidget
    btn_minimize: QPushButton
    btn_maximize: QPushButton
    btn_close: QPushButton


@dataclass
class SystemMenuRefs:
    button: QToolButton
    sys_menu: QMenu
    density_menu: QMenu
    theme_menu: QMenu


def setup_custom_titlebar(window, parent_layout: QVBoxLayout) -> TitleBarRefs:
    from ui.theme import theme_manager

    theme = theme_manager.current_theme
    tokens = build_ui_tokens(theme)
    titlebar = DraggableTitleBar()
    titlebar.setObjectName("customTitleBar")
    titlebar.setFixedHeight(tokens["shell"]["titlebar_height"])
    titlebar.setStyleSheet(_titlebar_shell_style(theme))

    titlebar_layout = QHBoxLayout(titlebar)
    titlebar_layout.setContentsMargins(16, 0, 0, 0)
    titlebar_layout.setSpacing(0)

    brand_label = QLabel("紫金研选")
    brand_label.setObjectName("titleBarBrand")
    titlebar_layout.addWidget(brand_label)

    sep = QFrame()
    sep.setObjectName("titleBarSeparator")
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(20)
    titlebar_layout.addWidget(sep)
    titlebar_layout.addSpacing(6)

    placeholder = QWidget()
    titlebar_layout.addWidget(placeholder)
    titlebar_layout.addStretch(1)

    btn_minimize = QPushButton("─")
    btn_minimize.setStyleSheet(
        _titlebar_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'], font_size=11)
    )
    btn_minimize.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_minimize.clicked.connect(window.showMinimized)

    btn_maximize = QPushButton("□")
    btn_maximize.setStyleSheet(
        _titlebar_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'])
    )
    btn_maximize.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_maximize.clicked.connect(window._toggle_maximize)

    btn_close = QPushButton("✕")
    btn_close.setStyleSheet(
        _titlebar_button_style(theme, theme['TEXT_MUTED'], "#C42B1C")
    )
    btn_close.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_close.clicked.connect(window.close)

    titlebar_layout.addWidget(btn_minimize)
    titlebar_layout.addWidget(btn_maximize)
    titlebar_layout.addWidget(btn_close)
    parent_layout.addWidget(titlebar, 0)

    return TitleBarRefs(
        titlebar=titlebar,
        layout=titlebar_layout,
        placeholder=placeholder,
        btn_minimize=btn_minimize,
        btn_maximize=btn_maximize,
        btn_close=btn_close,
    )


def inject_standalone_tabbar(window) -> QTabBar:
    from ui.theme import theme_manager

    window.tabs.tabBar().setVisible(False)

    standalone_bar = QTabBar()
    standalone_bar.setExpanding(False)
    standalone_bar.setDrawBase(False)
    standalone_bar.setUsesScrollButtons(True)
    standalone_bar.setElideMode(Qt.TextElideMode.ElideNone)
    standalone_bar.setStyleSheet(_standalone_tabbar_qss(theme_manager.current_theme))

    for i in range(window.tabs.count()):
        standalone_bar.addTab(window.tabs.tabText(i))
    standalone_bar.setCurrentIndex(window.tabs.currentIndex())

    window._syncing_tabs = False

    def on_bar_changed(index: int):
        if not window._syncing_tabs:
            window._syncing_tabs = True
            window.tabs.setCurrentIndex(index)
            window._syncing_tabs = False

    def on_tabs_changed(index: int):
        if not window._syncing_tabs:
            window._syncing_tabs = True
            standalone_bar.setCurrentIndex(index)
            window._syncing_tabs = False

    standalone_bar.currentChanged.connect(on_bar_changed)
    window.tabs.currentChanged.connect(on_tabs_changed)

    old = window._titlebar_tab_placeholder
    idx = window._titlebar_layout.indexOf(old)
    window._titlebar_layout.removeWidget(old)
    old.deleteLater()
    window._titlebar_layout.insertWidget(idx, standalone_bar)
    return standalone_bar


def setup_system_menu(window) -> SystemMenuRefs:
    from core.app_config import app_config
    from ui.theme import theme_manager

    btn_sys_menu = QToolButton()
    btn_sys_menu.setText("")
    btn_sys_menu.setObjectName("btnSysMenu")
    btn_sys_menu.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_sys_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    btn_sys_menu.setAutoRaise(False)
    tokens = build_ui_tokens(theme_manager.current_theme)
    btn_sys_menu.setFixedWidth(tokens["shell"]["system_button_width"])
    btn_sys_menu.setFixedHeight(tokens["shell"]["titlebar_height"])
    btn_sys_menu.setText("⚙️")
    btn_sys_menu.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    btn_sys_menu.setToolTip("系统菜单")
    btn_sys_menu.setAccessibleName("系统菜单")

    min_idx = window._titlebar_layout.indexOf(window._btn_minimize)
    window._titlebar_layout.insertWidget(min_idx, btn_sys_menu)

    sys_menu = QMenu(window)
    try:
        sys_menu.aboutToShow.connect(lambda: QApplication.restoreOverrideCursor())
        sys_menu.aboutToHide.connect(lambda: QApplication.restoreOverrideCursor())
    except Exception:
        pass

    sys_menu.setObjectName("sysMenu")

    window.act_f5 = sys_menu.addAction("全局数据同步 (F5)")
    window.act_f5.triggered.connect(window._action_refresh_f5)

    sys_menu.addSeparator()

    window.act_trade_calendar = sys_menu.addAction("交易日历")
    window.act_trade_calendar.triggered.connect(window._show_trade_calendar)

    sys_menu.addSeparator()

    window.act_network = sys_menu.addAction("网络状态：离线")
    window.act_network.triggered.connect(window._toggle_network)

    sys_menu.addSeparator()

    act_speed = sys_menu.addAction("重置行情连接")
    act_speed.triggered.connect(window._force_reconnect)

    sys_menu.addSeparator()

    density_menu = sys_menu.addMenu("表格密度")
    density_group = QActionGroup(window)
    density_group.setExclusive(True)

    window._act_density_compact = density_menu.addAction("紧凑")
    window._act_density_compact.setCheckable(True)
    density_group.addAction(window._act_density_compact)
    window._act_density_compact.triggered.connect(lambda: window._apply_table_density("紧凑"))

    window._act_density_comfort = density_menu.addAction("舒适")
    window._act_density_comfort.setCheckable(True)
    density_group.addAction(window._act_density_comfort)
    window._act_density_comfort.triggered.connect(lambda: window._apply_table_density("舒适"))

    window._density_menu = density_menu
    window._apply_table_density(app_config.table_density, persist=False)

    sys_menu.addSeparator()

    theme_menu = sys_menu.addMenu(f"界面主题：{theme_manager.current_theme_name}")
    for theme_name in theme_manager.theme_names():
        act = theme_menu.addAction(theme_name)
        act.triggered.connect(lambda checked, n=theme_name: theme_manager.switch_theme(n))

    theme_menu.addSeparator()
    window._act_auto_theme = theme_menu.addAction("日夜自动切换 (7:00-18:00)")
    window._act_auto_theme.setCheckable(True)
    window._act_auto_theme.setChecked(theme_manager.is_auto_switch())
    window._act_auto_theme.triggered.connect(lambda checked: theme_manager.set_auto_switch(checked))

    window._sys_menu = sys_menu
    window._theme_menu = theme_menu
    window.btn_sys_menu = btn_sys_menu
    btn_sys_menu.setMenu(sys_menu)

    for obj in (btn_sys_menu, sys_menu, density_menu, theme_menu):
        obj.installEventFilter(window)

    refresh_system_menu_theme(window)
    return SystemMenuRefs(
        button=btn_sys_menu,
        sys_menu=sys_menu,
        density_menu=density_menu,
        theme_menu=theme_menu,
    )


def refresh_system_menu_theme(window) -> None:
    from ui.styles.context_menu_qss import generate_context_menu_qss
    from ui.theme import theme_manager

    theme = theme_manager.current_theme

    if hasattr(window, "btn_sys_menu") and window.btn_sys_menu:
        window.btn_sys_menu.setStyleSheet(
            _system_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'])
        )
        window.btn_sys_menu.setText("⚙️")
        window.btn_sys_menu.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

    menu_qss = generate_context_menu_qss(theme)
    for attr_name in ("_sys_menu", "_density_menu", "_theme_menu"):
        menu = getattr(window, attr_name, None)
        if menu:
            menu.setStyleSheet(menu_qss)
            menu.setCursor(Qt.CursorShape.PointingHandCursor)

    theme_menu = getattr(window, "_theme_menu", None)
    if theme_menu:
        theme_menu.setTitle(f"界面主题：{theme_manager.current_theme_name}")


def apply_chrome_theme(window) -> None:
    from ui.theme import theme_manager

    theme = theme_manager.current_theme

    if hasattr(window, "_custom_titlebar") and window._custom_titlebar:
        window._custom_titlebar.setStyleSheet(_titlebar_shell_style(theme))

    if hasattr(window, "_btn_minimize") and window._btn_minimize:
        window._btn_minimize.setStyleSheet(
            _titlebar_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'], font_size=11)
        )
        window._btn_minimize.setFixedWidth(build_ui_tokens(theme)["shell"]["window_button_width"])
    if hasattr(window, "_btn_maximize") and window._btn_maximize:
        window._btn_maximize.setStyleSheet(
            _titlebar_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'])
        )
        window._btn_maximize.setFixedWidth(build_ui_tokens(theme)["shell"]["window_button_width"])
    if hasattr(window, "_btn_close") and window._btn_close:
        window._btn_close.setStyleSheet(
            _titlebar_button_style(theme, theme['TEXT_MUTED'], "#C42B1C")
        )
        window._btn_close.setFixedWidth(build_ui_tokens(theme)["shell"]["window_button_width"])

    if hasattr(window, "_standalone_tabbar") and window._standalone_tabbar:
        window._standalone_tabbar.setStyleSheet(_standalone_tabbar_qss(theme))

    if hasattr(window, "_custom_titlebar") and window._custom_titlebar:
        window._custom_titlebar.setFixedHeight(build_ui_tokens(theme)["shell"]["titlebar_height"])

    if hasattr(window, "btn_sys_menu") and window.btn_sys_menu:
        window.btn_sys_menu.setFixedWidth(build_ui_tokens(theme)["shell"]["system_button_width"])
        window.btn_sys_menu.setFixedHeight(build_ui_tokens(theme)["shell"]["titlebar_height"])

    if hasattr(window, "tabs_wrapper") and window.tabs_wrapper:
        window.tabs_wrapper.setStyleSheet(f"""
            QFrame#tabsWrapperFrame {{
                background-color: {theme['BG_GLASS']};
                border: none;
            }}
        """)

    refresh_system_menu_theme(window)
