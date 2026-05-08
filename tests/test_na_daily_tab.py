# -*- coding: utf-8 -*-
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.global_store import global_store
from core.quote_dispatcher import publish_rt_quotes
from core.task_manager import task_manager
from ui.tabs.na_daily_tab import NADailyTab


class DummyProvider:
    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    def fetch_realtime_quotes_batch(self, codes):
        self.calls.append(list(codes))
        return dict(self.response)


class DummyQuotePublisher:
    def publish_external_quotes(self, payload, *, source: str, require_valid: bool = False):
        return publish_rt_quotes(payload, source=source, require_valid=require_valid)


def _build_tab(monkeypatch, provider):
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    tab = NADailyTab(provider)
    tab._quote_publisher = DummyQuotePublisher()
    tab._patrol_timer.stop()
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {}),
        raising=False,
    )
    return tab


def test_na_daily_tab_refresh_table_market_data_only_fetches_blank_quotes(monkeypatch):
    provider = DummyProvider(
        {
            "000003": {"close": 30.0, "last_close": 29.0},
        }
    )
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {
            "000001": {"close": 10.5, "last_close": 10.0},
            "000002": {"close": 20.0, "last_close": 20.0},
        },
    )
    monkeypatch.setattr(task_manager, "is_active_task", lambda task_id: False)
    monkeypatch.setattr(
        task_manager,
        "run_in_background",
        lambda func, on_success=None, on_error=None, task_id=None: on_success(func()),
    )

    tab = _build_tab(monkeypatch, provider)
    try:
        tab._na_daily_codes = {"000001", "000002", "000003"}
        tab.model.update_data(
            [
                {"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--"},
                {"代码": "000002", "名称": "B", "现价": "20.00", "涨幅%": 0.0, "市值": "--"},
                {"代码": "000003", "名称": "C", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]
        )

        assert tab.model.row_data[0]["现价"] == "10.50"
        assert tab._collect_quote_refresh_codes(force=False) == ["000003"]

        spy = QSignalSpy(event_bus.sig_rt_quotes)
        monkeypatch.setattr(tab, "async_update_market_caps", lambda: None)
        tab.refresh_table_quotes_and_market_caps(quote_task_id="na_daily_quotes")

        assert provider.calls == [["000003"]]
        assert len(spy) == 1
        payload = spy[0][0]
        assert payload["000003"]["close"] == 30.0
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_tab_apply_rows_triggers_cap_and_quote_refresh(monkeypatch, tmp_path):
    provider = DummyProvider()
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})

    tab = _build_tab(monkeypatch, provider)
    refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )

    report_file = Path(tmp_path) / "战报_202604150930.md"
    report_file.write_text("# test\n", encoding="utf-8")

    try:
        tab._apply_na_daily_rows(
            [
                {
                    "代码": "000001",
                    "名称": "A",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": "20260415",
                    "细分板块": "",
                    "股价弹性": "",
                    "催化剂": "",
                    "风控": "",
                    "评级": "",
                    "_report_ts": 20260415093000,
                    "_report_row_rank": 0,
                }
            ],
            [str(report_file)],
            ("sig",),
        )

        assert tab._na_daily_codes == {"000001"}
        assert refresh_calls == [{"quote_task_id": "na_daily_quotes"}]
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_prime_background_loads_rows_immediately(monkeypatch, tmp_path):
    provider = DummyProvider()
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})

    tab = _build_tab(monkeypatch, provider)
    refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )

    report_file = Path(tmp_path) / "战报_202604150930.md"
    report_file.write_text("# test\n", encoding="utf-8")
    monkeypatch.setattr(
        tab,
        "_build_na_daily_rows",
        lambda: (
            [
                {
                    "代码": "000001",
                    "名称": "A",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": "20260415",
                    "细分板块": "先进封装",
                    "股价弹性": "",
                    "催化剂": "北美催化",
                    "风控": "",
                    "评级": "",
                    "_report_ts": 20260415093000,
                    "_report_row_rank": 0,
                }
            ],
            [str(report_file)],
            ("sig",),
        ),
    )

    try:
        tab.prime_background_load()

        assert tab._runtime_started is True
        assert len(tab.model.row_data) == 1
        assert tab._na_daily_codes == {"000001"}
        assert tab._patrol_timer.isActive()
        assert refresh_calls == [{"quote_task_id": "na_daily_quotes"}]
    finally:
        tab.close()
        tab.deleteLater()
