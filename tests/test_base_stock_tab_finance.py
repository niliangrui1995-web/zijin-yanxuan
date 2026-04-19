# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.quote_dispatcher import publish_rt_quotes
from ui.tabs.base_stock_tab import BaseStockTab


class DummyQuotePublisher:
    def publish_external_quotes(self, payload, *, source: str, require_valid: bool = False):
        return publish_rt_quotes(payload, source=source, require_valid=require_valid)


def test_base_stock_tab_async_market_caps_only_fetches_missing_a_share_finance(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    class DummyModel:
        def __init__(self):
            self.row_data = [
                {"代码": "000001", "现价": "10.50", "涨幅%": 5.0, "市值": "--"},
                {"代码": "AAPL", "现价": "188.00", "涨幅%": 1.2, "市值": "--"},
                {"代码": "000002", "现价": "11.20", "涨幅%": 2.0, "市值": "--", "_zongguben": 2_000_000_000},
            ]
            self.quote_calls = []

        def update_quotes(self, quotes):
            self.quote_calls.append(dict(quotes))

    class DummyTaskManager:
        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None):
            result = fn()
            if on_success:
                on_success(result)

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__(data_provider=None)
            self.model = DummyModel()

    import core.task_manager as task_manager_module
    from core.global_store import global_store
    from ui.tabs import base_stock_refresh as refresh_module
    from vcp.engine import VCPEngine

    app = QApplication.instance() or QApplication([])
    latest_quotes = {
        "000001": {"close": 10.5, "last_close": 10.0},
        "AAPL": {"close": 188.0, "last_close": 185.0},
        "000002": {"close": 11.2, "last_close": 11.0, "zongguben": 2_000_000_000},
    }
    finance_calls = []

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: latest_quotes)
    monkeypatch.setattr(task_manager_module, "task_manager", DummyTaskManager())
    refresh_module.MarketCapRefreshBatcher._scheduled = False
    refresh_module.MarketCapRefreshBatcher._pending_codes = set()
    refresh_module.MarketCapRefreshBatcher._waiters = {}
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: callback()),
    )
    monkeypatch.setattr(
        VCPEngine,
        "batch_get_finance_info",
        staticmethod(lambda codes: finance_calls.append(list(codes)) or {
            "000001": {"zongguben": 1_000_000_000, "source": "eastmoney"}
        }),
    )

    tab = DummyTab()
    tab._quote_publisher = DummyQuotePublisher()
    spy = QSignalSpy(event_bus.sig_rt_quotes)
    try:
        tab.async_update_market_caps()
        app.processEvents()

        assert tab.model.quote_calls == [
            latest_quotes,
            {
                "000001": {
                    "zongguben": 1_000_000_000,
                    "_zongguben": 1_000_000_000,
                    "finance_source": "eastmoney",
                }
            },
        ]
        assert finance_calls == [["000001"]]
        assert len(spy) == 1
        payload = spy[0][0]
        assert "000001" in payload
        assert payload["000001"]["zongguben"] == 1_000_000_000
        assert "AAPL" not in payload
        assert "000002" not in payload
    finally:
        tab.deleteLater()
