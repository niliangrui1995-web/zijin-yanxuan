# -*- coding: utf-8 -*-
"""Shell UI helpers for MainWindowQT."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QActionGroup, QBrush, QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.components import StatusGlyph
from ui.components.motion import install_button_feedback, install_menu_fade
from ui.components.shared_title_bar import DraggableTitleBar
from ui.components.shell_styles import (
    nav_group_button_qss as _nav_group_button_qss,
)
from ui.components.shell_styles import (
    standalone_tabbar_qss as _standalone_tabbar_qss,
)
from ui.components.shell_styles import (
    system_button_style as _system_button_style,
)
from ui.components.shell_styles import (
    titlebar_button_style as _titlebar_button_style,
)
from ui.components.shell_styles import (
    titlebar_secondary_button_qss as _titlebar_secondary_button_qss,
)
from ui.components.shell_styles import (
    titlebar_shell_style as _titlebar_shell_style,
)
from ui.components.shell_styles import (
    titlebar_sync_button_qss as _titlebar_sync_button_qss,
)
from ui.components.vector_icons import set_button_svg_icon, tab_svg_icon
from ui.theme_tokens import build_ui_tokens, get_state_tone

log = get_logger(__name__)

_LAUNCH_AT_LOGIN_TEXT = "\u5f00\u673a\u81ea\u542f\u52a8"
_LAUNCH_AT_LOGIN_UNSUPPORTED_TIP = "\u4ec5\u652f\u6301 Windows \u7cfb\u7edf"
_LAUNCH_AT_LOGIN_TIP = "\u968f Windows \u767b\u5f55\u81ea\u52a8\u542f\u52a8"


def _sync_launch_at_login_action(window) -> None:
    action = getattr(window, "_act_launch_at_login", None)
    if action is None:
        return

    supported_probe = getattr(window, "_is_launch_at_login_supported", None)
    enabled_probe = getattr(window, "_is_launch_at_login_enabled", None)
    supported = bool(supported_probe()) if callable(supported_probe) else False
    enabled = bool(enabled_probe()) if supported and callable(enabled_probe) else False

    previous = action.blockSignals(True)
    try:
        action.setEnabled(supported)
        action.setChecked(enabled)
        action.setToolTip(_LAUNCH_AT_LOGIN_TIP if supported else _LAUNCH_AT_LOGIN_UNSUPPORTED_TIP)
    finally:
        action.blockSignals(previous)


class MarketPulseStrip(QWidget):
    """A thin titlebar pulse strip that stays outside the content layout."""

    def __init__(self, host: QWidget, parent=None):
        super().__init__(host if parent is None else parent)
        self._host = host
        self._phase = 0.0
        self._brand = QColor("#B93A32")
        self._deep = QColor("#6F211D")
        self.setObjectName("marketPulseStrip")
        self.setFixedHeight(3)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.apply_theme()
        try:
            from ui.theme import theme_manager

            theme_manager.sig_theme_changed.connect(self._on_theme_changed)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._sync_geometry()
        self._timer.start()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.018) % 1.0
        self.update()

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        self._brand = QColor(
            theme.get("TITLEBAR_PULSE", theme.get("BRAND_HOVER", theme.get("BRAND_PRIMARY", "#B93A32")))
        )
        self._deep = QColor(
            theme.get("TITLEBAR_PULSE_BASE", theme.get("BRAND_DEEP", theme.get("BRAND_PRIMARY", "#6F211D")))
        )
        self.update()

    def _on_theme_changed(self, _theme_name: str) -> None:
        self.apply_theme()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ChildAdded,
        }:
            self._sync_geometry()
            QTimer.singleShot(0, self._sync_geometry)
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _sync_geometry(self) -> None:
        if self._host is None:
            return
        self.setGeometry(0, max(0, self._host.height() - self.height()), self._host.width(), self.height())
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = QRectF(self.rect())
        if rect.width() <= 0:
            return

        base = QColor(self._deep)
        base.setAlpha(220)
        painter.fillRect(rect, base)

        span = max(180.0, rect.width() * 0.34)
        center = (rect.width() + span * 2.0) * self._phase - span
        gradient = QLinearGradient(center - span, 0, center + span, 0)
        edge = QColor(self._brand)
        edge.setAlpha(0)
        glow = QColor(self._brand)
        glow.setAlpha(180)
        core = QColor(255, 241, 242)
        core.setAlpha(220)
        gradient.setColorAt(0.0, edge)
        gradient.setColorAt(0.45, glow)
        gradient.setColorAt(0.5, core)
        gradient.setColorAt(0.55, glow)
        gradient.setColorAt(1.0, edge)
        painter.fillRect(rect, gradient)


class StatusFlowStrip(QWidget):
    """A quiet bottom-edge strip for short status feedback."""

    _MODE_ALIASES = {
        "busy": "working",
        "working": "working",
        "cache": "cache",
        "online": "success",
        "ready": "success",
        "success": "success",
        "offline": "error",
        "error": "error",
    }

    def __init__(self, host: QWidget, parent=None):
        super().__init__(host if parent is None else parent)
        self._host = host
        self._mode = "neutral"
        self._phase = 0.0
        self._ticks_left = 0
        self._neutral = QColor("#1F2937")
        self._cyan = QColor("#22D3EE")
        self._brand = QColor("#B93A32")
        self._error = QColor("#E05243")
        self.setObjectName("statusFlowStrip")
        self.setFixedHeight(2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._timer = QTimer(self)
        self._timer.setInterval(42)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.apply_theme()
        self._sync_geometry()

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        self._neutral = QColor(theme.get("STATUSBAR_BORDER", theme.get("BORDER_SUBTLE", "#1F2937")))
        self._cyan = QColor(theme.get("COLOR_REALTIME", theme.get("NETWORK_ONLINE", "#4E9CC9")))
        self._brand = QColor(theme.get("STATUS_FLOW_WORKING", theme.get("BRAND_PRIMARY", "#B93A32")))
        self._error = QColor(theme.get("COLOR_ERROR", theme.get("NETWORK_OFFLINE", "#E05243")))
        self.update()

    def set_mode(self, mode: str, *, animate: bool = True) -> None:
        next_mode = self._MODE_ALIASES.get(str(mode or "").strip(), "neutral")
        self._mode = next_mode
        self._phase = 0.0
        if animate and next_mode != "neutral":
            self._ticks_left = 44 if next_mode == "error" else 34
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._ticks_left = 0
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = min(1.0, self._phase + 0.035)
        self._ticks_left -= 1
        if self._ticks_left <= 0:
            self._mode = "neutral"
            self._phase = 0.0
            self._timer.stop()
        self.update()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self._sync_geometry()
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _sync_geometry(self) -> None:
        if self._host is None:
            return
        self.setGeometry(0, max(0, self._host.height() - self.height()), self._host.width(), self.height())
        self.raise_()

    def _active_color(self) -> QColor:
        if self._mode in {"cache", "success"}:
            return QColor(self._cyan)
        if self._mode == "error":
            return QColor(self._error)
        return QColor(self._brand)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = QRectF(self.rect())
        if rect.width() <= 0:
            return

        base = QColor(self._neutral)
        base.setAlpha(58)
        painter.fillRect(rect, base)

        if self._mode == "neutral" or self._ticks_left <= 0:
            return

        color = self._active_color()
        span = max(110.0, rect.width() * (0.18 if self._mode == "error" else 0.24))
        center = (rect.width() + span * 1.5) * self._phase - span * 0.75
        gradient = QLinearGradient(center - span, 0, center + span, 0)
        edge = QColor(color)
        edge.setAlpha(0)
        glow = QColor(color)
        glow.setAlpha(130 if self._mode != "error" else 165)
        core = QColor(color)
        core.setAlpha(215 if self._mode != "working" else 180)
        gradient.setColorAt(0.0, edge)
        gradient.setColorAt(0.42, glow)
        gradient.setColorAt(0.5, core)
        gradient.setColorAt(0.58, glow)
        gradient.setColorAt(1.0, edge)
        painter.fillRect(rect, gradient)


class QuotePulseDot(QWidget):
    """Small titlebar heartbeat dot for realtime quote broadcasts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._base_color = QColor("#8290AA")
        self._pulse_color = QColor("#34C759")
        self._pulse_started = 0.0
        self._pulse_duration = 0.28
        self._timer = QTimer(self)
        self._timer.setInterval(24)
        self._timer.timeout.connect(self._tick)
        self.apply_theme()

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        tokens = build_ui_tokens(theme)
        self._base_color = QColor(theme.get("TEXT_MUTED", "#8290AA"))
        self._base_color.setAlpha(110 if tokens["is_dark"] else 140)
        self._pulse_color = QColor(theme.get("COLOR_SUCCESS", "#34C759"))
        flash_ms = int(tokens["motion"].get("quote_pulse_flash", 80))
        decay_ms = int(tokens["motion"].get("quote_pulse_decay", 200))
        self._pulse_duration = max(0.08, (flash_ms + decay_ms) / 1000.0)
        self.update()

    def pulse(self) -> None:
        self._pulse_started = time.perf_counter()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _pulse_progress(self) -> float:
        if self._pulse_started <= 0:
            return 1.0
        return max(0.0, min(1.0, (time.perf_counter() - self._pulse_started) / self._pulse_duration))

    def _tick(self) -> None:
        if self._pulse_progress() >= 1.0:
            self._timer.stop()
            self._pulse_started = 0.0
        self.update()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        progress = self._pulse_progress()
        active = self._pulse_started > 0 and progress < 1.0
        strength = 1.0 - progress if active else 0.0
        center = QRectF(self.rect()).center()

        if active:
            aura = QColor(self._pulse_color)
            aura.setAlpha(max(0, min(120, int(100 * strength))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(aura))
            painter.drawEllipse(center, 6.0, 6.0)

        dot = QColor(self._pulse_color if active else self._base_color)
        if active:
            dot.setAlpha(max(110, min(255, int(180 + 75 * strength))))
        painter.setPen(QPen(dot, 1))
        painter.setBrush(QBrush(dot))
        painter.drawEllipse(center, 3.0, 3.0)


class MainWindowStatusBar(QFrame):
    """底部状态栏：统一维护指示灯、状态文本和时钟。"""

    def __init__(self, version_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBarWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(34)
        self._status_tone = "offline"
        self.status_flow = StatusFlowStrip(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        from ui.theme import theme_manager

        default_dot = theme_manager.current_theme.get(
            "NETWORK_OFFLINE",
            theme_manager.current_theme.get("COLOR_ERROR", "#EF4444"),
        )
        self.status_dot = StatusGlyph("offline")
        self.status_dot.set_color(default_dot)
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

    def set_status_tone(self, tone: str, *, animate: bool = True) -> None:
        self._status_tone = tone if tone in {"online", "busy", "offline"} else "offline"
        self.status_dot.set_tone(self._status_tone)
        self.status_flow.set_mode(self._status_tone, animate=animate)

    def show_sync_feedback(self, state: str) -> None:
        mode = str(state or "").strip()
        if mode in {"idle", "neutral"}:
            self.status_flow.set_mode("neutral", animate=False)
            return
        self.status_flow.set_mode(mode, animate=True)

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
                background-color: {theme["BG_STATUSBAR"]};
                border-top: 1px solid {theme["STATUSBAR_BORDER"]};
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
        self.lbl_version.setStyleSheet(f"color: {theme['TEXT_DISABLED']}; font-size: {tokens['font']['size_xs']}px;")
        self.status_flow.apply_theme()
        self.set_status_tone(self._status_tone, animate=False)


class ShellNavigationWidget(QWidget):
    """标题栏一级导航 + 二级标签导航。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self._compact_nav = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._group_button_group = QButtonGroup(self)
        self._group_button_group.setExclusive(True)

        self.group_wrap = QWidget(self)
        self.group_layout = QHBoxLayout(self.group_wrap)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.setSpacing(4)
        layout.addWidget(self.group_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.tabbar = QTabBar(self)
        self.tabbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tabbar.setMinimumWidth(420)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabbar.setAccessibleName("二级页面导航")
        self.tabbar.setIconSize(QSize(16, 16))
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
            group: index for group, index in self._last_index_by_group.items() if group in self._group_to_indices
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
            button.setMaximumWidth(104)
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
                icon_theme = build_ui_tokens()["theme"]
                icon_tokens = build_ui_tokens(icon_theme)["icon"]
                self.tabbar.setUpdatesEnabled(False)
                updates_disabled = True
                while self.tabbar.count() > 0:
                    self.tabbar.removeTab(self.tabbar.count() - 1)
                for global_index in indices:
                    label = self._tabs.tabText(global_index)
                    icon = tab_svg_icon(
                        label=label,
                        color=icon_tokens["muted"],
                        size=icon_tokens["chrome_size"],
                        stroke_width=icon_tokens["stroke_width"],
                    )
                    self.tabbar.addTab(icon, label)
                self._tabbar_rebuild_count += 1
                self._update_tabbar_minimum_width()

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

    def _update_tabbar_minimum_width(self) -> None:
        tokens = build_ui_tokens()
        padding_x = max(12, tokens["control"]["tab_padding_x"] + (0 if self._compact_nav else 2))
        gap = 2 if self._compact_nav else max(3, tokens["shell"]["toolbar_group_gap"])
        metrics = self.tabbar.fontMetrics()
        icon_size = build_ui_tokens()["icon"]["chrome_size"]
        total = 0
        for index in range(self.tabbar.count()):
            label_width = metrics.horizontalAdvance(self.tabbar.tabText(index))
            total += label_width + icon_size + padding_x * 2 + gap + 14
        self.tabbar.setMinimumWidth(max(420, min(total, 760)))

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_mode()

    def _apply_responsive_mode(self) -> None:
        compact = self.width() < 760
        if compact == self._compact_nav:
            return
        self._compact_nav = compact
        self.tabbar.setExpanding(False)
        for button in self._group_buttons.values():
            button.setMaximumWidth(86 if compact else 104)
        self.apply_theme()
        self._update_tabbar_minimum_width()

    def apply_theme(self) -> None:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        tokens = build_ui_tokens(theme)
        for button in self._group_buttons.values():
            button.setStyleSheet(_nav_group_button_qss(theme))
        icon_tokens = tokens["icon"]
        self.tabbar.setIconSize(QSize(icon_tokens["chrome_size"], icon_tokens["chrome_size"]))
        for index in range(self.tabbar.count()):
            label = self.tabbar.tabText(index)
            self.tabbar.setTabIcon(
                index,
                tab_svg_icon(
                    label=label,
                    color=icon_tokens["muted"],
                    size=icon_tokens["chrome_size"],
                    stroke_width=icon_tokens["stroke_width"],
                ),
            )
        self.tabbar.setStyleSheet(_standalone_tabbar_qss(theme, compact=self._compact_nav))


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
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
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
        install_button_feedback(self.btn_sync)
        layout.addWidget(self.btn_sync, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_trade_calendar = QPushButton("交易日历", self)
        self.btn_trade_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_trade_calendar.setAccessibleName("交易日历")
        self.btn_trade_calendar.setToolTip("查看 A股交易日历与寡头财报日历")
        install_button_feedback(self.btn_trade_calendar)
        layout.addWidget(self.btn_trade_calendar, 0, Qt.AlignmentFlag.AlignVCenter)

        self.quote_pulse_dot = QuotePulseDot(self)
        self.quote_pulse_dot.setToolTip("quotes 同步心跳")
        layout.addWidget(self.quote_pulse_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_state = QLabel("同步就绪", self)
        self.lbl_state.setObjectName("titleBarSyncState")
        layout.addWidget(self.lbl_state, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_meta = QLabel("等待首次同步", self)
        self.lbl_meta.setObjectName("titleBarSyncMeta")
        self.lbl_meta.setMinimumWidth(220)
        self.lbl_meta.setMaximumWidth(420)
        self.lbl_meta.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.lbl_meta.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.lbl_meta, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme()

    def pulse_quotes(self) -> None:
        self.quote_pulse_dot.pulse()

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
        meta_text = "｜".join(segments) if segments else "等待首次同步"
        self.lbl_meta.setText(meta_text)
        self.lbl_meta.setToolTip(meta_text)
        self.lbl_state.setToolTip(meta_text)
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
                background-color: {tone["bg"]};
                color: {tone["fg"]};
                border: 1px solid {tone["border"]};
                border-radius: {tokens["radius"]["pill"]}px;
                padding: 0 {tokens["space"]["md"]}px;
                min-height: {tokens["control"]["toolbar_chip_height"]}px;
                font-size: {tokens["font"]["size_sm"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#titleBarSyncMeta {{
                color: {theme["TEXT_MUTED"]};
                font-size: {tokens["font"]["size_sm"]}px;
            }}
            """
        )
        self.btn_sync.setStyleSheet(_titlebar_sync_button_qss(theme))
        self.btn_trade_calendar.setStyleSheet(_titlebar_secondary_button_qss(theme))
        self.quote_pulse_dot.apply_theme()


@dataclass
class TitleBarRefs:
    titlebar: DraggableTitleBar
    layout: QHBoxLayout
    placeholder: QWidget
    pulse_strip: MarketPulseStrip
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
    icon_size = tokens["icon"]["chrome_size"]
    icon_color = tokens["icon"]["muted"]
    icon_stroke = tokens["icon"]["stroke_width"]
    titlebar = DraggableTitleBar()
    titlebar.setObjectName("customTitleBar")
    titlebar.setFixedHeight(tokens["shell"]["titlebar_height"])
    titlebar.setStyleSheet(_titlebar_shell_style(theme))
    pulse_strip = MarketPulseStrip(titlebar)

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

    icon_size = tokens["icon"]["chrome_size"]
    icon_color = tokens["icon"]["muted"]

    btn_minimize = QPushButton("", titlebar)
    btn_minimize.setStyleSheet(_titlebar_button_style(theme, theme["TEXT_MUTED"], theme["BG_HOVER"], font_size=11))
    btn_minimize.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_minimize.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_minimize.setToolTip("最小化窗口")
    btn_minimize.setAccessibleName("最小化窗口")
    set_button_svg_icon(btn_minimize, "minimize", icon_color, size=icon_size, stroke_width=icon_stroke)
    install_button_feedback(btn_minimize)
    btn_minimize.clicked.connect(window.showMinimized)

    btn_maximize = QPushButton("", titlebar)
    btn_maximize.setStyleSheet(_titlebar_button_style(theme, theme["TEXT_MUTED"], theme["BG_HOVER"]))
    btn_maximize.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_maximize.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_maximize.setToolTip("最大化或还原窗口")
    btn_maximize.setAccessibleName("最大化或还原窗口")
    set_button_svg_icon(btn_maximize, "maximize", icon_color, size=icon_size, stroke_width=icon_stroke)
    install_button_feedback(btn_maximize)
    btn_maximize.clicked.connect(window._toggle_maximize)

    btn_close = QPushButton("", titlebar)
    btn_close.setStyleSheet(_titlebar_button_style(theme, theme["TEXT_MUTED"], "#C42B1C"))
    btn_close.setFixedWidth(tokens["shell"]["window_button_width"])
    btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_close.setToolTip("关闭窗口")
    btn_close.setAccessibleName("关闭窗口")
    set_button_svg_icon(btn_close, "close", icon_color, size=icon_size, stroke_width=icon_stroke)
    install_button_feedback(btn_close)
    btn_close.clicked.connect(window.close)

    titlebar_layout.addWidget(btn_minimize)
    titlebar_layout.addWidget(btn_maximize)
    titlebar_layout.addWidget(btn_close)
    parent_layout.addWidget(titlebar, 0)

    return TitleBarRefs(
        titlebar=titlebar,
        layout=titlebar_layout,
        placeholder=placeholder,
        pulse_strip=pulse_strip,
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
        nav_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        nav_layout = QHBoxLayout(nav_host)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)

        nav_widget = ShellNavigationWidget(nav_host)
        sync_widget = TitleBarSyncWidget(nav_host)
        sync_widget.btn_sync.clicked.connect(window._action_refresh_f5)
        sync_widget.btn_trade_calendar.clicked.connect(window._show_trade_calendar)

        nav_layout.addWidget(nav_widget, 10, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        nav_layout.addWidget(sync_widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        old = window._titlebar_tab_placeholder
        idx = window._titlebar_layout.indexOf(old)
        window._titlebar_layout.removeWidget(old)
        old.deleteLater()
        window._titlebar_layout.insertWidget(idx, nav_host, 24)
        window._titlebar_layout.setStretch(idx, 24)
        if idx + 1 < window._titlebar_layout.count():
            window._titlebar_layout.setStretch(idx + 1, 0)

        window._titlebar_nav_host = nav_host
        window._shell_navigation_widget = nav_widget
        window._titlebar_sync_widget = sync_widget

    nav_widget.bind_workspace(getattr(window, "_workspace", None), window.tabs)
    window._standalone_tabbar = nav_widget.tabbar
    return nav_widget.tabbar


def setup_system_menu(window) -> SystemMenuRefs:
    from app.services.ui_config_service import app_config
    from ui.theme import theme_manager

    btn_parent = getattr(window, "_custom_titlebar", None) or window
    btn_sys_menu = QToolButton(btn_parent)
    btn_sys_menu.setText("")
    btn_sys_menu.setObjectName("btnSysMenu")
    btn_sys_menu.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_sys_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    btn_sys_menu.setAutoRaise(False)
    tokens = build_ui_tokens(theme_manager.current_theme)
    btn_sys_menu.setFixedWidth(tokens["shell"]["system_button_width"])
    btn_sys_menu.setFixedHeight(tokens["shell"]["titlebar_height"])
    set_button_svg_icon(
        btn_sys_menu,
        "gear",
        tokens["icon"]["muted"],
        size=tokens["icon"]["chrome_size"],
        stroke_width=tokens["icon"]["stroke_width"],
    )
    install_button_feedback(btn_sys_menu)
    btn_sys_menu.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn_sys_menu.setToolTip("系统菜单")
    btn_sys_menu.setAccessibleName("系统菜单")

    min_idx = window._titlebar_layout.indexOf(window._btn_minimize)
    window._titlebar_layout.insertWidget(min_idx, btn_sys_menu)

    sys_menu = QMenu(window)
    install_menu_fade(sys_menu)
    try:
        sys_menu.aboutToShow.connect(lambda: QApplication.restoreOverrideCursor())
        sys_menu.aboutToShow.connect(lambda: _sync_launch_at_login_action(window))
        sys_menu.aboutToHide.connect(lambda: QApplication.restoreOverrideCursor())
    except (AttributeError, RuntimeError, TypeError):
        pass

    sys_menu.setObjectName("sysMenu")

    window.act_network = sys_menu.addAction("网络状态：离线")
    window.act_network.triggered.connect(window._toggle_network)

    sys_menu.addSeparator()

    act_speed = sys_menu.addAction("重置行情连接")
    act_speed.triggered.connect(window._force_reconnect)

    act_runtime_health = sys_menu.addAction("运行时健康")
    act_runtime_health.triggered.connect(window._open_runtime_health)

    window._act_launch_at_login = sys_menu.addAction(_LAUNCH_AT_LOGIN_TEXT)
    window._act_launch_at_login.setCheckable(True)
    window._act_launch_at_login.triggered.connect(window._toggle_launch_at_login)
    _sync_launch_at_login_action(window)

    sys_menu.addSeparator()

    density_menu = sys_menu.addMenu("表格密度")
    install_menu_fade(density_menu)
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

    theme_menu = sys_menu.addMenu(f"界面主题：{theme_manager.current_theme_name}")
    install_menu_fade(theme_menu)
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
    tokens = build_ui_tokens(theme)

    if hasattr(window, "btn_sys_menu") and window.btn_sys_menu:
        window.btn_sys_menu.setStyleSheet(_system_button_style(theme, theme["TEXT_MUTED"], theme["BG_HOVER"]))
        set_button_svg_icon(
            window.btn_sys_menu,
            "gear",
            tokens["icon"]["muted"],
            size=tokens["icon"]["chrome_size"],
            stroke_width=tokens["icon"]["stroke_width"],
        )
        window.btn_sys_menu.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

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
    _sync_launch_at_login_action(window)


def apply_chrome_theme(window) -> None:
    from ui.theme import theme_manager

    theme = theme_manager.current_theme
    tokens = build_ui_tokens(theme)
    icon_size = tokens["icon"]["chrome_size"]
    icon_color = tokens["icon"]["muted"]

    if hasattr(window, "_custom_titlebar") and window._custom_titlebar:
        window._custom_titlebar.setStyleSheet(_titlebar_shell_style(theme))

    if hasattr(window, "_btn_minimize") and window._btn_minimize:
        window._btn_minimize.setStyleSheet(
            _titlebar_button_style(theme, theme["TEXT_MUTED"], theme["BG_HOVER"], font_size=11)
        )
        window._btn_minimize.setFixedWidth(tokens["shell"]["window_button_width"])
        set_button_svg_icon(
            window._btn_minimize,
            "minimize",
            icon_color,
            size=icon_size,
            stroke_width=tokens["icon"]["stroke_width"],
        )
    if hasattr(window, "_btn_maximize") and window._btn_maximize:
        window._btn_maximize.setStyleSheet(_titlebar_button_style(theme, theme["TEXT_MUTED"], theme["BG_HOVER"]))
        window._btn_maximize.setFixedWidth(tokens["shell"]["window_button_width"])
        set_button_svg_icon(
            window._btn_maximize,
            "restore" if getattr(window, "isMaximized", lambda: False)() else "maximize",
            icon_color,
            size=icon_size,
            stroke_width=tokens["icon"]["stroke_width"],
        )
    if hasattr(window, "_btn_close") and window._btn_close:
        window._btn_close.setStyleSheet(_titlebar_button_style(theme, theme["TEXT_MUTED"], "#C42B1C"))
        window._btn_close.setFixedWidth(tokens["shell"]["window_button_width"])
        set_button_svg_icon(
            window._btn_close,
            "close",
            icon_color,
            size=icon_size,
            stroke_width=tokens["icon"]["stroke_width"],
        )

    if hasattr(window, "_shell_navigation_widget") and window._shell_navigation_widget:
        window._shell_navigation_widget.apply_theme()

    if hasattr(window, "_titlebar_sync_widget") and window._titlebar_sync_widget:
        window._titlebar_sync_widget.apply_theme()

    if hasattr(window, "_market_pulse_strip") and window._market_pulse_strip:
        window._market_pulse_strip.apply_theme()

    if hasattr(window, "_custom_titlebar") and window._custom_titlebar:
        window._custom_titlebar.setFixedHeight(tokens["shell"]["titlebar_height"])

    if hasattr(window, "btn_sys_menu") and window.btn_sys_menu:
        window.btn_sys_menu.setFixedWidth(tokens["shell"]["system_button_width"])
        window.btn_sys_menu.setFixedHeight(tokens["shell"]["titlebar_height"])

    if hasattr(window, "tabs_wrapper") and window.tabs_wrapper:
        window.tabs_wrapper.setStyleSheet(f"""
            QFrame#tabsWrapperFrame {{
                background-color: {theme["BG_GLASS"]};
                border: none;
            }}
        """)

    if hasattr(window, "_titlebar_nav_host") and window._titlebar_nav_host:
        window._titlebar_nav_host.setStyleSheet("QWidget#titleBarNavHost { background: transparent; }")

    refresh_system_menu_theme(window)
