"""Runtime A/B: restore Watchlist after post-paint stages vs normal competition."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(r"D:\vcp_hunter\紫金研选")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _flags(window):
    names = (
        "_post_paint_native_effects_applied",
        "_post_paint_data_provider_initialized",
        "_post_paint_startup_orchestrator_initialized",
        "_post_paint_scan_engine_initialized",
        "_post_paint_central_quotes_initialized",
        "_post_paint_tab_activation_finished",
        "_post_paint_f5_retention_scheduled",
        "_post_paint_auto_refresh_initialized",
        "_post_paint_startup_scheduled",
        "_post_paint_scheduler_started",
        "_post_paint_kline_prewarm_scheduled",
        "_post_paint_runtime_started",
    )
    return {name: bool(getattr(window, name, False)) for name in names}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: run_postpaint_order_ab.py MODE OUTPUT_DIR")
    mode, output_text = argv
    if mode not in {"normal", "after_postpaint"}:
        raise SystemExit(f"unsupported mode: {mode}")
    output_dir = Path(output_text).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    import scripts.native_watchlist_profile as profile
    from PyQt6.QtCore import QTimer
    from ui.main_window_qt import MainWindowQT

    origin = time.perf_counter()
    audit = {"mode": mode, "timeline": [], "frozen_qtimers": []}
    original_activate = profile._NativeProfileController._activate_watchlist
    original_postpaint = MainWindowQT._start_post_paint_runtime
    original_poll = profile._NativeProfileController._poll_watchlist_loaded
    state = {"activation_requested": False, "postpaint_calls": 0, "postpaint_complete_at": None}

    def stamp(label, window, **more):
        audit["timeline"].append(
            {
                "at_ms": round((time.perf_counter() - origin) * 1000.0, 3),
                "label": label,
                "postpaint_flags": _flags(window),
                **more,
            }
        )

    def wrapped_postpaint(window):
        state["postpaint_calls"] += 1
        stamp("postpaint_enter", window, call=state["postpaint_calls"])
        result = original_postpaint(window)
        stamp("postpaint_exit", window, call=state["postpaint_calls"])
        if bool(getattr(window, "_post_paint_runtime_started", False)) and state["postpaint_complete_at"] is None:
            state["postpaint_complete_at"] = round((time.perf_counter() - origin) * 1000.0, 3)
            stamp("postpaint_complete", window)
        return result

    def request_activation_when_postpaint_done(controller):
        window = controller.window
        if state["activation_requested"]:
            return
        if bool(getattr(window, "_post_paint_runtime_started", False)):
            state["activation_requested"] = True
            stamp("restore_activate_after_postpaint", window)
            original_activate(controller)
            return
        QTimer.singleShot(0, lambda: request_activation_when_postpaint_done(controller))

    def wrapped_activate(controller):
        window = controller.window
        stamp("profile_restore_requested", window)
        # Do not freeze the post-paint timer. Instead let the exact production
        # staged sequence finish, then use the same restore_last_tab call.
        if mode == "after_postpaint":
            request_activation_when_postpaint_done(controller)
            return
        state["activation_requested"] = True
        return original_activate(controller)

    def wrapped_poll(controller):
        before = bool(getattr(controller.window, "_post_paint_runtime_started", False))
        result = original_poll(controller)
        if not audit.get("first_loaded_recorded"):
            workspace = getattr(controller.window, "_workspace", None)
            tab = workspace.get_loaded_tab("watchlist") if workspace is not None else None
            if tab is not None:
                audit["first_loaded_recorded"] = True
                stamp(
                    "watchlist_loaded",
                    controller.window,
                    visible=bool(tab.isVisible()),
                    load_reason=str(getattr(tab, "_workspace_load_reason", "")),
                )
        return result

    MainWindowQT._start_post_paint_runtime = wrapped_postpaint
    profile._NativeProfileController._activate_watchlist = wrapped_activate
    profile._NativeProfileController._poll_watchlist_loaded = wrapped_poll
    try:
        args = profile._parse_args(
            [
                "--background-prewarm",
                "--restore-last-tab",
                "--warmup-ms", "0",
                "--settle-ms", "1800",
                "--load-timeout-ms", "15000",
                "--heartbeat-ms", "25",
                "--output-dir", str(output_dir),
                "--no-cprofile",
            ]
        )
        report, _ = profile.run_profile(args)
    finally:
        MainWindowQT._start_post_paint_runtime = original_postpaint
        profile._NativeProfileController._activate_watchlist = original_activate
        profile._NativeProfileController._poll_watchlist_loaded = original_poll

    runtime = report.get("watchlist", {}).get("repaint_runtime", {})
    audit["result"] = {
        "status": report.get("status"),
        "tab_count": report.get("background_prewarm", {}).get("tab_count"),
        "watchlist": {
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
    audit["postpaint_call_count"] = state["postpaint_calls"]
    audit["postpaint_complete_at_ms"] = state["postpaint_complete_at"]
    (output_dir / "postpaint_order_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit["result"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
