# -*- coding: utf-8 -*-
"""基金持仓 SQLite 存储层。"""

from __future__ import annotations

import json
from datetime import datetime

from core.data_store import data_store
from core.fund_holdings_compare import (
    SUBJECT_QFII,
    SUBJECTS,
    build_change_rows,
    build_qfii_holder_change_rows,
    dedupe_qfii_raw_rows,
    get_compare_quarter_key,
    is_mainland_security_code,
    normalize_quarter_key,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fh_subject (
    subject_code TEXT PRIMARY KEY,
    subject_name TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fh_sync_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_code TEXT NOT NULL,
    sync_scope TEXT NOT NULL,
    requested_quarter_key TEXT,
    resolved_quarter_key TEXT,
    status TEXT NOT NULL,
    message TEXT,
    payload_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fh_raw_qfii (
    subject_code TEXT NOT NULL,
    quarter_key TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    holder_name TEXT NOT NULL,
    holder_rank INTEGER,
    hold_num_shares REAL,
    hold_market_value_cny REAL,
    hold_ratio_pct REAL,
    free_hold_ratio_pct REAL,
    update_date TEXT,
    holder_type TEXT,
    holder_newtype TEXT,
    change_text TEXT,
    raw_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_code, quarter_key, stock_code, holder_name)
);

CREATE TABLE IF NOT EXISTS fh_raw_ruiyuan (
    subject_code TEXT NOT NULL,
    quarter_key TEXT NOT NULL,
    end_date TEXT NOT NULL,
    rank_no INTEGER,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    net_value_ratio_pct REAL,
    hold_num_shares REAL,
    hold_market_value_cny REAL,
    latest_source_update TEXT,
    raw_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_code, quarter_key, stock_code)
);

CREATE TABLE IF NOT EXISTS fh_snapshot (
    subject_code TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    quarter_key TEXT NOT NULL,
    quarter_label TEXT NOT NULL,
    compare_quarter_key TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    holders_count INTEGER NOT NULL DEFAULT 0,
    hold_num_shares REAL NOT NULL DEFAULT 0,
    hold_market_value_cny REAL NOT NULL DEFAULT 0,
    net_value_ratio_pct REAL NOT NULL DEFAULT 0,
    free_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    hold_ratio_pct REAL NOT NULL DEFAULT 0,
    latest_source_update TEXT,
    raw_source TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_code, quarter_key, stock_code)
);

