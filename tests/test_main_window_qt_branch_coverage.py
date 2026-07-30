# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLineEdit, QMessageBox

from ui import main_window_qt as main


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)


class _Tabs:
    def __init__(self, index=0, count=3):
        self.index = index
        self._count = count
        self.currentChanged = _Signal()

    def currentIndex(self):
        return self.index

    def count(self):
        return self._count

    def setCurrentIndex(self, index):
        self.index = index

    def currentWidget(self):
        return SimpleNamespace()

    def tabBar(self):
        return SimpleNamespace(setVisible=lambda value: setattr(self, "bar_visible", value))


def test_deferred_factories_delegate_to_runtime_modules(monkeypatch):
    import app.services.runtime_services as runtime_services
    import app.services.scan_runtime_service as scan_services

    calls = []
    monkeypatch.setattr(runtime_services, "create_data_provider", lambda **kwargs: calls.append(kwargs) or "provider")
    monkeypatch.setattr(
        runtime_services,
        "create_startup_orchestrator",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "startup",
    )
    monkeypatch.setattr(scan_services, "create_scan_engine", lambda: "engine")
    assert main.create_data_provider(offline=False) == "provider"
    assert main.create_startup_orchestrator("window", job_runner="runner") == "startup"
    assert main.create_scan_engine() == "engine"


def test_ui_callback_and_call_signal_error_and_closing():
    calls = []
    main.MainWindowQT._run_ui_callback(SimpleNamespace(), lambda: calls.append(True))
    main.MainWindowQT._run_ui_callback(SimpleNamespace(), lambda: (_ for _ in ()).throw(ValueError("bad")))
    window = SimpleNamespace(_is_closing=False, _sig_ui_call=SimpleNamespace(emit=lambda cb: calls.append(cb)))

    def callback():
        return None

    main.MainWindowQT._call_in_ui(window, callback)
    assert calls[-1] is callback
    window._is_closing = True
    main.MainWindowQT._call_in_ui(window, callback)
    assert calls.count(callback) == 1


