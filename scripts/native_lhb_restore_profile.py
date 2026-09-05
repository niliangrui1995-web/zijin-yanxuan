"""Native Windows LHB restore probe with isolated cache and actual paint timings.

Run before/after in separate sequential processes. ``--source-root`` may point
at an audited source snapshot; no checkout or user settings are modified.
The probe never calls QWidget.grab(), repaint(), or processEvents() to sample.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import socket
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_FILES = (
    "lhb_pool_30d.json",
    "ai_industry_chain_stock_codes.json",
    "ai_industry_chain_context_map.json",
    "vcp_rps_precomputed.json",
)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, default=PROJECT_ROOT / "data/vcp_hunter.db")
    parser.add_argument("--expected-rows", type=int, default=42)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--phase-ms", type=int, default=1200)
    parser.add_argument("--trace-layout", action="store_true")
    parser.add_argument(
        "--skip-refresh-state-restore",
        action="store_true",
        help="Diagnostic A/B only; skips LHB refresh-state restoration.",
    )
    parser.add_argument(
        "--skip-same-current-index",
        action="store_true",
        help="Diagnostic A/B only; skips identical LHB setCurrentIndex calls.",
    )
    parser.add_argument(
        "--skip-header-restore", action="store_true", help="Diagnostic A/B only; skips LHB QHeaderView.restoreState."
    )
    parser.add_argument(
        "--execute-delayed-items-layout",
        action="store_true",
        help="Diagnostic A/B only; completes pending LHB item layout before paint.",
    )
    parser.add_argument(
        "--resize-header-sections",
        action="store_true",
        help="Diagnostic A/B only; completes pending LHB header section sizing before paint.",
    )
    return parser.parse_args(argv)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _summarize_paints(paints):
    from scripts.native_watchlist_profile import summarize_durations

    grouped = defaultdict(list)
    for paint in paints:
        grouped[paint["phase"]].append(paint)
    return {
        phase: {
            "actual_paint": summarize_durations([item["elapsed_ms"] for item in items]),
            "full_viewport_count": sum(item["full_viewport"] for item in items),
            "local_region_count": sum(not item["full_viewport"] for item in items),
            "first_rows": items[0]["rows"],
        }
        for phase, items in grouped.items()
    }


def _log_evidence(path, *, start="2026-09-05 03:08:26", end="2026-09-05 03:08:37"):
    """Read structured metrics once; warning and metric lines are not double counted."""
    metrics = defaultdict(list)
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not (start <= line[:19] < end) or "[structured] " not in line:
            continue
        try:
            payload = json.loads(line.split("[structured] ", 1)[1])
        except ValueError, IndexError:
            continue
        fields = payload.get("fields", {})
        name = fields.get("metric", "")
        if name in {"lhb_table_paint_ms", "lhb_table_paint_delay_ms", "ui_event_loop_stall_ms"}:
            metrics[name].append({"line": number, "timestamp": line[:23], **fields})
    return {
        "path": str(Path(path).resolve()),
        "sha256": _sha256(path),
        "start_inclusive": start,
        "end_exclusive": end,
        "metrics": dict(metrics),
    }


def _acceptance(report, expected_rows):
    """Require delivered native frames and preserved behavior, not only fast timings."""
    violations = []
    paints = report.get("actual_paints", [])
    if not any(
        item.get("visible") and item.get("full_viewport") and item.get("rows") == expected_rows for item in paints
    ):
        violations.append("missing complete visible first frame")
    if any(not item.get("visible") and item.get("full_viewport") for item in paints):
        violations.append("hidden full viewport paint")
    if report.get("final_row_count") != expected_rows:
        violations.append("final row count changed")
    prewarm = report.get("background_prewarm", {})
    if not prewarm.get("finished") or prewarm.get("loaded_count") != 11 or prewarm.get("failures"):
        violations.append("11-tab background prewarm incomplete")
    if not any(item.get("active_flash_cells", 0) > 0 for item in report.get("actions", [])):
        violations.append("quote flash not observed")
    if not any(
        item.get("tags", {}).get("reason") == "flash_expiry"
        for item in report.get("metrics", {}).get("lhb_table_paint_ms", [])
    ):
        violations.append("flash expiry paint not delivered")
    try:
        prices = [float(str(value).replace(",", "")) for value in report.get("final_sorted_prices", [])]
    except TypeError, ValueError:
        prices = []
    if len(prices) != expected_rows or prices != sorted(prices, reverse=True):
        violations.append("price sorting incorrect")
    return {"passed": not violations, "violations": violations}


def _build_comparison(before, after):
    from scripts.native_watchlist_profile import summarize_durations

    phases = list(dict.fromkeys(item["name"] for report in (before, after) for item in report["phases"]))

    def summarize(report):
        paints = report["actual_paints"]
        phase_stalls = defaultdict(list)
        for sample in report["metrics"]["ui_event_loop_stall_ms"]:
            phase_name = "startup"
            if "recorded_at" in sample:
                for phase in report["phases"]:
                    if phase.get("recorded_at", float("inf")) <= sample["recorded_at"]:
                        phase_name = phase["name"]
            phase_stalls[phase_name].append(sample["value"])
        return {
            "status": report["status"],
            "acceptance": report["acceptance"],
            "source_sha256": report["source_sha256"],
            "environment": report["environment"],
            "loaded_rows": report["loaded_rows"],
            "final_row_count": report["final_row_count"],
            "prewarm_loaded_count": report["background_prewarm"]["loaded_count"],
            "prewarm_finished": report["background_prewarm"]["finished"],
            "network_attempts": len(report["blocked_network_attempts"]),
            "viewport": report["viewport"],
            "actual_paint_count": len(paints),
            "full_viewport_count": sum(item["full_viewport"] for item in paints),
            "actual_paint_duration": summarize_durations([item["elapsed_ms"] for item in paints]),
            "actions": report["actions"],
            "model_signal_counts": report["model_signal_counts"],
            "phases": {
                phase: {
                    "actual_paint_count": sum(item["phase"] == phase for item in paints),
                    "full_viewport_count": sum(item["phase"] == phase and item["full_viewport"] for item in paints),
                    "actual_paint_duration": summarize_durations(
                        [item["elapsed_ms"] for item in paints if item["phase"] == phase]
                    ),
                    "event_loop_stall": summarize_durations(phase_stalls.get(phase, [])),
                    "active_dispatch": report["dispatcher"]["phases"].get(phase, {}).get("active_dispatch", {}),
                }
                for phase in phases
            },
        }

    cache_match = {
        name: before["isolation"]["cache"][name]["sha256"] == after["isolation"]["cache"][name]["sha256"]
        for name in CACHE_FILES
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "protocol": "sequential Windows Qt processes; real schedule_restore_last_tab; actual paintEvent timing; no QWidget.grab",
        "profiler_sha256_matches": before.get("profiler_sha256") == after.get("profiler_sha256"),
        "cache_sha256_matches": cache_match,
        "row_codes_match": set(before["row_codes"]) == set(after["row_codes"]),
        "before": summarize(before),
        "after": summarize(after),
        "original_log": _log_evidence(PROJECT_ROOT / "data/logs/vcp_20260905.log"),
        "limitations": [
            "single native run per revision; elapsed time is machine/load dependent",
            "startup event-loop stalls are separate from the LHB restore and interaction phases",
            "necessary first paint and genuine row sorting retain full viewport delivery",
        ],
    }


def _configure_runtime(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "runtime/data/Cache"
    cache.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name in CACHE_FILES:
        source = PROJECT_ROOT / "data/Cache" / name
        target = cache / name
        shutil.copy2(source, target)
        artifacts[name] = {"source": str(source), "copy": str(target), "sha256": _sha256(target)}
    os.environ["VCP_HUNTER_LOG_DIR"] = str(output / "logs")
    os.environ["VCP_HUNTER_TEST_QSETTINGS_DIR"] = str(output / "settings")
    os.environ["VCP_HUNTER_SETTINGS_ORGANIZATION"] = "VCPHunterDiagnostics"
    os.environ["VCP_HUNTER_SETTINGS_APPLICATION"] = f"NativeLhbRestore_{os.getpid()}"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    sys.path.insert(0, str(args.source_root.resolve()))
    from scripts.native_watchlist_profile import _prepare_profile_database

    database = _prepare_profile_database(args.source_db, output / "profile.db")
    os.environ["VCP_HUNTER_DB_PATH"] = database["target"]
    import core.runtime_paths as runtime_paths

    original_root = runtime_paths.PROJECT_ROOT
    for name, value in vars(runtime_paths).copy().items():
        if isinstance(value, str) and value.startswith(original_root):
            setattr(runtime_paths, name, str(output / "runtime") + value[len(original_root) :])
    return output, cache, {"cache": artifacts, "database": database}


def run_profile(args):
    output, cache, isolation = _configure_runtime(args)
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtGui import QRegion

    from scripts.native_watchlist_profile import _create_native_qt_application, _DispatcherPhaseProbe

    app, environment, dispatcher_type, _event, Qt, QTimer = _create_native_qt_application()
    report = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "running",
        "environment": environment,
        "isolation": isolation,
        "configuration": vars(args).copy(),
        "errors": [],
        "phases": [],
        "actual_paints": [],
        "model_signals": [],
        "actions": [],
        "blocked_network_attempts": [],
        "parent_events": [],
    }
    report["activation_path"] = "ClassicWorkspace.schedule_restore_last_tab('lhb', delay_ms=0)"
    report["profiler_sha256"] = _sha256(__file__)
    report["diagnostic_only"] = bool(
        args.trace_layout
        or args.skip_refresh_state_restore
        or args.skip_header_restore
        or args.skip_same_current_index
        or args.execute_delayed_items_layout
        or args.resize_header_sections
    )
    report["configuration"] = {
        key: str(value) if isinstance(value, Path) else value for key, value in report["configuration"].items()
    }
    source_files = (
        "ui/tabs/lhb_tab.py",
        "ui/models/stock_table_model.py",
        "ui/components/table_controls.py",
        "ui/models/table_model_helpers.py",
        "ui/tabs/base_stock_tab.py",
        "ui/tabs/table_view_state_binding.py",
        "infra/settings/table_view_state_store.py",
    )
    report["source_sha256"] = {name: _sha256(args.source_root / name) for name in source_files}
    report["original_log"] = _log_evidence(PROJECT_ROOT / "data/logs/vcp_20260905.log")
    isolation.update(
        {
            "startup_orchestrator_suppressed": True,
            "central_quotes_suppressed": True,
            "network_blocked": True,
            "quote_input": "deterministic replay against actual local cache rows",
            "calendar_input": "dates in copied LHB cache",
            "background_prewarm_enabled": True,
            "screenshot_method": "QScreen.grabWindow only after measurement ends",
        }
    )
    original_connect = socket.socket.connect

    def blocked_connect(sock, address):
        report["blocked_network_attempts"].append(str(address))
        raise OSError("network disabled in isolated LHB native profile")

    socket.socket.connect = blocked_connect
    import infra.storage.lhb_pool_repository as repository

    repository.DEFAULT_CACHE_PATH = cache / "lhb_pool_30d.json"
    repository.DEFAULT_LEGACY_POOL_PATH = cache / "lhb_pool_20d.json"
    repository.DEFAULT_SINGLE_DAY_CACHE_PATH = cache / "lhb_cache.json"
    import ui.tabs.lhb_tab as lhb_module
    from core.observability import metric_history
    from infra.diagnostics.ui_stall_probe import get_ui_stall_probe, install_ui_stall_probe
    from ui.components.table_controls import VCPTableView

    payload = json.loads((cache / "lhb_pool_30d.json").read_text(encoding="utf-8"))
    rps = json.loads((cache / "vcp_rps_precomputed.json").read_text(encoding="utf-8"))
    context = json.loads((cache / "ai_industry_chain_context_map.json").read_text(encoding="utf-8"))["context_map"]
    stock_codes = json.loads((cache / "ai_industry_chain_stock_codes.json").read_text(encoding="utf-8"))["stock_codes"]
    lhb_module.LhbPoolManager._stock_universe_provider = staticmethod(lambda: stock_codes)
    lhb_module.LhbTab._get_lhb_trade_dates = lambda self, *a, **kw: sorted(payload["daily_data"])
    lhb_module.LhbTab._get_engine = lambda self: SimpleNamespace(get_precomputed_rps=lambda: rps)
    lhb_module.LhbTab._load_ai_chain_context_map = staticmethod(lambda: dict(context))
    lhb_module.LhbTab.refresh_table_quotes_and_market_caps = lambda self, **kw: self._apply_quote_store_snapshot()

    origin = time.perf_counter()
    report["origin_epoch"] = time.time()
    report["method_trace"] = []
    state = {"phase": "startup", "tab": None, "finished": False, "signal_bound": False, "parents_bound": False}
    dispatcher = _DispatcherPhaseProbe(dispatcher_type.instance())
    dispatcher.start("startup")
    original_paint = VCPTableView.paintEvent
    traced_originals = []

    def header_semantics(header):
        return {
            "sections": [
                [
                    header.sectionSize(i),
                    header.visualIndex(i),
                    header.isSectionHidden(i),
                    header.sectionResizeMode(i).name,
                ]
                for i in range(header.count())
            ],
            "sort_section": header.sortIndicatorSection(),
            "sort_order": header.sortIndicatorOrder().name,
            "stretch_last_section": header.stretchLastSection(),
            "state_sha256": hashlib.sha256(bytes(header.saveState())).hexdigest(),
        }

    def trace_method(owner_class, name):
        original = getattr(owner_class, name, None)
        if original is None:
            report.setdefault("unavailable_python_methods", []).append(name)
            return
        traced_originals.append((owner_class, name, original))

        def traced(owner, *values, **keywords):
            if (
                args.skip_header_restore
                and name == "restoreState"
                and getattr(owner.parent(), "_paint_metric_scope", "") == "lhb"
            ):
                return True
            if (
                args.skip_refresh_state_restore
                and name == "_restore_refresh_state"
                and getattr(owner, "_paint_metric_scope", "") == "lhb"
            ):
                return None
            if (
                args.skip_same_current_index
                and name == "setCurrentIndex"
                and values
                and getattr(owner, "_paint_metric_scope", "") == "lhb"
                and owner.currentIndex() == values[0]
            ):
                return None
            if not args.trace_layout:
                return original(owner, *values, **keywords)
            started = time.perf_counter()
            entry = {
                "name": name,
                "phase": state["phase"],
                "at_ms": (started - origin) * 1000,
                "class": type(owner).__name__,
                "pending_reason": str((getattr(owner, "_pending_paint_metric", None) or {}).get("reason", "")),
            }
            report["method_trace"].append(entry)
            if isinstance(owner, VCPTableView):
                entry["v_scroll_before"] = owner.verticalScrollBar().value()
                entry["current_row_before"] = owner.currentIndex().row()
                if name in ("_capture_refresh_state", "_restore_refresh_state"):
                    entry["header_before"] = header_semantics(owner.horizontalHeader())
            elif name == "restoreState":
                entry["header_before"] = header_semantics(owner)
                entry["restore_bytes_sha256"] = hashlib.sha256(bytes(values[0])).hexdigest()
            try:
                return original(owner, *values, **keywords)
            finally:
                entry["elapsed_ms"] = (time.perf_counter() - started) * 1000
                if isinstance(owner, VCPTableView):
                    entry["v_scroll_after"] = owner.verticalScrollBar().value()
                    entry["current_row_after"] = owner.currentIndex().row()
                elif name == "restoreState":
                    entry["header_after"] = header_semantics(owner)

        setattr(owner_class, name, traced)

    if args.trace_layout or args.skip_refresh_state_restore or args.skip_same_current_index or args.skip_header_restore:
        from PyQt6.QtWidgets import QHeaderView

        for name in (
            "_capture_refresh_state",
            "_schedule_refresh_state_restore",
            "_restore_pending_refresh_state",
            "_restore_refresh_state",
            "_restore_pending_scrollbars",
            "_restore_scrollbars",
            "doItemsLayout",
            "updateGeometries",
            "resizeEvent",
            "_on_sort_indicator_changed",
            "setCurrentIndex",
            "scrollTo",
            "scrollContentsBy",
        ):
            trace_method(VCPTableView, name)
        for name in ("restoreState", "setSortIndicator", "resizeSection"):
            trace_method(QHeaderView, name)

    def timed_paint(table, event):
        is_lhb = str(getattr(table, "_paint_metric_tab", "")) == "lhb"
        parent = table.parent()
        while not is_lhb and parent is not None:
            is_lhb = isinstance(parent, lhb_module.LhbTab)
            parent = parent.parent()
        if not is_lhb or state["finished"]:
            return original_paint(table, event)
        region = QRegion(event.region())
        viewport = table.viewport().rect()
        started = time.perf_counter()
        try:
            return original_paint(table, event)
        finally:
            report["actual_paints"].append(
                {
                    "phase": state["phase"],
                    "at_ms": (started - origin) * 1000,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "full_viewport": QRegion(viewport).subtracted(region).isEmpty(),
                    "rect_count": region.rectCount(),
                    "rows": table.model().rowCount(),
                    "spontaneous": event.spontaneous(),
                    "visible": table.isVisible(),
                    "bounds": [
                        region.boundingRect().x(),
                        region.boundingRect().y(),
                        region.boundingRect().width(),
                        region.boundingRect().height(),
                    ],
                }
            )

    VCPTableView.paintEvent = timed_paint
    if args.execute_delayed_items_layout or args.resize_header_sections:
        original_layout_changed = VCPTableView._on_model_layout_changed
        traced_originals.append((VCPTableView, "_on_model_layout_changed", original_layout_changed))

        def complete_delayed_layout(table, *values):
            original_layout_changed(table, *values)
            if getattr(table, "_paint_metric_scope", "") == "lhb":
                if args.resize_header_sections:
                    table.horizontalHeader().resizeSections()
                if args.execute_delayed_items_layout:
                    table.executeDelayedItemsLayout()

        VCPTableView._on_model_layout_changed = complete_delayed_layout
    import ui.main_window_qt as main_window_module

    window = main_window_module.MainWindowQT(
        startup_enabled=True,
        auto_refresh_enabled=False,
        background_prewarm=True,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
        controlled_startup_probe_guard=False,
    )
    window._startup_enabled = False
    workspace = window._workspace
    install_ui_stall_probe(app, parent=window)

    class ParentEvents(QObject):
        def eventFilter(self, watched, event):
            if not state["finished"] and event.type() in (
                QEvent.Type.Paint,
                QEvent.Type.UpdateRequest,
                QEvent.Type.LayoutRequest,
                QEvent.Type.WindowActivate,
                QEvent.Type.WindowDeactivate,
                QEvent.Type.Resize,
            ):
                report["parent_events"].append(
                    {
                        "phase": state["phase"],
                        "at_ms": (time.perf_counter() - origin) * 1000,
                        "type": event.type().name,
                        "class": type(watched).__name__,
                        "name": watched.objectName(),
                    }
                )
            return False

    parent_probe = ParentEvents(window)
    window.installEventFilter(parent_probe)
    window.resize(1800, 1050)
    window.show()
    window.raise_()
    window.activateWindow()

    def phase(name):
        state["phase"] = name
        dispatcher.set_phase(name)
        report["phases"].append(
            {"name": name, "at_ms": (time.perf_counter() - origin) * 1000, "recorded_at": time.time()}
        )

    def signal(name, *values):
        report["model_signals"].append(
            {
                "phase": state["phase"],
                "name": name,
                "at_ms": (time.perf_counter() - origin) * 1000,
                "args": list(values),
            }
        )

    def bind_tab():
        tab = workspace._tabs_by_key.get("lhb")
        if tab is None:
            return None
        state["tab"] = tab
        if not state["signal_bound"]:
            state["signal_bound"] = True
            for model, prefix in ((tab.model, "source"), (tab.proxy_model, "proxy")):
                model.modelReset.connect(lambda prefix=prefix: signal(prefix + ".modelReset"))
                model.layoutChanged.connect(lambda *a, prefix=prefix: signal(prefix + ".layoutChanged"))
                model.dataChanged.connect(
                    lambda a, b, r, prefix=prefix: signal(
                        prefix + ".dataChanged", a.row(), b.row(), a.column(), b.column(), list(r)
                    )
                )
        if not state["parents_bound"]:
            state["parents_bound"] = True
            parent = tab.table.parent()
            while parent is not None and parent is not window:
                parent.installEventFilter(parent_probe)
                parent = parent.parent()
            if args.trace_layout:
                for child in (
                    tab.table,
                    tab.table.viewport(),
                    tab.table.horizontalHeader(),
                    tab.table.verticalHeader(),
                ):
                    child.installEventFilter(parent_probe)
                for label, header in (
                    ("horizontal", tab.table.horizontalHeader()),
                    ("vertical", tab.table.verticalHeader()),
                ):
                    header.geometriesChanged.connect(lambda label=label: signal(label + ".geometriesChanged"))
                    header.sectionResized.connect(
                        lambda index, before, after, label=label: signal(
                            label + ".sectionResized", index, before, after
                        )
                    )
        return tab

    original_initialize = lhb_module.LhbTab._init_ui

    def initialize_and_bind(tab):
        original_initialize(tab)
        for model, prefix in ((tab.model, "source"), (tab.proxy_model, "proxy")):
            model.modelReset.connect(lambda prefix=prefix: signal(prefix + ".modelReset"))
            model.layoutChanged.connect(lambda *a, prefix=prefix: signal(prefix + ".layoutChanged"))
            model.dataChanged.connect(
                lambda a, b, r, prefix=prefix: signal(
                    prefix + ".dataChanged", a.row(), b.row(), a.column(), b.column(), list(r)
                )
            )
        state["signal_bound"] = True

    lhb_module.LhbTab._init_ui = initialize_and_bind

    def restore():
        phase("restore_last_tab")
        workspace._on_startup_cache_bootstrap_ready()
        workspace.schedule_restore_last_tab("lhb", delay_ms=0)
        QTimer.singleShot(50, wait_rows)

    def wait_rows():
        tab = bind_tab()
        if (
            tab is None
            or not tab.model.rowCount()
            or tab._pool_load_in_progress
            or not tab.table.isVisible()
            or workspace.tabs.currentWidget() is not tab
        ):
            QTimer.singleShot(50, wait_rows)
            return
        report["loaded_rows"] = tab.model.rowCount()
        report["load_reason"] = getattr(tab, "_workspace_load_reason", "")
        report["row_codes"] = [row["代码"] for row in tab.model.row_data]
        viewport = tab.table.viewport()
        report["viewport"] = {
            "size": [viewport.width(), viewport.height()],
            "opaque": viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent),
            "auto_fill_background": viewport.autoFillBackground(),
            "style_sheet": tab.table.styleSheet(),
            "background_role": viewport.backgroundRole().name,
        }
        if tab.model.rowCount() != args.expected_rows:
            report["errors"].append(f"expected {args.expected_rows} real cache rows, got {tab.model.rowCount()}")
        QTimer.singleShot(args.phase_ms, prewarm)

    def idle():
        phase("idle_after_restore")
        QTimer.singleShot(args.phase_ms, repeat_snapshot)

    def repeat_snapshot():
        phase("same_snapshot")
        tab = state["tab"]
        tab._display_pool(
            copy.deepcopy(tab.model.row_data),
            row_data=copy.deepcopy(tab.model.row_data),
            emit_event=False,
            refresh_quotes=False,
        )
        QTimer.singleShot(args.phase_ms, quote_batch)

    def quote_batch():
        phase("quote_and_flash")
        tab = state["tab"]
        quotes = {
            row["代码"]: {"close": 20.0 + i / 100, "last_close": 20.0, "open": 20.0, "zongguben": 100000000.0}
            for i, row in enumerate(tab.model.row_data)
        }
        tab._apply_quote_snapshot(quotes)
        QTimer.singleShot(
            150,
            lambda: report["actions"].append(
                {
                    "phase": state["phase"],
                    "active_flash_cells": sum(len(cells) for cells in tab.model._flash_records.values()),
                }
            ),
        )
        QTimer.singleShot(args.phase_ms, sort_price)

    def sort_price():
        phase("sort_price")
        tab = state["tab"]
        tab.table.sortByColumn(tab.model.headers.index("现价"), Qt.SortOrder.DescendingOrder)
        QTimer.singleShot(args.phase_ms, same_sort)

    def same_sort():
        phase("same_sort_indicator")
        tab = state["tab"]
        tab.table._on_sort_indicator_changed(tab.model.headers.index("现价"), Qt.SortOrder.DescendingOrder)
        QTimer.singleShot(args.phase_ms, parent_update)

    def parent_update():
        phase("parent_background_update")
        window.update()
        QTimer.singleShot(args.phase_ms, finish)

    def prewarm():
        phase("background_prewarm")
        workspace._on_startup_cache_bootstrap_ready()
        QTimer.singleShot(100, poll_prewarm)

    def poll_prewarm():
        status = workspace.background_preload_status()
        if status.get("finished"):
            report["background_prewarm"] = status
            idle()
        elif (time.perf_counter() - origin) * 1000 < args.timeout_ms - 1000:
            QTimer.singleShot(200, poll_prewarm)

    def finish(*, timeout=False):
        if state["finished"]:
            return
        if timeout:
            report["errors"].append("native profile timed out")
        state["finished"] = True
        report["background_prewarm"] = workspace.background_preload_status()
        report["dispatcher"] = dispatcher.finish()
        report["paint_summary"] = _summarize_paints(report["actual_paints"])
        report["model_signal_counts"] = {
            name: dict(Counter(item["name"] for item in report["model_signals"] if item["phase"] == name))
            for name in {item["phase"] for item in report["model_signals"]}
        }
        report["metrics"] = {
            name: [
                {"value": item.value, "tags": item.tags, "recorded_at": item.recorded_at}
                for item in metric_history(name)
            ]
            for name in ("lhb_table_paint_ms", "lhb_table_paint_delay_ms", "ui_event_loop_stall_ms")
        }
        stall = get_ui_stall_probe()
        report["ui_stall_snapshot"] = stall.stall_snapshot() if stall else {}
        tab = state["tab"]
        if tab is not None:
            prices = [
                tab.proxy_model.index(row, tab.model.headers.index("现价")).data()
                for row in range(tab.proxy_model.rowCount())
            ]
            report["final_sorted_prices"] = prices
            report["final_row_count"] = tab.model.rowCount()
        report["acceptance"] = _acceptance(report, args.expected_rows)
        report["errors"].extend(report["acceptance"]["violations"])
        report["status"] = "ok" if not report["errors"] else "error"
        image_path = output / "native_lhb.png"
        screen = window.screen()
        if screen is not None:
            screenshot = screen.grabWindow(int(window.winId()))
            report["screenshot"] = {
                "path": str(image_path),
                "saved": screenshot.save(str(image_path)),
                "size": [screenshot.width(), screenshot.height()],
            }
        (output / "native_lhb_restore_profile.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cleanup = getattr(workspace, "shutdown", None)
        if callable(cleanup):
            cleanup()
        app.quit()

    QTimer.singleShot(500, restore)
    QTimer.singleShot(args.timeout_ms, lambda: finish(timeout=True))
    try:
        app.exec()
    finally:
        socket.socket.connect = original_connect
        VCPTableView.paintEvent = original_paint
        for owner_class, name, method in reversed(traced_originals):
            setattr(owner_class, name, method)
    return report


def main(argv=None):
    args = _parse_args(argv)
    report = run_profile(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "loaded_rows": report.get("loaded_rows"),
                "report": str(args.output_dir.resolve() / "native_lhb_restore_profile.json"),
                "paint_summary": report.get("paint_summary"),
                "errors": report["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
