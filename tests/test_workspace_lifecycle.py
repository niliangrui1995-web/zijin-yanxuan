from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

from ui import main_window_qt as main_window_module
from ui.main_window_host_port import MainWindowHostPortMixin
from ui.workspaces import classic_workspace as classic_module
from ui.workspaces.classic_workspace import ClassicWorkspace
from ui.workspaces.workspace_facade import WorkspaceFacade


class _Scheduler:
    def __init__(self) -> None:
        self.cancel_calls = 0
        self.delete_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def deleteLater(self) -> None:  # noqa: N802 - Qt compatibility
        self.delete_calls += 1


def test_workspace_facade_shutdown_is_idempotent_and_cancels_frame_schedulers():
    refresh_scheduler = _Scheduler()
    information_scheduler = _Scheduler()
    quote_replay_scheduler = _Scheduler()
    workspace = SimpleNamespace(
        _f5_refresh_scheduler=refresh_scheduler,
        _f5_information_source_scheduler=information_scheduler,
        _f5_quote_replay_scheduler=quote_replay_scheduler,
    )
    shutdown_calls = []
    facade = object.__new__(WorkspaceFacade)
    facade._workspace = workspace
    facade._stock_context_service = SimpleNamespace(
        shutdown=lambda **kwargs: shutdown_calls.append(kwargs) or True
    )
    facade._shutdown_started = False
    facade._shutdown_result = False

    assert facade.shutdown(timeout_ms=123) is True
    assert facade.shutdown(timeout_ms=999) is True
    assert shutdown_calls == [{"timeout_ms": 123}]
    assert workspace._f5_refresh_scheduler is None
    assert workspace._f5_information_source_scheduler is None
    assert workspace._f5_quote_replay_scheduler is None
    assert (refresh_scheduler.cancel_calls, refresh_scheduler.delete_calls) == (1, 1)
    assert (information_scheduler.cancel_calls, information_scheduler.delete_calls) == (1, 1)
    assert (quote_replay_scheduler.cancel_calls, quote_replay_scheduler.delete_calls) == (1, 1)


def test_classic_workspace_shutdown_blocks_delayed_background_prewarm(monkeypatch):
    calls = []
    workspace = SimpleNamespace(
        _shutting_down=False,
        _background_prewarm_started=False,
        _background_prewarm_queue=["scan"],
        _lazy_loading_keys={"lhb"},
        _pending_tab_activation_reasons={1: "user"},
        _copy_hook_refresh_queued=True,
        _restore_last_tab_timer=None,
        _disconnect_workspace_events=lambda: calls.append("disconnect"),
        iter_tabs=lambda: [],
        prime_stock_context_snapshots=lambda **_kwargs: calls.append("prewarm"),
    )
    monkeypatch.setattr(
        classic_module,
        "_shutdown_workspace_facade",
        lambda owner: calls.append(("facade", owner._shutting_down)),
    )

    ClassicWorkspace.shutdown(workspace)
    ClassicWorkspace.shutdown(workspace)
    ClassicWorkspace._start_background_tab_prewarm(workspace)
    ClassicWorkspace._prewarm_next_tab(workspace)

    assert calls == ["disconnect", ("facade", True)]
    assert workspace._background_prewarm_queue == []
    assert workspace._lazy_loading_keys == set()
    assert workspace._pending_tab_activation_reasons == {}
    assert workspace._copy_hook_refresh_queued is False
    assert workspace._background_prewarm_started is False


