# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QWidget

from infra.diagnostics import qt_webengine_preflight as preflight_module
from ui.components import kline_window_manager as manager_module
from ui.kline_pool_state import KLinePoolState


def _manager():
    manager = manager_module.KLineWindowManager()
    existing_thread = getattr(manager, "_webengine_preflight_thread", None)
    existing_event = getattr(manager, "_webengine_preflight_cancel_event", None)
    if existing_event is not None:
        existing_event.set()
    if existing_thread is not None and existing_thread.is_alive():
        existing_thread.join(1.0)
    assert existing_thread is None or not existing_thread.is_alive()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._prewarm_cancelled = False
    manager._prewarm_hidden_view_enabled = False
    manager._prewarm_ready = False
    manager._prewarm_failure = ""
    manager._prewarm_load_callback = None
    manager._prewarm_termination_callback = None
    manager._webengine_available = None
    manager._webengine_failure = ""
    manager._webengine_preflight_diagnostics = {}
    manager._webengine_preflight_started = False
    manager._webengine_preflight_thread = None
    manager._webengine_preflight_run = None
    manager._webengine_preflight_cancel_event = threading.Event()
    manager._webengine_preflight_lock = threading.RLock()
    manager._shutting_down = False
    manager._pending_open.clear()
    return manager


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)


class _Chart:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.destroyed = _Signal()
        self.visible = True
        self.shown = False
        self.closed = False
        self.raised = False
        self.activated = False

    def transition(self, target: KLinePoolState, *, reason: str) -> KLinePoolState:
        self._pool_state = target
        self._pool_transition_reason = reason
        self._closing = target in {
            KLinePoolState.CLOSING,
            KLinePoolState.IDLE,
            KLinePoolState.DISPOSED,
        }
        return target

    def isVisible(self):
        return self.visible

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True
        self.visible = False
        self._closing = True
        return True

    def windowTitle(self):
        return "Old Chart"

    def raise_(self):
        self.raised = True

    def activateWindow(self):
        self.activated = True


def test_hidden_prewarm_env_values(monkeypatch):
    monkeypatch.delenv(manager_module.HIDDEN_PREWARM_ENV, raising=False)
    assert not manager_module._hidden_prewarm_enabled()


class _FallbackPath:
    def __init__(self, exists=True):
        self._exists = exists

    def resolve(self):
        return self

    @property
    def parents(self):
        return [self, self]

    def __truediv__(self, other):
        return self

    def exists(self):
        return self._exists


