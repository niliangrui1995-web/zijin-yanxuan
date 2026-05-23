# -*- coding: utf-8 -*-
import datetime as _dt

from PyQt6.QtCore import QDate, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPen, QTextCharFormat
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.ui_earnings_calendar_service import (
    EarningsCalendarEvent,
    event_calendar_date,
    events_by_date,
    is_yfinance_date_conflict_event,
    is_yfinance_estimate_event,
    sorted_events,
)
from app.services.ui_market_calendar_service import MarketCalendar
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

_PRIORITY_TONE_FALLBACK_COLORS = {
    "super_giant": "#F6C453",
    "strategic_giant": "#A78BFA",
    "normal": "#22D3EE",
}
_PRIORITY_TONE_FALLBACK_ALPHA = {
    "super_giant": 230,
    "strategic_giant": 220,
    "normal": 205,
}
_DETACHED_EARNINGS_REFRESH_WORKERS: list[QThread] = []
_PRIORITY_LABELS = {
    "super_giant": "\u8d85\u7ea7\u5de8\u5934",
    "strategic_giant": "\u6218\u7565\u6838\u5fc3",
}
_SOURCE_TYPE_LABELS = {
    "official_ir_calendar": "\u5b98\u65b9IR\u65e5\u5386",
    "official_ir_event": "\u5b98\u65b9IR\u4e8b\u4ef6",
    "official_ir_press_release": "\u5b98\u65b9IR\u65b0\u95fb\u7a3f",
    "official_ir_results_page": "\u5b98\u65b9IR\u4e1a\u7ee9\u9875",
    "official_ir_linked_webcast": "\u5b98\u65b9IR\u94fe\u63a5\u4f1a\u8bae",
    "jpx_financial_announcement_schedule": "JPX\u51b3\u7b97\u65e5\u7a0b",
    "tdnet_disclosure": "TDnet\u62ab\u9732",
    "dart_disclosure": "DART\u62ab\u9732",
    "kind_disclosure": "KIND\u62ab\u9732",
    "mops_material_information": "MOPS\u91cd\u5927\u8baf\u606f",
    "sec_6k": "SEC 6-K",
}


def _c(token: str) -> str:
    return theme_manager.get(token)


def _priority_tone(priority: str) -> str:
    priority_text = str(priority or "").strip()
    return priority_text if priority_text in _PRIORITY_TONE_FALLBACK_COLORS else "normal"


def _remember_detached_refresh_worker(worker: QThread) -> None:
    if worker in _DETACHED_EARNINGS_REFRESH_WORKERS:
        return
    _DETACHED_EARNINGS_REFRESH_WORKERS.append(worker)

    def _forget_worker() -> None:
        try:
            _DETACHED_EARNINGS_REFRESH_WORKERS.remove(worker)
        except ValueError:
            pass

    try:
        worker.finished.connect(_forget_worker)
    except (RuntimeError, TypeError):
        pass


def _priority_marker_styles(calendar_tokens: dict | None = None) -> dict[str, dict[str, int | str]]:
    tokens = calendar_tokens or build_ui_tokens(theme_manager.current_theme).get("calendar", {})
    return {
        "super_giant": {
            "color": tokens.get("marker_super_giant", _PRIORITY_TONE_FALLBACK_COLORS["super_giant"]),
            "alpha": int(tokens.get("marker_super_giant_alpha", _PRIORITY_TONE_FALLBACK_ALPHA["super_giant"])),
        },
        "strategic_giant": {
            "color": tokens.get("marker_strategic_giant", _PRIORITY_TONE_FALLBACK_COLORS["strategic_giant"]),
            "alpha": int(tokens.get("marker_strategic_giant_alpha", _PRIORITY_TONE_FALLBACK_ALPHA["strategic_giant"])),
        },
        "normal": {
            "color": tokens.get("marker_normal", _PRIORITY_TONE_FALLBACK_COLORS["normal"]),
            "alpha": int(tokens.get("marker_normal_alpha", _PRIORITY_TONE_FALLBACK_ALPHA["normal"])),
        },
    }


