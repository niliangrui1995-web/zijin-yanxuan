# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui import kline_window_visibility as visibility
from ui.kline_load_controller import KlineLoadController
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
    controller = KlineLoadController(window_id="w")
    controller.begin("1")
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
        lambda window, candidate=None: submitted.append(
            candidate or window._runtime_lifecycle.take_pending_submission()
        ) or True,
    )
    window = SimpleNamespace(
        _closing=False,
        _load_controller=controller,
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


def _deferred_snapshot_window():
    page = _DeferredPage()
    controller = KlineLoadController(window_id="visibility-race")
    controller.begin("000001")
    lifecycle = KLineRuntimeLifecycleController()
    lifecycle.set_visibility(hidden=True)
    return SimpleNamespace(
        _closing=False,
        _load_controller=controller,
        _runtime_lifecycle=lifecycle,
        _runtime_active=False,
        _shell_loaded=True,
        _rt_timer=None,
        _latest_rt_quote=None,
        _browser_epoch=1,
        _visibility_epoch=0,
        browser=SimpleNamespace(page=lambda: page),
        _start_rt_timer=lambda: None,
        _page=page,
    )


def _record_snapshot(window, version):
    return window._runtime_lifecycle.record_snapshot_json(
        "{}", window_id="visibility-race", code="000001", generation=1, points=1, version=version
    )


def test_delayed_show_ack_after_hiding_cannot_replace_newer_snapshot():
    window = _deferred_snapshot_window()
    _record_snapshot(window, 1)
    visibility.sync_runtime_visibility(window, hidden=False, minimized=False)
    visibility.sync_runtime_visibility(window, hidden=True, minimized=False)
    latest = _record_snapshot(window, 2)

    window._page.callbacks[0]({"ok": True})

    assert window._runtime_lifecycle.latest_snapshot == latest
    assert window._runtime_lifecycle.set_visibility(hidden=False) == latest


def test_resume_ack_submits_newest_snapshot_received_while_ack_was_pending(monkeypatch):
    window = _deferred_snapshot_window()
    _record_snapshot(window, 1)
    submitted = []
    monkeypatch.setattr(
        visibility,
        "submit_pending_snapshot",
        lambda owner, candidate=None: submitted.append(
            candidate or owner._runtime_lifecycle.take_pending_submission()
        ),
    )
    visibility.sync_runtime_visibility(window, hidden=False, minimized=False)
    latest = _record_snapshot(window, 2)

    window._page.callbacks[0]({"ok": True})

    assert submitted == [latest]
    assert window._runtime_lifecycle.take_pending_submission() is None


def test_latest_show_ack_does_not_depend_on_obsolete_show_ack_arrival(monkeypatch):
    window = _deferred_snapshot_window()
    latest = _record_snapshot(window, 1)
    submitted = []
    monkeypatch.setattr(
        visibility,
        "submit_pending_snapshot",
        lambda owner, candidate=None: submitted.append(
            candidate or owner._runtime_lifecycle.take_pending_submission()
        ),
    )
    visibility.sync_runtime_visibility(window, hidden=False, minimized=False)
    visibility.sync_runtime_visibility(window, hidden=False, minimized=False)

    window._page.callbacks[1]({"ok": True})
    window._page.callbacks[0]({"ok": True})

    assert submitted == [latest]
    assert window._runtime_lifecycle.take_pending_submission() is None