def test_load_kline_window_class_fallback_and_validation(monkeypatch):
    original_import = builtins.__import__

    def missing_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ui.kline_window_qt":
            exc = ModuleNotFoundError("missing")
            exc.name = "ui.kline_window_qt"
            raise exc
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_import)
    monkeypatch.setattr(manager_module, "Path", lambda value: _FallbackPath(exists=False))
    with pytest.raises(ModuleNotFoundError):
        manager_module._load_kline_window_class()

    monkeypatch.setattr(manager_module, "Path", lambda value: _FallbackPath(exists=True))
    monkeypatch.setattr(manager_module.importlib.util, "spec_from_file_location", lambda *args: None)
    with pytest.raises(ModuleNotFoundError):
        manager_module._load_kline_window_class()

    fake_class = object()
    module = SimpleNamespace()

    class Loader:
        def exec_module(self, target):
            target.KLineChartWindow = fake_class

    spec = SimpleNamespace(name="ui.kline_window_qt", loader=Loader())
    missing = object()
    previous_module = sys.modules.get(spec.name, missing)
    monkeypatch.setattr(manager_module.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(manager_module.importlib.util, "module_from_spec", lambda value: module)
    try:
        assert manager_module._load_kline_window_class() is fake_class
        assert sys.modules[spec.name] is module
    finally:
        if previous_module is missing:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module


def test_load_kline_window_class_reraises_unrelated_import_error(monkeypatch):
    original_import = builtins.__import__

    def missing_dependency(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ui.kline_window_qt":
            exc = ModuleNotFoundError("dependency")
            exc.name = "dependency"
            raise exc
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_dependency)
    with pytest.raises(ModuleNotFoundError):
        manager_module._load_kline_window_class()
    for value in ("1", "TRUE", " yes ", "on"):
        monkeypatch.setenv(manager_module.HIDDEN_PREWARM_ENV, value)
        assert manager_module._hidden_prewarm_enabled()
    monkeypatch.setenv(manager_module.HIDDEN_PREWARM_ENV, "no")
    assert not manager_module._hidden_prewarm_enabled()


def test_prewarm_schedules_once_and_handles_timer_failure(monkeypatch):
    import PyQt6.QtCore as qtcore

    scheduled = []

    class Timer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((delay, callback))

    monkeypatch.setattr(qtcore, "QTimer", Timer)
    manager = _manager()
    assert manager.prewarm(delay_ms=-1, hidden_view=True)
    assert scheduled[0][0] == 0
    assert manager._prewarm_hidden_view_enabled is True
    assert not manager.prewarm()

    manager = _manager()
    scheduled.clear()
    preflight_calls = []
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: preflight_calls.append(True) or True)
    assert manager.prewarm(delay_ms=8000, hidden_view=False)
    assert manager_module.WEBENGINE_PREFLIGHT_IDLE_START_MS >= 1000
    assert [delay for delay, _callback in scheduled] == [manager_module.WEBENGINE_PREFLIGHT_IDLE_START_MS, 8000]
    scheduled[0][1]()
    assert preflight_calls == [True]

    class BrokenTimer:
        @staticmethod
        def singleShot(*args):
            raise RuntimeError("bad")

    manager = _manager()
    monkeypatch.setattr(qtcore, "QTimer", BrokenTimer)
    assert not manager.prewarm(hidden_view=False)
    assert manager._prewarm_started is False
    assert manager._prewarm_cancelled is True


def test_shutdown_closes_charts_and_cancels_delayed_prewarm():
    from PyQt6.QtWebEngineCore import QWebEnginePage

    manager = _manager()
    charts = [_Chart(), _Chart()]
    view = _Page()
    manager._charts = charts
    manager._prewarm_view = view
    manager._prewarm_started = True

    assert manager.shutdown()
    assert manager.shutdown()

    assert all(chart.closed for chart in charts)
    assert manager._charts == []
    assert manager._shutting_down is True
    assert manager._prewarm_cancelled is True
    assert manager._prewarm_started is False
    assert view.deleted
    assert view.trigger_actions == [QWebEnginePage.WebAction.Stop]
    assert manager.shutdown_diagnostics["prewarm_dispose_clean"] is True
    assert manager.shutdown_diagnostics["clean"] is True
    assert not manager.prewarm(delay_ms=0)
    manager._shutting_down = False


def test_application_lifecycle_owner_routes_quit_to_manager_shutdown(qt_application):
    manager = _manager()
    chart = _Chart()
    view = _Page()
    manager._charts = [chart]
    manager._prewarm_view = view
    manager._prewarm_started = True

    manager_module._ensure_manager_application_lifecycle_owner(manager)

    owner = manager._application_lifecycle_owner
    assert owner is not None
    assert owner.parent() is qt_application
    owner._on_application_quit()

    assert chart.closed is True
    assert view.deleted is True
    assert manager._shutting_down is True
    assert manager.shutdown_diagnostics["clean"] is True
    assert manager._application_lifecycle_owner is None
    manager._shutting_down = False


def test_webengine_preflight_cache_metrics_and_async_thread(monkeypatch):
    metrics = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    calls = []
    monkeypatch.setattr(
        manager_module,
        "check_qt_webengine_available",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "elapsed_ms": 12.5},
    )
    manager = _manager()
    assert manager._ensure_webengine_available() is True
    assert manager._ensure_webengine_available() is True
    assert len(calls) == 1 and metrics
    assert calls[0]["timeout_s"] == manager_module.WEBENGINE_PREFLIGHT_TIMEOUT_S == 15
    manager._webengine_available = None
    monkeypatch.setattr(manager_module, "check_qt_webengine_available", lambda **kwargs: {"ok": False})
    assert manager._ensure_webengine_available() is False
    assert manager._webengine_failure == "unknown"


