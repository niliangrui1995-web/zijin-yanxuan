# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt

from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER
from ui.kline_window_qt import KLINE_BROWSER_ATTACH_DELAY_MS, KLINE_INITIAL_LOAD_DELAY_MS
from ui.kline_window_stages import (
    KLineOpenStageCoordinator,
    KlineStageTimeline,
    _abort_attached_browser_pipeline,
    build_chart_host,
)


def _fire_single_shot(timer) -> None:
    timer.stop()
    timer.timeout.emit()


def test_browser_and_history_load_have_no_fixed_start_delay():
    assert KLINE_BROWSER_ATTACH_DELAY_MS == 0
    assert KLINE_INITIAL_LOAD_DELAY_MS == 0


def test_cold_browser_pipeline_uses_owned_precise_single_shot_slices(qt_application):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    events = []
    logs = []

    class _Signal:
        def connect(self, _callback):
            return None

    class _Page:
        def __init__(self):
            self.renderProcessTerminated = _Signal()

        def property(self, _name):
            return None

    class _Browser(QWidget):
        def __init__(self, parent):
            events.append("create")
            super().__init__(parent)
            self.loadFinished = _Signal()
            self._page = _Page()

        def setPage(self, page):
            events.append("handoff")
            self._page = page

        def page(self):
            return self._page

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 7
    window.browser = None
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: events.append("shell")
    window._on_chart_load_finished = lambda _ok: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    page = _Page()
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=_Browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda event, **fields: logs.append((event, fields)),
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_page = page

    owned_timers = (
        coordinator.browser_create_timer,
        coordinator.page_handoff_timer,
        coordinator.browser_attach_timer,
        coordinator.browser_activate_timer,
        coordinator.shell_load_timer,
    )
    assert all(timer.parent() is window for timer in owned_timers)
    assert all(timer.isSingleShot() for timer in owned_timers)
    assert all(timer.timerType() == Qt.TimerType.PreciseTimer for timer in owned_timers)

    assert coordinator.initialize_browser(staged=True) is True
    assert coordinator.browser_create_timer.interval() == 1
    assert events == []
    _fire_single_shot(coordinator.browser_create_timer)
    assert coordinator.page_handoff_timer.interval() == 1
    assert events == ["create"]
    _fire_single_shot(coordinator.page_handoff_timer)
    assert coordinator.browser_attach_timer.interval() == 1
    assert events == ["create", "handoff"]
    _fire_single_shot(coordinator.browser_attach_timer)
    assert coordinator.browser_activate_timer.interval() == 1
    assert coordinator.browser_activate_timer.isActive() is True
    assert coordinator.shell_load_timer.isActive() is False
    assert window.browser is not None
    assert events == ["create", "handoff"]
    _fire_single_shot(coordinator.browser_activate_timer)
    assert coordinator.shell_load_timer.interval() == 1
    assert events == ["create", "handoff"]
    _fire_single_shot(coordinator.shell_load_timer)

    assert events == ["create", "handoff", "shell"]
    diagnostics = window._browser_attach_diagnostics
    for name in (
        "browser_create_ms",
        "page_handoff_ms",
        "browser_attach_sync_ms",
        "browser_attach_total_ms",
        "hierarchy_slice_ms",
        "activation_queue_ms",
        "activation_slice_ms",
        "layout_commit_ms",
        "surface_show_ms",
        "max_sync_slice_ms",
        "pipeline_total_ms",
    ):
        assert diagnostics[name] >= 0.0
    assert diagnostics["max_sync_slice_ms"] == max(
        diagnostics["browser_create_ms"],
        diagnostics["page_handoff_slice_ms"],
        diagnostics["browser_attach_sync_ms"],
        diagnostics["load_shell_dispatch_ms"],
    )
    assert diagnostics["browser_attach_sync_ms"] == max(
        diagnostics["hierarchy_slice_ms"],
        diagnostics["activation_slice_ms"],
    )
    assert diagnostics["browser_attach_total_ms"] >= diagnostics["browser_attach_sync_ms"]
    assert diagnostics["pipeline_total_ms"] >= diagnostics["max_sync_slice_ms"]
    attached_log = next(fields for event, fields in logs if event == "kline.browser_attached")
    assert attached_log["max_sync_slice_ms"] == diagnostics["max_sync_slice_ms"]
    assert attached_log["pipeline_total_ms"] == diagnostics["pipeline_total_ms"]
    coordinator.stop()
    window.close()


