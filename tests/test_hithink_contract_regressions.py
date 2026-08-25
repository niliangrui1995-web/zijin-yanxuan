# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import importlib
import json
import time
import urllib.error
from types import SimpleNamespace

import pandas as pd
import pytest

from app.bootstrap import startup_orchestrator
from core.qt_global_store import GlobalStore
from domains.market_calendar import MarketCalendar
from domains.scan.indicator_service import IndicatorService
from infra.diagnostics.runtime_health import _quote_snapshot
from infra.market_data.provider_ports import ProviderHealthSnapshot
from infra.market_data.realtime_quote_provider import RealtimeQuoteProvider
from infra.market_data.tdx_data_provider import TdxDataProvider
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui import kline_window_runtime as kline_runtime
from ui import main_window_network
from ui.tabs import scan_tab as scan_tab_module
from ui.tabs import watchlist_tab as watchlist_tab_module
from ui.tabs.scan_tab import ScanTab
from ui.tabs.watchlist_tab import WatchlistTab
from ui.workers.scan_worker import ScanWorker
from vcp import data_provider_history_mixin as history
from vcp import data_provider_quotes, data_provider_realtime
from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin
from vcp.data_provider_local import build_offline_quotes
from vcp.data_provider_realtime_mixin import TdxDataProviderRealtimeMixin


class _NameStore:
    def __init__(self):
        self.saved: list[dict[str, str]] = []

    def load_json(self, _key, default=None):
        return {} if default is None else default

    def save_json(self, _key, payload):
        self.saved.append(dict(payload))


class _HithinkNameProvider(TdxDataProviderHistoryMixin):
    def __init__(self):
        self._offline = False
        self._rt_hithink_enabled = True
        self.tdx_vipdoc = ""
        self.code2name = {}
        self.quote_calls: list[list[str]] = []

    @staticmethod
    def _load_local_tdx_name_map_for_codes(_codes):
        return {}

    def fetch_realtime_quotes_batch(self, codes):
        self.quote_calls.append(list(codes))
        return {code: {"source": "hithink", "close": 10.0} for code in codes}


class _ReconnectProvider(TdxDataProviderRealtimeMixin):
    def __init__(self):
        self._offline = False
        self.thread_local = SimpleNamespace()
        self._rt_runtime_cooldown_until = 12.0
        self._rt_runtime_consecutive_failures = 2
        self._rt_runtime_last_error = "runtime failed"
        self._rt_hithink_cooldown_until = 34.0
        self._rt_hithink_last_error = "hithink failed"
        self._rt_eastmoney_cooldown_until = 56.0
        self._rt_eastmoney_last_error = "eastmoney failed"
        self.runtime_resets: list[tuple[tuple, dict]] = []

    def _reset_realtime_runtime(self, *args, **kwargs):
        self.runtime_resets.append((args, kwargs))


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    @staticmethod
    def close():
        return None


class _FakeTextResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("gbk")

    def read(self):
        return self._payload

    @staticmethod
    def close():
        return None


def _hithink_success_payload():
    return {
        "code": 0,
        "data": {
            "timestamp": 1_776_228_000_000,
            "item": [
                {
                    "thscode": "000001.SZ",
                    "last_price": 11.19,
                    "open_price": 11.16,
                    "high_price": 11.21,
                    "low_price": 11.15,
                    "prev_price": 11.17,
                    "price_change": 0.02,
                    "price_change_ratio_pct": 0.18,
                    "volume": 100,
                    "turnover": 1_119.0,
                }
            ],
        },
    }


def _eastmoney_fallback_quote(code: str) -> dict:
    return {
        "open": 10.0,
        "high": 10.2,
        "low": 9.9,
        "close": 10.1,
        "volume": 1_000.0,
        "amount": 10_100.0,
        "last_close": 10.0,
        "date": "2026-04-15",
        "source": "eastmoney",
        "quote_freshness": "network",
    }


def _hithink_source_provider(request_hithink, fetch_eastmoney):
    cooldown_reasons: list[str] = []
    return SimpleNamespace(
        _rt_hithink_enabled=True,
        _rt_hithink_cooldown_until=0.0,
        _request_hithink_quote_batch=request_hithink,
        _fetch_eastmoney_quotes_with_split_retry=fetch_eastmoney,
        _request_sina_quote_batch=lambda *_args, **_kwargs: {},
        _request_tencent_quote_batch=lambda *_args, **_kwargs: {},
        _enter_hithink_cooldown=lambda reason, **_kwargs: cooldown_reasons.append(str(reason)),
        _hithink_cooldown_reasons=cooldown_reasons,
    )


