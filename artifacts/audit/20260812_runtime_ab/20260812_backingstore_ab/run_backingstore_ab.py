"""Native runtime A/B for QAbstractScrollArea reveal update/background handling."""

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
    box = widget.geometry()
    return [box.x(), box.y(), box.width(), box.height()]


def _state(tab):
    from PyQt6.QtCore import Qt

    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    result = {}
    for label, widget in (("tab", tab), ("table", table), ("viewport", viewport)):
        if widget is None:
            result[label] = None
            continue
        result[label] = {
            "geometry": _rect(widget),
            "visible": bool(widget.isVisible()),
            "updates_enabled": bool(widget.updatesEnabled()),
            "auto_fill_background": bool(widget.autoFillBackground()),
            "wa_opaque": bool(widget.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)),
            "wa_no_system_background": bool(widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)),
            "wa_styled_background": bool(widget.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)),
        }
    if table is not None:
        bar = table.verticalScrollBar()
        result["vbar"] = {
            "visible": bool(bar.isVisible()),
            "maximum": int(bar.maximum()),
            "page_step": int(bar.pageStep()),
            "geometry": _rect(bar),
        }
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: run_backingstore_ab.py MODE OUTPUT_DIR")
    mode, output_text = argv
    modes = {"control", "viewport_updates_boundary", "tab_updates_boundary", "no_system_background_hold"}
    if mode not in modes:
        raise SystemExit(f"unsupported mode: {mode}")
    output_dir = Path(output_text).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer, Qt
    import ui.workspaces.classic_workspace as workspace_module
    from ui.tabs.watchlist_tab import WatchlistTab

    audit = {"mode": mode, "states": [], "actions": [], "frozen_qtimers": []}
    state = {"tab": None, "target": None, "restore_after_replace": False, "no_system_original": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_replace = workspace_module._replace_workspace_placeholder
    original_after_settle = profile._NativeProfileController._after_watchlist_settle
    original_finish = profile._NativeProfileController._finish

    def wrapped_prepare(tab, *args, **kwargs):
        if state["tab"] is None:
            state["tab"] = tab
            audit["states"].append({"label": "hidden_before", "value": _state(tab)})
            workspace = getattr(tab.parentWidget(), "_workspace", None)
            root = workspace.window() if workspace is not None else None
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        audit["frozen_qtimers"].append(
                            {
                                "parent": type(timer.parent()).__name__ if timer.parent() is not None else "",
                                "object_name": timer.objectName(),
                                "interval_ms": int(timer.interval()),
                            }
                        )
                        timer.stop()
            table = getattr(tab, "table_sp", None)
            viewport = table.viewport() if table is not None else None
            if mode == "viewport_updates_boundary" and viewport is not None:
                state["target"] = viewport
                state["restore_after_replace"] = True
                viewport.setUpdatesEnabled(False)
                audit["actions"].append({"operation": "disable_viewport_updates_before_prepare"})
                audit["states"].append({"label": "hidden_viewport_updates_disabled", "value": _state(tab)})
            elif mode == "tab_updates_boundary":
                state["target"] = tab
                state["restore_after_replace"] = True
                tab.setUpdatesEnabled(False)
                audit["actions"].append({"operation": "disable_watchlist_updates_before_prepare"})
                audit["states"].append({"label": "hidden_tab_updates_disabled", "value": _state(tab)})
            elif mode == "no_system_background_hold" and viewport is not None:
                state["target"] = viewport
                state["no_system_original"] = bool(
                    viewport.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
                )
                viewport.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
                audit["actions"].append({
                    "operation": "set_viewport_wa_no_system_background",
                    "from": state["no_system_original"],
                    "to": True,
                })
                audit["states"].append({"label": "hidden_no_system_background", "value": _state(tab)})
        return original_prepare(tab, *args, **kwargs)

    def wrapped_replace(workspace, spec, key, index, widget):
        result = original_replace(workspace, spec, key, index, widget)
        if key == "watchlist" and widget is state["tab"] and state["restore_after_replace"]:
            audit["states"].append({"label": "mounted_before_updates_reenable", "value": _state(widget)})
            target = state["target"]
            if target is not None:
                target.setUpdatesEnabled(True)
            audit["actions"].append({"operation": "reenable_updates_after_replace"})
            audit["states"].append({"label": "mounted_after_updates_reenable", "value": _state(widget)})
        return result

    def wrapped_after_settle(controller):
        if mode != "no_system_background_hold" or audit.get("background_restored"):
            return original_after_settle(controller)
        audit["background_restored"] = True
        tab = state["tab"]
        target = state["target"]
        if tab is not None and target is not None:
            audit["states"].append({"label": "before_background_restore", "value": _state(tab)})
            target.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, bool(state["no_system_original"]))
            audit["actions"].append({
                "operation": "restore_viewport_wa_no_system_background_after_settle",
                "to": bool(state["no_system_original"]),
            })
            audit["states"].append({"label": "after_background_restore_immediate", "value": _state(tab)})
        # Execute the production profile's final accounting after a tiny normal
        # event-loop turn while the widgets remain alive.
        QTimer.singleShot(120, lambda: original_after_settle(controller))

    def wrapped_finish(controller):
        tab = state["tab"]
        if tab is not None:
            audit["states"].append({"label": "before_profile_finish", "value": _state(tab)})
        return original_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    workspace_module._replace_workspace_placeholder = wrapped_replace
    profile._NativeProfileController._after_watchlist_settle = wrapped_after_settle
    profile._NativeProfileController._finish = wrapped_finish
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms", "300",
                "--settle-ms", "1800",
                "--load-timeout-ms", "15000",
                "--heartbeat-ms", "25",
                "--output-dir", str(output_dir),
                "--no-cprofile",
            ]
        )
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare
        workspace_module._replace_workspace_placeholder = original_replace
        profile._NativeProfileController._after_watchlist_settle = original_after_settle
        profile._NativeProfileController._finish = original_finish

    runtime = report.get("watchlist", {}).get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "final_runtime": {
            "row_count": report.get("watchlist", {}).get("row_count"),
            "visible": report.get("watchlist", {}).get("visible"),
            "watchlist_page_size": runtime.get("watchlist_page_size"),
            "table_size": runtime.get("table_size"),
            "viewport_size": runtime.get("viewport_size"),
        },
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    (output_dir / "backingstore_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit["result"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
