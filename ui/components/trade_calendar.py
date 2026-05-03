# -*- coding: utf-8 -*-
from PyQt6.QtCore import QDate, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPen, QTextCharFormat
from PyQt6.QtWidgets import QCalendarWidget, QDateEdit, QTableView

from app.services.ui_runtime_service import MarketCalendar
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def _c(token: str) -> str:
    return theme_manager.get(token)


class TradeCalendarWidget(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setGridVisible(False)
        self._configure_weekday_header()
        self._apply_theme_stylesheet()
        theme_manager.sig_theme_changed.connect(self._apply_theme_stylesheet)

    def _configure_weekday_header(self):
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames
        )

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
                border-radius: {radius['sm']}px;
                color: {text['secondary']};
                font-size: {font['size_sm']}px;
                font-weight: {font['weight_medium']};
                min-height: {nav_button_size}px;
                padding: 0 6px;
            }}
            QCalendarWidget QToolButton:hover {{
                color: {text['primary']};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{
                background-color: {surface['soft']};
                border: 1px solid {border['subtle']};
                border-radius: {radius['md']}px;
                min-width: {nav_button_size}px;
                max-width: {nav_button_size}px;
                padding: 0;
                font-size: {font['size_md']}px;
                font-weight: {font['weight_semibold']};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth:hover,
            QCalendarWidget QToolButton#qt_calendar_nextmonth:hover {{
                background-color: {surface['hover']};
                border: 1px solid {border['accent']};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth:pressed,
            QCalendarWidget QToolButton#qt_calendar_nextmonth:pressed {{
                background-color: {surface['toolbar']};
            }}
            QCalendarWidget QToolButton#qt_calendar_monthbutton,
            QCalendarWidget QToolButton#qt_calendar_yearbutton {{
                color: {text['primary']};
                font-size: {font['size_lg']}px;
                font-weight: {font['weight_bold']};
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
                color: {text['primary']};
                border: none;
                outline: none;
                selection-background-color: transparent;
                selection-color: {text['bright']};
            }}
            QCalendarWidget QAbstractItemView::item:disabled {{
                color: {_c('TEXT_DISABLED')};
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
                color: {text['header']};
                border: none;
                padding: 0 0 10px 0;
                font-size: {font['size_sm']}px;
                font-weight: {font['weight_semibold']};
            }}
            QCalendarWidget QSpinBox {{
                background-color: {_c('BG_INPUT')};
                color: {_c('TEXT_PRIMARY')};
                border: 1px solid {_c('BORDER_STRONG')};
                border-radius: {radius['sm']}px;
                padding: 0 8px;
                min-height: {control['input_height']}px;
            }}
            """
        )

    def paintCell(self, painter, rect, date):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)

        is_dark = theme_manager.is_dark()
        current_month = self.monthShown()
        current_year = self.yearShown()
        is_current_month = date.month() == current_month and date.year() == current_year
        is_selected = date == self.selectedDate()
        is_today = date == QDate.currentDate()
        is_trade_day = MarketCalendar.is_trade_day(date.toPyDate(), "CN")

        painter.fillRect(rect, QColor(_c("BG_INPUT")))

        chip_rect = QRectF(rect.adjusted(6, 5, -6, -5))
        if chip_rect.width() < 14 or chip_rect.height() < 14:
            chip_rect = QRectF(rect.adjusted(3, 3, -3, -3))

        fill_color = None
        border_color = None
        if is_selected:
            fill_color = QColor(_c("COLOR_INFO"))
            fill_color.setAlpha(42 if is_dark else 28)
            border_color = QColor(_c("COLOR_INFO"))
            border_color.setAlpha(184 if is_dark else 150)
        elif is_today and is_current_month:
            fill_color = QColor(_c("COLOR_INFO"))
            fill_color.setAlpha(16 if is_dark else 10)
            border_color = QColor(_c("COLOR_INFO"))
            border_color.setAlpha(148 if is_dark else 116)

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
            text_color = QColor(_c("COLOR_ERROR"))
            text_color.setAlpha(208 if is_dark else 180)
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
            dot_color = QColor(_c("COLOR_INFO"))
            dot_color.setAlpha(220 if is_dark else 180)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            dot_rect = QRectF(
                chip_rect.center().x() - 2.2,
                chip_rect.bottom() - 6.0,
                4.4,
                4.4,
            )
            painter.drawEllipse(dot_rect)

        painter.restore()


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
