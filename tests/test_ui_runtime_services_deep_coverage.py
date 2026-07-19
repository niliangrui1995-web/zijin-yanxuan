# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from core.task_errors import UserFacingTaskError
from ui.services import asian_market_runtime_service as runtime_module
from ui.services import auto_refresh_tasks as task_module


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _Worker:
    def __init__(self, codes=(), *, running=False):
        self.codes = list(codes)
        self.running = running
        self.calls = []
        self.progress = _Signal()
        self.result_ready = _Signal()
        self.finished = _Signal()

    def isRunning(self):
        return self.running

    def start(self):
        self.calls.append("start")
        self.running = True

    def trigger_refresh(self):
        self.calls.append("trigger")

    def resume_auto_refresh(self):
        self.calls.append("resume")

    def pause_for_cache_sync(self):
        self.calls.append("pause")

    def defer_auto_refresh(self, seconds, reason):
        self.calls.append(("defer", seconds, reason))

    def stop(self):
        self.calls.append("stop")

    def deleteLater(self):
        self.calls.append("delete")


def test_asian_runtime_lazy_delegates_and_progress_message(monkeypatch):
    from app.services import asian_market_service
    from ui.tabs import asian_market_workers

    calls = []
    monkeypatch.setattr(
        asian_market_service,
        "filter_asian_tickers",
        lambda market=None: calls.append(("filter", market)) or {"TW": "2330.TW"},
    )
    monkeypatch.setattr(
        asian_market_service,
        "sync_asian_kline_cache",
        lambda **kwargs: calls.append(("sync", kwargs)) or (True, "ok", {}),
    )
    monkeypatch.setattr(asian_market_workers, "AsianMarketWorker", lambda codes: ("worker", codes))
    monkeypatch.setattr(asian_market_workers, "is_asian_quote_refresh_time", lambda codes: bool(codes))

    assert runtime_module.filter_asian_tickers("TW") == {"TW": "2330.TW"}
    assert runtime_module.sync_asian_kline_cache(period="1y") == (True, "ok", {})
    assert runtime_module._create_asian_market_worker(["2330.TW"]) == ("worker", ["2330.TW"])
    assert runtime_module.is_asian_quote_refresh_time(["2330.TW"])
    assert calls == [("filter", "TW"), ("sync", {"period": "1y"})]

    assert runtime_module._runtime_degraded_progress_message("") == ""
    assert "已缓存 12 只" in runtime_module._runtime_degraded_progress_message(
        "source payload degraded; cached 12 updates and deferred UI repaint"
    )
    assert "超时降级" in runtime_module._runtime_degraded_progress_message(
        "cached some updates and deferred UI repaint"
    )
    assert runtime_module._runtime_degraded_progress_message("low time budget") == "low time budget"
    assert runtime_module._runtime_degraded_progress_message("普通进度") == ""


def test_asian_runtime_target_code_normalization_and_worker_failures(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    service._worker = _Worker([" 2330.TW ", "", "2330.TW", "0522.HK"])
    assert service.target_codes() == ["2330.TW", "0522.HK"]

    service._worker = None
    service._codes = ["0522.HK", "0522.HK"]
    assert service.target_codes() == ["0522.HK"]

    service._codes = []
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: {"a": " 2330.TW ", "b": ""})
    assert service.target_codes() == ["2330.TW"]

    service._codes = []
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: (_ for _ in ()).throw(OSError("bad")))
    assert service.target_codes() == []

    worker = _Worker()
    service._worker = worker
    service.set_target_codes([" 0522.HK ", "", "0522.HK"])
    assert service._codes == ["0522.HK"]
    assert worker.codes == ["0522.HK"]
    service.set_target_codes([])
    assert service._codes == ["0522.HK"]

    class _BrokenWorker:
        @property
        def codes(self):
            return []

        @codes.setter
        def codes(self, _value):
            raise RuntimeError("gone")

        def isRunning(self):
            raise RuntimeError("gone")

    service._worker = _BrokenWorker()
    service.set_target_codes(["2330.TW"])
    assert not service.is_running()


