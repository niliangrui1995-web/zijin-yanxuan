# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

from app.services.stock_context_model_service import StockSignal
from ui.workspaces import classic_workspace as workspace_module


class _Tabs:
    def __init__(self, count=2, current=0):
        self._count = count
        self._current = current
        self.changes = []

    def count(self):
        return self._count

    def currentIndex(self):
        return self._current

    def setCurrentIndex(self, index):
        self._current = index
        self.changes.append(index)


def test_classic_workspace_tracks_logical_activity_and_prepares_hidden_reveal():
    calls = []

    class Watchlist:
        def set_workspace_active(self, active):
            calls.append(("active", active))

        def prepare_workspace_reveal(self):
            calls.append(("prepare",))

    class OtherTab:
        def set_workspace_active(self, active):
            calls.append(("other", active))

    watchlist = Watchlist()
    workspace = SimpleNamespace(
        _tab_specs=[
            {"key": "watchlist", "loaded": True, "widget": watchlist},
            {"key": "scan", "loaded": True, "widget": OtherTab()},
            {"key": "lazy", "loaded": False, "widget": object()},
        ]
    )

    workspace_module._set_workspace_tab_activity(workspace, "watchlist")
    workspace_module._prepare_workspace_tab_reveal(watchlist)

    assert calls == [("active", True), ("other", False), ("prepare",)]


def test_workspace_staging_host_is_detached_from_live_workspace_layout(qt_application):
    """过期的懒加载页不能作为主工作区子树的一部分完成构造。"""
    workspace = QWidget()
    workspace.host = None
    workspace._background_preload_staging_host = None
    host = workspace_module._ensure_background_preload_staging_host(workspace)
    try:
        assert host.parentWidget() is None
        assert host.parent() is None
        assert getattr(host, "_workspace", None) is workspace

        workspace_module._dispose_background_preload_staging_host(workspace)

        assert workspace._background_preload_staging_host is None
        assert getattr(host, "_workspace", object()) is None
    finally:
        host.close()
        host.deleteLater()
        workspace.close()
        workspace.deleteLater()
        qt_application.processEvents()


