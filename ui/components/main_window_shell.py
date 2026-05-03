# -*- coding: utf-8 -*-
"""Shell UI helpers for MainWindowQT."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
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

from core.logger import get_logger
from ui.components import PulsingDot
from ui.components.shared_title_bar import DraggableTitleBar
from ui.theme_tokens import build_ui_tokens, get_state_tone


log = get_logger(__name__)


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
    tab_gap = max(3, tokens["shell"]["toolbar_group_gap"])
    tab_padding_x = max(12, tokens["control"]["tab_padding_x"] + 2)
    tab_radius = max(8, tokens["radius"]["md"])
    surface = tokens["surface"]
    border = tokens["border"]
    return f"""
        QTabBar {{
            background: transparent;
            border: none;
        }}
        QTabBar::tab {{
            background: {surface['toolbar_chip']};
            color: {theme['TAB_TEXT']};
            padding: {tokens['control']['tab_padding_y']}px {tab_padding_x}px;
            margin: 0 {tab_gap}px 0 0;
            border: 1px solid {border['subtle']};
            border-top: 2px solid transparent;
            font-size: {tokens['font']['size_sm']}px;
            font-weight: {tokens['font']['weight_semibold']};
            min-height: {tokens['shell']['tabbar_height']}px;
            min-width: 0px;
            border-radius: {tab_radius}px;
            font-family: {tokens['font']['family']};
        }}
        QTabBar::tab:selected {{
            color: {theme.get('TAB_ACTIVE_TEXT', theme['TEXT_PRIMARY'])};
            background: {theme.get('TAB_ACTIVE_BG', theme['BRAND_SUBTLE'])};
            border-color: {theme.get('TAB_ACTIVE_BORDER', theme['BORDER_BRAND'])};
            border-top: 2px solid {theme.get('TAB_ACTIVE_TOP', 'transparent')};
        }}
        QTabBar::tab:hover:!selected {{
            color: {theme['TAB_TEXT_HOVER']};
            background: {theme['TAB_HOVER_BG']};
            border-color: {border['strong']};
        }}
    """


def _nav_group_button_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    surface = tokens["surface"]
    border = tokens["border"]
    return f"""
        QPushButton {{
            background: {surface['toolbar_chip']};
            color: {theme['TEXT_SECONDARY']};
            border: 1px solid {border['subtle']};
            border-radius: {tokens['radius']['pill']}px;
            padding: 0 {tokens['space']['lg']}px;
            min-height: {tokens['control']['segment_height']}px;
            font-size: {tokens['font']['size_sm']}px;
            font-weight: {tokens['font']['weight_semibold']};
        }}
        QPushButton:hover {{
            color: {theme['TEXT_PRIMARY']};
            border-color: {border['strong']};
            background: {theme['BG_HOVER']};
        }}
        QPushButton:checked {{
            color: {theme.get('TAB_ACTIVE_TEXT', theme['TEXT_PRIMARY'])};
            border-color: {theme.get('TAB_ACTIVE_BORDER', theme['BORDER_BRAND'])};
            background: {theme.get('TAB_ACTIVE_BG', theme['BRAND_SUBTLE'])};
        }}
    """


def _titlebar_sync_button_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    return f"""
        QPushButton {{
            background: {theme['BRAND_PRIMARY']};
            color: {theme['TEXT_ON_ACCENT']};
            border: 1px solid {theme['BRAND_DEEP']};
            border-radius: {tokens['radius']['pill']}px;
            padding: 0 {tokens['space']['xl']}px;
            min-height: {tokens['control']['toolbar_button_height']}px;
            font-size: {tokens['font']['size_sm']}px;
            font-weight: {tokens['font']['weight_bold']};
        }}
        QPushButton:hover {{
            background: {theme['BRAND_DEEP']};
        }}
        QPushButton:pressed {{
            background: {theme['BRAND_PRIMARY']};
        }}
    """


class MainWindowStatusBar(QFrame):
    """底部状态栏：统一维护指示灯、状态文本和时钟。"""

    def __init__(self, version_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBarWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(34)
        self._status_tone = "offline"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        from ui.theme import theme_manager

        default_dot = theme_manager.current_theme.get(
            "NETWORK_OFFLINE",
            theme_manager.current_theme.get("COLOR_ERROR", "#EF4444"),
        )
        self.status_dot = PulsingDot(color=default_dot)
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

    def _resolve_status_dot_color(self, tone: str) -> str:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        mapping = {
            "online": theme.get("NETWORK_ONLINE", theme.get("COLOR_REALTIME", theme.get("COLOR_SUCCESS", "#10B981"))),
            "busy": theme.get("NETWORK_BUSY", theme.get("COLOR_WARNING", "#F59E0B")),
            "offline": theme.get("NETWORK_OFFLINE", theme.get("COLOR_ERROR", "#EF4444")),
        }
        return mapping.get(tone, mapping["offline"])

    def set_status_tone(self, tone: str) -> None:
        self._status_tone = tone if tone in {"online", "busy", "offline"} else "offline"
        self.status_dot.set_color(self._resolve_status_dot_color(self._status_tone))

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
        self.set_status_tone(self._status_tone)


class ShellNavigationWidget(QWidget):
    """标题栏一级导航 + 二级标签导航。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs = None
        self._workspace = None
        self._syncing = False
        self._group_to_indices: dict[str, list[int]] = {}
        self._last_index_by_group: dict[str, int] = {}
        self._visible_indices: list[int] = []
        self._current_group = ""
        self._tabbar_rebuild_count = 0
        self._slow_switch_threshold_ms = 12.0
        self._group_buttons: dict[str, QPushButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._group_button_group = QButtonGroup(self)
        self._group_button_group.setExclusive(True)

        self.group_wrap = QWidget(self)
        self.group_layout = QHBoxLayout(self.group_wrap)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.setSpacing(6)
        layout.addWidget(self.group_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.tabbar = QTabBar(self)
        self.tabbar.setExpanding(True)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(False)
        self.tabbar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabbar.setAccessibleName("二级页面导航")
        self.tabbar.currentChanged.connect(self._on_tabbar_changed)
        layout.addWidget(self.tabbar, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme()

    def bind_workspace(self, workspace, tabs) -> None:
        if self._tabs is not None:
            try:
                self._tabs.currentChanged.disconnect(self._on_tabs_changed)
            except (TypeError, RuntimeError):
                pass

        self._workspace = workspace
        self._tabs = tabs
        self._group_to_indices = {}
        self._current_group = ""
        self._visible_indices = []

        if workspace is not None and hasattr(workspace, "tab_indices_by_group"):
            self._group_to_indices = workspace.tab_indices_by_group()
        elif tabs is not None:
            self._group_to_indices = {"全部": list(range(tabs.count()))}

        self._last_index_by_group = {
            group: index
            for group, index in self._last_index_by_group.items()
            if group in self._group_to_indices
        }

        self._rebuild_group_buttons()

        if self._tabs is not None:
            self._tabs.currentChanged.connect(self._on_tabs_changed)
            self.sync_from_current_tab(self._tabs.currentIndex())

    def _clear_group_buttons(self) -> None:
        while self.group_layout.count():
            item = self.group_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._group_button_group.removeButton(widget)
                widget.deleteLater()
        self._group_buttons.clear()

    def _rebuild_group_buttons(self) -> None:
        self._clear_group_buttons()

        groups = [group for group, indices in self._group_to_indices.items() if indices]
        for group in groups:
            button = QPushButton(group, self.group_wrap)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"{group}一级导航")
            button.clicked.connect(lambda checked=False, g=group: self._switch_group(g))
            button.setStyleSheet(_nav_group_button_qss(build_ui_tokens()["theme"]))
            self._group_button_group.addButton(button)
            self._group_buttons[group] = button
            self.group_layout.addWidget(button)

        self.group_layout.addStretch(1)
        self.group_wrap.setVisible(bool(groups))

    def _switch_group(self, group: str, preferred_index: int | None = None) -> None:
        started_at = time.perf_counter()
        indices = list(self._group_to_indices.get(group, []))
        if not indices or self._tabs is None:
            return

        current_index = self._tabs.currentIndex()
        remembered_index = self._last_index_by_group.get(group)
        target_index = preferred_index if preferred_index in indices else remembered_index
        if target_index not in indices:
            target_index = current_index if current_index in indices else indices[0]

        needs_rebuild = (
            group != self._current_group
            or self._visible_indices != indices
            or not self._tabbar_matches_indices(indices)
        )
        self._remember_group_index(group, target_index)

        self._syncing = True
        previous_signal_state = self.tabbar.blockSignals(True)
        updates_disabled = False
        try:
            if needs_rebuild:
                self.tabbar.setUpdatesEnabled(False)
                updates_disabled = True
                while self.tabbar.count() > 0:
                    self.tabbar.removeTab(self.tabbar.count() - 1)
                for global_index in indices:
                    self.tabbar.addTab(self._tabs.tabText(global_index))
                self._tabbar_rebuild_count += 1

            visible_target = indices.index(target_index)
            if self.tabbar.currentIndex() != visible_target:
                self.tabbar.setCurrentIndex(visible_target)
            if self._tabs.currentIndex() != target_index:
                self._tabs.setCurrentIndex(target_index)

            button = self._group_buttons.get(group)
            if button is not None and not button.isChecked():
                button.setChecked(True)

            self._visible_indices = indices
            self._current_group = group
        finally:
            if updates_disabled:
                self.tabbar.setUpdatesEnabled(True)
            self.tabbar.blockSignals(previous_signal_state)
            self._syncing = False

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if elapsed_ms >= self._slow_switch_threshold_ms:
            log.debug(
                "shell navigation switch %.1fms group=%s target=%s rebuild=%s tabs=%s",
                elapsed_ms,
                group,
                target_index,
                needs_rebuild,
                len(indices),
            )

    def _tabbar_matches_indices(self, indices: list[int]) -> bool:
        if self._tabs is None or self.tabbar.count() != len(indices):
            return False
        for visible_index, global_index in enumerate(indices):
            if self.tabbar.tabText(visible_index) != self._tabs.tabText(global_index):
                return False
        return True

    def _find_group_for_index(self, tab_index: int) -> str:
        for group, indices in self._group_to_indices.items():
            if tab_index in indices:
                return group
        groups = list(self._group_to_indices.keys())
        return groups[0] if groups else ""

    def _remember_group_index(self, group: str, tab_index: int) -> None:
        if not group:
            return
        if tab_index not in self._group_to_indices.get(group, []):
            return
        self._last_index_by_group[group] = tab_index

    def sync_from_current_tab(self, tab_index: int) -> None:
        group = self._find_group_for_index(tab_index)
        if not group:
            return
        self._remember_group_index(group, tab_index)
        self._switch_group(group, preferred_index=tab_index)

    def _on_tabbar_changed(self, visible_index: int) -> None:
        if self._syncing or self._tabs is None:
            return
        if 0 <= visible_index < len(self._visible_indices):
            self._tabs.setCurrentIndex(self._visible_indices[visible_index])

    def _on_tabs_changed(self, tab_index: int) -> None:
        if self._syncing:
            return
        self.sync_from_current_tab(tab_index)

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        for button in self._group_buttons.values():
            button.setStyleSheet(_nav_group_button_qss(theme))
        self.tabbar.setStyleSheet(_standalone_tabbar_qss(theme))


class TitleBarSyncWidget(QFrame):
    """标题栏全局同步入口与同步状态摘要。"""

    _STATE_META = {
        "idle": ("同步就绪", "neutral"),
        "cache": ("本地缓存", "cached"),
        "working": ("同步中", "loading"),
        "success": ("已同步", "success"),
        "error": ("同步异常", "error"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._detail = ""
        self._freshness = ""

        self.setObjectName("titleBarSyncWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.btn_sync = QPushButton("全局同步", self)
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.setAccessibleName("全局同步")
        self.btn_sync.setToolTip("执行盘后全局同步（F5）")
        layout.addWidget(self.btn_sync, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_state = QLabel("同步就绪", self)
        self.lbl_state.setObjectName("titleBarSyncState")
        layout.addWidget(self.lbl_state, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_meta = QLabel("等待首次同步", self)
        self.lbl_meta.setObjectName("titleBarSyncMeta")
        layout.addWidget(self.lbl_meta, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme()

    def set_state(self, state: str, detail: str = "", freshness: str = "") -> None:
        canonical_state = str(state or "").strip() or "idle"
        if canonical_state not in self._STATE_META:
            canonical_state = "idle"

        self._state = canonical_state
        self._detail = str(detail or "").strip()
        if freshness:
            self._freshness = str(freshness or "").strip()

        state_text, _tone_name = self._STATE_META[self._state]
        self.lbl_state.setText(state_text)

        segments = []
        if self._detail:
            segments.append(self._detail)
        if self._freshness:
            segments.append(self._freshness)
        self.lbl_meta.setText("｜".join(segments) if segments else "等待首次同步")
        self.apply_theme()

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        tokens = build_ui_tokens(theme)
        _state_text, tone_name = self._STATE_META.get(self._state, self._STATE_META["idle"])
        tone = get_state_tone(tone_name, theme)

        self.setStyleSheet(
            f"""
            QFrame#titleBarSyncWidget {{
                background: transparent;
            }}
            QLabel#titleBarSyncState {{
                background-color: {tone['bg']};
                color: {tone['fg']};
                border: 1px solid {tone['border']};
                border-radius: {tokens['radius']['pill']}px;
                padding: 0 {tokens['space']['md']}px;
                min-height: {tokens['control']['toolbar_chip_height']}px;
                font-size: {tokens['font']['size_sm']}px;
                font-weight: {tokens['font']['weight_semibold']};
            }}
            QLabel#titleBarSyncMeta {{
                color: {theme['TEXT_MUTED']};
                font-size: {tokens['font']['size_sm']}px;
            }}
            """
        )
        self.btn_sync.setStyleSheet(_titlebar_sync_button_qss(theme))


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
    btn_minimize.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_minimize.setToolTip("最小化窗口")
    btn_minimize.setAccessibleName("最小化窗口")
    btn_minimize.clicked.connect(window.showMinimized)

    btn_maximize = QPushButton("□")
    btn_maximize.setStyleSheet(
        _titlebar_button_style(theme, theme['TEXT_MUTED'], theme['BG_HOVER'])
    )
    btn_maximize.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_maximize.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_maximize.setToolTip("最大化或还原窗口")
    btn_maximize.setAccessibleName("最大化或还原窗口")
    btn_maximize.clicked.connect(window._toggle_maximize)

    btn_close = QPushButton("✕")
    btn_close.setStyleSheet(
        _titlebar_button_style(theme, theme['TEXT_MUTED'], "#C42B1C")
    )
    btn_close.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_close.setToolTip("关闭窗口")
    btn_close.setAccessibleName("关闭窗口")
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
    if getattr(window, "tabs", None) is None:
        return QTabBar()
    window.tabs.tabBar().setVisible(False)

    nav_widget = getattr(window, "_shell_navigation_widget", None)
    sync_widget = getattr(window, "_titlebar_sync_widget", None)

    if nav_widget is None or sync_widget is None:
        nav_host = QWidget()
        nav_host.setObjectName("titleBarNavHost")
        nav_layout = QHBoxLayout(nav_host)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(10)

        nav_widget = ShellNavigationWidget(nav_host)
        sync_widget = TitleBarSyncWidget(nav_host)
        sync_widget.btn_sync.clicked.connect(window._action_refresh_f5)

        nav_layout.addWidget(nav_widget, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        nav_layout.addWidget(sync_widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        old = window._titlebar_tab_placeholder
        idx = window._titlebar_layout.indexOf(old)
        window._titlebar_layout.removeWidget(old)
        old.deleteLater()
        window._titlebar_layout.insertWidget(idx, nav_host, 1)

        window._titlebar_nav_host = nav_host
        window._shell_navigation_widget = nav_widget
        window._titlebar_sync_widget = sync_widget

    nav_widget.bind_workspace(getattr(window, "_workspace", None), window.tabs)
    window._standalone_tabbar = nav_widget.tabbar
    return nav_widget.tabbar


def setup_system_menu(window) -> SystemMenuRefs:
    from app.services.ui_runtime_service import app_config
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
    except (AttributeError, RuntimeError, TypeError):
        pass

    sys_menu.setObjectName("sysMenu")

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
    theme_group = QActionGroup(window)
    theme_group.setExclusive(True)
    window._theme_actions = {}
    for theme_name in theme_manager.theme_names():
        act = theme_menu.addAction(theme_name)
        act.setCheckable(True)
        act.setChecked(theme_name == theme_manager.current_theme_name)
        theme_group.addAction(act)
        window._theme_actions[theme_name] = act
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
    for theme_name, action in getattr(window, "_theme_actions", {}).items():
        action.setChecked(theme_name == theme_manager.current_theme_name)
    if hasattr(window, "_act_auto_theme") and window._act_auto_theme:
        window._act_auto_theme.setChecked(theme_manager.is_auto_switch())


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

    if hasattr(window, "_shell_navigation_widget") and window._shell_navigation_widget:
        window._shell_navigation_widget.apply_theme()

    if hasattr(window, "_titlebar_sync_widget") and window._titlebar_sync_widget:
        window._titlebar_sync_widget.apply_theme()

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

    if hasattr(window, "_titlebar_nav_host") and window._titlebar_nav_host:
        window._titlebar_nav_host.setStyleSheet("QWidget#titleBarNavHost { background: transparent; }")

    refresh_system_menu_theme(window)
