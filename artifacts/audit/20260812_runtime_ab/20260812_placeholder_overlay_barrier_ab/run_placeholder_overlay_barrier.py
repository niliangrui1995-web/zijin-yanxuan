"""Native runtime test of an explicit no-blank Watchlist reveal barrier.

The real Watchlist completes the production tab replacement before it is
revealed.  The diagnostic modes either retain the old LazyTabPlaceholder as a
temporary overlay or capture it into a sibling QLabel pixmap overlay.  They do
not consume Paint events or change the table model/data path.
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
    value = widget.geometry()
    return [value.x(), value.y(), value.width(), value.height()]


def _state(tab, overlay, tabs):
    table = getattr(tab, "table_sp", None)
    viewport = table.viewport() if table is not None else None
    return {
        "tab_count": int(tabs.count()) if tabs is not None else None,
        "current_index": int(tabs.currentIndex()) if tabs is not None else None,
        "current_is_watchlist": bool(tabs.currentWidget() is tab) if tabs is not None else False,
        "tab": {
            "visible": bool(tab.isVisible()),
            "updates_enabled": bool(tab.updatesEnabled()),
            "geometry": _rect(tab),
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
        "placeholder_overlay": _overlay_state(overlay),
    }


def _overlay_state(overlay):
    if overlay is None:
        return {"exists": False}
    try:
        parent = overlay.parent()
        pixmap = overlay.pixmap() if hasattr(overlay, "pixmap") else None
        return {
            "exists": True,
            "visible": bool(overlay.isVisible()),
            "enabled": bool(overlay.isEnabled()),
            "geometry": _rect(overlay),
            "pixmap_size": [pixmap.width(), pixmap.height()] if pixmap is not None and not pixmap.isNull() else None,
            "parent_class": parent.metaObject().className() if parent is not None else "",
            "parent_object_name": parent.objectName() if parent is not None else "",
        }
    except RuntimeError:
        return {"exists": False, "deleted": True}


class _Observer(QObject):
    def __init__(self, app, workspace, tab, origin):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app = app
        self.workspace = workspace
        self.tab = tab
        self.QEvent = QEvent
        self.origin = origin
        self.active = False
        self.count_since_turn = 0
        self.layout_events = []
        self.vcp_paints = []
        self.on_first_viewport_paint = None
        app.installEventFilter(self)

    def _is_workspace_branch(self, obj):
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
            cls = obj.metaObject().className()
            name = obj.objectName()
            return f"{cls}#{name}" if name else str(cls)
        except Exception:
            return type(obj).__name__

    def eventFilter(self, obj, event):
        if not self.active:
            return False
        at_ms = round((time.perf_counter() - self.origin) * 1000.0, 3)
        if event.type() == self.QEvent.Type.LayoutRequest and self._is_workspace_branch(obj):
            self.count_since_turn += 1
            self.layout_events.append({"at_ms": at_ms, "object": self._name(obj)})
        elif event.type() == self.QEvent.Type.Paint:
            table = getattr(self.tab, "table_sp", None)
            viewport = table.viewport() if table is not None else None
            if obj is table or obj is viewport:
                self.vcp_paints.append({"at_ms": at_ms, "target": "table" if obj is table else "viewport"})
                if obj is viewport and self.on_first_viewport_paint is not None:
                    callback = self.on_first_viewport_paint
                    self.on_first_viewport_paint = None
                    callback()
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) not in {1, 2}:
        raise SystemExit("usage: run_placeholder_overlay_barrier.py OUTPUT_DIR [immediate|after_first_paint|pixmap_immediate|pixmap_after_first_paint|pixmap_after_paint_quiescent]")
    output_dir = Path(argv[0]).resolve()
    reveal_mode = argv[1] if len(argv) == 2 else "immediate"
    if reveal_mode not in {"immediate", "after_first_paint", "pixmap_immediate", "pixmap_after_first_paint", "pixmap_after_paint_quiescent"}:
        raise SystemExit(f"unsupported reveal mode: {reveal_mode}")
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtWidgets import QApplication, QLabel, QStackedWidget
    import ui.workspaces.classic_workspace as ws
    from ui.tabs.watchlist_tab import WatchlistTab

    origin = time.perf_counter()
    audit = {"reveal_mode": reveal_mode, "policy": {"max_elapsed_ms": 50.0, "required_quiet_turns": 3}, "timeline": [], "frozen_qtimers": []}
    state = {"tab": None, "workspace": None, "overlay": None, "overlay_kind": None, "mount_perf": None, "mount_at": None, "content_enable_at": None, "reenable_at": None, "turns": 0, "quiet": 0, "post_paint_turns": 0, "post_paint_quiet": 0, "first_viewport_paint_at": None}
    observer = {"value": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_replace = ws._replace_workspace_placeholder
    original_finish = profile._NativeProfileController._finish

    def stamp(label, **extra):
        tab = state["tab"]
        workspace = state["workspace"]
        tabs = getattr(workspace, "tabs", None)
        audit["timeline"].append({
            "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
            "label": label,
            "state": _state(tab, state["overlay"], tabs) if tab is not None else {},
            **extra,
        })

    def hide_overlay_after_content_paint():
        tab = state["tab"]
        overlay = state["overlay"]
        obs = observer["value"]
        stamp("before_overlay_hide_after_content_paint")
        if overlay is not None:
            overlay.hide()
        state["reenable_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
        if obs is not None:
            obs.active = False
        stamp("after_overlay_hide_after_content_paint")

        def dispose_overlay():
            # Diagnostic split: retain the hidden free child until window
            # teardown.  This tells whether its DeferredDelete is the source
            # of the residual backing-store wave.
            stamp("overlay_hidden_retained_until_window_teardown")

        QTimer.singleShot(0, dispose_overlay)

    def settle_after_first_content_paint():
        """Do not uncover until post-first-paint layout propagation settles."""
        tab = state["tab"]
        obs = observer["value"]
        if tab is None or obs is None or state["reenable_at"] is not None:
            return
        state["post_paint_turns"] += 1
        seen = obs.count_since_turn
        obs.count_since_turn = 0
        state["post_paint_quiet"] = 0 if seen else state["post_paint_quiet"] + 1
        elapsed = (time.perf_counter() - state["mount_perf"]) * 1000.0
        stamp(
            "post_first_paint_barrier_turn",
            turn=state["post_paint_turns"],
            upstream_layout_requests=seen,
            quiet_turns=state["post_paint_quiet"],
            elapsed_since_mount_ms=round(elapsed, 3),
        )
        if state["post_paint_quiet"] >= 3 or elapsed >= 500.0:
            hide_overlay_after_content_paint()
            return
        QTimer.singleShot(0, settle_after_first_content_paint)

    def after_first_viewport_paint():
        if state["first_viewport_paint_at"] is not None:
            return
        state["first_viewport_paint_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
        stamp("first_viewport_paint_under_overlay")
        # Separate the initial paint event from its queued LayoutRequests.
        QTimer.singleShot(0, settle_after_first_content_paint)

    def finish_reveal():
        tab = state["tab"]
        overlay = state["overlay"]
        obs = observer["value"]
        if reveal_mode in {"after_first_paint", "pixmap_after_first_paint", "pixmap_after_paint_quiescent"}:
            stamp("enable_content_updates_under_placeholder")
            tab.setUpdatesEnabled(True)
            state["content_enable_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
            if obs is not None:
                if reveal_mode == "pixmap_after_paint_quiescent":
                    obs.on_first_viewport_paint = after_first_viewport_paint
                else:
                    obs.on_first_viewport_paint = lambda: QTimer.singleShot(0, hide_overlay_after_content_paint)
            # Safety only: a failed paint must never leave the real page
            # masked indefinitely. Normal path unblocks from first viewport Paint.
            QTimer.singleShot(1000, lambda: (
                hide_overlay_after_content_paint()
                if state["reenable_at"] is None
                else None
            ))
            return
        stamp("before_atomic_reveal")
        if overlay is not None:
            overlay.hide()
        tab.setUpdatesEnabled(True)
        state["reenable_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
        if obs is not None:
            obs.active = False
        stamp("after_atomic_reveal")

        def dispose_overlay():
            stamp("overlay_hidden_retained_until_window_teardown")

        QTimer.singleShot(0, dispose_overlay)

    def settle_turn():
        obs = observer["value"]
        if obs is None:
            return
        state["turns"] += 1
        seen = obs.count_since_turn
        obs.count_since_turn = 0
        state["quiet"] = 0 if seen else state["quiet"] + 1
        elapsed = (time.perf_counter() - state["mount_perf"]) * 1000.0
        stamp("barrier_turn", turn=state["turns"], upstream_layout_requests=seen, quiet_turns=state["quiet"], elapsed_since_mount_ms=round(elapsed, 3))
        if state["quiet"] >= 3 or elapsed >= 50.0:
            finish_reveal()
            return
        QTimer.singleShot(0, settle_turn)

    def wrapped_prepare(tab, *args, **kwargs):
        if state["tab"] is None:
            state["tab"] = tab
            state["workspace"] = getattr(tab.parentWidget(), "_workspace", None)
            workspace = state["workspace"]
            root = workspace.window() if workspace is not None else None
            stamp("hidden_before")
            if root is not None:
                for timer in root.findChildren(QTimer):
                    if timer.isActive():
                        audit["frozen_qtimers"].append({"parent": type(timer.parent()).__name__ if timer.parent() is not None else "", "interval_ms": int(timer.interval())})
                        timer.stop()
            tab.setUpdatesEnabled(False)
            observer["value"] = _Observer(QApplication.instance(), workspace, tab, origin)
            stamp("hidden_updates_disabled")
        return original_prepare(tab, *args, **kwargs)

    def replacement_with_placeholder_overlay(workspace, spec, key, index, widget, *, load_reason=""):
        if key != "watchlist" or widget is not state["tab"]:
            return original_replace(workspace, spec, key, index, widget, load_reason=load_reason)
        # Exact source mount sequence, except ownership of the just-removed
        # placeholder is retained temporarily as an overlay rather than being
        # deleteLater()'d before the queue settles.
        ws._prepare_workspace_preload_repaint_guard(workspace, widget, load_reason)
        tabs = workspace.tabs
        current_index = tabs.currentIndex()
        blocked = tabs.blockSignals(True)
        old = spec.get("page_widget") or spec.get("widget")
        placeholder_pixmap = old.grab() if reveal_mode.startswith("pixmap_") and old is not None else None
        if placeholder_pixmap is not None:
            audit["placeholder_snapshot"] = {
                "null": bool(placeholder_pixmap.isNull()),
                "size": [placeholder_pixmap.width(), placeholder_pixmap.height()],
            }
        try:
            tokens = workspace._workspace_icon_tokens
            tabs.insertTab(
                index,
                widget,
                ws.tab_svg_icon(
                    key=str(spec.get("icon_key") or key),
                    label=spec.get("title", ""),
                    color=tokens["muted"],
                    size=tokens["chrome_size"],
                    stroke_width=tokens["stroke_width"],
                ),
                spec.get("title", ""),
            )
            if old is not None and tabs.currentWidget() is old:
                tabs.setCurrentIndex(index)
            tabs.removeTab(index + 1)
            if old is not tabs.currentWidget() and 0 <= current_index < tabs.count():
                tabs.setCurrentIndex(index if current_index == index else current_index)
        finally:
            tabs.blockSignals(blocked)
        spec["page_widget"] = widget
        spec["mounted"] = True
        if old is not None and old is not widget and reveal_mode.startswith("pixmap_"):
            # Keep the exact production ownership/lifetime of the page.  Only
            # its already-rendered image becomes a stack sibling cover.
            stack = tabs.findChild(QStackedWidget, "qt_tabwidget_stackedwidget")
            if stack is None:
                stack = widget.parentWidget()
            overlay = QLabel(stack)
            overlay.setObjectName("runtime_watchlist_placeholder_pixmap_overlay")
            overlay.setPixmap(placeholder_pixmap)
            overlay.setGeometry(widget.geometry())
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            overlay.show()
            overlay.raise_()
            old.deleteLater()
            state["overlay"] = overlay
            state["overlay_kind"] = "placeholder_pixmap"
        elif old is not None and old is not widget:
            # QTabWidget has removed it from the page list.  Reparent as a
            # non-page child of the same stack, exactly over the new real page.
            stack = tabs.findChild(QStackedWidget, "qt_tabwidget_stackedwidget")
            if stack is None:
                stack = widget.parentWidget()
            old.setParent(stack)
            old.setGeometry(widget.geometry())
            old.setEnabled(False)
            old.show()
            old.raise_()
            state["overlay"] = old
            state["overlay_kind"] = "placeholder_widget"
        state["mount_perf"] = time.perf_counter()
        state["mount_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
        observer["value"].active = True
        stamp("production_mount_with_placeholder_overlay")
        QTimer.singleShot(0, settle_turn)

    def wrapped_finish(controller):
        stamp("before_profile_finish")
        return original_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    ws._replace_workspace_placeholder = replacement_with_placeholder_overlay
    profile._NativeProfileController._finish = wrapped_finish
    try:
        args = profile._parse_args([
            "--background-prewarm", "--restore-last-tab", "--warmup-ms", "300", "--settle-ms", "1900", "--load-timeout-ms", "15000", "--heartbeat-ms", "25", "--output-dir", str(output_dir), "--no-cprofile",
        ])
        report, _ = profile.run_profile(args)
    finally:
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare
        ws._replace_workspace_placeholder = original_replace
        profile._NativeProfileController._finish = original_finish
    obs = observer["value"]
    if obs is not None:
        obs.close()
        audit["upstream_layout_requests"] = obs.layout_events
        audit["vcp_paint_events_while_barrier_active"] = obs.vcp_paints
    runtime = report.get("watchlist", {}).get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "final": {"row_count": report.get("watchlist", {}).get("row_count"), "visible": report.get("watchlist", {}).get("visible"), "load_reason": report.get("watchlist", {}).get("workspace_load_reason"), "page_size": runtime.get("watchlist_page_size"), "table_size": runtime.get("table_size"), "viewport_size": runtime.get("viewport_size")},
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    audit["barrier"] = {"turns": state["turns"], "quiet_turns": state["quiet"], "post_paint_turns": state["post_paint_turns"], "post_paint_quiet_turns": state["post_paint_quiet"], "first_viewport_paint_at_ms": state["first_viewport_paint_at"], "mount_at_ms": state["mount_at"], "content_enable_at_ms": state["content_enable_at"], "reveal_at_ms": state["reenable_at"], "content_enable_delay_ms": round(float(state["content_enable_at"] - state["mount_at"]), 3) if state["content_enable_at"] is not None else None, "delay_ms": round(float(state["reenable_at"] - state["mount_at"]), 3) if state["reenable_at"] is not None else None}
    (output_dir / "placeholder_overlay_barrier_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": audit["result"], "barrier": audit["barrier"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
