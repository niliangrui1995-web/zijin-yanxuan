from datetime import datetime

from ui.tabs.fund_holdings_rules import (
    filter_ai_related_concepts,
    format_amount,
    format_pct,
    normalize_auto_sync_date,
    should_trigger_daily_auto_sync,
)


def test_fund_holdings_rules_normalize_and_gate_daily_sync():
    assert normalize_auto_sync_date("2026-04-24") == "20260424"
    assert normalize_auto_sync_date("2026/04/24 20:30") == "20260424"

    assert should_trigger_daily_auto_sync(
        datetime(2026, 4, 24, 20, 30),
        last_auto_sync_date="",
        pending_auto_sync_date="",
    )
    assert not should_trigger_daily_auto_sync(
        datetime(2026, 4, 24, 20, 29),
        last_auto_sync_date="",
        pending_auto_sync_date="",
    )
    assert not should_trigger_daily_auto_sync(
        datetime(2026, 4, 24, 21, 0),
        last_auto_sync_date="2026-04-24",
        pending_auto_sync_date="",
    )


def test_fund_holdings_rules_format_and_filter_ai_concepts():
    assert format_pct(1.234, show=True) == "1.23%"
    assert format_pct(1.234, show=True, signed=True) == "+1.23%"
    assert format_amount(1234567, divisor=10000.0, show=True) == "123.46"
    assert format_amount(10000, divisor=10000.0, show=False) == "--"

    assert filter_ai_related_concepts(["白酒概念", "CPO概念", "液冷服务", "AI营销", "CPO概念"]) == [
        "CPO",
        "液冷",
    ]