def test_renderer_guard_is_installed_before_attached_browser_becomes_visible(
    qt_application,
    monkeypatch,
):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from ui import kline_window_stages as stages_module

    events = []

    class _Signal:
        def connect(self, _callback):
            return None

        def disconnect(self, _callback):
            return None

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.loadFinished = _Signal()
            self._page = SimpleNamespace(renderProcessTerminated=_Signal())

        def page(self):
            return self._page

        def show(self):
            events.append("show")
            super().show()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window.browser = None
    window._browser_epoch = 0
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: True
    window._on_chart_load_finished = lambda _ok: None
    window._apply_browser_surface_theme = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    browser = _Browser(window.chart_host)
    monkeypatch.setattr(
        stages_module,
        "install_render_process_recovery",
        lambda *_args, **_kwargs: events.append("guard") or True,
    )
    monkeypatch.setattr(stages_module, "uninstall_render_process_recovery", lambda *_args: True)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )

    coordinator.initialize_browser(staged=True)
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)

    assert events == ["guard"]
    _fire_single_shot(coordinator.browser_activate_timer)
    assert events[:2] == ["guard", "show"]
    coordinator.stop()
    window.close()


def test_shell_timer_start_failure_drains_handed_off_page_exactly_once(
    qt_application,
    monkeypatch,
):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from ui import kline_window_stages as stages_module
    from ui.components.kline_window_manager import kline_manager

    class _Signal:
        def connect(self, _callback):
            return None

        def disconnect(self, _callback):
            return None

    class _Page:
        def __init__(self):
            self.stop_calls = 0
            self.delete_calls = 0
            self.renderProcessTerminated = _Signal()

        def property(self, _name):
            return None

        def stop(self):
            self.stop_calls += 1

        def deleteLater(self):
            self.delete_calls += 1

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.loadFinished = _Signal()
            self._page = None
            self.stop_calls = 0
            self.delete_calls = 0

        def setPage(self, page):
            self._page = page

        def page(self):
            return self._page

        def stop(self):
            self.stop_calls += 1

        def deleteLater(self):
            self.delete_calls += 1
            super().deleteLater()

    class _FailingTimer:
        def __init__(self):
            self.stop_calls = 0

        def start(self, _delay):
            raise RuntimeError("timer start failed")

        def stop(self):
            self.stop_calls += 1

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window.browser = None
    window._browser_epoch = 0
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: True
    window._on_chart_load_finished = lambda _ok: None
    window._apply_browser_surface_theme = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    page = _Page()
    browser = _Browser(window.chart_host)
    monkeypatch.setattr(stages_module, "install_render_process_recovery", lambda *_args: True)
    monkeypatch.setattr(stages_module, "uninstall_render_process_recovery", lambda *_args: True)
    monkeypatch.setattr(kline_manager, "release_page", lambda *_args, **_kwargs: False)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_page = page
    coordinator.initialize_browser(staged=True)
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)
    coordinator.shell_load_timer = _FailingTimer()

    _fire_single_shot(coordinator.browser_activate_timer)

    assert coordinator.pending_browser is None
    assert coordinator.pending_page is None
    assert coordinator._pending_shell_load is None
    assert window.browser is None
    assert window.chart_placeholder is not None
    assert page.stop_calls == 1
    assert page.delete_calls == 1
    assert browser.stop_calls == 1
    assert browser.delete_calls == 1
    coordinator.stop()
    window.close()


