"""Reproduce Watchlist snapshot capture cost with isolated synthetic tab data."""

from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SOURCE_COUNTS = {
    "scan": 4000,
    "ai_industry_chain": 361,
    "na_daily": 48,
    "foreign_block": 37,
    "earnings": 57,
    "fund_holdings": 4000,
    "lhb": 68,
}


class _RowsTab:
    def __init__(self, rows: list[dict], *, keywords: tuple[str, ...] = ()) -> None:
        self._rows = rows
        self._keywords = keywords

    def get_row_data(self) -> list[dict]:
        return self._rows

    def get_foreign_keywords(self) -> list[str]:
        return list(self._keywords)


class _ScanTab(_RowsTab):
    def get_scan_results(self) -> list[dict]:
        return self._rows


def _row(source: str, index: int, payload_points: int) -> dict:
    code = f"{index + 1:06d}"
    row = {
        "代码": code,
        "名称": f"样本{code}",
        "payload": {
            "history": [
                {"index": point, "value": float(index + point)}
                for point in range(payload_points)
            ]
        },
    }
    if source == "ai_industry_chain":
        row["细分板块"] = "AI硬件"
    elif source == "na_daily":
        row.update({"细分板块": "北美映射", "催化剂": "样本催化"})
    elif source == "foreign_block":
        row.update(
            {
                "交易详情": "买入",
                "买方营业部": "高盛样本",
                "成交金额(万元)": 100,
            }
        )
    elif source == "earnings":
        row.update({"报告期": "2026Q2", "环比%": "8"})
    elif source == "lhb":
        row.update({"最近上榜": "20260725", "上榜净买额(万)": 20})
    return row


def _workspace(source_counts: dict[str, int], payload_points: int):
    tabs = {
        source: (
            _ScanTab([_row(source, index, payload_points) for index in range(count)])
            if source == "scan"
            else _RowsTab(
                [_row(source, index, payload_points) for index in range(count)],
                keywords=("高盛",) if source == "foreign_block" else (),
            )
        )
        for source, count in source_counts.items()
    }
    return SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: tabs.get(key),
        tab_specs=lambda: [{"key": key, "title": key} for key in tabs],
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _summary(values: list[float]) -> dict:
    return {
        "samples": len(values),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values, default=0.0), 3),
        "critical_count_gte_100ms": sum(value >= 100.0 for value in values),
    }


def _measure(
    adapter,
    target_codes: tuple[str, ...],
    *,
    sources=None,
    filter_capture_to_targets: bool = False,
) -> tuple[float, float, object, int, int]:
    from app.services.stock_context_query_service import StockContextQueryService

    gc.collect()
    capture_started_at = time.perf_counter()
    snapshot = adapter.capture(
        include_rps_bundle=False,
        sources=sources,
        target_codes=target_codes if filter_capture_to_targets else None,
    )
    capture_ms = (time.perf_counter() - capture_started_at) * 1000.0

    query_started_at = time.perf_counter()
    result = StockContextQueryService(snapshot).query_watchlist_radar(
        target_codes=target_codes,
        include_source_cache_fallback=False,
        allow_lhb_cache_compute=False,
    )
    query_ms = (time.perf_counter() - query_started_at) * 1000.0
    captured_rows = sum(len(rows) for rows in snapshot.source_rows.values())
    candidate_source_rows = sum(snapshot.source_row_counts.values())
    return capture_ms, query_ms, result, captured_rows, candidate_source_rows


