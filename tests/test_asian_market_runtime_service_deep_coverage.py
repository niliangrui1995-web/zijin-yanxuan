# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from ui.services import asian_market_runtime_service as runtime_module


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _Worker:
    def __init__(self, codes=(), *, running=False, failures=()):
        self._codes = list(codes)
        self.running = running
        self.failures = set(failures)
        self.calls = []
        self.progress = _Signal()
        self.result_ready = _Signal()
        self.finished = _Signal()

    def _fail(self, operation):
        if operation in self.failures:
            raise RuntimeError(f"{operation} failed")

    @property
    def codes(self):
        return self._codes

    @codes.setter
    def codes(self, value):
        self._fail("codes")
        self._codes = list(value)

    def isRunning(self):
        self._fail("is_running")
        return self.running

    def start(self):
        self._fail("start")
        self.calls.append("start")
        self.running = True

    def trigger_refresh(self):
        self._fail("trigger")
        self.calls.append("trigger")

    def resume_auto_refresh(self):
        self._fail("resume")
        self.calls.append("resume")

    def pause_for_cache_sync(self):
        self._fail("pause")
        self.calls.append("pause")

    def defer_auto_refresh(self, seconds, reason):
        self._fail("defer")
        self.calls.append(("defer", seconds, reason))

    def stop(self):
        self.calls.append("stop")

    def deleteLater(self):
        self._fail("delete")
        self.calls.append("delete")


def test_runtime_lazy_delegates_and_progress_classification(monkeypatch):
    from app.services import asian_market_service

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
    workers = SimpleNamespace(
        AsianMarketWorker=lambda codes: ("worker", list(codes)),
        is_asian_quote_refresh_time=lambda codes: bool(codes),
    )
    monkeypatch.setattr(runtime_module, "_asian_market_workers_module", lambda: workers)

    assert runtime_module.filter_asian_tickers("TW") == {"TW": "2330.TW"}
    assert runtime_module.sync_asian_kline_cache(period="1y") == (True, "ok", {})
    assert runtime_module._create_asian_market_worker(["2330.TW"]) == ("worker", ["2330.TW"])
    assert runtime_module.is_asian_quote_refresh_time(["2330.TW"])
    assert "7" in runtime_module._runtime_degraded_progress_message(
        "source payload degraded; cached 7 updates and deferred UI repaint"
    )
    assert runtime_module._runtime_degraded_progress_message("timeout degraded markets")
    assert runtime_module._runtime_degraded_progress_message("ordinary progress") == ""
    assert calls == [("filter", "TW"), ("sync", {"period": "1y"})]


def test_runtime_target_codes_and_worker_assignment_fail_closed(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    service._worker = _Worker([" 2330.TW ", "", "2330.TW", "0522.HK"])
    assert service.target_codes() == ["2330.TW", "0522.HK"]

    service._worker = None
    service._codes = ["7203.T", "7203.T"]
    assert service.target_codes() == ["7203.T"]
    service._codes = []
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: {"a": " 005930.KS ", "b": ""})
    assert service.target_codes() == ["005930.KS"]

    service._codes = []
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: None)
    assert service.target_codes() == []

    broken = _Worker(failures={"codes", "is_running"})
    service._worker = broken
    service.set_target_codes([" 0522.HK ", "0522.HK", ""])
    assert service._codes == ["0522.HK"]
    assert service.current_worker() is broken
    assert service.is_running() is False
    service.set_target_codes([])
    assert service._codes == ["0522.HK"]


def test_runtime_defer_uses_longest_window_and_handles_stale_worker(monkeypatch):
    times = iter((100.0, 150.0, 201.0))
    monkeypatch.setattr(runtime_module.time, "time", lambda: next(times))
    service = runtime_module.AsianMarketRuntimeService()
    worker = _Worker(["2330.TW"])
    service._worker = worker
    service._auto_refresh_deferred_until = 200.0

    service.defer_auto_refresh(5, " waiting ")

    assert service._auto_refresh_deferred_until == 200.0
    assert worker.calls == [("defer", 5.0, "waiting")]
    assert service.runtime_state == "deferred"
    assert service._auto_refresh_defer_remaining() == 50.0
    assert service._auto_refresh_defer_remaining() == 0.0
    assert service._auto_refresh_defer_reason == ""

    monkeypatch.setattr(runtime_module.time, "time", lambda: 300.0)
    service._worker = _Worker(failures={"defer"})
    service.defer_auto_refresh(10, "stale")
    assert service._worker is None
    assert service.runtime_state == "deferred"