def test_asian_runtime_defer_lifecycle_and_state_payload(monkeypatch):
    times = iter([100.0, 102.0, 110.0])
    monkeypatch.setattr(runtime_module.time, "time", lambda: next(times))
    service = runtime_module.AsianMarketRuntimeService()
    states = []
    service.sig_runtime_state_changed.connect(states.append)

    service.defer_auto_refresh(0, "ignored")
    assert service.runtime_state == "idle"

    worker = _Worker(["2330.TW"])
    service._worker = worker
    service.defer_auto_refresh(5, "startup")
    assert worker.calls == [("defer", 5.0, "startup")]
    assert states[-1]["state"] == "deferred"
    assert service._auto_refresh_defer_remaining() == 3.0
    assert service._auto_refresh_defer_remaining() == 0.0
    assert service._auto_refresh_defer_reason == ""

    service._last_success_at = dt.datetime(2026, 7, 15, 9, 0)
    service._last_error = "x"
    service._set_runtime_state("", " msg ")
    assert states[-1] == {
        "state": "idle",
        "message": "msg",
        "running": False,
        "last_success_at": "2026-07-15T09:00:00",
        "last_error": "x",
    }
    service.clear_auto_refresh_defer()
    assert service._auto_refresh_deferred_until == 0.0


def test_asian_runtime_worker_creation_resume_pause_and_manual_refresh(monkeypatch):
    made = []
    service = runtime_module.AsianMarketRuntimeService(
        worker_factory=lambda codes: made.append(_Worker(codes)) or made[-1]
    )
    service.set_target_codes(["2330.TW"])
    worker = service._ensure_worker()
    assert worker is made[0]
    assert len(worker.progress.slots) == len(worker.result_ready.slots) == len(worker.finished.slots) == 1
    assert service._ensure_worker() is worker

    service.resume_auto_refresh()
    assert worker.calls[-1] == "resume"
    service.pause_for_cache_sync()
    assert worker.calls[-1] == "pause"
    assert service.runtime_state == "paused_for_cache_sync"

    assert service.trigger_refresh_once()
    assert worker.calls[-2:] == ["start", "trigger"]
    assert service.runtime_state == "manual_refresh_once"

    class _BrokenTrigger(_Worker):
        def trigger_refresh(self):
            raise RuntimeError("refresh failed")

    broken = _BrokenTrigger(running=True)
    service._worker = broken
    assert not service.trigger_refresh_once()
    assert service.last_error == "refresh failed"
    assert service.runtime_state == "error"

    class _StaleWorker(_Worker):
        @property
        def codes(self):
            return []

        @codes.setter
        def codes(self, _value):
            if hasattr(self, "_initialized"):
                raise RuntimeError("deleted")

        def __init__(self):
            self._initialized = True

    service._worker = _StaleWorker()
    replacement = service._ensure_worker()
    assert replacement is made[1]


