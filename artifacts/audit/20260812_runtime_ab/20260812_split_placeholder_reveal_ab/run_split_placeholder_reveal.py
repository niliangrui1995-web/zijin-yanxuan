"""A/B explicit reveal barrier: keep old LazyTabPlaceholder current until quiescent.

The real Watchlist is inserted into the production QTabWidget immediately, but
the already-visible placeholder remains current while the Qt layout queue
settles.  Then the exact current-index/remove-old transition happens once.
This test is runtime-only and leaves source files untouched.
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


def _state(tab, old, tabs):
    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    return {
        "tab_count": int(tabs.count()),
        "current_index": int(tabs.currentIndex()),
        "current_class": tabs.currentWidget().metaObject().className() if tabs.currentWidget() is not None else "",
        "current_object_name": tabs.currentWidget().objectName() if tabs.currentWidget() is not None else "",
        "current_is_real_watchlist": bool(tabs.currentWidget() is tab),
        "current_is_old_placeholder": bool(tabs.currentWidget() is old),
        "watchlist": {
            "visible": bool(tab.isVisible()), "updates_enabled": bool(tab.updatesEnabled()), "geometry": _rect(tab),
        },
        "table": {
            "visible": bool(table.isVisible()) if table is not None else False,
            "updates_enabled": bool(table.updatesEnabled()) if table is not None else False,
            "geometry": _rect(table),
        },
        "viewport": {
            "visible": bool(viewport.isVisible()) if viewport is not None else False,
            "updates_enabled": bool(viewport.updatesEnabled()) if viewport is not None else False,
            "geometry": _rect(viewport),
        },
        "old_placeholder": (
            {"visible": bool(old.isVisible()), "enabled": bool(old.isEnabled()), "geometry": _rect(old), "parent": old.parent().metaObject().className() if old.parent() is not None else ""}
            if old is not None else None
        ),
    }


class _Observer(QObject):
    def __init__(self, app, workspace, tab, origin):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app, self.workspace, self.tab, self.origin = app, workspace, tab, origin
        self.QEvent = QEvent
        self.active = False
        self.count_since_turn = 0
        self.layouts, self.paints = [], []
        app.installEventFilter(self)

    def _in_branch(self, obj):
        node = obj
        for _ in range(12):
            if node is self.workspace:
                return True
            try:
                node = node.parent()
            except Exception:
                return False
            if node is None:
                return False
        return False

    def _name(self, obj):
        try:
            return f"{obj.metaObject().className()}#{obj.objectName()}"
        except Exception:
            return type(obj).__name__

    def eventFilter(self, obj, event):
        if not self.active:
            return False
        at = round((time.perf_counter() - self.origin) * 1000.0, 3)
        if event.type() == self.QEvent.Type.LayoutRequest and self._in_branch(obj):
            self.count_since_turn += 1
            self.layouts.append({"at_ms": at, "object": self._name(obj)})
        elif event.type() == self.QEvent.Type.Paint:
            table = getattr(self.tab, "table_sp", None)
            viewport = table.viewport() if table is not None else None
            if obj is table or obj is viewport:
                self.paints.append({"at_ms": at, "target": "table" if obj is table else "viewport"})
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: run_split_placeholder_reveal.py OUTPUT_DIR")
    output = Path(argv[0]).resolve(); output.mkdir(parents=True, exist_ok=True)
    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import ui.workspaces.classic_workspace as ws
    from ui.tabs.watchlist_tab import WatchlistTab

    origin = time.perf_counter()
    data = {"policy": {"max_elapsed_ms": 50.0, "required_quiet_turns": 3}, "timeline": [], "frozen_qtimers": []}
    state = {"workspace": None, "tab": None, "old": None, "index": None, "mount_perf": None, "mount_at": None, "reveal_at": None, "turns": 0, "quiet": 0}
    observer = {"value": None}
    orig_prepare, orig_replace, orig_finish = WatchlistTab.prepare_workspace_preload_reveal, ws._replace_workspace_placeholder, profile._NativeProfileController._finish

    def stamp(label, **extra):
        tabs = state["workspace"].tabs if state["workspace"] is not None else None
        data["timeline"].append({"at_ms": round((time.perf_counter()-origin)*1000.0,3), "label": label, "state": _state(state["tab"], state["old"], tabs) if tabs is not None else {}, **extra})

    def reveal_real_page():
        workspace, tab, old, index = state["workspace"], state["tab"], state["old"], state["index"]
        tabs = workspace.tabs
        stamp("before_reveal_transition")
        blocked = tabs.blockSignals(True)
        try:
            tabs.setCurrentIndex(index)
            # Old placeholder shifted to index+1 by insertTab.
            tabs.removeTab(index + 1)
        finally:
            tabs.blockSignals(blocked)
        tabs.tabBar().setUpdatesEnabled(True)
        state["reveal_at"] = round((time.perf_counter()-origin)*1000.0,3)
        observer["value"].active = False
        stamp("after_reveal_transition")
        if old is not None:
            old.deleteLater()
        data.setdefault("actions", []).append({"operation": "switch_to_real_then_remove_placeholder", "at_ms": state["reveal_at"]})

    def settle_turn():
        obs = observer["value"]
        state["turns"] += 1
        seen = obs.count_since_turn; obs.count_since_turn = 0
        state["quiet"] = 0 if seen else state["quiet"]+1
        elapsed = (time.perf_counter()-state["mount_perf"])*1000.0
        stamp("barrier_turn", turn=state["turns"], upstream_layout_requests=seen, quiet_turns=state["quiet"], elapsed_since_mount_ms=round(elapsed,3))
        if state["quiet"] >= 3 or elapsed >= 50.0:
            reveal_real_page(); return
        QTimer.singleShot(0, settle_turn)

    def prepare(tab, *args, **kwargs):
        if state["tab"] is None:
            state["tab"] = tab; state["workspace"] = getattr(tab.parentWidget(), "_workspace", None)
            root = state["workspace"].window()
            stamp("hidden_before")
            for timer in root.findChildren(QTimer):
                if timer.isActive():
                    data["frozen_qtimers"].append({"parent": type(timer.parent()).__name__ if timer.parent() is not None else "", "interval_ms": int(timer.interval())}); timer.stop()
            observer["value"] = _Observer(QApplication.instance(), state["workspace"], tab, origin)
        return orig_prepare(tab, *args, **kwargs)

    def split_replace(workspace, spec, key, index, widget, *, load_reason=""):
        if key != "watchlist" or widget is not state["tab"]:
            return orig_replace(workspace, spec, key, index, widget, load_reason=load_reason)
        # Same production insert/icon configuration.  Difference: retain old
        # page current; postpone only current-index + removeTab to reveal.
        ws._prepare_workspace_preload_repaint_guard(workspace, widget, load_reason)
        tabs = workspace.tabs; old = spec.get("page_widget") or spec.get("widget")
        was_current = tabs.currentWidget() is old
        blocked = tabs.blockSignals(True)
        try:
            tokens = workspace._workspace_icon_tokens
            tabs.tabBar().setUpdatesEnabled(False)  # old 11-tab chrome stays visually stable for <=50 ms.
            tabs.insertTab(index, widget, ws.tab_svg_icon(key=str(spec.get("icon_key") or key), label=spec.get("title", ""), color=tokens["muted"], size=tokens["chrome_size"], stroke_width=tokens["stroke_width"]), spec.get("title", ""))
            if was_current:
                tabs.setCurrentIndex(index + 1)  # old placeholder remains current.
        finally:
            tabs.blockSignals(blocked)
        spec["page_widget"], spec["mounted"] = widget, True
        state.update({"old": old, "index": index, "mount_perf": time.perf_counter(), "mount_at": round((time.perf_counter()-origin)*1000.0,3)})
        observer["value"].active = True
        stamp("real_inserted_placeholder_still_current")
        QTimer.singleShot(0, settle_turn)

    def finish(controller):
        stamp("before_profile_finish")
        return orig_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = prepare
    ws._replace_workspace_placeholder = split_replace
    profile._NativeProfileController._finish = finish
    try:
        args = profile._parse_args(["--background-prewarm", "--restore-last-tab", "--warmup-ms", "300", "--settle-ms", "1900", "--load-timeout-ms", "15000", "--heartbeat-ms", "25", "--output-dir", str(output), "--no-cprofile"])
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = orig_prepare; ws._replace_workspace_placeholder = orig_replace; profile._NativeProfileController._finish = orig_finish
    obs=observer["value"]
    if obs is not None:
        obs.close(); data["upstream_layout_requests"] = obs.layouts; data["vcp_paint_events_before_reveal"] = obs.paints
    runtime=report.get("watchlist",{}).get("repaint_runtime",{})
    data["result"]={"status":report.get("status"),"tab_count":report.get("background_prewarm",{}).get("tab_count"),"final":{"row_count":report.get("watchlist",{}).get("row_count"),"visible":report.get("watchlist",{}).get("visible"),"load_reason":report.get("watchlist",{}).get("workspace_load_reason"),"page_size":runtime.get("watchlist_page_size"),"table_size":runtime.get("table_size"),"viewport_size":runtime.get("viewport_size")},"paint_reasons":report.get("watchlist_reveal",{}).get("metrics",{}).get("paint",{}).get("reasons",[]),"paint_after_first":report.get("watchlist_reveal",{}).get("metrics",{}).get("paint",{}).get("after_first",{}),"paint_durations":report.get("watchlist_reveal",{}).get("metrics",{}).get("paint",{}).get("durations",{}),"reveal_acceptance":report.get("watchlist_reveal",{}).get("acceptance",{})}
    data["barrier"]={"turns":state["turns"],"quiet_turns":state["quiet"],"mount_at_ms":state["mount_at"],"reveal_at_ms":state["reveal_at"],"delay_ms":round(float(state["reveal_at"]-state["mount_at"]),3) if state["reveal_at"] is not None else None}
    (output/"split_placeholder_reveal_audit.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"result":data["result"],"barrier":data["barrier"]},ensure_ascii=False))
    return 0

if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