def test_hithink_snapshot_volume_stays_in_shares_through_daily_kline_merge(monkeypatch):
    payload = {
        "code": 0,
        "data": {
            "timestamp": 1_776_228_000_000,
            "item": [
                {
                    "thscode": "000001.SZ",
                    "last_price": 11.19,
                    "open_price": 11.16,
                    "high_price": 11.21,
                    "low_price": 11.15,
                    "prev_price": 11.17,
                    "price_change": 0.02,
                    "price_change_ratio_pct": 0.18,
                    "volume": 14_321_500,
                    "turnover": 160_274_827.37,
                }
            ],
        },
    }
    quote = data_provider_quotes._parse_hithink_snapshot(
        payload,
        {"000001.SZ": "000001"},
        "2026-04-15",
    )["000001"]

    assert quote["volume"] == 14_321_500.0

    monkeypatch.setattr(kline_runtime.MarketCalendar, "is_trade_day", lambda *_args, **_kwargs: True)
    history_frame = pd.DataFrame(
        {"open": [10.0], "high": [10.5], "low": [9.8], "close": [10.1], "volume": [1_000_000.0]},
        index=[pd.Timestamp("2026-04-15")],
    )
    merged = kline_runtime._merge_cn_realtime_bar(
        history_frame,
        quote,
        target_trade_date=dt.date(2026, 4, 15),
    )

    assert merged.iloc[-1]["volume"] == 14_321_500.0


def _assert_quote_amount_matches_share_volume(quote: dict) -> None:
    assert quote["amount"] / (quote["volume"] * quote["close"]) == pytest.approx(1.0, rel=0.01)


def test_eastmoney_lot_volume_is_normalized_to_canonical_shares(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0, _rt_eastmoney_hosts=["push2.eastmoney.com"])
    payload = {
        "rc": 0,
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f2": 11.19,
                    "f5": 143_215,
                    "f6": 160_274_827.37,
                    "f15": 11.21,
                    "f16": 11.15,
                    "f17": 11.16,
                    "f18": 11.17,
                }
            ]
        },
    }
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", lambda *_args, **_kwargs: _FakeHttpResponse(payload))

    quote = data_provider_quotes.request_eastmoney_quote_batch(provider, ["000001"], "2026-04-15")["000001"]

    assert quote["volume"] == 14_321_500.0
    _assert_quote_amount_matches_share_volume(quote)


