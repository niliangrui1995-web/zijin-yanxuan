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
