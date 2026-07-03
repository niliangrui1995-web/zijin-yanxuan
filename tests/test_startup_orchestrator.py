from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject
from PyQt6.QtTest import QSignalSpy

import core.startup_orchestrator as startup_module
from core.event_bus import event_bus
from core.startup_orchestrator import (
    ASIAN_DATA_SYNC_RUNTIME_DEFER_SEC,
    ASIAN_DATA_SYNC_TASK_ID,
    ASIAN_DATA_SYNC_TIME_BUDGET_SEC,
    ASIAN_DATA_SYNC_TIMEOUT_RUNTIME_BACKOFF_SEC,
    ASIAN_DATA_SYNC_TIMEOUT_SEC,
    AUTO_RT_MONITOR_NETWORK_TASK_ID,
    DEFERRED_LOAD_TASK_ID,
    GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR,
    GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE,
    GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS,
    GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID,
    GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC,
    SMART_STARTUP_TASK_ID,
    StartupHostAdapter,
    StartupOrchestrator,
    ms_until_next_global_earnings_calendar_daily_refresh,
)


@pytest.fixture(autouse=True)
def _stub_global_earnings_cache_probe(monkeypatch):
    monkeypatch.setattr(
        startup_module,
        "_global_earnings_calendar_cache_snapshot",
        lambda: {"status": "miss", "events": 0},
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


class _DummyAsianMarketService:
    def __init__(self):
        self.calls = []

    def defer_auto_refresh(self, seconds, reason=""):
        self.calls.append(("defer", seconds, reason))

    def clear_auto_refresh_defer(self):
        self.calls.append(("clear",))

    def sync_runtime_state(self):
        self.calls.append(("sync",))
        return "running"


class _DummyDataProvider:
    def __init__(self):
        self.cache_data = {}
        self.code2name = {}
        self.online_mode_calls = []
        self.ensure_calls = []

    def load_cache_from_disk(self):
        return ""

    def test_network(self, timeout=3):
        return False

    def set_online_mode(self, online):
        self.online_mode_calls.append(bool(online))

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        self.ensure_calls.append((tuple(codes or ()), bool(refresh_missing)))
        return dict(self.code2name)


class _DummyMainWindow(QObject):
    def __init__(self):
        super().__init__()
        self._is_closing = False
        self.data_provider = _DummyDataProvider()
        self.cache_manager = _DummyCacheManager()
        self.engine = object()
        self.asian_market_service = _DummyAsianMarketService()
        self.table_rt = object()
        self.lbl_status = _DummyLabel()
        self.lbl_code_count = _DummyLabel()
        self.titlebar_sync_states = []
        self.tab_watchlist = None
        self._workspace = None
        self.network_updates = []
        self.online_done_count = 0
        self.rt_cache_restore_pending = False

    def _call_in_ui(self, callback):
        callback()

    def _set_titlebar_sync_state(self, *args):
        self.titlebar_sync_states.append(args)

    def _update_network_ui(self, online):
        self.network_updates.append(bool(online))

    def _on_smart_startup_online_done(self):
        self.online_done_count += 1

    def mark_rt_cache_restore_pending(self):
        self.rt_cache_restore_pending = True


class _ReadyRtModel:
    headers = ["代码", "现价"]

    def __init__(self):
        self.rows = []

    def update_data(self, rows):
        self.rows = list(rows or [])


class _ReadyRtTable:
    def __init__(self):
        self._model = _ReadyRtModel()

    def model(self):
        return self._model


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


def test_startup_host_adapter_exposes_narrow_main_window_boundary():
    mw = _DummyMainWindow()
    adapter = StartupHostAdapter(mw)

    assert adapter.timer_parent is mw
    assert adapter.data_provider is mw.data_provider
    assert adapter.workspace is None
    assert adapter.fallback_watchlist_tab is None

    adapter.set_code_count_text("标的池 3")
    adapter.set_status_text("ready")
    adapter.set_titlebar_sync_state("cache", "ok", "today")
    adapter.update_network_ui(True)
    adapter.on_smart_startup_online_done()

    assert mw.lbl_code_count.value == "标的池 3"
    assert mw.lbl_status.value == "ready"
    assert mw.titlebar_sync_states == [("cache", "ok", "today")]
    assert mw.network_updates == [True]
    assert mw.online_done_count == 1


def test_startup_host_adapter_defers_rt_cache_until_table_model_exists():
    mw = _DummyMainWindow()
    calls = []
    mw.cache_manager.load_rt_cache = lambda *args, **kwargs: calls.append((args, kwargs)) or True
    mw._workspace = types.SimpleNamespace(get_rt_table=lambda: None)
    adapter = StartupHostAdapter(mw)

    assert adapter.load_rt_cache() is False

    assert calls == []
    assert mw.rt_cache_restore_pending is True


def test_startup_host_adapter_loads_rt_cache_when_table_model_exists():
    mw = _DummyMainWindow()
    table = _ReadyRtTable()
    calls = []
    mw.cache_manager.load_rt_cache = lambda *args, **kwargs: calls.append((args, kwargs)) or True
    mw._workspace = types.SimpleNamespace(get_rt_table=lambda: table)
    adapter = StartupHostAdapter(mw)

    assert adapter.load_rt_cache() is True

    assert len(calls) == 1
    assert calls[0][0][0] is table
    assert mw.rt_cache_restore_pending is False


def test_startup_orchestrator_asian_sync_uses_process_runner(monkeypatch):
    runner = _InlineJobRunner()
    mw = _DummyMainWindow()
    orchestrator = StartupOrchestrator(mw, job_runner=runner)
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
    assert run_calls[0]["module_args"][3:] == [
        "--time-budget-sec",
        str(ASIAN_DATA_SYNC_TIME_BUDGET_SEC),
    ]
    assert run_calls[0]["kwargs"]["timeout"] == ASIAN_DATA_SYNC_TIMEOUT_SEC
    assert Path(run_calls[0]["kwargs"]["cwd"]).name == Path(__file__).resolve().parents[1].name
    assert run_calls[0]["kwargs"]["capture_output"] is True
    assert run_calls[0]["kwargs"]["text"] is True
    assert run_calls[0]["kwargs"]["no_window"] is True
    assert mw.asian_market_service.calls == [
        ("defer", ASIAN_DATA_SYNC_RUNTIME_DEFER_SEC, "startup_asian_sync"),
        ("clear",),
        ("sync",),
    ]


def test_startup_orchestrator_asian_sync_timeout_extends_runtime_backoff(monkeypatch):
    mw = _DummyMainWindow()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())

    def fake_exists(path):
        path = str(path)
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run_python_module(*_args, **_kwargs):
        raise startup_module.ProcessTimeoutError(cmd="python", timeout=ASIAN_DATA_SYNC_TIMEOUT_SEC)

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr("core.startup_orchestrator.run_python_module", fake_run_python_module)
    monkeypatch.setattr("core.startup_orchestrator.log_process_snapshot", lambda *_args, **_kwargs: None)

    orchestrator.deferred_data_load()

    assert mw.asian_market_service.calls == [
        ("defer", ASIAN_DATA_SYNC_RUNTIME_DEFER_SEC, "startup_asian_sync"),
        ("defer", ASIAN_DATA_SYNC_TIMEOUT_RUNTIME_BACKOFF_SEC, "startup_asian_sync_timeout"),
    ]


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