def test_webengine_preflight_retries_one_clean_timeout_then_succeeds(monkeypatch):
    results = iter(
        (
            {"ok": False, "timeout": True, "process_cleanup_ok": True, "reason": "timeout>15s"},
            {"ok": True, "process_cleanup_ok": True, "elapsed_ms": 20.0},
        )
    )
    calls = []
    monkeypatch.setattr(manager_module, "WEBENGINE_PREFLIGHT_RETRY_DELAY_S", 0)
    monkeypatch.setattr(
        manager_module,
        "check_qt_webengine_available",
        lambda **kwargs: calls.append(kwargs) or next(results),
    )
    manager = _manager()

    assert manager._ensure_webengine_available() is True
    assert len(calls) == 2
    assert manager._webengine_preflight_diagnostics["attempt_count"] == 2
    assert manager._webengine_preflight_diagnostics["attempts"][0]["timeout"] is True


@pytest.mark.parametrize("cleanup_ok, expected_calls", [(True, 2), (False, 1)])
def test_webengine_preflight_bounds_timeout_retry(monkeypatch, cleanup_ok, expected_calls):
    calls = []
    monkeypatch.setattr(manager_module, "WEBENGINE_PREFLIGHT_RETRY_DELAY_S", 0)
    monkeypatch.setattr(
        manager_module,
        "check_qt_webengine_available",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": False,
            "timeout": True,
            "process_cleanup_ok": cleanup_ok,
            "reason": "timeout>15s",
        },
    )
    manager = _manager()

    assert manager._ensure_webengine_available() is False
    assert len(calls) == expected_calls
    assert manager._webengine_preflight_diagnostics["attempt_count"] == expected_calls
    assert manager._webengine_failure == "timeout>15s"

    started = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            started.append((name, daemon))

        def start(self):
            self.target()

    manager = _manager()
    monkeypatch.setattr(manager_module.threading, "Thread", Thread)
    monkeypatch.setattr(
        manager,
        "_ensure_webengine_available",
        lambda _cancellation_event=None, **_kwargs: True,
    )
    assert manager._start_webengine_preflight_async()
    assert manager._webengine_preflight_started is False
    assert manager._webengine_preflight_thread is None
    assert started == [("KLineWebEnginePreflight", False)]
    manager._webengine_available = True
    assert not manager._start_webengine_preflight_async()
    manager._webengine_available = None
    manager._webengine_preflight_started = True
    assert not manager._start_webengine_preflight_async()


