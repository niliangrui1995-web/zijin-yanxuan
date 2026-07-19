from __future__ import annotations

import inspect

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QWidget

from ui.main_window_qt import MainWindowQT


class _DummyProvider:
    def __init__(self):
        self.cache_data = {}
        self.code2name = {"000001": "Ping An"}
        self._offline = True
        self.online = False

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        return dict(self.code2name)

    def fetch_realtime_quotes_batch(self, codes):
        return {}

    def test_network(self, timeout=3):
        return False

    def set_online_mode(self, online):
        self.online = bool(online)


class _FakeTaskManager:
    def __init__(self):
        self.shutdown_calls = 0

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None, **_kwargs):
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(str(exc))
        else:
            if on_success is not None:
                on_success(result)
        return str(getattr(task_id, "task_id", task_id) or "fake-task")

    def cancel_task(self, _task_id, **_kwargs):
        return True

    def abandon_task(self, _task_id, **_kwargs):
        return True

    def wait_for_tasks(self, _task_ids, **_kwargs):
        return True

    def shutdown(self):
        self.shutdown_calls += 1


def _process_events(rounds: int = 4):
    app = QApplication.instance()
    if app is not None:
        for _ in range(rounds):
            app.processEvents()


def test_main_window_keeps_data_prewarm_and_kline_preflight_by_default():
    signature = inspect.signature(MainWindowQT)

    assert signature.parameters["background_prewarm"].default is True
    assert signature.parameters["kline_prewarm_enabled"].default is True


def test_main_window_schedules_default_kline_preflight_only_after_post_paint(monkeypatch, qt_application):
    task_manager = _FakeTaskManager()
    prewarm_calls = []
    monkeypatch.setattr("ui.main_window_qt.create_data_provider", lambda *, offline=True: _DummyProvider())
    monkeypatch.setattr("ui.main_window_qt.create_scan_engine", lambda: object())
    monkeypatch.setattr("ui.main_window_qt.task_manager", task_manager)
    monkeypatch.setattr(
        "ui.main_window_qt.kline_manager.prewarm",
        lambda **kwargs: prewarm_calls.append(kwargs) or True,
    )
    monkeypatch.setattr("ui.main_window_qt.WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS", 0)

    window = MainWindowQT(
        startup_enabled=False,
        background_prewarm=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
    )
    try:
        assert prewarm_calls == []

        window.show()
        _process_events(rounds=24)

        assert prewarm_calls == [{"main_window": window, "delay_ms": 0, "hidden_view": True}]
    finally:
        if not window._is_closing:
            window.close()
            _process_events()
        window.deleteLater()


def test_apply_theme_suppresses_toast_before_main_window_visible(monkeypatch, qt_application):
    from ui import main_window_visuals
    from ui.components import toast_widget

    calls = []
    monkeypatch.setattr(main_window_visuals, "apply_chrome_theme", lambda _window: None)
    monkeypatch.setattr(toast_widget, "show_toast", lambda *args, **kwargs: calls.append((args, kwargs)))

    window = QWidget()
    try:
        main_window_visuals.apply_theme(window, notify=True)
        assert calls == []

        window.show()
        qt_application.processEvents()
        main_window_visuals.apply_theme(window, notify=True)
        assert len(calls) == 1
    finally:
        window.close()
        window.deleteLater()


def test_main_window_builds_and_closes_with_controlled_background_services(monkeypatch, qt_application):
    task_manager = _FakeTaskManager()
    monkeypatch.setattr("ui.main_window_qt.create_data_provider", lambda *, offline=True: _DummyProvider())
    monkeypatch.setattr("ui.main_window_qt.create_scan_engine", lambda: object())
    monkeypatch.setattr("ui.main_window_qt.task_manager", task_manager)

    window = MainWindowQT(
        startup_enabled=False,
        background_prewarm=False,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
    )
    try:
        _process_events()

        assert window._workspace is not None
        assert window.tabs is not None
        assert window.lbl_code_count.text() == "标的池: 0"
        assert window.data_provider is None
        assert window.engine is None
        assert window.startup_orchestrator is None
        assert window.auto_refresh_scheduler is None
        assert window.na_daily_service is None
        assert window.asian_market_service is None
        assert window.earnings_refresh_service is None
        assert window.central_quotes_svc is None
        assert window._workspace._restore_last_tab_timer is None
        assert window._process_watchdog.running is True

        window.close()
        _process_events()

        assert window._is_closing is True
        assert window._process_watchdog.running is False
        assert task_manager.shutdown_calls == 1
    finally:
        if not window._is_closing:
            window.close()
            _process_events()
        window.deleteLater()


def test_main_window_starts_lightweight_runtime_once_after_real_paint(monkeypatch, qt_application):
    calls = []
    task_manager = _FakeTaskManager()

    class _StartupOrchestrator:
        def schedule_startup(self):
            calls.append("startup_scheduled")

        def shutdown(self):
            calls.append("startup_shutdown")

    class _AutoRefreshScheduler:
        def start(self):
            calls.append("auto_refresh_started")

        def shutdown(self):
            calls.append("auto_refresh_shutdown")

    class _PaintProbe(QObject):
        def eventFilter(self, watched, event):
            if watched is window and event.type() == QEvent.Type.Paint:
                calls.append("paint")
            return False

    def initialize_auto_refresh_services(window):
        calls.append("services_initialized")
        window.auto_refresh_scheduler = _AutoRefreshScheduler()

    monkeypatch.setattr("ui.main_window_qt.create_data_provider", lambda *, offline=True: _DummyProvider())
    monkeypatch.setattr("ui.main_window_qt.create_scan_engine", lambda: object())
    monkeypatch.setattr("ui.main_window_qt.create_startup_orchestrator", lambda _window: _StartupOrchestrator())
    monkeypatch.setattr("ui.main_window_qt.task_manager", task_manager)
    monkeypatch.setattr(MainWindowQT, "_initialize_auto_refresh_services", initialize_auto_refresh_services)
    monkeypatch.setattr("ui.main_window_qt.apply_windows_frameless_taskbar_fix", lambda _window: None)
    monkeypatch.setattr("ui.main_window_qt.enable_windows_native_shadow", lambda _window: None)
    monkeypatch.setattr("ui.main_window_qt.enable_windows_system_backdrop", lambda *_args, **_kwargs: None)

    window = MainWindowQT(
        startup_enabled=True,
        background_prewarm=False,
        kline_prewarm_enabled=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
    )
    probe = _PaintProbe(window)
    window.installEventFilter(probe)
    try:
        _process_events()
        assert window._first_paint_recorded is False
        assert window.auto_refresh_scheduler is None
        assert "startup_scheduled" not in calls

        window.show()
        _process_events(8)
        window.update()
        _process_events(4)

        assert calls.count("paint") >= 1
        assert calls.count("services_initialized") == 1
        assert calls.count("startup_scheduled") == 1
        assert calls.count("auto_refresh_started") == 1
        assert calls.index("paint") < calls.index("services_initialized")
    finally:
        if not window._is_closing:
            window.close()
            _process_events()
        window.deleteLater()
