# -*- coding: utf-8 -*-
import datetime
from typing import Any

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
)

from app.services.ui_runtime_service import MarketCalendar
from ui.components.shared_title_bar import DraggableTitleBar
from ui.components.trade_calendar import TradeDateEdit
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def _latest_cn_trade_date() -> datetime.date:
    return MarketCalendar.get_latest_trade_date("CN")


def _cached_cn_trade_dates() -> list[datetime.date]:
    if MarketCalendar._trade_dates is None:
        MarketCalendar._trade_dates = MarketCalendar.load_trade_dates()
    if not MarketCalendar._trade_dates:
        return []

    dates: list[datetime.date] = []
    for item in MarketCalendar._trade_dates:
        try:
            dates.append(datetime.date.fromisoformat(str(item)))
        except ValueError:
            continue
    dates.sort()
    return dates


def _recent_trade_window(end_date: datetime.date, count: int) -> tuple[datetime.date, datetime.date]:
    cached_dates = [d for d in _cached_cn_trade_dates() if d <= end_date]
    if len(cached_dates) >= count:
        return cached_dates[-count], end_date

    cursor = end_date
    picked: list[datetime.date] = []
    while len(picked) < count:
        if MarketCalendar.is_trade_day(cursor, "CN"):
            picked.append(cursor)
        cursor -= datetime.timedelta(days=1)
    return picked[-1], end_date


def _first_trade_day_of_year(end_date: datetime.date) -> tuple[datetime.date, datetime.date]:
    first_day = datetime.date(end_date.year, 1, 1)
    cached_dates = [d for d in _cached_cn_trade_dates() if first_day <= d <= end_date]
    if cached_dates:
        return cached_dates[0], end_date

    cursor = first_day
    while cursor <= end_date:
        if MarketCalendar.is_trade_day(cursor, "CN"):
            return cursor, end_date
        cursor += datetime.timedelta(days=1)
    return end_date, end_date