def test_startup_orchestrator_deferred_load_loads_history_cache_by_default(monkeypatch):
    mw = _DummyMainWindow()
    calls = []
    mw.data_provider.load_cache_from_disk = lambda: calls.append("load") or "20260508"
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())

    def fake_exists(path):
        return not str(path).endswith("asian_kline_fetcher.py")

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)

    orchestrator.deferred_data_load()

    assert calls == ["load"]


def test_startup_orchestrator_deferred_load_stops_after_window_close(monkeypatch):
    mw = _DummyMainWindow()
    calls = {"rt_cache": 0, "rps": 0}

    def close_during_history_load():
        mw._is_closing = True
        mw.data_provider.cache_data = {"000001": object()}
        return "20260508"

    class _CountingCacheManager:
        def load_rt_cache(self, *_args, **_kwargs):
            calls["rt_cache"] += 1

        def try_load_rps_from_disk(self, *_args, **_kwargs):
            calls["rps"] += 1

    mw.data_provider.load_cache_from_disk = close_during_history_load
    mw.cache_manager = _CountingCacheManager()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    labels = []

    monkeypatch.setattr(
        "core.startup_orchestrator.service_toggle_registry.is_enabled",
        lambda key, *_args, **_kwargs: key == "startup_history_cache_load",
    )
    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda label, **_kwargs: labels.append(label),
    )

    orchestrator.deferred_data_load()

    assert calls == {"rt_cache": 0, "rps": 0}
    assert "startup.deferred_load.cancelled" in labels
    assert "startup.deferred_load.end" not in labels