def test_staged_browser_pipeline_restarts_for_latest_identity_before_attach(qt_application):
    from PyQt6.QtWidgets import QWidget

    created = []

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False
            created.append(self)

        def stop(self):
            return None

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._load_and_draw = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window.chart_host = QWidget(window)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=_Browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )

    assert coordinator.initialize_browser(staged=True) is True
    _fire_single_shot(coordinator.browser_create_timer)
    window._render_generation = 2
    _fire_single_shot(coordinator.page_handoff_timer)

    assert window.browser is None
    assert coordinator.pending_browser is None
    assert coordinator.pending_page is None
    assert created[0].deleted is True
    assert coordinator.browser_create_timer.isActive() is True
    assert coordinator.browser_attach_timer.isActive() is False
    assert coordinator.shell_load_timer.isActive() is False
    assert coordinator._active_browser_pipeline["identity"] == ("000001", 2)
    coordinator.stop()
    window.close()


def test_stale_attached_pipeline_clears_deleted_browser_before_restart():
    browser = object()
    page = object()
    state = {"generation": 1, "identity": ("000001", 1), "browser": browser}
    window = SimpleNamespace(_closing=False, browser=browser, _browser_epoch=4)
    restarted = []
    rolled_back = []
    coordinator = SimpleNamespace(
        window=window,
        pending_browser=browser,
        pending_page=page,
        _active_browser_pipeline=state,
        _browser_pipeline_generation=1,
        _current_load_identity=lambda: ("000002", 2),
        _rollback_browser_attachment=lambda owned: (
            rolled_back.append(owned),
            setattr(window, "browser", None),
        ),
    )

    def _restart(*, staged):
        restarted.append((staged, coordinator.pending_browser, coordinator.pending_page))
        return True

    coordinator.initialize_browser = _restart

    _abort_attached_browser_pipeline(
        coordinator,
        browser,
        page,
        state=state,
        epoch=4,
    )

    assert rolled_back == [browser]
    assert restarted == [(True, None, page)]
    assert coordinator.pending_browser is None


def test_stop_returns_resources_from_mid_browser_pipeline_and_cancels_all_slices(qt_application):
    from PyQt6.QtWidgets import QWidget

    class _Browser(QWidget):
        pass

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._load_and_draw = lambda: None
    browser = _Browser(window)
    page = object()
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_browser = browser
    coordinator.pending_page = page

    assert coordinator.initialize_browser(staged=True) is True
    _fire_single_shot(coordinator.browser_create_timer)
    pending_browser, pending_page = coordinator.stop()

    assert pending_browser is browser
    assert pending_page is page
    assert all(
        not timer.isActive()
        for timer in (
            coordinator.browser_create_timer,
            coordinator.page_handoff_timer,
            coordinator.browser_attach_timer,
            coordinator.shell_load_timer,
        )
    )
    qt_application.processEvents()
    assert window.browser is None
    window.close()


def _dispose_cancelled_pipeline(*, handoff_page: bool):
    from PyQt6.QtWidgets import QWidget

    from ui import kline_window_qt as kline

    class _Signal:
        def connect(self, _callback):
            return None

        def disconnect(self, _callback):
            return None

    class _Page:
        def __init__(self, parent=None):
            self._parent = parent
            self.stop_calls = 0
            self.delete_calls = 0

        def parent(self):
            return self._parent

        def property(self, _name):
            return None

        def stop(self):
            self.stop_calls += 1

        def deleteLater(self):
            self.delete_calls += 1

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.loadFinished = _Signal()
            self._page = _Page(self)
            self.stop_calls = 0
            self.delete_calls = 0

        def page(self):
            return self._page

        def setPage(self, page):
            self._page = page

        def stop(self):
            self.stop_calls += 1

        def deleteLater(self):
            self.delete_calls += 1
            super().deleteLater()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._load_and_draw = lambda: None
    window.chart_host = QWidget(window)
    browser = _Browser(window.chart_host)
    pending_page = _Page()
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_page = pending_page
    assert coordinator.initialize_browser(staged=True) is True
    _fire_single_shot(coordinator.browser_create_timer)
    if handoff_page:
        _fire_single_shot(coordinator.page_handoff_timer)
    pending_browser, returned_page = coordinator.stop()

    assert pending_browser is browser
    assert returned_page is pending_page
    assert kline._dispose_kline_browser(
        window,
        pending_browser,
        returned_page,
        allow_page_reuse=False,
    ) is True
    window.close()
    return browser, pending_page


