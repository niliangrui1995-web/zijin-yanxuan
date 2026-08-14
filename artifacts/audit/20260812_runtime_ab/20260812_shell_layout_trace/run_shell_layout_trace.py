"""Runtime-only native trace for the Watchlist reveal outer-layout invalidation chain."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(r"D:\vcp_hunter\紫金研选")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ptr(obj):
    try:
        from PyQt6 import sip

        return int(sip.unwrapinstance(obj))
    except Exception:
        return id(obj)


def _rect(value):
    if value is None:
        return None
    return [int(value.x()), int(value.y()), int(value.width()), int(value.height())]


class ShellLayoutTrace:
    def __init__(self, app, window, tab):
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtWidgets import QLayout, QWidget

        self.app = app
        self.window = window
        self.tab = tab
        self.QEvent = QEvent
        self.QObject = QObject
        self.QWidget = QWidget
        self.QLayout = QLayout
        self.origin = time.perf_counter()
        self.tracked = {}
        self.last_state = {}
        self.events = []
        self.first_table_paint_at = None
        self.table_paint_count = 0
        self.enabled = True
        self._register_tree()
        self.app.installEventFilter(self)
        self._snapshot_all("trace_attached")

    def _desc(self, obj):
        if obj is None:
            return "<none>"
        try:
            class_name = obj.metaObject().className()
        except Exception:
            class_name = type(obj).__name__
        try:
            name = str(obj.objectName() or "")
        except Exception:
            name = ""
        return f"{class_name}#{name}" if name else str(class_name)

    def _parent_desc(self, obj):
        try:
            return self._desc(obj.parent())
        except Exception:
            return ""

    def _register(self, obj):
        if obj is None:
            return
        key = _ptr(obj)
        if key not in self.tracked:
            self.tracked[key] = {"obj": obj, "desc": self._desc(obj), "parent": self._parent_desc(obj)}

    def _register_tree(self):
        for obj in [self.window, self.tab]:
            self._register(obj)
            try:
                for child in obj.findChildren(self.QWidget):
                    self._register(child)
            except Exception:
                pass
            try:
                for layout in obj.findChildren(self.QLayout):
                    self._register(layout)
            except Exception:
                pass
        table = getattr(self.tab, "table_sp", None)
        for obj in [
            table,
            table.viewport() if table is not None else None,
            table.horizontalHeader() if table is not None else None,
            table.verticalHeader() if table is not None else None,
            table.horizontalScrollBar() if table is not None else None,
            table.verticalScrollBar() if table is not None else None,
            getattr(self.window, "_custom_titlebar", None),
            getattr(self.window, "tabs_wrapper", None),
            getattr(self.window, "_status_bar_widget", None),
            getattr(self.window, "centralWidget", lambda: None)(),
        ]:
            self._register(obj)
            try:
                self._register(obj.layout())
            except Exception:
                pass

    def _state(self, obj):
        if not isinstance(obj, self.QWidget):
            if isinstance(obj, self.QLayout):
                margins = obj.contentsMargins()
                return {
                    "kind": "layout",
                    "class": self._desc(obj),
                    "geometry": _rect(obj.geometry()),
                    "margins": [margins.left(), margins.top(), margins.right(), margins.bottom()],
                    "spacing": int(obj.spacing()),
                    "count": int(obj.count()),
                    "enabled": bool(obj.isEnabled()),
                    "size_constraint": str(obj.sizeConstraint()),
                    "minimum_size": [int(obj.minimumSize().width()), int(obj.minimumSize().height())],
                    "size_hint": [int(obj.sizeHint().width()), int(obj.sizeHint().height())],
                }
            return {"kind": "object", "class": self._desc(obj)}
        margins = obj.contentsMargins()
        policy = obj.sizePolicy()
        layout = obj.layout()
        layout_state = None
        if layout is not None:
            layout_margins = layout.contentsMargins()
            layout_state = {
                "class": self._desc(layout),
                "geometry": _rect(layout.geometry()),
                "margins": [
                    layout_margins.left(), layout_margins.top(), layout_margins.right(), layout_margins.bottom()
                ],
                "spacing": int(layout.spacing()),
                "count": int(layout.count()),
                "enabled": bool(layout.isEnabled()),
                "size_constraint": str(layout.sizeConstraint()),
                "minimum_size": [int(layout.minimumSize().width()), int(layout.minimumSize().height())],
                "size_hint": [int(layout.sizeHint().width()), int(layout.sizeHint().height())],
            }
        return {
            "kind": "widget",
            "class": self._desc(obj),
            "parent": self._parent_desc(obj),
            "geometry": _rect(obj.geometry()),
            "contents_rect": _rect(obj.contentsRect()),
            "contents_margins": [margins.left(), margins.top(), margins.right(), margins.bottom()],
            "visible": bool(obj.isVisible()),
            "is_window": bool(obj.isWindow()),
            "minimum_size": [int(obj.minimumSize().width()), int(obj.minimumSize().height())],
            "maximum_size": [int(obj.maximumSize().width()), int(obj.maximumSize().height())],
            "minimum_size_hint": [int(obj.minimumSizeHint().width()), int(obj.minimumSizeHint().height())],
            "size_hint": [int(obj.sizeHint().width()), int(obj.sizeHint().height())],
            "size_policy": [str(policy.horizontalPolicy()), str(policy.verticalPolicy())],
            "layout": layout_state,
        }

    @staticmethod
    def _diff(before, after):
        if before is None:
            return {"initial": after}
        changed = {}
        for key in set(before) | set(after):
            if before.get(key) != after.get(key):
                changed[key] = {"before": before.get(key), "after": after.get(key)}
        return changed

    def _snapshot(self, obj, reason):
        key = _ptr(obj)
        if key not in self.tracked:
            return {}
        try:
            state = self._state(obj)
        except Exception as exc:
            return {"state_error": str(exc)}
        delta = self._diff(self.last_state.get(key), state)
        self.last_state[key] = state
        return delta

    def _snapshot_all(self, reason):
        rows = []
        for item in list(self.tracked.values()):
            delta = self._snapshot(item["obj"], reason)
            if delta:
                rows.append({"object": item["desc"], "delta": delta})
        return rows

    def _event_name(self, event):
        try:
            return event.type().name
        except Exception:
            return str(int(event.type()))

    def _event_child(self, event):
        try:
            return self._desc(event.child())
        except Exception:
            return ""

    def _is_table_paint_target(self, obj):
        table = getattr(self.tab, "table_sp", None)
        if table is None:
            return False
        try:
            return obj is table or obj is table.viewport()
        except Exception:
            return False

    def eventFilter(self, obj, event):
        if not self.enabled:
            return False
        type_value = event.type()
        relevant = {
            self.QEvent.Type.LayoutRequest,
            self.QEvent.Type.UpdateLater,
            self.QEvent.Type.UpdateRequest,
            self.QEvent.Type.Resize,
            self.QEvent.Type.Move,
            self.QEvent.Type.Show,
            self.QEvent.Type.Hide,
            self.QEvent.Type.Paint,
            self.QEvent.Type.StyleChange,
            self.QEvent.Type.ChildPolished,
            self.QEvent.Type.ChildAdded,
            self.QEvent.Type.ChildRemoved,
            self.QEvent.Type.ParentChange,
            self.QEvent.Type.Polish,
            self.QEvent.Type.PolishRequest,
            self.QEvent.Type.ContentsRectChange,
            self.QEvent.Type.DynamicPropertyChange,
            self.QEvent.Type.FontChange,
            self.QEvent.Type.PaletteChange,
            self.QEvent.Type.ZOrderChange,
            self.QEvent.Type.DeferredDelete,
        }
        if type_value not in relevant:
            return False
        tracked = _ptr(obj) in self.tracked
        table_paint = type_value == self.QEvent.Type.Paint and self._is_table_paint_target(obj)
        # Paint events in the large outer tree would drown structural events;
        # retain only VCP table/viewport paints as phase boundaries.
        if type_value == self.QEvent.Type.Paint and not table_paint:
            return False
        if not tracked:
            return False
        if table_paint:
            self.table_paint_count += 1
            if self.first_table_paint_at is None:
                self.first_table_paint_at = time.perf_counter()
        phase = "before_first_vcp" if self.first_table_paint_at is None else f"after_vcp_paint_{self.table_paint_count}"
        delta = self._snapshot(obj, self._event_name(event))
        row = {
            "at_ms": round((time.perf_counter() - self.origin) * 1000.0, 3),
            "phase": phase,
            "event": self._event_name(event),
            "object": self._desc(obj),
            "parent": self._parent_desc(obj),
            "delta": delta,
        }
        child = self._event_child(event)
        if child:
            row["child"] = child
        self.events.append(row)
        return False

    def close(self):
        self.enabled = False
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def _summarize(trace):
    layout_events = [row for row in trace.events if row["event"] == "LayoutRequest"]
    after_first = [
        row for row in layout_events if trace.first_table_paint_at is not None and row["phase"] != "before_first_vcp"
    ]
    first_tabs_wrapper = next((row for row in after_first if "tabsWrapperFrame" in row["object"]), None)
    context = []
    if first_tabs_wrapper is not None:
        index = trace.events.index(first_tabs_wrapper)
        context = trace.events[max(0, index - 14) : index + 8]
    outer_names = ("mainWindowFrame", "customTitleBar", "tabsWrapperFrame", "statusBarWidget", "MainWindowQT")
    outer_layouts = [row for row in after_first if any(name in row["object"] for name in outer_names)]
    meaningful_deltas = [
        row for row in trace.events
        if row["event"] in {"StyleChange", "ChildPolished", "ChildAdded", "ChildRemoved", "ParentChange", "Resize", "Move", "Show", "Hide"}
        and row["delta"]
    ]
    return {
        "tracked_count": len(trace.tracked),
        "vcp_table_paint_events_seen": trace.table_paint_count,
        "event_count": len(trace.events),
        "first_layout_requests_after_first_vcp": after_first[:18],
        "first_outer_layout_requests_after_first_vcp": outer_layouts[:18],
        "first_tabs_wrapper_layout_request_context": context,
        "meaningful_outer_state_events": [
            row for row in meaningful_deltas if any(name in row["object"] for name in outer_names)
        ][:80],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: run_shell_layout_trace.py OUTPUT_DIR")
    output_dir = Path(argv[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from ui.tabs.watchlist_tab import WatchlistTab

    state = {"trace": None, "frozen": []}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal

    def wrapped_prepare(tab, *args, **kwargs):
        if state["trace"] is None:
            workspace = getattr(tab.parentWidget(), "_workspace", None)
            root = workspace.window() if workspace is not None else None
            state["trace"] = ShellLayoutTrace(profile.QApplication.instance(), root, tab)
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        state["frozen"].append(
                            {
                                "parent": type(timer.parent()).__name__ if timer.parent() is not None else "",
                                "object_name": timer.objectName(),
                                "interval_ms": int(timer.interval()),
                            }
                        )
                        timer.stop()
        return original_prepare(tab, *args, **kwargs)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
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
    trace = state["trace"]
    if trace is not None:
        trace.close()
        trace_data = {"events": trace.events, "summary": _summarize(trace)}
    else:
        trace_data = {"events": [], "summary": {"error": "trace never attached"}}
    audit = {
        "frozen_qtimers": state["frozen"],
        "result": {
            "tab_count": report.get("background_prewarm", {}).get("tab_count"),
            "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
            "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
            "watchlist_runtime": report.get("watchlist", {}).get("repaint_runtime", {}),
        },
        "trace": trace_data,
    }
    (output_dir / "shell_layout_trace.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit["trace"]["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
