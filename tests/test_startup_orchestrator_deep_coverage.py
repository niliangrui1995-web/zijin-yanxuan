from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from app.bootstrap import startup_orchestrator as module


def test_startup_result_helpers_cover_empty_truncated_and_process_errors():
    assert module._normalize_log_detail("") == ""
    assert module._normalize_log_detail(" a\n b ") == "a | b"
    assert module._normalize_log_detail("abcdef", limit=5) == "ab..."
    error = module.ProcessExecutionError(7, ["python"], output="stdout", stderr="stderr\nline")
    summary, detail = module._format_subprocess_failure(error)
    assert "7" in summary and "stderr" in summary and detail == "stderr\nline"
    generic = ValueError("")
    assert module._format_subprocess_failure(generic) == ("ValueError", "ValueError")

    parsed = module._parse_global_earnings_calendar_refresh_stdout(
        b'noise\n[1]\n{"status":"ignored"}\n{"status":"degraded","events":-3}'
    )
    assert parsed["status"] == "degraded" and parsed["events"] == 0
    with pytest.raises(ValueError, match="missing"):
        module._parse_global_earnings_calendar_refresh_stdout("no result")

    assert module._coerce_global_earnings_calendar_refresh_result({"events": -2}) == {"events": 0, "status": "success"}
    assert module._coerce_global_earnings_calendar_refresh_result(3) == {"status": "success", "events": 3}
    assert module._nonnegative_int("bad", 9) == 9
    assert module._provider_names("not-list") == []
    assert module._provider_names([" a ", "", None, "b"]) == ["a", "b"]
    assert module._truthy(True) and module._truthy("YES") and not module._truthy("no")


def test_run_global_refresh_subprocess_uses_stdout(monkeypatch):
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout='{"status":"success","events":4}')

    monkeypatch.setattr(module, "run_python_module", runner)
    assert module._run_global_earnings_calendar_refresh_subprocess()["events"] == 4
    assert calls[0][1]["timeout"] == module.GLOBAL_EARNINGS_CALENDAR_SYNC_TIMEOUT_SEC


def test_global_cache_snapshot_success_failure_and_bad_counts(monkeypatch):
    import domains.global_earnings_calendar.service as service_module

    class Service:
        def load_events(self, *, allow_network):
            assert allow_network is False
            return [1, 2]

        @staticmethod
        def load_cache_status():
            return {"status": "", "retryable": "yes", "reused_event_count": "bad"}

    monkeypatch.setattr(service_module, "GlobalEarningsCalendarService", Service)
    snapshot = module._global_earnings_calendar_cache_snapshot()
    assert snapshot == {"status": "hit", "events": 2, "retryable": True}

    class Broken:
        def __init__(self):
            raise RuntimeError("broken")

    monkeypatch.setattr(service_module, "GlobalEarningsCalendarService", Broken)
    assert module._global_earnings_calendar_cache_snapshot()["status"] == "unavailable"


def test_mark_global_refresh_degraded_providers_and_bad_count(monkeypatch):
    import domains.global_earnings_calendar.service as service_module

    class Service:
        @staticmethod
        def mark_refresh_failed(_error, *, reason):
            return {"reused_event_count": "bad", "reason": "", "providers": [" one ", "", None, "two"]}

    monkeypatch.setattr(service_module, "GlobalEarningsCalendarService", Service)
    result = module._mark_global_earnings_calendar_refresh_degraded("error", reason="timeout")
    assert result["events"] == 0
    assert result["reason"] == "timeout"
    assert result["providers"] == ["one", "two"]


def test_global_refresh_timing_branches():
    before = datetime.datetime(2026, 1, 5, 1, 0)
    after = datetime.datetime(2026, 1, 5, 3, 0)
    assert module.ms_until_next_global_earnings_calendar_daily_refresh(before) == 60 * 60 * 1000
    assert module.ms_until_next_global_earnings_calendar_daily_refresh(after) == 23 * 60 * 60 * 1000
    assert module._is_global_earnings_calendar_offpeak(datetime.datetime(2026, 1, 3, 12, 0))
    assert module._is_global_earnings_calendar_offpeak(datetime.datetime(2026, 1, 5, 7, 59))
    assert module._is_global_earnings_calendar_offpeak(datetime.datetime(2026, 1, 5, 18, 0))
    assert not module._is_global_earnings_calendar_offpeak(datetime.datetime(2026, 1, 5, 12, 0))
    stale = module._global_earnings_calendar_retry_delay_ms(1, cache_events=1, now=datetime.datetime(2026, 1, 5, 19, 0))
    active = module._global_earnings_calendar_retry_delay_ms(
        99, cache_events=0, now=datetime.datetime(2026, 1, 5, 12, 0)
    )
    assert stale == module.GLOBAL_EARNINGS_CALENDAR_SYNC_OFFPEAK_STALE_CACHE_MIN_RETRY_DELAY_MS
    assert active == module.GLOBAL_EARNINGS_CALENDAR_SYNC_ACTIVE_MAX_RETRY_DELAY_MS


class _Label:
    def __init__(self):
        self.values = []

    def setText(self, value):
        self.values.append(value)


