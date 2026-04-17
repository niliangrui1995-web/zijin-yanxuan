# -*- coding: utf-8 -*-
from core.fund_holdings_compare import (
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    build_change_rows,
    build_qfii_snapshots,
    get_compare_quarter_key,
    normalize_quarter_key,
)


def test_normalize_quarter_key_supports_multiple_formats():
    assert normalize_quarter_key("2025Q4") == "2025Q4"
    assert normalize_quarter_key("2025-q3") == "2025Q3"
    assert normalize_quarter_key("2025/06/30") == "2025Q2"


def test_ruiyuan_uses_previous_quarter_compare_mapping():
    assert get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], "2025Q1") == "2024Q4"
    assert get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], "2025Q2") == "2025Q1"
    assert get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], "2025Q3") == "2025Q2"
    assert get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], "2025Q4") == "2025Q3"


def test_build_qfii_snapshots_aggregates_same_stock_multiple_holders():
    snapshots = build_qfii_snapshots(
        [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "A",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.1,
                "UPDATE_DATE": "2026-04-18 00:00:00",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "B",
                "HOLDER_RANK": 2,
                "HOLD_NUM": 2500,
                "HOLDER_MARKET_CAP": 30000,
                "FREE_HOLDNUM_RATIO": 0.8,
                "HOLD_RATIO": 0.2,
                "UPDATE_DATE": "2026-04-18 00:00:00",
            },
        ],
        SUBJECT_QFII,
        "2025Q4",
        "2025-12-31",
    )

    assert len(snapshots) == 1
    assert snapshots[0]["holders_count"] == 2
    assert snapshots[0]["hold_num_shares"] == 3500
    assert snapshots[0]["hold_market_value_cny"] == 40000
    assert snapshots[0]["hold_ratio_pct"] == 0.30000000000000004


def test_build_change_rows_uses_previous_quarter_for_ruiyuan():
    snapshots = [
        {
            "subject_code": SUBJECT_RUIYUAN["subject_code"],
            "subject_name": SUBJECT_RUIYUAN["subject_name"],
            "subject_type": SUBJECT_RUIYUAN["subject_type"],
            "quarter_key": "2024Q4",
            "quarter_label": "2024Q4",
            "compare_quarter_key": "2024Q3",
            "end_date": "2024-12-31",
            "stock_code": "000001",
            "stock_name": "平安银行",
            "holders_count": 1,
            "hold_num_shares": 1_000_000,
            "hold_market_value_cny": 10_000_000,
            "net_value_ratio_pct": 1.0,
            "free_hold_ratio_pct": 0.0,
            "hold_ratio_pct": 0.0,
            "latest_source_update": "2024-12-31",
            "raw_source": "eastmoney_fund",
        },
        {
            "subject_code": SUBJECT_RUIYUAN["subject_code"],
            "subject_name": SUBJECT_RUIYUAN["subject_name"],
            "subject_type": SUBJECT_RUIYUAN["subject_type"],
            "quarter_key": "2025Q1",
            "quarter_label": "2025Q1",
            "compare_quarter_key": "2024Q3",
            "end_date": "2025-03-31",
            "stock_code": "000001",
            "stock_name": "平安银行",
            "holders_count": 1,
            "hold_num_shares": 1_500_000,
            "hold_market_value_cny": 15_000_000,
            "net_value_ratio_pct": 1.5,
            "free_hold_ratio_pct": 0.0,
            "hold_ratio_pct": 0.0,
            "latest_source_update": "2025-03-31",
            "raw_source": "eastmoney_fund",
        },
    ]

    rows = build_change_rows(SUBJECT_RUIYUAN, snapshots)
    target = next(row for row in rows if row["quarter_key"] == "2025Q1" and row["stock_code"] == "000001")
    assert target["compare_quarter_key"] == "2024Q4"
    assert target["change_type"] == "增持"
    assert target["delta_hold_num_shares"] == 500_000
    assert target["delta_ratio_pct"] == 0.5
