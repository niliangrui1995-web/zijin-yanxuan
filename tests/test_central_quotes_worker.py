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
            return {
                code: {"close": 12.3, "last_close": 12.0, "source": "eastmoney"}
                for code in codes
            }

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