def test_asian_runtime_sync_state_all_paths(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService(worker_factory=lambda codes: _Worker(codes))
    service.set_target_codes(["2330.TW"])

    monkeypatch.setattr(runtime_module, "is_asian_quote_refresh_time", lambda _codes: True)
    service._auto_refresh_deferred_until = 999.0
    service._auto_refresh_defer_reason = "wait"
    monkeypatch.setattr(runtime_module.time, "time", lambda: 900.0)
    assert service.sync_runtime_state() == "deferred"

    service.clear_auto_refresh_defer()
    assert service.sync_runtime_state() == "started"
    assert service.sync_runtime_state() == "running"

    class _BrokenResume(_Worker):
        def resume_auto_refresh(self):
            raise RuntimeError("resume failed")

    service._worker = _BrokenResume(["2330.TW"])
    with pytest.raises(RuntimeError, match="resume failed"):
        service.sync_runtime_state()
    assert service.runtime_state == "error"

    monkeypatch.setattr(runtime_module, "is_asian_quote_refresh_time", lambda _codes: False)
    running = _Worker(["2330.TW"], running=True)
    service._worker = running
    shutdowns = []
    monkeypatch.setattr(
        runtime_module, "request_thread_shutdown", lambda worker, **kwargs: shutdowns.append((worker, kwargs))
    )
    assert service.sync_runtime_state() == "stopped"
    assert shutdowns[0][0] is running
    assert service._worker is None
    assert service.sync_runtime_state() == "skipped"


def test_asian_runtime_stop_callbacks_and_cache_write(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    assert not service.stop(auto=False)
    assert service.runtime_state == "idle"
    assert not service.stop(auto=True)
    assert service.runtime_state == "paused_for_cache_sync"

    worker = _Worker()
    service._worker = worker
    service._runtime_state = "running"
    service._on_worker_finished()
    assert worker.calls == ["delete"]
    assert service._worker is None
    assert service.runtime_state == "idle"

    worker = _Worker()
    service._worker = worker
    service._runtime_state = "manual_refresh_once"
    service._on_worker_finished()
    assert service.runtime_state == "manual_refresh_once"

    progress = []
    service.sig_progress.connect(progress.append)
    service._on_worker_progress("")
    service._runtime_state = "degraded"
    service._on_worker_progress("正在拉取亚洲市场最新报价")
    assert service.runtime_state == "running"
    service._on_worker_progress("timeout degraded markets")
    assert service.runtime_state == "degraded"
    assert len(progress) == 2

    updates = []
    service.sig_rt_update.connect(updates.append)
    service._on_rt_update({})
    service._on_rt_update({"2330.TW": {"close": 100}})
    assert list(updates[0]) == ["2330.TW"]
    assert service.last_success_at is not None

    workers = SimpleNamespace(GLOBAL_ASIAN_RT_CACHE={"a": 1})
    monkeypatch.setattr(runtime_module, "_asian_market_workers_module", lambda: workers)
    written = []
    monkeypatch.setattr(
        runtime_module, "write_realtime_quote_cache", lambda payload, path: written.append((payload, path))
    )
    runtime_module.AsianMarketRuntimeService._save_rt_cache()
    assert written == [({"a": 1}, runtime_module.RT_JSON_CACHE)]
    monkeypatch.setattr(
        runtime_module, "write_realtime_quote_cache", lambda *_args: (_ for _ in ()).throw(OSError("disk"))
    )
    runtime_module.AsianMarketRuntimeService._save_rt_cache()


def test_asian_runtime_expected_trade_dates_and_staleness(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    service.set_target_codes(["2330.TW", "0522.HK", "bad"])
    now_values = {
        "TW": dt.datetime(2026, 7, 15, 13, 0),
        "HK": dt.datetime(2026, 7, 15, 17, 0),
    }
    monkeypatch.setattr(runtime_module.MarketCalendar, "normalize_market", lambda value: value)
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda market: now_values[market])
    monkeypatch.setattr(runtime_module.MarketCalendar, "is_trade_day", lambda date_value, market: True)
    monkeypatch.setattr(
        runtime_module.MarketCalendar,
        "get_latest_trade_date",
        lambda market, ref_date: ref_date if market == "TW" else None,
    )
    expected = service._expected_latest_trade_dates()
    assert expected == {"TW": dt.date(2026, 7, 14)}

    service.set_target_codes([])
    service._codes = []
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: {})
    default_now = dt.datetime(2026, 7, 15, 18, 0)
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda _market: default_now)
    monkeypatch.setattr(runtime_module.MarketCalendar, "is_trade_day", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime_module.MarketCalendar, "get_latest_trade_date", lambda market, ref_date: ref_date)
    assert set(service._expected_latest_trade_dates()) == {"TW", "HK", "T", "KS"}

    cn_now = dt.datetime(2026, 7, 13, 15, 0)
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda _market: cn_now)
    monkeypatch.setattr(runtime_module, "cache_mtime", lambda _path: 1.0)
    monkeypatch.setattr(
        runtime_module.MarketCalendar, "from_timestamp", lambda _value, _market: dt.datetime(2026, 7, 10)
    )
    monkeypatch.setattr(runtime_module, "load_latest_trade_dates", lambda _path: {"TW": dt.date(2026, 7, 10)})
    monkeypatch.setattr(
        service, "_expected_latest_trade_dates", lambda: {"TW": dt.date(2026, 7, 11), "HK": dt.date(2026, 7, 11)}
    )
    stale = service.cache_staleness()
    assert stale["stale"] and stale["stale_by_mtime"] and stale["stale_by_trade_date"]
    assert len(stale["stale_markets"]) == 2


