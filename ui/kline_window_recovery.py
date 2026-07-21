# -*- coding: utf-8 -*-
"""Controlled single-recovery wiring for QWebEngine render-process loss."""

from __future__ import annotations

from contextlib import suppress

from PyQt6.QtCore import QTimer

from ui.kline_js_readiness import set_shell_ready
from ui.kline_pool_state import KLinePoolState
from ui.kline_typing import KLineBrowserProtocol, KLineRecoveryWindowProtocol
from ui.kline_window_rendering import cancel_snapshot_render_confirmation


def install_render_process_recovery(
    window: KLineRecoveryWindowProtocol, browser: KLineBrowserProtocol
) -> bool:
    def _on_terminated(status, exit_code, owned=browser):
        return handle_render_process_terminated(window, owned, status, exit_code)

    callback = _on_terminated
    try:
        signal = browser.page().renderProcessTerminated
        signal.connect(callback)
        browser._kline_render_process_callback = callback
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def uninstall_render_process_recovery(browser: KLineBrowserProtocol) -> bool:
    callback = getattr(browser, "_kline_render_process_callback", None)
    if callback is None:
        return False
    try:
        browser.page().renderProcessTerminated.disconnect(callback)
        del browser._kline_render_process_callback
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def handle_render_process_terminated(
    window: KLineRecoveryWindowProtocol,
    browser: KLineBrowserProtocol,
    status: object,
    exit_code: object,
) -> bool:
    if getattr(window, "_closing", False) or getattr(window, "browser", None) is not browser:
        return False
    cancel_snapshot_render_confirmation(window)
    realtime_timer = getattr(window, "_rt_timer", None)
    if realtime_timer is not None:
        with suppress(AttributeError, RuntimeError, TypeError):
            realtime_timer.stop()
    window._runtime_active = False
    decision = window._runtime_lifecycle.request_recovery(browser)
    window.transition(KLinePoolState.TAINTED, reason="render_process_terminated")
    if not decision.allowed:
        load_controller = getattr(window, "_load_controller", None)
        if load_controller is not None:
            with suppress(AttributeError, RuntimeError, TypeError):
                load_controller.close()
        with suppress(AttributeError, RuntimeError, TypeError):
            window._runtime_lifecycle.begin_close()
        window._set_status_message("图表渲染进程再次异常，请重新打开窗口", tone="error")
        return False
    set_shell_ready(browser, False)
    window._shell_loaded = False
    window._set_status_message("图表渲染进程异常，正在自动恢复...", tone="warning")

    def _recover() -> None:
        if getattr(window, "_closing", False) or getattr(window, "browser", None) is not browser:
            return
        stages = getattr(window, "_open_stages", None)
        if stages is not None:
            with suppress(AttributeError, RuntimeError, TypeError):
                stages.recover_browser(browser)

    QTimer.singleShot(0, _recover)
    return True
