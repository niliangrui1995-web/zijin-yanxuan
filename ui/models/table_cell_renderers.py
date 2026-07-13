# -*- coding: utf-8 -*-
"""Cell rendering helpers for stock table delegates."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QModelIndex, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

from ui.models.table_model_helpers import _c, _flash_decay_alpha, _qcolor_from_token, _theme_table_tokens


@dataclass
class _StockCellRenderContext:
    painter: QPainter
    option: QStyleOptionViewItem
    opt: QStyleOptionViewItem
    index: QModelIndex
    style: QStyle
    widget: object
    table_tokens: dict
    flash_duration: float
    is_selected: bool
    is_hovered: bool
    suppress_left_rails: bool
    show_current_cell_indicator: bool
    rail_color: object
    show_accent_rail: bool
    show_selected_rail: bool
    show_hover_rail: bool
    rail_width: int
    is_current: bool
    sorted_overlay: QColor | None
    flash_data: object
    text: object
    pill_color: object
    visual_payload: object


def build_stock_cell_context(
    painter: QPainter,
    option: QStyleOptionViewItem,
    opt: QStyleOptionViewItem,
    index: QModelIndex,
    style: QStyle,
    widget: object,
    flash_duration: float,
) -> _StockCellRenderContext:
    table_tokens = _theme_table_tokens()
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

    flash_data = index.data(Qt.ItemDataRole.UserRole + 1)
    text = index.data(Qt.ItemDataRole.DisplayRole)
    pill_color = index.data(Qt.ItemDataRole.UserRole + 2)
    visual_payload = index.data(Qt.ItemDataRole.UserRole + 5)

    return _StockCellRenderContext(
        painter=painter,
        option=option,
        opt=opt,
        index=index,
        style=style,
        widget=widget,
        table_tokens=table_tokens,
        flash_duration=flash_duration,
        is_selected=is_selected,
        is_hovered=is_hovered,
        suppress_left_rails=suppress_left_rails,
        show_current_cell_indicator=show_current_cell_indicator,
        rail_color=rail_color,
        show_accent_rail=show_accent_rail,
        show_selected_rail=show_selected_rail,
        show_hover_rail=show_hover_rail,
        rail_width=rail_width,
        is_current=is_current,
        sorted_overlay=sorted_overlay,
        flash_data=flash_data,
        text=text,
        pill_color=pill_color,
        visual_payload=visual_payload,
    )


def render_stock_cell(ctx: _StockCellRenderContext):
    if ctx.pill_color and ctx.text:
        _draw_cell_base(ctx)
        _draw_pill(ctx)
        return

    _draw_cell_base(ctx)
    if isinstance(ctx.visual_payload, dict):
        visual_kind = ctx.visual_payload.get("kind")
        if visual_kind == "tag_badges" and _draw_tag_badges(ctx, ctx.visual_payload):
            return
        if visual_kind == "money_bar":
            _draw_money_bar(ctx, ctx.visual_payload)
        elif visual_kind in {"risk_light", "status_light"} and _draw_indicator(
            ctx, ctx.visual_payload, center_only=visual_kind == "risk_light"
        ):
            return
        elif visual_kind == "currency_stamp":
            _draw_currency_stamp(ctx, ctx.visual_payload)
            return

    text_color, alignment = _resolve_text_style(ctx)
    text_rect = _content_rect(ctx)
    tooltip_text = ctx.index.data(Qt.ItemDataRole.ToolTipRole)
    if tooltip_text and len(str(ctx.text or "")) >= 12:
        _draw_plain_text(ctx, ctx.text, text_rect, fade=True)
        return

    elided_text = ctx.painter.fontMetrics().elidedText(
        str(ctx.text or ""),
        ctx.opt.textElideMode,
        max(0, text_rect.width() - 2),
    )
    ctx.painter.setPen(QPen(text_color))
    ctx.painter.drawText(text_rect, alignment, elided_text)


def _draw_cell_base(ctx: _StockCellRenderContext):
    opt_bg = QStyleOptionViewItem(ctx.opt)
    opt_bg.text = ""
    opt_bg.state &= ~QStyle.StateFlag.State_HasFocus
    opt_bg.state &= ~QStyle.StateFlag.State_FocusAtBorder
    opt_bg.state &= ~QStyle.StateFlag.State_KeyboardFocusChange
    ctx.style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, ctx.painter, ctx.widget)
    if ctx.sorted_overlay is not None:
        ctx.painter.fillRect(ctx.option.rect, ctx.sorted_overlay)
    _draw_flash_background(ctx)
    _clear_default_selected_left_marker(ctx)
    _draw_left_rail(ctx)
    _draw_flash_rail(ctx)
    _draw_current_cell_indicator(ctx)


def _content_rect(ctx: _StockCellRenderContext) -> QRect:
    left_padding = 8 + ctx.rail_width + (4 if ctx.rail_width else 0)
    return ctx.option.rect.adjusted(left_padding, 0, -8, 0)


def _resolve_text_style(ctx: _StockCellRenderContext):
    font = ctx.index.data(Qt.ItemDataRole.FontRole)
    if isinstance(font, QFont):
        ctx.painter.setFont(font)
    else:
        ctx.painter.setFont(ctx.opt.font)

    text_color = ctx.index.data(Qt.ItemDataRole.ForegroundRole)
    if not isinstance(text_color, QColor):
        color_role = QPalette.ColorRole.HighlightedText if ctx.is_selected else QPalette.ColorRole.Text
        text_color = ctx.opt.palette.color(color_role)

    alignment = ctx.index.data(Qt.ItemDataRole.TextAlignmentRole)
    if alignment is None:
        alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return text_color, int(alignment)


def _draw_plain_text(
    ctx: _StockCellRenderContext,
    value,
    rect: QRect | None = None,
    *,
    fade: bool = False,
    alignment_override=None,
):
    target = rect or _content_rect(ctx)
    text_color, alignment = _resolve_text_style(ctx)
    if alignment_override is not None:
        alignment = int(alignment_override)

    value_text = str(value or "")
    ctx.painter.setPen(QPen(text_color))
    fm = ctx.painter.fontMetrics()
    if fade and fm.horizontalAdvance(value_text) > max(0, target.width() - 2):
        elided_text = fm.elidedText(value_text, ctx.opt.textElideMode, max(0, target.width() - 2))
        ctx.painter.drawText(target, alignment, elided_text)
        return

    elided_text = fm.elidedText(value_text, ctx.opt.textElideMode, max(0, target.width() - 2))
    ctx.painter.drawText(target, alignment, elided_text)


def _draw_current_cell_indicator(ctx: _StockCellRenderContext):
    if not ctx.show_current_cell_indicator or not ctx.is_current:
        return

    left_inset = 2 + ctx.rail_width + (2 if ctx.rail_width else 0)
    indicator_rect = ctx.option.rect.adjusted(left_inset, 2, -2, -2)
    if indicator_rect.width() <= 4 or indicator_rect.height() <= 4:
        return

    fill_token = "current_cell_bg_selected" if ctx.is_selected else "current_cell_bg"
    fill_color = _qcolor_from_token(ctx.table_tokens[fill_token])
    border_color = _qcolor_from_token(ctx.table_tokens["current_cell_border"])

    ctx.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ctx.painter.setPen(Qt.PenStyle.NoPen)
    ctx.painter.setBrush(QBrush(fill_color))
    ctx.painter.drawRoundedRect(indicator_rect, 4, 4)

    pen = QPen(border_color)
    pen.setWidth(1)
    ctx.painter.setPen(pen)
    ctx.painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    ctx.painter.drawRoundedRect(indicator_rect, 4, 4)


def _draw_flash_background(ctx: _StockCellRenderContext):
    if not ctx.flash_data or not isinstance(ctx.flash_data, dict):
        return

    update_time = ctx.flash_data.get("time", 0)
    diff = ctx.flash_data.get("diff", 0)
    elapsed = time.time() - update_time
    if elapsed < 0 or elapsed >= ctx.flash_duration:
        return

    alpha = int(255 * _flash_decay_alpha(elapsed, ctx.flash_duration))
    if diff > 0:
        color_hex = _c("COLOR_RISE_STRONG")
    elif diff < 0:
        color_hex = _c("COLOR_FALL_STRONG")
    else:
        color_hex = _c("COLOR_INFO")
    bg_color = QColor(color_hex)
    flash_scale = float(ctx.table_tokens.get("flash_alpha_scale", 0.24))
    flash_max_alpha = int(ctx.table_tokens.get("flash_max_alpha", 76))
    bg_color.setAlpha(min(flash_max_alpha, max(0, int(alpha * flash_scale))))
    ctx.painter.fillRect(ctx.option.rect, QBrush(bg_color))


def _clear_default_selected_left_marker(ctx: _StockCellRenderContext):
    if not ctx.is_selected or ctx.index.column() != 0 or ctx.show_accent_rail:
        return

    clear_width = max(1, int(ctx.table_tokens.get("selected_rail_width", 3)))
    clear_rect = QRect(
        ctx.option.rect.left(),
        ctx.option.rect.top() + 1,
        clear_width,
        max(0, ctx.option.rect.height() - 2),
    )
    fill_key = "selected_hover_bg" if ctx.is_hovered else "selected_bg"
    fill_color = _qcolor_from_token(ctx.table_tokens[fill_key])
    if fill_color.alpha() < 255:
        base_color = ctx.option.palette.color(QPalette.ColorRole.Base)
        alpha = fill_color.alphaF()
        fill_color = QColor(
            round(fill_color.red() * alpha + base_color.red() * (1 - alpha)),
            round(fill_color.green() * alpha + base_color.green() * (1 - alpha)),
            round(fill_color.blue() * alpha + base_color.blue() * (1 - alpha)),
        )
    ctx.painter.fillRect(clear_rect, fill_color)


def _draw_left_rail(ctx: _StockCellRenderContext):
    if not (ctx.show_selected_rail or ctx.show_accent_rail or ctx.show_hover_rail):
        return

    width = ctx.rail_width
    if width <= 0:
        return

    rail_rect = QRect(
        ctx.option.rect.left(),
        ctx.option.rect.top() + 1,
        width,
        max(0, ctx.option.rect.height() - 2),
    )
    if ctx.show_selected_rail:
        ctx.painter.fillRect(rail_rect, _qcolor_from_token(ctx.table_tokens["selected_rail_color"]))
        return

    if ctx.show_hover_rail:
        hover_color = _qcolor_from_token(ctx.table_tokens.get("hover_rail_color"))
        hover_color.setAlpha(int(ctx.table_tokens.get("accent_rail_alpha", 190)))
        ctx.painter.fillRect(rail_rect, hover_color)
        return

    accent = QColor(ctx.rail_color)
    accent.setAlpha(int(ctx.table_tokens.get("accent_rail_alpha", 190)))
    ctx.painter.fillRect(rail_rect, accent)


def _draw_flash_rail(ctx: _StockCellRenderContext):
    if ctx.suppress_left_rails:
        return
    if not ctx.flash_data or not isinstance(ctx.flash_data, dict):
        return
    update_time = ctx.flash_data.get("time", 0)
    diff = ctx.flash_data.get("diff", 0)
    elapsed = time.time() - update_time
    if elapsed < 0 or elapsed >= ctx.flash_duration:
        return

    decay = _flash_decay_alpha(elapsed, ctx.flash_duration)
    if diff > 0:
        color_hex = _c("COLOR_RISE_STRONG")
    elif diff < 0:
        color_hex = _c("COLOR_FALL_STRONG")
    else:
        color_hex = _c("COLOR_INFO")

    width = max(1, int(ctx.table_tokens.get("flash_rail_width", 3)))
    rail_rect = QRect(
        ctx.option.rect.left(),
        ctx.option.rect.top() + 1,
        width,
        max(0, ctx.option.rect.height() - 2),
    )
    rail_color = QColor(color_hex)
    rail_color.setAlpha(max(0, min(255, int(ctx.table_tokens.get("flash_rail_alpha", 160) * decay))))
    ctx.painter.fillRect(rail_rect, rail_color)


def _draw_money_bar(ctx: _StockCellRenderContext, payload: dict):
    try:
        value = float(payload.get("value", 0.0))
        max_abs = max(float(payload.get("max_abs", 1.0)), abs(value), 1.0)
    except (TypeError, ValueError):
        return

    ratio = min(1.0, abs(value) / max_abs)
    if ratio <= 0:
        return

    rect = _content_rect(ctx).adjusted(0, 0, 0, -2)
    bar_height = max(3, min(5, rect.height() // 5))
    bar_width = max(2, int(rect.width() * ratio))
    y = rect.bottom() - bar_height - 3
    x = rect.left() if value >= 0 else rect.right() - bar_width + 1
    bar_rect = QRectF(x, y, bar_width, bar_height)
    color = QColor(_c("COLOR_RISE" if value >= 0 else "COLOR_FALL"))

    ctx.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    glow = QColor(color)
    glow.setAlpha(22)
    ctx.painter.setPen(Qt.PenStyle.NoPen)
    ctx.painter.setBrush(QBrush(glow))
    ctx.painter.drawRoundedRect(bar_rect.adjusted(-1, -2, 1, 2), 3, 3)

    color.setAlpha(82)
    ctx.painter.setBrush(QBrush(color))
    ctx.painter.drawRoundedRect(bar_rect, 2, 2)


def _draw_tag_badges(ctx: _StockCellRenderContext, payload: dict) -> bool:
    tags = payload.get("tags") if isinstance(payload, dict) else None
    if not tags:
        return False

    rect = _content_rect(ctx)
    _resolve_text_style(ctx)
    fm = ctx.painter.fontMetrics()
    badge_height = min(max(18, fm.height() + 4), max(18, rect.height() - 6))
    y = rect.center().y() - badge_height // 2
    x = rect.left()
    drawn = False

    ctx.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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

        ctx.painter.setPen(Qt.PenStyle.NoPen)
        ctx.painter.setBrush(QBrush(aura))
        ctx.painter.drawRoundedRect(badge_rect.adjusted(-1, -1, 1, 1), 8, 8)
        ctx.painter.setPen(QPen(stroke, 1))
        ctx.painter.setBrush(QBrush(fill))
        ctx.painter.drawRoundedRect(badge_rect, 7, 7)
        ctx.painter.setPen(QPen(label_color))
        ctx.painter.drawText(badge_rect.adjusted(8, 0, -8, 0), int(Qt.AlignmentFlag.AlignCenter), clipped_label)

        x += int(width) + 6
        drawn = True
    return drawn


def _indicator_color(tone: str) -> QColor:
    color_map = {
        "success": _c("COLOR_SUCCESS"),
        "warning": _c("COLOR_WARNING"),
        "error": _c("COLOR_ERROR"),
        "offline": _c("TEXT_MUTED"),
        "neutral": _c("COLOR_INFO"),
    }
    color = QColor(color_map.get(str(tone or ""), _c("COLOR_INFO")))
    return color if color.isValid() else QColor(_c("COLOR_INFO"))


def _draw_indicator(ctx: _StockCellRenderContext, payload: dict, *, center_only: bool) -> bool:
    rect = _content_rect(ctx)
    color = _indicator_color(payload.get("tone", "neutral"))
    pulse = bool(payload.get("pulse"))
    phase = (time.time() % 1.2) / 1.2
    dot_size = 8.0
    cy = float(rect.center().y())
    x = float(rect.center().x()) - dot_size / 2 if center_only else float(rect.left())

    ctx.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    aura = QColor(color)
    aura.setAlpha(max(18, int((52 if pulse else 28) * (1.0 - phase * 0.45))))
    ctx.painter.setPen(Qt.PenStyle.NoPen)
    ctx.painter.setBrush(QBrush(aura))
    ctx.painter.drawEllipse(QRectF(x - 4, cy - dot_size / 2 - 4, dot_size + 8, dot_size + 8))

    core = QColor(color)
    core.setAlpha(235)
    ctx.painter.setBrush(QBrush(core))
    ctx.painter.drawEllipse(QRectF(x, cy - dot_size / 2, dot_size, dot_size))

    if center_only:
        return True

    label_rect = rect.adjusted(int(dot_size) + 10, 0, 0, 0)
    label = payload.get("label")
    _draw_plain_text(
        ctx,
        str(label if label is not None else ctx.text),
        label_rect,
        alignment_override=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
    )
    return True


def _draw_currency_stamp(ctx: _StockCellRenderContext, payload: dict):
    _draw_plain_text(ctx, ctx.text)

    stamp = str(payload.get("stamp", "")).strip()
    if not stamp:
        return

    stamp_font = QFont(ctx.painter.font())
    point_size = stamp_font.pointSizeF()
    if point_size > 0:
        stamp_font.setPointSizeF(max(8.0, point_size - 2.0))
    ctx.painter.setFont(stamp_font)

    stamp_color = QColor(_c("TEXT_MUTED"))
    stamp_color.setAlpha(130)
    ctx.painter.setPen(QPen(stamp_color))
    rect = _content_rect(ctx).adjusted(0, ctx.option.rect.height() // 3, 0, -1)
    ctx.painter.drawText(rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), stamp)


def _draw_pill(ctx: _StockCellRenderContext):
    rect = ctx.option.rect
    ctx.painter.setFont(ctx.opt.font)
    fm = ctx.painter.fontMetrics()
    text_width = fm.horizontalAdvance(str(ctx.text))
    text_height = fm.height()

    pad_x = 12
    pad_y = 6
    align = ctx.index.data(Qt.ItemDataRole.TextAlignmentRole)
    draw_rect = QRect(0, 0, text_width + pad_x, text_height + pad_y)
    if align and (align & Qt.AlignmentFlag.AlignLeft.value):
        draw_rect.moveCenter(rect.center())
        draw_rect.moveLeft(rect.left() + 8 + ctx.rail_width + (4 if ctx.rail_width else 0))
    else:
        draw_rect.moveCenter(rect.center())

    ctx.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    p_color = QColor(ctx.pill_color)
    p_color.setAlpha(31)
    border_color = QColor(ctx.pill_color)
    border_color.setAlpha(51)
    ctx.painter.setBrush(QBrush(p_color))
    ctx.painter.setPen(QPen(border_color, 1))
    ctx.painter.drawRoundedRect(draw_rect, 6, 6)

    text_color = _qcolor_from_token(_c("INFO_BADGE_FG"))
    if not text_color.isValid():
        text_color = _qcolor_from_token(_c("TEXT_SECONDARY"))
    ctx.painter.setPen(QPen(text_color))
    ctx.painter.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, str(ctx.text))