def test_runtime_closed_and_noncallable_defer_paths_are_noops(monkeypatch):
    monkeypatch.setattr(runtime_module.time, "time", lambda: 100.0)
    closed = runtime_module.AsianMarketRuntimeService()
    closed._closed = True
    closed.defer_auto_refresh(5, "ignored")
    assert closed.runtime_state == "idle"

    service = runtime_module.AsianMarketRuntimeService()
    worker = SimpleNamespace(codes=[], isRunning=lambda: False, defer_auto_refresh=None)
    service._worker = worker
    service.defer_auto_refresh(5, "wait")
    assert service._worker is worker
    assert service.runtime_state == "deferred"

    service.defer_auto_refresh(0, "ignored")
    service.clear_auto_refresh_defer()
    assert service._auto_refresh_deferred_until == 0.0
    assert service._auto_refresh_defer_reason == ""


def test_runtime_worker_creation_replacement_and_signal_wiring():
    created = []
    service = runtime_module.AsianMarketRuntimeService(
        worker_factory=lambda codes: created.append(_Worker(codes)) or created[-1]
    )
    service.set_target_codes(["2330.TW"])

    first = service._ensure_worker()

    assert first is created[0]
    assert first.codes == ["2330.TW"]
    assert first.progress.slots == [service._on_worker_progress]
    assert first.result_ready.slots == [service._on_rt_update]
    assert first.finished.slots == [service._on_worker_finished]
    assert service._ensure_worker() is first

    service._worker = _Worker(failures={"codes"})
    replacement = service._ensure_worker()
    assert replacement is created[1]
    service._closed = True
    assert service._ensure_worker() is None


def test_runtime_resume_pause_and_manual_trigger_paths():
    worker = _Worker()
    service = runtime_module.AsianMarketRuntimeService(worker_factory=lambda _codes: worker)
    service.set_target_codes(["2330.TW"])

    service.resume_auto_refresh()
    service.pause_for_cache_sync()
    assert worker.calls == ["resume", "pause"]
    assert service.runtime_state == "paused_for_cache_sync"

    assert service.trigger_refresh_once() is True
    assert worker.calls[-2:] == ["start", "trigger"]
    assert service.runtime_state == "manual_refresh_once"
    worker.running = True
    assert service.trigger_refresh_once() is True
    assert worker.calls.count("start") == 1

    service._worker = _Worker(running=True, failures={"trigger"})
    assert service.trigger_refresh_once() is False
    assert service.last_error == "trigger failed"
    service._closed = True
    service.resume_auto_refresh()
    service.pause_for_cache_sync()
    assert service.trigger_refresh_once() is False


def test_runtime_sync_deferred_started_running_and_error_paths(monkeypatch):
    monkeypatch.setattr(runtime_module, "is_asian_quote_refresh_time", lambda _codes: True)
    monkeypatch.setattr(runtime_module.time, "time", lambda: 100.0)
    service = runtime_module.AsianMarketRuntimeService(worker_factory=lambda codes: _Worker(codes))
    service.set_target_codes(["2330.TW"])
    service._auto_refresh_deferred_until = 200.0
    service._auto_refresh_defer_reason = "startup"
    service._worker = _Worker(["2330.TW"])

    assert service.sync_runtime_state() == "deferred"
    assert service._worker.calls == [("defer", 100.0, "startup")]

    service._worker = _Worker(["2330.TW"], failures={"defer"})
    assert service.sync_runtime_state() == "deferred"
    assert service._worker is None

    service.clear_auto_refresh_defer()
    assert service.sync_runtime_state() == "started"
    assert service.sync_runtime_state() == "running"

    service._worker = _Worker(["2330.TW"], failures={"resume"})
    with pytest.raises(RuntimeError, match="resume failed"):
        service.sync_runtime_state()
    assert service.runtime_state == "error"


