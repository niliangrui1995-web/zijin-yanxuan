# -*- coding: utf-8 -*-

from core.buy_point import (
    BUY_POINT_BADGE,
    BUY_POINT_STYLE_BADGE,
    BUY_POINT_STYLE_TEXT,
    BUY_POINT_TEXT,
    calculate_buy_point_from_history,
)


def test_calculate_buy_point_from_history_supports_text_style():
    history = list(range(1, 21))

    result = calculate_buy_point_from_history(
        history=history,
        open_price=14,
        close_price=20,
        style=BUY_POINT_STYLE_TEXT,
    )

    assert result == BUY_POINT_TEXT


def test_calculate_buy_point_from_history_supports_badge_style():
    history = list(range(1, 21))

    result = calculate_buy_point_from_history(
        history=history,
        open_price=14,
        close_price=20,
        style=BUY_POINT_STYLE_BADGE,
    )

    assert result == BUY_POINT_BADGE
