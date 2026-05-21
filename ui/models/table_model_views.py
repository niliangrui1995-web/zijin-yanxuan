# -*- coding: utf-8 -*-
"""Proxy and delegate classes for table views."""

from __future__ import annotations

import time

from PyQt6.QtCore import QMimeData, QModelIndex, QRect, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ui.components import SearchFilter
from ui.models.table_model_helpers import (
    FLASH_DURATION_SECONDS,
    SERIAL_HEADER,
    _c,
    _qcolor_from_token,
    _theme_table_tokens,
)


class RtSortFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self._filter_text = ""
        self._exact_column_filters = {}

    def setColumnFilter(self, col_name, text):
        self.setColumnFilters(col_name, [text] if text else [])

    def setColumnFilters(self, col_name, values):
        normalized = {str(value or "").strip() for value in (values or []) if str(value or "").strip()}
        if normalized:
            self._exact_column_filters[col_name] = normalized
        else:
            self._exact_column_filters.pop(col_name, None)
        self.invalidateFilter()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        source = self.sourceModel()
        header = (
            source.headerData(index.column(), Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            if source
            else None
        )
        if header == SERIAL_HEADER:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(index.row() + 1)
            if role == Qt.ItemDataRole.UserRole:
                return index.row() + 1
            if role == Qt.ItemDataRole.ToolTipRole:
                return None
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_c("TEXT_SECONDARY"))
        return super().data(index, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column >= 0:
            source = self.sourceModel()
            header = (
                source.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) if source else None
            )
            if header == SERIAL_HEADER:
                return
        super().sort(column, order)

    def lessThan(self, left, right):
        leftData = self.sourceModel().data(left, Qt.ItemDataRole.UserRole)
        rightData = self.sourceModel().data(right, Qt.ItemDataRole.UserRole)

        # fallback to DisplayRole if UserRole is standard string
        if leftData is None or rightData is None:
            leftData = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole)
            rightData = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole)

        left_str = str(leftData).strip()
        right_str = str(rightData).strip()

        # Handle placeholders explicitly to sort them at the bottom
        if left_str in ("", "--", "-"):
            left_val = float("-inf")
        else:
            try:
                left_val = float(leftData)
            except (ValueError, TypeError):
                left_val = None

        if right_str in ("", "--", "-"):
            right_val = float("-inf")
        else:
            try:
                right_val = float(rightData)
            except (ValueError, TypeError):
                right_val = None

        if left_val is not None and right_val is not None:
            return left_val < right_val

        return left_str < right_str

    def setFilterText(self, text):
        self._filter_text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()

        # 1. 拦截层：表头精确定向筛选（模拟 Excel 表头筛选）
        if getattr(self, "_exact_column_filters", None):
            headers = model._headers if hasattr(model, "_headers") else []
            for col_name, patterns in self._exact_column_filters.items():
                if col_name in headers:
                    col_idx = headers.index(col_name)
                    idx = model.index(source_row, col_idx, source_parent)
                    val = str(model.data(idx, Qt.ItemDataRole.DisplayRole) or "")
                    pattern_set = patterns if isinstance(patterns, set) else {str(patterns or "").strip()}
                    pattern_set = {pattern for pattern in pattern_set if pattern}
                    if pattern_set and not any(pattern in val for pattern in pattern_set):
                        return False

        # 2. 全局拼音搜索层
        if not self._filter_text:
            return True

        headers = model._headers if hasattr(model, "_headers") else []
        code_col = headers.index("代码") if "代码" in headers else 0
        name_col = headers.index("名称") if "名称" in headers else 1
        code_idx = model.index(source_row, code_col, source_parent)
        name_idx = model.index(source_row, name_col, source_parent)

        c_text = str(model.data(code_idx, Qt.ItemDataRole.DisplayRole) or "").lower()
        n_text = str(model.data(name_idx, Qt.ItemDataRole.DisplayRole) or "").lower()

        if SearchFilter.match_pinyin_or_text(self._filter_text, c_text, n_text):
            return True

        # fallback to scan all cols if columns shifted
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            text = str(model.data(idx, Qt.ItemDataRole.DisplayRole) or "").lower()
            if self._filter_text in text:
                return True

        return False

    def mimeTypes(self):
        """确保 proxy 层声明的拖拽 MIME 类型与 source model 一致"""
        return ["application/x-watchlist-row"]

    def canDropMimeData(self, data, action, row, column, parent):
        """
        【关键修复】Qt 默认的 QSortFilterProxyModel.canDropMimeData 会先做
        index 映射预检，从上往下拖时映射常常失败，导致 drop 被静默拒绝。
        这里直接绕过那套映射，只检查 MIME 类型即可。
        """
        return data.hasFormat("application/x-watchlist-row")

    def supportedDropActions(self):
        """直接声明支持移动操作，防止 Qt 默认链路吞掉 drop 事件"""
        return Qt.DropAction.MoveAction

    def mimeData(self, indices):
        """
        拖拽发起时 Qt 会调用 proxy 的 mimeData。
        这里要把 proxy 行号 → 映射成 source 行号 → 编码到 MIME 里，
        确保 dropMimeData 收到的永远是 source model 的真实行号。
        """
        import json

        source = self.sourceModel()
        if not source:
            return QMimeData()

        source_rows = set()
        for proxy_idx in indices:
            src_idx = self.mapToSource(proxy_idx)
            if src_idx.isValid():
                source_rows.add(src_idx.row())

        mime = QMimeData()
        if source_rows:
            mime.setData("application/x-watchlist-row", json.dumps(sorted(source_rows)).encode("utf-8"))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        """
        拖拽释放时的核心处理：
        row/parent 是 proxy 空间的坐标，需要映射到 source 空间后转发给 source model。
        """
        if self.sortColumn() != -1:
            return False

        source = self.sourceModel()
        if not source:
            return False

        # 把 proxy 空间的 drop 位置转换为 source 空间
        if row >= 0:
            # 拖到两行之间 → 映射 proxy row → source row
            if row < self.rowCount():
                src_idx = self.mapToSource(self.index(row, 0))
                source_row = src_idx.row() if src_idx.isValid() else source.rowCount()
            else:
                source_row = source.rowCount()
        elif parent.isValid():
            # 拖到某行上面 → 取该行的 source 位置
            src_idx = self.mapToSource(parent)
            source_row = src_idx.row() if src_idx.isValid() else source.rowCount()
        else:
            source_row = source.rowCount()

        return source.dropMimeData(data, action, source_row, column, QModelIndex())


