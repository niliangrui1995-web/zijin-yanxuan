from __future__ import annotations

import subprocess
import types
from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.startup_orchestrator import (
    ASIAN_DATA_SYNC_TASK_ID,
    ASIAN_DATA_SYNC_TIMEOUT_SEC,
    AUTO_RT_MONITOR_NETWORK_TASK_ID,
    AUTO_RT_MONITOR_RETRY_INTERVAL_MS,
    DEFERRED_LOAD_TASK_ID,
    GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR,
    GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE,
    GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID,
    SMART_STARTUP_TASK_ID,
    StartupOrchestrator,
    ms_until_next_global_earnings_calendar_daily_refresh,
)


class _DummyLabel:
    def __init__(self):
        self.value = ""

    def setText(self, text):
        self.value = text


class _DummyCacheManager:
    def load_rt_cache(self, *_args, **_kwargs):
        return None

    def try_load_rps_from_disk(self, *_args, **_kwargs):
        return None


class _DummyDataProvider:
    def __init__(self):
        self.cache_data = {}

    def load_cache_from_disk(self):
        return ""

    def test_network(self, timeout=3):
        return False


class _DummyMainWindow(QObject):
    def __init__(self):
        super().__init__()
        self._is_closing = False
        self.data_provider = _DummyDataProvider()
        self.cache_manager = _DummyCacheManager()
        self.engine = object()
        self.table_rt = object()
        self.lbl_status = _DummyLabel()
        self.lbl_code_count = _DummyLabel()
        self.tab_watchlist = None
        self._workspace = None

    def _call_in_ui(self, callback):
        callback()


class _InlineJobRunner:
    def __init__(self):
        self.abandoned = []

    def run(self, task_id, fn, *args, **kwargs):
        result = fn()
        on_success = kwargs.get("on_success")
        if callable(on_success):
            on_success(result)
        return task_id

    def abandon(self, task_id):
        self.abandoned.append(task_id)
        return True


class _QueuedJobRunner:
    def __init__(self):
        self.jobs = []
        self.abandoned = []

    def run(self, task_id, fn, *args, **kwargs):
        self.jobs.append((task_id, fn, kwargs))
        return task_id

    def abandon(self, task_id):
        self.abandoned.append(task_id)
        return True


def test_startup_orchestrator_asian_sync_uses_process_runner(monkeypatch):
    runner = _InlineJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)
    run_calls = []

    def fake_exists(path):
        path = str(path)
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run_python_module(module_name, module_args=None, **kwargs):
        run_calls.append(
            {
                "module_name": module_name,
                "module_args": list(module_args or []),
                "kwargs": kwargs,
            }
        )
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr("core.startup_orchestrator.run_python_module", fake_run_python_module)

    orchestrator.deferred_data_load()

    assert run_calls, "expected asian sync subprocess to run"
    assert run_calls[0]["module_name"] == "vcp.fetchers.asian_kline_fetcher"
    assert run_calls[0]["module_args"][:2] == ["--strict-sync", "--output-dir"]
    assert run_calls[0]["module_args"][2]
    assert run_calls[0]["kwargs"]["timeout"] == ASIAN_DATA_SYNC_TIMEOUT_SEC
    assert Path(run_calls[0]["kwargs"]["cwd"]).name == Path(__file__).resolve().parents[1].name
    assert run_calls[0]["kwargs"]["capture_output"] is True
    assert run_calls[0]["kwargs"]["text"] is True
    assert run_calls[0]["kwargs"]["no_window"] is True


def test_startup_orchestrator_deferred_load_emits_cache_bootstrap_ready(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_cache_bootstrap_ready)

    def fake_exists(path):
        return not str(path).endswith("asian_kline_fetcher.py")

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)

    orchestrator.deferred_data_load()

    assert len(spy) == 1


def test_startup_orchestrator_deferred_load_records_process_snapshots(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    labels = []

    def fake_exists(path):
        return not str(path).endswith("asian_kline_fetcher.py")

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda label, **_kwargs: labels.append(label),
    )

    orchestrator.deferred_data_load()

    assert "startup.deferred_load.begin" in labels
    assert "startup.deferred_load.end" in labels