def test_dispose_before_page_handoff_cleans_extra_pending_page_once(qt_application):
    browser, pending_page = _dispose_cancelled_pipeline(handoff_page=False)

    assert pending_page is not browser.page()
    assert pending_page.stop_calls == 1
    assert pending_page.delete_calls == 1
    assert browser.stop_calls == 1
    assert browser.delete_calls == 1


def test_dispose_after_page_handoff_cleans_shared_page_once(qt_application):
    browser, pending_page = _dispose_cancelled_pipeline(handoff_page=True)

    assert pending_page is browser.page()
    assert pending_page.stop_calls == 1
    assert pending_page.delete_calls == 1
    assert browser.stop_calls == 1
    assert browser.delete_calls == 1


def test_staged_browser_pipeline_fails_closed_when_window_starts_closing(qt_application):
    from PyQt6.QtWidgets import QWidget

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False

        def stop(self):
            return None

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._load_and_draw = lambda: None
    window.chart_host = QWidget(window)
    browser = _Browser(window.chart_host)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )

    assert coordinator.initialize_browser(staged=True) is True
    _fire_single_shot(coordinator.browser_create_timer)
    window._closing = True
    _fire_single_shot(coordinator.page_handoff_timer)

    assert browser.deleted is True
    assert window.browser is None
    assert coordinator.pending_browser is None
    assert coordinator.pending_page is None
    assert coordinator.browser_create_timer.isActive() is False
    coordinator.stop()
    window.close()


def test_staged_browser_pipeline_restarts_for_latest_identity_before_shell(qt_application):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def disconnect(self, callback):
            self.callbacks.remove(callback)

    class _Page:
        def __init__(self):
            self.renderProcessTerminated = _Signal()

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False
            self.loadFinished = _Signal()
            self._page = _Page()

        def page(self):
            return self._page

        def stop(self):
            return None

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    shell_loads = []
    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._browser_epoch = 0
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: shell_loads.append("shell")
    window._on_chart_load_finished = lambda _ok: None
    window._apply_browser_surface_theme = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    browser = _Browser(window.chart_host)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )

    assert coordinator.initialize_browser(staged=True) is True
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)
    assert window.browser is browser
    assert coordinator.browser_activate_timer.isActive() is True
    _fire_single_shot(coordinator.browser_activate_timer)
    assert coordinator.shell_load_timer.isActive() is True

    window._render_generation = 2
    _fire_single_shot(coordinator.shell_load_timer)

    assert shell_loads == []
    assert browser.deleted is True
    assert window.browser is None
    assert coordinator.browser_create_timer.isActive() is True
    assert coordinator._active_browser_pipeline["identity"] == ("000001", 2)
    coordinator.stop()
    window.close()


def test_reset_for_lease_disposes_cancelled_pending_browser_and_page(
    qt_application,
    monkeypatch,
):
    from PyQt6.QtWidgets import QWidget

    from ui.components.kline_window_manager import kline_manager

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False
            self.stopped = False

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    class _Page:
        def __init__(self):
            self.deleted = False
            self.stopped = False

        def property(self, _name):
            return None

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._render_generation = 1
    window.browser = None
    window._load_and_draw = lambda: None
    window.chart_host = QWidget(window)
    browser = _Browser(window.chart_host)
    page = _Page()
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_browser = browser
    coordinator.pending_page = page
    monkeypatch.setattr(kline_manager, "release_page", lambda *_args, **_kwargs: False)

    coordinator.reset_for_lease(20.0)

    assert browser.stopped is True
    assert browser.deleted is True
    assert page.stopped is True
    assert page.deleted is True
    assert coordinator.pending_browser is None
    assert coordinator.pending_page is None
    assert coordinator.context_timer.isActive() is True
    coordinator.stop()
    window.close()