def test_asian_runtime_cache_sync_all_result_shapes(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    monkeypatch.setattr(service, "cache_staleness", lambda: {"stale": False, "marker": 1})
    assert service.run_cache_sync_if_stale() == {
        "job_key": "asian_market_cache_sync",
        "status": "skipped",
        "message": "cache fresh",
        "records": 0,
        "stale": False,
        "marker": 1,
    }

    monkeypatch.setattr(service, "cache_staleness", lambda: {"stale": True})
    emissions = []
    monkeypatch.setattr(
        runtime_module,
        "event_bus",
        SimpleNamespace(sig_asian_klines_ready=SimpleNamespace(emit=lambda: emissions.append(True))),
    )
    monkeypatch.setattr(
        runtime_module,
        "sync_asian_kline_cache",
        lambda **kwargs: sync_calls.append(kwargs)
        or (True, "ok", {"written_count": "bad", "rows": [1, 2], "missing": ["x"]}),
    )
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    sync_calls = []
    result = service.run_cache_sync_if_stale(cancellation_token=token)
    assert result["status"] == "success"
    assert result["records"] == 0
    assert result["missing"] == ["x"]
    assert emissions == [True]
    assert sync_calls == [{"max_workers": 3, "period": "1y", "cancellation_token": token}]

    monkeypatch.setattr(runtime_module, "sync_asian_kline_cache", lambda **_kwargs: (False, "failed", None))
    result = service.run_cache_sync_if_stale(emit_event=False)
    assert result["status"] == "degraded"
    assert result["error"] == "failed"
    assert service.last_error == "failed"


def test_auto_refresh_lazy_services_and_delegate_helpers(monkeypatch):
    import app.services.foreign_block_cache_service as cache_service
    import app.services.foreign_block_market_data_service as market_service
    import app.services.na_daily_service as na_module

    calls = []
    monkeypatch.setattr(
        market_service,
        "fetch_foreign_block_records",
        lambda **kwargs: calls.append(("fetch", kwargs)) or {"records": []},
    )
    monkeypatch.setattr(
        market_service,
        "build_foreign_block_cache_rows",
        lambda rows, **kwargs: calls.append(("build", rows, kwargs)) or [],
    )
    monkeypatch.setattr(
        cache_service, "save_foreign_block_cache", lambda rows, **kwargs: calls.append(("save", rows, kwargs))
    )
    assert task_module._fetch_foreign_block_records(days_to_fetch=3, cancellation_token="token") == {"records": []}
    assert task_module._build_foreign_block_rows([{"x": 1}], cancellation_token="token") == []
    task_module._save_foreign_block_cache([], days_to_fetch=3, latest_trade_date="")

    na_service = object()
    monkeypatch.setattr(na_module, "NADailyRefreshService", lambda: na_service)
    service = task_module.AutoRefreshTaskService()
    assert service._get_na_daily_service() is na_service
    assert service._get_na_daily_service() is na_service

    runtime_service = object()
    monkeypatch.setattr(runtime_module, "AsianMarketRuntimeService", lambda: runtime_service)
    assert service._get_asian_market_service() is runtime_service
    assert service._get_asian_market_service() is runtime_service
    assert [item[0] for item in calls] == ["fetch", "build", "save"]


def test_auto_refresh_lhb_validation_calendar_fallback_and_plain_payload(monkeypatch):
    import app.services.lhb_market_data_service as lhb_service
    import app.services.ui_lhb_pool_service as pool_module
    import app.services.ui_market_calendar_service as calendar_module

    service = task_module.AutoRefreshTaskService()
    with pytest.raises(ValueError, match="trade_date"):
        service.run_lhb_daily(" ")

    class _Manager:
        def __init__(self):
            self.rows = []
            self.last_auto_fetch_date = ""

        def add_day(self, date_text, rows):
            self.rows.append((date_text, rows))

        def prune(self, _dates):
            raise AssertionError("invalid calendar should not prune")

        def save(self):
            self.saved = True

        def get_cached_dates(self):
            return ["20260715"]

    manager = _Manager()
    monkeypatch.setattr(lhb_service, "fetch_lhb_pool_for_date", lambda *_args, **_kwargs: [{"code": "1"}])
    monkeypatch.setattr(task_module, "_filter_lhb_rows_to_ai_chain", lambda rows: rows)
    monkeypatch.setattr(pool_module, "LhbPoolManager", lambda: manager)
    monkeypatch.setattr(
        calendar_module.MarketCalendar,
        "get_recent_trade_dates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("date")),
    )
    result = service.run_lhb_daily("bad-date")
    assert result["status"] == "ok"
    assert result["records"] == 1
    assert manager.rows == [("bad-date", [{"code": "1"}])]


def test_auto_refresh_foreign_empty_partial_and_latest_date(monkeypatch):
    service = task_module.AutoRefreshTaskService()
    saved = []
    monkeypatch.setattr(
        task_module, "invoke_with_cancellation", lambda fn, _token, *args, **kwargs: fn(*args, **kwargs)
    )
    monkeypatch.setattr(task_module, "_fetch_foreign_block_records", lambda **_kwargs: {"records": []})
    monkeypatch.setattr(task_module, "_build_foreign_block_rows", lambda _rows, **_kwargs: [])
    monkeypatch.setattr(task_module, "_save_foreign_block_cache", lambda rows, **kwargs: saved.append((rows, kwargs)))
    result = service.run_foreign_block_daily("20260715", days_to_fetch=5)
    assert result["records"] == 0
    assert saved == [([], {"days_to_fetch": 5, "latest_trade_date": ""})]

    assert (
        task_module._latest_foreign_block_trade_date(
            [None, {"交易日期": ""}, {"交易日期": "20260714"}, {"交易日期": "20260715"}]
        )
        == "20260715"
    )
    with pytest.raises(UserFacingTaskError):
        task_module._foreign_incomplete_result("20260715", [], "", ["x"], [])
    partial = task_module._foreign_incomplete_result("20260715", [{"x": 1}], "20260715", [], ["bad"])
    assert partial["status"] == "partial"
    assert partial["failed_chunks"] == ["bad"]


def test_auto_refresh_fund_na_and_asian_workflows(monkeypatch):
    import app.services.ui_fund_holdings_service as fund_module

    service = task_module.AutoRefreshTaskService()
    monkeypatch.setattr(
        fund_module.fund_holdings_sync_service,
        "sync_latest_all",
        lambda **kwargs: {"message": " done ", "kwargs": kwargs},
    )
    assert service.run_fund_holdings_daily(" 20260715 ")["message"] == "done"

    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    assert service.run_fund_holdings_daily("20260715", cancellation_token=token)["result"]["kwargs"] == {
        "cancellation_token": token
    }
    monkeypatch.setattr(fund_module.fund_holdings_sync_service, "sync_latest_all", lambda: "ok")
    assert task_module.AutoRefreshTaskService().run_fund_holdings_daily("x")["message"] == ""

    class _NA:
        def refresh_full(self, **_kwargs):
            return {"records": "2", "cache_file": "full.json"}

        def refresh_incremental(self, **_kwargs):
            return {"records": None, "status": "cached", "cache_file": "inc.json"}

    service = task_module.AutoRefreshTaskService(na_daily_service=_NA())
    assert service.run_na_daily_full_0925("20260715")["records"] == 2
    assert service.run_na_daily_incremental("20260715")["message"] == "cached"

    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: {"a": "2330.TW", "b": ""})
    refresh_checks = []
    monkeypatch.setattr(
        runtime_module, "is_asian_quote_refresh_time", lambda codes: refresh_checks.append(list(codes)) or True
    )
    prepared = service.prepare_asian_market_runtime()
    assert prepared == {"target_codes": ["2330.TW"]}
    assert service.prepare_asian_market_runtime() == prepared
    assert refresh_checks == [["2330.TW"], ["2330.TW"]]

    broken_service = task_module.AutoRefreshTaskService()
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: (_ for _ in ()).throw(OSError("missing")))
    assert broken_service.prepare_asian_market_runtime() == {"target_codes": []}