def test_runtime_sync_after_hours_and_closed_short_circuits(monkeypatch):
    monkeypatch.setattr(runtime_module, "is_asian_quote_refresh_time", lambda _codes: False)
    shutdowns = []
    monkeypatch.setattr(runtime_module, "request_thread_shutdown", lambda worker, **kwargs: shutdowns.append(worker))
    service = runtime_module.AsianMarketRuntimeService()
    service._worker = _Worker(["2330.TW"], running=True)

    assert service.sync_runtime_state() == "stopped"
    assert len(shutdowns) == 1
    assert service.sync_runtime_state() == "skipped"

    service._closed = True
    assert service.sync_runtime_state() == "shutdown"
    assert service.runtime_state == "paused_for_cache_sync"


def test_runtime_stop_shutdown_and_finished_lifecycle(monkeypatch):
    shutdowns = []
    monkeypatch.setattr(
        runtime_module,
        "request_thread_shutdown",
        lambda worker, **kwargs: shutdowns.append((worker, kwargs)),
    )
    service = runtime_module.AsianMarketRuntimeService()
    assert service.stop() is False
    assert service.stop(auto=True) is False
    worker = _Worker()
    service._worker = worker
    assert service.stop() is True
    assert shutdowns[0][0] is worker
    assert shutdowns[0][1]["stop"].__self__ is worker

    service._worker = _Worker()
    service.shutdown()
    service.shutdown()
    assert service._closed is True
    assert len(shutdowns) == 2

    service._worker = _Worker(failures={"delete"})
    service._runtime_state = "error"
    service._on_worker_finished()
    assert service._worker is None
    assert service.runtime_state == "error"


def test_runtime_finished_progress_and_update_callbacks(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    states = []
    progress = []
    updates = []
    service.sig_runtime_state_changed.connect(states.append)
    service.sig_progress.connect(progress.append)
    service.sig_rt_update.connect(updates.append)

    service._runtime_state = "manual_refresh_once"
    service._on_worker_finished()
    assert service.runtime_state == "manual_refresh_once"
    service._runtime_state = "deferred"
    service._on_worker_progress("正在拉取亚洲市场最新报价")
    assert service.runtime_state == "running"
    service._last_error = "old"
    service._on_worker_progress("cached 4 updates and deferred UI repaint")
    assert service.runtime_state == "degraded"
    assert service.last_error == ""
    service._on_worker_progress("ordinary progress")
    assert len(progress) == 3

    service._runtime_state = ""
    service._on_rt_update({"2330.TW": {"close": 100}})
    assert updates == [{"2330.TW": {"close": 100}}]
    assert service.runtime_state == "running"
    assert states[-1]["message"] == "updates=1"

    service._closed = True
    service._on_worker_progress("ignored")
    service._on_rt_update({"x": 1})
    assert len(progress) == 3
    assert len(updates) == 1


def test_runtime_realtime_cache_write_and_expected_trade_dates(monkeypatch):
    payload = {"2330.TW": {"close": 100}}
    workers = SimpleNamespace(GLOBAL_ASIAN_RT_CACHE=payload)
    writes = []
    monkeypatch.setattr(runtime_module, "_asian_market_workers_module", lambda: workers)
    monkeypatch.setattr(runtime_module, "write_realtime_quote_cache", lambda data, path: writes.append((data, path)))
    runtime_module.AsianMarketRuntimeService._save_rt_cache()
    assert writes == [(payload, runtime_module.RT_JSON_CACHE)]
    monkeypatch.setattr(
        runtime_module,
        "write_realtime_quote_cache",
        lambda *_args: (_ for _ in ()).throw(PermissionError("readonly")),
    )
    runtime_module.AsianMarketRuntimeService._save_rt_cache()

    service = runtime_module.AsianMarketRuntimeService()
    service.set_target_codes(["2330.TW", "0005.HK", "INDEX.ZZ", "bad"])
    now_by_market = {
        "TW": dt.datetime(2026, 7, 15, 13, 0),
        "HK": dt.datetime(2026, 7, 15, 10, 0),
        "ZZ": dt.datetime(2026, 7, 15, 17, 0),
    }
    monkeypatch.setattr(runtime_module.MarketCalendar, "normalize_market", lambda market: market)
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda market: now_by_market[market])
    monkeypatch.setattr(
        runtime_module.MarketCalendar,
        "get_latest_completed_trade_date",
        lambda market: None if market == "ZZ" else dt.date(2026, 7, 14) if market == "TW" else dt.date(2026, 7, 15),
    )
    assert service._expected_latest_trade_dates() == {
        "TW": dt.date(2026, 7, 14),
        "HK": dt.date(2026, 7, 15),
    }