def test_shutdown_cancels_slow_preflight_without_state_writeback(monkeypatch):
    class BlockingProcess:
        def __init__(self):
            self.returncode = None
            self.started = threading.Event()
            self.terminated = False
            self.killed = False
            self.waited = False

        def communicate(self, timeout=None):
            self.started.set()
            if self.killed:
                return "cancel-out", "cancel-err"
            if self.terminated:
                raise preflight_module.subprocess.TimeoutExpired("preflight", timeout)
            time.sleep(min(float(timeout or 0.01), 0.01))
            raise preflight_module.subprocess.TimeoutExpired("preflight", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self):
            self.waited = True
            return self.returncode

    process = BlockingProcess()
    metrics = []
    logs = []
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        preflight_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        manager_module,
        "record_metric",
        lambda *args, **kwargs: metrics.append((args, kwargs)),
    )
    monkeypatch.setattr(
        manager_module,
        "emit_structured_log",
        lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    manager = _manager()
    owned_thread = None

    try:
        assert manager._start_webengine_preflight_async()
        assert process.started.wait(1.0)
        owned_thread = manager._webengine_preflight_thread
        assert owned_thread is not None
        assert owned_thread.daemon is False

        started_at = time.perf_counter()
        assert manager.shutdown()
        elapsed_s = time.perf_counter() - started_at

        assert elapsed_s < 1.0
        assert process.terminated and process.killed and process.waited
        assert process.poll() == -9
        assert not owned_thread.is_alive()
        assert manager._webengine_preflight_thread is None
        assert manager._webengine_preflight_started is False
        assert not any(
            thread.name == "KLineWebEnginePreflight" and thread.is_alive()
            for thread in threading.enumerate()
        )
        assert manager._webengine_available is None
        assert manager._webengine_failure == ""
        assert any(
            args[0] == "kline_webengine_preflight_shutdown_ms"
            and kwargs["tags"] == {"clean": "true"}
            for args, kwargs in metrics
        )
        assert any(
            args[0] == "kline.webengine_preflight_shutdown" and kwargs["clean"] is True
            for args, kwargs in logs
        )
    finally:
        if owned_thread is not None and owned_thread.is_alive():
            manager.shutdown()
        manager._shutting_down = False
        manager._webengine_preflight_cancel_event = threading.Event()


def test_preflight_shutdown_reports_bounded_join_failure(monkeypatch):
    class StuckThread:
        def __init__(self):
            self.join_timeout = None

        def join(self, timeout):
            self.join_timeout = timeout

        @staticmethod
        def is_alive():
            return True

    metrics = []
    logs = []
    monkeypatch.setattr(
        manager_module,
        "record_metric",
        lambda *args, **kwargs: metrics.append((args, kwargs)),
    )
    monkeypatch.setattr(
        manager_module,
        "emit_structured_log",
        lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    manager = _manager()
    thread = StuckThread()
    manager._webengine_preflight_thread = thread
    manager._webengine_preflight_started = True

    try:
        assert not manager._join_webengine_preflight(timeout_ms=25)

        assert thread.join_timeout == pytest.approx(0.025)
        assert manager._webengine_preflight_cancel_event.is_set()
        assert manager._webengine_preflight_thread is thread
        assert manager._webengine_preflight_started is True
        assert metrics[-1][1]["tags"] == {"clean": "false"}
        assert logs[-1][1]["clean"] is False
    finally:
        manager._webengine_preflight_thread = None
        manager._webengine_preflight_started = False
        manager._webengine_preflight_cancel_event = threading.Event()


def test_preflight_shutdown_reports_unreaped_child_without_state_writeback(monkeypatch):
    entered = threading.Event()
    warnings = []

    def _unclean_preflight(*, cancellation_event=None, **_kwargs):
        entered.set()
        assert cancellation_event is not None
        assert cancellation_event.wait(1.0)
        return {
            "ok": False,
            "reason": "cancelled",
            "cancelled": True,
            "process_cleanup_ok": False,
        }

    monkeypatch.setattr(manager_module, "check_qt_webengine_available", _unclean_preflight)
    monkeypatch.setattr(manager_module.log, "warning", warnings.append)
    manager = _manager()

    try:
        assert manager._start_webengine_preflight_async()
        assert entered.wait(1.0)

        assert manager.shutdown() is False

        assert manager._webengine_available is None
        assert manager._webengine_failure == ""
        assert manager._webengine_preflight_thread is None
        assert manager._webengine_preflight_run.process_cleanup_ok is False
        assert warnings == [
            "[KLine] WebEngine preflight child was not reaped before the hard cleanup deadline"
        ]
    finally:
        manager._webengine_preflight_run = None
        manager._shutting_down = False
        manager._webengine_preflight_cancel_event = threading.Event()


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, *args):
        self.messages.append(args)


def test_all_manager_notifications_emit_toast_status_metric_and_log(monkeypatch):
    import ui.components.toast_widget as toast

    toasts = []
    metrics = []
    logs = []
    monkeypatch.setattr(toast, "show_toast", lambda *args, **kwargs: toasts.append((args, kwargs)))
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    monkeypatch.setattr(manager_module, "emit_structured_log", lambda *args, **kwargs: logs.append((args, kwargs)))
    status = _StatusBar()
    main = SimpleNamespace(statusBar=lambda: status)
    manager = _manager()
    manager._webengine_failure = "sandbox"
    manager._notify_webengine_unavailable(main, " 1 ", " One ")
    manager._notify_webengine_preparing(main, " 2 ", " Two ")
    manager._notify_kline_module_unavailable(main, " 3 ", " Three ", ImportError("missing"))
    assert len(toasts) == len(status.messages) == len(metrics) == len(logs) == 3

    monkeypatch.setattr(toast, "show_toast", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad")))
    bad_main = SimpleNamespace(statusBar=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    manager._notify_webengine_unavailable(bad_main, "1", "one")
    manager._notify_webengine_preparing(None, "2", "two")
    manager._notify_kline_module_unavailable(None, "3", "three", RuntimeError())


class _Timer:
    def __init__(self):
        self.timeout = _Signal()
        self.single = None
        self.started = None
        self.stopped = False
        self.deleted = False

    def setSingleShot(self, value):
        self.single = value

    def start(self, ttl):
        self.started = ttl

    def stop(self):
        self.stopped = True

    def deleteLater(self):
        self.deleted = True


class _View(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hidden = False
        self.deleted = False
        self.object_name = None
        self.html = None
        self.updates_enabled = None
        self.loadFinished = _Signal()

    def hide(self):
        self.hidden = True

    def setParent(self, parent):
        super().setParent(parent)

    def deleteLater(self):
        self.deleted = True

    def setObjectName(self, name):
        self.object_name = name

    def resize(self, *args):
        self.size = args

    def setHtml(self, *args):
        self.html = args

    def setUpdatesEnabled(self, enabled):
        self.updates_enabled = bool(enabled)


class _Page:
    def __init__(self, parent=None):
        self._parent = parent
        self.deleted = False
        self.object_name = None
        self.html = None
        self.loadFinished = _Signal()
        self.renderProcessTerminated = _Signal()
        self._properties = {}
        self.trigger_actions = []

    def setObjectName(self, name):
        self.object_name = name

    def setHtml(self, *args):
        self.html = args

    def setProperty(self, name, value):
        self._properties[name] = value

    def property(self, name):
        return self._properties.get(name)

    def setParent(self, parent):
        self._parent = parent

    def triggerAction(self, action):
        self.trigger_actions.append(action)

    def parent(self):
        return self._parent

    def deleteLater(self):
        self.deleted = True


def test_prewarm_keeper_is_bounded_and_disposed_at_shutdown(monkeypatch):
    metrics = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    manager = _manager()
    view = _Page()
    manager._prewarm_view = view
    manager._prewarm_started = True
    manager._prewarm_ready = True
    assert manager.managed_webengine_keeper_count == 1
    assert manager.managed_webengine_keeper_ready is True
    manager._charts = [SimpleNamespace(browser=object()), SimpleNamespace(browser=None)]
    assert manager.active_chart_view_count == 1
    manager._dispose_prewarm_resource(reason="test")
    assert view.deleted and manager._prewarm_view is None and metrics
    assert manager.managed_webengine_keeper_count == 0
    assert manager.managed_webengine_keeper_ready is False
    manager._dispose_prewarm_resource(reason="empty")


def test_ready_keeper_page_round_trip_preserves_loaded_page(qt_application):
    manager = _manager()
    page = _Page(qt_application)
    manager_module._set_browser_property(page, manager_module.KLINE_SHELL_READY_PROPERTY, True)
    manager._prewarm_view = page
    manager._prewarm_ready = True

    borrowed = manager._take_ready_prewarm_page()

    assert borrowed is page
    assert manager.managed_webengine_keeper_count == 0

    assert manager.release_page(page, shell_ready=True, html_bytes=123) is True
    assert manager._prewarm_view is page
    assert page.parent() is qt_application
    assert page.property(manager_module.KLINE_SHELL_HTML_BYTES_PROPERTY) == 123
    assert manager.managed_webengine_keeper_count == 1

    manager._dispose_prewarm_resource(reason="test_round_trip")


def test_run_prewarm_preflight_skip_cancel_and_success(monkeypatch):
    manager = _manager()
    starts = []
    retries = []
    manager._prewarm_started = True
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: starts.append(True) or True)
    monkeypatch.setattr(manager_module, "_schedule_prewarm_retry", lambda target: retries.append(target))
    manager._run_prewarm()
    assert starts and retries == [manager] and manager._prewarm_started is True
    manager._webengine_available = False
    manager._prewarm_started = True
    manager._run_prewarm()
    assert manager._prewarm_started is False
    manager._webengine_available = True
    manager._prewarm_started = True
    manager._prewarm_hidden_view_enabled = False
    manager._run_prewarm()
    assert manager._prewarm_started is False

    manager._prewarm_hidden_view_enabled = True
    manager._prewarm_cancelled = True
    manager._prewarm_started = True
    manager._run_prewarm()
    assert manager._prewarm_started is False

    import PyQt6.QtWebEngineCore as webengine_core
    import PyQt6.QtWidgets as widgets

    monkeypatch.setattr(webengine_core, "QWebEnginePage", _Page)
    monkeypatch.setattr(
        widgets, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(closingDown=lambda: False))
    )
    manager._prewarm_cancelled = False
    manager._prewarm_started = True
    manager._run_prewarm()
    assert isinstance(manager._prewarm_view, _Page)
    assert manager._prewarm_view.object_name == "klinePrewarmPage"
    assert manager._prewarm_started is True
    assert manager.managed_webengine_keeper_ready is False
    manager._prewarm_view.loadFinished.callbacks[0](True)
    assert manager._prewarm_started is False
    assert manager.managed_webengine_keeper_ready is True

    monkeypatch.setattr(widgets, "QApplication", SimpleNamespace(instance=lambda: None))
    manager._prewarm_view = None
    manager._prewarm_started = True
    manager._run_prewarm()
    assert manager._prewarm_started is False


def test_run_prewarm_records_page_only_mode(monkeypatch):
    manager = _manager()
    manager._webengine_available = True
    manager._prewarm_started = True
    manager._prewarm_hidden_view_enabled = True
    manager._prewarm_main_window = None
    metrics = []
    created = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    monkeypatch.setattr(
        manager_module,
        "_create_hidden_prewarm_view",
        lambda target, _started_at: created.append(target),
    )

    manager._run_prewarm()

    assert created == [manager]
    assert metrics == [
        (("kline_webengine_prewarm_mode", 1), {"unit": "count", "tags": {"mode": "page_only"}})
    ]


def test_hidden_prewarm_defers_html_load_to_next_event_turn(monkeypatch):
    import PyQt6.QtCore as qtcore
    import PyQt6.QtWebEngineCore as webengine_core
    import PyQt6.QtWidgets as widgets

    scheduled = []

    class Timer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((delay, callback))

    monkeypatch.setattr(webengine_core, "QWebEnginePage", _Page)
    monkeypatch.setattr(qtcore, "QTimer", Timer)
    monkeypatch.setattr(
        widgets, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(closingDown=lambda: False))
    )
    manager = _manager()
    manager._webengine_available = True
    manager._prewarm_started = True
    manager._prewarm_hidden_view_enabled = True

    manager._run_prewarm()

    view = manager._prewarm_view
    assert isinstance(view, _Page)
    assert view.html is None
    assert [delay for delay, _callback in scheduled] == [0]

    scheduled.pop(0)[1]()

    assert view.html is not None
    assert [delay for delay, _callback in scheduled] == [manager_module.WEBENGINE_PREWARM_LOAD_TIMEOUT_MS]


def test_hidden_prewarm_load_failure_releases_unready_keeper(monkeypatch):
    import PyQt6.QtWebEngineCore as webengine_core
    import PyQt6.QtWidgets as widgets

    monkeypatch.setattr(webengine_core, "QWebEnginePage", _Page)
    manager = _manager()
    manager._webengine_available = True
    manager._prewarm_started = True
    manager._prewarm_hidden_view_enabled = True
    monkeypatch.setattr(
        widgets, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(closingDown=lambda: False))
    )

    manager._run_prewarm()
    view = manager._prewarm_view
    assert view is not None and manager._prewarm_started is True

    view.loadFinished.callbacks[0](False)

    assert view.deleted is True
    assert manager.managed_webengine_keeper_count == 0
    assert manager.managed_webengine_keeper_ready is False
    assert manager._prewarm_started is False
    assert manager._prewarm_failure == "load_failed"


