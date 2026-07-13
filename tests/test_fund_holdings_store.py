# -*- coding: utf-8 -*-
import pytest

import domains.fund_holdings.store as fund_store_module
from core.data_store import DataStore
from core.fund_holdings_compare import SUBJECT_QFII, SUBJECT_RUIYUAN, build_qfii_snapshots, build_ruiyuan_snapshots
from core.fund_holdings_store import FundHoldingsStore


@pytest.fixture
def fund_store(tmp_path):
    DataStore._instance = None
    store = DataStore(db_path=str(tmp_path / "fund_holdings_test.db"))
    try:
        yield store
    finally:
        store.close()
        DataStore._instance = None


def _qfii_raw_row(holder_name, *, secucode=None, **overrides):
    row = {
        "SECURITY_CODE": "000001",
        "SECURITY_NAME_ABBR": "平安银行",
        "HOLDER_NAME": holder_name,
        "HOLDER_RANK": 1,
        "HOLD_NUM": 1000,
        "HOLDER_MARKET_CAP": 10000,
        "FREE_HOLDNUM_RATIO": 0.5,
        "HOLD_RATIO": 0.10,
        "UPDATE_DATE": "2026-04-18 00:00:00",
        "HOLDER_TYPE": "QFII",
        "HOLDER_NEWTYPE": "QFII",
        "HOLD_CHANGE": "新进",
    }
    if secucode:
        row = {"SECUCODE": secucode, **row}
    row.update(overrides)
    return row