CREATE TABLE IF NOT EXISTS fh_change_cache (
    subject_code TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    quarter_key TEXT NOT NULL,
    compare_quarter_key TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    change_type TEXT NOT NULL,
    ratio_label TEXT NOT NULL,
    holders_count INTEGER NOT NULL DEFAULT 0,
    curr_hold_num_shares REAL NOT NULL DEFAULT 0,
    prev_hold_num_shares REAL NOT NULL DEFAULT 0,
    delta_hold_num_shares REAL NOT NULL DEFAULT 0,
    curr_hold_market_value_cny REAL NOT NULL DEFAULT 0,
    prev_hold_market_value_cny REAL NOT NULL DEFAULT 0,
    delta_hold_market_value_cny REAL NOT NULL DEFAULT 0,
    curr_ratio_pct REAL NOT NULL DEFAULT 0,
    prev_ratio_pct REAL NOT NULL DEFAULT 0,
    delta_ratio_pct REAL NOT NULL DEFAULT 0,
    curr_net_value_ratio_pct REAL NOT NULL DEFAULT 0,
    prev_net_value_ratio_pct REAL NOT NULL DEFAULT 0,
    delta_net_value_ratio_pct REAL NOT NULL DEFAULT 0,
    curr_free_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    prev_free_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    delta_free_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    curr_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    prev_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    delta_hold_ratio_pct REAL NOT NULL DEFAULT 0,
    latest_source_update TEXT,
    sort_quarter INTEGER NOT NULL DEFAULT 0,
    sort_value REAL NOT NULL DEFAULT 0,
    change_score REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_code, quarter_key, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_fh_snapshot_subject_quarter
ON fh_snapshot(subject_code, quarter_key);

CREATE INDEX IF NOT EXISTS idx_fh_change_subject_quarter
ON fh_change_cache(subject_code, quarter_key);

CREATE INDEX IF NOT EXISTS idx_fh_change_subject_type
ON fh_change_cache(subject_code, change_type);
"""


class FundHoldingsStore:
    def __init__(self, store=None):
        self._store = store or data_store
        self.ensure_schema()
        self.ensure_subjects()
        self.refresh_compare_cache()

    @staticmethod
    def _now_text() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def ensure_schema(self) -> None:
        self._store.execute_script(_SCHEMA_SQL)

    def ensure_subjects(self) -> None:
        now = self._now_text()
        with self._store.transaction() as cursor:
            for subject in SUBJECTS.values():
                cursor.execute(
                    """
                    INSERT INTO fh_subject(subject_code, subject_name, subject_type, display_order, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(subject_code) DO UPDATE SET
                        subject_name=excluded.subject_name,
                        subject_type=excluded.subject_type,
                        display_order=excluded.display_order,
                        updated_at=excluded.updated_at
                    """,
                    (
                        subject["subject_code"],
                        subject["subject_name"],
                        subject["subject_type"],
                        int(subject["display_order"]),
                        now,
                    ),
                )

    def refresh_compare_cache(self) -> None:
        """按当前规则重建 compare_quarter_key 与变化缓存，兼容历史老数据。"""
        updated_at = self._now_text()
        with self._store.transaction() as cursor:
            for subject in SUBJECTS.values():
                snapshot_rows = cursor.execute(
                    """
                    SELECT DISTINCT quarter_key
                    FROM fh_snapshot
                    WHERE subject_code = ?
                    """,
                    (subject["subject_code"],),
                ).fetchall()
                for row in snapshot_rows:
                    quarter_key = str(row["quarter_key"])
                    cursor.execute(
                        """
                        UPDATE fh_snapshot
                        SET compare_quarter_key = ?, updated_at = ?
                        WHERE subject_code = ? AND quarter_key = ?
                        """,
                        (
                            get_compare_quarter_key(subject["subject_type"], quarter_key),
                            updated_at,
                            subject["subject_code"],
                            quarter_key,
                        ),
                    )

                self._rebuild_change_cache_locked(cursor, subject, updated_at)

    def record_sync_run(
        self,
        *,
        subject_code: str,
        sync_scope: str,
        requested_quarter_key: str | None,
        resolved_quarter_key: str | None,
        status: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        now = self._now_text()
        self._store.execute(
            """
            INSERT INTO fh_sync_run(
                subject_code, sync_scope, requested_quarter_key, resolved_quarter_key,
                status, message, payload_json, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subject_code,
                sync_scope,
                requested_quarter_key,
                resolved_quarter_key,
                status,
                message,
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )

    def _snapshot_rows_for_subject_locked(self, cursor, subject_code: str) -> list[dict]:
        rows = cursor.execute(
            """
            SELECT
                subject_code, subject_name, subject_type, quarter_key, quarter_label,
                compare_quarter_key, end_date, stock_code, stock_name, holders_count,
                hold_num_shares, hold_market_value_cny, net_value_ratio_pct,
                free_hold_ratio_pct, hold_ratio_pct, latest_source_update, raw_source
            FROM fh_snapshot
            WHERE subject_code = ?
            ORDER BY quarter_key DESC, hold_market_value_cny DESC, stock_code ASC
            """,
            (subject_code,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _rebuild_change_cache_locked(self, cursor, subject: dict, updated_at: str) -> None:
        snapshot_rows = self._snapshot_rows_for_subject_locked(cursor, subject["subject_code"])
        change_rows = build_change_rows(subject, snapshot_rows)
        cursor.execute("DELETE FROM fh_change_cache WHERE subject_code = ?", (subject["subject_code"],))
        if not change_rows:
            return

        cursor.executemany(
            """
            INSERT INTO fh_change_cache(
                subject_code, subject_name, subject_type, quarter_key, compare_quarter_key, end_date,
                stock_code, stock_name, change_type, ratio_label, holders_count,
                curr_hold_num_shares, prev_hold_num_shares, delta_hold_num_shares,
                curr_hold_market_value_cny, prev_hold_market_value_cny, delta_hold_market_value_cny,
                curr_ratio_pct, prev_ratio_pct, delta_ratio_pct,
                curr_net_value_ratio_pct, prev_net_value_ratio_pct, delta_net_value_ratio_pct,
                curr_free_hold_ratio_pct, prev_free_hold_ratio_pct, delta_free_hold_ratio_pct,
                curr_hold_ratio_pct, prev_hold_ratio_pct, delta_hold_ratio_pct,
                latest_source_update, sort_quarter, sort_value, change_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["subject_code"],
                    row["subject_name"],
                    row["subject_type"],
                    row["quarter_key"],
                    row["compare_quarter_key"],
                    row["end_date"],
                    row["stock_code"],
                    row.get("stock_name", ""),
                    row["change_type"],
                    row["ratio_label"],
                    int(row.get("holders_count", 0) or 0),
                    float(row.get("curr_hold_num_shares", 0) or 0),
                    float(row.get("prev_hold_num_shares", 0) or 0),
                    float(row.get("delta_hold_num_shares", 0) or 0),
                    float(row.get("curr_hold_market_value_cny", 0) or 0),
                    float(row.get("prev_hold_market_value_cny", 0) or 0),
                    float(row.get("delta_hold_market_value_cny", 0) or 0),
                    float(row.get("curr_ratio_pct", 0) or 0),
                    float(row.get("prev_ratio_pct", 0) or 0),
                    float(row.get("delta_ratio_pct", 0) or 0),
                    float(row.get("curr_net_value_ratio_pct", 0) or 0),
                    float(row.get("prev_net_value_ratio_pct", 0) or 0),
                    float(row.get("delta_net_value_ratio_pct", 0) or 0),
                    float(row.get("curr_free_hold_ratio_pct", 0) or 0),
                    float(row.get("prev_free_hold_ratio_pct", 0) or 0),
                    float(row.get("delta_free_hold_ratio_pct", 0) or 0),
                    float(row.get("curr_hold_ratio_pct", 0) or 0),
                    float(row.get("prev_hold_ratio_pct", 0) or 0),
                    float(row.get("delta_hold_ratio_pct", 0) or 0),
                    row.get("latest_source_update", ""),
                    int(row.get("sort_quarter", 0) or 0),
                    float(row.get("sort_value", 0) or 0),
                    float(row.get("change_score", 0) or 0),
                    updated_at,
                )
                for row in change_rows
            ],
        )

    @staticmethod
    def _snapshot_params(snapshot: dict, updated_at: str) -> tuple:
        return (
            snapshot["subject_code"],
            snapshot["subject_name"],
            snapshot["subject_type"],
            snapshot["quarter_key"],
            snapshot["quarter_label"],
            snapshot["compare_quarter_key"],
            snapshot["end_date"],
            snapshot["stock_code"],
            snapshot.get("stock_name", ""),
            int(snapshot.get("holders_count", 0) or 0),
            float(snapshot.get("hold_num_shares", 0) or 0),
            float(snapshot.get("hold_market_value_cny", 0) or 0),
            float(snapshot.get("net_value_ratio_pct", 0) or 0),
            float(snapshot.get("free_hold_ratio_pct", 0) or 0),
            float(snapshot.get("hold_ratio_pct", 0) or 0),
            snapshot.get("latest_source_update", ""),
            snapshot.get("raw_source", ""),
            updated_at,
        )

    def replace_qfii_quarters(
        self,
        subject: dict,
        quarter_payloads: dict[str, dict],
        *,
        sync_scope: str,
        requested_quarter_key: str | None,
        resolved_quarter_key: str | None,
        message: str,
        payload_meta: dict | None = None,
    ) -> None:
        updated_at = self._now_text()
        with self._store.transaction() as cursor:
            for quarter_key, payload in sorted((quarter_payloads or {}).items()):
                norm_quarter = normalize_quarter_key(quarter_key)
                end_date = str(payload.get("end_date") or "").strip()
                raw_rows = dedupe_qfii_raw_rows([
                    dict(row)
                    for row in (payload.get("raw_rows") or [])
                    if is_mainland_security_code(row.get("SECURITY_CODE"))
                ])
                snapshots = list(payload.get("snapshots") or [])

                cursor.execute(
                    "DELETE FROM fh_raw_qfii WHERE subject_code = ? AND quarter_key = ?",
                    (subject["subject_code"], norm_quarter),
                )
                cursor.execute(
                    "DELETE FROM fh_snapshot WHERE subject_code = ? AND quarter_key = ?",
                    (subject["subject_code"], norm_quarter),
                )

                if raw_rows:
                    cursor.executemany(
                        """
                        INSERT INTO fh_raw_qfii(
                            subject_code, quarter_key, end_date, stock_code, stock_name, holder_name,
                            holder_rank, hold_num_shares, hold_market_value_cny, hold_ratio_pct,
                            free_hold_ratio_pct, update_date, holder_type, holder_newtype,
                            change_text, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                subject["subject_code"],
                                norm_quarter,
                                end_date,
                                str(row.get("SECURITY_CODE") or "").strip(),
                                str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                                str(row.get("HOLDER_NAME") or "").strip(),
                                int(float(row.get("HOLDER_RANK") or 0)),
                                float(row.get("HOLD_NUM") or 0),
                                float(row.get("HOLDER_MARKET_CAP") or 0),
                                float(row.get("HOLD_RATIO") or 0),
                                float(row.get("FREE_HOLDNUM_RATIO") or 0),
                                str(row.get("UPDATE_DATE") or "").strip(),
                                str(row.get("HOLDER_TYPE") or "").strip(),
                                str(row.get("HOLDER_NEWTYPE") or "").strip(),
                                str(row.get("HOLD_CHANGE") or row.get("HOLD_NUM_CHANGE") or "").strip(),
                                json.dumps(row, ensure_ascii=False, default=str),
                                updated_at,
                            )
                            for row in raw_rows
                        ],
                    )

                if snapshots:
                    cursor.executemany(
                        """
                        INSERT INTO fh_snapshot(
                            subject_code, subject_name, subject_type, quarter_key, quarter_label,
                            compare_quarter_key, end_date, stock_code, stock_name, holders_count,
                            hold_num_shares, hold_market_value_cny, net_value_ratio_pct,
                            free_hold_ratio_pct, hold_ratio_pct, latest_source_update, raw_source, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [self._snapshot_params(snapshot, updated_at) for snapshot in snapshots],
                    )

            self._rebuild_change_cache_locked(cursor, subject, updated_at)

        self.record_sync_run(
            subject_code=subject["subject_code"],
            sync_scope=sync_scope,
            requested_quarter_key=requested_quarter_key,
            resolved_quarter_key=resolved_quarter_key,
            status="success",
            message=message,
            payload=payload_meta,
        )

    def replace_ruiyuan_quarters(
        self,
        subject: dict,
        quarter_payloads: dict[str, dict],
        *,
        sync_scope: str,
        requested_quarter_key: str | None,
        resolved_quarter_key: str | None,
        message: str,
        payload_meta: dict | None = None,
    ) -> None:
        updated_at = self._now_text()
        with self._store.transaction() as cursor:
            for quarter_key, payload in sorted((quarter_payloads or {}).items()):
                norm_quarter = normalize_quarter_key(quarter_key)
                end_date = str(payload.get("end_date") or "").strip()
                raw_rows = list(payload.get("raw_rows") or [])
                snapshots = list(payload.get("snapshots") or [])

                cursor.execute(
                    "DELETE FROM fh_raw_ruiyuan WHERE subject_code = ? AND quarter_key = ?",
                    (subject["subject_code"], norm_quarter),
                )
                cursor.execute(
                    "DELETE FROM fh_snapshot WHERE subject_code = ? AND quarter_key = ?",
                    (subject["subject_code"], norm_quarter),
                )

                if raw_rows:
                    cursor.executemany(
                        """
                        INSERT INTO fh_raw_ruiyuan(
                            subject_code, quarter_key, end_date, rank_no, stock_code, stock_name,
                            net_value_ratio_pct, hold_num_shares, hold_market_value_cny,
                            latest_source_update, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                subject["subject_code"],
                                norm_quarter,
                                end_date,
                                int(float(row.get("rank_no") or 0)),
                                str(row.get("stock_code") or "").strip(),
                                str(row.get("stock_name") or "").strip(),
                                float(row.get("net_value_ratio_pct") or 0),
                                float(row.get("hold_num_shares") or 0),
                                float(row.get("hold_market_value_cny") or 0),
                                str(row.get("latest_source_update") or end_date).strip(),
                                json.dumps(row, ensure_ascii=False, default=str),
                                updated_at,
                            )
                            for row in raw_rows
                        ],
                    )

                if snapshots:
                    cursor.executemany(
                        """
                        INSERT INTO fh_snapshot(
                            subject_code, subject_name, subject_type, quarter_key, quarter_label,
                            compare_quarter_key, end_date, stock_code, stock_name, holders_count,
                            hold_num_shares, hold_market_value_cny, net_value_ratio_pct,
                            free_hold_ratio_pct, hold_ratio_pct, latest_source_update, raw_source, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [self._snapshot_params(snapshot, updated_at) for snapshot in snapshots],
                    )

            self._rebuild_change_cache_locked(cursor, subject, updated_at)

        self.record_sync_run(
            subject_code=subject["subject_code"],
            sync_scope=sync_scope,
            requested_quarter_key=requested_quarter_key,
            resolved_quarter_key=resolved_quarter_key,
            status="success",
            message=message,
            payload=payload_meta,
        )

    def list_subjects(self) -> list[dict]:
        return self._store.fetch_all(
            """
            SELECT subject_code, subject_name, subject_type, display_order
            FROM fh_subject
            ORDER BY display_order ASC, subject_code ASC
            """
        )

    def list_quarters(self) -> list[str]:
        rows = self._store.fetch_all(
            """
            SELECT DISTINCT quarter_key
            FROM fh_snapshot
            ORDER BY quarter_key DESC
            """
        )
        return [str(row["quarter_key"]) for row in rows]

    def get_latest_quarter_map(self) -> dict[str, str]:
        rows = self._store.fetch_all(
            """
            SELECT subject_code, MAX(quarter_key) AS latest_quarter
            FROM fh_snapshot
            GROUP BY subject_code
            """
        )
        return {
            str(row["subject_code"]): str(row["latest_quarter"])
            for row in rows
            if row.get("latest_quarter")
        }

    def get_latest_sync_map(self) -> dict[str, dict]:
        rows = self._store.fetch_all(
            """
            SELECT subject_code, status, resolved_quarter_key, message, finished_at
            FROM fh_sync_run
            WHERE id IN (
                SELECT MAX(id)
                FROM fh_sync_run
                GROUP BY subject_code
            )
            """
        )
        return {str(row["subject_code"]): row for row in rows}

    def _query_cached_change_rows(self) -> list[dict]:
        return [
            dict(row)
            for row in self._store.fetch_all(
                """
                SELECT
                    subject_code, subject_name, subject_type, quarter_key, compare_quarter_key, end_date,
                    stock_code, stock_name, change_type, ratio_label, holders_count,
                    curr_hold_num_shares, prev_hold_num_shares, delta_hold_num_shares,
                    curr_hold_market_value_cny, prev_hold_market_value_cny, delta_hold_market_value_cny,
                    curr_ratio_pct, prev_ratio_pct, delta_ratio_pct,
                    curr_net_value_ratio_pct, prev_net_value_ratio_pct, delta_net_value_ratio_pct,
                    curr_free_hold_ratio_pct, prev_free_hold_ratio_pct, delta_free_hold_ratio_pct,
                    curr_hold_ratio_pct, prev_hold_ratio_pct, delta_hold_ratio_pct,
                    latest_source_update, sort_quarter, sort_value
                FROM fh_change_cache
                WHERE subject_code != ?
                ORDER BY sort_quarter DESC, sort_value DESC, stock_code ASC
                """
                ,
                (SUBJECT_QFII["subject_code"],),
            )
            if is_mainland_security_code(row["stock_code"])
        ]

    def _query_qfii_holder_change_rows(self) -> list[dict]:
        raw_rows = [
            dict(row)
            for row in self._store.fetch_all(
                """
                SELECT
                    subject_code, quarter_key, end_date, stock_code, stock_name, holder_name, holder_rank,
                    hold_num_shares, hold_market_value_cny, hold_ratio_pct, free_hold_ratio_pct,
                    update_date, raw_json
                FROM fh_raw_qfii
                WHERE subject_code = ?
                ORDER BY quarter_key DESC, stock_code ASC, holder_rank ASC, holder_name ASC
                """,
                (SUBJECT_QFII["subject_code"],),
            )
            if is_mainland_security_code(row["stock_code"])
        ]
        return build_qfii_holder_change_rows(raw_rows, SUBJECT_QFII)

    def query_change_rows(self) -> list[dict]:
        rows = self._query_cached_change_rows()
        rows.extend(self._query_qfii_holder_change_rows())
        return sorted(
            rows,
            key=lambda row: (
                -int(row.get("sort_quarter", 0) or 0),
                -float(row.get("sort_value", 0) or 0),
                str(row.get("stock_code") or ""),
                str(row.get("subject_name") or ""),
            ),
        )


fund_holdings_store = FundHoldingsStore()
