"""Runtime-only true reveal-barrier A/B for staged Watchlist mounting.

The production mount is completed first.  Only updates of the newly mounted
Watchlist page are held across N queued event-loop turns, then re-enabled.  No
paint guard, model mutation, tab removal, or source edit is involved.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject


PROJECT_ROOT = Path(r"D:\vcp_hunter\紫金研选")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _rect(widget):
    if widget is None:
        return None
    box = widget.geometry()
    return [box.x(), box.y(), box.width(), box.height()]


def _widget_state(tab):
    from PyQt6.QtCore import Qt

    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    return {
        "tab": {
            "geometry": _rect(tab),
            "visible": bool(tab.isVisible()),
            "updates_enabled": bool(tab.updatesEnabled()),
        },
        "table": {
            "geometry": _rect(table),
            "visible": bool(table.isVisible()) if table is not None else False,
            "updates_enabled": bool(table.updatesEnabled()) if table is not None else False,
            "wa_opaque": bool(table.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)) if table is not None else False,
        },
        "viewport": {
            "geometry": _rect(viewport),
            "visible": bool(viewport.isVisible()) if viewport is not None else False,
            "updates_enabled": bool(viewport.updatesEnabled()) if viewport is not None else False,
            "wa_opaque": bool(viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)) if viewport is not None else False,
            "wa_no_system_background": bool(
                viewport.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            ) if viewport is not None else False,
        },
    }


def _screen_capture(window, widget, path: Path):
    """Capture the actual native screen crop, without QWidget.render()/paint."""
    try:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(widget.mapToGlobal(QPoint(1, 1))) or window.screen()
        origin = widget.mapToGlobal(QPoint(0, 0))
        pixmap = screen.grabWindow(0, origin.x(), origin.y(), widget.width(), widget.height())
        if pixmap.isNull():
            return {"saved": False, "reason": "null_pixmap"}
        pixmap.save(str(path), "PNG")
        image = pixmap.toImage()
        samples = []
        step_x = max(1, image.width() // 32)
        step_y = max(1, image.height() // 18)
        for y in range(0, image.height(), step_y):
            for x in range(0, image.width(), step_x):
                color = image.pixelColor(x, y)
                samples.append((color.red(), color.green(), color.blue(), color.alpha()))
        payload = bytes(value for sample in samples for value in sample)
        return {
            "saved": True,
            "size": [image.width(), image.height()],
            "sample_distinct_rgba": len(set(samples)),
            "sample_alpha_nonzero": sum(sample[3] > 0 for sample in samples),
            "sample_count": len(samples),
            "sample_sha256": hashlib.sha256(payload).hexdigest(),
            "path": str(path),
        }
    except Exception as exc:  # diagnostic-only; do not disturb the reveal.
        return {"saved": False, "reason": str(exc)}


class _VcpPaintProbe(QObject):
    def __init__(self, app, tab, state, origin):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app = app
        self.tab = tab
        self.state = state
        self.origin = origin
        self.QEvent = QEvent
        self.events = []
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() != self.QEvent.Type.Paint:
            return False
        table = getattr(self.tab, "table_sp", None)
        viewport = table.viewport() if table is not None else None
        if obj is not table and obj is not viewport:
            return False
        at_ms = round((time.perf_counter() - self.origin) * 1000.0, 3)
        row = {
            "at_ms": at_ms,
            "target": "table" if obj is table else "viewport",
            "barrier_active": bool(self.state.get("barrier_active")),
            "turns_completed": int(self.state.get("turns_completed", 0)),
            "after_reenable_ms": (
                round(at_ms - float(self.state["reenable_at_ms"]), 3)
                if self.state.get("reenable_at_ms") is not None
                else None
            ),
        }
        self.events.append(row)
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: run_reveal_barrier_ab.py TURNS OUTPUT_DIR")
    turns = int(argv[0])
    if turns < 0 or turns > 3:
        raise SystemExit("TURNS must be 0..3")
    output_dir = Path(argv[1]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import ui.workspaces.classic_workspace as workspace_module
    from ui.tabs.watchlist_tab import WatchlistTab

    origin = time.perf_counter()
    audit = {"turns_requested": turns, "states": [], "actions": [], "frozen_qtimers": [], "captures": {}}
    state = {
        "tab": None,
        "workspace": None,
        "barrier_active": False,
        "turns_completed": 0,
        "reenable_at_ms": None,
        "mount_return_at_ms": None,
    }
    paint_probe = {"value": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_replace = workspace_module._replace_workspace_placeholder
    original_finish = profile._NativeProfileController._finish

    def stamp(label, tab):
        audit["states"].append(
            {
                "label": label,
                "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
                "value": _widget_state(tab),
            }
        )

    def reveal_after_turns():
        tab = state["tab"]
        if tab is None:
            return
        if state["turns_completed"] < turns:
            state["turns_completed"] += 1
            stamp(f"after_event_loop_turn_{state['turns_completed']}", tab)
            QTimer.singleShot(0, reveal_after_turns)
            return
        stamp("before_reenable", tab)
        tab.setUpdatesEnabled(True)
        state["barrier_active"] = False
        state["reenable_at_ms"] = round((time.perf_counter() - origin) * 1000.0, 3)
        audit["actions"].append({"operation": "reenable_watchlist_updates", "at_ms": state["reenable_at_ms"]})
        stamp("after_reenable", tab)

        def capture_after_reveal():
            stamp("after_reenable_settle", tab)

        QTimer.singleShot(80, capture_after_reveal)

    def wrapped_prepare(tab, *args, **kwargs):
        if state["tab"] is None:
            state["tab"] = tab
            state["workspace"] = getattr(tab.parentWidget(), "_workspace", None)
            root = state["workspace"].window() if state["workspace"] is not None else None
            paint_probe["value"] = _VcpPaintProbe(QApplication.instance(), tab, state, origin)
            stamp("hidden_before", tab)
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
            if turns > 0:
                tab.setUpdatesEnabled(False)
                state["barrier_active"] = True
                audit["actions"].append({"operation": "disable_watchlist_updates_before_production_mount"})
                stamp("hidden_updates_disabled", tab)
        return original_prepare(tab, *args, **kwargs)

    def wrapped_replace(workspace, spec, key, index, widget):
        result = original_replace(workspace, spec, key, index, widget)
        if key == "watchlist" and widget is state["tab"]:
            state["mount_return_at_ms"] = round((time.perf_counter() - origin) * 1000.0, 3)
            stamp("production_mount_return", widget)
            if turns > 0:
                QTimer.singleShot(0, reveal_after_turns)
        return result

    def wrapped_finish(controller):
        tab = state["tab"]
        if tab is not None:
            stamp("before_profile_finish", tab)
        return original_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    workspace_module._replace_workspace_placeholder = wrapped_replace
    profile._NativeProfileController._finish = wrapped_finish
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms", "300",
                "--settle-ms", "1900",
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
        profile._NativeProfileController._finish = original_finish
    probe = paint_probe["value"]
    if probe is not None:
        probe.close()
        audit["vcp_paint_events"] = probe.events
    else:
        audit["vcp_paint_events"] = []
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
    audit["timing"] = {
        "mount_return_at_ms": state["mount_return_at_ms"],
        "reenable_at_ms": state["reenable_at_ms"],
        "reveal_delay_ms": (
            round(float(state["reenable_at_ms"]) - float(state["mount_return_at_ms"]), 3)
            if state["reenable_at_ms"] is not None and state["mount_return_at_ms"] is not None
            else 0.0
        ),
        "turns_completed": int(state["turns_completed"]),
    }
    (output_dir / "reveal_barrier_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"result": audit["result"], "timing": audit["timing"], "paint_events": audit["vcp_paint_events"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
