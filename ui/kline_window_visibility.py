# -*- coding: utf-8 -*-
"""Pause hidden K-line runtimes and replay only their latest complete state."""

from __future__ import annotations

from ui.kline_render_bridge import build_runtime_active_script
from ui.kline_window_rendering import requeue_snapshot, submit_pending_snapshot


def _resume_owner_is_current(window, *, browser, browser_epoch: int, visibility_epoch: int, identity) -> bool:
    controller = getattr(window, "_load_controller", None)
    identity_current = controller is None or (
        identity is not None and controller.is_current(identity)
    )
    return bool(
        getattr(window, "browser", None) is browser
        and int(getattr(window, "_browser_epoch", 0) or 0) == browser_epoch
        and int(getattr(window, "_visibility_epoch", -1)) == visibility_epoch
        and identity_current
    )


def _resume_runtime(window, snapshot, *, browser, browser_epoch: int, visibility_epoch: int, identity) -> None:
    if getattr(window, "_closing", False):
        return
    if not getattr(window, "_runtime_active", False):
        if snapshot is not None:
            requeue_snapshot(window, snapshot)
        return
    if not _resume_owner_is_current(
        window,
        browser=browser,
        browser_epoch=browser_epoch,
        visibility_epoch=visibility_epoch,
        identity=identity,
    ):
        if snapshot is not None:
            requeue_snapshot(window, snapshot)
        return
    if snapshot is not None:
        submit_pending_snapshot(window, snapshot)
    else:
        submit_pending_snapshot(window)
    window._start_rt_timer()
    if getattr(window, "_latest_rt_quote", None) is not None:
        from ui.kline_window_runtime import resume_realtime_updates

        resume_realtime_updates(window)


def _submit_runtime_state(window, *, browser, browser_epoch, visibility_epoch, identity, active, snapshot) -> None:
    def _on_ack(_ack) -> None:
        if active:
            _resume_runtime(
                window,
                snapshot,
                browser=browser,
                browser_epoch=browser_epoch,
                visibility_epoch=visibility_epoch,
                identity=identity,
            )

    try:
        browser.page().runJavaScript(build_runtime_active_script(active), _on_ack)
    except (AttributeError, RuntimeError, TypeError):
        if snapshot is not None:
            requeue_snapshot(window, snapshot)


def sync_runtime_visibility(window, *, hidden: bool, minimized: bool) -> bool:
    lifecycle = window._runtime_lifecycle
    resume_snapshot = lifecycle.set_visibility(hidden=hidden, minimized=minimized)
    active = lifecycle.runtime_active
    window._runtime_active = active
    timer = getattr(window, "_rt_timer", None)
    if not active and timer is not None:
        timer.stop()
    browser = getattr(window, "browser", None)
    if browser is None or not getattr(window, "_shell_loaded", False):
        if resume_snapshot is not None:
            requeue_snapshot(window, resume_snapshot)
        return active
    browser_epoch = int(getattr(window, "_browser_epoch", 0) or 0)
    visibility_epoch = int(getattr(window, "_visibility_epoch", 0) or 0) + 1
    window._visibility_epoch = visibility_epoch
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    _submit_runtime_state(
        window,
        browser=browser,
        browser_epoch=browser_epoch,
        visibility_epoch=visibility_epoch,
        identity=identity,
        active=active,
        snapshot=resume_snapshot,
    )
    return active