def test_startup_orchestrator_code_count_uses_lightweight_code_map_when_history_skipped(monkeypatch):
    mw = _DummyMainWindow()
    mw.data_provider.code2name = {
        "000001": "平安银行",
        "600000": "浦发银行",
        "300750": "宁德时代",
        "00700": "腾讯控股",
    }
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())

    monkeypatch.setattr(
        "core.startup_orchestrator.os.path.exists",
        lambda path: not str(path).endswith("asian_kline_fetcher.py"),
    )
    monkeypatch.setattr(
        "core.startup_orchestrator.service_toggle_registry.is_enabled",
        lambda key, *_args, **_kwargs: False if key == "startup_history_cache_load" else True,
    )

    orchestrator.deferred_data_load()

    assert mw.lbl_code_count.value == "标的池: 3 只"


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


def test_startup_orchestrator_smart_startup_stops_after_window_close(monkeypatch):
    mw = _DummyMainWindow()

    class _ClosingProvider(_DummyDataProvider):
        def __init__(self):
            super().__init__()
            self.online_mode_calls = []

        def test_network(self, timeout=3):
            mw._is_closing = True
            return True

        def set_online_mode(self, online):
            self.online_mode_calls.append(bool(online))

    mw.data_provider = _ClosingProvider()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    labels = []

    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda label, **_kwargs: labels.append(label),
    )

    orchestrator.smart_startup()

    assert mw.data_provider.online_mode_calls == []
    assert labels == ["startup.smart.begin"]


def test_startup_orchestrator_smart_startup_limits_name_refresh_to_watchlist(monkeypatch):
    class _Provider(_DummyDataProvider):
        def test_network(self, timeout=3):
            return True

        def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
            self.ensure_calls.append((tuple(codes or ()), bool(refresh_missing)))
            return {
                "000001": "Ping An",
                "600519": "Moutai",
            }

    class _WatchlistTab:
        def get_realtime_quote_codes(self):
            return {"000001", "600519", "00700", "not-a-code"}

    class _Workspace:
        def __init__(self):
            self.tab_watchlist = _WatchlistTab()
            self.refreshed_maps = []

        def get_loaded_tab(self, key):
            return self.tab_watchlist if key == "watchlist" else None

        def refresh_watchlist_names(self, code2name):
            self.refreshed_maps.append(dict(code2name))
            return True

    mw = _DummyMainWindow()
    mw.data_provider = _Provider()
    mw._workspace = _Workspace()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda *_args, **_kwargs: None,
    )

    orchestrator.smart_startup()

    assert len(mw.data_provider.ensure_calls) == 1
    codes, refresh_missing = mw.data_provider.ensure_calls[0]
    assert set(codes) == {"000001", "600519"}
    assert refresh_missing is True
    assert mw._workspace.refreshed_maps[-1]["000001"] == "Ping An"
    assert mw.network_updates == [True]
    assert mw.online_done_count == 1


def test_startup_orchestrator_smart_startup_skips_full_name_refresh_without_watchlist_codes(monkeypatch):
    class _Provider(_DummyDataProvider):
        def __init__(self):
            super().__init__()
            self.code2name = {"000001": "Ping An"}

        def test_network(self, timeout=3):
            return True

        def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
            raise AssertionError("smart startup should not refresh all code names without watchlist codes")

    class _WatchlistTab:
        def get_realtime_quote_codes(self):
            return set()

    class _Workspace:
        def __init__(self):
            self.tab_watchlist = _WatchlistTab()
            self.refreshed_maps = []

        def get_loaded_tab(self, key):
            return self.tab_watchlist if key == "watchlist" else None

        def refresh_watchlist_names(self, code2name):
            self.refreshed_maps.append(dict(code2name))
            return True

    mw = _DummyMainWindow()
    mw.data_provider = _Provider()
    mw._workspace = _Workspace()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda *_args, **_kwargs: None,
    )

    orchestrator.smart_startup()

    assert mw.data_provider.code2name == {"000001": "Ping An"}
    assert mw._workspace.refreshed_maps == [{"000001": "Ping An"}]


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

    assert ms_until_next_global_earnings_calendar_daily_refresh(datetime.datetime(2026, 5, 4, 1, 30)) == 30 * 60 * 1000
    assert (
        ms_until_next_global_earnings_calendar_daily_refresh(datetime.datetime(2026, 5, 4, 2, 0)) == 24 * 60 * 60 * 1000
    )
    assert GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_HOUR == 2
    assert GLOBAL_EARNINGS_CALENDAR_DAILY_REFRESH_MINUTE == 0