def test_runtime_expected_trade_dates_default_market_pool(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    monkeypatch.setattr(runtime_module, "filter_asian_tickers", lambda: {})
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda _market: dt.datetime(2026, 7, 15, 18, 0))
    monkeypatch.setattr(
        runtime_module.MarketCalendar,
        "get_latest_completed_trade_date",
        lambda _market: dt.date(2026, 7, 15),
    )

    assert set(service._expected_latest_trade_dates()) == {"TW", "HK", "T", "KS"}


def test_runtime_cache_staleness_weekend_fresh_and_trade_date_paths(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda _market: dt.datetime(2026, 7, 12, 10, 0))
    monkeypatch.setattr(runtime_module, "cache_mtime", lambda _path: 0.0)
    monkeypatch.setattr(runtime_module, "load_latest_trade_dates", lambda _path: {})
    monkeypatch.setattr(service, "_expected_latest_trade_dates", lambda: {})
    weekend = service.cache_staleness()
    assert weekend["target_dt"] == dt.datetime(2026, 7, 10, 16, 30)
    assert weekend["stale_by_mtime"] is True
    assert weekend["stale_by_trade_date"] is False

    current = dt.date(2026, 7, 13)
    monkeypatch.setattr(runtime_module.MarketCalendar, "now", lambda _market: dt.datetime(2026, 7, 13, 17, 0))
    monkeypatch.setattr(runtime_module, "cache_mtime", lambda _path: 1.0)
    monkeypatch.setattr(
        runtime_module.MarketCalendar,
        "from_timestamp",
        lambda *_args: dt.datetime(2026, 7, 13, 17, 1),
    )
    monkeypatch.setattr(runtime_module, "load_latest_trade_dates", lambda _path: {"TW": current})
    monkeypatch.setattr(service, "_expected_latest_trade_dates", lambda: {"TW": current})
    assert service.cache_staleness()["stale"] is False

    monkeypatch.setattr(runtime_module, "load_latest_trade_dates", lambda _path: {})
    monkeypatch.setattr(service, "_expected_latest_trade_dates", lambda: {"TW": current})
    stale = service.cache_staleness()
    assert stale["stale_by_trade_date"] is True
    assert stale["stale_markets"] == [("TW", None, current)]


def test_runtime_cache_sync_fresh_success_and_degraded_shapes(monkeypatch):
    service = runtime_module.AsianMarketRuntimeService()
    token = object()
    cancellation_checks = []
    monkeypatch.setattr(runtime_module, "_raise_if_cancelled", cancellation_checks.append)
    monkeypatch.setattr(service, "cache_staleness", lambda: {"stale": False, "marker": 1})
    fresh = service.run_cache_sync_if_stale(cancellation_token=token)
    assert fresh["status"] == "skipped"
    assert cancellation_checks == [token, token]

    emissions = []
    monkeypatch.setattr(service, "cache_staleness", lambda: {"stale": True})
    monkeypatch.setattr(
        runtime_module,
        "event_bus",
        SimpleNamespace(sig_asian_klines_ready=SimpleNamespace(emit=lambda: emissions.append(True))),
    )
    sync_calls = []
    monkeypatch.setattr(
        runtime_module,
        "sync_asian_kline_cache",
        lambda **kwargs: sync_calls.append(kwargs) or (True, None, {"rows": [1, 2], "missing": None}),
    )
    success = service.run_cache_sync_if_stale(cancellation_token=token)
    assert success["status"] == "success"
    assert success["records"] == 2
    assert success["missing"] == []
    assert emissions == [True]
    assert sync_calls == [{"max_workers": 3, "period": "1y", "cancellation_token": token}]

    monkeypatch.setattr(runtime_module, "sync_asian_kline_cache", lambda **_kwargs: (False, "", None))
    degraded = service.run_cache_sync_if_stale(emit_event=False, cancellation_token=token)
    assert degraded["status"] == "degraded"
    assert degraded["error"] == "asian cache sync failed"
    assert service.last_error == "asian cache sync failed"