def test_hidden_prewarm_start_failures_resume_pending_open(monkeypatch):
    import PyQt6.QtWebEngineCore as webengine_core
    import PyQt6.QtWidgets as widgets

    resumes = []
    manager = _manager()
    monkeypatch.setattr(manager._pending_open, "request_resume", lambda: resumes.append("resume"))

    class BrokenLoadPage(_Page):
        def setHtml(self, *args):
            raise RuntimeError("load failed")

    view = BrokenLoadPage()
    manager._prewarm_view = view
    manager._prewarm_started = True
    manager_module._load_hidden_prewarm_view(manager, view)

    assert manager._prewarm_view is None
    assert resumes == ["resume"]

    class BrokenPage:
        def __init__(self, _parent=None):
            raise RuntimeError("create failed")

    monkeypatch.setattr(webengine_core, "QWebEnginePage", BrokenPage)
    monkeypatch.setattr(
        widgets, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(closingDown=lambda: False))
    )
    manager._prewarm_started = True
    manager_module._create_hidden_prewarm_view(manager, time.perf_counter())

    assert manager._prewarm_view is None
    assert resumes == ["resume", "resume"]


def test_remove_chart_post_close_and_active_count(monkeypatch):
    manager = _manager()
    alive = _Chart(code="1")
    dead = _Chart(code="2")
    dead.visible = False
    manager._charts = [alive, dead]
    manager._remove_chart_ref(dead)
    assert manager._charts == [alive]
    manager._charts = [alive, dead]
    assert manager.active_count == 1
    alive.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("deleted"))
    assert manager.active_count == 0


