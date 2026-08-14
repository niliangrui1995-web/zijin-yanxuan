"""Native A/B for QAbstractScrollArea viewport background ownership.

Modes are deliberately narrow:
* ``control``: leave the real Watchlist viewport unchanged.
* ``base``: set only ``autoFillBackground=True`` and ``backgroundRole=Base``.
* ``base_styled``: the Base variant plus ``WA_StyledBackground`` (reserved for
  a separately justified follow-up; it never sets WA_OpaquePaintEvent).

No source file is modified.  No updates are disabled and no Paint event is
consumed.  A terminal screen capture is recorded solely to verify the visible
viewport after the profiler's normal settle window.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from copy import deepcopy
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


def _color_name(color):
    try:
        return color.name(color.NameFormat.HexArgb)
    except Exception:
        return ""


def _palette_state(viewport, palette_type, qt_type):
    if viewport is None:
        return {"exists": False}
    palette = viewport.palette()
    base_role = palette_type.ColorRole.Base
    window_role = palette_type.ColorRole.Window
    active = palette_type.ColorGroup.Active
    inactive = palette_type.ColorGroup.Inactive
    disabled = palette_type.ColorGroup.Disabled
    return {
        "exists": True,
        "visible": bool(viewport.isVisible()),
        "updates_enabled": bool(viewport.updatesEnabled()),
        "geometry": _rect(viewport),
        "auto_fill_background": bool(viewport.autoFillBackground()),
        "background_role": viewport.backgroundRole().name,
        "foreground_role": viewport.foregroundRole().name,
        "base_colors": {
            "active": _color_name(palette.color(active, base_role)),
            "inactive": _color_name(palette.color(inactive, base_role)),
            "disabled": _color_name(palette.color(disabled, base_role)),
        },
        "window_colors": {
            "active": _color_name(palette.color(active, window_role)),
            "inactive": _color_name(palette.color(inactive, window_role)),
            "disabled": _color_name(palette.color(disabled, window_role)),
        },
        "wa_opaque_paint_event": bool(viewport.testAttribute(qt_type.WidgetAttribute.WA_OpaquePaintEvent)),
        "wa_styled_background": bool(viewport.testAttribute(qt_type.WidgetAttribute.WA_StyledBackground)),
    }


class _CapturePaintObserver(QObject):
    """Prove the terminal screenshot itself did not enqueue a VCP Paint."""

    def __init__(self, app, tab):
        from PyQt6.QtCore import QEvent

        super().__init__()
        self.app = app
        self.tab = tab
        self.QEvent = QEvent
        self.events = []
        self.active = False
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if not self.active or event.type() != self.QEvent.Type.Paint:
            return False
        table = getattr(self.tab, "table_sp", None)
        viewport = table.viewport() if table is not None else None
        if obj is table or obj is viewport:
            self.events.append("table" if obj is table else "viewport")
        return False

    def close(self):
        try:
            self.app.removeEventFilter(self)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        raise SystemExit("usage: run_viewport_background_ab.py OUTPUT_DIR control|base|base_styled [theme_readback]")
    output_dir = Path(argv[0]).resolve()
    mode = str(argv[1]).strip()
    theme_readback = len(argv) == 3 and str(argv[2]).strip() == "theme_readback"
    if len(argv) == 3 and not theme_readback:
        raise SystemExit("unsupported optional mode; expected theme_readback")
    if mode not in {"control", "base", "base_styled"}:
        raise SystemExit(f"unsupported mode: {mode}")
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QPoint, QTimer, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication
    import ui.workspaces.classic_workspace as workspace_module
    from ui.tabs.watchlist_tab import WatchlistTab

    origin = time.perf_counter()
    audit = {"mode": mode, "theme_readback": theme_readback, "timeline": [], "viewport": {}, "terminal_capture": {}}
    state = {"tab": None, "viewport": None, "paint_observer": None, "theme_phase": "off", "controller": None}
    original_prepare = WatchlistTab.prepare_workspace_preload_reveal
    original_replace = workspace_module._replace_workspace_placeholder
    original_finish = profile._NativeProfileController._finish

    def stamp(label, **extra):
        audit["timeline"].append(
            {
                "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
                "label": label,
                "viewport": _palette_state(state["viewport"], QPalette, Qt),
                **extra,
            }
        )

    def wrapped_prepare(tab, *args, **kwargs):
        result = original_prepare(tab, *args, **kwargs)
        if state["tab"] is not None:
            return result
        table = getattr(tab, "table_sp", None)
        viewport = table.viewport() if table is not None else None
        state["tab"] = tab
        state["viewport"] = viewport
        state["paint_observer"] = _CapturePaintObserver(QApplication.instance(), tab)
        audit["viewport"]["before_mount"] = _palette_state(viewport, QPalette, Qt)
        stamp("before_viewport_background_variant")
        return result

    def apply_variant():
        viewport = state["viewport"]
        audit["viewport"]["before_apply"] = _palette_state(viewport, QPalette, Qt)
        stamp("before_viewport_background_variant_apply")
        if viewport is not None and mode in {"base", "base_styled"}:
            viewport.setAutoFillBackground(True)
            viewport.setBackgroundRole(QPalette.ColorRole.Base)
            if mode == "base_styled":
                viewport.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        audit["viewport"]["after_apply"] = _palette_state(viewport, QPalette, Qt)
        stamp("after_viewport_background_variant_apply")

    def wrapped_replace(workspace, spec, key, index, widget, *, load_reason=""):
        result = original_replace(workspace, spec, key, index, widget, load_reason=load_reason)
        if key == "watchlist" and widget is state["tab"]:
            apply_variant()
        return result

    def capture_viewport(label):
        tab = state["tab"]
        viewport = state["viewport"]
        observer = state["paint_observer"]
        if tab is None or viewport is None or observer is None:
            return {"status": "skipped_no_viewport"}
        before = _palette_state(viewport, QPalette, Qt)
        stamp(f"before_{label}_screen_capture")
        observer.events.clear()
        captured = {"status": "capture_failed", "path": "", "width": 0, "height": 0, "device_pixel_ratio": 0.0, "sha256": ""}
        observer.active = True
        try:
            screen = QApplication.primaryScreen()
            top_left = viewport.mapToGlobal(QPoint(0, 0))
            pixmap = screen.grabWindow(0, top_left.x(), top_left.y(), viewport.width(), viewport.height()) if screen is not None else None
            image_path = output_dir / f"{label}_viewport_visual.png"
            saved = bool(pixmap is not None and not pixmap.isNull() and pixmap.save(str(image_path), "PNG"))
            captured = {
                "status": "saved" if saved else "capture_failed",
                "path": str(image_path) if saved else "",
                "width": int(pixmap.width()) if pixmap is not None else 0,
                "height": int(pixmap.height()) if pixmap is not None else 0,
                "device_pixel_ratio": float(pixmap.devicePixelRatio()) if pixmap is not None else 0.0,
                "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest() if saved else "",
            }
        finally:
            observer.active = False
        captured["vcp_paint_events_during_capture"] = list(observer.events)
        captured["before"] = before
        captured["after"] = _palette_state(viewport, QPalette, Qt)
        stamp(f"after_{label}_screen_capture")
        return captured

    def terminal_capture(controller):
        captured = capture_viewport("final")
        audit["terminal_capture"] = captured
        audit["viewport"]["before_capture"] = captured.get("before", {})
        audit["viewport"]["after_capture"] = captured.get("after", {})

    def finish_after_theme_restore(controller):
        if state["theme_phase"] != "restore_pending":
            return
        theme_state = audit.setdefault("theme_readback_result", {})
        theme_state["after_restore"] = _palette_state(state["viewport"], QPalette, Qt)
        theme_state["restored_capture"] = capture_viewport("theme_restored")
        state["theme_phase"] = "done"
        terminal_capture(controller)
        original_finish(controller)

    def inspect_alternate_theme(controller, manager, original_name):
        if state["theme_phase"] != "alternate_pending":
            return
        theme_state = audit.setdefault("theme_readback_result", {})
        theme_state["after_alternate"] = _palette_state(state["viewport"], QPalette, Qt)
        theme_state["alternate_capture"] = capture_viewport("theme_alternate")
        manager.switch_theme(original_name)
        theme_state["restore_requested_name"] = original_name
        state["theme_phase"] = "restore_pending"
        QTimer.singleShot(0, lambda: finish_after_theme_restore(controller))

    def start_theme_readback(controller):
        from ui.theme import theme_manager

        original_name = str(theme_manager.current_theme_name)
        alternatives = [name for name in theme_manager.theme_names() if str(name) != original_name]
        theme_state = audit.setdefault("theme_readback_result", {})
        theme_state["pre_theme_acceptance"] = deepcopy(controller.report.get("watchlist_reveal", {}))
        theme_state["original_name"] = original_name
        if not alternatives:
            theme_state["status"] = "skipped_no_alternate_theme"
            state["theme_phase"] = "done"
            terminal_capture(controller)
            original_finish(controller)
            return
        alternate_name = str(alternatives[0])
        theme_state["alternate_requested_name"] = alternate_name
        state["theme_phase"] = "alternate_pending"
        manager = theme_manager
        manager.switch_theme(alternate_name)
        QTimer.singleShot(0, lambda: inspect_alternate_theme(controller, manager, original_name))

    def wrapped_finish(controller):
        if theme_readback and state["theme_phase"] == "off":
            state["controller"] = controller
            start_theme_readback(controller)
            return
        if theme_readback and state["theme_phase"] in {"alternate_pending", "restore_pending"}:
            return
        terminal_capture(controller)
        return original_finish(controller)

    WatchlistTab.prepare_workspace_preload_reveal = wrapped_prepare
    workspace_module._replace_workspace_placeholder = wrapped_replace
    profile._NativeProfileController._finish = wrapped_finish
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
        WatchlistTab.prepare_workspace_preload_reveal = original_prepare
        workspace_module._replace_workspace_placeholder = original_replace
        profile._NativeProfileController._finish = original_finish

    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "final": {
            "row_count": report.get("watchlist", {}).get("row_count"),
            "visible": report.get("watchlist", {}).get("visible"),
            "load_reason": report.get("watchlist", {}).get("workspace_load_reason"),
            "runtime": report.get("watchlist", {}).get("repaint_runtime", {}),
        },
        "paint_reasons": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("reasons", []),
        "paint_after_first": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("after_first", {}),
        "paint_durations": report.get("watchlist_reveal", {}).get("metrics", {}).get("paint", {}).get("durations", {}),
        "reveal_acceptance": report.get("watchlist_reveal", {}).get("acceptance", {}),
    }
    (output_dir / "viewport_background_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    observer = state["paint_observer"]
    if observer is not None:
        observer.close()
    print(json.dumps({"result": audit["result"], "viewport": audit["viewport"], "terminal_capture": audit["terminal_capture"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