def test_sina_share_volume_stays_canonical_without_lot_multiplier(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    fields = [""] * 32
    fields[0] = "MOUTAI"
    fields[1] = "1300.00"
    fields[2] = "1302.00"
    fields[3] = "1304.00"
    fields[4] = "1310.00"
    fields[5] = "1297.00"
    fields[8] = "2111118"
    fields[9] = "2757527115"
    fields[30] = "2026-04-30"
    fields[31] = "14:24:06"
    response = 'var hq_str_sh600519="' + ",".join(fields) + '";'
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", lambda *_args, **_kwargs: _FakeTextResponse(response))

    quote = data_provider_quotes.request_sina_quote_batch(provider, ["600519"], "2026-04-15")["600519"]

    assert quote["volume"] == 2_111_118.0
    _assert_quote_amount_matches_share_volume(quote)


def test_tencent_lot_volume_is_normalized_to_canonical_shares(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    fields = [""] * 38
    fields[1] = "MOUTAI"
    fields[2] = "600519"
    fields[3] = "1384.79"
    fields[4] = "1401.17"
    fields[5] = "1400.00"
    fields[6] = "52753"
    fields[30] = "20260430142406"
    fields[31] = "-16.38"
    fields[32] = "-1.17"
    fields[33] = "1401.17"
    fields[34] = "1380.00"
    fields[35] = "1384.79/52753/7316111748"
    response = 'v_sh600519="' + "~".join(fields) + '";'
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", lambda *_args, **_kwargs: _FakeTextResponse(response))

    quote = data_provider_quotes.request_tencent_quote_batch(provider, ["600519"], "2026-04-15")["600519"]

    assert quote["volume"] == 5_275_300.0
    _assert_quote_amount_matches_share_volume(quote)


def test_indicator_daily_kline_zero_amount_uses_share_volume_without_lot_multiplier():
    dates = pd.date_range("2026-04-01", periods=10, freq="B")
    daily_kline = pd.DataFrame(
        {
            "open": [10.0] * 10,
            "high": [10.5] * 10,
            "low": [9.5] * 10,
            "close": [10.0] * 10,
            "volume": [1_000.0] * 10,
            "amount": [0.0] * 10,
        },
        index=dates,
    )

    result = IndicatorService.calculate_indicators(daily_kline, include_chart=False)

    assert result.iloc[-1]["amount"] == 10_000.0


def test_indicator_realtime_kline_derives_amount_from_share_volume_without_lot_multiplier(monkeypatch):
    dates = pd.date_range("2026-04-01", periods=10, freq="B")
    history_frame = pd.DataFrame(
        {
            "open": [10.0] * 10,
            "high": [10.5] * 10,
            "low": [9.5] * 10,
            "close": [10.0] * 10,
            "volume": [1_000.0] * 10,
        },
        index=dates,
    )
    provider = SimpleNamespace(get_data=lambda _code: history_frame)
    monkeypatch.setattr(MarketCalendar, "is_market_active", lambda: False)
    monkeypatch.setattr(MarketCalendar, "now", lambda *_args, **_kwargs: dt.datetime(2026, 4, 14, 15, 0))

    result = RealtimeQuoteProvider(provider).build_realtime_df(
        "000001",
        {
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1_000.0,
            "date": "2026-04-14",
        },
    )

    assert result is not None
    assert result.iloc[-1]["amount"] == 10_000.0


def test_watchlist_add_ui_path_never_enables_online_name_refresh(monkeypatch):
    calls: list[tuple[list[str], bool]] = []
    scheduled_codes: list[str] = []

    class _Provider:
        code2name = {}

        @staticmethod
        def ensure_code_name_map(codes=None, *, refresh_missing=False):
            calls.append((list(codes or []), refresh_missing))
            if refresh_missing:
                raise AssertionError("watchlist GUI attempted an online name refresh")
            return {}

    input_box = SimpleNamespace(
        text=lambda: "000002",
        setFocus=lambda: None,
        selectAll=lambda: None,
        clear=lambda: None,
    )
    tab = SimpleNamespace(
        data_provider=_Provider(),
        add_stock_input=input_box,
        _normalize_quote_code=lambda value: str(value or ""),
        _get_a_share_name_map=lambda: {},
        _schedule_missing_a_share_name_resolution=lambda code: scheduled_codes.append(code),
    )
    tab._resolve_missing_a_share_name = lambda code: WatchlistTab._resolve_missing_a_share_name(tab, code)
    monkeypatch.setattr("ui.tabs.watchlist_tab.show_toast", lambda *_args, **_kwargs: None)

    WatchlistTab._add_custom_stock(tab)

    assert calls == [(["000002"], False)]
    assert scheduled_codes == ["000002"]


def test_watchlist_background_name_resolution_auto_adds_verified_stock(monkeypatch):
    added: list[tuple[str, str, dict, list[str]]] = []
    toasts: list[str] = []
    tab = SimpleNamespace(
        _closing=False,
        _a_share_name_map={},
        _normalize_quote_code=lambda value: str(value or ""),
        refresh_watchlist_names=lambda _names: False,
    )
    monkeypatch.setattr(watchlist_tab_module.watchlist_vm, "is_in_watchlist", lambda _code: False)
    monkeypatch.setattr(
        watchlist_tab_module.watchlist_vm,
        "add_stock",
        lambda code, name, vcp_data=None, source_tags=None: added.append(
            (code, name, dict(vcp_data or {}), list(source_tags or []))
        )
        or True,
    )
    monkeypatch.setattr(watchlist_tab_module, "show_toast", lambda message, *_args, **_kwargs: toasts.append(message))

    WatchlistTab._on_missing_a_share_name_resolved(tab, "000002", {"000002": "万科A"})

    assert added == [
        (
            "000002",
            "万科A",
            {"代码": "000002", "名称": "万科A", "code": "000002", "name": "万科A"},
            ["手动"],
        )
    ]
    assert tab._a_share_name_map == {"000002": "万科A"}
    assert any("自动加入" in message for message in toasts)


def test_scan_name_helper_defaults_to_non_network_refresh():
    calls: list[tuple[list[str], bool]] = []

    class _Provider:
        code2name = {"000001": "平安银行"}

        @staticmethod
        def ensure_code_name_map(codes=None, *, refresh_missing=False):
            calls.append((list(codes or []), refresh_missing))
            if refresh_missing:
                raise AssertionError("scan GUI attempted an online name refresh")
            return dict(_Provider.code2name)

    rows = ScanTab._refresh_scan_result_names(
        SimpleNamespace(data_provider=_Provider()),
        [{"代码": "000001", "名称": "000001"}],
    )

    assert rows[0]["名称"] == "平安银行"
    assert calls == [(["000001"], False)]


def test_scan_result_ready_explicitly_disables_online_name_refresh(monkeypatch):
    refresh_flags: list[bool] = []
    emitted: list[bool] = []

    def _refresh(rows, *, refresh_missing=True):
        refresh_flags.append(refresh_missing)
        return list(rows)

    tab = SimpleNamespace(
        _scan_mode="full",
        _refresh_scan_result_names=_refresh,
        _render_scan_table=lambda _rows: None,
    )
    monkeypatch.setattr(scan_tab_module.event_bus, "sig_scan_updated", SimpleNamespace(emit=lambda: emitted.append(True)))

    ScanTab._on_scan_results(tab, [{"代码": "000001", "名称": "000001"}])

    assert refresh_flags == [False]
    assert emitted == [True]


def test_ensure_code_name_map_uses_hithink_metadata_when_snapshot_has_no_name(monkeypatch):
    data_store_module = importlib.import_module("infra.storage.data_store")
    store = _NameStore()
    provider = _HithinkNameProvider()
    metadata_calls: list[list[str]] = []

    def _metadata_lookup(_provider, codes, *, cancellation_token=None):
        del cancellation_token
        metadata_calls.append(list(codes))
        return {"920045": "天纺标"}

    monkeypatch.setattr(data_store_module, "DataStore", lambda: store)
    monkeypatch.setattr(history, "request_hithink_ticker_names", _metadata_lookup, raising=False)

    names = provider.ensure_code_name_map(["920045"], refresh_missing=True)

    assert names["920045"] == "天纺标"
    assert metadata_calls == [["920045"]]
    assert provider.quote_calls == [["920045"]]
    assert store.saved[-1]["920045"] == "天纺标"


def test_force_reconnect_clears_primary_and_eastmoney_fallback_recovery_state():
    provider = _ReconnectProvider()

    provider.force_reconnect_servers()

    assert provider._rt_hithink_cooldown_until == 0.0
    assert provider._rt_hithink_last_error == ""
    assert provider._rt_eastmoney_cooldown_until == 0.0
    assert provider._rt_eastmoney_last_error == ""
    assert provider.runtime_resets == [
        (("强制刷新同花顺盘中实时行情连接",), {"log_warning": False, "penalize_server": False})
    ]


def test_provider_health_exposes_hithink_and_fallback_state_independently():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider.get_quote_request_stats = lambda: {"quote_primary_source": "hithink"}
    provider.get_realtime_runtime_stats = lambda: {"state": "ready"}
    provider._rt_hithink_cooldown_until = 345.0
    provider._rt_hithink_last_error = "hithink timeout"
    provider._rt_eastmoney_cooldown_until = 234.0
    provider._rt_eastmoney_last_error = "eastmoney timeout"

    snapshot = provider.read_provider_health()

    assert isinstance(snapshot, ProviderHealthSnapshot)
    assert snapshot.hithink_cooldown_until == 345.0
    assert snapshot.hithink_last_error == "hithink timeout"
    assert snapshot.eastmoney_cooldown_until == 234.0
    assert snapshot.eastmoney_last_error == "eastmoney timeout"
    assert snapshot.as_dict()["hithink_cooldown_until"] == 345.0

    diagnostic = _quote_snapshot(SimpleNamespace(data_provider=provider, central_quotes_svc=None))
    assert diagnostic["hithink_cooldown_until"] == 345.0
    assert diagnostic["hithink_last_error"] == "hithink timeout"


def test_hithink_primary_health_does_not_fold_eastmoney_cooldown_into_quote_availability():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider._rt_hithink_enabled = True
    provider.get_quote_request_stats = lambda: {"quote_primary_source": "hithink"}
    provider.get_realtime_runtime_stats = lambda: {"state": "ready"}
    provider._rt_hithink_cooldown_until = 0.0
    provider._rt_hithink_last_error = ""
    provider._rt_eastmoney_cooldown_until = 345.0
    provider._rt_eastmoney_last_error = "eastmoney timeout"

    snapshot = provider.read_provider_health()

    assert data_provider_realtime._quote_cooldown_until(provider) == 0.0
    assert snapshot.quote_cooldown_until == 0.0
    assert snapshot.quote_last_error == ""
    assert snapshot.eastmoney_cooldown_until == 345.0
    assert snapshot.eastmoney_last_error == "eastmoney timeout"


def test_force_reconnect_ui_describes_hithink_primary_not_legacy_eastmoney(monkeypatch):
    messages: list[str] = []

    class _Lifecycle:
        @staticmethod
        def run_background(_name, fn, *, on_success, **_kwargs):
            on_success(fn(None))

    class _Provider:
        @staticmethod
        def is_online():
            return True

        @staticmethod
        def force_reconnect_servers():
            return None

        @staticmethod
        def test_network(timeout):
            assert timeout == 3
            return True

    import ui.components.toast_widget as toast_widget

    monkeypatch.setattr(main_window_network, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(toast_widget, "show_toast", lambda message, *_args, **_kwargs: messages.append(message))
    window = SimpleNamespace(
        data_provider=_Provider(),
        _status_bar_widget=SimpleNamespace(set_status_tone=lambda _tone: None),
        _call_in_ui=lambda callback: callback(),
        _update_network_ui=lambda _online: None,
    )

    main_window_network.force_reconnect(window)

    assert messages and "同花顺" in messages[0]
    assert "东方财富" not in messages[0]


def test_hithink_4001_retries_within_the_request_budget(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    attempts: list[str] = []
    waits: list[float] = []

    def _open(*_args, **_kwargs):
        attempts.append("request")
        if len(attempts) == 1:
            return _FakeHttpResponse({"code": 4001, "message": "limited", "data": None})
        return _FakeHttpResponse(_hithink_success_payload())

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-only-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _open)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda seconds, _token: waits.append(seconds))

    quotes = data_provider_quotes.request_hithink_quote_batch(provider, ["000001"], "2026-04-15")

    assert len(attempts) == 2
    assert len(waits) == 1
    assert quotes["000001"]["source"] == "hithink"


def test_hithink_http_5xx_stops_at_budget_and_redacts_api_key(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    secret = "test-only-key"
    attempts: list[str] = []
    waits: list[float] = []

    def _open(request, **_kwargs):
        attempts.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 503, f"upstream rejected X-api-key={secret}", None, None)

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", secret)
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _open)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda seconds, _token: waits.append(seconds))

    try:
        data_provider_quotes.request_hithink_quote_batch(provider, ["000001"], "2026-04-15")
    except RuntimeError as exc:
        error_text = str(exc)
    else:
        raise AssertionError("HTTP 503 must exhaust the bounded retry budget")

    assert len(attempts) == data_provider_quotes._HITHINK_RETRY_ATTEMPTS
    assert len(waits) == data_provider_quotes._HITHINK_RETRY_ATTEMPTS - 1
    assert secret not in error_text