def test_open_chart_unavailable_module_error_limit_and_success(monkeypatch):
    manager = _manager()
    notices = []
    monkeypatch.setattr(manager, "_notify_webengine_unavailable", lambda *args: notices.append("unavailable"))
    manager._webengine_available = False
    assert manager.open_chart(None, "1", "one", object()) is None
    assert notices == ["unavailable"]

    manager._webengine_available = True
    monkeypatch.setattr(
        manager_module,
        "_load_kline_window_class",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("missing")),
    )
    monkeypatch.setattr(manager, "_notify_kline_module_unavailable", lambda *args: notices.append("module"))
    assert manager.open_chart(None, "1", "one", object()) is None
    assert notices[-1] == "module"

    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)
    metrics = []
    logs = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    monkeypatch.setattr(manager_module, "emit_structured_log", lambda *args, **kwargs: logs.append((args, kwargs)))
    old = [_Chart(code=str(index)) for index in range(manager_module.MAX_CHART_WINDOWS - 1)]
    hidden = _Chart(code="hidden")
    hidden.visible = False
    hidden.metaObject = lambda: None
    manager._charts = [hidden, *old]
    manager._prewarm_started = True
    chart = manager.open_chart(
        None,
        " 000001 ",
        " Ping ",
        object(),
        vcp_data=None,
        code_list=None,
        current_idx=2,
    )
    assert isinstance(chart, _Chart) and chart.shown and chart.raised and chart.activated
    assert hidden.closed
    assert chart.kwargs["vcp_data"] == {"code": " 000001 ", "name": " Ping "}
    assert chart.kwargs["code_list"] == []
    assert chart.kwargs["browser_page"] is None
    assert manager._prewarm_started is True
    assert manager._prewarm_cancelled is False
    assert len(manager._charts) == manager_module.MAX_CHART_WINDOWS
    assert metrics and logs
    chart.destroyed.callbacks[0]()


