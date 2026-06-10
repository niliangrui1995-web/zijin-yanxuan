from __future__ import annotations

import inspect

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


def test_main_window_schedules_default_kline_preflight(monkeypatch, qt_application):
    task_manager = _FakeTaskManager()
    prewarm_calls = []
    monkeypatch.setattr("ui.main_window_qt.create_data_provider", lambda *, offline=True: _DummyProvider())
    monkeypatch.setattr("ui.main_window_qt.create_scan_engine", lambda: object())
    monkeypatch.setattr("ui.main_window_qt.task_manager", task_manager)
    monkeypatch.setattr(
        "ui.main_window_qt.kline_manager.prewarm",
        lambda **kwargs: prewarm_calls.append(kwargs) or True,
    )

    window = MainWindowQT(
        startup_enabled=False,
        background_prewarm=False,
        central_quotes_enabled=False,
        restore_last_tab_enabled=False,
    )
    try:
        _process_events()

        assert prewarm_calls == [{"delay_ms": 2500}]
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
        assert window.lbl_code_count.text() == "标的池: 1 只"
        assert window.startup_orchestrator._deferred_timer.isActive() is False
        assert window.startup_orchestrator._smart_timer.isActive() is False
        assert window.startup_orchestrator._auto_rt_timer is None
        assert window.auto_refresh_scheduler._timer.isActive() is False
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
