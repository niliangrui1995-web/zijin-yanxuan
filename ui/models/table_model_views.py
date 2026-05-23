# -*- coding: utf-8 -*-
"""Proxy and delegate classes for table views."""

from __future__ import annotations

import time

from PyQt6.QtCore import QMimeData, QModelIndex, QRect, QRectF, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ui.components import SearchFilter
from ui.models.table_model_helpers import (
    FLASH_DURATION_SECONDS,
    SERIAL_HEADER,
    _c,
    _flash_decay_alpha,
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
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        suppress_left_rails = bool(widget and widget.property("suppressLeftRails"))
        show_current_cell_indicator = bool(widget and widget.property("showCurrentCellIndicator"))
        rail_color = None if suppress_left_rails else index.data(Qt.ItemDataRole.UserRole + 4)
        show_accent_rail = bool(rail_color) and index.column() == 0 and not suppress_left_rails
        show_selected_rail = False
        show_hover_rail = False
        selected_rail_width = table_tokens["selected_rail_width"] if show_selected_rail else 0
        accent_rail_width = table_tokens["accent_rail_width"] if show_accent_rail else 0
        hover_rail_width = table_tokens.get("hover_rail_width", 3) if show_hover_rail and not suppress_left_rails else 0
        rail_width = selected_rail_width or accent_rail_width or hover_rail_width
        current_index = widget.currentIndex() if widget and hasattr(widget, "currentIndex") else QModelIndex()
        is_current = current_index.isValid() and current_index == index
        skip_sorted_overlay = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        sorted_column = widget.sorted_column() if widget and hasattr(widget, "sorted_column") else -1
        sorted_overlay = None
        if not is_selected and not skip_sorted_overlay and sorted_column == index.column():
            sorted_overlay = _qcolor_from_token(table_tokens["sorted_column_bg"])

        def draw_current_cell_indicator():
            if not show_current_cell_indicator or not is_current:
                return

            left_inset = 2 + rail_width + (2 if rail_width else 0)
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

        def draw_flash_background():
            if not flash_data or not isinstance(flash_data, dict):
                return

            update_time = flash_data.get("time", 0)
            diff = flash_data.get("diff", 0)
            elapsed = time.time() - update_time
            if elapsed < 0 or elapsed >= self.flash_duration:
                return

            alpha = int(255 * _flash_decay_alpha(elapsed, self.flash_duration))
            if diff > 0:
                color_hex = _c("COLOR_RISE_STRONG")
            elif diff < 0:
                color_hex = _c("COLOR_FALL_STRONG")
            else:
                color_hex = _c("COLOR_INFO")
            bg_color = QColor(color_hex)
            flash_scale = float(table_tokens.get("flash_alpha_scale", 0.24))
            flash_max_alpha = int(table_tokens.get("flash_max_alpha", 76))
            bg_color.setAlpha(min(flash_max_alpha, max(0, int(alpha * flash_scale))))
            painter.fillRect(option.rect, QBrush(bg_color))

        def clear_default_selected_left_marker():
            if not is_selected or index.column() != 0 or show_accent_rail:
                return

            clear_width = max(1, int(table_tokens.get("selected_rail_width", 3)))
            clear_rect = QRect(
                option.rect.left(),
                option.rect.top() + 1,
                clear_width,
                max(0, option.rect.height() - 2),
            )
            fill_key = "selected_hover_bg" if is_hovered else "selected_bg"
            fill_color = _qcolor_from_token(table_tokens[fill_key])
            if fill_color.alpha() < 255:
                base_color = option.palette.color(QPalette.ColorRole.Base)
                alpha = fill_color.alphaF()
                fill_color = QColor(
                    round(fill_color.red() * alpha + base_color.red() * (1 - alpha)),
                    round(fill_color.green() * alpha + base_color.green() * (1 - alpha)),
                    round(fill_color.blue() * alpha + base_color.blue() * (1 - alpha)),
                )
            painter.fillRect(clear_rect, fill_color)

        def draw_left_rail():
            if not (show_selected_rail or show_accent_rail or show_hover_rail):
                return

            width = rail_width
            if width <= 0:
                return

            rail_rect = QRect(
                option.rect.left(),
                option.rect.top() + 1,
                width,
                max(0, option.rect.height() - 2),
            )
            if show_selected_rail:
                painter.fillRect(rail_rect, _qcolor_from_token(table_tokens["selected_rail_color"]))
                return

            if show_hover_rail:
                hover_color = _qcolor_from_token(table_tokens.get("hover_rail_color"))
                hover_color.setAlpha(int(table_tokens.get("accent_rail_alpha", 190)))
                painter.fillRect(rail_rect, hover_color)
                return

            accent = QColor(rail_color)
            accent.setAlpha(int(table_tokens.get("accent_rail_alpha", 190)))
            painter.fillRect(rail_rect, accent)

        def draw_flash_rail():
            if suppress_left_rails:
                return
            if not flash_data or not isinstance(flash_data, dict):
                return
            update_time = flash_data.get("time", 0)
            diff = flash_data.get("diff", 0)
            elapsed = time.time() - update_time
            if elapsed < 0 or elapsed >= self.flash_duration:
                return

            decay = _flash_decay_alpha(elapsed, self.flash_duration)
            if diff > 0:
                color_hex = _c("COLOR_RISE_STRONG")
            elif diff < 0:
                color_hex = _c("COLOR_FALL_STRONG")
            else:
                color_hex = _c("COLOR_INFO")

            width = max(1, int(table_tokens.get("flash_rail_width", 3)))
            rail_rect = QRect(
                option.rect.left(),
                option.rect.top() + 1,
                width,
                max(0, option.rect.height() - 2),
            )
            rail_color = QColor(color_hex)
            rail_color.setAlpha(max(0, min(255, int(table_tokens.get("flash_rail_alpha", 160) * decay))))
            painter.fillRect(rail_rect, rail_color)

        # 2. 判断是否是自定义绘制的胶囊文本 (Pill)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        pill_color = index.data(Qt.ItemDataRole.UserRole + 2)  # Pill Color Role
        visual_payload = index.data(Qt.ItemDataRole.UserRole + 5)

        def draw_cell_base():
            opt_bg = QStyleOptionViewItem(opt)
            opt_bg.text = ""
            opt_bg.state &= ~QStyle.StateFlag.State_HasFocus
            opt_bg.state &= ~QStyle.StateFlag.State_FocusAtBorder
            opt_bg.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, painter, widget)
            if sorted_overlay is not None:
                painter.fillRect(option.rect, sorted_overlay)
            draw_flash_background()
            clear_default_selected_left_marker()
            draw_left_rail()
            draw_flash_rail()
            draw_current_cell_indicator()

        def content_rect() -> QRect:
            left_padding = 8 + rail_width + (4 if rail_width else 0)
            return option.rect.adjusted(left_padding, 0, -8, 0)

        def resolve_text_style():
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
            return text_color, int(alignment)

        def draw_plain_text(value, rect: QRect | None = None, *, fade: bool = False, alignment_override=None):
            target = rect or content_rect()
            text_color, alignment = resolve_text_style()
            if alignment_override is not None:
                alignment = int(alignment_override)

            value_text = str(value or "")
            painter.setPen(QPen(text_color))
            fm = painter.fontMetrics()
            if fade and fm.horizontalAdvance(value_text) > max(0, target.width() - 2):
                elided_text = fm.elidedText(value_text, opt.textElideMode, max(0, target.width() - 2))
                painter.drawText(target, alignment, elided_text)
                return

            elided_text = fm.elidedText(value_text, opt.textElideMode, max(0, target.width() - 2))
            painter.drawText(target, alignment, elided_text)

        def draw_money_bar(payload: dict):
            try:
                value = float(payload.get("value", 0.0))
                max_abs = max(float(payload.get("max_abs", 1.0)), abs(value), 1.0)
            except (TypeError, ValueError):
                return

            ratio = min(1.0, abs(value) / max_abs)
            if ratio <= 0:
                return

            rect = content_rect().adjusted(0, 0, 0, -2)
            bar_height = max(3, min(5, rect.height() // 5))
            bar_width = max(2, int(rect.width() * ratio))
            y = rect.bottom() - bar_height - 3
            x = rect.left() if value >= 0 else rect.right() - bar_width + 1
            bar_rect = QRectF(x, y, bar_width, bar_height)
            color = QColor(_c("COLOR_RISE" if value >= 0 else "COLOR_FALL"))

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            glow = QColor(color)
            glow.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawRoundedRect(bar_rect.adjusted(-1, -2, 1, 2), 3, 3)

            color.setAlpha(82)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(bar_rect, 2, 2)

        def draw_tag_badges(payload: dict) -> bool:
            tags = payload.get("tags") if isinstance(payload, dict) else None
            if not tags:
                return False

            rect = content_rect()
            resolve_text_style()
            fm = painter.fontMetrics()
            badge_height = min(max(18, fm.height() + 4), max(18, rect.height() - 6))
            y = rect.center().y() - badge_height // 2
            x = rect.left()
            drawn = False

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for tag in tags[:4]:
                label = str((tag or {}).get("text", "")).strip()
                if not label:
                    continue

                remaining = rect.right() - x + 1
                if remaining < 24:
                    break

                raw_width = fm.horizontalAdvance(label) + 16
                width = min(raw_width, remaining)
                clipped_label = fm.elidedText(label, Qt.TextElideMode.ElideRight, max(0, width - 16))
                color = QColor((tag or {}).get("color") or _c("COLOR_INFO"))
                if not color.isValid():
                    color = QColor(_c("COLOR_INFO"))

                badge_rect = QRectF(x, y, width, badge_height)
                aura = QColor(color)
                aura.setAlpha(18)
                fill = QColor(color)
                fill.setAlpha(44)
                stroke = QColor(color)
                stroke.setAlpha(108)
                label_color = QColor(color)
                label_color.setAlpha(235)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(aura))
                painter.drawRoundedRect(badge_rect.adjusted(-1, -1, 1, 1), 8, 8)
                painter.setPen(QPen(stroke, 1))
                painter.setBrush(QBrush(fill))
                painter.drawRoundedRect(badge_rect, 7, 7)
                painter.setPen(QPen(label_color))
                painter.drawText(badge_rect.adjusted(8, 0, -8, 0), int(Qt.AlignmentFlag.AlignCenter), clipped_label)

                x += int(width) + 6
                drawn = True
            return drawn

        def indicator_color(tone: str) -> QColor:
            color_map = {
                "success": _c("COLOR_SUCCESS"),
                "warning": _c("COLOR_WARNING"),
                "error": _c("COLOR_ERROR"),
                "offline": _c("TEXT_MUTED"),
                "neutral": _c("COLOR_INFO"),
            }
            color = QColor(color_map.get(str(tone or ""), _c("COLOR_INFO")))
            return color if color.isValid() else QColor(_c("COLOR_INFO"))

        def draw_indicator(payload: dict, *, center_only: bool) -> bool:
            rect = content_rect()
            color = indicator_color(payload.get("tone", "neutral"))
            pulse = bool(payload.get("pulse"))
            phase = (time.time() % 1.2) / 1.2
            dot_size = 8.0
            cy = float(rect.center().y())
            x = float(rect.center().x()) - dot_size / 2 if center_only else float(rect.left())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            aura = QColor(color)
            aura.setAlpha(max(18, int((52 if pulse else 28) * (1.0 - phase * 0.45))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(aura))
            painter.drawEllipse(QRectF(x - 4, cy - dot_size / 2 - 4, dot_size + 8, dot_size + 8))

            core = QColor(color)
            core.setAlpha(235)
            painter.setBrush(QBrush(core))
            painter.drawEllipse(QRectF(x, cy - dot_size / 2, dot_size, dot_size))

            if center_only:
                return True

            label_rect = rect.adjusted(int(dot_size) + 10, 0, 0, 0)
            label = payload.get("label")
            draw_plain_text(
                str(label if label is not None else text),
                label_rect,
                alignment_override=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            )
            return True

        def draw_currency_stamp(payload: dict):
            draw_plain_text(text)

            stamp = str(payload.get("stamp", "")).strip()
            if not stamp:
                return

            stamp_font = QFont(painter.font())
            point_size = stamp_font.pointSizeF()
            if point_size > 0:
                stamp_font.setPointSizeF(max(8.0, point_size - 2.0))
            painter.setFont(stamp_font)

            stamp_color = QColor(_c("TEXT_MUTED"))
            stamp_color.setAlpha(130)
            painter.setPen(QPen(stamp_color))
            rect = content_rect().adjusted(0, option.rect.height() // 3, 0, -1)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), stamp)

        if pill_color and text:
            draw_cell_base()
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
                draw_rect.moveLeft(rect.left() + 8 + rail_width + (4 if rail_width else 0))
            else:
                draw_rect.moveCenter(rect.center())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            p_color = QColor(pill_color)
            p_color.setAlpha(31)
            border_color = QColor(pill_color)
            border_color.setAlpha(51)
            painter.setBrush(QBrush(p_color))
            painter.setPen(QPen(border_color, 1))
            painter.drawRoundedRect(draw_rect, 6, 6)

            text_color = _qcolor_from_token(_c("INFO_BADGE_FG"))
            if not text_color.isValid():
                text_color = _qcolor_from_token(_c("TEXT_SECONDARY"))
            painter.setPen(QPen(text_color))
            painter.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, str(text))
        else:
            draw_cell_base()
            if isinstance(visual_payload, dict):
                visual_kind = visual_payload.get("kind")
                if visual_kind == "tag_badges" and draw_tag_badges(visual_payload):
                    painter.restore()
                    return
                if visual_kind == "money_bar":
                    draw_money_bar(visual_payload)
                elif visual_kind == "risk_light" and draw_indicator(visual_payload, center_only=True):
                    painter.restore()
                    return
                elif visual_kind == "status_light" and draw_indicator(visual_payload, center_only=False):
                    painter.restore()
                    return
                elif visual_kind == "currency_stamp":
                    draw_currency_stamp(visual_payload)
                    painter.restore()
                    return
            left_padding = 8 + rail_width + (4 if rail_width else 0)
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

            tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
            if tooltip_text and len(str(text or "")) >= 12:
                draw_plain_text(text, text_rect, fade=True)
                painter.restore()
                return

            elided_text = painter.fontMetrics().elidedText(
                str(text or ""),
                opt.textElideMode,
                max(0, text_rect.width() - 2),
            )
            painter.setPen(QPen(text_color))
            painter.drawText(text_rect, alignment, elided_text)

        painter.restore()