def test_chart_limit_fails_closed_when_oldest_refuses_close(monkeypatch):
    class RefusingChart(_Chart):
        def close(self):
            return False

    manager = _manager()
    manager._webengine_available = True
    oldest = RefusingChart()
    manager._charts = [oldest, *[_Chart() for _ in range(manager_module.MAX_CHART_WINDOWS - 1)]]
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)

    assert manager.open_chart(None, "000001", "Ping", object()) is None
    assert manager._charts[0] is oldest
    assert len(manager._charts) == manager_module.MAX_CHART_WINDOWS


def test_open_chart_preserves_same_stock_multi_window_behavior(monkeypatch):
    manager = _manager()
    manager._webengine_available = True
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)

    first = manager.open_chart(None, "000001", "平安银行", object(), vcp_data={})
    second = manager.open_chart(None, "000001", "平安银行", object(), vcp_data={})

    assert first is not second
    assert manager._charts == [first, second]


def test_open_chart_waits_for_cn_provider_but_allows_asian_cache_path(monkeypatch):
    manager = _manager()
    manager._webengine_available = True
    notices = []
    monkeypatch.setattr(manager, "notify_data_provider_preparing", lambda *args: notices.append(args[1]))
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)

    assert manager.open_chart(None, "000001", "Ping", None) is None
    assert notices == ["000001"]
    assert manager._charts == []

    chart = manager.open_chart(None, "2330.TW", "TSMC", None)
    assert isinstance(chart, _Chart)
    assert chart.kwargs["data_provider"] is None