def test_fund_holdings_store_query_change_rows_uses_signature_cache(monkeypatch, fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    calls = {"cache": 0, "qfii": 0}
    monkeypatch.setattr(repo, "_query_change_rows_signature", lambda: ("sig",))

    def _cached_rows():
        calls["cache"] += 1
        return [
            {
                "stock_code": "000001",
                "subject_name": "睿远基金",
                "quarter_key": "2025Q4",
                "change_type": "增持",
                "sort_quarter": 202504,
                "sort_value": 1,
            }
        ]

    def _qfii_rows():
        calls["qfii"] += 1
        return [
            {
                "stock_code": "300750",
                "subject_name": "QFII",
                "quarter_key": "2025Q4",
                "change_type": "新进",
                "sort_quarter": 202504,
                "sort_value": 2,
            }
        ]

    monkeypatch.setattr(repo, "_query_cached_change_rows", _cached_rows)
    monkeypatch.setattr(repo, "_query_qfii_holder_change_rows", _qfii_rows)

    first = repo.query_change_rows()
    first[0]["stock_code"] = "changed"
    second = repo.query_change_rows()

    assert calls == {"cache": 1, "qfii": 1}
    assert [row["stock_code"] for row in second] == ["300750", "000001"]

    repo.invalidate_change_rows_cache()
    repo.query_change_rows()
    assert calls == {"cache": 2, "qfii": 2}


def test_qfii_stock_code_filter_runs_before_holder_name_normalization(monkeypatch, fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    rows = []
    for quarter_key, end_date in (("2025Q3", "2025-09-30"), ("2025Q4", "2025-12-31")):
        for stock_code, stock_name, holder_name in (
            ("000001", "Ping An Bank", "Target Holder"),
            ("300750", "CATL", "Unrelated Holder"),
        ):
            rows.append(
                (
                    SUBJECT_QFII["subject_code"],
                    quarter_key,
                    end_date,
                    stock_code,
                    stock_name,
                    holder_name,
                    1,
                    1000.0,
                    10_000.0,
                    0.1,
                    0.5,
                    "2026-04-18",
                    "QFII",
                    "QFII",
                    "increase",
                    "{}",
                    "2026-04-18 12:00:00",
                )
            )
    store.executemany(
        """
        INSERT INTO fh_raw_qfii(
            subject_code, quarter_key, end_date, stock_code, stock_name, holder_name,
            holder_rank, hold_num_shares, hold_market_value_cny, hold_ratio_pct,
            free_hold_ratio_pct, update_date, holder_type, holder_newtype, change_text,
            raw_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    normalized_batches = []

    def _capture_rows(raw_rows, _subject):
        normalized_batches.append(list(raw_rows))
        return []

    monkeypatch.setattr(fund_store_module, "build_qfii_holder_change_rows", _capture_rows)

    result = repo._query_qfii_holder_change_rows(
        {"2025Q4"},
        stock_codes={"000001"},
    )

    assert result == []
    assert len(normalized_batches) == 1
    assert {row["stock_code"] for row in normalized_batches[0]} == {"000001"}
    assert {row["quarter_key"] for row in normalized_batches[0]} == {"2025Q3", "2025Q4"}


def test_fund_holdings_store_rebuilds_change_cache_for_qfii(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q3_rows = [_qfii_raw_row("A", HOLD_CHANGE="增持")]
    q4_rows = [
        _qfii_raw_row(
            "A",
            HOLD_NUM=1500,
            HOLDER_MARKET_CAP=15000,
            FREE_HOLDNUM_RATIO=0.8,
            HOLD_RATIO=0.15,
            HOLD_CHANGE="增持",
        )
    ]
    payloads = {
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": q3_rows,
            "snapshots": build_qfii_snapshots(q3_rows, SUBJECT_QFII, "2025Q3", "2025-09-30"),
        },
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    latest_map = repo.get_latest_quarter_map()
    assert latest_map[SUBJECT_QFII["subject_code"]] == "2025Q4"

    change_rows = repo.query_change_rows()
    target = next(
        row
        for row in change_rows
        if row["subject_code"] == SUBJECT_QFII["subject_code"] and row["quarter_key"] == "2025Q4"
    )
    assert target["change_type"] == "增持"
    assert target["delta_hold_num_shares"] == 500
    assert target["delta_ratio_pct"] == 0.04999999999999999

    filtered_rows = repo.query_change_rows(quarter_keys={"2025Q4"})
    filtered_target = next(
        row
        for row in filtered_rows
        if row["subject_code"] == SUBJECT_QFII["subject_code"] and row["quarter_key"] == "2025Q4"
    )
    assert {row["quarter_key"] for row in filtered_rows} == {"2025Q4"}
    assert filtered_target["change_type"] == target["change_type"]
    assert filtered_target["delta_hold_num_shares"] == 500

    sync_map = repo.get_latest_sync_map()
    assert sync_map[SUBJECT_QFII["subject_code"]]["resolved_quarter_key"] == "2025Q4"


def test_fund_holdings_store_refreshes_existing_compare_quarter_cache(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        {
            "rank_no": 1,
            "stock_code": "000001",
            "stock_name": "平安银行",
            "net_value_ratio_pct": 1.0,
            "hold_num_shares": 1_000_000,
            "hold_market_value_cny": 10_000_000,
            "latest_source_update": "2024-12-31",
        }
    ]
    q1_rows = [
        {
            "rank_no": 1,
            "stock_code": "000001",
            "stock_name": "平安银行",
            "net_value_ratio_pct": 1.5,
            "hold_num_shares": 1_500_000,
            "hold_market_value_cny": 15_000_000,
            "latest_source_update": "2025-03-31",
        }
    ]
    payloads = {
        "2024Q4": {
            "quarter_key": "2024Q4",
            "end_date": "2024-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_ruiyuan_snapshots(q4_rows, SUBJECT_RUIYUAN, "2024Q4", "2024-12-31"),
        },
        "2025Q1": {
            "quarter_key": "2025Q1",
            "end_date": "2025-03-31",
            "raw_rows": q1_rows,
            "snapshots": build_ruiyuan_snapshots(q1_rows, SUBJECT_RUIYUAN, "2025Q1", "2025-03-31"),
        },
    }

    repo.replace_ruiyuan_quarters(
        SUBJECT_RUIYUAN,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q1",
        resolved_quarter_key="2025Q1",
        message="睿远指定季度 2025Q1 已同步",
    )

    store.execute(
        """
        UPDATE fh_snapshot
        SET compare_quarter_key = ?, updated_at = ?
        WHERE subject_code = ? AND quarter_key = ?
        """,
        ("2024Q3", "2026-04-18 00:00:00", SUBJECT_RUIYUAN["subject_code"], "2025Q1"),
    )
    store.execute(
        """
        UPDATE fh_change_cache
        SET compare_quarter_key = ?, delta_hold_num_shares = ?, delta_ratio_pct = ?, updated_at = ?
        WHERE subject_code = ? AND quarter_key = ?
        """,
        ("2024Q3", 0, 0, "2026-04-18 00:00:00", SUBJECT_RUIYUAN["subject_code"], "2025Q1"),
    )

    refreshed_repo = FundHoldingsStore(store=store)
    snapshot_row = store.fetch_one(
        """
        SELECT compare_quarter_key
        FROM fh_snapshot
        WHERE subject_code = ? AND quarter_key = ? AND stock_code = ?
        """,
        (SUBJECT_RUIYUAN["subject_code"], "2025Q1", "000001"),
    )
    assert snapshot_row["compare_quarter_key"] == "2024Q4"

    change_rows = refreshed_repo.query_change_rows()
    target = next(
        row
        for row in change_rows
        if row["subject_code"] == SUBJECT_RUIYUAN["subject_code"] and row["quarter_key"] == "2025Q1"
    )
    assert target["compare_quarter_key"] == "2024Q4"
    assert target["change_type"] == "增持"
    assert target["delta_hold_num_shares"] == 500_000
    assert target["delta_ratio_pct"] == 0.5


def test_fund_holdings_store_uses_hold_num_only_for_change_type(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        {
            "rank_no": 1,
            "stock_code": "000001",
            "stock_name": "平安银行",
            "net_value_ratio_pct": 1.0,
            "hold_num_shares": 1_000_000,
            "hold_market_value_cny": 10_000_000,
            "latest_source_update": "2024-12-31",
        }
    ]
    q1_rows = [
        {
            "rank_no": 1,
            "stock_code": "000001",
            "stock_name": "平安银行",
            "net_value_ratio_pct": 1.5,
            "hold_num_shares": 1_000_000,
            "hold_market_value_cny": 15_000_000,
            "latest_source_update": "2025-03-31",
        }
    ]
    payloads = {
        "2024Q4": {
            "quarter_key": "2024Q4",
            "end_date": "2024-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_ruiyuan_snapshots(q4_rows, SUBJECT_RUIYUAN, "2024Q4", "2024-12-31"),
        },
        "2025Q1": {
            "quarter_key": "2025Q1",
            "end_date": "2025-03-31",
            "raw_rows": q1_rows,
            "snapshots": build_ruiyuan_snapshots(q1_rows, SUBJECT_RUIYUAN, "2025Q1", "2025-03-31"),
        },
    }

    repo.replace_ruiyuan_quarters(
        SUBJECT_RUIYUAN,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q1",
        resolved_quarter_key="2025Q1",
        message="睿远指定季度 2025Q1 已同步",
    )

    change_rows = repo.query_change_rows()
    target = next(
        row
        for row in change_rows
        if row["subject_code"] == SUBJECT_RUIYUAN["subject_code"] and row["quarter_key"] == "2025Q1"
    )
    assert target["change_type"] == "持平"
    assert target["delta_hold_num_shares"] == 0
    assert target["delta_ratio_pct"] == 0.5


def test_fund_holdings_store_qfii_subject_names_show_holder_names_and_skip_hk(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        _qfii_raw_row("阿布达比投资局"),
        _qfii_raw_row(
            "科威特政府投资局",
            HOLDER_RANK=2,
            HOLD_NUM=500,
            HOLDER_MARKET_CAP=5000,
            FREE_HOLDNUM_RATIO=0.2,
            HOLD_RATIO=0.05,
        ),
        _qfii_raw_row(
            "香港样本",
            SECURITY_CODE="00700",
            SECURITY_NAME_ABBR="腾讯控股",
            HOLDER_RANK=3,
            HOLD_NUM=300,
            HOLDER_MARKET_CAP=3000,
            FREE_HOLDNUM_RATIO=0.1,
            HOLD_RATIO=0.03,
        ),
    ]
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    change_rows = repo.query_change_rows()
    qfii_rows = [row for row in change_rows if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"]
    assert len(qfii_rows) == 2
    assert {row["subject_name"] for row in qfii_rows} == {"阿布达比投资局", "科威特政府投资局"}
    assert {row["change_type"] for row in qfii_rows} == {"新进"}

    raw_rows = store.fetch_all(
        """
        SELECT stock_code
        FROM fh_raw_qfii
        WHERE subject_code = ? AND quarter_key = ?
        ORDER BY stock_code ASC
        """,
        (SUBJECT_QFII["subject_code"], "2025Q4"),
    )
    assert [row["stock_code"] for row in raw_rows] == ["000001", "000001"]


def test_fund_holdings_store_qfii_prefers_current_listed_code_over_old_nq_code(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        _qfii_raw_row(
            "BARCLAYS BANK PLC",
            secucode="872731.NQ",
            SECURITY_CODE="872731",
            SECURITY_NAME_ABBR="德石股份",
            HOLD_NUM=542381,
            HOLDER_MARKET_CAP=6042124.34,
            FREE_HOLDNUM_RATIO=0.3607,
            HOLD_RATIO=0.3607,
        ),
        _qfii_raw_row(
            "BARCLAYS BANK PLC",
            secucode="301158.SZ",
            SECURITY_CODE="301158",
            SECURITY_NAME_ABBR="德石股份",
            HOLD_NUM=542381,
            HOLDER_MARKET_CAP=12133062.97,
            FREE_HOLDNUM_RATIO=0.3607,
            HOLD_RATIO=0.3607,
        ),
    ]
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    change_rows = [
        row
        for row in repo.query_change_rows()
        if row["quarter_key"] == "2025Q4" and row["subject_name"] == "BARCLAYS BANK PLC"
    ]
    assert len(change_rows) == 1
    assert change_rows[0]["stock_code"] == "301158"

    raw_rows = store.fetch_all(
        """
        SELECT stock_code
        FROM fh_raw_qfii
        WHERE subject_code = ? AND quarter_key = ?
        ORDER BY stock_code ASC
        """,
        (SUBJECT_QFII["subject_code"], "2025Q4"),
    )
    assert [row["stock_code"] for row in raw_rows] == ["301158"]


def test_fund_holdings_store_qfii_exit_rows_fallback_to_compare_quarter_holder_names(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q3_rows = [_qfii_raw_row("阿布达比投资局")]
    payloads = {
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": q3_rows,
            "snapshots": build_qfii_snapshots(q3_rows, SUBJECT_QFII, "2025Q3", "2025-09-30"),
        },
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": [
                _qfii_raw_row(
                    "示例QFII",
                    SECURITY_CODE="000002",
                    SECURITY_NAME_ABBR="万科A",
                    HOLD_NUM=800,
                    HOLDER_MARKET_CAP=8000,
                    FREE_HOLDNUM_RATIO=0.4,
                    HOLD_RATIO=0.08,
                )
            ],
            "snapshots": build_qfii_snapshots(
                [
                    {
                        "SECURITY_CODE": "000002",
                        "SECURITY_NAME_ABBR": "万科A",
                        "HOLDER_NAME": "示例QFII",
                        "HOLDER_RANK": 1,
                        "HOLD_NUM": 800,
                        "HOLDER_MARKET_CAP": 8000,
                        "FREE_HOLDNUM_RATIO": 0.4,
                        "HOLD_RATIO": 0.08,
                        "UPDATE_DATE": "2026-04-18 00:00:00",
                    }
                ],
                SUBJECT_QFII,
                "2025Q4",
                "2025-12-31",
            ),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    change_rows = repo.query_change_rows()
    target = next(row for row in change_rows if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001")
    assert target["change_type"] == "退出"
    assert target["subject_name"] == "阿布达比投资局"


def test_fund_holdings_store_qfii_multiple_holders_same_stock_track_independently(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q3_rows = [_qfii_raw_row("阿布达比投资局", HOLD_CHANGE="增持")]
    q4_rows = [
        _qfii_raw_row(
            "阿布达比投资局",
            HOLD_NUM=1500,
            HOLDER_MARKET_CAP=15000,
            FREE_HOLDNUM_RATIO=0.8,
            HOLD_RATIO=0.15,
            HOLD_CHANGE="增持",
        ),
        _qfii_raw_row(
            "科威特政府投资局",
            HOLDER_RANK=2,
            HOLD_NUM=500,
            HOLDER_MARKET_CAP=5000,
            FREE_HOLDNUM_RATIO=0.2,
            HOLD_RATIO=0.05,
        ),
    ]
    payloads = {
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": q3_rows,
            "snapshots": build_qfii_snapshots(q3_rows, SUBJECT_QFII, "2025Q3", "2025-09-30"),
        },
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    q4_rows = [
        row for row in repo.query_change_rows() if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
    ]
    assert len(q4_rows) == 2

    abudhabi_row = next(row for row in q4_rows if row["subject_name"] == "阿布达比投资局")
    kuwait_row = next(row for row in q4_rows if row["subject_name"] == "科威特政府投资局")
    assert abudhabi_row["change_type"] == "增持"
    assert abudhabi_row["delta_hold_num_shares"] == 500
    assert kuwait_row["change_type"] == "新进"
    assert kuwait_row["delta_hold_num_shares"] == 500


def test_fund_holdings_store_qfii_holder_names_merge_when_only_spaces_differ(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        _qfii_raw_row("UBSAG"),
        _qfii_raw_row("UBS AG"),
    ]
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    q4_rows = [
        row for row in repo.query_change_rows() if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
    ]
    assert len(q4_rows) == 1
    assert q4_rows[0]["subject_name"] == "UBS AG"

    raw_rows = store.fetch_all(
        """
        SELECT holder_name
        FROM fh_raw_qfii
        WHERE subject_code = ? AND quarter_key = ?
        ORDER BY holder_name ASC
        """,
        (SUBJECT_QFII["subject_code"], "2025Q4"),
    )
    assert [row["holder_name"] for row in raw_rows] == ["UBS AG"]


def test_fund_holdings_store_qfii_holder_names_merge_when_only_spaces_differ_across_quarters(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q3_rows = [
        _qfii_raw_row("UBSAG"),
    ]
    q4_rows = [
        _qfii_raw_row(
            "UBS AG",
            HOLD_NUM=1500,
            HOLDER_MARKET_CAP=15000,
            FREE_HOLDNUM_RATIO=0.8,
            HOLD_RATIO=0.15,
            HOLD_CHANGE="增持",
        ),
    ]
    payloads = {
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": q3_rows,
            "snapshots": build_qfii_snapshots(q3_rows, SUBJECT_QFII, "2025Q3", "2025-09-30"),
        },
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    qfii_rows = [
        row for row in repo.query_change_rows() if row["stock_code"] == "000001" and row["subject_name"] == "UBS AG"
    ]
    assert len(qfii_rows) == 2

    q4_row = next(row for row in qfii_rows if row["quarter_key"] == "2025Q4")
    q3_row = next(row for row in qfii_rows if row["quarter_key"] == "2025Q3")
    assert q4_row["change_type"] == "增持"
    assert q4_row["delta_hold_num_shares"] == 500
    assert q3_row["change_type"] == "新进"

    raw_rows = store.fetch_all(
        """
        SELECT quarter_key, holder_name
        FROM fh_raw_qfii
        WHERE subject_code = ? AND stock_code = ?
        ORDER BY quarter_key ASC
        """,
        (SUBJECT_QFII["subject_code"], "000001"),
    )
    assert [(row["quarter_key"], row["holder_name"]) for row in raw_rows] == [
        ("2025Q3", "UBS AG"),
        ("2025Q4", "UBS AG"),
    ]


def test_fund_holdings_store_qfii_holder_names_merge_when_only_dots_differ(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        _qfii_raw_row("MORGAN STANLEY & CO.INTERNATIONAL PLC"),
        _qfii_raw_row("MORGAN STANLEY & CO.INTERNATIONAL PLC."),
    ]
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    qfii_rows = [
        row for row in repo.query_change_rows() if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
    ]
    assert len(qfii_rows) == 1
    assert qfii_rows[0]["subject_name"] == "MORGAN STANLEY & CO.INTERNATIONAL PLC"

    raw_rows = store.fetch_all(
        """
        SELECT holder_name
        FROM fh_raw_qfii
        WHERE subject_code = ? AND quarter_key = ?
        ORDER BY holder_name ASC
        """,
        (SUBJECT_QFII["subject_code"], "2025Q4"),
    )
    assert [row["holder_name"] for row in raw_rows] == ["MORGAN STANLEY & CO.INTERNATIONAL PLC"]


def test_fund_holdings_store_qfii_groups_rows_by_institution_and_capital_attribute(fund_store):
    store = fund_store
    repo = FundHoldingsStore(store=store)
    q4_rows = [
        _qfii_raw_row("MORGAN STANLEY&CO.INTERNATIONAL PLC.-自有资金"),
        _qfii_raw_row(
            "MORGAN STANLEY & CO.INTERNATIONAL PLC",
            HOLDER_RANK=2,
            HOLD_NUM=1500,
            HOLDER_MARKET_CAP=15000,
            FREE_HOLDNUM_RATIO=0.8,
            HOLD_RATIO=0.15,
        ),
    ]
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": q4_rows,
            "snapshots": build_qfii_snapshots(q4_rows, SUBJECT_QFII, "2025Q4", "2025-12-31"),
        },
    }

    repo.replace_qfii_quarters(
        SUBJECT_QFII,
        payloads,
        sync_scope="specific",
        requested_quarter_key="2025Q4",
        resolved_quarter_key="2025Q4",
        message="QFII 指定季度 2025Q4 已同步",
    )

    qfii_rows = [
        row for row in repo.query_change_rows() if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
    ]
    assert len(qfii_rows) == 2
    assert {row["subject_name"] for row in qfii_rows} == {"MORGAN STANLEY & CO.INTERNATIONAL PLC"}
    assert {row["capital_attribute"] for row in qfii_rows} == {"自有资金", "未标注"}