def test_kline_stage_timeline_buffers_parallel_completion_into_strict_order():
    timeline = KlineStageTimeline()

    assert timeline.mark("data_ready", elapsed_ms=12.0) == ()
    assert timeline.mark("first_interaction", elapsed_ms=18.0) == ()
    assert timeline.mark("shell_ready", elapsed_ms=5.0) == ("shell_ready",)
    assert timeline.mark("browser_ready", elapsed_ms=20.0) == ("browser_ready", "data_ready")
    assert timeline.mark("chart_ready", elapsed_ms=28.0) == ()
    assert timeline.mark("js_ready", elapsed_ms=25.0) == ("js_ready", "chart_ready", "first_interaction")

    diagnostics = timeline.diagnostics()
    assert diagnostics["complete"] is True
    assert diagnostics["completed_stages"] == list(KLINE_OPEN_STAGE_ORDER)
    assert diagnostics["pending_stages"] == []
    assert diagnostics["observed_timings_ms"]["data_ready"] == 12.0
    assert diagnostics["timings_ms"]["data_ready"] == 20.0
    assert list(diagnostics["timings_ms"].values()) == sorted(diagnostics["timings_ms"].values())


def test_kline_stage_timeline_is_one_shot_and_fail_closed_when_incomplete():
    timeline = KlineStageTimeline()

    assert timeline.mark("chart_ready", elapsed_ms=30.0) == ()
    assert timeline.mark("chart_ready", elapsed_ms=31.0) == ()
    assert timeline.diagnostics() == {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "completed_stages": [],
        "pending_stages": ["chart_ready"],
        "timings_ms": {},
        "observed_timings_ms": {"chart_ready": 30.0},
        "complete": False,
    }


def test_kline_stage_timeline_rejects_unknown_stage():
    timeline = KlineStageTimeline()

    try:
        timeline.mark("page_ready", elapsed_ms=1.0)
    except ValueError as exc:
        assert "stage" in str(exc)
    else:
        raise AssertionError("unknown stages must be rejected")


def test_open_stage_coordinator_starts_data_before_browser_and_only_once(qt_application):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    loads = []
    shell_loads = []
    window = QWidget()
    window._closing = False
    window.code = "000001"
    window.name = "平安银行"
    window._render_generation = 0
    window.vcp_data = {}
    window._open_context_resolved = False
    window.browser = None
    window._context_ready = False
    window._resolve_vcp_context = lambda *_args: {}
    window._refresh_header_context = lambda: None
    window._load_and_draw = lambda: loads.append("data")
    window._load_chart_shell = lambda: shell_loads.append("shell")
    window._apply_qt_theme = lambda: None
    window._on_chart_load_finished = lambda _ok: None
    window._set_status_message = lambda *_args, **_kwargs: None
    window._log = type("Log", (), {"debug": lambda *_args: None})()
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)

    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=QWidget,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=50,
        initial_load_delay_ms=50,
    )
    coordinator.finish_deferred_context()

    assert coordinator.initial_load_timer.isActive() is True
    assert window._open_context_resolved is True
    assert window.browser is None
    coordinator.initial_load_timer.stop()
    coordinator.initial_load_timer.timeout.emit()
    assert loads == ["data"]

    class _Signal:
        def connect(self, _callback):
            return None

    class _Browser(QWidget):
        def __init__(self):
            super().__init__()
            self.loadFinished = _Signal()
            self._page = type("Page", (), {"renderProcessTerminated": _Signal()})()

        def page(self):
            return self._page

    coordinator.attach_browser(_Browser())
    coordinator.finish_deferred_context()

    assert shell_loads == []
    _fire_single_shot(coordinator.shell_load_timer)
    assert shell_loads == ["shell"]
    assert window._browser_attach_diagnostics["load_shell_deferred"] is True
    assert window._browser_attach_diagnostics["load_shell_dispatch_ok"] is True
    assert window._browser_attach_diagnostics["load_shell_dispatch_ms"] >= 0.0
    assert window.chart_placeholder is None
    assert coordinator.initial_load_timer.isActive() is False
    assert loads == ["data"]
    assert coordinator.begin_chart_load() is True
    assert coordinator.begin_chart_load() is False

    from ui.kline_load_controller import KlineLoadController

    controller = KlineLoadController(window_id="window-a")
    controller.begin(window.code)
    window._load_controller = controller
    assert coordinator.begin_chart_load() is True
    assert coordinator.begin_chart_load() is False
    controller.begin(window.code)
    assert coordinator.begin_chart_load() is True
    coordinator.stop()


