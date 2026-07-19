# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui import kline_window_recovery as recovery
from ui.kline_load_controller import KlineLoadController
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController
from ui.kline_window_visibility import sync_runtime_visibility


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)


class _Browser:
    def __init__(self):
        self.render_signal = _Signal()
        self._page = SimpleNamespace(renderProcessTerminated=self.render_signal)

    def page(self):
        return self._page


def test_render_process_signal_can_be_disconnected_during_browser_disposal():
    browser = _Browser()
    window = SimpleNamespace()

    assert recovery.install_render_process_recovery(window, browser) is True
    assert len(browser.render_signal.callbacks) == 1
    assert recovery.uninstall_render_process_recovery(browser) is True
    assert browser.render_signal.callbacks == []
    assert recovery.uninstall_render_process_recovery(browser) is False


def test_render_process_recovery_is_scheduled_once_for_current_browser(monkeypatch):
    calls = []
    browser = object()
    lifecycle = SimpleNamespace(
        request_recovery=lambda token: SimpleNamespace(allowed=token is browser, reason="recovery_scheduled")
    )
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _runtime_lifecycle=lifecycle,
        _open_stages=SimpleNamespace(recover_browser=lambda owned: calls.append(owned)),
        _set_status_message=lambda *args, **kwargs: calls.append((args, kwargs)),
        _snapshot_inflight="old",
    )
    monkeypatch.setattr(recovery.QTimer, "singleShot", lambda delay, callback: callback())

    assert recovery.handle_render_process_terminated(window, browser, "crashed", 9) is True
    assert calls[-1] is browser
    assert window._snapshot_inflight is None
    assert recovery.handle_render_process_terminated(window, object(), "crashed", 9) is False


def test_recovery_denial_does_not_rebuild_browser(monkeypatch):
    scheduled = []
    browser = object()
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _runtime_lifecycle=SimpleNamespace(
            request_recovery=lambda token: SimpleNamespace(allowed=False, reason="recovery_already_used")
        ),
        _open_stages=SimpleNamespace(recover_browser=lambda owned: scheduled.append(owned)),
        _set_status_message=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(recovery.QTimer, "singleShot", lambda delay, callback: scheduled.append(callback))

    assert recovery.handle_render_process_terminated(window, browser, "crashed", 9) is False
    assert scheduled == []


def test_second_renderer_failure_enters_terminal_runtime_state(monkeypatch):
    stopped = []
    browser = object()
    lifecycle = KLineRuntimeLifecycleController()
    assert lifecycle.request_recovery(browser).allowed is True
    controller = KlineLoadController(window_id="terminal-window")
    controller.begin("000001")
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _shell_loaded=False,
        _runtime_lifecycle=lifecycle,
        _load_controller=controller,
        _open_stages=SimpleNamespace(recover_browser=lambda _owned: None),
        _set_status_message=lambda *_args, **_kwargs: None,
        _rt_timer=SimpleNamespace(stop=lambda: stopped.append("realtime")),
        _render_commit_timer=SimpleNamespace(stop=lambda: stopped.append("poll")),
        _render_watchdog_timer=SimpleNamespace(stop=lambda: stopped.append("watchdog")),
        _snapshot_inflight=object(),
        _snapshot_inflight_browser=browser,
        _snapshot_inflight_epoch=1,
        _snapshot_render_query_pending=True,
        _snapshot_render_deadline=123.0,
        _runtime_active=True,
    )
    monkeypatch.setattr(recovery.QTimer, "singleShot", lambda *_args: None)

    assert recovery.handle_render_process_terminated(window, browser, "crashed", 9) is False

    assert stopped == ["poll", "watchdog", "realtime"]
    assert window._snapshot_inflight is None
    assert window._snapshot_render_query_pending is False
    assert window._runtime_active is False
    assert lifecycle.runtime_active is False
    assert controller.closed is True
    assert sync_runtime_visibility(window, hidden=False, minimized=False) is False
    assert window._runtime_active is False
