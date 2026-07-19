# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui import kline_js_readiness as readiness


class _Page:
    def __init__(self):
        self.callback = None

    def runJavaScript(self, script, callback):
        assert "applySnapshot" in script
        assert "getSnapshotRenderState" in script
        self.callback = callback


def _window(browser):
    calls = []
    return SimpleNamespace(
        _closing=False,
        browser=browser,
        _browser_epoch=2,
        _shell_loaded=False,
        _open_stages=SimpleNamespace(record=lambda stage: calls.append(stage)),
        _apply_chart_theme=lambda **kwargs: calls.append("theme"),
        _apply_chart_market_state=lambda: calls.append("market"),
        _apply_chart_glass_mode=lambda: calls.append("glass"),
        _set_status_message=lambda text, tone="info": calls.append(tone),
        isHidden=lambda: False,
        isMinimized=lambda: False,
        calls=calls,
    )


def test_js_ready_requires_positive_api_probe_and_current_browser_epoch(monkeypatch):
    page = _Page()
    browser = SimpleNamespace(page=lambda: page)
    window = _window(browser)
    visible = []
    monkeypatch.setattr(readiness, "sync_runtime_visibility", lambda *args, **kwargs: visible.append(kwargs))

    assert readiness.begin_js_readiness_probe(window, browser, 2) is True
    page.callback({"ok": True, "applySnapshot": True, "snapshotRenderState": True})

    assert window._shell_loaded is True
    assert window.calls[:4] == ["theme", "market", "glass", "js_ready"]
    assert visible


def test_stale_or_false_probe_never_marks_js_ready(monkeypatch):
    page = _Page()
    browser = SimpleNamespace(page=lambda: page)
    window = _window(browser)
    monkeypatch.setattr(readiness, "sync_runtime_visibility", lambda *args, **kwargs: None)

    readiness.begin_js_readiness_probe(window, browser, 2)
    window._browser_epoch = 3
    page.callback({"ok": True, "applySnapshot": True, "snapshotRenderState": True})
    assert window._shell_loaded is False
    assert "js_ready" not in window.calls

    window._browser_epoch = 2
    readiness.begin_js_readiness_probe(window, browser, 2)
    page.callback({"ok": False, "applySnapshot": False})
    assert window._shell_loaded is False
    assert window.calls[-1] == "error"
