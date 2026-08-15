# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pandas as pd

from app.services.kline_render_preparer import PreparedKlineRender
from ui import kline_window_rendering as rendering
from ui.kline_load_controller import KlineLoadController
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController
from ui.kline_window_rendering import (
    handle_snapshot_ack,
    handle_snapshot_render_state,
    load_chart_shell,
    queue_prepared_render,
    submit_pending_snapshot,
)


class _Stages:
    def __init__(self):
        self.calls = []

    def record(self, stage):
        self.calls.append(stage)


class _Page:
    def __init__(self):
        self.calls = []

    def runJavaScript(self, script, callback=None):
        self.calls.append((script, callback))


class _ShellBrowser:
    def __init__(self):
        self.calls = []

    def setHtml(self, html, base_url):
        self.calls.append((html, base_url))


class _ReadyShellBrowser(_ShellBrowser):
    def __init__(self):
        super().__init__()
        self._properties = {
            "klineShellReady": True,
            "klineShellHtmlBytes": 321,
        }

    def property(self, name):
        return self._properties.get(name)


class _FailingPage:
    def runJavaScript(self, _script, _callback=None):
        raise RuntimeError("script submission failed")


class _FallbackBrowser(_ShellBrowser):
    def __init__(self):
        super().__init__()
        self._page = _FailingPage()

    def page(self):
        return self._page


def _window(*, shell_loaded=True):
    controller = KlineLoadController(window_id="window-a")
    controller.begin("000001")
    page = _Page()
    statuses = []
    starts = []
    post_commit_updates = []
    return SimpleNamespace(
        _closing=False,
        _load_controller=controller,
        _runtime_lifecycle=KLineRuntimeLifecycleController(),
        _snapshot_version=1,
        _shell_loaded=shell_loaded,
        _browser_epoch=1,
        _snapshot_inflight=None,
        _snapshot_inflight_browser=None,
        _snapshot_inflight_epoch=None,
        _pending_frame=None,
        _pending_prepared_render=None,
        _last_prepared_render=None,
        _history_frame=None,
        _open_stages=_Stages(),
        browser=SimpleNamespace(page=lambda: page),
        code="000001",
        df=None,
        _set_status_message=lambda text, tone="info": statuses.append((text, tone)),
        _set_pending_chart_status=lambda text, tone: statuses.append((text, tone)),
        _finish_pending_chart_status=lambda: statuses.append(("finished", "success")),
        _apply_chart_market_state=lambda: post_commit_updates.append("market"),
        _apply_chart_glass_mode=lambda: post_commit_updates.append("glass"),
        _start_rt_timer=lambda: starts.append(True),
        _page=page,
        _statuses=statuses,
        _starts=starts,
        _post_commit_updates=post_commit_updates,
    )


def _prepared(version=1):
    frame = _frame()
    return PreparedKlineRender(
        owner_id="window-a",
        generation=1,
        code="000001",
        title="平安银行 (000001) 日线",
        snapshot_version=version,
        payload_json=(
            '{"windowId":"window-a","generation":1,"snapshotVersion":%d,'
            '"code":"000001","points":6,"data":{"dates":["d"]}}' % version
        ),
        point_count=6,
        _display_frame=frame,
        _history_frame=frame,
    )


def _frame():
    return pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100.0]},
        index=pd.to_datetime(["2026-07-16"]),
    )


def test_data_can_finish_before_shell_and_applies_only_after_js_is_ready():
    window = _window(shell_loaded=False)
    assert queue_prepared_render(window, _prepared(), loading=False) is True
    assert window._open_stages.calls == ["data_ready"]
    assert window._page.calls == []
    assert window.df is None

    window._shell_loaded = True
    assert submit_pending_snapshot(window) is True
    script, callback = window._page.calls[-1]
    assert "window.applySnapshot" in script
    assert callback is not None


def test_static_shell_is_built_once_and_reused_for_recovery_reload():
    browser = _ShellBrowser()
    built = []
    window = SimpleNamespace(_closing=False, browser=browser, _shell_loaded=True)

    for _ in range(2):
        assert load_chart_shell(
            window,
            echarts_js_path=r"D:\assets\echarts.min.js",
            shell_builder=lambda **kwargs: built.append(kwargs) or "<html>static-shell</html>",
            theme_colors={"bg": "black"},
        ) is True

    assert len(built) == 1
    assert len(browser.calls) == 2
    assert window._last_chart_html_bytes == len("<html>static-shell</html>".encode("utf-8"))
    assert window._shell_loaded is False