def test_startup_orchestrator_asian_sync_logs_succinct_failure_message(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    records = {"warning": [], "debug": []}

    class _FakeLog:
        def warning(self, message):
            records["warning"].append(message)

        def debug(self, message):
            records["debug"].append(message)

        def info(self, _message):
            return None

        def error(self, _message):
            return None

    def fake_exists(path):
        path = str(path)
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run_python_module(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["python", "asian_kline_fetcher.py"],
            stderr="connect failed\nHTTP 429 Too Many Requests",
        )

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr("core.startup_orchestrator.run_python_module", fake_run_python_module)
    monkeypatch.setattr("core.startup_orchestrator.log", _FakeLog())
    monkeypatch.setattr("core.startup_orchestrator.log_process_snapshot", lambda *_args, **_kwargs: None)

    orchestrator.deferred_data_load()

    assert len(records["warning"]) == 1
    assert "429" in records["warning"][0]
    assert "1" in records["warning"][0]
    assert len(records["debug"]) == 1
    assert "connect failed" in records["debug"][0]
    assert "HTTP 429 Too Many Requests" in records["debug"][0]


def test_startup_orchestrator_offline_network_log_is_visible_info(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    records = {"info": [], "debug": [], "error": []}

    class _FakeLog:
        def info(self, message):
            records["info"].append(message)

        def debug(self, message):
            records["debug"].append(message)

        def error(self, message):
            records["error"].append(message)

    monkeypatch.setattr("core.startup_orchestrator.log", _FakeLog())
    monkeypatch.setattr("core.startup_orchestrator.log_process_snapshot", lambda *_args, **_kwargs: None)

    orchestrator.smart_startup()

    assert len(records["info"]) == 1
    assert records["error"] == []
    assert records["debug"] == []


def test_startup_orchestrator_smart_startup_records_process_snapshots(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    labels = []

    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda label, **_kwargs: labels.append(label),
    )

    orchestrator.smart_startup()

    assert labels == ["startup.smart.begin", "startup.smart.end"]


def test_startup_orchestrator_skips_asian_sync_when_toggle_disabled(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    run_calls = []

    def fake_is_enabled(key, overrides=None):
        return False if key == "silent_asian_sync" else True

    monkeypatch.setattr("core.startup_orchestrator.service_toggle_registry.is_enabled", fake_is_enabled)
    monkeypatch.setattr(
        "core.startup_orchestrator.run_python_module",
        lambda *_args, **_kwargs: run_calls.append(True),
    )

    orchestrator.deferred_data_load()

    assert run_calls == []


def test_startup_orchestrator_skips_auto_rt_monitor_when_toggle_disabled(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    real_import = __import__

    def fake_is_enabled(key, overrides=None):
        return False if key == "workspace_auto_rt_monitor" else True

    def guarded_import(name, *args, **kwargs):
        if name == "core.market_calendar":
            raise AssertionError("market calendar should not be imported when toggle is disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("core.startup_orchestrator.service_toggle_registry.is_enabled", fake_is_enabled)
    monkeypatch.setattr("builtins.__import__", guarded_import)

    orchestrator.auto_start_rt_if_ready()


def test_global_earnings_daily_refresh_delay_targets_next_0200():
    import datetime

    assert ms_until_next_global_earnings_calendar_daily_refresh(
        datetime.datetime(2026, 5, 4, 1, 30)
    ) == 30 * 60 * 1000
    assert ms_until_next_global_earnings_calendar_daily_refresh(
        datetime.datetime(2026, 5, 4, 2, 0)
    ) == 24 * 60 * 60 * 1000
    assert GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR == 2
    assert GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE == 0


def test_startup_orchestrator_schedules_auto_rt_retry_timer():
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.schedule_startup()
    try:
        assert orchestrator._auto_rt_timer.isActive() is True
        assert orchestrator._auto_rt_timer.interval() == AUTO_RT_MONITOR_RETRY_INTERVAL_MS
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert orchestrator._global_earnings_calendar_daily_timer.isSingleShot() is True
        assert 0 < orchestrator._global_earnings_calendar_daily_timer.interval() <= 24 * 60 * 60 * 1000
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_sync_allows_next_period_after_completion(monkeypatch):
    calls = []

    class _FakeService:
        def refresh_events(self):
            calls.append("refresh")
            return [object(), object()]

    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.GlobalEarningsCalendarService",
        _FakeService,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.refresh_global_earnings_calendar()
    orchestrator.refresh_global_earnings_calendar()

    assert calls == ["refresh", "refresh"]


def test_startup_orchestrator_global_earnings_sync_emits_update_event(monkeypatch):
    class _FakeService:
        def refresh_events(self):
            return [object()]

    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.GlobalEarningsCalendarService",
        _FakeService,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_earnings_updated)

    orchestrator.refresh_global_earnings_calendar()

    assert len(spy) == 1


def test_startup_orchestrator_daily_earnings_timer_refreshes_and_rearms(monkeypatch):
    calls = []

    class _FakeService:
        def refresh_events(self):
            calls.append("refresh")
            return [object()]

    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.GlobalEarningsCalendarService",
        _FakeService,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator._run_daily_global_earnings_calendar_refresh()
    try:
        assert calls == ["refresh"]
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert 0 < orchestrator._global_earnings_calendar_daily_timer.interval() <= 24 * 60 * 60 * 1000
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_sync_skips_overlapping_runs(monkeypatch):
    calls = []

    class _FakeService:
        def refresh_events(self):
            calls.append("refresh")
            return [object()]

    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.GlobalEarningsCalendarService",
        _FakeService,
    )

    runner = _QueuedJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)

    orchestrator.refresh_global_earnings_calendar()
    orchestrator.refresh_global_earnings_calendar()

    assert len(runner.jobs) == 1
    task_id, job, _kwargs = runner.jobs[0]
    assert task_id == GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID

    job()
    orchestrator.refresh_global_earnings_calendar()

    assert calls == ["refresh"]
    assert len(runner.jobs) == 2


def test_startup_orchestrator_skips_global_earnings_sync_when_toggle_disabled(monkeypatch):
    calls = []

    def fake_is_enabled(key, overrides=None):
        return False if key == "daily_global_earnings_calendar_sync" else True

    monkeypatch.setattr("core.startup_orchestrator.service_toggle_registry.is_enabled", fake_is_enabled)
    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.GlobalEarningsCalendarService",
        lambda: calls.append("constructed"),
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.refresh_global_earnings_calendar()

    assert calls == []


def test_startup_orchestrator_auto_starts_rt_when_ready(monkeypatch):
    class _Workspace:
        def __init__(self):
            self.started = 0

        def auto_start_rt_monitor(self):
            self.started += 1
            return True

    mw = _DummyMainWindow()
    mw.data_provider.cache_data = {f"{idx:06d}": object() for idx in range(120)}
    mw.data_provider.is_online = lambda: True
    mw._workspace = _Workspace()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    monkeypatch.setattr(
        "core.market_calendar.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": True),
    )

    orchestrator.auto_start_rt_if_ready()

    assert mw._workspace.started == 1


def test_startup_orchestrator_retries_network_before_auto_start(monkeypatch):
    class _Provider(_DummyDataProvider):
        def __init__(self):
            super().__init__()
            self.cache_data = {f"{idx:06d}": object() for idx in range(120)}
            self.online = False
            self.probes = 0

        def is_online(self):
            return self.online

        def test_network(self, timeout=3):
            self.probes += 1
            return True

        def set_online_mode(self, online):
            self.online = bool(online)

    class _Workspace:
        def __init__(self):
            self.started = 0

        def auto_start_rt_monitor(self):
            self.started += 1
            return True

    mw = _DummyMainWindow()
    mw.data_provider = _Provider()
    mw._workspace = _Workspace()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    monkeypatch.setattr(
        "core.market_calendar.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": True),
    )

    orchestrator.auto_start_rt_if_ready()

    assert mw.data_provider.probes == 1
    assert mw.data_provider.online is True
    assert mw._workspace.started == 1


def test_startup_orchestrator_shutdown_abandons_background_tasks():
    runner = _InlineJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)

    orchestrator.shutdown()

    assert runner.abandoned == [
        DEFERRED_LOAD_TASK_ID,
        ASIAN_DATA_SYNC_TASK_ID,
        GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID,
        SMART_STARTUP_TASK_ID,
        AUTO_RT_MONITOR_NETWORK_TASK_ID,
    ]
