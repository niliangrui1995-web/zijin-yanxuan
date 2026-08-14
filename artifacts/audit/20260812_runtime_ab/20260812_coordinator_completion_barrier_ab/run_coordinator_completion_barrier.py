"""Runtime A/B: delay only Watchlist coordinator completion before mount.

The real Watchlist remains staged under the existing hidden staging host while
the original LazyTabPlaceholder stays current.  After three zero-delay event
turns without a workspace-branch LayoutRequest (hard cap 50 ms), the original
``_complete_active_step`` is invoked unchanged; it performs the normal mount,
reveal, and visible-Watchlist lazy handoff.  No update suppression, model
change, or Paint-event consumption is used.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject


PROJECT_ROOT = Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _rect(widget):
    if widget is None:
        return None
    value = widget.geometry()
    return [value.x(), value.y(), value.width(), value.height()]


def _object_info(widget):
    if widget is None:
        return {"exists": False}
    try:
        parent = widget.parentWidget()
        return {
            "exists": True,
            "class": widget.metaObject().className(),
            "object_name": widget.objectName(),
            "visible": bool(widget.isVisible()),
            "updates_enabled": bool(widget.updatesEnabled()),
            "geometry": _rect(widget),
            "parent_class": parent.metaObject().className() if parent is not None else "",
            "parent_object_name": parent.objectName() if parent is not None else "",
        }
    except RuntimeError:
        return {"exists": False, "deleted": True}


def _state(workspace, tab):
    tabs = getattr(workspace, "tabs", None)
    current = tabs.currentWidget() if tabs is not None else None
    spec = workspace._spec_for_key_or_index("watchlist") if workspace is not None else None
    placeholder = (spec or {}).get("page_widget") if spec else None
    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    return {
        "tab_count": int(tabs.count()) if tabs is not None else None,
        "current_index": int(tabs.currentIndex()) if tabs is not None else None,
        "current": _object_info(current),
        "current_is_watchlist": bool(current is tab) if tabs is not None else False,
        "watchlist": _object_info(tab),
        "watchlist_parent": _object_info(tab.parentWidget()) if tab is not None else {"exists": False},
        "watchlist_table": _object_info(table),
        "watchlist_viewport": _object_info(viewport),
        "watchlist_spec_mounted": bool((spec or {}).get("mounted", False)),
        "watchlist_placeholder": _object_info(placeholder),
    }


class _Observer(QObject):
    def __init__(self, app, workspace, tab, origin):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app = app
        self.workspace = workspace
        self.tab = tab
        self.QEvent = QEvent
        self.origin = origin
        self.layout_since_turn = 0
        self.layout_events = []
        self.vcp_paints = []
        app.installEventFilter(self)

    def _name(self, obj):
        try:
            class_name = obj.metaObject().className()
            name = obj.objectName()
            return f"{class_name}#{name}" if name else str(class_name)
        except Exception:
            return type(obj).__name__

    def _in_workspace_branch(self, obj):
        node = obj
        for _ in range(14):
            if node is self.workspace:
                return True
            try:
                node = node.parent()
            except Exception:
                return False
            if node is None:
                return False
        return False

    def eventFilter(self, obj, event):
        at_ms = round((time.perf_counter() - self.origin) * 1000.0, 3)
        if event.type() == self.QEvent.Type.LayoutRequest and self._in_workspace_branch(obj):
            self.layout_since_turn += 1
            self.layout_events.append({"at_ms": at_ms, "object": self._name(obj)})
        elif event.type() == self.QEvent.Type.Paint:
            table = getattr(self.tab, "table_sp", None)
            viewport = table.viewport() if table is not None else None
            if obj is table or obj is viewport:
                self.vcp_paints.append(
                    {"at_ms": at_ms, "target": "table" if obj is table else "viewport"}
                )
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: run_coordinator_completion_barrier.py OUTPUT_DIR")
    output_dir = Path(argv[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from ui.workspaces.background_tab_preload import BackgroundTabPreloadCoordinator

    origin = time.perf_counter()
    audit = {
        "policy": {"max_elapsed_ms": 50.0, "required_quiet_turns": 3},
        "timeline": [],
    }
    state = {
        "workspace": None,
        "tab": None,
        "observer": None,
        "barrier_started_at": None,
        "completion_called_at": None,
        "turns": 0,
        "quiet_turns": 0,
        "intercept_count": 0,
        "settle_started": False,
    }
    original_complete = BackgroundTabPreloadCoordinator._complete_active_step
    original_finish = profile._NativeProfileController._finish

    def stamp(label, **extra):
        workspace = state["workspace"]
        tab = state["tab"]
        audit["timeline"].append(
            {
                "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
                "label": label,
                "state": _state(workspace, tab) if workspace is not None and tab is not None else {},
                **extra,
            }
        )

    def finish_original_completion(coordinator):
        if state["completion_called_at"] is not None:
            return
        state["completion_called_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
        stamp("before_original_complete_active_step")
        original_complete(coordinator)
        stamp("after_original_complete_active_step")

    def settle_turn(coordinator):
        observer = state["observer"]
        started_at = state["barrier_started_at"]
        if observer is None or started_at is None or state["completion_called_at"] is not None:
            return
        state["turns"] += 1
        seen = observer.layout_since_turn
        observer.layout_since_turn = 0
        state["quiet_turns"] = 0 if seen else state["quiet_turns"] + 1
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        stamp(
            "barrier_turn",
            turn=state["turns"],
            upstream_layout_requests=seen,
            quiet_turns=state["quiet_turns"],
            elapsed_since_ready_ms=round(elapsed_ms, 3),
        )
        if state["quiet_turns"] >= 3 or elapsed_ms >= 50.0:
            stamp(
                "coordinator_completion_release",
                quiescence_reached=state["quiet_turns"] >= 3,
                cap_reached=elapsed_ms >= 50.0,
            )
            finish_original_completion(coordinator)
            return
        QTimer.singleShot(0, lambda: settle_turn(coordinator))

    def wrapped_complete(coordinator):
        workspace = coordinator.workspace
        active_key = str(getattr(workspace, "_background_prewarm_active_key", "") or "")
        if active_key != "watchlist":
            return original_complete(coordinator)
        state["intercept_count"] += 1
        if state["settle_started"]:
            return None
        state["settle_started"] = True
        state["workspace"] = workspace
        state["tab"] = getattr(workspace, "_background_prewarm_active_widget", None)
        state["barrier_started_at"] = time.perf_counter()
        timer = getattr(coordinator, "timer", None)
        if timer is not None:
            timer.stop()
        state["observer"] = _Observer(QApplication.instance(), workspace, state["tab"], origin)
        stamp("watchlist_ready_completion_deferred")
        QTimer.singleShot(0, lambda: settle_turn(coordinator))
        return None

    def wrapped_profile_finish(controller):
        stamp("before_profile_finish")
        return original_finish(controller)

    BackgroundTabPreloadCoordinator._complete_active_step = wrapped_complete
    profile._NativeProfileController._finish = wrapped_profile_finish
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms",
                "300",
                "--settle-ms",
                "1900",
                "--load-timeout-ms",
                "15000",
                "--heartbeat-ms",
                "25",
                "--output-dir",
                str(output_dir),
                "--no-cprofile",
            ]
        )
        report, _ = profile.run_profile(args)
    finally:
        BackgroundTabPreloadCoordinator._complete_active_step = original_complete
        profile._NativeProfileController._finish = original_finish

    observer = state["observer"]
    if observer is not None:
        observer.close()
        audit["upstream_layout_requests"] = observer.layout_events
        audit["vcp_paint_events"] = observer.vcp_paints
    else:
        audit["upstream_layout_requests"] = []
        audit["vcp_paint_events"] = []
    runtime = report.get("watchlist", {}).get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "background_prewarm": report.get("background_prewarm", {}),
        "final": {
            "row_count": report.get("watchlist", {}).get("row_count"),
            "visible": report.get("watchlist", {}).get("visible"),
            "load_reason": report.get("watchlist", {}).get("workspace_load_reason"),
            "page_size": runtime.get("watchlist_page_size"),
            "table_size": runtime.get("table_size"),
            "viewport_size": runtime.get("viewport_size"),
        },
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    started_at = state["barrier_started_at"]
    released_at = state["completion_called_at"]
    audit["barrier"] = {
        "intercept_count": state["intercept_count"],
        "turns": state["turns"],
        "quiet_turns": state["quiet_turns"],
        "ready_deferred_at_ms": round((started_at - origin) * 1000.0, 3) if started_at else None,
        "original_complete_at_ms": released_at,
        "delay_ms": round(float(released_at - (started_at - origin) * 1000.0), 3) if started_at and released_at is not None else None,
    }
    (output_dir / "coordinator_completion_barrier_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"result": audit["result"], "barrier": audit["barrier"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
