# -*- coding: utf-8 -*-
import datetime
from typing import Any

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QComboBox,
)

from core.market_calendar import MarketCalendar


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


class VCPScanRangeDialog(QDialog):
    PRESET_RECENT_30 = "recent_30"
    PRESET_RECENT_60 = "recent_60"
    PRESET_RECENT_120 = "recent_120"
    PRESET_YTD = "ytd"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scanRangeDialog")
        self.setWindowTitle("选择扫描时间区间")
        self.setModal(True)
        self.resize(520, 240)

        self._syncing_dates = False
        self._active_preset: str | None = None
        self._preset_buttons: dict[str, QPushButton] = {}

        latest_trade_date = _latest_cn_trade_date()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

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
        date_section.setObjectName("dialogSection")
        date_layout = QGridLayout(date_section)
        date_layout.setContentsMargins(14, 14, 14, 14)
        date_layout.setHorizontalSpacing(12)
        date_layout.setVerticalSpacing(10)

        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.calendarWidget().setVerticalHeaderFormat(0)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")

        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.calendarWidget().setVerticalHeaderFormat(0)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate(latest_trade_date.year, latest_trade_date.month, latest_trade_date.day))

        date_layout.addWidget(QLabel("开始日期"), 0, 0)
        date_layout.addWidget(self.start_date_edit, 0, 1)
        date_layout.addWidget(QLabel("结束日期"), 1, 0)
        date_layout.addWidget(self.end_date_edit, 1, 1)
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
        self._active_preset = preset_key
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


class VCPScanSettingsDialog(QDialog):
    DEFAULT_PRESET_NAME = "VCP 标准"

    def __init__(self, params: dict[str, Any], user_presets: dict[str, dict[str, float]] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("VCP 扫描参数设置")
        self.setModal(True)
        self.resize(460, 360)

        self._user_presets = dict(user_presets or {})
        self._builtin_presets = self._build_builtin_presets()
        self._all_presets: dict[str, dict[str, float]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

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
