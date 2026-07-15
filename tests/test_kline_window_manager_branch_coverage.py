# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ui.components import kline_window_manager as manager_module


def _manager():
    manager = manager_module.KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._prewarm_cancelled = False
    manager._prewarm_expire_timer = None
    manager._prewarm_hidden_view_enabled = False
    manager._prewarm_ttl_ms = 0
    manager._webengine_available = None
    manager._webengine_failure = ""
    manager._webengine_preflight_started = False
    manager._post_close_collect_scheduled = False
    return manager


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _Chart:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.destroyed = _Signal()
        self.visible = True
        self.shown = False
        self.closed = False
        self.raised = False
        self.activated = False

    def isVisible(self):
        return self.visible

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True
        self.visible = False

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
    monkeypatch.setattr(manager_module.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(manager_module.importlib.util, "module_from_spec", lambda value: module)
    assert manager_module._load_kline_window_class() is fake_class
    assert sys.modules[spec.name] is module


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
    assert manager.prewarm(delay_ms=-1, ttl_ms=-3, hidden_view=True)
    assert scheduled[0][0] == 0
    assert manager._prewarm_ttl_ms == 0
    assert manager._prewarm_hidden_view_enabled is True
    assert not manager.prewarm()

    class BrokenTimer:
        @staticmethod
        def singleShot(*args):
            raise RuntimeError("bad")

    manager = _manager()
    monkeypatch.setattr(qtcore, "QTimer", BrokenTimer)
    assert not manager.prewarm(hidden_view=False)
    assert manager._prewarm_started is False


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
    manager._webengine_available = None
    monkeypatch.setattr(manager_module, "check_qt_webengine_available", lambda **kwargs: {"ok": False})
    assert manager._ensure_webengine_available() is False
    assert manager._webengine_failure == "unknown"

    started = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            started.append((name, daemon))

        def start(self):
            self.target()

    manager = _manager()
    monkeypatch.setattr(manager_module.threading, "Thread", Thread)
    monkeypatch.setattr(manager, "_ensure_webengine_available", lambda: True)
    assert manager._start_webengine_preflight_async()
    assert manager._webengine_preflight_started is False
    manager._webengine_available = True
    assert not manager._start_webengine_preflight_async()
    manager._webengine_available = None
    manager._webengine_preflight_started = True
    assert not manager._start_webengine_preflight_async()


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


class _View:
    def __init__(self):
        self.hidden = False
        self.parent = "parent"
        self.deleted = False
        self.object_name = None
        self.html = None

    def hide(self):
        self.hidden = True

    def setParent(self, parent):
        self.parent = parent

    def deleteLater(self):
        self.deleted = True

    def setObjectName(self, name):
        self.object_name = name

    def resize(self, *args):
        self.size = args

    def setHtml(self, *args):
        self.html = args


def test_prewarm_timer_disposal_expiry_and_take(monkeypatch):
    import PyQt6.QtCore as qtcore

    monkeypatch.setattr(qtcore, "QTimer", _Timer)
    metrics = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    manager = _manager()
    manager._cancel_prewarm_expire_timer()
    manager._prewarm_ttl_ms = 50
    manager._schedule_prewarm_expiry()
    timer = manager._prewarm_expire_timer
    assert timer.single is True and timer.started == 50
    manager._cancel_prewarm_expire_timer()
    assert timer.stopped and timer.deleted

    view = _View()
    manager._prewarm_view = view
    manager._prewarm_started = True
    manager._dispose_prewarm_view(reason="test")
    assert view.hidden and view.parent is None and view.deleted
    assert manager._prewarm_view is None and metrics
    manager._dispose_prewarm_view(reason="empty")

    manager._prewarm_view = _View()
    assert manager.take_prewarmed_browser() is None
    assert manager._prewarm_view is None
    assert manager.take_prewarmed_browser() is None

    manager._prewarm_ttl_ms = 0
    manager._prewarm_view = _View()
    manager._schedule_prewarm_expiry()
    assert manager._prewarm_view is None


def test_run_prewarm_preflight_skip_cancel_and_success(monkeypatch):
    manager = _manager()
    starts = []
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: starts.append(True) or True)
    manager._run_prewarm()
    assert starts and manager._prewarm_started is False
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

    import PyQt6.QtWidgets as widgets

    webengine = ModuleType("PyQt6.QtWebEngineWidgets")
    webengine.QWebEngineView = _View
    monkeypatch.setitem(sys.modules, "PyQt6.QtWebEngineWidgets", webengine)
    monkeypatch.setattr(
        widgets, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(closingDown=lambda: False))
    )
    manager._prewarm_cancelled = False
    manager._prewarm_started = True
    manager._prewarm_ttl_ms = 10
    monkeypatch.setattr(manager, "_schedule_prewarm_expiry", lambda: None)
    manager._run_prewarm()
    assert isinstance(manager._prewarm_view, _View)
    assert manager._prewarm_view.object_name == "klinePrewarmWebEngine"

    monkeypatch.setattr(widgets, "QApplication", SimpleNamespace(instance=lambda: None))
    manager._prewarm_view = None
    manager._prewarm_started = True
    manager._run_prewarm()
    assert manager._prewarm_started is False


def test_remove_chart_post_close_and_active_count(monkeypatch):
    manager = _manager()
    alive = _Chart(code="1")
    dead = _Chart(code="2")
    dead.visible = False
    manager._charts = [alive, dead]
    scheduled = []
    monkeypatch.setattr(manager, "_schedule_post_close_collect", lambda: scheduled.append(True))
    manager._remove_chart_ref(dead)
    assert manager._charts == [alive] and scheduled
    manager._charts = [alive, dead]
    assert manager.active_count == 1
    alive.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("deleted"))
    assert manager.active_count == 0


def test_post_close_schedule_guard_fallback_and_metric(monkeypatch):
    import PyQt6.QtCore as qtcore

    scheduled = []
    manager = _manager()
    monkeypatch.setattr(qtcore.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    manager._schedule_post_close_collect()
    manager._schedule_post_close_collect()
    assert len(scheduled) == 1
    metrics = []
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    scheduled[0][1]()
    assert manager._post_close_collect_scheduled is False and metrics

    manager = _manager()
    monkeypatch.setattr(qtcore.QTimer, "singleShot", lambda *args: (_ for _ in ()).throw(RuntimeError("bad")))
    manager._schedule_post_close_collect()
    assert manager._post_close_collect_scheduled is False


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
    old = [_Chart(code=str(index)) for index in range(manager_module.MAX_CHART_WINDOWS)]
    hidden = _Chart(code="hidden")
    hidden.visible = False
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
    assert old[0].closed
    assert chart.kwargs["vcp_data"] == {"code": " 000001 ", "name": " Ping "}
    assert chart.kwargs["code_list"] == []
    assert manager._prewarm_cancelled is True
    assert len(manager._charts) == manager_module.MAX_CHART_WINDOWS
    assert metrics and logs
    chart.destroyed.callbacks[0]()


def test_open_chart_raise_failures_are_contained(monkeypatch):
    class BrokenChart(_Chart):
        def raise_(self):
            raise RuntimeError("deleted")

    manager = _manager()
    manager._webengine_available = True
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: BrokenChart)
    assert manager.open_chart(None, "1", "one", object(), vcp_data={}) is not None


def test_is_alive_handles_deleted_wrapper():
    assert manager_module._is_alive(_Chart())
    dead = _Chart()
    dead.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("deleted"))
    assert not manager_module._is_alive(dead)