def test_prewarmed_shell_reuses_page_without_rebuilding_html(qt_application):
    browser = _ReadyShellBrowser()
    built = []
    load_finished = []
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _browser_epoch=7,
        _on_chart_load_finished=lambda ok: load_finished.append(ok),
    )

    assert load_chart_shell(
        window,
        echarts_js_path=r"D:\assets\echarts.min.js",
        shell_builder=lambda **kwargs: built.append(kwargs) or "<html>unexpected</html>",
        theme_colors={"bg": "black"},
    ) is True

    assert built == []
    assert browser.calls == []
    assert window._last_chart_html_bytes == 321
    qt_application.processEvents()
    assert load_finished == [True]


def test_js_submission_failure_builds_only_one_controlled_fallback_page(monkeypatch):
    window = _window()
    browser = _FallbackBrowser()
    window.browser = browser
    window._chart_echarts_js_path = r"D:\assets\echarts.min.js"
    window._chart_theme_colors = {"bg": "black"}
    window._chart_base_url = object()
    built = []
    monkeypatch.setattr(
        rendering,
        "build_kline_html",
        lambda *args: built.append(args) or "<html>fallback</html>",
    )

    assert queue_prepared_render(window, _prepared(), loading=False) is True
    assert len(built) == 1
    assert len(browser.calls) == 1
    assert window._shell_loaded is False
    assert any(tone == "warning" for _text, tone in window._statuses)

    window._shell_loaded = True
    assert submit_pending_snapshot(window) is False
    assert len(built) == 1
    assert len(browser.calls) == 1


def test_chart_ready_and_frame_commit_require_exact_latest_snapshot_ack():
    window = _window()
    frame = _frame()
    queue_prepared_render(window, _prepared(), loading=False)
    snapshot = window._snapshot_inflight

    bad = {"ok": True, "applied": True, "windowId": "other", "generation": 1, "code": "000001", "points": 6}
    assert handle_snapshot_ack(window, snapshot, bad) is False
    assert window.df is None
    assert "chart_ready" not in window._open_stages.calls

    queue_prepared_render(window, _prepared(version=2), loading=False)
    snapshot = window._snapshot_inflight
    good = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 2,
    }
    assert handle_snapshot_ack(window, snapshot, good) is False
    assert window.df is None
    assert "chart_ready" not in window._open_stages.calls
    query_script, _query_callback = window._page.calls[-1]
    assert "window.getSnapshotRenderState" in query_script
    rendered = dict(good, applied=False, rendered=True)
    assert handle_snapshot_render_state(window, snapshot, rendered) is True
    assert window.df.equals(frame)
    assert window._history_frame.equals(frame)
    assert window._load_controller.owns_current_frame("000001", 1) is True
    assert window._open_stages.calls[-1] == "chart_ready"
    assert window._starts == [True]
    assert window._post_commit_updates == []


def test_queue_prepared_render_transfers_owned_frames_without_gui_copy(monkeypatch):
    window = _window(shell_loaded=False)
    prepared = _prepared()
    display_frame = prepared._display_frame
    history_frame = prepared._history_frame

    def _reject_gui_copy(*_args, **_kwargs):
        raise AssertionError("GUI render handoff must not deep-copy DataFrames")

    monkeypatch.setattr(pd.DataFrame, "copy", _reject_gui_copy)

    assert queue_prepared_render(window, prepared, loading=False) is True
    assert window._pending_frame[1] is display_frame
    assert window._pending_frame[2] is history_frame
    assert prepared._display_frame is None
    assert prepared._history_frame is None


def test_old_generation_rendered_event_cannot_commit_new_lease():
    window = _window()
    queue_prepared_render(window, _prepared(), loading=False)
    snapshot = window._snapshot_inflight
    applied = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 1,
    }
    assert handle_snapshot_ack(window, snapshot, applied) is False
    window._load_controller.begin("000002")
    window.code = "000002"
    rendered = dict(applied, applied=False, rendered=True)

    assert handle_snapshot_render_state(window, snapshot, rendered) is False
    assert window.df is None
    assert "chart_ready" not in window._open_stages.calls


def test_stale_prepared_generation_is_dropped_without_touching_visible_frame():
    window = _window()
    old_frame = _frame()
    window.df = old_frame
    stale = PreparedKlineRender(
        owner_id="window-a",
        generation=0,
        code="000001",
        title="old",
        snapshot_version=1,
        payload_json='{"data":{}}',
        point_count=6,
    )

    assert queue_prepared_render(window, stale, loading=False) is False
    assert window.df is old_frame
    assert window._page.calls == []


