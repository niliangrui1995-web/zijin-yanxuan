# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QWidget

from ui.workers.central_quotes_worker import CentralQuotesService


def test_central_quotes_service_enriches_quotes_with_missing_finance(monkeypatch):
    class DummyProvider:
        def fetch_realtime_quotes_batch(self, codes):
            return {"000001": {"close": 10.5, "last_close": 10.0, "source": "eastmoney"}}

        def get_realtime_runtime_stats(self):
            return {}

    from core.global_store import global_store
    from vcp.engine import VCPEngine

    main_window = QWidget()
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})

    monkeypatch.setattr(
        global_store,
        "get_missing_a_share_finance_codes",
        lambda codes: ["000001"],
    )
    monkeypatch.setattr(
        VCPEngine,
        "batch_get_finance_info",
        staticmethod(lambda codes: {"000001": {"zongguben": 1_000_000_000, "source": "eastmoney"}}),
    )

    try:
        payload = service._fetch_quote_payload({"000001"})
        quote = payload["quotes"]["000001"]
        assert quote["zongguben"] == 1_000_000_000
        assert quote["_zongguben"] == 1_000_000_000
        assert quote["market_cap"] == 10_500_000_000
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()


def test_central_quotes_service_treats_tencent_as_live_source(monkeypatch):
    class DummyProvider:
        def is_online(self):
            return True

        def compact_runtime_caches(self):
            return {}

        def get_realtime_runtime_stats(self):
            return {}

        def fetch_realtime_quotes_batch(self, codes):
            return {
                code: {
                    "close": 10.5,
                    "last_close": 10.0,
                    "source": "tencent",
                }
                for code in codes
            }

    from core.global_store import global_store
    from ui.workers import central_quotes_worker

    main_window = QWidget()
    service = CentralQuotesService(main_window, DummyProvider(), code_supplier=lambda: {"000001"})
    published = []

    def _run_immediately(func, *, on_success=None, on_error=None, task_id=None):
        del task_id
        try:
            result = func()
        except Exception as exc:  # pragma: no cover - test helper mirrors worker API
            if on_error is not None:
                on_error(str(exc))
            return
        if on_success is not None:
            on_success(result)

    monkeypatch.setattr(
        global_store,
        "get_missing_a_share_finance_codes",
        lambda codes: [],
    )
    monkeypatch.setattr(central_quotes_worker.MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(central_quotes_worker.MarketCalendar, "get_market_status", lambda market="CN": "交易中")
    monkeypatch.setattr(central_quotes_worker.task_manager, "run_in_background", _run_immediately)
    monkeypatch.setattr(
        service,
        "publish_external_quotes",
        lambda payload, **kwargs: published.append((payload, kwargs)) or payload,
    )

    try:
        service._trigger_fetch()

        assert service._consecutive_failures == 0
        assert published
        assert published[0][0]["000001"]["source"] == "tencent"
        assert published[0][1]["source"] == "central_quotes.realtime"
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()