def run_probe(*, samples: int, target_count: int, payload_points: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="watchlist-context-probe-") as temp_dir:
        isolated_root = Path(temp_dir)
        os.environ["VCP_HUNTER_LOG_DIR"] = str(isolated_root / "logs")
        os.environ["VCP_HUNTER_DB_PATH"] = str(isolated_root / "probe.db")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PyQt6.QtCore import QCoreApplication

        from app.services import stock_context_query_service
        from domains.stock_context.signal_builders import RADAR_SOURCE_KEYS
        from ui.workspaces.stock_context_widget_adapter import StockContextWidgetSnapshotAdapter

        application = QCoreApplication.instance() or QCoreApplication([])
        stock_context_query_service.load_earnings_state_payload = lambda: ({}, "")
        adapter = StockContextWidgetSnapshotAdapter(
            _workspace(DEFAULT_SOURCE_COUNTS, payload_points)
        )
        target_codes = tuple(f"{index + 1:06d}" for index in range(target_count))

        _measure(adapter, target_codes)
        _measure(adapter, target_codes, sources=RADAR_SOURCE_KEYS)
        _measure(
            adapter,
            target_codes,
            sources=RADAR_SOURCE_KEYS,
            filter_capture_to_targets=True,
        )

        full_capture: list[float] = []
        full_query: list[float] = []
        scoped_capture: list[float] = []
        scoped_query: list[float] = []
        target_filtered_capture: list[float] = []
        target_filtered_query: list[float] = []
        full_rows = 0
        scoped_rows = 0
        target_filtered_rows = 0
        target_filtered_candidate_rows = 0
        equivalent = True
        for sample_index in range(samples):
            if sample_index % 2:
                scoped = _measure(adapter, target_codes, sources=RADAR_SOURCE_KEYS)
                target_filtered = _measure(
                    adapter,
                    target_codes,
                    sources=RADAR_SOURCE_KEYS,
                    filter_capture_to_targets=True,
                )
                full = _measure(adapter, target_codes)
            else:
                full = _measure(adapter, target_codes)
                target_filtered = _measure(
                    adapter,
                    target_codes,
                    sources=RADAR_SOURCE_KEYS,
                    filter_capture_to_targets=True,
                )
                scoped = _measure(adapter, target_codes, sources=RADAR_SOURCE_KEYS)
            full_capture.append(full[0])
            full_query.append(full[1])
            scoped_capture.append(scoped[0])
            scoped_query.append(scoped[1])
            target_filtered_capture.append(target_filtered[0])
            target_filtered_query.append(target_filtered[1])
            full_rows = full[3]
            scoped_rows = scoped[3]
            target_filtered_rows = target_filtered[3]
            target_filtered_candidate_rows = target_filtered[4]
            equivalent = equivalent and full[2] == scoped[2] == target_filtered[2]

        del application
        full_summary = _summary(full_capture)
        scoped_summary = _summary(scoped_capture)
        target_filtered_summary = _summary(target_filtered_capture)
        scoped_median = scoped_summary["median_ms"]
        target_filtered_median = target_filtered_summary["median_ms"]
        reduction_pct = (
            (scoped_median - target_filtered_median) / scoped_median * 100.0
            if scoped_median
            else 0.0
        )
        result = {
            "config": {
                "samples": samples,
                "target_count": target_count,
                "payload_points": payload_points,
                "source_counts": DEFAULT_SOURCE_COUNTS,
                "isolated_log_and_database": True,
            },
            "legacy_full_scope": {
                "captured_rows": full_rows,
                "capture": full_summary,
                "worker_query": _summary(full_query),
            },
            "watchlist_radar_scope": {
                "captured_rows": scoped_rows,
                "candidate_source_rows": scoped[4],
                "capture": scoped_summary,
                "worker_query": _summary(scoped_query),
            },
            "target_filtered_scope": {
                "captured_rows": target_filtered_rows,
                "candidate_source_rows": target_filtered_candidate_rows,
                "capture": target_filtered_summary,
                "worker_query": _summary(target_filtered_query),
            },
            "capture_median_reduction_pct": round(reduction_pct, 1),
            "radar_result_equivalent": equivalent,
        }
        from infra.storage.data_store import DataStore

        DataStore.close_all()
        logging.shutdown()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--target-count", type=int, default=42)
    parser.add_argument("--payload-points", type=int, default=4)
    args = parser.parse_args()
    if args.samples < 1 or args.target_count < 1 or args.payload_points < 0:
        parser.error("samples/target-count must be positive and payload-points cannot be negative")

    result = run_probe(
        samples=args.samples,
        target_count=args.target_count,
        payload_points=args.payload_points,
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["radar_result_equivalent"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
