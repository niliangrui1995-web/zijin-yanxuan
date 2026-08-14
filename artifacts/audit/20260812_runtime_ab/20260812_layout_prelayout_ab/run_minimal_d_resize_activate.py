"""Isolated runtime-only A/B: pre-size staged Watchlist then activate its root layout.

This never imports or modifies workspace source files.  It hooks the production
prepare_workspace_preload_reveal call in a child process and emits a compact
audit beside the native profiler report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(r"D:\vcp_hunter\紫金研选")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _rect(widget):
    if widget is None:
        return None
    geometry = widget.geometry()
    return [geometry.x(), geometry.y(), geometry.width(), geometry.height()]


def _scrollbar_state(bar):
    if bar is None:
        return None
    return {
        "geometry": _rect(bar),
        "min": int(bar.minimum()),
        "max": int(bar.maximum()),
        "page": int(bar.pageStep()),
        "value": int(bar.value()),
        "visible": bool(bar.isVisible()),
    }


def _table_snapshot(tab, placeholder=None, host=None):
    table = getattr(tab, "table_sp", None)
    wrapper = getattr(tab, "table_state", None)
    viewport = table.viewport() if table is not None else None
    horizontal_header = table.horizontalHeader() if table is not None else None
    vertical_header = table.verticalHeader() if table is not None else None
    return {
        "host": _rect(host),
        "placeholder": _rect(placeholder),
        "tab": _rect(tab),
        "wrapper": _rect(wrapper),
        "table": _rect(table),
        "viewport": _rect(viewport),
        "hheader": _rect(horizontal_header),
        "vheader": _rect(vertical_header),
        "hbar": _scrollbar_state(table.horizontalScrollBar() if table is not None else None),
        "vbar": _scrollbar_state(table.verticalScrollBar() if table is not None else None),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: run_minimal_d_resize_activate.py OUTPUT_DIR")
    output_dir = Path(argv[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from ui.tabs.watchlist_tab import WatchlistTab

    audit = {"mode": "resize_plus_tab_layout_activate", "actions": [], "seen": 0}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal

    def wrapped_prepare(tab, *args, **kwargs):
        # The factory can create other Watchlist instances only in unusual
        # runs; action exactly once preserves the normal user-facing route.
        if not audit["seen"]:
            audit["seen"] = 1
            host = tab.parentWidget()
            workspace = getattr(host, "_workspace", None)
            specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
            spec = next((item for item in specs if item.get("key") == "watchlist"), {})
            placeholder = spec.get("page_widget")
            audit["hidden_before"] = _table_snapshot(tab, placeholder, host)

            root = workspace.window() if workspace is not None else None
            frozen = []
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        frozen.append({
                            "parent": type(timer.parent()).__name__ if timer.parent() is not None else "",
                            "object_name": timer.objectName(),
                            "interval_ms": int(timer.interval()),
                        })
                        timer.stop()
            audit["actions"].append({"operation": "stop_active_qtimers", "count": len(frozen), "timers": frozen})

            target = placeholder.size() if placeholder is not None else tab.size()
            if host is not None:
                host.resize(target)
            tab.resize(target)
            audit["actions"].append({
                "operation": "staging_host_and_watchlist_resize",
                "target": [int(target.width()), int(target.height())],
            })
            layout = tab.layout()
            activated = bool(layout.activate()) if layout is not None else False
            audit["actions"].append({"operation": "tab_layout_activate", "result": activated})
            audit["hidden_after_action"] = _table_snapshot(tab, placeholder, host)
        return original_prepare(tab, *args, **kwargs)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms", "300",
                "--settle-ms", "1500",
                "--load-timeout-ms", "15000",
                "--heartbeat-ms", "25",
                "--output-dir", str(output_dir),
                "--no-cprofile",
            ]
        )
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare

    watchlist = report.get("watchlist", {})
    repaint = watchlist.get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "watchlist_final": {
            "row_count": watchlist.get("row_count"),
            "visible": watchlist.get("visible"),
            "workspace_load_reason": watchlist.get("workspace_load_reason"),
            "watchlist_page_size": repaint.get("watchlist_page_size"),
            "table_size": repaint.get("table_size"),
            "viewport_size": repaint.get("viewport_size"),
        },
        "paint_reasons": (
            report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", [])
        ),
        "paint_after_first": (
            report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {})
        ),
        "paint_durations": (
            report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {})
        ),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    (output_dir / "minimal_d_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit["result"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
