# -*- coding: utf-8 -*-
import pytest

from core.fund_holdings_compare import (
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    build_change_rows,
    build_qfii_holder_change_rows,
    build_qfii_snapshots,
    dedupe_qfii_raw_rows,
    get_compare_quarter_key,
    normalize_holder_name,
    normalize_quarter_key,
)


@pytest.mark.parametrize("names", [("平安银行", "万科A"), ("同名标的", "同名标的"), ("", "")])
def test_qfii_dedupe_preserves_distinct_securities_with_identical_holdings(names):
    rows = [
        {
            "quarter_key": "2026Q2",
            "SECUCODE": f"{code}.SZ",
            "SECURITY_CODE": code,
            "SECURITY_NAME_ABBR": name,
            "HOLDER_NAME": "BARCLAYS BANK PLC",
            "HOLDER_RANK": 1,
            "HOLD_NUM": 1_000_000,
            "HOLDER_MARKET_CAP": 10_000_000,
            "HOLD_RATIO": 0.1,
            "FREE_HOLDNUM_RATIO": 0.2,
        }
        for code, name in zip(("000001", "000002"), names, strict=True)
    ]

    result = dedupe_qfii_raw_rows(rows)

    assert {row["SECURITY_CODE"] for row in result} == {"000001", "000002"}


def test_qfii_dedupe_does_not_infer_unverified_nq_alias_from_identical_holdings():
    rows = [
        {
            "quarter_key": "2026Q2",
            "SECUCODE": secucode,
            "SECURITY_CODE": secucode.split(".")[0],
            "SECURITY_NAME_ABBR": "同名标的",
            "HOLDER_NAME": "BARCLAYS BANK PLC",
            "HOLD_NUM": 1_000_000,
            "HOLD_RATIO": 0.1,
            "FREE_HOLDNUM_RATIO": 0.2,
        }
        for secucode in ("831999.NQ", "301999.SZ")
    ]

    assert len(dedupe_qfii_raw_rows(rows)) == 2


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
            {
                "SECURITY_CODE": "00700",
                "SECURITY_NAME_ABBR": "腾讯控股",
                "HOLDER_NAME": "HK",
                "HOLDER_RANK": 3,
                "HOLD_NUM": 500,
                "HOLDER_MARKET_CAP": 5000,
                "FREE_HOLDNUM_RATIO": 0.1,
                "HOLD_RATIO": 0.05,
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


def test_build_qfii_snapshots_prefers_current_listed_code_over_old_nq_code():
    snapshots = build_qfii_snapshots(
        [
            {
                "SECUCODE": "872731.NQ",
                "SECURITY_CODE": "872731",
                "SECURITY_NAME_ABBR": "德石股份",
                "HOLDER_NAME": "BARCLAYS BANK PLC",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 542381,
                "HOLDER_MARKET_CAP": 6042124.34,
                "FREE_HOLDNUM_RATIO": 0.3607,
                "HOLD_RATIO": 0.3607,
                "UPDATE_DATE": "2026-04-18 00:00:00",
            },
            {
                "SECUCODE": "301158.SZ",
                "SECURITY_CODE": "301158",
                "SECURITY_NAME_ABBR": "德石股份",
                "HOLDER_NAME": "BARCLAYS BANK PLC",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 542381,
                "HOLDER_MARKET_CAP": 12133062.97,
                "FREE_HOLDNUM_RATIO": 0.3607,
                "HOLD_RATIO": 0.3607,
                "UPDATE_DATE": "2026-04-18 00:00:00",
            },
        ],
        SUBJECT_QFII,
        "2025Q4",
        "2025-12-31",
    )

    assert len(snapshots) == 1
    assert snapshots[0]["stock_code"] == "301158"
    assert snapshots[0]["stock_name"] == "德石股份"
    assert snapshots[0]["holders_count"] == 1


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


def test_build_change_rows_uses_hold_num_only_for_change_type():
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
            "hold_num_shares": 1_000_000,
            "hold_market_value_cny": 12_000_000,
            "net_value_ratio_pct": 1.5,
            "free_hold_ratio_pct": 0.0,
            "hold_ratio_pct": 0.0,
            "latest_source_update": "2025-03-31",
            "raw_source": "eastmoney_fund",
        },
    ]

    rows = build_change_rows(SUBJECT_RUIYUAN, snapshots)
    target = next(row for row in rows if row["quarter_key"] == "2025Q1" and row["stock_code"] == "000001")
    assert target["change_type"] == "持平"
    assert target["delta_hold_num_shares"] == 0
    assert target["delta_ratio_pct"] == 0.5


def test_build_qfii_holder_change_rows_tracks_each_holder_individually():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q3",
                "end_date": "2025-09-30",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "阿布达比投资局",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-09-30",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "阿布达比投资局",
                "hold_num_shares": 1500,
                "hold_market_value_cny": 15_000,
                "hold_ratio_pct": 0.15,
                "free_hold_ratio_pct": 0.25,
                "update_date": "2025-12-31",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "科威特政府投资局",
                "hold_num_shares": 500,
                "hold_market_value_cny": 5_000,
                "hold_ratio_pct": 0.05,
                "free_hold_ratio_pct": 0.08,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    abudhabi_row = next(
        row for row in rows if row["quarter_key"] == "2025Q4" and row["subject_name"] == "阿布达比投资局"
    )
    kuwait_row = next(
        row for row in rows if row["quarter_key"] == "2025Q4" and row["subject_name"] == "科威特政府投资局"
    )

    assert abudhabi_row["change_type"] == "增持"
    assert abudhabi_row["delta_hold_num_shares"] == 500
    assert kuwait_row["change_type"] == "新进"
    assert kuwait_row["delta_hold_num_shares"] == 500


