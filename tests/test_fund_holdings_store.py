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
