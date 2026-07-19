# -*- coding: utf-8 -*-
"""Fail-closed JS API probe for one current WebEngine browser epoch."""

from __future__ import annotations

from collections.abc import Mapping

from ui.kline_window_visibility import sync_runtime_visibility

_JS_READINESS_PROBE = (
    "(function(){"
    "const ready=typeof window.applySnapshot==='function'&&typeof window.setRuntimeActive==='function'"
    "&&typeof window.resetForLease==='function'&&typeof window.getSnapshotRenderState==='function';"
    "return {ok:ready,applySnapshot:typeof window.applySnapshot==='function',"
    "snapshotRenderState:typeof window.getSnapshotRenderState==='function'};"
    "})()"
)
_SHELL_READY_PROPERTY = "klineShellReady"


def set_shell_ready(browser, ready: bool) -> None:
    objects = [browser]
    try:
        objects.append(browser.page())
    except (AttributeError, RuntimeError, TypeError):
        pass
    for obj in objects:
        try:
            obj.setProperty(_SHELL_READY_PROPERTY, bool(ready))
        except (AttributeError, RuntimeError, TypeError):
            try:
                setattr(obj, f"_{_SHELL_READY_PROPERTY}", bool(ready))
            except (AttributeError, RuntimeError, TypeError):
                pass


def _is_current_epoch(window, browser, epoch: int) -> bool:
    return bool(
        not getattr(window, "_closing", False)
        and getattr(window, "browser", None) is browser
        and int(getattr(window, "_browser_epoch", -1)) == int(epoch)
    )


def _finish_js_readiness_probe(window, browser, epoch: int, ack) -> None:
    if not _is_current_epoch(window, browser, epoch):
        return
    ready = bool(
        isinstance(ack, Mapping)
        and ack.get("ok") is True
        and ack.get("applySnapshot") is True
        and ack.get("snapshotRenderState") is True
    )
    if not ready:
        set_shell_ready(browser, False)
        window._shell_loaded = False
        window._set_status_message("图表脚本接口未就绪，请重试", tone="error")
        return
    set_shell_ready(browser, True)
    window._shell_loaded = True
    window._apply_chart_theme(animate=False)
    window._apply_chart_market_state()
    window._apply_chart_glass_mode()
    stages = getattr(window, "_open_stages", None)
    if stages is not None:
        stages.record("js_ready")
    sync_runtime_visibility(window, hidden=window.isHidden(), minimized=window.isMinimized())


def begin_js_readiness_probe(window, browser, epoch: int) -> bool:
    if not _is_current_epoch(window, browser, epoch):
        return False
    try:
        browser.page().runJavaScript(
            _JS_READINESS_PROBE,
            lambda ack, owned=browser, owned_epoch=epoch: _finish_js_readiness_probe(
                window,
                owned,
                owned_epoch,
                ack,
            ),
        )
    except (AttributeError, RuntimeError, TypeError):
        set_shell_ready(browser, False)
        window._shell_loaded = False
        window._set_status_message("图表脚本探测失败，请重试", tone="error")
        return False
    return True