def test_deferred_shell_load_is_cancelled_for_a_stale_browser_epoch(qt_application):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    class _Signal:
        def connect(self, _callback):
            return None

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.loadFinished = _Signal()
            self._page = type("Page", (), {"renderProcessTerminated": _Signal()})()

        def page(self):
            return self._page

    shell_loads = []
    window = QWidget()
    window._closing = False
    window.code = "000001"
    window.browser = None
    window._browser_epoch = 0
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: shell_loads.append("shell")
    window._on_chart_load_finished = lambda _ok: None
    window._set_status_message = lambda *_args, **_kwargs: None
    browser = _Browser(window.chart_host)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )

    coordinator.attach_browser(browser)
    window._browser_epoch += 1
    _fire_single_shot(coordinator.shell_load_timer)

    assert shell_loads == []
    assert not hasattr(window, "_browser_attach_diagnostics")
    assert window.chart_placeholder is not None
    coordinator.stop()
    window.close()


def test_deferred_shell_load_failure_rolls_back_and_keeps_placeholder(qt_application):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    statuses = []
    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._browser_epoch = 1
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: False
    window._on_chart_load_finished = lambda _ok: None
    window._set_status_message = lambda text, **kwargs: statuses.append((text, kwargs))
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    browser = QWidget(window.chart_host)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=QWidget,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    window.browser = browser
    started_at = time.perf_counter()
    coordinator.schedule_shell_load(
        browser,
        {"started": started_at, "load_queued": started_at, "load_scheduled": started_at},
    )

    _fire_single_shot(coordinator.shell_load_timer)

    assert window.browser is None
    assert window.chart_placeholder.text() == "图表组件初始化失败"
    assert statuses[-1][1]["tone"] == "error"
    assert window._browser_attach_diagnostics["load_shell_dispatch_ok"] is False
    coordinator.stop()
    window.close()


def test_stop_cancels_deferred_shell_load_and_returns_handed_off_page(qt_application):
    from PyQt6.QtWidgets import QWidget

    loads = []
    window = QWidget()
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: loads.append("shell")
    window._browser_epoch = 1
    browser = QWidget(window)
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=QWidget,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    window.browser = browser
    handed_off_page = object()
    coordinator.pending_page = handed_off_page
    started_at = time.perf_counter()
    coordinator.schedule_shell_load(
        browser,
        {"started": started_at, "load_queued": started_at, "load_scheduled": started_at},
    )

    _pending_browser, pending_page = coordinator.stop()
    qt_application.processEvents()

    assert pending_page is handed_off_page
    assert loads == []
    window.close()


def test_prewarmed_page_handoff_keeps_browser_in_final_host(qt_application):
    from PyQt6.QtWidgets import QVBoxLayout, QWidget

    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def disconnect(self, callback):
            self.callbacks.remove(callback)

    class _Page:
        def __init__(self):
            self._parent = None
            self._properties = {
                "klineShellReady": True,
                "klineShellHtmlBytes": 321,
            }
            self.renderProcessTerminated = _Signal()

        def property(self, name):
            return self._properties.get(name)

        def setParent(self, parent):
            self._parent = parent

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.loadFinished = _Signal()
            self.reparent_calls = []
            self._page = None

        def setParent(self, parent, *args):
            self.reparent_calls.append(parent)
            return super().setParent(parent, *args)

        def setPage(self, page):
            self._page = page

        def page(self):
            return self._page

    window = QWidget()
    window.container = QWidget(window)
    container_layout = QVBoxLayout(window.container)
    build_chart_host(window, container_layout)
    browser = _Browser(window.chart_host)
    page = _Page()
    window._closing = False
    window.code = "000001"
    window.browser = None
    window._browser_epoch = 0
    window._load_and_draw = lambda: None
    window._load_chart_shell = lambda: True
    window._on_chart_load_finished = lambda _ok: None
    window._apply_browser_surface_theme = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None

    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.start(browser_page=page)
    coordinator.context_timer.stop()
    coordinator.initialize_browser(staged=True)
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)
    _fire_single_shot(coordinator.browser_activate_timer)
    _fire_single_shot(coordinator.shell_load_timer)

    assert window.browser is browser
    assert browser.parentWidget() is window.chart_host
    assert browser.reparent_calls == []
    assert page._parent is None
    assert browser.property("klineShellReady") is True
    assert window._browser_attach_diagnostics["page_reused"] is True
    assert window._browser_attach_diagnostics["set_parent_ms"] < 1.0
    coordinator.stop()
    window.close()


