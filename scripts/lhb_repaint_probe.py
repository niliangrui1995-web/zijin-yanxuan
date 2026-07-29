"""Deterministic offscreen probe for LHB quote-batch repaint regions.

Examples:
  python scripts/lhb_repaint_probe.py --rows 70
  python scripts/lhb_repaint_probe.py --rows 70 --update-threshold 200
  python scripts/lhb_repaint_probe.py --rows 70 --benchmark-full-paint
  python scripts/lhb_repaint_probe.py --rows 70 --max-paint-ms 200
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROBE_RUNTIME = tempfile.TemporaryDirectory(prefix="vcp_lhb_repaint_probe_")
_PROBE_RUNTIME_ROOT = Path(_PROBE_RUNTIME.name)
os.environ["VCP_HUNTER_DB_PATH"] = str(_PROBE_RUNTIME_ROOT / "probe.db")
os.environ["VCP_HUNTER_LOG_DIR"] = str(_PROBE_RUNTIME_ROOT / "logs")
os.environ["VCP_HUNTER_TEST_QSETTINGS_DIR"] = str(_PROBE_RUNTIME_ROOT / "settings")
os.environ["VCP_HUNTER_SETTINGS_ORGANIZATION"] = "VCPHunterProbe"
os.environ["VCP_HUNTER_SETTINGS_APPLICATION"] = "LhbRepaintProbe"

from PyQt6.QtCore import QEvent, QObject  # noqa: E402
from PyQt6.QtGui import QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_IMPORT_LOGGING_DISABLE = logging.root.manager.disable
logging.disable(logging.CRITICAL)
try:
    from core.observability import clear_metric_history, metric_history  # noqa: E402
    from ui.tabs.lhb_tab import LHB_VIEW_UPDATE_THRESHOLD, LhbTab  # noqa: E402
finally:
    logging.disable(_IMPORT_LOGGING_DISABLE)

_PROBE_RUNTIME_CLEANED = False


def _cleanup_probe_runtime() -> None:
    global _PROBE_RUNTIME_CLEANED
    if _PROBE_RUNTIME_CLEANED:
        return
    _PROBE_RUNTIME_CLEANED = True
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        from infra.storage.data_store import data_store

        data_store.close()
        logging.shutdown()
        _PROBE_RUNTIME.cleanup()
    finally:
        logging.disable(previous_logging_disable)


atexit.register(_cleanup_probe_runtime)


class _PaintRegionProbe(QObject):
    def __init__(self, viewport):
        super().__init__(viewport)
        self._viewport = viewport
        self.ratios: list[float] = []
        self.rect_counts: list[int] = []

    def eventFilter(self, watched, event):
        if watched is self._viewport and event.type() == QEvent.Type.Paint:
            viewport_rect = self._viewport.rect()
            viewport_area = max(1, viewport_rect.width() * viewport_rect.height())
            bounds = event.region().boundingRect()
            dirty_area = max(0, bounds.width() * bounds.height())
            self.ratios.append(min(1.0, dirty_area / viewport_area))
            self.rect_counts.append(event.region().rectCount())
        return False


def _lhb_rows(count: int) -> list[dict]:
    return [
        {
            "代码": f"{row:06d}",
            "名称": f"探针{row:02d}",
            "现价": 10.0,
            "涨幅%": 0.0,
            "市值": "10亿",
            "买点": "",
            "上榜次数": 1,
            "最近上榜": "07-28",
            "上榜净买额(万)": float(row + 1),
            "机构净买(万)": float(row + 1) / 2.0,
            "外资净买入": "未现身",
            "外资净买(万)": 0.0,
            "换手率%": 1.0,
            LhbTab.AI_CHAIN_CONTEXT_COLUMN: "--",
        }
        for row in range(count)
    ]


def _quote_payload(rows: list[dict], *, cycle: int = 0) -> dict[str, dict]:
    return {
        str(row["代码"]): {
            "close": 11.0 + max(0, int(cycle)) + index / 100.0,
            "last_close": 10.0,
            "open": 10.0,
            "zongguben": 100_000_000.0,
        }
        for index, row in enumerate(rows)
    }


def _process_events_for(app: QApplication, duration_ms: int) -> None:
    deadline = time.perf_counter() + max(0, int(duration_ms)) / 1000.0
    while time.perf_counter() < deadline:
        app.processEvents()


def _expected_quote_region_ratio(tab: LhbTab) -> float:
    viewport_rect = tab.table.viewport().rect()
    viewport_area = max(1, viewport_rect.width() * viewport_rect.height())
    quote_columns = [tab.model.headers.index(header) for header in ("现价", "涨幅%", "市值", "买点")]
    region = QRegion()
    for row in range(tab.proxy_model.rowCount()):
        for column in quote_columns:
            rect = tab.table.visualRect(tab.proxy_model.index(row, column)).intersected(viewport_rect)
            if not rect.isEmpty():
                region = region.united(QRegion(rect))
    bounds = region.boundingRect()
    return min(1.0, max(0, bounds.width() * bounds.height()) / viewport_area)


def _full_paint_benchmark(tab: LhbTab, app: QApplication, cycles: int) -> dict:
    model = tab.model
    cached_max = model._money_bar_max_abs
    original_value = model._money_value_for_visual

    def _run(*, legacy_rescan: bool) -> dict:
        calls = 0

        def _tracked_value(header, row):
            nonlocal calls
            calls += 1
            return original_value(header, row)

        def _uncached_max(header: str) -> float:
            values = (
                model._money_value_for_visual(header, row)
                for row in model.row_data
                if isinstance(row, dict)
            )
            return max((abs(float(value)) for value in values if value is not None), default=0.0)

        model._money_value_for_visual = _tracked_value
        model._money_bar_max_abs = _uncached_max if legacy_rescan else cached_max
        model._clear_money_bar_max_abs_cache()
        tab.table.viewport().repaint()
        app.processEvents()
        calls = 0
        durations = []
        for _ in range(max(1, int(cycles))):
            started_at = time.perf_counter()
            tab.table.viewport().repaint()
            durations.append((time.perf_counter() - started_at) * 1000.0)
            app.processEvents()
        return {
            "cycles": len(durations),
            "median_ms": round(statistics.median(durations), 3),
            "max_ms": round(max(durations), 3),
            "money_value_calls": calls,
        }

    try:
        legacy = _run(legacy_rescan=True)
        cached = _run(legacy_rescan=False)
    finally:
        model._money_value_for_visual = original_value
        model._money_bar_max_abs = cached_max
        model._clear_money_bar_max_abs_cache()
    return {"legacy_rescan": legacy, "cached_scale": cached}


def run_probe(
    *,
    row_count: int,
    update_threshold: int | None,
    benchmark_full_paint: bool = False,
    max_paint_ms: float | None = None,
    cycles: int = 5,
) -> dict:
    app = QApplication.instance() or QApplication([])
    tab = LhbTab(object(), autoload_pool=False)
    tab._should_start_pool_on_show = lambda: False
    rows = _lhb_rows(max(1, int(row_count)))
    tab.model.update_data(rows, hydrate_latest_quotes=False)
    tab._refresh_lhb_lineage(rows)
    tab.table_state.show_table()
    tab.resize(1200, 800)
    tab.show()
    _process_events_for(app, 120)

    if update_threshold is not None:
        tab.table.setUpdateThreshold(max(0, int(update_threshold)))
    threshold = int(tab.table.updateThreshold())
    expected_quote_region_ratio = _expected_quote_region_ratio(tab)
    region_probe = _PaintRegionProbe(tab.table.viewport())
    tab.table.viewport().installEventFilter(region_probe)
    clear_metric_history()

    probe_cycles = max(1, int(cycles))
    changed_row_counts = []
    apply_durations = []
    for cycle in range(probe_cycles):
        apply_started_at = time.perf_counter()
        changed_row_counts.append(tab.model.update_quotes(_quote_payload(rows, cycle=cycle)))
        apply_durations.append((time.perf_counter() - apply_started_at) * 1000.0)
        _process_events_for(app, 220)

    paint_samples = metric_history("lhb_table_paint_ms")
    paint_reasons = [str(sample.tags.get("reason", "")) for sample in paint_samples]
    quote_paint_samples = [
        sample for sample in paint_samples if sample.tags.get("reason") == "quote_data_changed"
    ]
    quote_paint_ratios = [
        float(sample.tags.get("dirty_bounding_area_ratio", 0.0) or 0.0)
        for sample in quote_paint_samples
    ]
    phase_ratios = list(region_probe.ratios)
    phase_full_viewport_count = sum(ratio >= 0.99 for ratio in phase_ratios)
    quote_full_viewport_count = sum(ratio >= 0.99 for ratio in quote_paint_ratios)
    changed_index_count = len(rows) * 4
    expects_threshold_full_repaint = changed_index_count > threshold
    quote_paint_count = len(quote_paint_samples)
    threshold_tag_match_count = sum(
        sample.tags.get("reason") == "quote_data_changed"
        and sample.tags.get("changed_indexes") == str(changed_index_count)
        and sample.tags.get("update_threshold") == str(threshold)
        and sample.tags.get("threshold_exceeded") == str(expects_threshold_full_repaint).lower()
        for sample in paint_samples
    )
    threshold_tags_match = threshold_tag_match_count >= probe_cycles
    paint_durations = [float(sample.value) for sample in quote_paint_samples]
    paint_max_ms = max(paint_durations, default=0.0)
    paint_budget_pass = max_paint_ms is None or paint_max_ms <= max(0.0, float(max_paint_ms))
    bounded_region_pass = bool(
        quote_paint_ratios
        and max(quote_paint_ratios) <= min(0.98, expected_quote_region_ratio + 0.02)
    )
    repaint_shape_pass = bool(
        quote_full_viewport_count >= probe_cycles
        if expects_threshold_full_repaint
        else quote_full_viewport_count == 0 and phase_full_viewport_count == 0 and bounded_region_pass
    )
    accepted = bool(
        all(changed_rows == len(rows) for changed_rows in changed_row_counts)
        and quote_paint_count == probe_cycles
        and threshold_tags_match
        and paint_budget_pass
        and repaint_shape_pass
    )
    result = {
        "mode": "threshold_full_reproduction" if expects_threshold_full_repaint else "bounded_region",
        "cycles": probe_cycles,
        "rows": len(rows),
        "changed_rows": changed_row_counts,
        "quote_span_indexes": changed_index_count,
        "update_threshold": threshold,
        "threshold_exceeded": expects_threshold_full_repaint,
        "threshold_tags_match": threshold_tags_match,
        "threshold_tag_match_count": threshold_tag_match_count,
        "quote_apply_median_ms": round(statistics.median(apply_durations), 3),
        "quote_apply_max_ms": round(max(apply_durations), 3),
        "quote_apply_durations_ms": [round(value, 3) for value in apply_durations],
        "paint_event_count": len(phase_ratios),
        "quote_paint_count": quote_paint_count,
        "phase_full_viewport_count": phase_full_viewport_count,
        "quote_full_viewport_count": quote_full_viewport_count,
        "expected_quote_region_ratio": round(expected_quote_region_ratio, 4),
        "bounded_region_pass": bounded_region_pass,
        "max_dirty_bounding_area_ratio": round(max(quote_paint_ratios, default=0.0), 4),
        "paint_max_ms": round(paint_max_ms, 3),
        "paint_budget_ms": None if max_paint_ms is None else max(0.0, float(max_paint_ms)),
        "paint_budget_pass": paint_budget_pass,
        "paint_ratios": [round(ratio, 4) for ratio in phase_ratios],
        "quote_paint_ratios": [round(ratio, 4) for ratio in quote_paint_ratios],
        "paint_reasons": paint_reasons,
        "paint_threshold_tags": [
            {
                key: sample.tags.get(key, "")
                for key in ("changed_indexes", "update_threshold", "threshold_exceeded")
            }
            for sample in paint_samples
        ],
        "paint_durations_ms": [round(value, 3) for value in paint_durations],
    }
    if benchmark_full_paint:
        benchmark = _full_paint_benchmark(tab, app, 3)
        legacy_calls = int(benchmark["legacy_rescan"]["money_value_calls"])
        cached_calls = int(benchmark["cached_scale"]["money_value_calls"])
        benchmark["linear_cache_gate_pass"] = cached_calls > 0 and legacy_calls >= cached_calls * 2
        result["full_paint_benchmark"] = benchmark
        accepted = accepted and bool(benchmark["linear_cache_gate_pass"])
    result["status"] = "pass" if accepted else "fail"
    tab.table.viewport().removeEventFilter(region_probe)
    tab.close()
    tab.deleteLater()
    app.processEvents()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=70)
    parser.add_argument(
        "--update-threshold",
        type=int,
        default=None,
        help=f"Override the LHB view threshold; current default is {LHB_VIEW_UPDATE_THRESHOLD}.",
    )
    parser.add_argument("--benchmark-full-paint", action="store_true")
    parser.add_argument("--cycles", type=int, default=5, help="Quote update cycles used by the structural gate.")
    parser.add_argument(
        "--max-paint-ms",
        type=float,
        default=None,
        help="Optional same-machine paint budget. Omit for the cross-environment structural gate.",
    )
    args = parser.parse_args()
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        result = run_probe(
            row_count=args.rows,
            update_threshold=args.update_threshold,
            benchmark_full_paint=bool(args.benchmark_full_paint),
            max_paint_ms=args.max_paint_ms,
            cycles=args.cycles,
        )
    finally:
        _cleanup_probe_runtime()
        logging.disable(previous_logging_disable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