def test_startup_orchestrator_global_earnings_reads_cache_before_network_refresh(monkeypatch):
    calls = []

    monkeypatch.setattr(
        startup_module,
        "_global_earnings_calendar_cache_snapshot",
        lambda: calls.append("cache") or {"status": "hit", "events": 3},
    )
    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: calls.append("refresh") or {"status": "success", "events": 7},
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.refresh_global_earnings_calendar()

    assert calls == ["cache", "refresh"]


def test_global_earnings_refresh_subprocess_uses_hidden_timeout(monkeypatch):
    captured = {}

    def fake_run_python_module(module_name, module_args=None, **kwargs):
        captured["module_name"] = module_name
        captured["module_args"] = module_args
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(stdout='log line\n{"status":"success","events":7}\n')

    monkeypatch.setattr(startup_module, "run_python_module", fake_run_python_module)

    assert startup_module._run_global_earnings_calendar_refresh_subprocess() == {"status": "success", "events": 7}
    assert captured["module_name"] == "domains.global_earnings_calendar.refresh_cache"
    assert captured["module_args"] is None
    assert captured["kwargs"]["no_window"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["timeout"] == GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC


def test_global_earnings_refresh_subprocess_parses_degraded_status(monkeypatch):
    def fake_run_python_module(module_name, module_args=None, **kwargs):
        return types.SimpleNamespace(
            stdout=(
                "log line\n"
                '{"status":"degraded","events":82,"providers":["MOPS"],"reused_event_count":3}\n'
            )
        )

    monkeypatch.setattr(startup_module, "run_python_module", fake_run_python_module)

    assert startup_module._run_global_earnings_calendar_refresh_subprocess() == {
        "status": "degraded",
        "events": 82,
        "providers": ["MOPS"],
        "reused_event_count": 3,
    }


def test_startup_orchestrator_leaves_auto_rt_retry_to_global_scheduler():
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.schedule_startup()
    try:
        assert orchestrator._auto_rt_timer is None
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert orchestrator._global_earnings_calendar_daily_timer.isSingleShot() is True
        assert 0 < orchestrator._global_earnings_calendar_daily_timer.interval() <= 24 * 60 * 60 * 1000
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_sync_allows_next_period_after_completion(monkeypatch):
    calls = []

    def fake_refresh():
        calls.append("refresh")
        return 2

    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        fake_refresh,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator.refresh_global_earnings_calendar()
    orchestrator.refresh_global_earnings_calendar()

    assert calls == ["refresh", "refresh"]


def test_startup_orchestrator_global_earnings_sync_emits_update_event(monkeypatch):
    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: 1,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_earnings_updated)

    orchestrator.refresh_global_earnings_calendar()

    assert len(spy) == 1


def test_startup_orchestrator_global_earnings_sync_marks_degraded_result(monkeypatch):
    snapshots = []

    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: {"status": "degraded", "events": 82, "providers": ["MOPS"], "reused_event_count": 3},
    )
    monkeypatch.setattr(
        "core.startup_orchestrator.log_process_snapshot",
        lambda name, **kwargs: snapshots.append((name, kwargs)),
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_earnings_updated)

    orchestrator.refresh_global_earnings_calendar()

    end_snapshots = [item for item in snapshots if item[0] == "global_earnings_calendar.background_refresh.end"]
    assert end_snapshots
    assert end_snapshots[-1][1]["extra"]["status"] == "degraded"
    assert end_snapshots[-1][1]["extra"]["events"] == 82
    assert end_snapshots[-1][1]["extra"]["providers"] == "MOPS"
    assert end_snapshots[-1][1]["extra"]["reused_event_count"] == 3
    assert len(spy) == 1


