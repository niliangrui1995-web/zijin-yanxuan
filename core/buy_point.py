# -*- coding: utf-8 -*-
"""买点规则核心模块。

统一维护：
- 买点命中判定
- 文本/徽标两种展示样式
"""

from __future__ import annotations

BUY_POINT_STYLE_TEXT = "text"
BUY_POINT_STYLE_BADGE = "badge"

BUY_POINT_TEXT = "触发"
BUY_POINT_BADGE = "✅"


def render_buy_point(active: bool, style: str = BUY_POINT_STYLE_TEXT) -> str:
    if not active:
        return ""
    if style == BUY_POINT_STYLE_BADGE:
        return BUY_POINT_BADGE
    return BUY_POINT_TEXT


def is_buy_point_active(open_price: float, close_price: float, ma10: float, ma20: float) -> bool:
    return (
        close_price >= open_price
        and ma10 > ma20
        and open_price < ma10
        and close_price > ma20 * 0.95
    )


def calculate_buy_point_from_history(
    history: list[float],
    open_price: float,
    close_price: float,
    style: str = BUY_POINT_STYLE_TEXT,
) -> str:
    if not history or len(history) < 20:
        return ""
    if open_price <= 0 or close_price <= 0:
        return ""

    ma10 = sum(history[-10:]) / 10 if len(history) >= 10 else 0
    ma20 = sum(history[-20:]) / 20 if len(history) >= 20 else 0
    return render_buy_point(
        is_buy_point_active(open_price=open_price, close_price=close_price, ma10=ma10, ma20=ma20),
        style=style,
    )
