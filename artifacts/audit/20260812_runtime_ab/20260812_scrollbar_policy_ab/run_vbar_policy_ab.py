"""Native, isolated runtime A/B for hidden Watchlist vertical-scrollbar convergence.

Modes intentionally touch only the VCP vertical scrollbar policy around the
production staging/mount boundary.  Workspace source is never changed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(r"D:\vcp_hunter\紫金研选")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _rect(widget):
    if widget is None:
        return None
    box = widget.geometry()
    return [box.x(), box.y(), box.width(), box.height()]


def _policy_name(value):
    from PyQt6.QtCore import Qt

    names = {
        Qt.ScrollBarPolicy.ScrollBarAsNeeded: "AsNeeded",
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff: "AlwaysOff",
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn: "AlwaysOn",
    }
    return names.get(value, str(value))


def _state(tab, *, placeholder=None, host=None):
    table = getattr(tab, "table_sp", None)
    wrapper = getattr(tab, "table_state", None)
    viewport = table.viewport() if table is not None else None
    bar = table.verticalScrollBar() if table is not None else None
    return {
        "at_monotonic_ms": round(time.perf_counter() * 1000.0, 3),
        "host": _rect(host),
        "placeholder": _rect(placeholder),
        "tab": _rect(tab),
        "wrapper": _rect(wrapper),
        "table": _rect(table),
        "viewport": _rect(viewport),
        "vertical_policy": _policy_name(table.verticalScrollBarPolicy()) if table is not None else "",
        "vbar": (
            {
                "geometry": _rect(bar),
                "minimum": int(bar.minimum()),
                "maximum": int(bar.maximum()),
                "page_step": int(bar.pageStep()),
                "visible": bool(bar.isVisible()),
            }
            if bar is not None
            else None
        ),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: run_vbar_policy_ab.py MODE OUTPUT_DIR")
    mode, output_text = argv
    if mode not in {"control", "hold", "hidden_update_restore", "initial_restore"}:
        raise SystemExit(f"unsupported mode: {mode}")
    output_dir = Path(output_text).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer, Qt
    from ui.components.table_controls import VCPTableView
    from ui.tabs.watchlist_tab import WatchlistTab

    audit = {"mode": mode, "actions": [], "states": [], "initial_policy_events": []}
    restore_origin = {"policy": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_vcp_init = VCPTableView.__init__
    original_after_settle = profile._NativeProfileController._after_watchlist_settle

    if mode == "initial_restore":
        def vcp_init_with_policy(table, *args, **kwargs):
            original_vcp_init(table, *args, **kwargs)
            original_policy = table.verticalScrollBarPolicy()
            table._runtime_ab_original_vbar_policy = original_policy
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            audit["initial_policy_events"].append(
                {
                    "original": _policy_name(original_policy),
                    "forced": "AlwaysOn",
                    "table_object_name": table.objectName(),
                }
            )

        VCPTableView.__init__ = vcp_init_with_policy

    def wrapped_prepare(tab, *args, **kwargs):
        host = tab.parentWidget()
        workspace = getattr(host, "_workspace", None)
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        spec = next((item for item in specs if item.get("key") == "watchlist"), {})
        placeholder = spec.get("page_widget")
        table = getattr(tab, "table_sp", None)
        if not audit["states"]:
            audit["states"].append({"label": "hidden_before", "value": _state(tab, placeholder=placeholder, host=host)})
            root = workspace.window() if workspace is not None else None
            frozen = []
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        frozen.append(
                            {
                                "parent": type(timer.parent()).__name__ if timer.parent() is not None else "",
                                "object_name": timer.objectName(),
                                "interval_ms": int(timer.interval()),
                            }
                        )
                        timer.stop()
            audit["actions"].append({"operation": "stop_active_qtimers", "count": len(frozen), "timers": frozen})

            if table is not None and mode in {"hold", "hidden_update_restore"}:
                restore_origin["policy"] = table.verticalScrollBarPolicy()
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
                audit["actions"].append(
                    {
                        "operation": "force_hidden_policy",
                        "from": _policy_name(restore_origin["policy"]),
                        "to": "AlwaysOn",
                    }
                )
                audit["states"].append({"label": "hidden_always_on", "value": _state(tab, placeholder=placeholder, host=host)})
                if mode == "hidden_update_restore":
                    table.updateGeometries()
                    audit["actions"].append({"operation": "hidden_updateGeometries"})
                    audit["states"].append({"label": "hidden_after_update_geometries", "value": _state(tab, placeholder=placeholder, host=host)})
                    table.setVerticalScrollBarPolicy(restore_origin["policy"])
                    audit["actions"].append(
                        {"operation": "restore_before_mount", "to": _policy_name(restore_origin["policy"])}
                    )
                    audit["states"].append({"label": "hidden_restored_before_mount", "value": _state(tab, placeholder=placeholder, host=host)})
            elif table is not None and mode == "initial_restore":
                restore_origin["policy"] = getattr(
                    table, "_runtime_ab_original_vbar_policy", table.verticalScrollBarPolicy()
                )
                audit["states"].append({"label": "hidden_initial_always_on", "value": _state(tab, placeholder=placeholder, host=host)})
                table.setVerticalScrollBarPolicy(restore_origin["policy"])
                audit["actions"].append(
                    {"operation": "restore_before_mount", "to": _policy_name(restore_origin["policy"])}
                )
                audit["states"].append({"label": "hidden_initial_restored_before_mount", "value": _state(tab, placeholder=placeholder, host=host)})

        return original_prepare(tab, *args, **kwargs)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare

    if mode == "hold":
        def restore_after_initial_settle(controller):
            if audit.get("hold_restore_started"):
                return original_after_settle(controller)
            audit["hold_restore_started"] = True
            workspace = getattr(controller.window, "_workspace", None)
            tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
            table = getattr(tab, "table_sp", None)
            if table is None or restore_origin["policy"] is None:
                return original_after_settle(controller)
            host = tab.parentWidget()
            specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
            spec = next((item for item in specs if item.get("key") == "watchlist"), {})
            audit["states"].append(
                {"label": "before_settle_restore", "value": _state(tab, placeholder=spec.get("page_widget"), host=host)}
            )
            table.setVerticalScrollBarPolicy(restore_origin["policy"])
            table.updateGeometries()
            audit["actions"].append(
                {"operation": "restore_after_initial_settle", "to": _policy_name(restore_origin["policy"])}
            )
            audit["states"].append(
                {"label": "after_settle_restore_immediate", "value": _state(tab, placeholder=spec.get("page_widget"), host=host)}
            )
            # Let the normal backing-store/layout cycle settle before its final
            # acceptance snapshot; table remains alive for this callback.
            QTimer.singleShot(120, lambda: original_after_settle(controller))

        profile._NativeProfileController._after_watchlist_settle = restore_after_initial_settle
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms", "300",
                "--settle-ms", "2200",
                "--load-timeout-ms", "15000",
                "--heartbeat-ms", "25",
                "--output-dir", str(output_dir),
                "--no-cprofile",
            ]
        )
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare
        VCPTableView.__init__ = original_vcp_init
        profile._NativeProfileController._after_watchlist_settle = original_after_settle

    watchlist = report.get("watchlist", {})
    runtime = watchlist.get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "final": {
            "row_count": watchlist.get("row_count"),
            "visible": watchlist.get("visible"),
            "watchlist_page_size": runtime.get("watchlist_page_size"),
            "table_size": runtime.get("table_size"),
            "viewport_size": runtime.get("viewport_size"),
        },
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    (output_dir / "vbar_policy_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit["result"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
