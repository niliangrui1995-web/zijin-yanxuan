# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from ui.workers import central_quotes_worker as worker_module


class _Provider:
    def get_quote_request_stats(self):
        return {}


def _service():
    return worker_module.CentralQuotesService(None, _Provider(), code_supplier=lambda: [])


def test_central_quote_provider_stats_timer_failure_and_health_states(monkeypatch, qt_application):
    provider = _Provider()
    monkeypatch.setattr(
        worker_module,
        "read_provider_health",
        lambda _provider: SimpleNamespace(request_stats={"a": 1}, eastmoney_cooldown_until=0),
    )
    assert worker_module._provider_request_stats(provider) == {"a": 1}
    monkeypatch.setattr(
        worker_module,
        "read_provider_health",
        lambda _provider: SimpleNamespace(request_stats={}, eastmoney_cooldown_until=0),
    )
    provider.get_quote_request_stats = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    assert worker_module._provider_request_stats(provider) == {}
    provider.get_quote_request_stats = lambda: "bad"
    assert worker_module._provider_request_stats(provider) == {}

    service = _service()
    try:
        cooldowns = []
        service._poller = SimpleNamespace(
            enter_realtime_cooldown=lambda *args, **kwargs: cooldowns.append((args, kwargs))
        )
        service._FAILURE_THRESHOLD = 2
        service._COOLDOWN_TICKS = 4
        service._record_failure("one")
        assert service._consecutive_failures == 1
        service._record_failure("two")
        assert service._circuit_breaker_cooldown == 4
        assert cooldowns[0][1]["cooldown_sec"] == 300

        class _BrokenTimer:
            def isActive(self):
                raise RuntimeError("gone")

            def start(self, interval):
                self.interval = interval

            def stop(self):
                pass

        service._timer.stop()
        service._timer = _BrokenTimer()
        assert not service._timer_is_active()
        service._closed = True
        assert not service._ensure_timer_running()
        service._closed = False
        assert service._ensure_timer_running()

        monkeypatch.setattr(service, "_timer_is_active", lambda: True)
        service._closed = True
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "closed"
        )
        service._closed = False
        monkeypatch.setattr(service, "_timer_is_active", lambda: False)
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "degraded_scheduler_inactive"
        )
        monkeypatch.setattr(service, "_timer_is_active", lambda: True)
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=False,
                market_status="收盘",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "paused_market_closed"
        )

        service._is_fetching = True
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="开盘集合竞价",
                runtime_stats={"inflight": "bad", "consecutive_failures": "bad"},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "opening_warmup_fetching"
        )
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "fetching"
        )
    finally:
        service._closed = True


def test_central_quote_status_warmup_stats_and_pressure(monkeypatch, qt_application):
    service = _service()
    try:
        assert service._rt_cache_status_text(2, "x") == "2"
        assert service._rt_cache_status_text(0, "opening_warmup") == "首轮预热中(0)"
        assert service._rt_cache_status_text(0, "fetching") == "首轮待写入(0)"
        assert service._owner_thread_status_text({}, True) == "存活"
        assert service._owner_thread_status_text({"owner_thread_applicable": True}, False) == "已停止"
        assert service._owner_thread_status_text({}, False) == "未使用(HTTP行情)"

        monkeypatch.setattr(
            worker_module.MarketCalendar,
            "get_market_status",
            lambda _market: (_ for _ in ()).throw(RuntimeError("bad")),
        )
        assert service._market_status_text() == "unknown"

        monkeypatch.setattr(worker_module, "_OPENING_WARMUP_FETCH_LIMIT", 2)
        codes = {"000001", "000002", "000003"}
        assert service._opening_warmup_codes(codes, market_status="交易中") == codes
        first = service._opening_warmup_codes(codes, market_status="开盘集合竞价")
        second = service._opening_warmup_codes(codes, market_status="开盘集合竞价")
        assert len(first) == len(second) == 2
        assert first != second
        assert service._opening_warmup_codes({"000001"}, market_status="开盘集合竞价") == {"000001"}

        assert service._stats_int({"x": "bad"}, "x") == 0
        assert service._stats_float({"x": "bad"}, "x") == 0.0
        assert service._stats_time(123) == 123.0
        assert service._stats_time("") == 0.0
        assert service._stats_time("bad") == 0.0
        parsed = service._stats_time("2026-07-15T09:30:00")
        assert parsed > 0

        assert service._quote_stats_fallback_pressure({}, now=100, label="x") == (False, "")
        stats = {
            "recent_requested_count": 120,
            "recent_pending_count": 110,
            "recent_cache_hit_count": 0,
            "recent_elapsed_ms": 12000,
            "recent_status": "partial_fallback",
            "recent_source_layers": ["sina_fallback"],
            "recent_ended_at_ts": 95,
        }
        pressure, reason = service._quote_stats_fallback_pressure(stats, now=100, label="provider")
        assert pressure and "provider fallback pressure" in reason
        stats["recent_ended_at_ts"] = 0
        assert service._quote_stats_fallback_pressure(stats, now=100, label="provider") == (False, "")
    finally:
        service.shutdown()


