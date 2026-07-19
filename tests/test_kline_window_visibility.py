# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui import kline_window_visibility as visibility
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController


class _Timer:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class _Page:
    def __init__(self):
        self.scripts = []

    def runJavaScript(self, script, callback=None):
        self.scripts.append(script)
        if callback is not None:
            callback({"ok": True})


class _DeferredPage:
    def __init__(self):
        self.callbacks = []

    def runJavaScript(self, _script, callback=None):
        self.callbacks.append(callback)


def test_hidden_runtime_pauses_and_resume_submits_latest_snapshot(monkeypatch):
    lifecycle = KLineRuntimeLifecycleController()
    snapshot = lifecycle.record_snapshot_json(
        '{"windowId":"w","generation":1,"code":"1","points":6,"data":{}}',
        window_id="w",
        code="1",
        generation=1,
        points=6,
        version=1,
    )
    page = _Page()
    timer = _Timer()
    submitted = []
    starts = []
    monkeypatch.setattr(
        visibility,
        "submit_pending_snapshot",
        lambda window, candidate=None: submitted.append(candidate) or True,
    )
    window = SimpleNamespace(
        _closing=False,
        _runtime_lifecycle=lifecycle,
        _runtime_active=True,
        _shell_loaded=True,
        _rt_timer=timer,
        _latest_rt_quote=None,
        browser=SimpleNamespace(page=lambda: page),
        _start_rt_timer=lambda: starts.append(True),
    )

    assert visibility.sync_runtime_visibility(window, hidden=True, minimized=False) is False
    assert timer.stops == 1
    assert "false" in page.scripts[-1]
    assert submitted == []

    assert visibility.sync_runtime_visibility(window, hidden=False, minimized=False) is True
    assert "true" in page.scripts[-1]
    assert submitted == [snapshot]
    assert starts == [True]


def test_resume_snapshot_is_requeued_when_browser_is_not_ready(monkeypatch):
    lifecycle = KLineRuntimeLifecycleController()
    lifecycle.record_snapshot_json(
        "{}", window_id="w", code="1", generation=1, points=0, version=1
    )
    lifecycle.set_visibility(hidden=True)
    requeued = []
    monkeypatch.setattr(visibility, "requeue_snapshot", lambda window, snapshot: requeued.append(snapshot))
    window = SimpleNamespace(
        _closing=False,
        _runtime_lifecycle=lifecycle,
        _runtime_active=False,
        _shell_loaded=False,
        _rt_timer=None,
        browser=None,
    )

    assert visibility.sync_runtime_visibility(window, hidden=False, minimized=False) is True
    assert len(requeued) == 1


def test_stale_visibility_callback_cannot_resume_current_runtime(monkeypatch):
    page = _DeferredPage()
    starts = []
    browser = SimpleNamespace(page=lambda: page)
    monkeypatch.setattr(visibility, "submit_pending_snapshot", lambda *args, **kwargs: True)
    window = SimpleNamespace(
        _closing=False,
        _runtime_lifecycle=KLineRuntimeLifecycleController(),
        _runtime_active=True,
        _shell_loaded=True,
        _rt_timer=None,
        _latest_rt_quote=None,
        _browser_epoch=3,
        _visibility_epoch=0,
        browser=browser,
        _start_rt_timer=lambda: starts.append(True),
    )

    assert visibility.sync_runtime_visibility(window, hidden=False, minimized=False) is True
    assert visibility.sync_runtime_visibility(window, hidden=False, minimized=False) is True
    page.callbacks[0]({"ok": True})
    assert starts == []
    page.callbacks[1]({"ok": True})
    assert starts == [True]
