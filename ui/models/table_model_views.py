# -*- coding: utf-8 -*-
"""Proxy and delegate classes for table views."""

from __future__ import annotations

from PyQt6.QtCore import QMimeData, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ui.components import SearchFilter
from ui.models.table_cell_renderers import build_stock_cell_context, can_use_native_cell_paint, render_stock_cell
from ui.models.table_model_helpers import (
    FLASH_DURATION_SECONDS,
    SERIAL_HEADER,
    STOCK_CELL_PRESENTATION_ROLE,
    STOCK_CELL_RENDER_ROLE,
    _c,
)


class RtSortFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self._filter_text = ""
        self._exact_column_filters = {}
        self._serial_column = -1

    def setSourceModel(self, source_model):  # noqa: N802 - Qt API naming
        super().setSourceModel(source_model)
        self._serial_column = self._find_serial_column(source_model)

    @staticmethod
    def _find_serial_column(source_model) -> int:
        if source_model is None:
            return -1
        try:
            column_count = int(source_model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1
        for column in range(max(0, column_count)):
            try:
                header = source_model.headerData(
                    column,
                    Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return -1
            if header == SERIAL_HEADER:
                return column
        return -1

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

        if index.column() == self._serial_column:
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
            if role == STOCK_CELL_PRESENTATION_ROLE:
                payload = super().data(index, role)
                if isinstance(payload, tuple) and len(payload) == 6:
                    return (str(index.row() + 1), *payload[1:])
        return super().data(index, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column >= 0 and column == self._serial_column:
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

    @staticmethod
    def _payload_requires_custom_paint(payload, option: QStyleOptionViewItem, index, widget) -> bool:
        """Avoid constructing a full VCP render context for ordinary table cells."""
        if not isinstance(payload, tuple) or len(payload) != 5:
            return True
        rail_color, skip_sorted_overlay, flash_data, pill_color, visual_payload = payload
        if bool(option.state & QStyle.StateFlag.State_Selected):
            return True
        if pill_color or isinstance(visual_payload, dict) or isinstance(flash_data, dict):
            return True
        suppress_left_rails = bool(widget and widget.property("suppressLeftRails"))
        if rail_color and index.column() == 0 and not suppress_left_rails:
            return True
        if not skip_sorted_overlay and widget and hasattr(widget, "sorted_column"):
            try:
                if int(widget.sorted_column()) == index.column():
                    return True
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return True
        if widget and widget.property("showCurrentCellIndicator") and hasattr(widget, "currentIndex"):
            try:
                current_index = widget.currentIndex()
                if current_index.isValid() and current_index == index:
                    return True
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return True
        return False

    @staticmethod
    def _apply_plain_presentation(opt: QStyleOptionViewItem, presentation) -> object:
        if not isinstance(presentation, tuple) or len(presentation) != 6:
            return None
        text, alignment, font, foreground, background, render_payload = presentation
        if not isinstance(font, QFont):
            return None

        opt.text = "" if text is None else str(text)
        try:
            opt.displayAlignment = Qt.AlignmentFlag(int(alignment))
        except (TypeError, ValueError):
            return None
        opt.font = font
        opt.fontMetrics = QFontMetrics(font)
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        if opt.text:
            opt.features |= QStyleOptionViewItem.ViewItemFeature.HasDisplay

        palette = QPalette(opt.palette)
        if isinstance(foreground, QColor) and foreground.isValid():
            palette.setColor(QPalette.ColorRole.Text, foreground)
        opt.palette = palette
        opt.backgroundBrush = QBrush(background) if isinstance(background, QColor) and background.isValid() else QBrush()
        return render_payload

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()

        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        presentation = index.data(STOCK_CELL_PRESENTATION_ROLE)
        opt = QStyleOptionViewItem(option)
        render_payload = self._apply_plain_presentation(opt, presentation)
        if render_payload is not None and not self._payload_requires_custom_paint(render_payload, option, index, widget):
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.state &= ~QStyle.StateFlag.State_FocusAtBorder
            opt.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
            painter.restore()
            return

        self.initStyleOption(opt, index)
        if widget and widget.property("simpleCellPaint"):
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.state &= ~QStyle.StateFlag.State_FocusAtBorder
            opt.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
            painter.restore()
            return

        render_payload = index.data(STOCK_CELL_RENDER_ROLE)
        if not self._payload_requires_custom_paint(render_payload, option, index, widget):
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.state &= ~QStyle.StateFlag.State_FocusAtBorder
            opt.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
            painter.restore()
            return

        ctx = build_stock_cell_context(
            painter=painter,
            option=option,
            opt=opt,
            index=index,
            style=style,
            widget=widget,
            flash_duration=self.flash_duration,
            render_payload=render_payload,
        )
        if can_use_native_cell_paint(ctx):
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.state &= ~QStyle.StateFlag.State_FocusAtBorder
            opt.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        else:
            render_stock_cell(ctx)

        painter.restore()
