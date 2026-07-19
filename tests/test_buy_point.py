# -*- coding: utf-8 -*-

import pytest

from core.buy_point import (
    BUY_POINT_BADGE,
    BUY_POINT_STYLE_BADGE,
    BUY_POINT_STYLE_TEXT,
    BUY_POINT_TEXT,
    calculate_buy_point_from_history,
    is_buy_point_active,
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


@pytest.mark.parametrize(
    ("history", "open_price", "close_price"),
    [
        pytest.param([], 10, 11, id="missing-history"),
        pytest.param([10] * 19, 10, 11, id="insufficient-history"),
        pytest.param([10] * 20, 0, 11, id="invalid-open"),
        pytest.param([10] * 20, 10, 0, id="invalid-close"),
    ],
)
def test_calculate_buy_point_from_history_rejects_incomplete_or_invalid_input(
    history,
    open_price,
    close_price,
):
    assert calculate_buy_point_from_history(history, open_price, close_price) == ""


@pytest.mark.parametrize(
    ("open_price", "close_price", "ma10", "ma20"),
    [
        pytest.param(10, 9.99, 12, 10, id="close-below-open"),
        pytest.param(9, 10.5, 10, 10, id="ma10-not-above-ma20"),
        pytest.param(12, 13, 12, 10, id="open-not-below-ma10"),
        pytest.param(9, 9.5, 12, 10, id="close-at-ma20-floor"),
    ],
)
def test_is_buy_point_active_rejects_each_rule_boundary(open_price, close_price, ma10, ma20):
    assert is_buy_point_active(open_price, close_price, ma10, ma20) is False