def test_old_browser_epoch_callback_cannot_commit_after_recovery():
    window = _window()
    queue_prepared_render(window, _prepared(), loading=False)
    snapshot = window._snapshot_inflight
    old_browser = window.browser
    window.browser = SimpleNamespace(page=lambda: _Page())
    window._browser_epoch = 2
    good = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 1,
    }

    assert handle_snapshot_ack(window, snapshot, good, browser=old_browser, epoch=1) is False
    assert window.df is None


def test_hidden_queued_ack_is_requeued_for_python_commit_on_resume():
    window = _window()
    queue_prepared_render(window, _prepared(), loading=False)
    snapshot = window._snapshot_inflight
    window._runtime_lifecycle.set_visibility(hidden=True)
    queued = {
        "ok": True,
        "queued": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 1,
    }

    assert handle_snapshot_ack(window, snapshot, queued) is False
    assert window.df is None
    assert window._runtime_lifecycle.set_visibility(hidden=False) == snapshot
    assert not any(tone == "error" for _text, tone in window._statuses)


def test_apply_callback_never_returns_watchdog_releases_inflight(monkeypatch):
    window = _window()
    assert queue_prepared_render(window, _prepared(), loading=False) is True
    snapshot = window._snapshot_inflight
    monkeypatch.setattr(rendering, "load_controlled_fallback_page", lambda *_args: True)
    window._snapshot_render_deadline = 0.0

    rendering._on_snapshot_render_watchdog(window)

    assert snapshot is not None
    assert window._snapshot_inflight is None
    assert window._snapshot_render_query_pending is False
    assert window._snapshot_render_deadline is None
    assert "chart_ready" not in window._open_stages.calls


def test_render_state_callback_never_returns_watchdog_releases_query(monkeypatch):
    window = _window()
    assert queue_prepared_render(window, _prepared(), loading=False) is True
    snapshot = window._snapshot_inflight
    applied = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 1,
    }
    assert handle_snapshot_ack(window, snapshot, applied) is False
    assert window._snapshot_render_query_pending is True
    monkeypatch.setattr(rendering, "load_controlled_fallback_page", lambda *_args: True)
    window._snapshot_render_deadline = 0.0

    rendering._on_snapshot_render_watchdog(window)

    assert window._snapshot_inflight is None
    assert window._snapshot_render_query_pending is False
    assert "chart_ready" not in window._open_stages.calls


def test_new_snapshot_supersedes_old_render_query_without_waiting_for_timeout():
    window = _window()
    assert queue_prepared_render(window, _prepared(version=1), loading=False) is True
    old_snapshot = window._snapshot_inflight
    applied = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 1,
        "code": "000001",
        "points": 6,
        "snapshotVersion": 1,
    }
    assert handle_snapshot_ack(window, old_snapshot, applied) is False
    assert window._snapshot_render_query_pending is True

    assert queue_prepared_render(window, _prepared(version=2), loading=False) is True

    new_snapshot = window._snapshot_inflight
    assert new_snapshot is not None
    assert new_snapshot.version == 2
    assert new_snapshot is not old_snapshot
    assert window._snapshot_render_query_pending is False
    assert "window.applySnapshot" in window._page.calls[-1][0]
    stale_rendered = dict(applied, applied=False, rendered=True)
    assert handle_snapshot_render_state(window, old_snapshot, stale_rendered) is False
    assert window._snapshot_inflight is new_snapshot


def test_cancel_render_confirmation_stops_both_reusable_timers():
    stopped = []
    window = SimpleNamespace(
        _render_commit_timer=SimpleNamespace(stop=lambda: stopped.append("poll")),
        _render_watchdog_timer=SimpleNamespace(stop=lambda: stopped.append("watchdog")),
        _snapshot_inflight=object(),
        _snapshot_inflight_browser=object(),
        _snapshot_inflight_epoch=3,
        _snapshot_render_query_pending=True,
        _snapshot_render_deadline=123.0,
    )

    rendering.cancel_snapshot_render_confirmation(window)

    assert stopped == ["poll", "watchdog"]
    assert window._snapshot_inflight is None
    assert window._snapshot_inflight_browser is None
    assert window._snapshot_inflight_epoch is None
    assert window._snapshot_render_query_pending is False
    assert window._snapshot_render_deadline is None
