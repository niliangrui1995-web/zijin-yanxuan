# -*- coding: utf-8 -*-
"""完整 K 线物理窗口池的租约与生命周期契约。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.services.kline_open_context import KlineOpenContext
from ui.components import kline_window_manager as manager_module
from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER, KlineLoadController
from ui.kline_window_pool_lifecycle import KLinePoolState, kline_pool_state_of
from ui.kline_window_stages import KLineOpenStageCoordinator


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks.remove(callback)


class _Page:
    def __init__(self) -> None:
        self.renderProcessTerminated = _Signal()


class _Browser:
    def __init__(self, host) -> None:
        self._host = host
        self._page = _Page()
        self.parent_changes = []
        self.page_changes = []
        self._properties = {
            manager_module.KLINE_SHELL_READY_PROPERTY: True,
            manager_module.KLINE_SHELL_HTML_BYTES_PROPERTY: 1024,
        }

    def parentWidget(self):
        return self._host

    def page(self):
        return self._page

    def property(self, name):
        return self._properties.get(name)

    def setParent(self, parent) -> None:
        self.parent_changes.append(parent)
        self._host = parent

    def setPage(self, page) -> None:
        self.page_changes.append(page)
        self._page = page


class _ReusableChart:
    instances = []

    def __init__(self, **kwargs) -> None:
        type(self).instances.append(self)
        self.kwargs = dict(kwargs)
        self.code = str(kwargs.get("code") or "")
        self.name = str(kwargs.get("name") or "")
        self.visible = False
        self.hidden_calls = 0
        self.close_calls = 0
        self.delete_calls = 0
        self.activation_calls = []
        self.reset_mode = "sync_ok"
        self.reset_callback = None
        self.complete_raises = False
        self.opacity_raises = False
        self.show_raises = False
        self.close_mode = "ok"
        self.final_dispose_raises = False
        self._closing = False
        self._shell_loaded = True
        self._browser_epoch = 1
        self._last_shell_load_epoch = 1
        self._last_shell_load_ok = True
        self.chart_host = object()
        self.browser = _Browser(self.chart_host)
        self._load_controller = KlineLoadController(window_id="pooled-physical-window")
        self._render_generation = 0
        self.destroyed = _Signal()
        self._pool_idle = False
        self._pool_tainted = False

    def transition(self, target: KLinePoolState, *, reason: str) -> KLinePoolState:
        self._pool_state = target
        self._pool_transition_reason = reason
        self._pool_idle = target is KLinePoolState.IDLE
        self._pool_tainted = target is KLinePoolState.TAINTED
        self._closing = target in {
            KLinePoolState.CLOSING,
            KLinePoolState.IDLE,
            KLinePoolState.DISPOSED,
        }
        return target

    def metaObject(self):
        return SimpleNamespace()

    def windowTitle(self) -> str:
        return f"{self.name} ({self.code})"

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        if self.show_raises:
            raise RuntimeError("show failed")
        self.visible = True

    def hide(self) -> None:
        self.visible = False
        self.hidden_calls += 1

    def raise_(self) -> None:
        return None

    def activateWindow(self) -> None:
        return None

    def close(self) -> bool:
        self.close_calls += 1
        if self.close_mode == "raise":
            raise RuntimeError("close failed")
        if self.close_mode == "false":
            return False
        self.visible = False
        self._closing = True
        return True

    def deleteLater(self) -> None:
        self.delete_calls += 1
        self.visible = False
        self._closing = True

    def setAttribute(self, *_args) -> None:
        return None

    def setWindowOpacity(self, _value) -> None:
        if self.opacity_raises:
            raise RuntimeError("opacity failed")

    def setGeometry(self, _geometry) -> None:
        return None

    def geometry(self):
        return object()

    def _browser_is_pool_healthy(self) -> bool:
        return bool(
            self._shell_loaded
            and self._last_shell_load_ok is True
            and self.browser.property(manager_module.KLINE_SHELL_READY_PROPERTY)
            and self.browser.parentWidget() is self.chart_host
        )

    def reset_browser_for_pool(self, callback) -> bool:
        self._closing = True
        if self.reset_mode == "raise":
            raise RuntimeError("reset failed")
        if self.reset_mode == "pending":
            self.reset_callback = callback
            return True
        if self.reset_mode == "not_started":
            return False
        callback(True)
        return True

    def complete_pool_return(self) -> bool:
        if self.complete_raises:
            raise RuntimeError("complete failed")
        self._pool_idle = True
        self._closing = True
        self.hide()
        return True

    def park_preheated_shell(self) -> bool:
        self._pool_idle = True
        self._closing = True
        return True

    def final_dispose(self) -> None:
        if self.final_dispose_raises:
            raise RuntimeError("final dispose failed")
        self.deleteLater()

    def activate_lease(self, *args, **kwargs) -> bool:
        """Fake 仅模拟租约身份；管理器仍必须显式调用该 API。"""
        self.activation_calls.append((args, dict(kwargs)))
        values = dict(kwargs)
        positional_names = (
            "main_window",
            "code",
            "name",
            "data_provider",
            "vcp_data",
            "code_list",
            "current_idx",
        )
        values.update(dict(zip(positional_names, args, strict=False)))
        self._closing = False
        self._pool_idle = False
        self.code = str(values.get("code") or "")
        self.name = str(values.get("name") or "")
        self._load_controller.reopen_lease()
        identity = self._load_controller.begin(self.code)
        self._render_generation = identity.generation
        return True


@pytest.fixture
def isolated_manager(monkeypatch):
    monkeypatch.setattr(manager_module.KLineWindowManager, "_instance", None)
    manager = manager_module.KLineWindowManager()
    manager._webengine_available = True
    _ReusableChart.instances = []
    yield manager
    manager._charts = []


def test_reopening_controller_lease_keeps_uuid_and_advances_generation():
    controller = KlineLoadController(window_id="stable-physical-window")
    old_identity = controller.begin("000001")
    assert controller.claim_frame(old_identity) is True

    controller.close()
    controller.reopen_lease()
    new_identity = controller.begin("000001")

    assert controller.window_id == old_identity.window_id == new_identity.window_id
    assert new_identity.generation == old_identity.generation + 1
    assert controller.closed is False
    assert controller.is_current(old_identity) is False
    assert controller.claim_frame(old_identity) is False
    assert controller.owns_current_frame(old_identity.code, old_identity.generation) is False
    assert controller.task_id("history") == "kline:stable-physical-window:2:history"


def test_stage_coordinator_reuses_timers_and_resets_timeline_for_new_lease(qt_application):
    from PyQt6.QtWidgets import QWidget

    window = QWidget()
    window._closing = False
    window.code = "000001"
    window._load_and_draw = lambda: None
    coordinator = KLineOpenStageCoordinator(
        window,
        open_started_at=10.0,
        browser_factory=lambda _parent: None,
        record_metric=lambda *_args, **_kwargs: None,
        emit_structured_log=lambda *_args, **_kwargs: None,
        browser_delay_ms=0,
        initial_load_delay_ms=0,
    )
    timers = (coordinator.context_timer, coordinator.browser_timer, coordinator.initial_load_timer)
    coordinator.record("shell_ready")
    coordinator.record("browser_ready")
    coordinator._initial_load_scheduled = True
    coordinator._last_load_identity = ("000001", 1)
    coordinator.pending_browser = object()
    coordinator.pending_page = object()
    window._context_ready = True
    window._load_requested = True

    coordinator.reset_for_lease(20.0)

    assert (coordinator.context_timer, coordinator.browser_timer, coordinator.initial_load_timer) == timers
    assert window._context_init_timer is timers[0]
    assert window._browser_init_timer is timers[1]
    assert window._initial_load_timer is timers[2]
    assert coordinator.open_started_at == 20.0
    assert coordinator.recorded_stages == set()
    assert coordinator.stage_diagnostics() == {
        "required_stages": list(KLINE_OPEN_STAGE_ORDER),
        "completed_stages": [],
        "pending_stages": [],
        "timings_ms": {},
        "observed_timings_ms": {},
        "complete": False,
    }
    assert coordinator.pending_browser is None
    assert coordinator.pending_page is None
    assert coordinator._initial_load_scheduled is False
    assert coordinator._last_load_identity is None
    assert window._context_ready is False
    assert window._load_requested is False
    assert coordinator.context_timer.isActive() is True
    assert coordinator.browser_timer.isActive() is False
    assert coordinator.initial_load_timer.isActive() is False

    coordinator.stop()
    window.close()


def test_idle_hidden_chart_is_not_counted_as_active(isolated_manager):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    chart.show()
    manager._charts = [chart]

    assert manager.release_chart(chart, cleanup_ok=True) is True

    assert chart.isVisible() is False
    assert chart.hidden_calls == 1
    assert manager.active_count == 0
    assert manager.active_chart_view_count == 0
    assert manager._take_ready_idle_chart() is chart
    assert manager.active_count == 0


def test_idle_pool_is_bounded_and_rejects_unclean_or_unhealthy_windows(isolated_manager):
    manager = isolated_manager
    first = _ReusableChart(code="000001", name="第一只")
    second = _ReusableChart(code="000002", name="第二只")

    assert manager.release_chart(first, cleanup_ok=True) is True
    assert manager.release_chart(second, cleanup_ok=True) is False
    assert manager._take_ready_idle_chart() is first

    cleanup_failed = _ReusableChart(code="000003", name="清理失败")
    assert manager.release_chart(cleanup_failed, cleanup_ok=False) is False
    assert manager._take_ready_idle_chart() is None

    unhealthy = _ReusableChart(code="000004", name="壳已失效")
    unhealthy._shell_loaded = False
    unhealthy._last_shell_load_ok = False
    unhealthy.browser._properties[manager_module.KLINE_SHELL_READY_PROPERTY] = False
    assert manager.release_chart(unhealthy, cleanup_ok=True) is False
    assert manager._take_ready_idle_chart() is None


def test_idle_checkout_never_reparents_browser_or_replaces_page(isolated_manager):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    original_browser = chart.browser
    original_host = original_browser.parentWidget()
    original_page = original_browser.page()

    assert manager.release_chart(chart, cleanup_ok=True) is True
    checked_out = manager._take_ready_idle_chart()

    assert checked_out is chart
    assert checked_out.browser is original_browser
    assert checked_out.browser.parentWidget() is original_host
    assert checked_out.browser.page() is original_page
    assert original_browser.parent_changes == []
    assert original_browser.page_changes == []


def test_shutdown_permanently_destroys_idle_chart(isolated_manager, monkeypatch):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    assert manager.release_chart(chart, cleanup_ok=True) is True
    monkeypatch.setattr(manager, "_join_webengine_preflight", lambda **_kwargs: True)

    assert manager.shutdown() is True

    assert chart.close_calls + chart.delete_calls >= 1
    assert manager._take_ready_idle_chart() is None
    assert manager.shutdown_diagnostics["clean"] is True
    assert manager.shutdown_diagnostics["managed_keepers"] == 0
    assert manager.shutdown_diagnostics["pending_open"] is False


def test_chart_window_exposes_explicit_lease_activation_api():
    from ui.kline_window_qt import KLineChartWindow

    assert callable(getattr(KLineChartWindow, "activate_lease", None))


def test_full_window_lease_diagnostics_prove_zero_hierarchy_mutation():
    from ui.kline_window_qt import KLineChartWindow

    source = inspect.getsource(KLineChartWindow.activate_lease)

    assert '"full_window_reused": True' in source
    assert '"page_reused": True' in source
    assert '"set_parent_ms": 0.0' in source
    assert '"set_page_ms": 0.0' in source
    assert "setParent(" not in source
    assert "setPage(" not in source


def test_reused_lease_replays_current_theme_market_and_glass_state():
    from ui.kline_window_qt import KLineChartWindow

    source = inspect.getsource(KLineChartWindow.activate_lease)

    assert "self._apply_chart_theme(animate=False)" in source
    assert "self._apply_chart_market_state()" in source
    assert "self._apply_chart_glass_mode()" in source


def test_reused_lease_skips_unchanged_static_qt_theme_restyle(monkeypatch):
    from ui import kline_window_header as header
    from ui.theme import theme_manager

    window = SimpleNamespace(
        _kline_qt_theme_signature=(theme_manager.current_theme_name, False),
        _magnetically_attached=False,
    )

    assert header.apply_qt_theme(window) is False
    window._magnetically_attached = True
    assert header._qt_theme_for_restyle(window) is theme_manager.current_theme

    window._magnetically_attached = False
    alternate_theme = next(
        name for name in theme_manager.theme_names() if name != theme_manager.current_theme_name
    )
    monkeypatch.setattr(theme_manager, "_current_name", alternate_theme)
    assert header._qt_theme_for_restyle(window) is theme_manager.current_theme


def test_reused_lease_refreshes_dynamic_header_only_once():
    from ui.kline_window_pool_lifecycle import _refresh_lease_chrome

    header_refreshes = []
    window = SimpleNamespace(
        code="000001",
        name="平安银行",
        setWindowTitle=lambda _title: None,
        _update_nav_buttons=lambda: None,
        _apply_qt_theme=lambda: False,
        _check_fav_status=lambda: header_refreshes.append("favorite_header"),
        _refresh_header_context=lambda: header_refreshes.append("explicit_header"),
        _set_status_message=lambda *_args, **_kwargs: None,
    )

    _refresh_lease_chrome(window)

    assert header_refreshes == ["favorite_header"]


def test_lease_signal_connect_is_transactional(monkeypatch):
    from ui import kline_window_qt as kline
    from ui.kline_window_qt import KLineChartWindow

    quotes = _Signal()

    class _FailingSignal:
        def connect(self, _callback):
            raise RuntimeError("theme connect failed")

    window = SimpleNamespace(
        _lease_signals_connected=False,
        _on_global_rt_quotes=lambda *_args: None,
        _on_theme_changed=lambda *_args: None,
    )
    monkeypatch.setattr(kline, "event_bus", SimpleNamespace(sig_rt_quotes=quotes))
    monkeypatch.setattr(kline, "theme_manager", SimpleNamespace(sig_theme_changed=_FailingSignal()))

    with pytest.raises(RuntimeError, match="theme connect failed"):
        KLineChartWindow._connect_lease_signals(window)

    assert quotes.callbacks == []
    assert window._lease_signals_connected is False


def test_lease_signal_disconnect_failure_is_not_reported_clean(monkeypatch):
    from ui import kline_window_qt as kline
    from ui.kline_window_qt import KLineChartWindow

    class _FailingDisconnectSignal:
        def disconnect(self, _callback):
            raise RuntimeError("disconnect failed")

    window = SimpleNamespace(
        _lease_signals_connected=True,
        _on_global_rt_quotes=lambda *_args: None,
        _on_theme_changed=lambda *_args: None,
    )
    monkeypatch.setattr(
        kline,
        "event_bus",
        SimpleNamespace(sig_rt_quotes=_FailingDisconnectSignal()),
    )
    monkeypatch.setattr(
        kline,
        "theme_manager",
        SimpleNamespace(sig_theme_changed=_FailingDisconnectSignal()),
    )

    assert KLineChartWindow._disconnect_lease_signals(window) is False
    assert window._lease_signals_connected is True


def test_final_dispose_never_downgrades_full_window_to_legacy_page_keeper():
    from ui.kline_window_qt import KLineChartWindow

    source = inspect.getsource(KLineChartWindow.final_dispose)

    assert "allow_page_reuse=False" in source


def test_close_cancels_without_gui_wait_and_rejects_pooling_while_workers_run(monkeypatch):
    from ui import kline_window_qt as kline

    calls = []
    window = SimpleNamespace(
        code="000001",
        _render_generation=7,
        _load_controller=SimpleNamespace(close=lambda: calls.append("controller_close")),
        _runtime_lifecycle=SimpleNamespace(begin_close=lambda: calls.append("runtime_close")),
    )
    monkeypatch.setattr(
        kline,
        "shutdown_task_lifecycle_for_owner",
        lambda owner, timeout_ms: calls.append(("wait", owner, timeout_ms)) or False,
    )

    assert kline._shutdown_kline_window_tasks(window) is False
    assert calls == [
        "controller_close",
        "runtime_close",
        ("wait", window, 0),
    ]


def test_close_rejects_pooling_when_cancelled_kline_worker_is_still_retiring(monkeypatch):
    from ui import kline_window_qt as kline

    window = SimpleNamespace(
        _load_controller=SimpleNamespace(close=lambda: None),
        _runtime_lifecycle=SimpleNamespace(begin_close=lambda: None),
        _active_kline_task_tickets={"submission-ticket"},
    )
    monkeypatch.setattr(kline, "shutdown_task_lifecycle_for_owner", lambda *_args, **_kwargs: True)

    assert kline._shutdown_kline_window_tasks(window) is False


def test_close_keeps_one_realtime_timer_and_uses_verified_cleanup_helper():
    from ui.kline_window_qt import KLineChartWindow

    source = inspect.getsource(KLineChartWindow.closeEvent)

    assert "_shutdown_kline_window_tasks(self" in source
    assert "self._rt_timer = None" not in source
    assert "allow_page_reuse=False" in source


def test_pool_return_normalizes_fullscreen_minimized_and_button_state():
    from ui.kline_window_qt import KLineChartWindow

    calls = []
    geometry = SimpleNamespace(isNull=lambda: False)
    button = SimpleNamespace(
        setText=lambda value: calls.append(("text", value)),
        setToolTip=lambda value: calls.append(("tooltip", value)),
    )
    window = SimpleNamespace(
        _fullscreen_geometry=geometry,
        _magnetically_attached=True,
        _snapping_to_main_window=True,
        btn_fullscreen=button,
        hide=lambda: calls.append("hide"),
        setWindowState=lambda state: calls.append(("state", state)),
        setMinimumSize=lambda width, height: calls.append(("minimum", width, height)),
        setMaximumSize=lambda width, height: calls.append(("maximum", width, height)),
        setGeometry=lambda value: calls.append(("geometry", value)),
    )

    KLineChartWindow._normalize_window_for_pool_return(window)

    assert calls[0] == "hide"
    assert ("geometry", geometry) in calls
    assert ("text", "□") in calls
    assert any(call[0] == "tooltip" and "F11" in call[1] for call in calls if isinstance(call, tuple))
    assert window._fullscreen_geometry is None
    assert window._magnetically_attached is False
    assert window._snapping_to_main_window is False


def test_activation_exception_permanently_disposes_checked_out_window(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _ReusableChart)
    chart = _ReusableChart(code="000001", name="平安银行")
    assert manager.release_chart(chart, cleanup_ok=True) is True

    def _raise_activation_error(*_args, **_kwargs):
        raise RuntimeError("activation failed")

    chart.activate_lease = _raise_activation_error

    with pytest.raises(RuntimeError, match="activation failed"):
        manager.open_chart(None, "000002", "万科A", object(), vcp_data={})

    assert chart.delete_calls == 1
    assert manager._take_ready_idle_chart() is None


def test_show_failure_permanently_disposes_unmanaged_window(isolated_manager, monkeypatch):
    manager = isolated_manager

    class _ShowFailChart(_ReusableChart):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.show_raises = True

    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _ShowFailChart)

    chart = manager.open_chart(None, "000001", "平安银行", object(), vcp_data={})

    assert chart is None
    created = _ReusableChart.instances[-1]
    assert created.delete_calls == 1
    assert manager.active_count == 0


def test_return_timeout_fail_closed_disposes_and_unblocks_manager(isolated_manager):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    chart.reset_mode = "pending"

    assert manager.release_chart(chart, cleanup_ok=True) is True
    assert manager._reclaiming_chart is chart

    manager._on_chart_return_timeout()

    assert manager._reclaiming_chart is None
    assert manager._take_ready_idle_chart() is None
    assert chart.delete_calls == 1


@pytest.mark.parametrize("failure_mode", ["reset_raises", "complete_raises"])
def test_return_exceptions_fail_closed_dispose_owned_window(isolated_manager, failure_mode):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    if failure_mode == "reset_raises":
        chart.reset_mode = "raise"
    else:
        chart.complete_raises = True

    assert manager.release_chart(chart, cleanup_ok=True) is True

    assert manager._reclaiming_chart is None
    assert manager._take_ready_idle_chart() is None
    assert chart.delete_calls == 1


def test_full_window_prewarm_rejects_missing_renderer_termination_guard(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    chart = _ReusableChart(code="000000", name="K线准备")
    manager._prewarm_window = chart
    manager._prewarm_started = True
    monkeypatch.setattr(manager, "_install_idle_chart_termination", lambda _chart: False)

    manager_module._finish_full_window_prewarm(manager, chart, 1.0, chart.geometry())

    assert manager._prewarm_window is None
    assert manager._idle_chart is None
    assert manager._prewarm_ready is False
    assert chart.delete_calls == 1


def test_full_window_prewarm_creation_exception_disposes_local_chart(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    chart = _ReusableChart(code="000000", name="K线准备")
    chart.opacity_raises = True
    manager._prewarm_main_window = object()
    manager._prewarm_started = True
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: lambda **_kwargs: chart)

    manager_module._create_hidden_full_window_keeper(manager, 1.0)

    assert manager._prewarm_window is None
    assert manager._prewarm_ready is False
    assert chart.delete_calls == 1


def test_tainted_full_window_prewarm_is_rejected_on_next_poll(isolated_manager):
    manager = isolated_manager
    chart = _ReusableChart(code="000000", name="K线准备")
    chart._pool_tainted = True
    manager._prewarm_window = chart
    manager._prewarm_started = True

    manager_module._poll_full_window_prewarm(manager, chart, 1.0, chart.geometry())

    assert manager._prewarm_window is None
    assert manager._prewarm_started is False
    assert manager._prewarm_ready is False
    assert manager._prewarm_failure == "full_window_failed"
    assert chart.delete_calls == 1


def test_full_window_renderer_requires_nonblocking_settle_window(monkeypatch):
    chart = SimpleNamespace()
    ticks = iter((10.0, 10.299, 10.301))
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: next(ticks))

    assert manager_module._full_window_renderer_settled(chart) is False
    assert manager_module._full_window_renderer_settled(chart) is False
    assert manager_module._full_window_renderer_settled(chart) is True


def test_keeper_count_exposes_broken_double_keeper_invariant(isolated_manager):
    manager = isolated_manager
    manager._idle_chart = object()
    manager._prewarm_view = object()

    assert manager.managed_webengine_keeper_count == 2


def test_idle_renderer_guard_disconnect_failure_rejects_reuse(isolated_manager):
    manager = isolated_manager
    chart = _ReusableChart(code="000001", name="平安银行")
    assert manager.release_chart(chart, cleanup_ok=True) is True

    def _fail_disconnect(_callback):
        raise RuntimeError("disconnect failed")

    chart.browser.page().renderProcessTerminated.disconnect = _fail_disconnect

    assert manager._take_ready_idle_chart() is None
    assert kline_pool_state_of(chart) is KLinePoolState.TAINTED
    assert chart.delete_calls == 1
    assert manager._idle_termination_callback is None


def test_legacy_keeper_guard_install_failure_is_never_reported_ready(
    isolated_manager,
    qt_application,
):
    manager = isolated_manager

    class _FailingConnectSignal:
        def connect(self, _callback):
            raise RuntimeError("guard connect failed")

    page = SimpleNamespace(
        renderProcessTerminated=_FailingConnectSignal(),
        setParent=lambda parent: setattr(page, "_parent", parent),
        parent=lambda: getattr(page, "_parent", None),
        setProperty=lambda name, value: setattr(page, name, value),
        property=lambda name: getattr(page, name, None),
    )

    assert manager.release_page(page, shell_ready=True, html_bytes=123) is False
    assert manager._prewarm_view is None
    assert manager.managed_webengine_keeper_ready is False
    assert manager._prewarm_failure == "termination_guard_failed"


def test_constructor_failure_disposes_warm_page_when_keeper_rejects_return(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    deleted = []
    stopped = []
    page = SimpleNamespace(
        renderProcessTerminated=_Signal(),
        property=lambda name: {
            manager_module.KLINE_SHELL_READY_PROPERTY: True,
            manager_module.KLINE_SHELL_HTML_BYTES_PROPERTY: 123,
        }.get(name),
        stop=lambda: stopped.append(True),
        setUpdatesEnabled=lambda _enabled: None,
        hide=lambda: None,
        deleteLater=lambda: deleted.append(True),
    )
    manager._prewarm_view = page
    manager._prewarm_ready = True
    assert manager_module._install_keeper_termination(manager, page) is True
    monkeypatch.setattr(
        manager_module,
        "_load_kline_window_class",
        lambda: lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("construct failed")),
    )
    monkeypatch.setattr(manager, "release_page", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="construct failed"):
        manager.open_chart(None, "000001", "平安银行", object(), vcp_data={})

    assert stopped == [True]
    assert deleted == [True]


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        ("active_close", "active_close_clean"),
        ("pooled_dispose", "pooled_dispose_clean"),
        ("preflight", "preflight_clean"),
        ("return_timer", "return_timer_clean"),
        ("prewarm_dispose", "prewarm_dispose_clean"),
    ],
)
def test_shutdown_aggregates_each_resource_failure(
    isolated_manager,
    monkeypatch,
    failure,
    diagnostic,
):
    manager = isolated_manager
    monkeypatch.setattr(manager, "_join_webengine_preflight", lambda **_kwargs: failure != "preflight")

    if failure == "active_close":
        chart = _ReusableChart(code="000001", name="活动窗口")
        chart.close_mode = "false"
        manager._charts = [chart]
    elif failure == "pooled_dispose":
        chart = _ReusableChart(code="000001", name="池窗口")
        chart.final_dispose_raises = True
        manager._idle_chart = chart
    elif failure == "return_timer":
        manager._chart_return_timer = SimpleNamespace(
            stop=lambda: None,
            deleteLater=lambda: (_ for _ in ()).throw(RuntimeError("timer delete failed")),
        )
    elif failure == "prewarm_dispose":
        manager._prewarm_view = SimpleNamespace(
            stop=lambda: None,
            setUpdatesEnabled=lambda _enabled: None,
            hide=lambda: None,
            setParent=lambda _parent: None,
            deleteLater=lambda: (_ for _ in ()).throw(RuntimeError("page delete failed")),
            setProperty=lambda *_args: None,
        )

    assert manager.shutdown() is False
    assert manager.shutdown_diagnostics[diagnostic] is False
    assert manager.shutdown_diagnostics["clean"] is False
    assert manager._prewarm_main_window is None
    assert manager.managed_webengine_keeper_count == 0


def test_same_stock_reuses_physical_window_and_stale_generation_cannot_commit(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _ReusableChart)
    provider = object()

    first = manager.open_chart(None, "000001", "平安银行", provider, vcp_data={})
    old_identity = first._load_controller.begin(first.code)
    assert first._load_controller.claim_frame(old_identity) is True
    window_id = first._load_controller.window_id
    browser = first.browser
    browser_host = browser.parentWidget()
    browser_page = browser.page()
    first._load_controller.close()
    assert manager.release_chart(first, cleanup_ok=True) is True

    reopened = manager.open_chart(None, "000001", "平安银行", provider, vcp_data={})

    assert reopened is first
    assert len(_ReusableChart.instances) == 1
    assert len(reopened.activation_calls) == 1
    assert reopened._load_controller.window_id == window_id
    assert reopened._load_controller.current_identity.generation == old_identity.generation + 1
    assert reopened._load_controller.is_current(old_identity) is False
    assert reopened._load_controller.claim_frame(old_identity) is False
    assert reopened._load_controller.owns_current_frame(
        old_identity.code,
        old_identity.generation,
    ) is False
    assert reopened.browser is browser
    assert reopened.browser.parentWidget() is browser_host
    assert reopened.browser.page() is browser_page
    assert browser.parent_changes == []
    assert browser.page_changes == []


def test_watchlist_open_arms_focus_guard_before_kline_window_activation(
    isolated_manager,
    monkeypatch,
):
    class OrderedChart(_ReusableChart):
        def show(self) -> None:
            order.append("show")
            super().show()

    manager = isolated_manager
    order = []
    main_window = object()
    context = KlineOpenContext(
        code="000001",
        name="平安银行",
        source_tab_key="watchlist",
    )
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: OrderedChart)
    monkeypatch.setattr(
        manager_module,
        "prepare_watchlist_kline_focus_repaint_guard",
        lambda window, *, source_tab_key, phase: order.append(
            (window, source_tab_key, phase)
        )
        or True,
        raising=False,
    )

    chart = manager.open_chart(
        main_window,
        "000001",
        "平安银行",
        object(),
        vcp_data={},
        open_context=context,
    )

    assert chart is not None
    assert order == [(main_window, "watchlist", "open"), "show"]


def test_watchlist_open_arms_focus_guard_before_webengine_preflight_notice(
    isolated_manager,
    monkeypatch,
):
    manager = isolated_manager
    manager._webengine_available = None
    order = []
    main_window = object()
    context = KlineOpenContext(
        code="000001",
        name="平安银行",
        source_tab_key="watchlist",
    )
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: True)
    monkeypatch.setattr(
        manager,
        "_notify_webengine_preparing",
        lambda *_args: order.append("preparing"),
    )
    monkeypatch.setattr(
        manager_module,
        "prepare_watchlist_kline_focus_repaint_guard",
        lambda window, *, source_tab_key, phase: order.append(
            (window, source_tab_key, phase)
        )
        or True,
        raising=False,
    )

    assert manager.open_chart(
        main_window,
        "000001",
        "平安银行",
        object(),
        vcp_data={},
        open_context=context,
    ) is None

    assert order == [(main_window, "watchlist", "open"), "preparing"]
