from __future__ import annotations

from PyQt6.QtWidgets import QApplication

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