class TradeCalendarWidget(QCalendarWidget):
    def __init__(self, parent=None, *, earnings_events: dict[str, list[EarningsCalendarEvent]] | None = None):
        super().__init__(parent)
        self._closing = False
        self._earnings_events_by_date: dict[str, list[EarningsCalendarEvent]] = {}
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setGridVisible(False)
        self._configure_weekday_header()
        self.set_earnings_events(earnings_events or {})
        self._apply_theme_stylesheet()
        theme_manager.sig_theme_changed.connect(self._apply_theme_stylesheet)

    def set_earnings_events(
        self, events: dict[str, list[EarningsCalendarEvent]] | list[EarningsCalendarEvent] | None
    ) -> None:
        if self._closing:
            return
        if isinstance(events, list):
            self._earnings_events_by_date = events_by_date(events)
        else:
            self._earnings_events_by_date = {str(day): list(items or []) for day, items in dict(events or {}).items()}
        self.updateCells()

    def earnings_events_for_date(self, date_value) -> list[EarningsCalendarEvent]:
        if isinstance(date_value, QDate):
            key = date_value.toString("yyyy-MM-dd")
        else:
            key = str(date_value or "").strip()[:10]
        return list(self._earnings_events_by_date.get(key, []))

    @staticmethod
    def earnings_marker_policy(events: list[EarningsCalendarEvent]) -> dict[str, list[str] | str]:
        dot_tones = [_priority_tone(event.priority) for event in list(events or [])[:3]]
        return {"dot_tones": dot_tones, "count_text": ""}

    def _configure_weekday_header(self):
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames)

        view = self.findChild(QTableView, "qt_calendar_calendarview")
        if view is not None:
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setShowGrid(False)

    def _apply_weekday_formats(self):
        weekday_format = QTextCharFormat()
        weekday_format.setForeground(QColor(_c("TEXT_SECONDARY")))
        weekday_format.setFontWeight(int(QFont.Weight.DemiBold))

        weekend_color = QColor(_c("COLOR_ERROR"))
        weekend_color.setAlpha(220 if theme_manager.is_dark() else 190)
        weekend_format = QTextCharFormat(weekday_format)
        weekend_format.setForeground(weekend_color)

        for day in (
            Qt.DayOfWeek.Monday,
            Qt.DayOfWeek.Tuesday,
            Qt.DayOfWeek.Wednesday,
            Qt.DayOfWeek.Thursday,
            Qt.DayOfWeek.Friday,
        ):
            self.setWeekdayTextFormat(day, weekday_format)

        self.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, weekend_format)
        self.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, weekend_format)

    def _apply_theme_stylesheet(self):
        if self._closing:
            return
        ui = build_ui_tokens(theme_manager.current_theme)
        font = ui["font"]
        radius = ui["radius"]
        control = ui["control"]
        surface = ui["surface"]
        border = ui["border"]
        text = ui["text"]

        nav_button_size = max(control["button_height"] + 2, 34)
        nav_height = max(control["toolbar_button_height"], 36)

        self._apply_weekday_formats()
        self.setStyleSheet(
            f"""
            QCalendarWidget {{
                background: transparent;
                border: none;
            }}
            QCalendarWidget QWidget {{
                alternate-background-color: transparent;
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background: transparent;
                border: none;
                padding: 0 0 8px 0;
                margin: 0 0 8px 0;
                min-height: {nav_height}px;
            }}
            QCalendarWidget QToolButton {{
                background: transparent;
                border: none;
                border-radius: {radius["sm"]}px;
                color: {text["secondary"]};
                font-size: {font["size_sm"]}px;
                font-weight: {font["weight_medium"]};
                min-height: {nav_button_size}px;
                padding: 0 6px;
            }}
            QCalendarWidget QToolButton:hover {{
                color: {text["primary"]};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{
                background-color: {surface["soft"]};
                border: 1px solid {border["subtle"]};
                border-radius: {radius["md"]}px;
                min-width: {nav_button_size}px;
                max-width: {nav_button_size}px;
                padding: 0;
                font-size: {font["size_md"]}px;
                font-weight: {font["weight_semibold"]};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth:hover,
            QCalendarWidget QToolButton#qt_calendar_nextmonth:hover {{
                background-color: {surface["hover"]};
                border: 1px solid {border["accent"]};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth:pressed,
            QCalendarWidget QToolButton#qt_calendar_nextmonth:pressed {{
                background-color: {surface["toolbar"]};
            }}
            QCalendarWidget QToolButton#qt_calendar_monthbutton,
            QCalendarWidget QToolButton#qt_calendar_yearbutton {{
                color: {text["primary"]};
                font-size: {font["size_lg"]}px;
                font-weight: {font["weight_bold"]};
                padding: 0 6px;
                min-width: 0;
            }}
            QCalendarWidget QToolButton#qt_calendar_monthbutton::menu-indicator,
            QCalendarWidget QToolButton#qt_calendar_yearbutton::menu-indicator {{
                image: none;
                width: 0px;
                subcontrol-position: right center;
            }}
            QCalendarWidget QAbstractItemView {{
                background: transparent;
                color: {text["primary"]};
                border: none;
                outline: none;
                selection-background-color: transparent;
                selection-color: {text["bright"]};
            }}
            QCalendarWidget QAbstractItemView::item:disabled {{
                color: {_c("TEXT_DISABLED")};
            }}
            QCalendarWidget QWidget#qt_calendar_calendarview,
            QCalendarWidget QTableView {{
                background: transparent;
                border: none;
                outline: none;
                selection-background-color: transparent;
            }}
            QCalendarWidget QTableView QHeaderView::section {{
                background: transparent;
                color: {text["header"]};
                border: none;
                padding: 0 0 10px 0;
                font-size: {font["size_sm"]}px;
                font-weight: {font["weight_semibold"]};
            }}
            QCalendarWidget QSpinBox {{
                background-color: {_c("BG_INPUT")};
                color: {_c("TEXT_PRIMARY")};
                border: 1px solid {_c("BORDER_STRONG")};
                border-radius: {radius["sm"]}px;
                padding: 0 8px;
                min-height: {control["input_height"]}px;
            }}
            """
        )
        self.updateCells()
        self.update()

    def _dispose(self) -> None:
        self._closing = True
        try:
            theme_manager.sig_theme_changed.disconnect(self._apply_theme_stylesheet)
        except (RuntimeError, TypeError):
            pass

    def closeEvent(self, event):
        self._dispose()
        super().closeEvent(event)

    def deleteLater(self):
        self._dispose()
        super().deleteLater()

    def paintCell(self, painter, rect, date):
        if self._closing:
            return
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)

        calendar_tokens = build_ui_tokens(theme_manager.current_theme)["calendar"]
        current_month = self.monthShown()
        current_year = self.yearShown()
        is_current_month = date.month() == current_month and date.year() == current_year
        is_selected = date == self.selectedDate()
        is_today = date == QDate.currentDate()
        is_trade_day = MarketCalendar.is_trade_day(date.toPyDate(), "CN")

        painter.fillRect(rect, QColor(str(calendar_tokens["cell_fill"])))

        chip_rect = QRectF(rect.adjusted(6, 5, -6, -5))
        if chip_rect.width() < 14 or chip_rect.height() < 14:
            chip_rect = QRectF(rect.adjusted(3, 3, -3, -3))

        fill_color = None
        border_color = None
        if is_selected:
            fill_color = QColor(str(calendar_tokens["selected_color"]))
            fill_color.setAlpha(int(calendar_tokens["selected_bg_alpha"]))
            border_color = QColor(str(calendar_tokens["selected_color"]))
            border_color.setAlpha(int(calendar_tokens["selected_border_alpha"]))
        elif is_today and is_current_month:
            fill_color = QColor(str(calendar_tokens["today_color"]))
            fill_color.setAlpha(int(calendar_tokens["today_bg_alpha"]))
            border_color = QColor(str(calendar_tokens["today_color"]))
            border_color.setAlpha(int(calendar_tokens["today_border_alpha"]))

        if fill_color is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(chip_rect, 10, 10)

        if border_color is not None:
            painter.setPen(QPen(border_color, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(chip_rect.adjusted(0.6, 0.6, -0.6, -0.6), 10, 10)

        if is_selected:
            text_color = QColor(_c("TEXT_BRIGHT"))
        elif is_current_month and not is_trade_day:
            text_color = QColor(str(calendar_tokens["non_trade_color"]))
            text_color.setAlpha(int(calendar_tokens["non_trade_text_alpha"]))
        elif is_current_month:
            text_color = QColor(_c("TEXT_PRIMARY"))
        else:
            text_color = QColor(_c("TEXT_DISABLED"))

        font = QFont()
        font.setPointSize(10)
        font.setBold(is_selected or is_today)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))

        if is_today and not is_selected and is_current_month:
            dot_color = QColor(str(calendar_tokens["today_color"]))
            dot_color.setAlpha(int(calendar_tokens["today_dot_alpha"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            dot_rect = QRectF(
                chip_rect.center().x() - 2.2,
                chip_rect.bottom() - 6.0,
                4.4,
                4.4,
            )
            painter.drawEllipse(dot_rect)

        day_events = self.earnings_events_for_date(date)
        if day_events and is_current_month:
            marker = self.earnings_marker_policy(day_events)
            dot_tones = list(marker.get("dot_tones", []))
            if dot_tones:
                marker_styles = _priority_marker_styles(calendar_tokens)
                dot_y = rect.center().y() + 13.0
                first_x = rect.center().x() - ((len(dot_tones) - 1) * 4.2)
                painter.setPen(Qt.PenStyle.NoPen)
                for idx, tone in enumerate(dot_tones):
                    style = marker_styles.get(str(tone), marker_styles["normal"])
                    dot_color = QColor(str(style["color"]))
                    dot_color.setAlpha(int(style["alpha"]))
                    painter.setBrush(dot_color)
                    painter.drawEllipse(QRectF(first_x + idx * 8.4 - 2.2, dot_y, 4.4, 4.4))

        painter.restore()


class EarningsCalendarRefreshWorker(QThread):
    sig_result = pyqtSignal(object)
    sig_error = pyqtSignal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service

    def run(self) -> None:
        try:
            self.sig_result.emit(self._service.refresh_events())
        except Exception as exc:  # noqa: BLE001 - keep UI worker from killing the dialog thread
            self.sig_error.emit(str(exc))


class OligarchEarningsCalendarPanel(QFrame):
    eventsChanged = pyqtSignal(object)

    FILTER_MODES = (
        ("7d", "\u672a\u67657\u5929"),
        ("30d", "\u672a\u676530\u5929"),
        ("super_giant", "\u8d85\u7ea7\u5de8\u5934"),
        ("strategic_giant", "\u6218\u7565\u6838\u5fc3"),
        ("all", "\u5168\u90e8\u8d5b\u9053"),
    )

    def __init__(self, parent=None, *, events: list[EarningsCalendarEvent] | None = None, service=None):
        super().__init__(parent)
        self._closing = False
        self._events = list(events or [])
        self._service = service
        self._filter_mode = "30d"
        self._selected_date = ""
        self._refresh_worker = None
        self.setObjectName("oligarchEarningsPanel")
        self._init_ui()
        self._apply_theme()
        theme_manager.sig_theme_changed.connect(self._apply_theme)
        self._rebuild_cards()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title_label = QLabel("\u5be1\u5934\u8d22\u62a5\u65e5\u5386", self)
        self.title_label.setObjectName("earningsPanelTitle")
        header.addWidget(self.title_label, 1)

        self.btn_refresh = QToolButton(self)
        self.btn_refresh.setObjectName("earningsRefreshButton")
        self.btn_refresh.setText("\u21bb")
        self.btn_refresh.setToolTip("\u5237\u65b0\u5be1\u5934\u8d22\u62a5\u65e5\u5386")
        self.btn_refresh.clicked.connect(self.refresh_from_service)
        header.addWidget(self.btn_refresh, 0)
        layout.addLayout(header)

        self.search_box = QLineEdit(self)
        self.search_box.setObjectName("earningsSearchBox")
        self.search_box.setPlaceholderText("\u641c\u7d22\u516c\u53f8\u6216Ticker...")
        self.search_box.textChanged.connect(self._rebuild_cards)
        layout.addWidget(self.search_box)

        segment_row = QHBoxLayout()
        segment_row.setContentsMargins(0, 0, 0, 0)
        segment_row.setSpacing(4)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        for mode, label in self.FILTER_MODES:
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setObjectName("earningsSegmentButton")
            button.clicked.connect(lambda checked=False, mode=mode: self.set_filter_mode(mode))
            self._mode_group.addButton(button)
            self._mode_buttons[mode] = button
            segment_row.addWidget(button)
        self._mode_buttons[self._filter_mode].setChecked(True)
        layout.addLayout(segment_row)

        self.summary_label = QLabel("", self)
        self.summary_label.setObjectName("earningsSummaryLabel")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("earningsEventScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget(self.scroll)
        self.cards_host.setObjectName("earningsCardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.scroll.setWidget(self.cards_host)
        layout.addWidget(self.scroll, 1)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("earningsPanelStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _apply_theme(self) -> None:
        if self._closing:
            return
        tokens = build_ui_tokens(theme_manager.current_theme)
        t = tokens["theme"]
        self.setStyleSheet(
            f"""
            QFrame#oligarchEarningsPanel {{
                background: {tokens["surface"]["panel"]};
                border: 1px solid {tokens["border"]["subtle"]};
                border-radius: {tokens["radius"]["md"]}px;
            }}
            QLabel#earningsPanelTitle {{
                color: {tokens["text"]["primary"]};
                font-size: {tokens["font"]["size_lg"]}px;
                font-weight: {tokens["font"]["weight_bold"]};
            }}
            QToolButton#earningsRefreshButton {{
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                border-radius: {tokens["radius"]["sm"]}px;
                border: 1px solid {tokens["border"]["default"]};
                background: {tokens["surface"]["soft"]};
                color: {tokens["text"]["secondary"]};
                font-size: {tokens["font"]["size_lg"]}px;
            }}
            QToolButton#earningsRefreshButton:hover {{
                border-color: {tokens["border"]["accent"]};
                background: {tokens["surface"]["hover"]};
                color: {tokens["text"]["primary"]};
            }}
            QLineEdit#earningsSearchBox {{
                min-height: {tokens["control"]["input_height"]}px;
                border-radius: {tokens["radius"]["sm"]}px;
                border: 1px solid {tokens["border"]["default"]};
                background: {tokens["surface"]["input"]};
                color: {tokens["text"]["primary"]};
                padding: 0 10px;
            }}
            QPushButton#earningsSegmentButton {{
                min-height: {tokens["control"]["segment_height"]}px;
                border-radius: {tokens["radius"]["sm"]}px;
                border: 1px solid {tokens["border"]["subtle"]};
                background: {tokens["surface"]["soft"]};
                color: {tokens["text"]["secondary"]};
                padding: 0 8px;
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QPushButton#earningsSegmentButton:checked {{
                border-color: {t.get("SEGMENT_ACTIVE_BORDER", t["COLOR_INFO"])};
                color: {t.get("SEGMENT_ACTIVE_TEXT", tokens["text"]["primary"])};
                background: {t.get("SEGMENT_ACTIVE_BG", t["BG_HOVER"])};
            }}
            QScrollArea#earningsEventScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#earningsCardsHost {{
                background: transparent;
            }}
            QLabel#earningsPanelStatus {{
                color: {tokens["text"]["muted"]};
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QLabel#earningsSummaryLabel {{
                min-height: 24px;
                border-radius: {tokens["radius"]["sm"]}px;
                border: 1px solid {tokens["border"]["subtle"]};
                background: {tokens["surface"]["input"]};
                color: {tokens["text"]["secondary"]};
                padding: 0 9px;
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QWidget#earningsDateGroup {{
                background: transparent;
            }}
            QLabel#earningsDateGroupTitle {{
                color: {tokens["text"]["primary"]};
                font-size: {tokens["font"]["size_sm"]}px;
                font-weight: {tokens["font"]["weight_bold"]};
            }}
            QLabel#earningsDateGroupCount {{
                min-width: 32px;
                border-radius: {tokens["radius"]["xs"]}px;
                background: {tokens["surface"]["soft"]};
                color: {tokens["text"]["muted"]};
                padding: 2px 7px;
                font-size: {tokens["font"]["size_xs"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QFrame#earningsEventCard {{
                background: {tokens["surface"]["input"]};
                border: 1px solid {tokens["border"]["subtle"]};
                border-radius: {tokens["radius"]["sm"]}px;
            }}
            QLabel#earningsEventTicker {{
                color: {tokens["text"]["primary"]};
                font-size: {tokens["font"]["size_md"]}px;
                font-weight: {tokens["font"]["weight_bold"]};
            }}
            QLabel#earningsEventCompany {{
                color: {tokens["text"]["secondary"]};
                font-size: {tokens["font"]["size_sm"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#earningsEventMeta {{
                color: {tokens["text"]["muted"]};
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QLabel#earningsEventTimeExact {{
                color: {tokens["text"]["primary"]};
                font-size: {tokens["font"]["size_xs"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#earningsEventTimePending {{
                color: {tokens["text"]["secondary"]};
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QLabel#earningsEventSource {{
                color: {tokens["text"]["muted"]};
                font-size: {tokens["font"]["size_xs"]}px;
            }}
            QLabel#earningsEventBadgeConfirmed {{
                color: {tokens["state"]["success"]["fg"]};
                background: {tokens["state"]["success"]["bg"]};
                border: 1px solid {tokens["state"]["success"]["border"]};
                border-radius: {tokens["radius"]["xs"]}px;
                padding: 2px 7px;
                font-size: {tokens["font"]["size_xs"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#earningsEventBadgeBroad {{
                color: {tokens["state"]["warning"]["fg"]};
                background: {tokens["state"]["warning"]["bg"]};
                border: 1px solid {tokens["state"]["warning"]["border"]};
                border-radius: {tokens["radius"]["xs"]}px;
                padding: 2px 7px;
                font-size: {tokens["font"]["size_xs"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#earningsEventBadgePending {{
                color: {tokens["text"]["secondary"]};
                background: {tokens["surface"]["soft"]};
                border: 1px solid {tokens["border"]["subtle"]};
                border-radius: {tokens["radius"]["xs"]}px;
                padding: 2px 7px;
                font-size: {tokens["font"]["size_xs"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#earningsEmptyState {{
                color: {tokens["text"]["muted"]};
                font-size: {tokens["font"]["size_sm"]}px;
                padding: 18px 4px;
            }}
            """
        )

    def set_events(self, events: list[EarningsCalendarEvent]) -> None:
        if self._closing:
            return
        self._events = list(events or [])
        self._rebuild_cards()
        self.eventsChanged.emit(list(self._events))

    def set_selected_date(self, date_text: str) -> None:
        if self._closing:
            return
        self._selected_date = str(date_text or "").strip()[:10]
        self._rebuild_cards()

    def set_filter_mode(self, mode: str) -> None:
        if self._closing:
            return
        if mode not in dict(self.FILTER_MODES):
            mode = "30d"
        self._filter_mode = mode
        self._selected_date = ""
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)
        self._rebuild_cards()

    def filtered_events(self) -> list[EarningsCalendarEvent]:
        events = list(self._events)
        today = _dt.date.today()
        if self._selected_date:
            events = [event for event in events if event_calendar_date(event) == self._selected_date]
        elif self._filter_mode in {"7d", "30d"}:
            days = 7 if self._filter_mode == "7d" else 30
            end = today + _dt.timedelta(days=days)
            filtered = []
            for event in events:
                try:
                    event_day = _dt.date.fromisoformat(event_calendar_date(event)[:10])
                except ValueError:
                    continue
                if today <= event_day <= end:
                    filtered.append(event)
            events = filtered
        elif self._filter_mode == "super_giant":
            events = [event for event in events if event.priority == "super_giant"]
        elif self._filter_mode == "strategic_giant":
            events = [event for event in events if event.priority == "strategic_giant"]

        query = self.search_box.text().strip().lower() if hasattr(self, "search_box") else ""
        if query:
            events = [
                event
                for event in events
                if query in event.ticker.lower() or query in event.company.lower() or query in event.sector.lower()
            ]
        return sorted_events(events)

    def grouped_events(self) -> list[tuple[str, list[EarningsCalendarEvent]]]:
        groups: list[tuple[str, list[EarningsCalendarEvent]]] = []
        group_lookup: dict[str, list[EarningsCalendarEvent]] = {}
        for event in self.filtered_events():
            day = event_calendar_date(event)
            if not day:
                continue
            if day not in group_lookup:
                group_lookup[day] = []
                groups.append((day, group_lookup[day]))
            group_lookup[day].append(event)
        return groups

    def _clear_cards(self) -> None:
        if self._closing:
            return
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _format_event_line(event: EarningsCalendarEvent) -> str:
        parts = []
        priority_label = _PRIORITY_LABELS.get(str(event.priority or "").strip())
        if priority_label:
            parts.append(priority_label)
        parts.append(event.sector)
        if event.market:
            parts.append(event.market)
        if event.fiscal_period:
            parts.append(event.fiscal_period)
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _format_time_line(event: EarningsCalendarEvent) -> str:
        if event.beijing_time:
            return f"\u5317\u4eac {event.beijing_time}"
        if is_yfinance_date_conflict_event(event):
            return "Yahoo Finance \u4f30\u7b97\u65e5\u671f\u51b2\u7a81\uff5c\u8bf7\u4ee5\u5b98\u65b9\u62ab\u9732\u4e3a\u51c6"
        if is_yfinance_estimate_event(event):
            return "Yahoo Finance \u4f30\u7b97\u65e5\u671f\uff5c\u5b98\u65b9\u672a\u786e\u8ba4"
        time_label = str(event.time_label or "").strip()
        if time_label and time_label not in {"\u5f85\u786e\u8ba4", "\u672a\u77e5", "-"}:
            return f"{time_label}\uff5c\u5177\u4f53\u65f6\u523b\u5f85\u786e\u8ba4"
        return "\u4ec5\u65e5\u671f\uff5c\u65f6\u95f4\u5f85\u786e\u8ba4"

    def _event_status_text(self, event: EarningsCalendarEvent) -> str:
        if event.status == "confirmed":
            return "\u786e\u8ba4"
        if is_yfinance_date_conflict_event(event):
            return "\u65e5\u671f\u51b2\u7a81"
        if is_yfinance_estimate_event(event):
            return "\u4f30\u7b97"
        if event.beijing_time:
            return "\u7cbe\u786e"
        if event.source == "\u793a\u4f8b":
            return "\u793a\u4f8b"
        time_label = str(event.time_label or "").strip()
        if time_label and time_label not in {"\u5f85\u786e\u8ba4", "\u672a\u77e5", "-"}:
            return time_label
        return "\u5f85\u786e\u8ba4"

    def _event_badge_object_name(self, event: EarningsCalendarEvent) -> str:
        if is_yfinance_estimate_event(event):
            return "earningsEventBadgePending"
        if event.status == "confirmed" or event.beijing_time:
            return "earningsEventBadgeConfirmed"
        time_label = str(event.time_label or "").strip()
        if time_label and time_label not in {"\u5f85\u786e\u8ba4", "\u672a\u77e5", "-"}:
            return "earningsEventBadgeBroad"
        return "earningsEventBadgePending"

    @staticmethod
    def _format_source_text(event: EarningsCalendarEvent) -> str:
        source_text = event.source or "\u672a\u77e5\u6765\u6e90"
        source_type = str(event.call_time_source_type or "").strip()
        if source_type:
            source_type_label = _SOURCE_TYPE_LABELS.get(source_type, source_type)
            if source_type_label not in source_text:
                source_text = f"{source_text}\uff5c{source_type_label}"
        if is_yfinance_date_conflict_event(event):
            return f"{source_text}\uff08\u4f30\u7b97\u51b2\u7a81\uff0c\u975e\u5b98\u65b9\uff09"
        if is_yfinance_estimate_event(event):
            return f"{source_text}\uff08\u4f30\u7b97\uff0c\u975e\u5b98\u65b9\uff09"
        return source_text

    @staticmethod
    def _format_group_title(day: str) -> str:
        try:
            date_value = _dt.date.fromisoformat(day[:10])
        except ValueError:
            return day
        weekdays = (
            "\u5468\u4e00",
            "\u5468\u4e8c",
            "\u5468\u4e09",
            "\u5468\u56db",
            "\u5468\u4e94",
            "\u5468\u516d",
            "\u5468\u65e5",
        )
        return f"{date_value:%m-%d} {weekdays[date_value.weekday()]}"

    @staticmethod
    def _time_precision_counts(events: list[EarningsCalendarEvent]) -> tuple[int, int, int]:
        exact = sum(1 for event in events if event.beijing_time)
        broad = sum(
            1
            for event in events
            if not event.beijing_time
            and str(event.time_label or "").strip()
            and str(event.time_label or "").strip() not in {"\u5f85\u786e\u8ba4", "\u672a\u77e5", "-"}
        )
        date_only = max(0, len(events) - exact - broad)
        return exact, broad, date_only

    def _create_group_header(self, day: str, events: list[EarningsCalendarEvent]) -> QWidget:
        group = QWidget(self.cards_host)
        group.setObjectName("earningsDateGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(self._format_group_title(day), group)
        title.setObjectName("earningsDateGroupTitle")
        header.addWidget(title, 0)
        count = QLabel(f"{len(events)} \u5bb6", group)
        count.setObjectName("earningsDateGroupCount")
        header.addWidget(count, 0)
        header.addStretch(1)
        layout.addLayout(header)
        return group

    def _create_event_card(self, event: EarningsCalendarEvent) -> QFrame:
        card = QFrame(self.cards_host)
        card.setObjectName("earningsEventCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        ticker_label = QLabel(event.ticker, card)
        ticker_label.setObjectName("earningsEventTicker")
        top.addWidget(ticker_label, 0)
        company_label = QLabel(event.company, card)
        company_label.setObjectName("earningsEventCompany")
        company_label.setWordWrap(False)
        top.addWidget(company_label, 1)
        badge = QLabel(self._event_status_text(event), card)
        badge.setObjectName(self._event_badge_object_name(event))
        top.addWidget(badge, 0)
        layout.addLayout(top)

        meta_label = QLabel(self._format_event_line(event), card)
        meta_label.setObjectName("earningsEventMeta")
        meta_label.setWordWrap(True)
        layout.addWidget(meta_label)

        time_label = QLabel(self._format_time_line(event), card)
        time_label.setObjectName("earningsEventTimeExact" if event.beijing_time else "earningsEventTimePending")
        time_label.setWordWrap(True)
        layout.addWidget(time_label)

        source_text = self._format_source_text(event)
        source_label = QLabel(f"\u6765\u6e90 {source_text}", card)
        source_label.setObjectName("earningsEventSource")
        source_label.setWordWrap(True)
        layout.addWidget(source_label)
        return card

    def _rebuild_cards(self, *_args) -> None:
        if self._closing or not hasattr(self, "cards_layout"):
            return
        self._clear_cards()
        groups = self.grouped_events()
        events = [event for _, group_events in groups for event in group_events]
        if groups:
            for day, group_events in groups:
                group = self._create_group_header(day, group_events)
                group_layout = group.layout()
                if isinstance(group_layout, QVBoxLayout):
                    for event in group_events:
                        group_layout.addWidget(self._create_event_card(event))
                self.cards_layout.addWidget(group)
        else:
            empty_label = QLabel("\u6ca1\u6709\u5339\u914d\u7684\u8d22\u62a5\u4e8b\u4ef6", self.cards_host)
            empty_label.setObjectName("earningsEmptyState")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(empty_label)
        self.cards_layout.addStretch(1)

        exact_count, broad_count, date_only_count = self._time_precision_counts(events)
        self.summary_label.setText(
            f"\u663e\u793a {len(events)} \u5bb6\uff5c\u5317\u4eac\u65f6\u95f4 {exact_count}\uff5c"
            f"\u76d8\u524d/\u76d8\u540e {broad_count}\uff5c\u4ec5\u65e5\u671f {date_only_count}"
        )

        source_parts = []
        if self._events:
            sources = sorted({event.source or "\u672a\u77e5" for event in self._events})
            source_parts.append("\u6765\u6e90: " + " + ".join(sources))
            yfinance_estimates = sum(1 for event in self._events if is_yfinance_estimate_event(event))
            if yfinance_estimates:
                source_parts.append(f"Yahoo\u4f30\u7b97\u672a\u786e\u8ba4 {yfinance_estimates}")
            universe_count = len(getattr(getattr(self, "_service", None), "universe", {}) or {})
            if universe_count:
                covered = len({event.ticker for event in self._events})
                source_parts.append(f"\u8986\u76d6 {covered}/{universe_count}")
        else:
            source_parts.append("\u6682\u65e0\u771f\u5b9e\u8d22\u62a5\u65e5\u5386\u6570\u636e")
        source_parts.append(f"\u663e\u793a {len(events)}/{len(self._events)}")
        if events:
            source_parts.append(f"\u7cbe\u786e\u65f6\u95f4 {exact_count}/{len(events)}")
        self.status_label.setText("\uff5c".join(source_parts))

    def refresh_from_service(self) -> None:
        if self._closing:
            return
        if self._service is None:
            self._rebuild_cards()
            return
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.status_label.setText(
            "\u6b63\u5728\u62c9\u53d6\u5be1\u5934\u5b57\u5178\u5168\u5e02\u573a\u8d22\u62a5\u65e5\u5386..."
        )
        worker = EarningsCalendarRefreshWorker(self._service)
        self._refresh_worker = worker
        worker.sig_result.connect(self._on_refresh_result)
        worker.sig_error.connect(self._on_refresh_error)
        worker.finished.connect(self._on_refresh_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_refresh_result(self, events) -> None:
        if self._closing:
            return
        self.set_events(list(events or []))

    def _on_refresh_error(self, message: str) -> None:
        if self._closing:
            return
        self.status_label.setText(f"\u5237\u65b0\u5931\u8d25: {message}")

    def _on_refresh_finished(self) -> None:
        if self._closing:
            return
        self.btn_refresh.setEnabled(True)
        self._refresh_worker = None

    def reload_from_service_cache(self) -> None:
        if self._closing:
            return
        if self._service is None:
            return
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            return
        try:
            events = self._service.load_events(allow_network=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        self.set_events(list(events or []))

    def _dispose(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            theme_manager.sig_theme_changed.disconnect(self._apply_theme)
        except (RuntimeError, TypeError):
            pass
        worker = self._refresh_worker
        self._refresh_worker = None
        if worker is None:
            return
        for signal_name, slot in (
            ("sig_result", self._on_refresh_result),
            ("sig_error", self._on_refresh_error),
            ("finished", self._on_refresh_finished),
        ):
            signal = getattr(worker, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            worker.setParent(None)
        except (RuntimeError, TypeError):
            pass
        try:
            if worker.isRunning():
                worker.requestInterruption()
                _remember_detached_refresh_worker(worker)
            else:
                worker.deleteLater()
        except (RuntimeError, TypeError):
            pass

    def closeEvent(self, event):
        self._dispose()
        super().closeEvent(event)

    def deleteLater(self):
        self._dispose()
        super().deleteLater()


class TradeDateEdit(QDateEdit):
    def __init__(
        self,
        parent=None,
        *,
        display_format: str = "yyyy-MM-dd",
        date: QDate | None = None,
        fixed_width: int | None = None,
    ):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self._trade_calendar = TradeCalendarWidget(self)
        self.setCalendarWidget(self._trade_calendar)
        self.setDisplayFormat(display_format)
        if date is not None:
            self.setDate(date)
        if fixed_width:
            self.setFixedWidth(fixed_width)