def test_hithink_empty_timeout_error_preserves_transient_marker(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    attempts: list[str] = []

    def _open(*_args, **_kwargs):
        attempts.append("request")
        raise TimeoutError()

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-only-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _open)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda *_args: None)

    with pytest.raises(RuntimeError, match=r"同花顺实时报价请求失败: timeout") as captured:
        data_provider_quotes.request_hithink_quote_batch(provider, ["000001"], "2026-04-15")

    assert len(attempts) == data_provider_quotes._HITHINK_RETRY_ATTEMPTS
    assert data_provider_realtime._is_hithink_transient_transport_failure(captured.value)


def test_hithink_http_421_retries_over_a_new_request(monkeypatch):
    provider = SimpleNamespace(_rt_api_call_timeout_sec=1.0)
    attempts: list[str] = []

    def _open(request, **_kwargs):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 421, "misdirected request", None, None)
        return _FakeHttpResponse(_hithink_success_payload())

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-only-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _open)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda *_args: None)

    quotes = data_provider_quotes.request_hithink_quote_batch(provider, ["000001"], "2026-04-15")

    assert len(attempts) == 2
    assert quotes["000001"]["source"] == "hithink"


def test_hithink_expired_deadline_preserves_timeout_marker(monkeypatch):
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-only-key")

    with pytest.raises(TimeoutError, match="hithink request timeout"):
        data_provider_quotes._request_hithink_snapshot_payload(
            SimpleNamespace(_rt_api_call_timeout_sec=1.0),
            ["000001.SZ"],
            deadline_monotonic=time.monotonic() - 0.1,
        )