def test_auto_refresh_runtime_sync_cache_and_earnings(monkeypatch):
    class _Asian:
        def __init__(self):
            self._worker = None
            self._codes = []
            self.calls = []

        def set_target_codes(self, codes):
            self.calls.append(("codes", list(codes)))

        def stop(self, auto=False):
            self.calls.append(("stop", auto))
            return True

        def sync_runtime_state(self):
            self.calls.append(("sync",))
            return "started"

        def run_cache_sync_if_stale(self, emit_event=False, cancellation_token=None):
            self.calls.append(("cache", emit_event, cancellation_token))
            return {"status": "success"}

    asian = _Asian()
    service = task_module.AutoRefreshTaskService(asian_market_service=asian)
    assert service.sync_asian_market_runtime({"target_codes": ["2330.TW"]})["status"] == "started"
    assert asian.calls[:2] == [("codes", ["2330.TW"]), ("sync",)]

    asian.calls.clear()
    assert service.sync_asian_market_runtime({"target_codes": []})["status"] == "stopped"
    assert asian.calls == [("stop", True)]

    asian._codes = ["existing"]
    asian.calls.clear()
    assert service.sync_asian_market_runtime(None)["status"] == "started"
    assert asian.calls == [("sync",)]
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    result = service.run_asian_market_cache_sync(" 20260715 ", cancellation_token=token)
    assert result == {"status": "success", "trade_date": "20260715"}
    assert asian.calls[-1] == ("cache", False, token)

    import app.services.earnings_refresh_process_service as earnings_module

    calls = []
    monkeypatch.setattr(
        task_module,
        "invoke_with_cancellation",
        lambda fn, token, *args, **kwargs: calls.append((fn, token, args, kwargs)) or {"job_key": "earnings"},
    )
    first = service.run_earnings_startup_gap_fill("20260715", cancellation_token="t")
    second = service.run_earnings_routine("20260715", routine_time=" 09:00 ", cancellation_token="t")
    assert first["trade_date"] == "20260715"
    assert second["routine_time"] == "09:00"
    assert calls == [
        (earnings_module.run_earnings_refresh, "t", ("startup-gap-fill",), {}),
        (earnings_module.run_earnings_refresh, "t", ("routine",), {"routine_time": " 09:00 "}),
    ]


def test_auto_refresh_ai_chain_filter_success_and_failure(monkeypatch):
    import app.services.ui_industry_chain_service as chain_module

    monkeypatch.setattr(chain_module, "load_cached_ai_industry_chain_stock_codes", lambda: {"300308"})
    monkeypatch.setattr(
        chain_module,
        "filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: [row for row in rows if row.get("code") in kwargs["stock_codes"]],
    )
    assert task_module._filter_lhb_rows_to_ai_chain([{"code": "300308"}, {"code": "1"}]) == [{"code": "300308"}]
    monkeypatch.setattr(
        chain_module, "load_cached_ai_industry_chain_stock_codes", lambda: (_ for _ in ()).throw(OSError("bad"))
    )
    assert task_module._filter_lhb_rows_to_ai_chain([{"code": "300308"}]) == []
