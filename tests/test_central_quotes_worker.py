# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.global_store import global_store
from core.market_calendar import MarketCalendar
from ui.workers.central_quotes_worker import CentralQuotesService


def test_central_quotes_service_uses_30s_a_share_polling():
    app = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider())
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

    class DummyModel:
        row_data = [{"代码": "000001"}]

    class DummyTab:
        source_model = DummyModel()

    class DummyProvider:
        def __init__(self):
            self.calls = []

        def fetch_realtime_quotes_batch(self, codes):
            self.calls.append(list(codes))
            return {"000001": {"close": 12.3, "last_close": 12.0}}

    main_window.tab_scan = DummyTab()
    provider = DummyProvider()
    service = CentralQuotesService(main_window, provider)
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


def test_central_quotes_service_collects_foreign_block_codes_from_model_rows():
    app = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    class DummyModel:
        row_data = [
            {"代码": "600000"},
            {"代码": "600000"},
            {"代码": "000001"},
        ]

    class DummyForeignBlockTab:
        model = DummyModel()

    main_window.tab_foreign_block = DummyForeignBlockTab()
    service = CentralQuotesService(main_window, DummyProvider())
    try:
        assert service._get_all_active_codes() == {"600000", "000001"}
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()