def test_hithink_bj_code_isolated_from_standard_batch(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def _payload_for_codes(_provider, thscodes, **_kwargs):
        calls.append(tuple(thscodes))
        if thscodes == ["430139.BJ"]:
            raise data_provider_quotes._HithinkBusinessError(1002)
        return {
            "code": 0,
            "data": {
                "timestamp": 1_776_228_000_000,
                "item": [
                    {
                        "thscode": thscode,
                        "last_price": 11.19,
                        "open_price": 11.16,
                        "high_price": 11.21,
                        "low_price": 11.15,
                        "prev_price": 11.17,
                        "price_change": 0.02,
                        "price_change_ratio_pct": 0.18,
                        "volume": 100,
                        "turnover": 1_119.0,
                    }
                    for thscode in thscodes
                ],
            },
        }

    monkeypatch.setattr(data_provider_quotes, "_request_hithink_snapshot_payload", _payload_for_codes)

    quotes = data_provider_quotes.request_hithink_quote_batch(
        SimpleNamespace(_rt_api_call_timeout_sec=1.0),
        ["000001", "600519", "430139"],
        "2026-04-15",
    )

    assert calls == [("000001.SZ", "600519.SH"), ("430139.BJ",)]
    assert set(quotes) == {"000001", "600519"}


def test_hithink_3002_isolated_as_missing_code_uses_fallback_without_cooldown(monkeypatch):
    hithink_batches: list[tuple[str, ...]] = []

    def _not_ready(*_args, **_kwargs):
        hithink_batches.append(tuple(_args[1]))
        raise data_provider_quotes._HithinkBusinessError(3002)

    monkeypatch.setattr(data_provider_quotes, "_request_hithink_snapshot_payload", _not_ready)
    eastmoney_batches: list[tuple[str, ...]] = []
    provider = _hithink_source_provider(
        lambda codes, date, **_kwargs: data_provider_quotes.request_hithink_quote_batch(provider, codes, date),
        lambda codes, _date, _min_batch_size, **_kwargs: (
            {
                code: _eastmoney_fallback_quote(code)
                for code in eastmoney_batches.append(tuple(codes)) or codes
            },
            [],
        ),
    )

    quotes, failures, _eastmoney_available, _used_sina, _used_tencent = (
        data_provider_realtime._fetch_realtime_quote_batch_sources(
            provider,
            ["000001", "600519"],
            inferred_trade_date="2026-04-15",
            min_batch_size=1,
            eastmoney_available=True,
        )
    )

    assert {code: quote["source"] for code, quote in quotes.items()} == {
        "000001": "eastmoney",
        "600519": "eastmoney",
    }
    assert hithink_batches == [("000001.SZ", "600519.SH")]
    assert eastmoney_batches == [("000001", "600519")]
    assert failures == []
    assert provider._hithink_cooldown_reasons == []


def test_hithink_split_timeout_keeps_successful_half_for_only_missing_fallback(monkeypatch):
    def _payload_for_codes(_provider, thscodes, **_kwargs):
        if len(thscodes) == 2:
            raise data_provider_quotes._HithinkBusinessError(1002)
        if thscodes == ["000001.SZ"]:
            return _hithink_success_payload()
        if thscodes == ["600519.SH"]:
            raise TimeoutError("right half timed out")
        raise AssertionError(f"unexpected split request: {thscodes}")

    monkeypatch.setattr(data_provider_quotes, "_request_hithink_snapshot_payload", _payload_for_codes)
    eastmoney_batches: list[tuple[str, ...]] = []
    provider = _hithink_source_provider(
        lambda codes, date, **_kwargs: data_provider_quotes.request_hithink_quote_batch(provider, codes, date),
        lambda codes, _date, _min_batch_size, **_kwargs: (
            {
                code: _eastmoney_fallback_quote(code)
                for code in eastmoney_batches.append(tuple(codes)) or codes
            },
            [],
        ),
    )

    quotes, failures, _eastmoney_available, _used_sina, _used_tencent = (
        data_provider_realtime._fetch_realtime_quote_batch_sources(
            provider,
            ["000001", "600519"],
            inferred_trade_date="2026-04-15",
            min_batch_size=1,
            eastmoney_available=True,
        )
    )

    assert quotes["000001"]["source"] == "hithink"
    assert quotes["600519"]["source"] == "eastmoney"
    assert eastmoney_batches == [("600519",)]
    assert failures == []
    assert provider._hithink_cooldown_reasons == []


def test_hithink_split_does_not_swallow_rate_limit_child_error(monkeypatch):
    def _payload_for_codes(_provider, thscodes, **_kwargs):
        if len(thscodes) == 2:
            raise data_provider_quotes._HithinkBusinessError(1002)
        if thscodes == ["000001.SZ"]:
            return _hithink_success_payload()
        if thscodes == ["600519.SH"]:
            raise urllib.error.HTTPError("https://example.invalid", 429, "limited", None, None)
        raise AssertionError(f"unexpected split request: {thscodes}")

    monkeypatch.setattr(data_provider_quotes, "_request_hithink_snapshot_payload", _payload_for_codes)

    with pytest.raises(urllib.error.HTTPError) as captured:
        data_provider_quotes.request_hithink_quote_batch(
            SimpleNamespace(_rt_api_call_timeout_sec=1.0),
            ["000001", "600519"],
            "2026-04-15",
        )

    assert captured.value.code == 429


@pytest.mark.parametrize(
    "field",
    ("last_price", "open_price", "high_price", "low_price", "prev_price", "volume", "turnover"),
)
def test_hithink_null_core_snapshot_value_is_missing_not_network_zero(field):
    payload = _hithink_success_payload()
    payload["data"]["item"][0][field] = None

    quotes = data_provider_quotes._parse_hithink_snapshot(
        payload,
        {"000001.SZ": "000001"},
        "2026-04-15",
    )

    assert quotes == {}


def test_hithink_explicit_zero_core_snapshot_values_are_preserved():
    payload = _hithink_success_payload()
    row = payload["data"]["item"][0]
    for field in ("last_price", "open_price", "high_price", "low_price", "prev_price", "volume", "turnover"):
        row[field] = 0

    quote = data_provider_quotes._parse_hithink_snapshot(
        payload,
        {"000001.SZ": "000001"},
        "2026-04-15",
    )["000001"]

    assert {field: quote[field] for field in ("close", "open", "high", "low", "last_close", "volume", "amount")} == {
        "close": 0.0,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "last_close": 0.0,
        "volume": 0.0,
        "amount": 0.0,
    }


def test_watchlist_unresolved_names_keep_one_background_task_per_code(monkeypatch):
    scheduled: dict[str, tuple] = {}
    added: list[tuple[str, str]] = []

    class _Lifecycle:
        @staticmethod
        def run_background(name, fn, *, on_success, **kwargs):
            task_id = kwargs["task_id"]
            scheduled[name] = (fn, on_success, str(getattr(task_id, "task_id", task_id)))

    class _Provider:
        @staticmethod
        def ensure_code_name_map(codes=None, *, refresh_missing=False, **_kwargs):
            assert refresh_missing is True
            names = {"000001": "平安银行", "000002": "万科A"}
            return {code: names[code] for code in codes or []}

    tab = SimpleNamespace(
        _closing=False,
        data_provider=_Provider(),
        _a_share_name_map={},
        _normalize_quote_code=lambda value: str(value or ""),
        refresh_watchlist_names=lambda _names: False,
    )
    tab._on_missing_a_share_name_resolved = lambda code, names: WatchlistTab._on_missing_a_share_name_resolved(
        tab, code, names
    )
    tab._on_missing_a_share_name_resolution_error = lambda code, error: WatchlistTab._on_missing_a_share_name_resolution_error(
        tab, code, error
    )
    monkeypatch.setattr(watchlist_tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(watchlist_tab_module.watchlist_vm, "is_in_watchlist", lambda _code: False)
    monkeypatch.setattr(
        watchlist_tab_module.watchlist_vm,
        "add_stock",
        lambda code, name, *_args, **_kwargs: added.append((code, name)) or True,
    )
    monkeypatch.setattr(watchlist_tab_module, "show_toast", lambda *_args, **_kwargs: None)

    WatchlistTab._schedule_missing_a_share_name_resolution(tab, "000001")
    WatchlistTab._schedule_missing_a_share_name_resolution(tab, "000002")
    for fn, on_success, _task_id in list(scheduled.values()):
        on_success(fn(None))

    assert set(scheduled) == {
        "manual_stock_name_resolution:000001",
        "manual_stock_name_resolution:000002",
    }
    assert {task_id for _fn, _on_success, task_id in scheduled.values()} == {
        "watchlist_manual_stock_name_resolution:000001",
        "watchlist_manual_stock_name_resolution:000002",
    }
    assert added == [("000001", "平安银行"), ("000002", "万科A")]


def test_scan_worker_passes_cancellation_to_candidate_name_resolution():
    token = CancellationToken()
    seen_tokens: list[CancellationToken | None] = []

    class _Provider:
        code2name = {}

        @staticmethod
        def ensure_code_name_map(_codes, *, refresh_missing=False, cancellation_token=None):
            assert refresh_missing is True
            seen_tokens.append(cancellation_token)
            if cancellation_token is None:
                raise AssertionError("candidate name resolution did not receive the scan cancellation token")
            cancellation_token.cancel("test_cancel_during_name_resolution")
            cancellation_token.raise_if_cancelled()

    worker = ScanWorker(
        _Provider(),
        SimpleNamespace(),
        "2026-04-15",
        "2026-04-15",
        SimpleNamespace(rps_threshold=80),
        cancellation_token=token,
    )
    matrix = {"2026-04-15": {"rps250": {"000001": 90}, "rps120": {"000001": 90}}}

    with pytest.raises(TaskCancelledError):
        worker._refresh_candidate_names(matrix)

    assert seen_tokens == [token]


def test_network_reserves_probe_budget_after_hithink_timeout_for_sina_success(monkeypatch):
    clock = [100.0]
    calls: list[tuple[str, float]] = []

    class _ProbeProvider(TdxDataProviderRealtimeMixin):
        def __init__(self):
            self._rt_hithink_enabled = True
            self._rt_api_call_timeout_sec = 8.0
            self._rt_quote_batch_size = 20
            self._rt_runtime_dedup_window_sec = 8.5
            self._rt_last_network_probe = {}

        def _request_hithink_quote_batch(self, *_args, **_kwargs):
            calls.append(("hithink", self._rt_api_call_timeout_sec))
            clock[0] += self._rt_api_call_timeout_sec
            raise TimeoutError("hithink timed out")

        def _request_eastmoney_quote_batch(self, *_args, **_kwargs):
            calls.append(("eastmoney", self._rt_api_call_timeout_sec))
            clock[0] += self._rt_api_call_timeout_sec
            raise TimeoutError("eastmoney timed out")

        def _request_sina_quote_batch(self, *_args, **_kwargs):
            calls.append(("sina", self._rt_api_call_timeout_sec))
            return {"000001": _eastmoney_fallback_quote("000001")}

        def _request_tencent_quote_batch(self, *_args, **_kwargs):
            raise AssertionError("Sina success should short-circuit Tencent")

    monkeypatch.setattr("vcp.data_provider_realtime_mixin.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "vcp.data_provider_realtime_mixin.MarketCalendar.today",
        lambda *_args, **_kwargs: dt.date(2026, 4, 15),
    )
    provider = _ProbeProvider()

    assert provider.test_network(timeout=2) is True

    assert [source for source, _timeout in calls] == ["hithink", "eastmoney", "sina"]
    assert [timeout for _source, timeout in calls] == [
        pytest.approx(1.4),
        pytest.approx(0.2),
        pytest.approx(0.2),
    ]
    probe = provider.get_last_network_probe()
    assert probe["hithink_quote_probe"].startswith("fail:")
    assert probe["eastmoney_quote_probe"].startswith("fail:")
    assert probe["sina_quote_probe"] == "ok"


def test_smart_startup_uses_extended_hithink_probe_timeout(monkeypatch):
    observed_timeouts: list[int] = []
    provider = SimpleNamespace(test_network=lambda timeout: observed_timeouts.append(timeout) or False)
    orchestrator = SimpleNamespace(
        _alive=lambda: True,
        host=SimpleNamespace(data_provider=provider),
    )
    monkeypatch.setattr(startup_orchestrator, "log_process_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(startup_orchestrator, "_record_smart_startup_completion", lambda *_args, **_kwargs: None)

    startup_orchestrator._execute_smart_startup(orchestrator)

    assert observed_timeouts == [3]


def test_local_quote_fallback_replaces_old_hithink_provenance_without_relabeling_stale_runtime_cache():
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.0, 10.2],
            "volume": [1_000.0, 1_200.0],
            "amount": [10_000.0, 12_240.0],
        },
        index=pd.to_datetime(["2026-04-14", "2026-04-15"]),
    )
    local_quote = build_offline_quotes(["000001"], lambda _code: frame)["000001"]
    store = GlobalStore()
    store.merge_quotes(
        {
            "000001": {
                "close": 10.8,
                "source": "hithink",
                "quote_freshness": "network",
            }
        }
    )
    store.merge_quotes({"000001": {**local_quote, "quote_freshness": "stale"}})

    local_merged = store.get_latest_quotes()["000001"]
    assert local_merged["close"] == 10.2
    assert local_merged["source"] == "offline_local"
    assert local_merged["quote_freshness"] == "stale"

    store.merge_quotes(
        {
            "000001": {
                "close": 10.3,
                "source": "hithink",
                "quote_freshness": "stale",
            }
        }
    )
    assert store.get_latest_quotes()["000001"]["source"] == "hithink"


