"""Runtime A/B: mount Watchlist, then reveal after outer-layout quiescence.

No paint event is consumed and no model/table guard is installed.  The page's
updates are held only while the regular Qt queue drains, with a hard 50 ms cap
and three consecutive event-loop turns without an upstream LayoutRequest.
"""

from __future__ import annotations

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
    rect = widget.geometry()
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def _state(tab):
    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    return {
        "tab_visible": bool(tab.isVisible()),
        "tab_updates": bool(tab.updatesEnabled()),
        "tab_geometry": _rect(tab),
        "table_visible": bool(table.isVisible()) if table is not None else False,
        "table_updates": bool(table.updatesEnabled()) if table is not None else False,
        "table_geometry": _rect(table),
        "viewport_visible": bool(viewport.isVisible()) if viewport is not None else False,
        "viewport_updates": bool(viewport.updatesEnabled()) if viewport is not None else False,
        "viewport_geometry": _rect(viewport),
    }


class _LayoutObserver(QObject):
    def __init__(self, app, workspace, tab, origin):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app = app
        self.workspace = workspace
        self.tab = tab
        self.QEvent = QEvent
        self.origin = origin
        self.active = False
        self.layout_seen_since_turn = 0
        self.layout_events = []
        self.vcp_paints = []
        app.installEventFilter(self)

    def _name(self, obj):
        try:
            cls = obj.metaObject().className()
        except Exception:
            cls = type(obj).__name__
        try:
            name = obj.objectName()
        except Exception:
            name = ""
        return f"{cls}#{name}" if name else str(cls)

    def _is_upstream(self, obj):
        if obj is self.workspace or obj is getattr(self.workspace, "tabs", None):
            return True
        parent = obj
        for _ in range(10):
            try:
                parent = parent.parent()
            except Exception:
                return False
            if parent is None:
                return False
            if parent is self.workspace:
                return True
        return False

    def eventFilter(self, obj, event):
        if not self.active:
            return False
        at_ms = round((time.perf_counter() - self.origin) * 1000.0, 3)
        if event.type() == self.QEvent.Type.LayoutRequest and self._is_upstream(obj):
            self.layout_seen_since_turn += 1
            self.layout_events.append({"at_ms": at_ms, "object": self._name(obj)})
        elif event.type() == self.QEvent.Type.Paint:
            table = getattr(self.tab, "table_sp", None)
            viewport = table.viewport() if table is not None else None
            if obj is table or obj is viewport:
                self.vcp_paints.append({"at_ms": at_ms, "target": "table" if obj is table else "viewport"})
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: run_layout_quiescence_barrier.py OUTPUT_DIR")
    output_dir = Path(argv[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import ui.workspaces.classic_workspace as workspace_module
    from ui.tabs.watchlist_tab import WatchlistTab

    origin = time.perf_counter()
    audit = {
        "policy": {"max_elapsed_ms": 50.0, "required_quiet_turns": 3},
        "timeline": [],
        "frozen_qtimers": [],
    }
    state = {"tab": None, "workspace": None, "mount_at": None, "reenable_at": None, "turn": 0, "quiet": 0}
    observer = {"value": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_replace = workspace_module._replace_workspace_placeholder
    original_finish = profile._NativeProfileController._finish

    def stamp(label, **extra):
        tab = state["tab"]
        audit["timeline"].append(
            {
                "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
                "label": label,
                "state": _state(tab) if tab is not None else {},
                **extra,
            }
        )

    def reveal_or_continue():
        tab = state["tab"]
        obs = observer["value"]
        if tab is None or obs is None:
            return
        state["turn"] += 1
        seen = obs.layout_seen_since_turn
        obs.layout_seen_since_turn = 0
        if seen:
            state["quiet"] = 0
        else:
            state["quiet"] += 1
        elapsed = (time.perf_counter() - (state["mount_perf"] or time.perf_counter())) * 1000.0
        stamp("barrier_turn", turn=state["turn"], upstream_layout_requests=seen, quiet_turns=state["quiet"], elapsed_since_mount_ms=round(elapsed, 3))
        if state["quiet"] >= 3 or elapsed >= 50.0:
            stamp("before_reenable", quiescence_reached=state["quiet"] >= 3, cap_reached=elapsed >= 50.0)
            tab.setUpdatesEnabled(True)
            obs.active = False
            state["reenable_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
            stamp("after_reenable")
            return
        QTimer.singleShot(0, reveal_or_continue)

    def wrapped_prepare(tab, *args, **kwargs):
        if state["tab"] is None:
            state["tab"] = tab
            state["workspace"] = getattr(tab.parentWidget(), "_workspace", None)
            root = state["workspace"].window() if state["workspace"] is not None else None
            stamp("hidden_before")
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        audit["frozen_qtimers"].append(
                            {"parent": type(timer.parent()).__name__ if timer.parent() is not None else "", "interval_ms": int(timer.interval())}
                        )
                        timer.stop()
            observer["value"] = _LayoutObserver(QApplication.instance(), state["workspace"], tab, origin)
            tab.setUpdatesEnabled(False)
            stamp("hidden_updates_disabled")
        return original_prepare(tab, *args, **kwargs)

    def wrapped_replace(workspace, spec, key, index, widget):
        result = original_replace(workspace, spec, key, index, widget)
        if key == "watchlist" and widget is state["tab"]:
            state["mount_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
            state["mount_perf"] = time.perf_counter()
            observer["value"].active = True
            stamp("production_mount_return")
            QTimer.singleShot(0, reveal_or_continue)
        return result

    def wrapped_finish(controller):
        stamp("before_profile_finish")
        return original_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    workspace_module._replace_workspace_placeholder = wrapped_replace
    profile._NativeProfileController._finish = wrapped_finish
    try:
        args = profile._parse_args(
            ["--background-prewarm", "--restore-last-tab", "--warmup-ms", "300", "--settle-ms", "1900", "--load-timeout-ms", "15000", "--heartbeat-ms", "25", "--output-dir", str(output_dir), "--no-cprofile"]
        )
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare
        workspace_module._replace_workspace_placeholder = original_replace
        profile._NativeProfileController._finish = original_finish
    obs = observer["value"]
    if obs is not None:
        obs.close()
        audit["upstream_layout_requests"] = obs.layout_events
        audit["vcp_paint_events_while_barrier_active"] = obs.vcp_paints
    else:
        audit["upstream_layout_requests"] = []
        audit["vcp_paint_events_while_barrier_active"] = []
    runtime = report.get("watchlist", {}).get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "final": {"row_count": report.get("watchlist", {}).get("row_count"), "visible": report.get("watchlist", {}).get("visible"), "page_size": runtime.get("watchlist_page_size"), "table_size": runtime.get("table_size"), "viewport_size": runtime.get("viewport_size")},
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    audit["barrier"] = {"turns": state["turn"], "quiet_turns": state["quiet"], "mount_at_ms": state["mount_at"], "reenable_at_ms": state["reenable_at"], "delay_ms": round(float(state["reenable_at"] - state["mount_at"]), 3) if state["reenable_at"] is not None else None}
    (output_dir / "layout_quiescence_barrier_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": audit["result"], "barrier": audit["barrier"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
