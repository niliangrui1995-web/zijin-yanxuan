# -*- coding: utf-8 -*-
"""K-line physical-window state machine and QObject lifetime contracts."""

from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace
from typing import cast

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal

from ui.components import kline_window_manager as manager_module
from ui.kline_typing import KLineBrowserProtocol
from ui.kline_window_pool_lifecycle import (
    KLinePoolState,
    KLineWindowPoolLifecycleMixin,
    initialize_kline_pool_state,
)


class _LifecycleObject(KLineWindowPoolLifecycleMixin, QObject):
    def __init__(self) -> None:
        super().__init__()
        initialize_kline_pool_state(self)


class _PooledPage(QObject):
    renderProcessTerminated = pyqtSignal(object, int)


class _PooledBrowser(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._page = _PooledPage(self)
        self.setProperty(manager_module.KLINE_SHELL_READY_PROPERTY, True)

    def parentWidget(self):
        return self.parent()

    def page(self) -> _PooledPage:
        return self._page


class _PooledPhysicalWindow(KLineWindowPoolLifecycleMixin, QObject):
    manager: manager_module.KLineWindowManager | None = None
    created_count = 0

    def __init__(self, **kwargs) -> None:
        super().__init__()
        type(self).created_count += 1
        initialize_kline_pool_state(self)
        self.code = str(kwargs.get("code") or "")
        self.name = str(kwargs.get("name") or "")
        self._visible = False
        self._shell_loaded = True
        self._last_shell_load_ok = True
        self.chart_host = QObject(self)
        self.browser = cast(KLineBrowserProtocol, _PooledBrowser(self.chart_host))

    def windowTitle(self) -> str:
        return f"{self.name} ({self.code})"

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def raise_(self) -> None:
        return None

    def activateWindow(self) -> None:
        return None

    def setAttribute(self, *_args) -> None:
        return None

    def activate_lease(self, **kwargs) -> bool:
        self.code = str(kwargs.get("code") or "")
        self.name = str(kwargs.get("name") or "")
        self.transition(KLinePoolState.ACTIVE, reason="test_manager_reuse")
        return True

    def reset_browser_for_pool(self, callback) -> bool:
        callback(True)
        return True

    def complete_pool_return(self) -> bool:
        if self.pool_state is not KLinePoolState.CLOSING:
            return False
        self.transition(KLinePoolState.IDLE, reason="test_manager_pool_return")
        self.hide()
        return True

    def close(self) -> bool:
        if self.pool_state is not KLinePoolState.ACTIVE or self.manager is None:
            return False
        self.transition(KLinePoolState.CLOSING, reason="test_manager_close")
        self.hide()
        return bool(self.manager.release_chart(self, cleanup_ok=True))

    def final_dispose(self) -> bool:
        self.transition(KLinePoolState.DISPOSED, reason="test_manager_shutdown")
        self.hide()
        self.deleteLater()
        return True


def test_pool_state_transitions_are_explicit_and_legacy_flags_are_read_only():
    window = _LifecycleObject()

    assert window.pool_state is KLinePoolState.ACTIVE
    assert window._closing is False

    window.transition(KLinePoolState.CLOSING, reason="close")
    assert window._closing is True
    window.transition(KLinePoolState.IDLE, reason="reset_acknowledged")
    assert window._pool_idle is True
    window.transition(KLinePoolState.ACTIVE, reason="reuse")
    window.transition(KLinePoolState.TAINTED, reason="renderer_failed")
    assert window._pool_tainted is True
    assert window._closing is False
    window.transition(KLinePoolState.DISPOSED, reason="terminal_close")
    assert window._force_dispose is True

    with pytest.raises(RuntimeError, match="DISPOSED -> ACTIVE"):
        window.transition(KLinePoolState.ACTIVE, reason="invalid_reuse")
    with pytest.raises(AttributeError):
        window._pool_idle = True


def test_watchlist_close_returns_to_qt_without_repaint_guard(monkeypatch):
    class CloseProbe(KLineWindowPoolLifecycleMixin):
        pass

    events = []
    probe = CloseProbe()
    initialize_kline_pool_state(probe)
    probe.code = "000001"
    probe._lease_signals_connected = False
    probe._open_stages = SimpleNamespace(stop=lambda: (None, None))
    probe._rt_timer = None

    qt_api = SimpleNamespace(
        _cancel_header_resize_refresh=lambda _window: None,
        cancel_snapshot_render_confirmation=lambda _window: None,
        _shutdown_kline_window_tasks=lambda _window: True,
        log=SimpleNamespace(debug=lambda _message: None),
    )
    monkeypatch.setattr("ui.kline_window_pool_lifecycle._qt_api", lambda: qt_api)
    monkeypatch.setattr("ui.kline_window_pool_lifecycle._recovery_cleanup_clean", lambda _window: True)
    monkeypatch.setattr("ui.kline_window_pool_lifecycle._release_chart_to_pool", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "ui.kline_window_pool_lifecycle._call_next_close_event",
        lambda _window, _event: events.append("qt_close"),
    )
    probe.closeEvent(object())

    assert events == ["qt_close"]


def test_one_qobject_survives_100_open_close_reuse_cycles_without_leaking(
    qt_application, monkeypatch
):
    monkeypatch.setattr(manager_module.KLineWindowManager, "_instance", None)
    manager = manager_module.KLineWindowManager()
    manager._webengine_available = True
    _PooledPhysicalWindow.manager = manager
    _PooledPhysicalWindow.created_count = 0
    monkeypatch.setattr(manager_module, "_load_kline_window_class", lambda: _PooledPhysicalWindow)
    physical = manager.open_chart(None, "seed", "seed", object(), vcp_data={})
    assert physical is not None
    assert physical.close() is True
    for cycle in range(100):
        opened = manager.open_chart(None, f"{cycle:06d}", f"stock-{cycle}", object(), vcp_data={})
        assert opened is physical
        assert physical.pool_state is KLinePoolState.ACTIVE
        assert _PooledPhysicalWindow.created_count == 1
        assert len(physical.findChildren(QObject)) == 3
        health = manager.runtime_health_snapshot()
        assert health["browser_count"] == 1
        assert health["page_count"] == 1
        assert opened.close() is True
        assert physical.pool_state is KLinePoolState.IDLE
        assert manager._idle_chart is physical
        assert manager.active_count == 0

    tracked = [physical, physical.chart_host, physical.browser, physical.browser.page()]
    tracked_refs = [weakref.ref(item) for item in tracked]
    assert manager.shutdown() is True
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_application.processEvents()
    assert all(sip.isdeleted(item) for item in tracked)

    del opened, physical
    tracked.clear()
    gc.collect()
    assert all(item() is None for item in tracked_refs)