def test_ensure_code_name_map_reraises_cancellation_from_quote_lookup(monkeypatch):
    data_store_module = importlib.import_module("infra.storage.data_store")
    provider = _HithinkNameProvider()
    provider._rt_hithink_enabled = False

    def _cancelled_fetch(_codes, *, cancellation_token=None):
        assert cancellation_token is not None
        raise TaskCancelledError("name lookup cancelled")

    provider.fetch_realtime_quotes_batch = _cancelled_fetch
    monkeypatch.setattr(data_store_module, "DataStore", _NameStore)

    with pytest.raises(TaskCancelledError, match="name lookup cancelled"):
        provider.ensure_code_name_map(
            ["000001"],
            refresh_missing=True,
            cancellation_token=CancellationToken(),
        )


def test_ensure_code_name_map_reraises_cancellation_from_hithink_metadata(monkeypatch):
    data_store_module = importlib.import_module("infra.storage.data_store")
    provider = _HithinkNameProvider()

    def _empty_fetch(_codes, *, cancellation_token=None):
        assert cancellation_token is not None
        return {}

    def _cancelled_metadata(_provider, _codes, *, cancellation_token=None):
        assert cancellation_token is not None
        raise TaskCancelledError("metadata lookup cancelled")

    provider.fetch_realtime_quotes_batch = _empty_fetch
    monkeypatch.setattr(data_store_module, "DataStore", _NameStore)
    monkeypatch.setattr(history, "request_hithink_ticker_names", _cancelled_metadata)

    with pytest.raises(TaskCancelledError, match="metadata lookup cancelled"):
        provider.ensure_code_name_map(
            ["000001"],
            refresh_missing=True,
            cancellation_token=CancellationToken(),
        )
