# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QCalendarWidget
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QFont, QPen

from core.market_calendar import MarketCalendar
from ui.theme import theme_manager

def _c(token: str) -> str:
    return theme_manager.get(token)

class TradeCalendarWidget(QCalendarWidget):
    """完全自绘日历，彻底绕过 Qt 内部的颜色机智，统一节假日显示样式"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QCalendarWidget QWidget { alternate-background-color: transparent; }")
        # 隐藏周数侧边栏，释放水平空间解决 "..." 折叠问题
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)

    def paintCell(self, painter, rect, date):
        """完全接管单元格的绘制"""
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)

        current_month = self.monthShown()
        current_year = self.yearShown()
        is_current_month = (date.month() == current_month and date.year() == current_year)
        is_selected = (date == self.selectedDate())
        is_today = (date == QDate.currentDate())
        
        is_trade_day = MarketCalendar.is_trade_day(date.toPyDate(), "CN")

        # ── 1. 底色 ──
        bg_color = QColor(_c("BG_TABLE_BASE"))
        if is_selected:
            bg_color = QColor(_c("BRAND_PRIMARY"))
            bg_color.setAlpha(45)
        elif is_today and is_current_month:
            bg_color = QColor(_c("BG_HOVER"))
        painter.fillRect(rect, bg_color)

        # ── 2. 今日圆环标识 ──
        if is_today and is_current_month:
            ring_color = QColor(_c("BRAND_PRIMARY"))
            ring_color.setAlpha(180)
            pen = QPen(ring_color, 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = rect.adjusted(3, 3, -3, -3)
            painter.drawRoundedRect(inset, 4, 4)

        # ── 3. 文字颜色（统一节假日样式） ──
        if is_selected:
            text_color = QColor(_c("TEXT_BRIGHT"))
        elif is_current_month:
            if not is_trade_day:
                # 统一节假日颜色（不刺眼的红色/休市提示色）
                text_color = QColor(_c("ERROR"))
                text_color.setAlpha(200)
            else:
                text_color = QColor(_c("TEXT_PRIMARY"))
        else:
            # 非本月日期用暗淡色
            text_color = QColor(_c("TEXT_DISABLED"))

        font = QFont()
        font.setPointSize(10)
        if is_today and is_current_month:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))

        painter.restore()