class _ThemedDialog(QDialog):
    """Shared frameless shell for scan dialogs so dialog chrome matches the active theme."""

    def __init__(
        self,
        object_name: str,
        window_title: str,
        size: tuple[int, int],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setWindowTitle(window_title)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(*size)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._container = QFrame(self)
        self._container.setObjectName("dialogContainer")
        outer_layout.addWidget(self._container)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(1, 1, 1, 18)
        container_layout.setSpacing(0)

        self._title_bar = DraggableTitleBar(self)
        self._title_bar.setObjectName("dialogTitleBar")
        title_bar_layout = QHBoxLayout(self._title_bar)
        title_bar_layout.setContentsMargins(14, 0, 8, 0)
        title_bar_layout.setSpacing(0)

        self._window_title_label = QLabel(window_title)
        self._window_title_label.setObjectName("dialogWindowTitle")
        title_bar_layout.addWidget(self._window_title_label)
        title_bar_layout.addStretch(1)

        self._btn_close = QToolButton(self._title_bar)
        self._btn_close.setObjectName("dialogCloseButton")
        self._btn_close.setText("✕")
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self.reject)
        title_bar_layout.addWidget(self._btn_close)
        container_layout.addWidget(self._title_bar)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(20, 18, 20, 0)
        self.content_layout.setSpacing(14)
        container_layout.addLayout(self.content_layout)

        self._refresh_shell_metrics()
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    def _refresh_shell_metrics(self):
        tokens = build_ui_tokens(theme_manager.current_theme)
        titlebar_height = tokens["shell"]["titlebar_height"]
        self._title_bar.setFixedHeight(titlebar_height)
        self._btn_close.setFixedSize(36, titlebar_height)

    def _on_theme_changed(self, _theme_name: str):
        self._refresh_shell_metrics()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class TradeDateRangeDialog(_ThemedDialog):
    def __init__(
        self,
        *,
        object_name: str = "tradeDateRangeDialog",
        window_title: str = "选择区间",
        headline: str = "选择时间区间",
        hint: str = "使用统一交易日历控件选择开始和结束日期。",
        confirm_text: str = "确定",
        start_label: str = "开始日期",
        end_label: str = "结束日期",
        default_start: datetime.date | None = None,
        default_end: datetime.date | None = None,
        parent=None,
    ):
        super().__init__(object_name, window_title, (438, 276), parent)

        latest_trade_date = _latest_cn_trade_date()
        start_date = default_start or latest_trade_date
        end_date = default_end or latest_trade_date

        layout = self.content_layout

        title = QLabel(headline)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint_label = QLabel(hint)
        hint_label.setObjectName("dialogHint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        calendar_card = QFrame()
        calendar_card.setObjectName("tradeCalendarBody")
        calendar_layout = QVBoxLayout(calendar_card)
        calendar_layout.setContentsMargins(14, 14, 14, 14)
        calendar_layout.setSpacing(12)

        section_title = QLabel("回补区间")
        section_title.setObjectName("calendarSectionTitle")
        calendar_layout.addWidget(section_title)

        date_grid = QGridLayout()
        date_grid.setHorizontalSpacing(12)
        date_grid.setVerticalSpacing(10)

        self.start_date_edit = TradeDateEdit(fixed_width=140)
        self.end_date_edit = TradeDateEdit(fixed_width=140)
        self.start_date_edit.setDate(QDate(start_date.year, start_date.month, start_date.day))
        self.end_date_edit.setDate(QDate(end_date.year, end_date.month, end_date.day))

        date_grid.addWidget(QLabel(start_label), 0, 0)
        date_grid.addWidget(self.start_date_edit, 0, 1)
        date_grid.addWidget(QLabel(end_label), 1, 0)
        date_grid.addWidget(self.end_date_edit, 1, 1)
        date_grid.setColumnStretch(2, 1)
        calendar_layout.addLayout(date_grid)
        layout.addWidget(calendar_card)

        footer = QHBoxLayout()
        footer.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_confirm = QPushButton(confirm_text)
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.clicked.connect(self.accept)
        footer.addWidget(btn_confirm)
        layout.addLayout(footer)

    def selected_range(self) -> tuple[str, str]:
        start_date = self.start_date_edit.date().toPyDate()
        end_date = self.end_date_edit.date().toPyDate()
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date.isoformat(), end_date.isoformat()


class VCPScanRangeDialog(_ThemedDialog):
    PRESET_RECENT_30 = "recent_30"
    PRESET_RECENT_60 = "recent_60"
    PRESET_RECENT_120 = "recent_120"
    PRESET_YTD = "ytd"

    def __init__(self, parent=None):
        super().__init__("scanRangeDialog", "选择扫描时间区间", (520, 292), parent)

        self._syncing_dates = False
        self._preset_buttons: dict[str, QPushButton] = {}

        latest_trade_date = _latest_cn_trade_date()

        layout = self.content_layout

        title = QLabel("VCP 区间扫描")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint = QLabel("默认按中国 A 股交易日区间扫描，支持快捷区间和手动调整。")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        quick_section = QFrame()
        quick_section.setObjectName("dialogSection")
        quick_layout = QVBoxLayout(quick_section)
        quick_layout.setContentsMargins(14, 14, 14, 14)
        quick_layout.setSpacing(10)
        quick_layout.addWidget(QLabel("快捷区间"))

        quick_buttons = QHBoxLayout()
        quick_buttons.setSpacing(8)
        presets = [
            ("近30交易日", self.PRESET_RECENT_30),
            ("近60交易日", self.PRESET_RECENT_60),
            ("近120交易日", self.PRESET_RECENT_120),
            ("今年至今", self.PRESET_YTD),
        ]
        for text, preset_key in presets:
            btn = QPushButton(text)
            btn.setProperty("class", "segmentControl")
            btn.setMinimumWidth(100)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked=False, key=preset_key: self._apply_preset(key))
            quick_buttons.addWidget(btn, 1)
            self._preset_buttons[preset_key] = btn
        quick_layout.addLayout(quick_buttons)
        layout.addWidget(quick_section)

        date_section = QFrame()
        date_section.setObjectName("tradeCalendarBody")
        date_layout = QGridLayout(date_section)
        date_layout.setContentsMargins(14, 14, 14, 14)
        date_layout.setHorizontalSpacing(12)
        date_layout.setVerticalSpacing(10)

        date_section_title = QLabel("交易日历区间")
        date_section_title.setObjectName("calendarSectionTitle")
        date_layout.addWidget(date_section_title, 0, 0, 1, 2)

        self.start_date_edit = TradeDateEdit(fixed_width=140)
        self.end_date_edit = TradeDateEdit(fixed_width=140)
        self.end_date_edit.setDate(QDate(latest_trade_date.year, latest_trade_date.month, latest_trade_date.day))

        date_layout.addWidget(QLabel("开始日期"), 1, 0)
        date_layout.addWidget(self.start_date_edit, 1, 1)
        date_layout.addWidget(QLabel("结束日期"), 2, 0)
        date_layout.addWidget(self.end_date_edit, 2, 1)
        layout.addWidget(date_section)

        footer = QHBoxLayout()
        footer.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_confirm = QPushButton("开始扫描")
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.clicked.connect(self.accept)
        footer.addWidget(btn_confirm)
        layout.addLayout(footer)

        self.start_date_edit.dateChanged.connect(self._on_manual_date_changed)
        self.end_date_edit.dateChanged.connect(self._on_manual_date_changed)
        self._apply_preset(self.PRESET_RECENT_30)

    def _set_button_active(self, preset_key: str | None):
        for key, button in self._preset_buttons.items():
            button.setProperty("state", "active" if key == preset_key else "")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _set_dates(self, start_date: datetime.date, end_date: datetime.date):
        self._syncing_dates = True
        try:
            self.start_date_edit.setDate(QDate(start_date.year, start_date.month, start_date.day))
            self.end_date_edit.setDate(QDate(end_date.year, end_date.month, end_date.day))
        finally:
            self._syncing_dates = False

    def _apply_preset(self, preset_key: str):
        end_date = _latest_cn_trade_date()
        if preset_key == self.PRESET_RECENT_30:
            start_date, end_date = _recent_trade_window(end_date, 30)
        elif preset_key == self.PRESET_RECENT_60:
            start_date, end_date = _recent_trade_window(end_date, 60)
        elif preset_key == self.PRESET_RECENT_120:
            start_date, end_date = _recent_trade_window(end_date, 120)
        else:
            start_date, end_date = _first_trade_day_of_year(end_date)

        self._set_dates(start_date, end_date)
        self._set_button_active(preset_key)

    def _on_manual_date_changed(self, _date: QDate):
        if self._syncing_dates:
            return
        self._set_button_active(None)

    def selected_range(self) -> tuple[str, str]:
        start_date = self.start_date_edit.date().toPyDate()
        end_date = self.end_date_edit.date().toPyDate()
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date.isoformat(), end_date.isoformat()