def test_central_quote_fallback_code_rotation_and_skip_decisions(monkeypatch, qt_application):
    service = _service()
    try:
        monkeypatch.setattr(
            worker_module,
            "read_provider_health",
            lambda _provider: SimpleNamespace(request_stats={}, eastmoney_cooldown_until=200),
        )
        assert service._quote_fallback_cooldown_left({"quote_cooldown_until": "bad"}, now=100) == 100

        codes = {f"{index:06d}" for index in range(5)}
        monkeypatch.setattr(worker_module, "_FALLBACK_PRESSURE_FETCH_LIMIT", 2)
        monkeypatch.setattr(worker_module.time, "time", lambda: 100.0)
        monkeypatch.setattr(service, "_quote_fallback_cooldown_left", lambda *_args, **_kwargs: 10)
        monkeypatch.setattr(service, "_recent_quote_fallback_pressure", lambda **_kwargs: (False, ""))
        first = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        second = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        third = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        assert len(first) == len(second) == len(third) == 2
        assert service._fallback_pressure_codes(codes, provider_stats={}, market_status="开盘集合竞价") == codes

        assert not service._should_skip_fallback_pressure_fetch(
            codes, provider_stats={}, maintenance_stats={}, market_status="交易中", reason="manual"
        )
        assert not service._should_skip_fallback_pressure_fetch(
            set(), provider_stats={}, maintenance_stats={}, market_status="交易中", reason="timer"
        )
        monkeypatch.setattr(service, "_recent_quote_fallback_pressure", lambda **_kwargs: (True, "pressure"))
        service._last_fallback_pressure_skip_log_at = 0
        maintenance = {
            "rt_quote_cache_size": "bad",
            "rt_runtime": {"last_success_at": "bad"},
        }
        assert not service._should_skip_fallback_pressure_fetch(
            codes, provider_stats={}, maintenance_stats=maintenance, market_status="交易中", reason="timer"
        )
        maintenance = {"rt_quote_cache_size": 5, "rt_runtime": {"last_success_at": 95}}
        assert service._should_skip_fallback_pressure_fetch(
            codes, provider_stats={}, maintenance_stats=maintenance, market_status="交易中", reason="timer"
        )
        maintenance["rt_runtime"]["last_success_at"] = 1000
        assert not service._should_skip_fallback_pressure_fetch(
            codes, provider_stats={}, maintenance_stats=maintenance, market_status="交易中", reason="timer"
        )
    finally:
        service.shutdown()


def test_central_quote_off_market_callbacks_and_realtime_result_error_paths(monkeypatch, qt_application):
    service = _service()
    captured = []
    monkeypatch.setattr(
        worker_module,
        "_submit_central_task",
        lambda service, name, fn, on_success, on_error, task_id, timeout: captured.append(
            (name, fn, on_success, on_error)
        ),
    )
    published = []
    monkeypatch.setattr(
        service,
        "publish_external_quotes",
        lambda quotes, **kwargs: published.append((quotes, kwargs)) or quotes,
    )
    try:
        service._emit_off_market_snapshot({"000001"})
        name, bg, success, error = captured.pop()
        assert name == "off_market_snapshot"
        generation = service._off_market_snapshot_generation
        service._off_market_snapshot_generation += 1
        success({"quotes": {"000001": {"close": 10}}})
        assert not published
        service._off_market_snapshot_generation = generation
        success({"quotes": {"000001": {"close": 0}}})
        assert not published
        service._off_market_snapshot_fetching = True
        service._off_market_snapshot_generation = generation
        success({"quotes": {"000001": {"close": 10}}})
        assert published[-1][1]["source"] == "central_quotes.off_market"
        service._off_market_snapshot_fetching = True
        error_generation = service._off_market_snapshot_generation
        service._off_market_snapshot_generation += 1
        error("stale")
        assert service._off_market_snapshot_fetching
        service._off_market_snapshot_generation = error_generation
        error("bad")
        assert not service._off_market_snapshot_fetching

        captured.clear()
        service._closed = False
        service._get_all_active_codes = lambda: {"000001"}
        service._run_maintenance = lambda **_kwargs: {}
        service._poller = SimpleNamespace(is_online=lambda: True, get_runtime_stats=lambda: {})
        service._ensure_timer_running = lambda: True
        service._market_status_text = lambda: "交易中"
        service._observe_quote_window = lambda _value: None
        service._opening_warmup_codes = lambda codes, **_kwargs: codes
        service._fallback_pressure_codes = lambda codes, **_kwargs: codes
        service._should_skip_fallback_pressure_fetch = lambda *args, **kwargs: False
        monkeypatch.setattr(worker_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
        service._trigger_fetch_for_reason("manual")
        name, bg, result, failure = captured.pop()
        assert name == "realtime_poll"
        token = service._fetch_generation
        service._fetch_generation += 1
        result({"quotes": {"000001": {"close": 10, "source": "eastmoney"}}})
        assert service._is_fetching
        service._fetch_generation = token
        service._closed = True
        result({})
        assert not service._is_fetching
        service._closed = False
        service._is_fetching = True
        result({"quotes": {"000001": {"close": 10, "source": "eastmoney"}}})
        assert not service._is_fetching

        service._is_fetching = True
        service._fetch_generation = token + 1
        failure("stale")
        assert service._is_fetching
        service._fetch_generation = token
        service._closed = True
        failure("closed")
        assert not service._is_fetching
    finally:
        service._closed = True
        service._task_lifecycle.shutdown(timeout_ms=10)