class StockItemDelegate(QStyledItemDelegate):
    """
    负责高级单元格渲染的委托类，包含闪烁褪色动画（后续由定时器或外部驱动刷新）
    和高级彩色状态胶囊（Pill）绘制。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flash_duration = FLASH_DURATION_SECONDS

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        table_tokens = _theme_table_tokens()

        # 0. 绘制基础默认背景（借用系统的绘制，并屏蔽默认文字）
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        show_selected_rail = is_selected and index.column() == 0
        selected_rail_width = table_tokens["selected_rail_width"] if show_selected_rail else 0
        current_index = widget.currentIndex() if widget and hasattr(widget, "currentIndex") else QModelIndex()
        is_current = current_index.isValid() and current_index == index
        skip_sorted_overlay = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        sorted_column = widget.sorted_column() if widget and hasattr(widget, "sorted_column") else -1
        sorted_overlay = None
        if not is_selected and not skip_sorted_overlay and sorted_column == index.column():
            sorted_overlay = _qcolor_from_token(table_tokens["sorted_column_bg"])

        def draw_current_cell_indicator():
            if not is_current:
                return

            left_inset = 2 + selected_rail_width + (2 if show_selected_rail else 0)
            indicator_rect = option.rect.adjusted(left_inset, 2, -2, -2)
            if indicator_rect.width() <= 4 or indicator_rect.height() <= 4:
                return

            fill_token = "current_cell_bg_selected" if is_selected else "current_cell_bg"
            fill_color = _qcolor_from_token(table_tokens[fill_token])
            border_color = _qcolor_from_token(table_tokens["current_cell_border"])

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(indicator_rect, 4, 4)

            pen = QPen(border_color)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRoundedRect(indicator_rect, 4, 4)

        # 1. 获取闪电更新动画数据
        flash_data = index.data(Qt.ItemDataRole.UserRole + 1)
        if flash_data and isinstance(flash_data, dict):
            update_time = flash_data.get("time", 0)
            diff = flash_data.get("diff", 0)  # >0 涨, <0 跌
            elapsed = time.time() - update_time
            if elapsed < self.flash_duration:
                alpha = int(255 * (1.0 - (elapsed / self.flash_duration)))
                if diff > 0:
                    color_hex = _c("COLOR_RISE_STRONG")
                elif diff < 0:
                    color_hex = _c("COLOR_FALL_STRONG")
                else:
                    color_hex = _c("COLOR_INFO")
                bg_color = QColor(color_hex)
                bg_color.setAlpha(min(72, max(0, int(alpha * 0.24))))
                painter.fillRect(option.rect, bg_color)

        # 2. 判断是否是自定义绘制的胶囊文本 (Pill)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        pill_color = index.data(Qt.ItemDataRole.UserRole + 2)  # Pill Color Role

        if pill_color and text:
            opt_bg = QStyleOptionViewItem(opt)
            opt_bg.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, painter, widget)
            if sorted_overlay is not None:
                painter.fillRect(option.rect, sorted_overlay)
            if show_selected_rail:
                rail_rect = QRect(
                    option.rect.left(),
                    option.rect.top() + 1,
                    selected_rail_width,
                    max(0, option.rect.height() - 2),
                )
                painter.fillRect(rail_rect, _qcolor_from_token(table_tokens["selected_rail_color"]))
            draw_current_cell_indicator()
            rect = option.rect
            painter.setFont(opt.font)
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(str(text))
            text_height = fm.height()

            # 胶囊边界框
            pad_x = 12
            pad_y = 6
            # 计算剧中或靠左的绘制位置
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            draw_rect = QRect(0, 0, text_width + pad_x, text_height + pad_y)
            if align and (align & Qt.AlignmentFlag.AlignLeft.value):
                draw_rect.moveCenter(rect.center())
                draw_rect.moveLeft(rect.left() + 8 + selected_rail_width + (4 if show_selected_rail else 0))
            else:
                draw_rect.moveCenter(rect.center())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            p_color = QColor(pill_color)
            p_color.setAlpha(35)
            painter.setBrush(QBrush(p_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(draw_rect, 6, 6)

            text_color = QColor(pill_color)
            text_color.setAlpha(255)
            painter.setPen(QPen(text_color))
            painter.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, str(text))
        else:
            opt_bg = QStyleOptionViewItem(opt)
            opt_bg.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, painter, widget)
            if sorted_overlay is not None:
                painter.fillRect(option.rect, sorted_overlay)
            if show_selected_rail:
                rail_rect = QRect(
                    option.rect.left(),
                    option.rect.top() + 1,
                    selected_rail_width,
                    max(0, option.rect.height() - 2),
                )
                painter.fillRect(rail_rect, _qcolor_from_token(table_tokens["selected_rail_color"]))
            draw_current_cell_indicator()
            left_padding = 8 + selected_rail_width + (4 if show_selected_rail else 0)
            text_rect = option.rect.adjusted(left_padding, 0, -8, 0)

            font = index.data(Qt.ItemDataRole.FontRole)
            if isinstance(font, QFont):
                painter.setFont(font)
            else:
                painter.setFont(opt.font)

            text_color = index.data(Qt.ItemDataRole.ForegroundRole)
            if not isinstance(text_color, QColor):
                color_role = QPalette.ColorRole.HighlightedText if is_selected else QPalette.ColorRole.Text
                text_color = opt.palette.color(color_role)

            alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
            if alignment is None:
                alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            elided_text = painter.fontMetrics().elidedText(
                str(text or ""),
                opt.textElideMode,
                max(0, text_rect.width() - 2),
            )
            painter.setPen(QPen(text_color))
            painter.drawText(text_rect, alignment, elided_text)

        painter.restore()
