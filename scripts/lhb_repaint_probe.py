"""Deterministic offscreen probe for LHB quote-batch repaint regions.

Examples:
  python scripts/lhb_repaint_probe.py --rows 70
   python scripts/lhb_repaint_probe.py --rows 70 --update-threshold 200
   python scripts/lhb_repaint_probe.py --rows 70 --benchmark-full-paint
   python scripts/lhb_repaint_probe.py --rows 70 --max-paint-ms 200
   python scripts/lhb_repaint_probe.py --rows 50 --shell-nav-cycles 3
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
from PyQt6.QtTest import QSignalSpy  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_IMPORT_LOGGING_DISABLE = logging.root.manager.disable
logging.disable(logging.CRITICAL)
try:
    from core.observability import clear_metric_history, metric_history  # noqa: E402
    from infra.diagnostics.ui_stall_probe import install_ui_stall_probe  # noqa: E402
    from ui.components.main_window_shell import ShellNavigationWidget  # noqa: E402
    from ui.components.smooth_tab_widget import SmoothTabWidget  # noqa: E402
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


class _ShellNavProbeWorkspace:
    def __init__(self, tabs: SmoothTabWidget, lhb_index: int):
        self.tabs = tabs
        self._lhb_index = lhb_index
        self.activations: list[tuple[int, str]] = []
        self.prepare_intervals: list[int] = []

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return {
            "home": [0, *range(2, self.tabs.count())],
            "lhb": [self._lhb_index],
        }

    def prepare_shell_group_rebuild_navigation(self, *, interval_ms: int = 0) -> None:
        self.prepare_intervals.append(int(interval_ms))

    def activate_tab(self, index: int, *, reason: str = "user") -> bool:
        self.activations.append((int(index), str(reason)))
        widget = self.tabs.widget(int(index))
        if int(index) == self._lhb_index and isinstance(widget, LhbTab):
            widget._workspace_load_reason = str(reason)
            widget.prepare_shell_nav_repaint_guard()
        self.tabs.setCurrentIndex(int(index))
        if int(index) == self._lhb_index and isinstance(widget, LhbTab):
            widget.on_workspace_tab_activated()
        return True


def _sort_quote_payload(rows: list[dict], *, cycle: int) -> dict[str, dict]:
    count = max(1, len(rows))
    descending_by_code = bool(cycle % 2)
    payload = {}
    for row_number, row in enumerate(rows, start=1):
        relative = count - row_number + 1 if descending_by_code else row_number
        payload[str(row["代码"])] = {
            "close": 10.0 + relative / 100.0,
            "last_close": 10.0,
            "open": 10.0,
            "zongguben": 100_000_000.0,
        }
    return payload


def _signal_spans(spy: QSignalSpy) -> list[tuple[int, int, int, int]]:
    return [
        (entry[0].row(), entry[0].column(), entry[1].row(), entry[1].column())
        for entry in spy
        if len(entry) >= 2 and entry[0].isValid() and entry[1].isValid()
    ]


def run_shell_nav_probe(*, row_count: int, cycles: int) -> dict:
    """Exercise the real ShellNavigationWidget path with an 11-tab workspace."""
    app = QApplication.instance() or QApplication([])
    tabs = SmoothTabWidget()
    nav = ShellNavigationWidget()
    tab = LhbTab(object(), autoload_pool=False)
    tab._should_start_pool_on_show = lambda: False
    tab._pool_bootstrap_started = True
    tabs.addTab(QWidget(), "Home")
    lhb_index = tabs.addTab(tab, "LHB")
    for index in range(2, 11):
        tabs.addTab(QWidget(), f"Tab {index}")
    workspace = _ShellNavProbeWorkspace(tabs, lhb_index)
    rows = _lhb_rows(max(1, int(row_count)))
    stall_probe = install_ui_stall_probe(app)
    if stall_probe is not None:
        stall_probe.reset_stall_snapshot()

    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        tab._refresh_lhb_lineage(rows)
        tab.table_state.show_table()
        tab.table._pending_paint_metric = None
        tabs.resize(1200, 800)
        tabs.show()
        _process_events_for(app, 80)
        nav.bind_workspace(workspace, tabs)
        _process_events_for(app, 30)

        source_layout = QSignalSpy(tab.model.layoutChanged)
        proxy_layout = QSignalSpy(tab.proxy_model.layoutChanged)
        source_reset = QSignalSpy(tab.model.modelReset)
        source_data = QSignalSpy(tab.model.dataChanged)
        proxy_data = QSignalSpy(tab.proxy_model.dataChanged)
        cycle_results = []
        probe_cycles = max(1, int(cycles))
        expected_span = (0, 0, len(rows) - 1, tab.model.columnCount() - 1)

        for cycle in range(probe_cycles):
            before_layout = len(source_layout)
            before_proxy_layout = len(proxy_layout)
            before_reset = len(source_reset)
            before_data = len(source_data)
            before_proxy_data = len(proxy_data)
            clear_metric_history()

            nav._switch_group("lhb")
            _process_events_for(app, 80)
            tab._apply_quote_snapshot_now(_sort_quote_payload(rows, cycle=cycle))
            _process_events_for(app, 80)
            tab.table.viewport().update()
            _process_events_for(app, 80)

            paint_samples = metric_history("lhb_table_paint_ms")
            paint_tags = [dict(sample.tags) for sample in paint_samples]
            structural_index = next(
                (
                    index
                    for index, tags in enumerate(paint_tags)
                    if tags.get("structural_reason") == "model_layout_changed"
                ),
                -1,
            )
            later_tags = paint_tags[structural_index + 1 :] if structural_index >= 0 else []
            guard_events = [
                {
                    key: str(sample.tags.get(key, ""))
                    for key in ("decision", "fallback_reason", "age_ms", "remaining")
                }
                for sample in metric_history("lhb_shell_nav_repaint_guard")
            ]
            guard_decisions = [event["decision"] for event in guard_events]
            data_spans = _signal_spans(source_data)[before_data:]
            proxy_data_spans = _signal_spans(proxy_data)[before_proxy_data:]
            flash_callbacks = metric_history("lhb_flash_repaint_callback_ms")
            activation_samples = metric_history("lhb_tab_activation_ms")
            event_loop_stalls = metric_history("ui_event_loop_stall_ms")
            method_stalls = metric_history("ui_method_stall_ms")
            critical_event_loop_stalls = sum(
                sample.tags.get("severity") == "critical"
                for sample in event_loop_stalls
            )
            cycle_results.append(
                {
                    "cycle": cycle + 1,
                    "tab_count": tabs.count(),
                    "activation": workspace.activations[-1] if workspace.activations else None,
                    "source_layout_changed": len(source_layout) - before_layout,
                    "proxy_layout_changed": len(proxy_layout) - before_proxy_layout,
                    "source_model_reset": len(source_reset) - before_reset,
                    "source_data_spans": data_spans,
                    "proxy_data_spans": proxy_data_spans,
                    "expected_structural_span": expected_span,
                    "paint_samples": [
                        {
                            key: tags.get(key, "")
                            for key in (
                                "reason",
                                "pending_reasons",
                                "delivery_kind",
                                "delivered_full_viewport",
                                "requested_full_viewport",
                            )
                        }
                        for tags in paint_tags
                    ],
                    "other_full_viewport_after_structure": sum(
                        tags.get("reason") == "other" and tags.get("delivered_full_viewport") == "true"
                        for tags in later_tags
                    ),
                    "guard_decisions": guard_decisions,
                    "guard_events": guard_events,
                    "activation_ms": [round(float(sample.value), 3) for sample in activation_samples],
                    "flash_requested_full_viewport": [
                        str(sample.tags.get("requested_full_viewport", "")) for sample in flash_callbacks
                    ],
                    "event_loop_stall_ms": [round(float(sample.value), 3) for sample in event_loop_stalls],
                    "method_stall_ms": [round(float(sample.value), 3) for sample in method_stalls],
                    "critical_event_loop_stalls": critical_event_loop_stalls,
                }
            )
            nav._switch_group("home")
            _process_events_for(app, 60)

        accepted = bool(
            tabs.count() == 11
            and all(
                result["activation"] == (lhb_index, "shell_nav")
                and result["source_layout_changed"] == 1
                and result["proxy_layout_changed"] == 1
                and result["source_model_reset"] == 0
                and result["expected_structural_span"] in result["source_data_spans"]
                and result["expected_structural_span"] in result["proxy_data_spans"]
                and result["other_full_viewport_after_structure"] == 0
                and "rearm_after_structure" in result["guard_decisions"]
                and "suppress_redundant_full" in result["guard_decisions"]
                for result in cycle_results
            )
        )
        return {
            "mode": "offscreen_shell_nav_structural_repaint",
            "status": "pass" if accepted else "fail",
            "tab_count": tabs.count(),
            "cycles": cycle_results,
            "group_rebuild_prepared": workspace.prepare_intervals,
            "stall_snapshot": stall_probe.stall_snapshot() if stall_probe is not None else {"installed": False},
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "note": "Structural gate only; absolute paint/stall milliseconds are same-machine diagnostics.",
        }
    finally:
        nav.deleteLater()
        tabs.close()
        tabs.deleteLater()
        app.processEvents()


def run_probe(
    *,
    row_count: int,
    update_threshold: int | None,
    benchmark_full_paint: bool = False,
    max_paint_ms: float | None = None,
    cycles: int = 5,
    shell_nav_cycles: int = 0,
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
    flash_requested_full_viewport = [
        str(sample.tags.get("requested_full_viewport", ""))
        for sample in metric_history("lhb_flash_repaint_callback_ms")
    ]
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
        "flash_requested_full_viewport": flash_requested_full_viewport,
    }
    if benchmark_full_paint:
        benchmark = _full_paint_benchmark(tab, app, 3)
        legacy_calls = int(benchmark["legacy_rescan"]["money_value_calls"])
        cached_calls = int(benchmark["cached_scale"]["money_value_calls"])
        benchmark["linear_cache_gate_pass"] = cached_calls > 0 and legacy_calls >= cached_calls * 2
        result["full_paint_benchmark"] = benchmark
        accepted = accepted and bool(benchmark["linear_cache_gate_pass"])
    if shell_nav_cycles > 0:
        shell_nav = run_shell_nav_probe(row_count=len(rows), cycles=shell_nav_cycles)
        result["shell_nav"] = shell_nav
        accepted = accepted and shell_nav["status"] == "pass"
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
        "--shell-nav-cycles",
        type=int,
        default=0,
        help="Also exercise ShellNavigationWidget with 11 tabs and LHB default quote reordering.",
    )
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
            shell_nav_cycles=max(0, int(args.shell_nav_cycles)),
        )
    finally:
        _cleanup_probe_runtime()
        logging.disable(previous_logging_disable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