def test_startup_host_adapter_full_boundary():
    calls = []
    workspace = SimpleNamespace(refresh_watchlist_names=lambda value: calls.append(("names", value)))
    service = SimpleNamespace(
        defer_auto_refresh=lambda *args: calls.append(("defer", args)),
        clear_auto_refresh_defer=lambda: calls.append(("clear",)),
        sync_runtime_state=lambda: calls.append(("sync",)),
    )
    cache = SimpleNamespace(try_load_rps_from_disk=lambda *args, **kwargs: calls.append(("rps", args, kwargs)))
    window = SimpleNamespace(
        data_provider="provider",
        cache_manager=cache,
        engine="engine",
        asian_market_service=service,
        tab_watchlist="fallback",
        current_workspace=lambda: workspace,
        is_closing=lambda: True,
        call_in_ui=lambda callback: callback(),
        refresh_code_count_label_from_provider=lambda: 7,
        lbl_code_count=_Label(),
        lbl_status=_Label(),
        set_titlebar_sync_state=lambda *args: calls.append(("title", args)),
        update_network_ui=lambda value: calls.append(("online", value)),
        on_smart_startup_online_done=lambda: calls.append(("online_done",)),
    )
    host = module.StartupHostAdapter(window)
    assert host.timer_parent is window
    assert host.data_provider == "provider" and host.workspace is workspace
    assert host.fallback_watchlist_tab == "fallback" and host.engine == "engine"
    assert host.is_closing() is True
    executed = []
    host.call_in_ui(lambda: executed.append(1), lambda: True)
    host.call_in_ui(lambda: executed.append(2), lambda: False)
    assert executed == [1]
    assert host.refresh_code_count_label_from_provider() == 7
    host.set_code_count_text("count")
    host.set_status_text("status")
    host.set_titlebar_sync_state("a", "b")
    host.try_load_rps_from_disk(executed.append)
    host.update_network_ui(True)
    host.on_smart_startup_online_done()
    host.refresh_watchlist_names({"a": "A"})
    host.defer_asian_market_auto_refresh(3, "reason")
    host.resume_asian_market_auto_refresh()
    assert window.lbl_code_count.values == ["count"]
    assert window.lbl_status.values == ["status"]
    assert any(call[0] == "rps" for call in calls)
    assert ("clear",) in calls and ("sync",) in calls


def test_startup_host_adapter_noop_and_direct_ui_fallback():
    window = SimpleNamespace()
    host = module.StartupHostAdapter(window)
    executed = []
    host.call_in_ui(lambda: executed.append(True), lambda: True)
    host.call_in_ui(lambda: executed.append(False), lambda: False)
    assert executed == [True]
    assert host.workspace is None and host.is_closing() is False
    assert host.refresh_code_count_label_from_provider() is None
    host.set_code_count_text("x")
    host.set_status_text("x")
    host.set_titlebar_sync_state()
    host.try_load_rps_from_disk(executed.append)
    host.update_network_ui(False)
    host.on_smart_startup_online_done()
    host.refresh_watchlist_names({})
    host.defer_asian_market_auto_refresh(1)
    host.resume_asian_market_auto_refresh()


def _bare_orchestrator(host):
    orchestrator = module.StartupOrchestrator.__new__(module.StartupOrchestrator)
    orchestrator.host = host
    orchestrator._closed = False
    orchestrator._job_runner = SimpleNamespace()
    return orchestrator


def test_loaded_watchlist_codes_all_sources_and_errors():
    host = SimpleNamespace(workspace=None, fallback_watchlist_tab=None)
    orchestrator = _bare_orchestrator(host)
    assert orchestrator._loaded_watchlist_codes() == []

    rows = [{"code": "000001"}, {"code": "bad"}, "invalid"]
    host.workspace = SimpleNamespace(tab_watchlist=SimpleNamespace(model=SimpleNamespace(row_data=rows)))
    assert orchestrator._loaded_watchlist_codes() == ["000001"]

    tab = SimpleNamespace(get_realtime_quote_codes=lambda: ["600001", "bad"])
    host.workspace = SimpleNamespace(get_loaded_tab=lambda _name: tab)
    assert orchestrator._loaded_watchlist_codes() == ["600001"]
    tab.get_realtime_quote_codes = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    assert orchestrator._loaded_watchlist_codes() == []


def test_refresh_startup_names_provider_missing_and_merge():
    host = SimpleNamespace(data_provider=None, workspace=None, fallback_watchlist_tab=None)
    orchestrator = _bare_orchestrator(host)
    assert orchestrator._refresh_startup_code_names() == {}

    provider = SimpleNamespace(
        code2name={"000001": "old", "": "skip"},
        ensure_code_name_map=lambda codes, refresh_missing: {"600001": "new", "": "skip"},
    )
    host.data_provider = provider
    host.workspace = SimpleNamespace(
        get_loaded_tab=lambda _name: SimpleNamespace(get_realtime_quote_codes=lambda: ["600001"])
    )
    result = orchestrator._refresh_startup_code_names()
    assert result == {"000001": "old", "600001": "new"}
    assert provider.code2name == result


def test_safe_ui_call_alive_runtime_error_and_asian_sync_paths(monkeypatch):
    calls = []
    host = SimpleNamespace(
        timer_parent=object(),
        is_closing=lambda: False,
        call_in_ui=lambda *_args: (_ for _ in ()).throw(RuntimeError("closed")),
    )
    orchestrator = _bare_orchestrator(host)
    orchestrator._safe_call_in_ui(lambda: calls.append(True))
    orchestrator._closed = True
    orchestrator._safe_call_in_ui(lambda: calls.append(False))
    assert calls == []
    project_root, output_dir, json_cache, module_entry = orchestrator._asian_data_sync_paths()
    assert output_dir.startswith(project_root)
    assert json_cache.endswith("asian_klines_latest.json")
    assert module_entry.endswith("asian_kline_fetcher.py")