def test_build_qfii_holder_change_rows_prefers_current_listed_code_over_old_nq_code():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "872731",
                "stock_name": "德石股份",
                "holder_name": "BARCLAYS BANK PLC",
                "hold_num_shares": 542381,
                "hold_market_value_cny": 6042124.34,
                "hold_ratio_pct": 0.3607,
                "free_hold_ratio_pct": 0.3607,
                "update_date": "2025-12-31",
                "raw_json": '{"SECUCODE":"872731.NQ"}',
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "301158",
                "stock_name": "德石股份",
                "holder_name": "BARCLAYS BANK PLC",
                "hold_num_shares": 542381,
                "hold_market_value_cny": 12133062.97,
                "hold_ratio_pct": 0.3607,
                "free_hold_ratio_pct": 0.3607,
                "update_date": "2025-12-31",
                "raw_json": '{"SECUCODE":"301158.SZ"}',
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 1
    assert rows[0]["stock_code"] == "301158"
    assert rows[0]["stock_name"] == "德石股份"
    assert rows[0]["change_type"] == "新进"


def test_build_qfii_holder_change_rows_prefers_current_listed_code_when_name_changed():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2026Q1",
                "end_date": "2026-03-31",
                "stock_code": "831243",
                "stock_name": "晓鸣农牧",
                "holder_name": "BARCLAYS BANK PLC",
                "holder_rank": 8,
                "hold_num_shares": 834097,
                "hold_market_value_cny": 13262142.3,
                "hold_ratio_pct": 0.4447,
                "free_hold_ratio_pct": 0.668384439284,
                "update_date": "2026-03-31",
                "raw_json": '{"SECUCODE":"831243.NQ"}',
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2026Q1",
                "end_date": "2026-03-31",
                "stock_code": "300967",
                "stock_name": "晓鸣股份",
                "holder_name": "BARCLAYS BANK PLC",
                "holder_rank": 8,
                "hold_num_shares": 834097,
                "hold_market_value_cny": 16860479.34,
                "hold_ratio_pct": 0.4447,
                "free_hold_ratio_pct": 0.668384439284,
                "update_date": "2026-03-31",
                "raw_json": '{"SECUCODE":"300967.SZ"}',
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 1
    assert rows[0]["stock_code"] == "300967"
    assert rows[0]["stock_name"] == "晓鸣股份"


def test_normalize_holder_name_collapses_space_only_differences():
    assert normalize_holder_name("  UBS   AG  ") == "UBS AG"
    assert normalize_holder_name("J.P.Morgan Securities PLC - 自有资金") == "J.P.Morgan Securities PLC-自有资金"
    assert normalize_holder_name("中国 国际 金融 香港 资产 管理 有限公司") == "中国国际金融香港资产管理有限公司"


def test_build_qfii_holder_change_rows_merges_holder_names_that_only_differ_by_spaces():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "UBSAG",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "UBS AG",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 1
    assert rows[0]["subject_name"] == "UBS AG"


def test_build_qfii_holder_change_rows_merges_holder_names_that_only_differ_by_spaces_across_quarters():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q3",
                "end_date": "2025-09-30",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "UBSAG",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-09-30",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "UBS AG",
                "hold_num_shares": 1500,
                "hold_market_value_cny": 15_000,
                "hold_ratio_pct": 0.15,
                "free_hold_ratio_pct": 0.25,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    q4_rows = [row for row in rows if row["quarter_key"] == "2025Q4"]
    q3_rows = [row for row in rows if row["quarter_key"] == "2025Q3"]

    assert len(q4_rows) == 1
    assert len(q3_rows) == 1
    assert q4_rows[0]["subject_name"] == "UBS AG"
    assert q3_rows[0]["subject_name"] == "UBS AG"
    assert q4_rows[0]["change_type"] == "增持"
    assert q4_rows[0]["delta_hold_num_shares"] == 500


def test_build_qfii_holder_change_rows_merges_holder_names_that_only_differ_by_dots():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "MORGAN STANLEY & CO.INTERNATIONAL PLC",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "MORGAN STANLEY & CO.INTERNATIONAL PLC.",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 1
    assert rows[0]["subject_name"] == "MORGAN STANLEY & CO.INTERNATIONAL PLC"


def test_build_qfii_holder_change_rows_uses_institution_name_and_capital_attribute():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "MORGAN STANLEY&CO.INTERNATIONAL PLC.-自有资金",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "MORGAN STANLEY & CO.INTERNATIONAL PLC",
                "hold_num_shares": 1500,
                "hold_market_value_cny": 15_000,
                "hold_ratio_pct": 0.15,
                "free_hold_ratio_pct": 0.25,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 2
    assert {row["subject_name"] for row in rows} == {"MORGAN STANLEY & CO.INTERNATIONAL PLC"}
    assert {row["capital_attribute"] for row in rows} == {"自有资金", "未标注"}


def test_build_qfii_holder_change_rows_merges_same_institution_self_owned_variants():
    rows = build_qfii_holder_change_rows(
        [
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "J.P.Morgan Securities PLC-自有资金",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
            {
                "subject_code": SUBJECT_QFII["subject_code"],
                "quarter_key": "2025Q4",
                "end_date": "2025-12-31",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "holder_name": "J.PMorgan Securities PLC-自有资金",
                "hold_num_shares": 1000,
                "hold_market_value_cny": 10_000,
                "hold_ratio_pct": 0.10,
                "free_hold_ratio_pct": 0.20,
                "update_date": "2025-12-31",
            },
        ],
        SUBJECT_QFII,
    )

    assert len(rows) == 1
    assert rows[0]["subject_name"] == "J.P.Morgan Securities PLC"
    assert rows[0]["capital_attribute"] == "自有资金"
