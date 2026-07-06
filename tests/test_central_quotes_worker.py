# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QWidget

from core.event_bus import event_bus
from core.global_store import global_store
from core.market_calendar import MarketCalendar
from ui.workers.central_quotes_worker import CentralQuotesService


def test_central_quotes_service_uses_30s_a_share_polling():
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: set())
    try:
        assert service._timer.interval() == 30000
        assert service._COOLDOWN_TICKS == 10
        assert service._heartbeat_every_ticks == 2
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_refresh_after_cache_reload_re_emits_off_market_snapshot(monkeypatch):
    app = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        def __init__(self):
            self.calls = []

        def fetch_realtime_quotes_batch(self, codes):
            self.calls.append(list(codes))
            return {"000001": {"close": 12.3, "last_close": 12.0}}

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: {"000001"})
    spy = QSignalSpy(event_bus.sig_rt_quotes)

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", classmethod(lambda cls, market="CN": False))
    global_store.reset_runtime_state()
    service._off_market_snapshot_emitted = True

    try:
        service.refresh_after_cache_reload()
        app.processEvents()

        assert provider.calls == [["000001"]]
        assert service._off_market_snapshot_emitted is True
        assert len(spy) == 1
        assert spy[0][0]["000001"]["close"] == 12.3
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_skips_timer_duplicate_after_cache_reload(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20

        def fetch_realtime_quotes_batch(self, codes):
            self.calls.append(tuple(sorted(codes)))
            return {code: {"close": 12.3, "last_close": 12.0, "source": "eastmoney"} for code in codes}

        def is_online(self):
            return True

        def get_realtime_runtime_stats(self):
            return {}

        def compact_runtime_caches(self):
            return {}

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: {"000001", "600519"})

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "交易中"))
    monkeypatch.setattr(
        worker_module.task_manager,
        "run_in_background",
        lambda fn, on_success=None, on_error=None, task_id=None: on_success(fn()),
    )

    try:
        service.refresh_after_cache_reload()
        service._trigger_fetch()
        service._post_cache_reload_quiet_until = 0.0
        service._trigger_fetch()

        assert provider.calls == [
            ("000001", "600519"),
            ("000001", "600519"),
        ]
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_normalizes_codes_from_supplier():
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(
        main_window,
        DummyProvider(),
        code_supplier=lambda: ["600000", "600000", "000001", "bad"],
    )
    try:
        assert service._get_all_active_codes() == {"600000", "000001"}
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_refreshes_code_supplier_via_public_setter():
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    try:
        service._missing_code_supplier_warned = True
        service.set_code_supplier(lambda: {"600519"})

        assert service._get_all_active_codes() == {"600519"}
        assert service._missing_code_supplier_warned is False
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_without_code_supplier_skips_polling():
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider())
    try:
        assert service._get_all_active_codes() == set()
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_publish_external_quotes_updates_store_and_emits():
    app = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    spy = QSignalSpy(event_bus.sig_rt_quotes)
    global_store.reset_runtime_state()

    try:
        service.publish_external_quotes(
            {"000001": {"close": 12.8, "last_close": 12.0}},
            source="test.external",
        )
        app.processEvents()

        assert len(spy) == 1
        assert global_store.get_latest_quotes()["000001"]["close"] == 12.8
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_heartbeat_marks_market_closed_pause(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5162,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": 0,
                    "consecutive_failures": 0,
                    "reconnect_count": 0,
                    "cooldown_until": 0,
                    "worker_alive": False,
                },
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    from ui.workers import central_quotes_worker as worker_module

    messages = []
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "盘后"))
    monkeypatch.setattr(service, "_collect_thread_health", lambda: (3, 0))
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))

    try:
        service._tick_count = service._heartbeat_every_ticks
        service._run_maintenance(active_codes_count=163, quote_refreshable=False)

        heartbeat = next(message for message in messages if "[报价站] 心跳" in message)
        assert "实时缓存=0" in heartbeat
        assert "工作线程存活" not in heartbeat
        assert "市场=盘后" in heartbeat
        assert "状态=paused_market_closed" in heartbeat
        assert "活跃依据=market_closed" in heartbeat
        assert "下一步=下个交易时段自动轮询" in heartbeat
        assert "调度器存活=True" in heartbeat
        assert "底层owner线程=未使用(HTTP行情)" in heartbeat
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_heartbeat_marks_active_when_success_recent_despite_owner_stopped(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0

    class DummyProvider:
        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 173,
                "history_symbol_count": 5162,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": now - 18,
                    "consecutive_failures": 0,
                    "reconnect_count": 0,
                    "cooldown_until": 0,
                    "worker_alive": False,
                },
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    messages = []
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "交易中"))
    monkeypatch.setattr(service, "_collect_thread_health", lambda: (5, 0))
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))

    try:
        service._tick_count = service._heartbeat_every_ticks
        service._run_maintenance(active_codes_count=172, quote_refreshable=True)

        heartbeat = next(message for message in messages if "[报价站] 心跳" in message)
        assert "实时缓存=173" in heartbeat
        assert "市场=交易中" in heartbeat
        assert "状态=active_refreshing" in heartbeat
        assert "活跃依据=recent_success" in heartbeat
        assert "下一步=持续30秒调度轮询" in heartbeat
        assert "底层owner线程=未使用(HTTP行情)" in heartbeat
        assert "工作线程存活" not in heartbeat
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_heartbeat_marks_provider_errors_as_degraded(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    class DummyProvider:
        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5162,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": 0,
                    "consecutive_failures": 2,
                    "reconnect_count": 1,
                    "cooldown_until": 0,
                    "worker_alive": False,
                    "last_error": "network down",
                },
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    messages = []
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "交易中"))
    monkeypatch.setattr(service, "_collect_thread_health", lambda: (5, 0))
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))

    try:
        service._tick_count = service._heartbeat_every_ticks
        service._run_maintenance(active_codes_count=172, quote_refreshable=True)

        heartbeat = next(message for message in messages if "[报价站] 心跳" in message)
        assert "状态=degraded_provider_errors" in heartbeat
        assert "活跃依据=provider_errors" in heartbeat
        assert "下一步=等待下一轮重试或进入冷却" in heartbeat
        assert "底层owner线程=未使用(HTTP行情)" in heartbeat
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_auto_polls_when_quote_window_reopens(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20

        def fetch_realtime_quotes_batch(self, codes):
            self.calls.append(tuple(sorted(codes)))
            return {code: {"close": 12.3, "last_close": 12.0, "source": "eastmoney"} for code in codes}

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5162,
                "rt_runtime": {"worker_alive": False},
            }

        def get_realtime_runtime_stats(self):
            return {"worker_alive": False}

        def protect_against_thread_anomaly(self, _count):
            return False

    from ui.workers import central_quotes_worker as worker_module

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: {"000001"})
    market_open = [False]
    submitted_tasks = []
    messages = []

    def _run_immediately(fn, on_success=None, on_error=None, task_id=None):
        submitted_tasks.append(task_id)
        if on_success:
            on_success(fn())

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: market_open[0]))
    monkeypatch.setattr(
        MarketCalendar,
        "get_market_status",
        classmethod(lambda cls, market="CN": "交易中" if market_open[0] else "盘后"),
    )
    monkeypatch.setattr(worker_module.task_manager, "run_in_background", _run_immediately)
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))
    global_store.reset_runtime_state()
    service._off_market_snapshot_emitted = True

    try:
        service._trigger_fetch()
        assert provider.calls == []

        market_open[0] = True
        service._trigger_fetch()

        assert provider.calls == [("000001",)]
        assert submitted_tasks == [worker_module.CENTRAL_QUOTES_POLL]
        assert any("报价窗口恢复，自动拉起实时轮询" in message for message in messages)
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_limits_opening_auction_cold_fetch(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    codes = {f"0000{idx:02d}" for idx in range(1, 8)}

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20

        def fetch_realtime_quotes_batch(self, fetch_codes):
            ordered = tuple(sorted(fetch_codes))
            self.calls.append(ordered)
            return {
                code: {"close": 10.0, "last_close": 9.8, "source": "eastmoney"}
                for code in ordered
            }

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5163,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": 0,
                    "consecutive_failures": 0,
                    "reconnect_count": 0,
                    "cooldown_until": 0,
                    "worker_alive": False,
                },
            }

        def get_realtime_runtime_stats(self):
            return {"worker_alive": False}

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: codes)
    messages = []

    def _run_immediately(fn, on_success=None, on_error=None, task_id=None):
        del on_error, task_id
        if on_success is not None:
            on_success(fn())

    monkeypatch.setattr(worker_module, "_OPENING_WARMUP_FETCH_LIMIT", 3)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "开盘集合竞价"))
    monkeypatch.setattr(worker_module.task_manager, "run_in_background", _run_immediately)
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))
    global_store.reset_runtime_state()

    try:
        service._trigger_fetch()
        service._trigger_fetch()
        service._trigger_fetch()

        assert provider.calls == [
            ("000001", "000002", "000003"),
            ("000004", "000005", "000006"),
            ("000001", "000002", "000007"),
        ]
        assert any("开盘集合竞价冷启动限流，本轮联网 3/7 只" in message for message in messages)
        assert not any(len(call) == len(codes) for call in provider.calls)
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_limits_fallback_cooldown_full_fetch(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0
    codes = {f"{idx:06d}" for idx in range(1, 8)}

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20
            self._rt_eastmoney_cooldown_until = now + 120

        def fetch_realtime_quotes_batch(self, fetch_codes):
            ordered = tuple(sorted(fetch_codes))
            self.calls.append(ordered)
            return {
                code: {"close": 10.0, "last_close": 9.8, "source": "sina"}
                for code in ordered
            }

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5163,
                "rt_runtime": {"worker_alive": False},
            }

        def get_realtime_runtime_stats(self):
            return {"worker_alive": False}

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: codes)

    def _run_immediately(fn, on_success=None, on_error=None, task_id=None):
        del on_error, task_id
        if on_success is not None:
            on_success(fn())

    monkeypatch.setattr(worker_module, "_FALLBACK_PRESSURE_FETCH_LIMIT", 3)
    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "交易中"))
    monkeypatch.setattr(worker_module.task_manager, "run_in_background", _run_immediately)
    global_store.reset_runtime_state()

    try:
        service._trigger_fetch()
        service._trigger_fetch()
        service._trigger_fetch()
        provider._rt_eastmoney_cooldown_until = now - 1
        service._trigger_fetch()

        assert provider.calls == [
            ("000001", "000002", "000003"),
            ("000004", "000005", "000006"),
            ("000001", "000002", "000007"),
            tuple(sorted(codes)),
        ]
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_limits_recent_fallback_pressure_after_cooldown(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0
    codes = {f"{idx:06d}" for idx in range(1, 8)}

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20
            self._rt_eastmoney_cooldown_until = now - 1

        def fetch_realtime_quotes_batch(self, fetch_codes):
            ordered = tuple(sorted(fetch_codes))
            self.calls.append(ordered)
            return {
                code: {"close": 10.0, "last_close": 9.8, "source": "sina"}
                for code in ordered
            }

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 170,
                "history_symbol_count": 5163,
                "rt_runtime": {"worker_alive": False},
            }

        def get_realtime_runtime_stats(self):
            return {"worker_alive": False}

        def get_quote_request_stats(self):
            return {
                "recent_requested_count": 170,
                "recent_pending_count": 170,
                "recent_cache_hit_count": 0,
                "recent_elapsed_ms": 45189.0,
                "recent_source_layers": ["sina", "network_throttled_fallback_pressure"],
                "recent_status": "network_partial_with_fallback",
                "recent_ended_at_ts": now - 20,
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: codes)

    def _run_immediately(fn, on_success=None, on_error=None, task_id=None):
        del on_error, task_id
        if on_success is not None:
            on_success(fn())

    monkeypatch.setattr(worker_module, "_FALLBACK_PRESSURE_FETCH_LIMIT", 3)
    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "trading"))
    monkeypatch.setattr(worker_module.task_manager, "run_in_background", _run_immediately)
    global_store.reset_runtime_state()

    try:
        service._trigger_fetch()
        service._trigger_fetch()

        assert provider.calls == [
            ("000001", "000002", "000003"),
            ("000004", "000005", "000006"),
        ]
        assert not any(len(call) == len(codes) for call in provider.calls)
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_uses_own_pressure_stats_when_scan_recent_overwrites_provider(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0
    codes = {f"{idx:06d}" for idx in range(1, 8)}
    scan_recent_stats = {
        "recent_requested_count": 38,
        "recent_pending_count": 1,
        "recent_cache_hit_count": 37,
        "recent_elapsed_ms": 320.0,
        "recent_source_layers": ["runtime_cache"],
        "recent_status": "network_ok",
        "recent_ended_at_ts": now - 5,
    }
    central_pressure_stats = {
        "recent_requested_count": 168,
        "recent_pending_count": 168,
        "recent_cache_hit_count": 0,
        "recent_elapsed_ms": 11559.0,
        "recent_source_layers": ["sina"],
        "recent_status": "network_ok",
        "recent_ended_at_ts": now - 20,
    }

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20
            self._rt_eastmoney_cooldown_until = now - 1

        def fetch_realtime_quotes_batch(self, fetch_codes):
            ordered = tuple(sorted(fetch_codes))
            self.calls.append(ordered)
            return {
                code: {"close": 10.0, "last_close": 9.8, "source": "sina"}
                for code in ordered
            }

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 169,
                "history_symbol_count": 5163,
                "rt_runtime": {"worker_alive": False},
            }

        def get_realtime_runtime_stats(self):
            return {"worker_alive": False}

        def get_quote_request_stats(self):
            return scan_recent_stats

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: codes)
    service._last_central_quote_request_stats = central_pressure_stats

    def _run_immediately(fn, on_success=None, on_error=None, task_id=None):
        del on_error, task_id
        if on_success is not None:
            on_success(fn())

    monkeypatch.setattr(worker_module, "_FALLBACK_PRESSURE_FETCH_LIMIT", 3)
    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "收盘集合竞价"))
    monkeypatch.setattr(worker_module.task_manager, "run_in_background", _run_immediately)
    global_store.reset_runtime_state()

    try:
        service._trigger_fetch()

        assert provider.calls == [("000001", "000002", "000003")]
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_skips_timer_fetch_when_fallback_pressure_has_fresh_cache(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0
    codes = {f"{idx:06d}" for idx in range(1, 8)}
    scan_recent_stats = {
        "recent_requested_count": 38,
        "recent_pending_count": 1,
        "recent_cache_hit_count": 37,
        "recent_elapsed_ms": 320.0,
        "recent_source_layers": ["runtime_cache"],
        "recent_status": "network_ok",
        "recent_ended_at_ts": now - 5,
    }
    central_slow_pressure_stats = {
        "recent_requested_count": 103,
        "recent_pending_count": 103,
        "recent_cache_hit_count": 0,
        "recent_elapsed_ms": 17116.78,
        "recent_source_layers": ["eastmoney"],
        "recent_status": "network_ok",
        "recent_ended_at_ts": now - 20,
    }

    class DummyProvider:
        def __init__(self):
            self.calls = []
            self._rt_api_call_timeout_sec = 1.0
            self._rt_quote_batch_size = 20
            self._rt_eastmoney_cooldown_until = now - 1

        def fetch_realtime_quotes_batch(self, fetch_codes):
            self.calls.append(tuple(sorted(fetch_codes)))
            return {
                code: {"close": 10.0, "last_close": 9.8, "source": "sina"}
                for code in fetch_codes
            }

        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": len(codes),
                "history_symbol_count": 5163,
                "rt_runtime": {
                    "last_success_at": now - 10,
                    "worker_alive": False,
                },
            }

        def get_realtime_runtime_stats(self):
            return {
                "last_success_at": now - 10,
                "worker_alive": False,
            }

        def get_quote_request_stats(self):
            return scan_recent_stats

        def protect_against_thread_anomaly(self, _count):
            return False

    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider, code_supplier=lambda: codes)
    service._last_central_quote_request_stats = central_slow_pressure_stats
    submitted_tasks = []
    messages = []

    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "trading"))
    monkeypatch.setattr(
        worker_module.task_manager,
        "run_in_background",
        lambda fn, on_success=None, on_error=None, task_id=None: submitted_tasks.append(task_id),
    )
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))
    global_store.reset_runtime_state()

    try:
        service._trigger_fetch()

        assert provider.calls == []
        assert submitted_tasks == []
        assert any("跳过本轮自动联网" in message for message in messages)
    finally:
        global_store.reset_runtime_state()
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_heartbeat_counts_eastmoney_quote_cooldown(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    now = 1_800_000_000.0

    class DummyProvider:
        _rt_eastmoney_cooldown_until = now + 90

        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 169,
                "history_symbol_count": 5163,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": now - 20,
                    "consecutive_failures": 0,
                    "reconnect_count": 0,
                    "cooldown_until": 0,
                    "worker_alive": False,
                },
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    messages = []
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    monkeypatch.setattr(worker_module.time, "time", lambda: now)
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "收盘集合竞价"))
    monkeypatch.setattr(service, "_collect_thread_health", lambda: (5, 0))
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))

    try:
        service._tick_count = service._heartbeat_every_ticks
        service._run_maintenance(
            active_codes_count=168,
            quote_refreshable=True,
            market_status="收盘集合竞价",
        )

        heartbeat = next(message for message in messages if "[报价站] 心跳" in message)
        assert "冷却剩余=90s" in heartbeat
        assert "状态=cooldown" in heartbeat
        assert "市场=收盘集合竞价" in heartbeat
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_heartbeat_marks_opening_warmup_without_dead_thread(monkeypatch):
    _ = QApplication.instance() or QApplication([])
    main_window = QWidget()

    from ui.workers import central_quotes_worker as worker_module

    class DummyProvider:
        def compact_runtime_caches(self):
            return {
                "rt_quote_cache_size": 0,
                "history_symbol_count": 5163,
                "rt_runtime": {
                    "inflight": 0,
                    "last_success_at": 0,
                    "consecutive_failures": 0,
                    "reconnect_count": 0,
                    "cooldown_until": 0,
                    "worker_alive": False,
                },
            }

        def protect_against_thread_anomaly(self, _count):
            return False

    messages = []
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(MarketCalendar, "get_market_status", classmethod(lambda cls, market="CN": "开盘集合竞价"))
    monkeypatch.setattr(service, "_collect_thread_health", lambda: (5, 0))
    monkeypatch.setattr(worker_module.log, "info", lambda message: messages.append(str(message)))

    try:
        service._tick_count = service._heartbeat_every_ticks
        service._run_maintenance(
            active_codes_count=180,
            quote_refreshable=True,
            market_status="开盘集合竞价",
        )

        heartbeat = next(message for message in messages if "[报价站] 心跳" in message)
        assert "实时缓存=首轮预热中(0)" in heartbeat
        assert "状态=opening_warmup" in heartbeat
        assert "活跃依据=opening_warmup" in heartbeat
        assert "下一步=集合竞价限量预热，连续竞价后全量轮询" in heartbeat
        assert "底层owner线程=未使用(HTTP行情)" in heartbeat
        assert "工作线程存活" not in heartbeat
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()