def test_auto_refresh_initialization_guard_and_construction(monkeypatch):
    import app.services.na_daily_service as na_module
    import ui.services.asian_market_runtime_service as asian_module
    import ui.services.auto_refresh_scheduler as scheduler_module
    import ui.services.earnings_refresh_service as earnings_module

    monkeypatch.setattr(na_module, "NADailyRefreshService", lambda parent: ("na", parent))
    monkeypatch.setattr(asian_module, "AsianMarketRuntimeService", lambda parent: ("asian", parent))
    monkeypatch.setattr(earnings_module, "EarningsRefreshService", lambda parent: ("earnings", parent))
    monkeypatch.setattr(scheduler_module, "AutoRefreshScheduler", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    window = SimpleNamespace(_auto_refresh_enabled=False, auto_refresh_scheduler=None, data_provider="p", engine="e")
    main.MainWindowQT._initialize_auto_refresh_services(window)
    assert window.auto_refresh_scheduler is None
    window._auto_refresh_enabled = True
    main.MainWindowQT._initialize_auto_refresh_services(window)
    assert window.auto_refresh_scheduler.kwargs["data_provider"] == "p"
    existing = window.auto_refresh_scheduler
    main.MainWindowQT._initialize_auto_refresh_services(window)
    assert window.auto_refresh_scheduler is existing


class _Timer:
    def __init__(self, active=False):
        self.active = active
        self.started = False

    def isActive(self):
        return self.active

    def start(self, delay_ms=None):
        self.started = True
        self.delay_ms = delay_ms


def test_post_paint_schedule_and_runtime_success_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_schedule_f5_startup_retention", lambda _window: calls.append("retention"))
    timer = _Timer()
    window = SimpleNamespace(_is_closing=False, _post_paint_runtime_started=False, _post_paint_runtime_timer=timer)
    main.MainWindowQT._schedule_post_paint_runtime(window)
    assert timer.started
    timer.started = False
    timer.active = True
    main.MainWindowQT._schedule_post_paint_runtime(window)
    assert not timer.started
    window._is_closing = True
    main.MainWindowQT._schedule_post_paint_runtime(window)

    scheduler = SimpleNamespace(start=lambda: calls.append("scheduler"))
    runtime_window = SimpleNamespace(
        _is_closing=False,
        _post_paint_runtime_started=False,
        _auto_refresh_enabled=True,
        _startup_enabled=True,
        _initialize_auto_refresh_services=lambda: calls.append("init"),
        startup_orchestrator=SimpleNamespace(schedule_startup=lambda: calls.append("startup")),
        auto_refresh_scheduler=scheduler,
    )
    for _ in range(12):
        main.MainWindowQT._start_post_paint_runtime(runtime_window)
        if runtime_window._post_paint_runtime_started:
            break
    assert calls == ["retention", "init", "startup", "scheduler"]
    assert runtime_window._post_paint_runtime_started is True
    main.MainWindowQT._start_post_paint_runtime(runtime_window)

    failures = [True]
    retry_timer = _Timer()
    broken = SimpleNamespace(
        _is_closing=False,
        _post_paint_runtime_started=False,
        _auto_refresh_enabled=False,
        _startup_enabled=True,
        startup_orchestrator=SimpleNamespace(
            schedule_startup=lambda: (_ for _ in ()).throw(RuntimeError("bad")) if failures else calls.append("retry")
        ),
        auto_refresh_scheduler=None,
        _post_paint_runtime_timer=retry_timer,
    )
    for _ in range(12):
        main.MainWindowQT._start_post_paint_runtime(broken)
        if retry_timer.delay_ms == 250:
            break
    assert broken._post_paint_runtime_started is False
    assert broken._post_paint_tab_activation_finished is True
    assert broken._post_paint_auto_refresh_initialized is True
    assert getattr(broken, "_post_paint_scheduler_started", False) is False
    assert retry_timer.started is True
    assert retry_timer.delay_ms == 250

    failures.clear()
    retry_timer.active = False
    for _ in range(4):
        main.MainWindowQT._start_post_paint_runtime(broken)
        if broken._post_paint_runtime_started:
            break
    assert broken._post_paint_runtime_started is True
    assert broken._post_paint_scheduler_started is True
    assert calls[-1] == "retry"


def test_post_paint_core_services_attach_engine_before_quotes(monkeypatch):
    calls = []
    workspace = SimpleNamespace(engine=None)
    window = SimpleNamespace(
        engine=None,
        central_quotes_svc=None,
        _workspace=workspace,
        _init_central_broadcaster=lambda: calls.append("quotes"),
    )
    monkeypatch.setattr(main, "create_scan_engine", lambda: calls.append("engine") or "scan-engine")

    assert main._initialize_post_paint_scan_engine(window) is True
    assert main._initialize_post_paint_central_quotes(window) is True
    assert window.engine == "scan-engine"
    assert workspace.engine == "scan-engine"
    assert calls == ["engine", "quotes"]


def test_disconnect_main_window_runtime_signals_is_complete_and_idempotent():
    disconnected = []

    class Signal:
        def __init__(self, name):
            self.name = name

        def disconnect(self, slot):
            disconnected.append((self.name, slot.__name__))

    domain_bus = SimpleNamespace(
        sig_network_status_changed=Signal("network"),
        sig_rt_quotes=Signal("quotes"),
    )
    ui_bus = SimpleNamespace(
        sig_task_progress=Signal("progress"),
        sig_show_kline=Signal("kline"),
        sig_show_kline_with_list=Signal("kline_list"),
    )
    theme_bus = SimpleNamespace(sig_theme_changed=Signal("theme"))
    window = SimpleNamespace(
        _update_network_ui=lambda: None,
        _on_rt_quotes_pulse=lambda: None,
        _on_task_progress=lambda: None,
        _on_show_kline=lambda: None,
        _on_show_kline_with_list=lambda: None,
        _apply_theme=lambda: None,
    )

    main._disconnect_main_window_runtime_signals(
        window,
        domain_bus=domain_bus,
        ui_bus=ui_bus,
        theme_bus=theme_bus,
    )
    main._disconnect_main_window_runtime_signals(
        window,
        domain_bus=domain_bus,
        ui_bus=ui_bus,
        theme_bus=theme_bus,
    )

    assert [name for name, _slot in disconnected] == [
        "network",
        "quotes",
        "progress",
        "kline",
        "kline_list",
        "theme",
    ]


def test_workspace_tab_activation_waits_for_first_paint():
    calls = []
    workspace = SimpleNamespace(
        schedule_restore_last_tab=lambda target, *, delay_ms: calls.append((target, delay_ms)),
        tab_specs=lambda: [{"key": "watchlist"}, {"key": "scan"}],
    )
    window = SimpleNamespace(
        _workspace=workspace,
        _post_paint_runtime_started=False,
        _restore_last_tab_enabled=True,
        _app_config=SimpleNamespace(last_active_tab=0, last_active_tab_key="scan"),
    )

    main._queue_workspace_tab_activation(window, workspace)
    assert calls == []
    main._activate_pending_workspace_tab(window)
    assert calls == [("scan", main.POST_PAINT_TAB_ACTIVATION_DELAY_MS)]

    window._restore_last_tab_enabled = False
    window._post_paint_runtime_started = True
    main._queue_workspace_tab_activation(window, workspace)
    assert calls[-1] == (0, 0)


def test_kline_prewarm_is_a_post_paint_stage_after_tab_activation(monkeypatch):
    calls = []
    scheduled = []
    window = SimpleNamespace(_kline_prewarm_enabled=True, _is_closing=False)
    monkeypatch.setattr(main.kline_manager, "prewarm", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(main.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    labels = [label for _flag, label, _action in main._post_paint_stage_specs(window)]

    assert labels.index("tab_activation") < labels.index("kline_prewarm")
    assert main._schedule_post_paint_kline_prewarm(window) is True
    assert calls == []
    assert scheduled[0][0] == main.WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS
    scheduled.pop(0)[1]()
    assert calls == [{"main_window": window, "delay_ms": 0, "hidden_view": True}]

    window._kline_prewarm_enabled = False
    assert main._schedule_post_paint_kline_prewarm(window) is True
    assert len(calls) == 1


def test_kline_prewarm_waits_for_shell_navigation_quiet_window(monkeypatch):
    calls = []
    scheduled = []
    workspace = SimpleNamespace(_last_shell_nav_load_at=99.5)
    window = SimpleNamespace(
        _workspace=workspace,
        _kline_prewarm_enabled=True,
        _is_closing=False,
        _pending_f5_request=False,
        _f5_precompute_start_pending=False,
        _f5_job_controller=None,
    )
    monkeypatch.setattr(main.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main.kline_manager, "prewarm", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(main.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    main._try_post_paint_kline_prewarm(window)

    assert calls == []
    assert scheduled[0][0] == main.KLINE_PREWARM_BUSY_RETRY_DELAY_MS
    workspace._last_shell_nav_load_at = 0.0
    scheduled.pop(0)[1]()
    assert calls == [{"main_window": window, "delay_ms": 0, "hidden_view": True}]


def test_kline_prewarm_waits_until_tab_preload_and_quiet_tail_finish(monkeypatch):
    calls = []
    scheduled = []
    workspace = SimpleNamespace(
        _background_prewarm_enabled=True,
        _background_prewarm_finished=False,
        _background_prewarm_finished_at=0.0,
        _last_shell_nav_load_at=0.0,
    )
    window = SimpleNamespace(
        _workspace=workspace,
        _kline_prewarm_enabled=True,
        _is_closing=False,
        _pending_f5_request=False,
        _f5_precompute_start_pending=False,
        _f5_job_controller=None,
    )
    monkeypatch.setattr(main.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main.kline_manager, "prewarm", lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(main.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    main._try_post_paint_kline_prewarm(window)
    assert calls == []
    workspace._background_prewarm_finished = True
    workspace._background_prewarm_finished_at = 95.0
    scheduled.pop(0)[1]()
    assert calls == []
    workspace._background_prewarm_finished_at = 90.0
    scheduled.pop(0)[1]()

    assert calls == [{"main_window": window, "delay_ms": 0, "hidden_view": True}]


def test_central_broadcaster_and_code_count_paths():
    calls = []
    window = SimpleNamespace(
        _central_quotes_enabled=False, _bootstrap=SimpleNamespace(install_central_quotes=lambda: calls.append(True))
    )
    main.MainWindowQT._init_central_broadcaster(window)
    assert window.central_quotes_svc is None
    window._central_quotes_enabled = True
    main.MainWindowQT._init_central_broadcaster(window)
    assert calls

    labels = []
    provider = SimpleNamespace(cache_data={}, code2name={"000001": "A", "600000": "B", "123": "x", "900000": "x"})
    count_window = SimpleNamespace(
        data_provider=provider,
        lbl_code_count=SimpleNamespace(setText=lambda text: labels.append(text)),
        _is_display_a_share_code=main.MainWindowQT._is_display_a_share_code,
    )
    assert main.MainWindowQT._refresh_code_count_label_from_provider(count_window) == 2
    provider.cache_data = {"a": 1}
    assert main.MainWindowQT._refresh_code_count_label_from_provider(count_window) == 1
    assert main.MainWindowQT._is_display_a_share_code("688001")
    assert not main.MainWindowQT._is_display_a_share_code(None)
    assert not main.MainWindowQT._is_display_a_share_code("900001")


def test_network_and_shell_delegates(monkeypatch):
    calls = []
    import ui.main_window_network as network
    import ui.main_window_runtime as runtime
    import ui.main_window_visuals as visuals

    monkeypatch.setattr(runtime, "safe_run_post_online_refresh", lambda *args: calls.append("online"))
    monkeypatch.setattr(network, "toggle_network", lambda window: calls.append("toggle"))
    monkeypatch.setattr(network, "update_network_ui", lambda *args, **kwargs: calls.append(("ui", kwargs)))
    monkeypatch.setattr(network, "force_reconnect", lambda window: calls.append("reconnect"))
    monkeypatch.setattr(visuals, "apply_table_density", lambda *args, **kwargs: calls.append(("density", kwargs)))
    monkeypatch.setattr(visuals, "show_trade_calendar", lambda window: calls.append("calendar"))
    window = SimpleNamespace(data_provider=object(), engine=object())
    main.MainWindowQT._on_smart_startup_online_done(window)
    main.MainWindowQT._toggle_network(window)
    main.MainWindowQT._update_network_ui(window, True, "ok")
    main.MainWindowQT._force_reconnect(window)
    main.MainWindowQT._apply_table_density(window, "compact", persist=False)
    main.MainWindowQT._show_trade_calendar(window)
    assert len(calls) == 6


def test_splash_gear_and_public_port_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "setup_system_menu", lambda window: calls.append("menu"))
    splash = SimpleNamespace(set_progress=lambda *args: calls.append(args))
    window = SimpleNamespace(_splash=splash, _update_last_f5_time=lambda: calls.append("f5"))
    main.MainWindowQT._splash_update(window, 50, "half")
    main.MainWindowQT._init_gear_menu(window)
    assert calls == [(50, "half"), "menu", "f5"]
    main.MainWindowQT._splash_update(SimpleNamespace(_splash=None), 1)

    ports = []
    port_window = SimpleNamespace(
        _action_refresh_f5=lambda: ports.append("sync"),
        _activate_workspace_tab=lambda index: ports.append(("tab", index)),
        _apply_table_density=lambda *args, **kwargs: ports.append(("density", args, kwargs)),
        _on_show_kline=lambda code: ports.append(("chart", code)),
    )
    main.MainWindowQT.trigger_global_sync(port_window)
    main.MainWindowQT.activate_workspace_tab(port_window, 2)
    main.MainWindowQT.apply_table_density(port_window, "compact", False)
    main.MainWindowQT.open_security_chart(port_window, "1")
    assert len(ports) == 4


def test_escape_shortcut_palette_popup_modal_and_line_edit(monkeypatch):
    palette = SimpleNamespace(isVisible=lambda: True, reject=lambda: setattr(palette, "rejected", True))
    window = SimpleNamespace(_command_palette=palette)
    main.MainWindowQT._handle_escape_shortcut(window)
    assert palette.rejected

    popup = SimpleNamespace(close=lambda: setattr(popup, "closed", True))
    app = SimpleNamespace(activePopupWidget=lambda: popup, activeModalWidget=lambda: None)
    monkeypatch.setattr(main.QApplication, "instance", lambda: app)
    window = SimpleNamespace(_command_palette=None)
    main.MainWindowQT._handle_escape_shortcut(window)
    assert popup.closed

    modal = SimpleNamespace(reject=lambda: setattr(modal, "rejected", True))
    app.activePopupWidget = lambda: None
    app.activeModalWidget = lambda: modal
    main.MainWindowQT._handle_escape_shortcut(window)
    assert modal.rejected

    edit = QLineEdit()
    edit.setFocus()
    app.activeModalWidget = lambda: None
    window.focusWidget = lambda: edit
    main.MainWindowQT._handle_escape_shortcut(window)


def test_activate_tab_current_key_and_stall_context(monkeypatch):
    tabs = _Tabs(index=1)
    activated = []
    workspace = SimpleNamespace(
        activate_tab=lambda index, reason: activated.append((index, reason)) or True,
        tab_specs=lambda: [{"key": "watchlist"}, {"key": "system_log"}],
    )
    window = SimpleNamespace(tabs=tabs, _workspace=workspace)
    main.MainWindowQT._activate_workspace_tab(window, 1)
    assert activated == [(1, "command")]
    workspace.activate_tab = lambda *args, **kwargs: False
    main.MainWindowQT._activate_workspace_tab(window, 2)
    assert tabs.index == 2
    assert main.MainWindowQT._current_workspace_tab_key(window) == ""
    tabs.index = 1
    assert main.MainWindowQT._current_workspace_tab_key(window) == "system_log"
    tabs.currentIndex = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    assert main.MainWindowQT._current_workspace_tab_key(window) == ""
    assert main.MainWindowQT._current_workspace_tab_key(SimpleNamespace(_workspace=None, tabs=tabs)) == ""

    monkeypatch.setattr(main.time, "perf_counter", lambda: 5.0)
    window._current_workspace_tab_key = lambda: "system_log"
    window._f5_precompute_ui_grace_until = 10.0
    tabs.currentIndex = lambda: 0
    context = main.MainWindowQT._ui_stall_context(window)
    assert context["background"] == "f5_precompute" and context["widget"] == "SimpleNamespace"
    window._f5_precompute_ui_grace_until = "bad"
    main.MainWindowQT._ui_stall_context(window)


def test_rebind_chrome_and_quote_supplier(monkeypatch):
    calls = []
    tabs = _Tabs()
    nav = SimpleNamespace(bind_workspace=lambda *args: calls.append(args))
    window = SimpleNamespace(tabs=tabs, _workspace="workspace", _shell_navigation_widget=nav)
    main.MainWindowQT._rebind_workspace_chrome(window)
    assert tabs.bar_visible is False and calls
    monkeypatch.setattr(main, "inject_standalone_tabbar", lambda window: "standalone")
    window._shell_navigation_widget = None
    main.MainWindowQT._rebind_workspace_chrome(window)
    assert window._standalone_tabbar == "standalone"
    monkeypatch.setattr(main, "inject_standalone_tabbar", lambda window: (_ for _ in ()).throw(RuntimeError("bad")))
    main.MainWindowQT._rebind_workspace_chrome(window)

    supplied = []
    service = SimpleNamespace(set_code_supplier=lambda value: supplied.append(value))
    window.central_quotes_svc = service
    window._workspace = SimpleNamespace(get_realtime_quote_codes=lambda: ["1"])
    main.MainWindowQT._refresh_central_quote_code_supplier(window)
    assert callable(supplied[-1])
    main.MainWindowQT._refresh_central_quote_code_supplier(SimpleNamespace(central_quotes_svc=None))
    main.MainWindowQT._refresh_central_quote_code_supplier(
        SimpleNamespace(central_quotes_svc=object(), _workspace=None)
    )


def test_replace_workspace_success_restore_and_old_cleanup(monkeypatch):
    calls = []
    old_tabs = _Tabs()
    old = SimpleNamespace(
        tabs=old_tabs, shutdown=lambda: calls.append("old_shutdown"), deleteLater=lambda: calls.append("old_delete")
    )
    new_tabs = _Tabs(index=2)
    new = SimpleNamespace(
        tabs=new_tabs,
        schedule_restore_last_tab=lambda index, *, delay_ms: calls.append(("restore", index, delay_ms)),
    )
    layout = SimpleNamespace(
        addWidget=lambda *args: calls.append(("add", args)),
        removeWidget=lambda widget: calls.append(("remove", widget)),
    )
    window = SimpleNamespace(
        _workspace=old,
        tabs=old_tabs,
        _tabs_wrapper_layout=layout,
        _restore_last_tab_enabled=True,
        _post_paint_runtime_started=True,
        _app_config=SimpleNamespace(last_active_tab=3),
        _kline_prewarm_enabled=False,
        install_workspace_table_copy_hooks=lambda: calls.append("hooks"),
        _remember_last_active_tab=lambda index: None,
        _rebind_workspace_chrome=lambda: calls.append("rebind"),
        _refresh_central_quote_code_supplier=lambda: calls.append("supplier"),
    )
    assert main.MainWindowQT._replace_workspace_impl(window, new) is new
    assert window._workspace is new and "old_shutdown" in calls and "old_delete" in calls
    assert ("restore", 3, 0) in calls

    keyed_tabs = _Tabs(index=0)
    keyed = SimpleNamespace(
        tabs=keyed_tabs,
        tab_specs=lambda: [{"key": "watchlist"}, {"key": "scan"}],
        schedule_restore_last_tab=lambda target, *, delay_ms: calls.append(("key", target, delay_ms)),
    )
    window._workspace = new
    window.tabs = new_tabs
    window._app_config.last_active_tab_key = "scan"
    main.MainWindowQT._replace_workspace_impl(window, keyed)
    assert ("key", "scan", 0) in calls

    fallback = SimpleNamespace(
        tabs=_Tabs(index=0),
        tab_specs=lambda: [{"key": "watchlist"}, {"key": "lhb"}],
        schedule_restore_last_tab=lambda target, *, delay_ms: calls.append(("fallback", target, delay_ms)),
    )
    window._workspace = keyed
    window.tabs = keyed_tabs
    window._app_config.last_active_tab_key = "removed_tab"
    window._app_config.last_active_tab = 1
    main.MainWindowQT._replace_workspace_impl(window, fallback)
    assert ("fallback", 1, 0) in calls

    fresh_tabs = _Tabs(index=2)
    fresh = SimpleNamespace(tabs=fresh_tabs, restore_last_tab=fresh_tabs.setCurrentIndex)
    window._workspace = None
    window.tabs = None
    window._restore_last_tab_enabled = False
    main.MainWindowQT._replace_workspace_impl(window, fresh)
    assert fresh_tabs.index == 0


def test_replace_workspace_failure_rolls_back_and_deletes(monkeypatch):
    calls = []
    old_tabs = _Tabs()
    old = SimpleNamespace(tabs=old_tabs)
    new_tabs = _Tabs()
    new = SimpleNamespace(
        tabs=new_tabs,
        schedule_restore_last_tab=lambda index, *, delay_ms: (_ for _ in ()).throw(RuntimeError("bad")),
        shutdown=lambda: calls.append("shutdown"),
        deleteLater=lambda: calls.append("delete"),
    )
    window = SimpleNamespace(
        _workspace=old,
        tabs=old_tabs,
        _tabs_wrapper_layout=SimpleNamespace(
            addWidget=lambda *args: None, removeWidget=lambda widget: calls.append("remove")
        ),
        _restore_last_tab_enabled=True,
        _post_paint_runtime_started=True,
        _app_config=SimpleNamespace(last_active_tab=1),
        _kline_prewarm_enabled=False,
        _remember_last_active_tab=lambda index: None,
        _rebind_workspace_chrome=lambda: calls.append("rebind"),
    )
    with pytest.raises(RuntimeError):
        main.MainWindowQT._replace_workspace_impl(window, new)
    assert window._workspace is old and calls == ["shutdown", "remove", "delete", "rebind"]


def test_titlebar_sync_tooltip_pulse_progress_and_show_kline(monkeypatch):
    states = []
    feedback = []
    window = SimpleNamespace(
        _last_sync_freshness="old",
        _titlebar_sync_widget=SimpleNamespace(set_state=lambda *args, **kwargs: states.append((args, kwargs))),
        _status_bar_widget=SimpleNamespace(show_sync_feedback=lambda state: feedback.append(state)),
    )
    main.MainWindowQT._set_titlebar_sync_state(window, "", "detail", "fresh")
    assert window._titlebar_sync_state == "idle" and window._last_sync_freshness == "fresh"
    assert states and feedback == ["idle"]
    main.MainWindowQT._set_titlebar_sync_state(SimpleNamespace(_last_sync_freshness=""), "ok")

    assert main.MainWindowQT._tooltip_text_for_event(window, None, object()) == ""
    assert (
        main.MainWindowQT._tooltip_text_for_event(
            window, SimpleNamespace(objectName=lambda: "floatingTooltip"), object()
        )
        == ""
    )
    obj = SimpleNamespace(objectName=lambda: "x", parent=lambda: None, toolTip=lambda: " tip ")
    assert main.MainWindowQT._tooltip_text_for_event(window, obj, object()) == "tip"

    pulses = []
    quote_payloads = []
    pulse_window = SimpleNamespace(
        _titlebar_sync_widget=SimpleNamespace(
            pulse_quotes=lambda: pulses.append(True),
            set_quote_status=lambda payload: quote_payloads.append(payload),
        )
    )
    main.MainWindowQT._on_rt_quotes_pulse(pulse_window, None)
    main.MainWindowQT._on_rt_quotes_pulse(pulse_window, {"1": {}})
    assert pulses == [True]
    assert quote_payloads == [{"1": {}}]
    main.MainWindowQT._on_rt_quotes_pulse(SimpleNamespace(_titlebar_sync_widget=None), {"1": {}})

    progress = []
    progress_window = SimpleNamespace(
        progress_bar=SimpleNamespace(setValue=lambda value: progress.append(value)),
        lbl_status=SimpleNamespace(setText=lambda text: progress.append(text)),
    )
    main.MainWindowQT._on_task_progress(progress_window, "other", 1, "x")
    main.MainWindowQT._on_task_progress(progress_window, "scan", 50, "half")
    assert progress == [50, "half"]

    show = []
    show_window = SimpleNamespace(_on_show_kline_with_list=lambda *args: show.append(args))
    main.MainWindowQT._on_show_kline(show_window, "1")
    assert show == [("1", [], 0)]


def test_show_kline_request_and_apply_theme(monkeypatch):
    import app.services.kline_open_service as open_service
    import ui.main_window_visuals as visuals

    monkeypatch.setattr(
        open_service,
        "build_kline_open_context",
        lambda **kwargs: SimpleNamespace(
            code=kwargs["code"],
            name="One",
            current_idx=kwargs["current_idx"],
        ),
    )
    opened = []
    monkeypatch.setattr(main.kline_manager, "open_chart", lambda **kwargs: opened.append(kwargs))
    provider_notices = []
    monkeypatch.setattr(
        main.kline_manager,
        "notify_data_provider_preparing",
        lambda *args: provider_notices.append(args[1]),
    )
    workspace = SimpleNamespace(tab_specs=lambda: [{"key": "scan"}])
    window = SimpleNamespace(
        _workspace=workspace,
        tabs=_Tabs(index=0),
        data_provider=SimpleNamespace(code2name={"1": "One"}),
    )
    main.MainWindowQT._on_show_kline_with_list(window, "1", [{"code": "1"}], 0)
    assert opened[-1]["name"] == "One"
    assert opened[-1]["open_context"].code == "1"
    window.data_provider = None
    main.MainWindowQT._on_show_kline_with_list(window, "000001", [], 0)
    assert provider_notices == []
    assert len(opened) == 2 and opened[-1]["data_provider"] is None
    main.MainWindowQT._on_show_kline_with_list(window, "2330.TW", [], 0)
    assert len(opened) == 3 and opened[-1]["data_provider"] is None
    window.tabs = None
    window._workspace = None
    main.MainWindowQT._on_show_kline_with_list(window, "2", [], 0)

    applied = []
    monkeypatch.setattr(visuals, "apply_theme", lambda *args, **kwargs: applied.append(kwargs))
    main.MainWindowQT._apply_theme(SimpleNamespace(isVisible=lambda: True))
    assert applied == [{"notify": True}]


def test_launch_action_checked_and_toggle_outcomes(monkeypatch):
    import app.services.ui_autostart_service as autostart
    import ui.components.toast_widget as toast

    class Action:
        def __init__(self):
            self.checked = None
            self.blocked = False

        def blockSignals(self, value):
            previous = self.blocked
            self.blocked = value
            return previous

        def setChecked(self, value):
            self.checked = value

    action = Action()
    window = SimpleNamespace(_act_launch_at_login=action)
    main.MainWindowQT._set_launch_at_login_action_checked(window, True)
    assert action.checked is True and action.blocked is False
    main.MainWindowQT._set_launch_at_login_action_checked(SimpleNamespace(_act_launch_at_login=None), True)

    messages = []
    monkeypatch.setattr(toast, "show_toast", lambda *args, **kwargs: messages.append(args))
    monkeypatch.setattr(autostart, "set_launch_at_login_enabled", lambda *args: None)
    toggle_window = SimpleNamespace(
        _project_root="root",
        _set_launch_at_login_action_checked=lambda value: messages.append(("checked", value)),
        _is_launch_at_login_enabled=lambda: False,
    )
    main.MainWindowQT._toggle_launch_at_login(toggle_window, True)
    main.MainWindowQT._toggle_launch_at_login(toggle_window, False)
    assert ("checked", True) in messages and ("checked", False) in messages
    monkeypatch.setattr(autostart, "set_launch_at_login_enabled", lambda *args: (_ for _ in ()).throw(OSError("bad")))
    main.MainWindowQT._toggle_launch_at_login(toggle_window, True)


def test_f5_action_cancel_and_confirm(monkeypatch):
    import ui.components.message_box as boxes
    import ui.main_window_runtime as runtime

    monkeypatch.setattr(boxes, "show_themed_question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window = SimpleNamespace(data_provider=object(), engine=object())
    main.MainWindowQT._action_refresh_f5(window)
    started = []
    monkeypatch.setattr(boxes, "show_themed_question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(runtime, "start_f5_precompute", lambda *args, **kwargs: started.append((args, kwargs)))
    labels = []
    window = SimpleNamespace(
        data_provider=object(),
        engine=object(),
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
        _set_titlebar_sync_state=lambda *args: labels.append(args),
    )
    main.MainWindowQT._action_refresh_f5(window)
    assert started and window._f5_cancelled is False and labels


def test_f5_action_waits_for_post_paint_runtime_and_replays(monkeypatch):
    callbacks = []
    labels = []
    window = SimpleNamespace(
        data_provider=None,
        engine=None,
        _is_closing=False,
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
        _set_titlebar_sync_state=lambda *args: labels.append(args),
    )
    main.MainWindowQT._action_refresh_f5(window)
    assert window._pending_f5_request is True
    assert labels

    monkeypatch.setattr(main.QTimer, "singleShot", lambda _delay, callback: callbacks.append(callback))
    replayed = []
    window.data_provider = object()
    window.engine = object()
    window._action_refresh_f5 = lambda: replayed.append(True)
    main._replay_pending_f5_request(window)
    assert window._pending_f5_request is False
    assert len(callbacks) == 1
    callbacks[0]()
    assert replayed == [True]


def test_workspace_factory_wrapper_and_central_quotes_factory(monkeypatch):
    import ui.workers.central_quotes_worker as quotes
    import ui.workspaces as workspaces

    created = []
    monkeypatch.setattr(
        workspaces, "ClassicWorkspace", lambda *args, **kwargs: created.append((args, kwargs)) or "workspace"
    )
    monkeypatch.setattr(
        quotes, "CentralQuotesService", lambda *args, **kwargs: created.append((args, kwargs)) or "quotes"
    )
    monkeypatch.setattr(main, "ui_stall_span", lambda *args, **kwargs: nullcontext())
    window = SimpleNamespace(
        data_provider="provider",
        engine="engine",
        tabs_wrapper="wrapper",
        _workspace_background_prewarm=True,
        _startup_enabled=False,
        _controlled_startup_probe_guard=True,
        _current_workspace_tab_key=lambda: "scan",
    )
    assert main.MainWindowQT.create_workspace(window) == "workspace"
    parent = object()
    assert main.MainWindowQT.create_workspace(window, parent=parent) == "workspace"
    assert created[-1][1]["parent"] is parent
    assert main.MainWindowQT.create_central_quotes_service(window, code_supplier=lambda: []) == "quotes"
    window._replace_workspace_impl = lambda workspace: ("replaced", workspace)
    assert main.MainWindowQT.replace_workspace(window, "new") == ("replaced", "new")


def test_command_palette_reuses_dialog(monkeypatch):
    import ui.components.command_palette as palette_module

    class Palette:
        def __init__(self, parent):
            self.calls = []

        def set_dynamic_provider(self, provider):
            self.calls.append(("provider", provider))

        def set_commands(self, commands):
            self.calls.append(("commands", commands))

        def show(self):
            self.calls.append("show")

        def raise_(self):
            self.calls.append("raise")

        def activateWindow(self):
            self.calls.append("activate")

    monkeypatch.setattr(palette_module, "CommandPaletteDialog", Palette)
    service = SimpleNamespace(build_stock_commands=lambda: [], build_commands=lambda: [1])
    window = SimpleNamespace(_command_palette=None, _command_service=service)
    main.MainWindowQT._open_command_palette(window)
    first = window._command_palette
    main.MainWindowQT._open_command_palette(window)
    assert window._command_palette is first and first.calls.count("show") == 2


def test_runtime_health_dialog_new_and_existing(monkeypatch):
    import ui.components.runtime_health_dialog as health

    class Dialog:
        def __init__(self, *args, **kwargs):
            self.destroyed = _Signal()
            self.calls = []

        def refresh(self):
            self.calls.append("refresh")

        def show(self):
            self.calls.append("show")

        def raise_(self):
            self.calls.append("raise")

        def activateWindow(self):
            self.calls.append("activate")

    monkeypatch.setattr(health, "RuntimeHealthDialog", Dialog)
    window = SimpleNamespace(_runtime_health_dialog=None)
    main.MainWindowQT._open_runtime_health(window)
    dialog = window._runtime_health_dialog
    main.MainWindowQT._open_runtime_health(window)
    assert "refresh" in dialog.calls
    dialog.destroyed.callbacks[0]()
    assert window._runtime_health_dialog is None


def test_custom_titlebar_tabbar_and_maximize(monkeypatch):
    refs = SimpleNamespace(
        titlebar="title",
        layout="layout",
        placeholder="placeholder",
        pulse_strip="pulse",
        btn_minimize="min",
        btn_maximize="max",
        btn_close="close",
    )
    monkeypatch.setattr(main, "setup_custom_titlebar", lambda *args: refs)
    monkeypatch.setattr(main, "inject_standalone_tabbar", lambda window: "tabs")
    icons = []
    monkeypatch.setattr(main, "set_button_svg_icon", lambda *args, **kwargs: icons.append((args, kwargs)))
    window = SimpleNamespace()
    main.MainWindowQT._init_custom_titlebar(window, object())
    assert window._custom_titlebar == "title" and window._btn_close == "close"
    main.MainWindowQT._inject_tabbar_into_titlebar(window)
    assert window._standalone_tabbar == "tabs"

    main.MainWindowQT._sync_maximize_button_icon(SimpleNamespace())
    state = {"max": False}
    window = SimpleNamespace(
        _btn_maximize=object(),
        isMaximized=lambda: state["max"],
        showNormal=lambda: state.update(max=False),
        showMaximized=lambda: state.update(max=True),
        _sync_maximize_button_icon=lambda: icons.append("sync"),
    )
    main.MainWindowQT._sync_maximize_button_icon(window)
    assert icons[-1][0][1] == "maximize"
    main.MainWindowQT._toggle_maximize(window)
    assert state["max"] and icons[-1] == "sync"
    main.MainWindowQT._toggle_maximize(window)
    assert not state["max"]


def test_workspace_tables_copy_hooks_and_save_state(monkeypatch):
    hooks = []
    monkeypatch.setattr(main, "install_table_copy_hooks", lambda tables: hooks.append(tables))
    config = SimpleNamespace(last_active_tab=0)
    window = SimpleNamespace(_app_config=config)
    main.MainWindowQT._remember_last_active_tab(window, 2)
    assert config.last_active_tab == 2

    config = SimpleNamespace(last_active_tab=0, last_active_tab_key="")
    window = SimpleNamespace(
        _app_config=config,
        _workspace=SimpleNamespace(tab_specs=lambda: [{"key": "watchlist"}, {"key": "scan"}]),
    )
    main.MainWindowQT._remember_last_active_tab(window, 1)
    assert config.last_active_tab == 1
    assert config.last_active_tab_key == "scan"
    assert main.MainWindowQT.iter_workspace_tables(SimpleNamespace(_workspace=None)) == []
    assert main.MainWindowQT.iter_workspace_tables(SimpleNamespace(_workspace=object())) == []
    mounted_table = SimpleNamespace(window=lambda: window)
    staged_table = SimpleNamespace(window=lambda: object())
    workspace = SimpleNamespace(iter_tables=lambda: [mounted_table, staged_table])
    window._workspace = workspace
    window.iter_workspace_tables = lambda: main.MainWindowQT.iter_workspace_tables(window)
    main.MainWindowQT.install_workspace_table_copy_hooks(window)
    assert hooks == [[mounted_table]]
    main.MainWindowQT.install_workspace_table_copy_hooks(window, tables=[staged_table])
    assert hooks == [[mounted_table], [staged_table]]

    values = []
    settings = SimpleNamespace(
        setValue=lambda *args: values.append(args),
        sync=lambda: values.append(("sync",)),
    )
    tabs = _Tabs(index=1)
    window = SimpleNamespace(
        _settings=settings,
        tabs=tabs,
        _remember_last_active_tab=lambda index: values.append(("tab", index)),
        saveGeometry=lambda: "geometry",
    )
    main.MainWindowQT._save_ui_state(window)
    assert ("geometry", "geometry") in values and ("geometry_version", 2) in values
    window.tabs = None
    main.MainWindowQT._save_ui_state(window)


def test_restore_ui_state_cached_failure_screen_and_no_screen(monkeypatch):
    class Settings:
        def __init__(self, version, geometry):
            self.version, self.geometry = version, geometry

        def value(self, key, default=None, type=None):
            return self.version if key == "geometry_version" else self.geometry

    restored = []
    window = SimpleNamespace(_settings=Settings(2, "geom"), restoreGeometry=lambda value: restored.append(value))
    main.MainWindowQT._restore_ui_state(window)
    assert restored == ["geom"]

    geometry = SimpleNamespace(
        width=lambda: 1000,
        height=lambda: 800,
        center=lambda: "center",
    )
    frame = SimpleNamespace(moveCenter=lambda value: restored.append(value), topLeft=lambda: "top-left")
    screen = SimpleNamespace(availableGeometry=lambda: geometry)
    monkeypatch.setattr(main, "QApplication", SimpleNamespace(primaryScreen=lambda: screen))
    window = SimpleNamespace(
        _settings=Settings(0, None),
        resize=lambda *args: restored.append(args),
        frameGeometry=lambda: frame,
        move=lambda value: restored.append(value),
    )
    main.MainWindowQT._restore_ui_state(window)
    assert (800, 560) in restored and "top-left" in restored
    monkeypatch.setattr(main, "QApplication", SimpleNamespace(primaryScreen=lambda: None))
    main.MainWindowQT._restore_ui_state(window)
    assert (1024, 768) in restored

    monkeypatch.setattr(main, "QApplication", SimpleNamespace(primaryScreen=lambda: None))
    broken = SimpleNamespace(
        _settings=Settings(2, "geom"),
        restoreGeometry=lambda value: (_ for _ in ()).throw(RuntimeError("bad")),
        resize=lambda *args: restored.append(args),
    )
    main.MainWindowQT._restore_ui_state(broken)


def test_update_last_f5_time_and_completion_delegate(monkeypatch):
    labels = []
    monkeypatch.setattr(main, "active_rps_cache_mtime", lambda path: 1_700_000_000)
    window = SimpleNamespace(
        act_f5=SimpleNamespace(setText=lambda text: labels.append(text)),
        _titlebar_sync_state="idle",
        _set_titlebar_sync_state=lambda *args: labels.append(args),
    )
    main.MainWindowQT._update_last_f5_time(window)
    assert window._last_sync_freshness and labels
    monkeypatch.setattr(main, "active_rps_cache_mtime", lambda path: 0)
    main.MainWindowQT._update_last_f5_time(window)
    assert "暂无" in window._last_sync_freshness
    window._titlebar_sync_state = "working"
    before = len(labels)
    main.MainWindowQT._update_last_f5_time(window)
    assert len(labels) == before + 1

    import ui.main_window_runtime as runtime

    calls = []
    monkeypatch.setattr(runtime, "finish_f5_reload", lambda *args, **kwargs: calls.append((args, kwargs)))
    main.MainWindowQT._on_f5_done(window, 3, 1.2)
    assert calls[-1][1]["count"] == 3


def test_autostart_support_and_enabled_probe(monkeypatch):
    import app.services.ui_autostart_service as autostart

    monkeypatch.setattr(autostart, "is_launch_at_login_supported", lambda root: root == "root")
    monkeypatch.setattr(autostart, "is_launch_at_login_enabled", lambda root: True)
    window = SimpleNamespace(_project_root="root")
    assert main.MainWindowQT._is_launch_at_login_supported(window)
    assert main.MainWindowQT._is_launch_at_login_enabled(window)
    monkeypatch.setattr(autostart, "is_launch_at_login_enabled", lambda root: (_ for _ in ()).throw(OSError("bad")))
    assert not main.MainWindowQT._is_launch_at_login_enabled(window)