def test_stale_interactive_lazy_load_is_cancelled_before_widget_construction():
    calls = []
    stale_workspace = SimpleNamespace(
        _shutting_down=False,
        _lazy_loading_keys={"scan"},
        tabs=_Tabs(count=2, current=0),
        _tab_index_for_key=lambda _key: 1,
        ensure_tab_loaded=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    workspace_module._load_queued_tab(stale_workspace, "scan", "shell_nav")

    assert calls == []
    assert stale_workspace._lazy_loading_keys == set()

    current_workspace = SimpleNamespace(
        _shutting_down=False,
        _lazy_loading_keys={"scan"},
        tabs=_Tabs(count=2, current=1),
        _tab_index_for_key=lambda _key: 1,
        ensure_tab_loaded=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    workspace_module._load_queued_tab(current_workspace, "scan", "shell_nav")

    assert calls == [(('scan',), {'reason': 'shell_nav'})]


def test_background_lazy_load_is_not_cancelled_when_its_tab_is_not_selected():
    calls = []
    workspace = SimpleNamespace(
        _shutting_down=False,
        _lazy_loading_keys={"scan"},
        tabs=_Tabs(count=2, current=0),
        _tab_index_for_key=lambda _key: 1,
        ensure_tab_loaded=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    workspace_module._load_queued_tab(workspace, "scan", "background_prewarm")

    assert calls == [(('scan',), {'reason': 'background_prewarm'})]


def test_explicit_placeholder_load_is_not_cancelled_when_its_tab_is_not_selected():
    calls = []
    workspace = SimpleNamespace(
        _shutting_down=False,
        _lazy_loading_keys={"scan"},
        tabs=_Tabs(count=2, current=0),
        _tab_index_for_key=lambda _key: 1,
        ensure_tab_loaded=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    workspace_module._load_queued_tab(workspace, "scan", "placeholder_action")

    assert calls == [(('scan',), {'reason': 'placeholder_action'})]


def test_classic_workspace_resolve_lazy_placeholder_and_shutdown_errors(monkeypatch, qt_application):
    class _Resolved:
        pass

    module = SimpleNamespace(Resolved=_Resolved)
    monkeypatch.delitem(workspace_module.__dict__, "Resolved", raising=False)
    monkeypatch.setattr(workspace_module, "import_module", lambda name: module)
    assert workspace_module._resolve_tab_class("Resolved", "fake.module") is _Resolved
    assert workspace_module._resolve_tab_class("Resolved", "unused") is _Resolved

    calls = []
    placeholder = workspace_module.LazyTabPlaceholder("", lambda: calls.append("load"))
    try:
        assert placeholder.lbl_title.text() == "页面待加载"
        placeholder.set_loading()
        assert not placeholder.btn_load.isEnabled()
        placeholder.set_error("")
        assert placeholder.btn_load.isEnabled()
        assert "失败" in placeholder.lbl_detail.text()
        placeholder._handle_load()
        assert calls == ["load"]
        placeholder._load_callback = None
        placeholder._handle_load()
    finally:
        placeholder.close()
        placeholder.deleteLater()

    workspace_module._shutdown_workspace_facade(SimpleNamespace())
    bad_facade = SimpleNamespace(shutdown=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")))
    workspace_module._shutdown_workspace_facade(SimpleNamespace(_workspace_facade=bad_facade))


def test_classic_workspace_spec_lookup_load_guards_and_error_paths(monkeypatch, qt_application):
    specs = [{"key": "a", "loaded": True, "widget": "A"}, {"key": "b", "loaded": False}]
    fake = SimpleNamespace(_tab_specs=specs, _tabs_by_key={"a": "A"})

    def lookup(value):
        return workspace_module.ClassicWorkspace._spec_for_key_or_index(fake, value)

    assert lookup(0) is specs[0]
    assert lookup(9) is None
    assert lookup("") is None
    assert lookup("b") is specs[1]
    assert lookup("missing") is None
    assert workspace_module.ClassicWorkspace.get_loaded_tab(fake, " a ") == "A"

    ensure_fake = SimpleNamespace(
        _spec_for_key_or_index=lambda value: None,
        _ensure_tab_loaded_impl=lambda *args: "unexpected",
    )
    assert workspace_module.ClassicWorkspace.ensure_tab_loaded(ensure_fake, "missing") is None

    marks = []
    loaded_fake = SimpleNamespace(_mark_system_log_shell_nav=lambda key, reason: marks.append((key, reason)))
    assert (
        workspace_module.ClassicWorkspace._ensure_tab_loaded_impl(
            loaded_fake, {"loaded": True, "widget": "W"}, "a", "shell_nav"
        )
        == "W"
    )
    assert marks == [("a", "shell_nav")]

    placeholder = workspace_module.LazyTabPlaceholder("B", lambda: None)
    try:
        failing = SimpleNamespace(
            _lazy_loading_keys={"b"},
            _create_real_tab=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        spec = {"widget": placeholder}
        assert workspace_module.ClassicWorkspace._ensure_tab_loaded_impl(failing, spec, "b") is None
        assert "b" not in failing._lazy_loading_keys
        assert placeholder.btn_load.text() == "重试"
    finally:
        placeholder.close()
        placeholder.deleteLater()

    no_factory = SimpleNamespace(INTERACTIVE_LOAD_REASONS=set())
    try:
        workspace_module.ClassicWorkspace._create_real_tab(no_factory, {"key": "x"})
    except TypeError as exc:
        assert "missing tab factory" in str(exc)
    else:
        raise AssertionError("missing factory should fail")


def test_classic_workspace_tab_change_queue_and_navigation_guards(monkeypatch):
    fake = SimpleNamespace(
        _spec_for_key_or_index=lambda _index: None,
        _take_tab_activation_reason=lambda _index: "tab_switch",
        _mark_system_log_shell_nav=lambda *_args: None,
    )
    workspace_module.ClassicWorkspace._on_current_tab_changed(fake, 99)

    fake = SimpleNamespace(
        _lazy_loading_keys={"loading"},
        tabs=_Tabs(count=1),
        _lazy_tab_load_delay_ms=lambda _reason: 0,
        ensure_tab_loaded=lambda *_args, **_kwargs: None,
        _startup_last_allowed_index=0,
    )
    assert not workspace_module.ClassicWorkspace._queue_lazy_tab_load(fake, {"widget": object()}, "loading", reason="x")
    scheduled = []
    monkeypatch.setattr(
        workspace_module.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback))
    )
    assert workspace_module.ClassicWorkspace._queue_lazy_tab_load(
        fake, {"widget": object()}, "new", reason="x", index=9
    )
    assert "new" in fake._lazy_loading_keys

    nav = SimpleNamespace(_shell_group_rebuild_quiet_until=0.0)
    workspace_module.ClassicWorkspace.prepare_shell_group_rebuild_navigation(nav, interval_ms="bad")
    assert nav._shell_group_rebuild_quiet_until == 0.0
    workspace_module.ClassicWorkspace.prepare_shell_group_rebuild_navigation(nav, interval_ms=0)

    tabs = _Tabs(count=2, current=1)
    restore = SimpleNamespace(
        _startup_last_allowed_index=-1,
        tabs=tabs,
        _startup_suppressed_tab_switch_keys=set(),
        _pending_tab_activation_reasons={},
    )
    workspace_module.ClassicWorkspace._restore_startup_allowed_tab_after_suppressed_switch(restore, "x")
    restore._startup_last_allowed_index = 1
    workspace_module.ClassicWorkspace._restore_startup_allowed_tab_after_suppressed_switch(restore, "x")
    restore._startup_last_allowed_index = 0
    workspace_module.ClassicWorkspace._restore_startup_allowed_tab_after_suppressed_switch(restore, "x")
    scheduled[-1][1]()
    assert tabs.currentIndex() == 0


def test_classic_workspace_activate_tab_all_invalid_and_current_paths():
    tabs = _Tabs(count=2, current=0)
    fake = SimpleNamespace(
        tabs=tabs,
        _pending_tab_activation_reasons={},
        _startup_last_allowed_index=-1,
        _spec_for_key_or_index=lambda index: None if index == 0 else {"key": "b", "loaded": False},
        _queue_lazy_tab_load=lambda *args, **kwargs: True,
        _defer_interactive_activation_until_preload_ready=lambda *_args: False,
        _mark_system_log_shell_nav=lambda *_args: None,
        _notify_tab_activated=lambda *_args: None,
    )

    def activate(index, **kwargs):
        return workspace_module.ClassicWorkspace.activate_tab(fake, index, **kwargs)

    assert not activate("bad")
    assert not activate(9)
    assert activate(0)
    assert activate(1, reason="shell_nav")
    assert tabs.currentIndex() == 1
    assert fake._pending_tab_activation_reasons[1] == "shell_nav"

    widget = object()
    fake._spec_for_key_or_index = lambda _index: {"key": "b", "loaded": True, "widget": widget}
    notified = []
    fake._notify_tab_activated = lambda key, item: notified.append((key, item))
    assert activate(1)
    assert notified == [("b", widget)]


def test_classic_workspace_activate_tab_attaches_bounded_transition_context():
    tabs = _Tabs(count=2, current=0)
    source = SimpleNamespace(_workspace_tab_transition_context={"stale": "value"})
    target = SimpleNamespace()
    specs = {
        0: {"key": "watchlist", "loaded": True, "mounted": True, "widget": source},
        1: {"key": "asian_market", "loaded": True, "mounted": True, "widget": target},
    }
    fake = SimpleNamespace(
        tabs=tabs,
        _tab_transition_sequence=0,
        _pending_tab_activation_reasons={},
        _startup_last_allowed_index=-1,
        _background_prewarm_active_key="",
        _background_prewarm_finished=True,
        _spec_for_key_or_index=lambda index: specs.get(index),
        _defer_interactive_activation_until_preload_ready=lambda *_args: False,
        _mark_system_log_shell_nav=lambda *_args: None,
        _notify_tab_activated=lambda *_args: None,
    )

    assert workspace_module.ClassicWorkspace.activate_tab(fake, 1, reason="shell_nav")

    assert source._workspace_tab_transition_context == {}
    assert target._workspace_tab_transition_context == {
        "transition_id": "1",
        "source_tab": "watchlist",
        "target_tab": "asian_market",
        "reason": "shell_nav",
        "mounted_before": True,
        "preload_active_key": "",
        "preload_state": "interactive_warm",
    }


def test_classic_workspace_reactivating_current_tab_does_not_create_transition_context():
    tabs = _Tabs(count=2, current=1)
    target = SimpleNamespace(_workspace_tab_transition_context={})
    spec = {"key": "asian_market", "loaded": True, "mounted": True, "widget": target}
    fake = SimpleNamespace(
        tabs=tabs,
        _tab_transition_sequence=0,
        _pending_tab_activation_reasons={},
        _startup_last_allowed_index=-1,
        _spec_for_key_or_index=lambda _index: spec,
        _defer_interactive_activation_until_preload_ready=lambda *_args: False,
        _mark_system_log_shell_nav=lambda *_args: None,
        _notify_tab_activated=lambda *_args: None,
    )

    assert workspace_module.ClassicWorkspace.activate_tab(fake, 1, reason="shell_nav") is True

    assert fake._tab_transition_sequence == 0
    assert fake._pending_tab_activation_reasons == {}
    assert target._workspace_tab_transition_context == {}


def test_classic_workspace_arms_loaded_watchlist_guard_before_shell_nav_switch():
    tabs = _Tabs(count=2, current=0)
    calls = []

    class WatchlistWidget:
        def prepare_shell_nav_repaint_guard(self):
            calls.append(tabs.currentIndex())

    spec = {"key": "watchlist", "loaded": True, "widget": WatchlistWidget()}
    fake = SimpleNamespace(
        tabs=tabs,
        _pending_tab_activation_reasons={},
        _startup_last_allowed_index=-1,
        _spec_for_key_or_index=lambda _index: spec,
        _defer_interactive_activation_until_preload_ready=lambda *_args: False,
    )

    assert workspace_module.ClassicWorkspace.activate_tab(fake, 1, reason="shell_nav")
    assert calls == [0]
    assert tabs.currentIndex() == 1

    tabs._current = 0
    calls.clear()
    assert workspace_module.ClassicWorkspace.activate_tab(fake, 1, reason="user")
    assert calls == []


def test_classic_workspace_arms_loaded_lhb_guard_before_shell_nav_switch():
    tabs = _Tabs(count=2, current=0)
    calls = []

    class LhbWidget:
        def prepare_shell_nav_repaint_guard(self):
            calls.append(tabs.currentIndex())

    spec = {"key": "lhb", "loaded": True, "widget": LhbWidget()}
    fake = SimpleNamespace(
        tabs=tabs,
        _pending_tab_activation_reasons={},
        _startup_last_allowed_index=-1,
        _spec_for_key_or_index=lambda _index: spec,
        _defer_interactive_activation_until_preload_ready=lambda *_args: False,
    )

    assert workspace_module.ClassicWorkspace.activate_tab(fake, 1, reason="shell_nav")
    assert calls == [0]
    assert tabs.currentIndex() == 1


def test_classic_workspace_event_wiring_prewarm_prime_and_copy_hook_errors(monkeypatch):
    import app.services.ui_event_service as event_module

    class _BadSignal:
        def connect(self, _slot):
            raise RuntimeError("connect")

        def disconnect(self, _slot):
            raise RuntimeError("disconnect")

    monkeypatch.setattr(
        event_module,
        "domain_events",
        SimpleNamespace(sig_ai_industry_chain_updated=_BadSignal(), sig_fund_holdings_updated=_BadSignal()),
    )
    fake = SimpleNamespace(
        _on_ai_industry_chain_source_updated=lambda *_args: None,
        _on_fund_holdings_source_updated=lambda *_args: None,
    )
    workspace_module.ClassicWorkspace._connect_workspace_events(fake)
    fake._workspace_event_bus = event_module.domain_events
    fake._workspace_events_connected = True
    workspace_module.ClassicWorkspace._disconnect_workspace_events(fake)
    assert not fake._workspace_events_connected

    facade_calls = []
    facade = SimpleNamespace(refresh_tabs_after_ai_industry_chain_update=lambda: facade_calls.append("refresh"))
    monkeypatch.setattr(workspace_module, "_resolve_workspace_facade", lambda _self: facade)
    fake.prime_stock_context_snapshots = lambda **kwargs: facade_calls.append(kwargs)
    workspace_module.ClassicWorkspace._on_ai_industry_chain_source_updated(fake)
    workspace_module.ClassicWorkspace._on_fund_holdings_source_updated(fake)
    assert facade_calls == ["refresh", {"force": True, "include_lhb": False}]

    started = SimpleNamespace(
        _background_prewarm_started=True,
        _startup_cache_bootstrap_required=False,
        _startup_cache_bootstrap_ready=True,
    )
    workspace_module.ClassicWorkspace._start_background_tab_prewarm(started)

    bad_widget = SimpleNamespace(prime_startup_state=lambda: (_ for _ in ()).throw(RuntimeError("prime")))
    workspace_module.ClassicWorkspace._prime_tab_runtime(SimpleNamespace(), bad_widget)
    workspace_module.ClassicWorkspace._prime_tab_runtime(SimpleNamespace(), SimpleNamespace())

    host = SimpleNamespace(install_workspace_table_copy_hooks=lambda: (_ for _ in ()).throw(RuntimeError("hook")))
    copy_fake = SimpleNamespace(
        host=host, window=lambda: None, _copy_hook_refresh_queued=False, COPY_HOOK_REFRESH_DELAY_MS=1
    )
    monkeypatch.setattr(workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())
    workspace_module.ClassicWorkspace._schedule_workspace_table_copy_hooks(copy_fake)
    assert not copy_fake._copy_hook_refresh_queued
    copy_fake.host = SimpleNamespace()
    workspace_module.ClassicWorkspace._schedule_workspace_table_copy_hooks(copy_fake)


def test_classic_workspace_restore_timer_facade_delegates_and_tab_index(monkeypatch):
    fake = SimpleNamespace(_restore_last_tab_timer=None)
    workspace_module.ClassicWorkspace.schedule_restore_last_tab(fake, -1)
    workspace_module.ClassicWorkspace.schedule_restore_last_tab(fake, 1, delay_ms="bad")

    stopped = []
    previous = SimpleNamespace(stop=lambda: stopped.append("stop"), deleteLater=lambda: stopped.append("delete"))
    fake = SimpleNamespace(
        _restore_last_tab_timer=previous,
        _background_prewarm_queue=[],
        BACKGROUND_PREWARM_INTERVAL_MS=1,
        RESTORE_LAST_TAB_DELAY_MS=1,
        restore_last_tab=lambda index: stopped.append(("restore", index)),
    )

    class _Timer:
        def __init__(self, _parent):
            self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, "callback", callback))

        def setSingleShot(self, _value):
            pass

        def start(self, delay):
            stopped.append(("start", delay))

        def deleteLater(self):
            stopped.append("timer-delete")

    monkeypatch.setattr(workspace_module, "QTimer", _Timer)
    workspace_module.ClassicWorkspace.schedule_restore_last_tab(fake, 1, delay_ms=0)
    assert stopped[:2] == ["stop", "delete"]

    class _Facade:
        def get_scan_results(self):
            return [{"x": 1}]

        def run_incremental_scan(self):
            return True

        def open_scan_settings(self):
            return True

        def refresh_lhb_history(self):
            return True

        def run_fund_holdings_sync(self):
            return True

        def select_code_row(self, code, preferred):
            return (code, preferred) == ("1", 2)

        def refresh_watchlist_names(self, mapping):
            return mapping == {"1": "A"}

    facade = _Facade()
    monkeypatch.setattr(workspace_module, "_resolve_workspace_facade", lambda _self: facade)
    shell = SimpleNamespace(tabs=_Tabs(current=1))
    assert workspace_module.ClassicWorkspace.current_tab_index(shell) == 1
    assert workspace_module.ClassicWorkspace.get_scan_results(shell) == [{"x": 1}]
    assert workspace_module.ClassicWorkspace.run_incremental_scan(shell)
    assert workspace_module.ClassicWorkspace.open_scan_settings(shell)
    assert workspace_module.ClassicWorkspace.refresh_lhb_history(shell)
    assert workspace_module.ClassicWorkspace.run_fund_holdings_sync(shell)
    assert workspace_module.ClassicWorkspace.select_code_row(shell, "1", 2)
    assert workspace_module.ClassicWorkspace.refresh_watchlist_names(shell, {"1": "A"})

    index_fake = SimpleNamespace(tab_specs=lambda: [{"key": "a"}, {"key": "b"}])
    assert workspace_module.ClassicWorkspace._tab_index_for_key(index_fake, "") == -1
    assert workspace_module.ClassicWorkspace._tab_index_for_key(index_fake, "b") == 1
    assert workspace_module.ClassicWorkspace._tab_index_for_key(index_fake, "x") == -1


def test_classic_workspace_security_detail_guards_and_signal_fallback(monkeypatch):
    assert not workspace_module.ClassicWorkspace.open_security_detail(SimpleNamespace(), "")

    signal = StockSignal(code="", source_tab="scan", signal_type="x", summary="")
    assert not workspace_module.ClassicWorkspace._activate_stock_signal_source(SimpleNamespace(), signal)

    signal = StockSignal(code="1", source_tab="missing", signal_type="x", summary="")
    fake = SimpleNamespace(
        tab_specs=lambda: [],
        select_code_row=lambda code, preferred_tab_index=None: (code, preferred_tab_index) == ("1", None),
    )
    assert workspace_module.ClassicWorkspace._activate_stock_signal_source(fake, signal)