def test_open_chart_is_blocked_after_manager_shutdown(monkeypatch):
    manager = _manager()
    manager._webengine_available = True
    manager.shutdown()
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)

    assert manager.open_chart(None, "000001", "平安银行", object()) is None
    assert manager._charts == []


def test_open_chart_raise_failures_are_contained(monkeypatch):
    class BrokenChart(_Chart):
        def raise_(self):
            raise RuntimeError("deleted")

    manager = _manager()
    manager._webengine_available = True
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: BrokenChart)
    assert manager.open_chart(None, "1", "one", object(), vcp_data={}) is not None


def test_pending_kline_open_is_latest_only_and_resumes_once(monkeypatch):
    manager = _manager()
    manager._webengine_available = None
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: True)
    monkeypatch.setattr(manager, "_notify_webengine_preparing", lambda *_args: None)
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _Chart)

    assert manager.open_chart(None, "000001", "First", object()) is None
    assert manager.open_chart(None, "000002", "Latest", object()) is None
    assert manager._pending_open.request.code == "000002"

    manager._webengine_available = True
    manager._pending_open.resume()

    assert manager._pending_open.request is None
    assert len(manager._charts) == 1
    assert manager._charts[0].kwargs["code"] == "000002"
    manager._pending_open.resume()
    assert len(manager._charts) == 1


def test_pending_open_bridge_resumes_on_gui_thread(qt_application):
    gui_thread_id = threading.get_ident()
    callback_threads = []
    bridge = manager_module._create_pending_open_bridge(
        lambda: callback_threads.append(threading.get_ident())
    )
    assert bridge is not None

    worker = threading.Thread(target=bridge.requested.emit)
    worker.start()
    worker.join(1.0)
    deadline = time.monotonic() + 1.0
    while not callback_threads and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.001)

    assert callback_threads == [gui_thread_id]
    bridge.deleteLater()


def test_pending_kline_open_clears_on_failure_and_shutdown(monkeypatch):
    manager = _manager()
    manager._webengine_available = None
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: True)
    monkeypatch.setattr(manager, "_notify_webengine_preparing", lambda *_args: None)
    unavailable = []
    monkeypatch.setattr(manager, "_notify_webengine_unavailable", lambda *_args: unavailable.append(True))

    assert manager.open_chart(None, "000001", "First", object()) is None
    manager._webengine_available = False
    manager._pending_open.resume()

    assert manager._pending_open.request is None
    assert manager._charts == []
    assert unavailable == [True]

    manager._webengine_available = None
    assert manager.open_chart(None, "000002", "Second", object()) is None
    assert manager._pending_open.request is not None
    manager.shutdown()
    assert manager._pending_open.request is None


def test_preflight_and_keeper_completion_request_pending_resume(monkeypatch):
    manager = _manager()
    resume_requests = []
    monkeypatch.setattr(manager._pending_open, "request_resume", lambda: resume_requests.append("resume"))

    assert manager_module._commit_webengine_preflight_result(
        manager,
        threading.Event(),
        {"ok": True, "elapsed_ms": 1.0},
    ) is True

    view = _View()
    manager._prewarm_view = view
    manager._prewarm_load_callback = lambda _ok: None
    view.loadFinished.connect(manager._prewarm_load_callback)
    manager_module._complete_hidden_prewarm(manager, view, time.perf_counter(), True)

    assert resume_requests == ["resume", "resume"]


def test_is_alive_handles_deleted_wrapper():
    assert manager_module._is_alive(_Chart())
    dead = _Chart()
    dead.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("deleted"))
    assert not manager_module._is_alive(dead)