def test_open_stage_coordinator_does_not_resolve_prebuilt_open_context(qt_application):
    from PyQt6.QtWidgets import QWidget

    calls = []
    window = QWidget()
    window._closing = False
    window._context_ready = False
    window._open_context_resolved = True
    window.code = "000001"
    window.name = "平安银行"
    window.vcp_data = {"source": "open-context"}
    window.browser = QWidget(window)
    window._resolve_vcp_context = lambda *_args: calls.append("resolve")
    window._refresh_header_context = lambda: calls.append("header")
    window._load_and_draw = lambda: None
    window._log = type("Log", (), {"debug": lambda *_args: None})()

    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=QWidget,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=50,
        initial_load_delay_ms=50,
    )
    window.browser = QWidget(window)

    coordinator.finish_deferred_context()

    assert calls == ["header"]
    assert window.vcp_data == {"source": "open-context"}
    assert window._context_diagnostics["prebuilt"] is True
    assert window._context_diagnostics["context_resolve_ms"] >= 0.0
    assert window._context_diagnostics["header_refresh_ms"] >= 0.0
    coordinator.stop()


def test_page_handoff_attach_failure_rolls_back_browser_and_disposes_unowned_page(
    qt_application,
    monkeypatch,
):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from ui.components.kline_window_manager import kline_manager

    class _Page:
        def __init__(self):
            self.deleted = False
            self.stopped = False
            self._properties = {"klineShellReady": True, "klineShellHtmlBytes": 123}

        def property(self, name):
            return self._properties.get(name)

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False
            self.stopped = False
            self._page = None

        def setPage(self, page):
            self._page = page

        def page(self):
            return self._page

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window.browser = None
    window._browser_epoch = 0
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    window._load_and_draw = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    browser = _Browser(window.chart_host)
    page = _Page()
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    coordinator.pending_page = page
    monkeypatch.setattr(kline_manager, "release_page", lambda *_args, **_kwargs: False)

    def _fail_after_window_assignment(owned_browser):
        window.browser = owned_browser
        raise RuntimeError("attach failed")

    monkeypatch.setattr(coordinator, "attach_browser_hierarchy", _fail_after_window_assignment)

    coordinator.initialize_browser(staged=True)
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)

    assert window.browser is None
    assert coordinator.pending_page is None
    assert page.stopped is True
    assert page.deleted is True
    assert browser.stopped is True
    assert browser.deleted is True
    coordinator.stop()
    window.close()


def test_recovery_attach_failure_disposes_new_browser_and_clears_window_reference(
    qt_application,
    monkeypatch,
):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    class _Browser(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.deleted = False
            self.stopped = False

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True
            super().deleteLater()

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._browser_epoch = 0
    window.chart_host = QWidget(window)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_placeholder = QLabel("loading", window.chart_host)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    window._load_and_draw = lambda: None
    window._set_status_message = lambda *_args, **_kwargs: None
    failed_browser = _Browser(window.chart_host)
    new_browser = _Browser(window.chart_host)
    window.browser = failed_browser
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=None,
        browser_factory=lambda _parent: new_browser,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    window.browser = failed_browser

    def _fail_after_window_assignment(owned_browser):
        window.browser = owned_browser
        raise RuntimeError("recovery attach failed")

    monkeypatch.setattr(coordinator, "attach_browser_hierarchy", _fail_after_window_assignment)

    assert coordinator.recover_browser(failed_browser) is True
    _fire_single_shot(coordinator.browser_create_timer)
    _fire_single_shot(coordinator.page_handoff_timer)
    _fire_single_shot(coordinator.browser_attach_timer)
    assert failed_browser.deleted is True
    assert new_browser.stopped is True
    assert new_browser.deleted is True
    assert window.browser is None
    coordinator.stop()
    window.close()
