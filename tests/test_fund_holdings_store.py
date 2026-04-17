# -*- coding: utf-8 -*-
import os
import tempfile

from core.data_store import DataStore
from core.fund_holdings_compare import SUBJECT_QFII, SUBJECT_RUIYUAN, build_qfii_snapshots, build_ruiyuan_snapshots
from core.fund_holdings_store import FundHoldingsStore


def _make_store():
    DataStore._instance = None
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "fund_holdings_test.db")
    store = DataStore(db_path=db_path)
    return store, db_path


def test_fund_holdings_store_rebuilds_change_cache_for_qfii():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q3_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "A",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "增持",
            }
        ]
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "A",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1500,
                "HOLDER_MARKET_CAP": 15000,
                "FREE_HOLDNUM_RATIO": 0.8,
                "HOLD_RATIO": 0.15,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "增持",
            }
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
        target = next(row for row in change_rows if row["subject_code"] == SUBJECT_QFII["subject_code"] and row["quarter_key"] == "2025Q4")
        assert target["change_type"] == "增持"
        assert target["delta_hold_num_shares"] == 500
        assert target["delta_ratio_pct"] == 0.04999999999999999

        sync_map = repo.get_latest_sync_map()
        assert sync_map[SUBJECT_QFII["subject_code"]]["resolved_quarter_key"] == "2025Q4"
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_refreshes_existing_compare_quarter_cache():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
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
        target = next(row for row in change_rows if row["subject_code"] == SUBJECT_RUIYUAN["subject_code"] and row["quarter_key"] == "2025Q1")
        assert target["compare_quarter_key"] == "2024Q4"
        assert target["change_type"] == "增持"
        assert target["delta_hold_num_shares"] == 500_000
        assert target["delta_ratio_pct"] == 0.5
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_uses_hold_num_only_for_change_type():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
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
        target = next(row for row in change_rows if row["subject_code"] == SUBJECT_RUIYUAN["subject_code"] and row["quarter_key"] == "2025Q1")
        assert target["change_type"] == "持平"
        assert target["delta_hold_num_shares"] == 0
        assert target["delta_ratio_pct"] == 0.5
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_subject_names_show_holder_names_and_skip_hk():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "阿布达比投资局",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "科威特政府投资局",
                "HOLDER_RANK": 2,
                "HOLD_NUM": 500,
                "HOLDER_MARKET_CAP": 5000,
                "FREE_HOLDNUM_RATIO": 0.2,
                "HOLD_RATIO": 0.05,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
            {
                "SECURITY_CODE": "00700",
                "SECURITY_NAME_ABBR": "腾讯控股",
                "HOLDER_NAME": "香港样本",
                "HOLDER_RANK": 3,
                "HOLD_NUM": 300,
                "HOLDER_MARKET_CAP": 3000,
                "FREE_HOLDNUM_RATIO": 0.1,
                "HOLD_RATIO": 0.03,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
        qfii_rows = [
            row for row in change_rows
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
        ]
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
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_prefers_current_listed_code_over_old_nq_code():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q4_rows = [
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
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
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
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
            row for row in repo.query_change_rows()
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
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_exit_rows_fallback_to_compare_quarter_holder_names():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q3_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "阿布达比投资局",
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
                "raw_rows": [
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
                        "HOLDER_TYPE": "QFII",
                        "HOLDER_NEWTYPE": "QFII",
                        "HOLD_CHANGE": "新进",
                    }
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
        target = next(
            row for row in change_rows
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
        )
        assert target["change_type"] == "退出"
        assert target["subject_name"] == "阿布达比投资局"
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_multiple_holders_same_stock_track_independently():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q3_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "阿布达比投资局",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "增持",
            }
        ]
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "阿布达比投资局",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1500,
                "HOLDER_MARKET_CAP": 15000,
                "FREE_HOLDNUM_RATIO": 0.8,
                "HOLD_RATIO": 0.15,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "增持",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "科威特政府投资局",
                "HOLDER_RANK": 2,
                "HOLD_NUM": 500,
                "HOLDER_MARKET_CAP": 5000,
                "FREE_HOLDNUM_RATIO": 0.2,
                "HOLD_RATIO": 0.05,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
            row for row in repo.query_change_rows()
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
        ]
        assert len(q4_rows) == 2

        abudhabi_row = next(row for row in q4_rows if row["subject_name"] == "阿布达比投资局")
        kuwait_row = next(row for row in q4_rows if row["subject_name"] == "科威特政府投资局")
        assert abudhabi_row["change_type"] == "增持"
        assert abudhabi_row["delta_hold_num_shares"] == 500
        assert kuwait_row["change_type"] == "新进"
        assert kuwait_row["delta_hold_num_shares"] == 500
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_holder_names_merge_when_only_spaces_differ():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "UBSAG",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "UBS AG",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
            row for row in repo.query_change_rows()
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
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
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_holder_names_merge_when_only_spaces_differ_across_quarters():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q3_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "UBSAG",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
        ]
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "UBS AG",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1500,
                "HOLDER_MARKET_CAP": 15000,
                "FREE_HOLDNUM_RATIO": 0.8,
                "HOLD_RATIO": 0.15,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "增持",
            },
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
            row for row in repo.query_change_rows()
            if row["stock_code"] == "000001" and row["subject_name"] == "UBS AG"
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
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_holder_names_merge_when_only_dots_differ():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "MORGAN STANLEY & CO.INTERNATIONAL PLC",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "MORGAN STANLEY & CO.INTERNATIONAL PLC.",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
            row for row in repo.query_change_rows()
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
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
    finally:
        store.close()
        os.remove(db_path)


def test_fund_holdings_store_qfii_groups_rows_by_institution_and_capital_attribute():
    store, db_path = _make_store()
    repo = FundHoldingsStore(store=store)
    try:
        q4_rows = [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "MORGAN STANLEY&CO.INTERNATIONAL PLC.-自有资金",
                "HOLDER_RANK": 1,
                "HOLD_NUM": 1000,
                "HOLDER_MARKET_CAP": 10000,
                "FREE_HOLDNUM_RATIO": 0.5,
                "HOLD_RATIO": 0.10,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "HOLDER_NAME": "MORGAN STANLEY & CO.INTERNATIONAL PLC",
                "HOLDER_RANK": 2,
                "HOLD_NUM": 1500,
                "HOLDER_MARKET_CAP": 15000,
                "FREE_HOLDNUM_RATIO": 0.8,
                "HOLD_RATIO": 0.15,
                "UPDATE_DATE": "2026-04-18 00:00:00",
                "HOLDER_TYPE": "QFII",
                "HOLDER_NEWTYPE": "QFII",
                "HOLD_CHANGE": "新进",
            },
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
            row for row in repo.query_change_rows()
            if row["quarter_key"] == "2025Q4" and row["stock_code"] == "000001"
        ]
        assert len(qfii_rows) == 2
        assert {row["subject_name"] for row in qfii_rows} == {"MORGAN STANLEY & CO.INTERNATIONAL PLC"}
        assert {row["capital_attribute"] for row in qfii_rows} == {"自有资金", "未标注"}
    finally:
        store.close()
        os.remove(db_path)