def test_startup_orchestrator_global_earnings_retryable_degraded_rearms_soon(monkeypatch):
    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: {
            "status": "degraded",
            "events": 82,
            "reason": "refresh_exception",
            "retryable": True,
            "reused_event_count": 82,
        },
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    try:
        orchestrator.refresh_global_earnings_calendar()

        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert orchestrator._global_earnings_calendar_daily_timer.interval() == GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_failure_logs_detail_and_retries(monkeypatch):
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

    def fake_refresh():
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["python", "-m", "domains.global_earnings_calendar.refresh_cache"],
            stderr="sqlite busy\nretry later",
        )

    monkeypatch.setattr("core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess", fake_refresh)
    monkeypatch.setattr("core.startup_orchestrator.log", _FakeLog())
    monkeypatch.setattr("core.startup_orchestrator.log_process_snapshot", lambda *_args, **_kwargs: None)

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_earnings_updated)
    try:
        orchestrator.refresh_global_earnings_calendar()

        assert any("sqlite busy" in message for message in records["warning"])
        assert any("retry later" in message for message in records["debug"])
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert orchestrator._global_earnings_calendar_daily_timer.interval() == GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS
        assert len(spy) == 1
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_timeout_marks_degraded_cache_and_retries(monkeypatch):
    marks = []

    def fake_refresh():
        raise startup_module.ProcessTimeoutError(
            cmd=["python", "-m", "domains.global_earnings_calendar.refresh_cache"],
            timeout=GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC,
        )

    def fake_mark(error, *, reason):
        marks.append((reason, error.__class__.__name__))
        return {
            "status": "degraded",
            "events": 5,
            "retryable": True,
            "reused_event_count": 5,
            "reason": reason,
        }

    monkeypatch.setattr("core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess", fake_refresh)
    monkeypatch.setattr("core.startup_orchestrator._mark_global_earnings_calendar_refresh_degraded", fake_mark)

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_earnings_updated)
    try:
        orchestrator.refresh_global_earnings_calendar()

        assert marks == [("refresh_timeout", "TimeoutExpired")]
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert orchestrator._global_earnings_calendar_daily_timer.interval() == GLOBAL_EARNINGS_CALENDAR_SYNC_RETRY_DELAY_MS
        assert len(spy) == 1
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_daily_earnings_timer_refreshes_and_rearms(monkeypatch):
    calls = []

    def fake_refresh():
        calls.append("refresh")
        return 1

    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        fake_refresh,
    )

    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())

    orchestrator._run_daily_global_earnings_calendar_refresh()
    try:
        assert calls == ["refresh"]
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert 0 < orchestrator._global_earnings_calendar_daily_timer.interval() <= 24 * 60 * 60 * 1000
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_daily_earnings_timer_queues_background_refresh_and_rearms(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: calls.append("refresh") or {"status": "success", "events": 1},
    )

    runner = _QueuedJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)

    orchestrator._run_daily_global_earnings_calendar_refresh()
    try:
        assert calls == []
        assert len(runner.jobs) == 1
        assert orchestrator._global_earnings_calendar_daily_timer.isActive() is True
        assert 0 < orchestrator._global_earnings_calendar_daily_timer.interval() <= 24 * 60 * 60 * 1000

        task_id, job, _kwargs = runner.jobs[0]
        assert task_id == GLOBAL_EARNINGS_CALENDAR_SYNC_TASK_ID

        job()

        assert calls == ["refresh"]
    finally:
        orchestrator.shutdown()


def test_startup_orchestrator_global_earnings_sync_skips_overlapping_runs(monkeypatch):
    calls = []

    def fake_refresh():
        calls.append("refresh")
        return 1

    monkeypatch.setattr(
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        fake_refresh,
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
        "core.startup_orchestrator._run_global_earnings_calendar_refresh_subprocess",
        lambda: calls.append("refresh"),
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


def test_startup_orchestrator_auto_rt_probe_stops_after_window_close(monkeypatch):
    class _Provider(_DummyDataProvider):
        def __init__(self, owner):
            super().__init__()
            self.cache_data = {f"{idx:06d}": object() for idx in range(120)}
            self.owner = owner
            self.online = False
            self.probes = 0

        def is_online(self):
            return self.online

        def test_network(self, timeout=3):
            self.probes += 1
            self.owner._is_closing = True
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
    mw.data_provider = _Provider(mw)
    mw._workspace = _Workspace()
    orchestrator = StartupOrchestrator(mw, job_runner=_InlineJobRunner())
    monkeypatch.setattr(
        "core.market_calendar.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": True),
    )

    orchestrator.auto_start_rt_if_ready()

    assert mw.data_provider.probes == 1
    assert mw.data_provider.online is False
    assert mw._workspace.started == 0
    assert orchestrator._auto_rt_network_probe_active is False


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