class VCPScanSettingsDialog(_ThemedDialog):
    DEFAULT_PRESET_NAME = "VCP 标准"

    def __init__(self, params: dict[str, Any], user_presets: dict[str, dict[str, float]] | None = None, parent=None):
        super().__init__("settingsDialog", "VCP 扫描参数设置", (460, 408), parent)

        self._user_presets = dict(user_presets or {})
        self._builtin_presets = self._build_builtin_presets()
        self._all_presets: dict[str, dict[str, float]] = {}

        layout = self.content_layout

        title = QLabel("扫描核心参数")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint = QLabel("预设只负责快速填充参数，点击“保存”后才会写回当前扫描配置。")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        preset_section = QFrame()
        preset_section.setObjectName("dialogSection")
        preset_layout = QHBoxLayout(preset_section)
        preset_layout.setContentsMargins(14, 14, 14, 14)
        preset_layout.setSpacing(10)

        preset_layout.addWidget(QLabel("预设方案"))
        self.combo_preset = QComboBox()
        preset_layout.addWidget(self.combo_preset, 1)

        self.btn_save_preset = QPushButton("保存预设")
        self.btn_save_preset.setProperty("class", "secondary")
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        preset_layout.addWidget(self.btn_save_preset)
        layout.addWidget(preset_section)

        form_section = QFrame()
        form_section.setObjectName("dialogSection")
        form_layout = QGridLayout(form_section)
        form_layout.setContentsMargins(14, 14, 14, 14)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(12)

        self.spn_rps = QSpinBox()
        self.spn_rps.setRange(50, 99)

        self.spn_amp = QDoubleSpinBox()
        self.spn_amp.setRange(0.1, 1.5)
        self.spn_amp.setSingleStep(0.05)
        self.spn_amp.setDecimals(2)

        self.spn_ma_bind = QDoubleSpinBox()
        self.spn_ma_bind.setRange(0.01, 0.2)
        self.spn_ma_bind.setSingleStep(0.01)
        self.spn_ma_bind.setDecimals(2)

        self.spn_amount = QDoubleSpinBox()
        self.spn_amount.setRange(0.1, 50.0)
        self.spn_amount.setSingleStep(0.5)
        self.spn_amount.setDecimals(2)

        self.spn_high250 = QDoubleSpinBox()
        self.spn_high250.setRange(0.01, 1.0)
        self.spn_high250.setSingleStep(0.05)
        self.spn_high250.setDecimals(2)

        rows = [
            ("RPS 阈值", self.spn_rps, "核心强度门槛"),
            ("振幅上限", self.spn_amp, "0.45 = 45%"),
            ("均线粘合", self.spn_ma_bind, "0.05 = 5%"),
            ("成交额(亿)", self.spn_amount, "20日均成交额"),
            ("距250日高", self.spn_high250, "0.10 = 10%"),
        ]
        for row_idx, (label_text, widget, hint_text) in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("dialogFieldLabel")
            hint_label = QLabel(hint_text)
            hint_label.setObjectName("dialogHint")
            form_layout.addWidget(label, row_idx, 0)
            form_layout.addWidget(widget, row_idx, 1)
            form_layout.addWidget(hint_label, row_idx, 2)
        layout.addWidget(form_section)

        footer = QHBoxLayout()
        self.btn_restore = QPushButton("恢复默认")
        self.btn_restore.setProperty("class", "secondary")
        self.btn_restore.clicked.connect(self._restore_defaults)
        footer.addWidget(self.btn_restore)
        footer.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_confirm = QPushButton("保存")
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.clicked.connect(self.accept)
        footer.addWidget(btn_confirm)
        layout.addLayout(footer)

        self.combo_preset.currentIndexChanged.connect(self._on_preset_selected)
        self._refresh_presets()
        self.set_values(params)

    def _build_builtin_presets(self) -> dict[str, dict[str, float]]:
        return {
            "VCP 标准": {"rps": 80, "amp": 0.45, "ma_bind": 0.05, "amount": 0.8, "high250": 0.10},
            "稳健主升": {"rps": 85, "amp": 0.35, "ma_bind": 0.03, "amount": 1.2, "high250": 0.05},
            "温和筑底": {"rps": 70, "amp": 0.25, "ma_bind": 0.05, "amount": 0.5, "high250": 0.20},
            "激进 (低门槛)": {"rps": 60, "amp": 0.60, "ma_bind": 0.08, "amount": 0.3, "high250": 0.20},
            "保守 (严筛选)": {"rps": 90, "amp": 0.30, "ma_bind": 0.03, "amount": 2.0, "high250": 0.08},
        }

    def _refresh_presets(self):
        self._all_presets = {**self._builtin_presets, **self._user_presets}
        self.combo_preset.blockSignals(True)
        try:
            self.combo_preset.clear()
            self.combo_preset.addItem("选择预设...", "")
            for preset_name in self._all_presets:
                self.combo_preset.addItem(preset_name, preset_name)
        finally:
            self.combo_preset.blockSignals(False)

    def _on_preset_selected(self, _index: int):
        preset_name = self.combo_preset.currentData()
        if not preset_name:
            return
        preset = self._all_presets.get(str(preset_name))
        if preset:
            self.set_values(preset)

    def _on_save_preset(self):
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称:")
        if not ok or not name.strip():
            return
        clean_name = name.strip()
        self._user_presets[clean_name] = self.values()
        self._refresh_presets()
        idx = self.combo_preset.findData(clean_name)
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)

    def _restore_defaults(self):
        self.set_values(self._builtin_presets[self.DEFAULT_PRESET_NAME])
        idx = self.combo_preset.findData(self.DEFAULT_PRESET_NAME)
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)

    def set_values(self, params: dict[str, Any]):
        self.spn_rps.setValue(int(params.get("rps", 80)))
        self.spn_amp.setValue(float(params.get("amp", 0.45)))
        self.spn_ma_bind.setValue(float(params.get("ma_bind", 0.05)))
        self.spn_amount.setValue(float(params.get("amount", 0.8)))
        self.spn_high250.setValue(float(params.get("high250", 0.10)))

    def values(self) -> dict[str, float]:
        return {
            "rps": float(self.spn_rps.value()),
            "amp": float(self.spn_amp.value()),
            "ma_bind": float(self.spn_ma_bind.value()),
            "amount": float(self.spn_amount.value()),
            "high250": float(self.spn_high250.value()),
        }

    def user_presets(self) -> dict[str, dict[str, float]]:
        return self._user_presets