def test_classic_workspace_close_routes_through_shutdown(qt_application):
    workspace = ClassicWorkspace(
        data_provider=None,
        engine=None,
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    workspace.show()

    assert workspace.close() is True
    assert workspace._shutting_down is True
    assert workspace._background_prewarm_enabled is False
    workspace.deleteLater()


def test_classic_workspace_delete_routes_through_shutdown(qt_application):
    workspace = ClassicWorkspace(
        data_provider=None,
        engine=None,
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )

    workspace.deleteLater()

    assert workspace._shutting_down is True
    assert workspace._background_prewarm_enabled is False


def test_classic_workspace_shutdown_blocks_queued_lazy_load_and_activation(monkeypatch):
    callbacks = []
    calls = []
    workspace = SimpleNamespace(
        _shutting_down=False,
        _lazy_loading_keys=set(),
        tabs=SimpleNamespace(count=lambda: 1, currentWidget=lambda: "widget"),
        _startup_last_allowed_index=0,
        ensure_tab_loaded=lambda key, *, reason: calls.append(("load", key, reason)),
        _lazy_tab_load_delay_ms=lambda _reason: 0,
        _activation_callback_delay_ms=lambda: 0,
    )
    monkeypatch.setattr(classic_module.QTimer, "singleShot", lambda _delay, callback: callbacks.append(callback))

    assert ClassicWorkspace._queue_lazy_tab_load(
        workspace,
        {"widget": None},
        "scan",
        reason="restore_last_tab",
        index=0,
    )
    widget = SimpleNamespace(on_workspace_tab_activated=lambda: calls.append(("activated",)))
    workspace.tabs.currentWidget = lambda: widget
    ClassicWorkspace._notify_tab_activated(workspace, "scan", widget)
    assert len(callbacks) == 2

    workspace._shutting_down = True
    for callback in callbacks:
        callback()

    assert calls == []
    assert workspace._lazy_loading_keys == set()
    assert ClassicWorkspace.ensure_tab_loaded(workspace, "scan") is None
    assert ClassicWorkspace.activate_tab(workspace, 0) is False


def test_immediate_data_tab_click_waits_for_runtime_dependencies(qt_application):
    created = []

    class FakeTab(QWidget):
        def __init__(self, key: str, parent):
            super().__init__(parent)
            created.append(key)

    workspace = ClassicWorkspace(
        data_provider=None,
        engine=None,
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    for spec in workspace._tab_specs:
        key = spec["key"]
        spec["factory"] = lambda key=key, **_kwargs: FakeTab(key, workspace)

    try:
        assert workspace.ensure_tab_loaded("watchlist", reason="placeholder_action") is None
        assert workspace.ensure_tab_loaded("scan", reason="placeholder_action") is None
        assert created == []
        assert set(workspace._runtime_pending_tab_loads) == {"watchlist", "scan"}

        system_log = workspace.ensure_tab_loaded("system_log", reason="placeholder_action")
        assert isinstance(system_log, FakeTab)
        assert created == ["system_log"]

        workspace.attach_runtime_services(data_provider=object())
        QTest.qWait(5)
        qt_application.processEvents()
        assert created == ["system_log", "watchlist"]
        assert set(workspace._runtime_pending_tab_loads) == {"scan"}

        workspace.attach_runtime_services(engine=object())
        QTest.qWait(5)
        qt_application.processEvents()
        assert created == ["system_log", "watchlist", "scan"]
        assert workspace._runtime_pending_tab_loads == {}
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_user_tab_choice_consumes_pending_startup_restore(qt_application):
    class Host(MainWindowHostPortMixin):
        pass

    host = Host()
    workspace = ClassicWorkspace(
        data_provider=None,
        engine=None,
        host=host,
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    try:
        host._pending_workspace_tab_activation = (workspace, "watchlist")
        system_log_index = workspace._tab_index_for_key("system_log")
        assert workspace.activate_tab(system_log_index, reason="shell_nav") is True
        assert host._pending_workspace_tab_activation is None

        host._pending_workspace_tab_activation = (workspace, "watchlist")
        workspace.activate_tab(0, reason="restore_last_tab")
        assert host._pending_workspace_tab_activation == (workspace, "watchlist")
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_post_paint_restore_loads_only_the_target_tab(monkeypatch, qt_application):
    loaded = []

    class FakeTab(QWidget):
        def __init__(self, key, parent):
            super().__init__(parent)
            loaded.append(key)

    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    for spec in workspace._tab_specs:
        key = spec["key"]
        spec["factory"] = lambda key=key, **_kwargs: FakeTab(key, workspace)

    window = SimpleNamespace(
        _is_closing=False,
        _post_paint_runtime_started=False,
        _pending_workspace_tab_activation=(workspace, "scan"),
        _workspace=workspace,
        _auto_refresh_enabled=False,
        _startup_enabled=False,
        auto_refresh_scheduler=None,
    )
    monkeypatch.setattr(main_window_module, "_schedule_f5_startup_retention", lambda _window: True)
    try:
        for _ in range(12):
            main_window_module._run_post_paint_runtime(window)
            if window._post_paint_runtime_started:
                break
        assert window._post_paint_runtime_started is True
        assert workspace._restore_last_tab_timer.interval() == main_window_module.POST_PAINT_TAB_ACTIVATION_DELAY_MS

        workspace._restore_last_tab_timer.timeout.emit()
        QTest.qWait(5)
        qt_application.processEvents()

        assert loaded == ["scan"]
        assert [spec["key"] for spec in workspace.tab_specs() if spec["loaded"]] == ["scan"]
    finally:
        workspace.shutdown()
        workspace.deleteLater()
