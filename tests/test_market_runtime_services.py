from __future__ import annotations

import builtins
import sys
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import scan_runtime_service
from domains.global_earnings_calendar import http_utils
from domains.market_calendar import MarketCalendar
from infra.market_data import adjustment_service as adjustment_module
from infra.market_data.adjustment_service import AdjustmentService
from infra.market_data.local_history_provider import LocalHistoryProvider
from infra.market_data.realtime_quote_provider import RealtimeQuoteProvider
from infra.navigation.external_terminal_navigator import ExternalTerminalNavigator


class _RecorderLog:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", message))

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))


def test_http_utils_decodes_text_content_and_status_fallbacks():
    response = SimpleNamespace(text="ready", encoding="")
    assert http_utils.response_text(response, encoding="gbk") == "ready"
    assert response.encoding == "gbk"

    class BadEncodingResponse:
        text = "ready"

        @property
        def encoding(self):
            return ""

        @encoding.setter
        def encoding(self, value):
            raise ValueError(value)

    assert http_utils.response_text(BadEncodingResponse(), encoding="gbk") == "ready"

    assert http_utils.response_text(SimpleNamespace(text=None, content="中文".encode("utf-8"))) == "中文"
    assert http_utils.response_text(SimpleNamespace(text=None, content=123)) == ""

    called = []
    http_utils.raise_for_status(SimpleNamespace(raise_for_status=lambda: called.append(True)))
    assert called == [True]

    with pytest.raises(http_utils.requests.HTTPError):
        http_utils.raise_for_status(SimpleNamespace(status_code=500))


def test_adjustment_service_delegates_to_provider_state(monkeypatch):
    provider = SimpleNamespace(
        tdx_vipdoc="vipdoc",
        gbbq_cache_file="cache",
        legacy_gbbq_cache_file="legacy",
        _local_gbbq={"old": True},
        _local_gbbq_code_cache={"000001": "cached"},
    )
    service = AdjustmentService(provider)

    monkeypatch.setattr(adjustment_module, "load_local_gbbq", lambda *args, **kwargs: {"loaded": args, "force": kwargs})
    monkeypatch.setattr(adjustment_module, "load_local_gbbq_for_code", lambda *args: {"code": args[-1], "cache": args[-2]})
    monkeypatch.setattr(adjustment_module, "get_market_code", lambda code: f"market:{code}")
    monkeypatch.setattr(adjustment_module, "apply_forward_adjustment_impl", lambda *args: {"adjusted": args})

    loaded = service.load_local_gbbq(force=True)
    by_code = service.load_local_gbbq_for_code("000001")

    assert provider._local_gbbq is loaded
    assert loaded["force"] == {"force": True}
    assert by_code == {"code": "000001", "cache": {"000001": "cached"}}
    assert service.get_market_code("600000") == "market:600000"
    assert service.apply_forward_adjustment("api", 1, "000001", "df")["adjusted"][-1] is loaded
    assert service.apply_forward_adjustment("api", 1, "000001", "df", local_gbbq={"x": 1})["adjusted"][-1] == {"x": 1}


def test_scan_runtime_service_delegates_to_engine_and_domain_services(monkeypatch):
    calls = []

    monkeypatch.setattr(scan_runtime_service.VCPEngine, "get_instance", classmethod(lambda cls: "engine"))
    monkeypatch.setattr(
        scan_runtime_service.IndicatorService,
        "calculate_indicators",
        staticmethod(lambda df, include_chart=True: calls.append(("indicators", df, include_chart)) or "indicators"),
    )
    monkeypatch.setattr(
        scan_runtime_service.VCPEngine,
        "batch_get_finance_info",
        staticmethod(lambda codes: calls.append(("finance", codes)) or {"000001": {}}),
    )
    monkeypatch.setattr(
        scan_runtime_service.VCPEngine,
        "batch_check_market_cap",
        staticmethod(lambda codes, close_prices=None: calls.append(("market_cap", codes, close_prices)) or {"000001": 1}),
    )
    monkeypatch.setattr(
        scan_runtime_service.BreakoutMonitorService,
        "precompute_ready_pool",
        staticmethod(lambda *args, **kwargs: calls.append(("pool", args, kwargs)) or {"000001": {}}),
    )
    monkeypatch.setattr(
        scan_runtime_service.BreakoutMonitorService,
        "rt_quick_check",
        staticmethod(lambda quote, entry: calls.append(("quick", quote, entry)) or (True, "near", 80)),
    )

    assert scan_runtime_service.create_scan_engine() == "engine"
    assert scan_runtime_service.calculate_scan_indicators("df", include_chart=False) == "indicators"
    assert scan_runtime_service.batch_get_finance_info(["000001"]) == {"000001": {}}
    assert scan_runtime_service.batch_check_market_cap(["000001"], close_prices={"000001": 10}) == {"000001": 1}
    assert scan_runtime_service.precompute_ready_pool({"data": 1}, {"r120": 1}, {"r250": 1}, "params") == {"000001": {}}
    assert scan_runtime_service.quick_check_breakout({"close": 10}, {"box_high": 11}) == (True, "near", 80)

    assert [call[0] for call in calls] == ["indicators", "finance", "market_cap", "pool", "quick"]


def test_scan_runtime_service_build_rps_matrix_uses_polars_engine(monkeypatch):
    calls = []

    import vcp.polars_engine as polars_engine

    monkeypatch.setattr(
        polars_engine,
        "build_rps_matrix_pl",
        lambda data, start, end, cache: calls.append((data, start, end, cache)) or {"20260420": {}},
    )

    result = scan_runtime_service.build_rps_matrix({"000001": "df"}, "2026-04-01", "2026-04-20", {"cache": 1})

    assert result == {"20260420": {}}
    assert calls == [({"000001": "df"}, "2026-04-01", "2026-04-20", {"cache": 1})]


def test_local_history_provider_prefers_sufficient_local_data():
    local_df = pd.DataFrame(
        {
            "datetime": pd.date_range("2025-01-01", periods=260, freq="D"),
            "close": range(260),
            "vol": range(260),
        }
    ).set_index("datetime")
    adjusted = local_df.copy()
    provider = SimpleNamespace(
        tdx_vipdoc="D:/HT/vipdoc",
        _get_market_code=lambda code: 0,
        _fetch_from_local_tdx=lambda code: local_df,
        _apply_forward_adjustment=lambda api, market, code, df: adjusted,
    )

    result = LocalHistoryProvider(provider, logger=_RecorderLog()).fetch_standard_data("api", "000001")

    assert "volume" in result.columns
    assert "vol" not in result.columns


def test_local_history_provider_fetches_network_and_renames_volume():
    class _Api:
        def get_security_bars(self, *_args):
            return [
                {"datetime": "2026-01-01 00:00", "close": 10.0, "vol": 100},
                {"datetime": "2026-01-02 00:00", "close": 11.0, "vol": 120},
            ]

    provider = SimpleNamespace(
        tdx_vipdoc="",
        _get_market_code=lambda code: 1,
        _apply_forward_adjustment=lambda api, market, code, df: df,
    )

    result = LocalHistoryProvider(provider, logger=_RecorderLog()).fetch_standard_data(_Api(), "600000", count=2)

    assert list(result["volume"]) == [100, 120]
    assert list(result.index) == sorted(result.index)


def test_local_history_provider_uses_existing_cache_before_open_and_without_server_pool():
    existing = pd.DataFrame({"close": [1, 2]}, index=pd.date_range("2026-01-01", periods=2))
    provider = SimpleNamespace(
        get_data=lambda code: existing,
        _is_before_930_today=lambda: True,
        _is_after_1500_today=lambda: False,
        server_pool=True,
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())

    assert service.get_data_fresh_for_chart("000001") is existing

    provider._is_before_930_today = lambda: False
    provider.server_pool = None
    assert service.get_data_fresh_for_chart("000001") is existing


def test_local_history_provider_keeps_fresh_after_close_cache(monkeypatch):
    latest = pd.Timestamp("2026-04-20")
    existing = pd.DataFrame({"close": [1, 2]}, index=[latest - pd.Timedelta(days=1), latest])
    provider = SimpleNamespace(
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: True,
        server_pool=True,
    )
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": latest.date()))

    assert LocalHistoryProvider(provider, logger=_RecorderLog()).get_data_fresh_for_chart("000001") is existing


def test_local_history_provider_merges_incremental_data(monkeypatch):
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    new = pd.DataFrame(
        {"close": [260.0, 261.0], "open": [259.0, 260.0]},
        index=pd.date_range(existing.index.max() + pd.Timedelta(days=1), periods=2, freq="D"),
    )
    provider = SimpleNamespace(
        cache_data={},
        cache_lock=nullcontext(),
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda api, code, count: new)
    monkeypatch.setattr(LocalHistoryProvider.__module__ + ".IndicatorService.calculate_indicators", lambda df: df)

    result = service.get_data_fresh_for_chart("000001")

    assert len(result) == 252
    assert provider.cache_data["000001"] is result


def test_local_history_provider_refetches_full_when_incremental_gap_is_large(monkeypatch):
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    new = pd.DataFrame(
        {"close": [300.0], "open": [299.0]},
        index=[existing.index.max() + pd.Timedelta(days=20)],
    )
    full = pd.DataFrame(
        {"close": [20.0 + idx for idx in range(260)], "open": [19.0 + idx for idx in range(260)]},
        index=pd.date_range("2025-01-01", periods=260, freq="D"),
    )
    provider = SimpleNamespace(
        cache_data={},
        cache_lock=nullcontext(),
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    calls = []
    service = LocalHistoryProvider(provider, logger=_RecorderLog())

    def fake_fetch(api, code, count):
        calls.append(count)
        return new if len(calls) == 1 else full

    monkeypatch.setattr(service, "fetch_standard_data", fake_fetch)
    monkeypatch.setattr("infra.market_data.local_history_provider.IndicatorService.calculate_indicators", lambda df: df)

    result = service.get_data_fresh_for_chart("000001")

    assert result is full
    assert provider.cache_data["000001"] is full
    assert len(calls) == 2


def test_local_history_provider_fetches_full_for_short_cache(monkeypatch):
    existing = pd.DataFrame({"close": [10.0]}, index=[pd.Timestamp("2025-01-01")])
    full = pd.DataFrame(
        {"close": [20.0 + idx for idx in range(260)], "open": [19.0 + idx for idx in range(260)]},
        index=pd.date_range("2025-01-01", periods=260, freq="D"),
    )
    provider = SimpleNamespace(
        cache_data={},
        cache_lock=nullcontext(),
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda api, code, count: full)
    monkeypatch.setattr("infra.market_data.local_history_provider.IndicatorService.calculate_indicators", lambda df: df)

    assert service.get_data_fresh_for_chart("000001") is full
    assert provider.cache_data["000001"] is full


def test_local_history_provider_returns_existing_on_fetch_exception(monkeypatch):
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    provider = SimpleNamespace(
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))

    assert service.get_data_fresh_for_chart("000001") is existing


def test_local_history_provider_fetch_standard_data_polars_unavailable(monkeypatch):
    class _Api:
        @staticmethod
        def get_security_bars(*_args):
            return [{"datetime": "2026-01-01 00:00", "close": 10.0, "vol": 100}]

    provider = SimpleNamespace(
        tdx_vipdoc="",
        _get_market_code=lambda code: 1,
        _apply_forward_adjustment=lambda api, market, code, df: df,
    )
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "polars":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    result = LocalHistoryProvider(provider, logger=_RecorderLog()).fetch_standard_data(_Api(), "600000", count=1)

    assert list(result["volume"]) == [100]


def test_local_history_provider_local_data_edge_paths():
    class _PolarsLikeFrame:
        columns = ["vol"]

        def __len__(self):
            return 260

        @staticmethod
        def rename(mapping):
            return {"renamed": mapping}

    class _EmptyApi:
        @staticmethod
        def get_security_bars(*_args):
            return []

    logger = _RecorderLog()
    frame = _PolarsLikeFrame()
    provider = SimpleNamespace(
        tdx_vipdoc="D:/HT/vipdoc",
        _get_market_code=lambda code: 0,
        _fetch_from_local_tdx=lambda code: frame,
        _apply_forward_adjustment=lambda api, market, code, df: frame,
    )
    assert LocalHistoryProvider(provider, logger=logger).fetch_standard_data(_EmptyApi(), "000001") == {
        "renamed": {"vol": "volume"}
    }

    provider._apply_forward_adjustment = lambda *_args: (_ for _ in ()).throw(RuntimeError("bad adjust"))
    assert LocalHistoryProvider(provider, logger=logger).fetch_standard_data(_EmptyApi(), "000001") is None

    provider._fetch_from_local_tdx = lambda code: pd.DataFrame({"close": [1.0]})
    assert LocalHistoryProvider(provider, logger=logger).fetch_standard_data(_EmptyApi(), "000001") is None

    provider._fetch_from_local_tdx = lambda code: None
    assert LocalHistoryProvider(provider, logger=logger).fetch_standard_data(_EmptyApi(), "000001") is None
    assert any(level == "error" for level, _message in logger.messages)


def test_local_history_provider_network_exception_paths():
    class _ValueErrorApi:
        @staticmethod
        def get_security_bars(*_args):
            raise ValueError("bad data")

    class _TimeoutApi:
        @staticmethod
        def get_security_bars(*_args):
            raise TimeoutError("timeout")

    provider = SimpleNamespace(
        tdx_vipdoc="",
        _get_market_code=lambda code: 1,
        _apply_forward_adjustment=lambda api, market, code, df: df,
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())

    with pytest.raises(ValueError):
        service.fetch_standard_data(_ValueErrorApi(), "600000")
    assert service.fetch_standard_data(_TimeoutApi(), "600000") is None


def test_local_history_provider_chart_refresh_handles_date_and_import_edges(monkeypatch):
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    bad_index = pd.DataFrame({"close": [1.0]}, index=["bad-date"])
    provider = SimpleNamespace(
        cache_data={},
        cache_lock=nullcontext(),
        get_data=lambda code: bad_index,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: True,
        server_pool=False,
    )
    assert LocalHistoryProvider(provider, logger=_RecorderLog()).get_data_fresh_for_chart("000001") is bad_index

    provider.get_data = lambda code: existing
    provider._is_after_1500_today = lambda: False
    provider.server_pool = True
    provider._get_thread_api = lambda: "api"
    new = pd.DataFrame(
        {"close": [260.0], "open": [259.0]},
        index=[existing.index.max() + pd.Timedelta(days=1)],
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda api, code, count: new)
    monkeypatch.setattr("infra.market_data.local_history_provider.IndicatorService.calculate_indicators", lambda df: df)

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "polars":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert len(service.get_data_fresh_for_chart("000001")) == 251


def test_local_history_provider_chart_refresh_accepts_polars_incremental(monkeypatch):
    pl = pytest.importorskip("polars")
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    new = pl.DataFrame(
        {
            "datetime": [existing.index.max() + pd.Timedelta(days=1)],
            "close": [260.0],
            "open": [259.0],
        }
    )
    provider = SimpleNamespace(
        cache_data={},
        cache_lock=nullcontext(),
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda api, code, count: new)
    monkeypatch.setattr("infra.market_data.local_history_provider.IndicatorService.calculate_indicators", lambda df: df)

    result = service.get_data_fresh_for_chart("000001")

    assert len(result) == 251
    assert provider.cache_data["000001"] is result


def test_local_history_provider_chart_refresh_returns_existing_on_data_exception(monkeypatch):
    existing = pd.DataFrame(
        {"close": [10.0 + idx for idx in range(250)], "open": [9.0 + idx for idx in range(250)]},
        index=pd.date_range("2025-01-01", periods=250, freq="D"),
    )
    provider = SimpleNamespace(
        get_data=lambda code: existing,
        _is_before_930_today=lambda: False,
        _is_after_1500_today=lambda: False,
        server_pool=True,
        _get_thread_api=lambda: "api",
    )
    service = LocalHistoryProvider(provider, logger=_RecorderLog())
    monkeypatch.setattr(service, "fetch_standard_data", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()))

    assert service.get_data_fresh_for_chart("000001") is existing


class _Runtime:
    def __init__(self, alive=True, stats=None):
        self._alive = alive
        self._stats = stats or {"last_success_at": 2.0, "reconnect_count": 3, "server": "s1"}
        self.closed = False

    def is_alive(self):
        return self._alive

    def snapshot(self):
        return dict(self._stats)

    def close(self):
        self.closed = True

    def request(self, params, timeout):
        self._stats["last_success_at"] = 5.0
        return [{"params": params, "timeout": timeout}]


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _runtime_provider(runtime=None):
    return SimpleNamespace(
        _rt_runtime=runtime,
        _rt_runtime_lock=_Lock(),
        _rt_runtime_last_success_at=0.0,
        _rt_runtime_reconnect_archived=0,
        _rt_runtime_cooldown_until=0.0,
        _rt_runtime_consecutive_failures=0,
        _rt_runtime_last_error="",
        _rt_runtime_cooldown_sec=30,
        _rt_runtime_failure_threshold=2,
        _rt_runtime_thread_threshold=5,
        _deprioritize_server=lambda server, reason: setattr(provider_ref, "deprioritized", (server, reason)),
    )


provider_ref = SimpleNamespace(deprioritized=None)


def test_realtime_quote_provider_runtime_lifecycle(monkeypatch):
    provider = _runtime_provider(_Runtime(alive=False))
    provider._deprioritize_server = lambda server, reason: setattr(provider_ref, "deprioritized", (server, reason))
    logger = _RecorderLog()
    created = []

    monkeypatch.setattr(
        "infra.market_data.realtime_quote_provider.RealtimeQuoteRuntime",
        lambda provider_arg, logger_arg: created.append(_Runtime(alive=True)) or created[-1],
    )

    service = RealtimeQuoteProvider(provider, logger=logger)
    runtime = service.ensure_runtime()

    assert runtime is created[0]
    assert provider._rt_runtime_reconnect_archived == 3

    service.reset_runtime("failed")
    assert runtime.closed is True
    assert provider._rt_runtime is None
    assert provider_ref.deprioritized == ("s1", "failed")
    assert provider._rt_runtime_last_error == "failed"


def test_realtime_quote_provider_failures_cooldown_and_stats(monkeypatch):
    provider = _runtime_provider(_Runtime(alive=True, stats={"inflight": 2, "last_success_at": 4, "reconnect_count": 1}))
    service = RealtimeQuoteProvider(provider, logger=_RecorderLog())

    quotes = service.submit_request([("000001", 0)], timeout_sec=1.5)
    assert quotes == [{"params": [("000001", 0)], "timeout": 1.5}]
    assert provider._rt_runtime_last_success_at == 5.0

    service.register_success()
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""

    assert service.protect_against_thread_anomaly(6, threshold=5) is True
    assert provider._rt_runtime_cooldown_until > 0

    provider._rt_runtime = None
    stats = service.get_runtime_stats()
    assert stats["cooldown_until"] == provider._rt_runtime_cooldown_until
    assert stats["last_error"]


def test_realtime_quote_provider_builds_intraday_frame(monkeypatch):
    hist = pd.DataFrame(
        {
            "open": [10.0 + idx for idx in range(10)],
            "high": [11.0 + idx for idx in range(10)],
            "low": [9.0 + idx for idx in range(10)],
            "close": [10.5 + idx for idx in range(10)],
            "volume": [100.0 for _idx in range(10)],
            "amount": [1000.0 for _idx in range(10)],
        },
        index=pd.date_range("2026-04-01", periods=10, freq="D"),
    )
    provider = SimpleNamespace(get_data=lambda code: hist)
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 10, 0)))
    monkeypatch.setattr(MarketCalendar, "is_market_active", staticmethod(lambda: True))
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": datetime(2026, 4, 20).date()))
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market="CN": None))
    monkeypatch.setattr("infra.market_data.realtime_quote_provider.IndicatorService.calculate_indicators", lambda df: df)

    result = RealtimeQuoteProvider(provider, logger=_RecorderLog()).build_realtime_df(
        "000001",
        {"open": 20.0, "high": 21.0, "low": 19.5, "close": 20.5, "volume": 120.0, "amount": 2400.0},
    )

    assert pd.Timestamp("2026-04-20") in result.index
    assert result.loc[pd.Timestamp("2026-04-20"), "volume"] > 120.0


def test_realtime_quote_provider_runtime_error_edges():
    class _BadSnapshotRuntime:
        closed = False

        @staticmethod
        def snapshot():
            raise RuntimeError("bad snapshot")

        def close(self):
            self.closed = True

    provider = _runtime_provider(_BadSnapshotRuntime())
    provider._rt_runtime_cooldown_until = 9999999999.0
    service = RealtimeQuoteProvider(provider, logger=_RecorderLog())

    service.archive_runtime(None)
    service.archive_runtime(_BadSnapshotRuntime())

    with pytest.raises(TimeoutError):
        service.ensure_runtime()

    provider._rt_runtime_cooldown_until = 0.0
    runtime = _BadSnapshotRuntime()
    provider._rt_runtime = runtime
    service.reset_runtime("reset")
    assert runtime.closed is True
    assert provider._rt_runtime_last_error == "reset"


def test_realtime_quote_provider_register_failure_paths():
    provider = _runtime_provider(None)
    provider._rt_runtime_failure_threshold = 2
    service = RealtimeQuoteProvider(provider, logger=_RecorderLog())

    assert service.protect_against_thread_anomaly(3, threshold=5) is False

    service.register_failure("first")
    assert provider._rt_runtime_consecutive_failures == 1
    assert provider._rt_runtime_last_error == "first"

    service.register_failure("second")
    assert provider._rt_runtime_consecutive_failures == 2
    assert provider._rt_runtime_cooldown_until > 0
    assert provider._rt_runtime_last_error == "second"


def test_realtime_quote_provider_build_realtime_df_rejects_missing_inputs():
    short_hist = pd.DataFrame({"close": [1.0]}, index=[pd.Timestamp("2026-04-01")])
    provider = SimpleNamespace(get_data=lambda code: short_hist)
    service = RealtimeQuoteProvider(provider, logger=_RecorderLog())

    assert service.build_realtime_df("000001", {"open": 1.0, "close": 1.0}) is None

    hist = pd.DataFrame(
        {"open": range(10), "high": range(10), "low": range(10), "close": range(10)},
        index=pd.date_range("2026-04-01", periods=10),
    )
    provider.get_data = lambda code: hist
    assert service.build_realtime_df("000001", {"open": 0, "close": 1.0}) is None
    assert service.build_realtime_df("000001", {"open": 1.0, "close": 0}) is None


def test_realtime_quote_provider_build_realtime_df_updates_existing_quote_date(monkeypatch):
    hist = pd.DataFrame(
        {
            "open": [10.0 + idx for idx in range(10)],
            "high": [11.0 + idx for idx in range(10)],
            "low": [9.0 + idx for idx in range(10)],
            "close": [10.5 + idx for idx in range(10)],
            "volume": [100.0 for _idx in range(10)],
            "amount": [1000.0 for _idx in range(10)],
        },
        index=pd.date_range("2026-04-01", periods=10, freq="D"),
    )
    provider = SimpleNamespace(get_data=lambda code: hist)
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 12, 0)))
    monkeypatch.setattr(MarketCalendar, "is_market_active", staticmethod(lambda: True))
    monkeypatch.setattr("infra.market_data.realtime_quote_provider.IndicatorService.calculate_indicators", lambda df: df)

    result = RealtimeQuoteProvider(provider, logger=_RecorderLog()).build_realtime_df(
        "000001",
        {
            "date": "2026-04-10",
            "open": 30.0,
            "high": 31.0,
            "low": 29.0,
            "close": 30.5,
            "volume": 200.0,
            "amount": 5000.0,
        },
    )

    assert result.loc[pd.Timestamp("2026-04-10"), "close"] == 30.5


def test_realtime_quote_provider_build_realtime_df_trade_date_fallbacks(monkeypatch):
    hist = pd.DataFrame(
        {
            "open": [10.0 + idx for idx in range(10)],
            "high": [11.0 + idx for idx in range(10)],
            "low": [9.0 + idx for idx in range(10)],
            "close": [10.5 + idx for idx in range(10)],
            "volume": [100.0 for _idx in range(10)],
            "amount": [1000.0 for _idx in range(10)],
        },
        index=pd.date_range("2026-04-01", periods=10, freq="D"),
    )
    provider = SimpleNamespace(get_data=lambda code: hist)
    monkeypatch.setattr("infra.market_data.realtime_quote_provider.IndicatorService.calculate_indicators", lambda df: df)

    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market="CN": datetime(2026, 4, 20).date()))
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 13, 30)))
    monkeypatch.setattr(MarketCalendar, "is_market_active", staticmethod(lambda: True))
    result = RealtimeQuoteProvider(provider, logger=_RecorderLog()).build_realtime_df(
        "000001",
        {"open": 20.0, "high": 21.0, "low": 19.0, "close": 20.5, "volume": 200.0, "amount": 5000.0},
    )
    assert pd.Timestamp("2026-04-20") in result.index

    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market="CN": (_ for _ in ()).throw(RuntimeError())))
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": datetime(2026, 4, 21).date()))
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 21, 15, 0)))
    result = RealtimeQuoteProvider(provider, logger=_RecorderLog()).build_realtime_df(
        "000001",
        {"open": 22.0, "high": 23.0, "low": 21.0, "close": 22.5, "volume": 200.0, "amount": 5000.0},
    )
    assert pd.Timestamp("2026-04-21") in result.index


def test_external_terminal_navigator_normalizes_prefix_and_web_fallback(monkeypatch):
    opened = []
    emitted = []

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )

    assert ExternalTerminalNavigator._normalize_quote_code("sh.600000") == "600000"
    assert ExternalTerminalNavigator._detect_quote_prefix("600000") == "SH"
    assert ExternalTerminalNavigator._detect_quote_prefix("830000") == "BJ"
    assert ExternalTerminalNavigator._detect_quote_prefix("000001") == "SZ"

    ExternalTerminalNavigator(SimpleNamespace())._open_quote_web_fallback("600000", "missing")

    assert opened == ["https://quote.eastmoney.com/SH600000.html"]
    assert emitted and emitted[0][0] == "warn"


def test_external_terminal_navigator_input_quote_code_empty_and_type_fallback(monkeypatch):
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    monkeypatch.setattr(navigator, "_activate_window", lambda user32, hwnd: None)
    monkeypatch.setattr(navigator, "_try_fill_input_control", lambda hwnd, bare, app_name: False)
    monkeypatch.setattr(ExternalTerminalNavigator, "_type_quote_code", staticmethod(lambda bare, app_name: True))

    assert navigator._input_quote_code("user32", "hwnd", "", "APP") is False
    assert navigator._input_quote_code("user32", "hwnd", "sh600000", "APP") is True
    assert emitted[0][0] == "warn"


def test_external_terminal_navigator_finds_and_fills_input_controls(monkeypatch):
    class _Win32Gui:
        visible = {1: True, 2: True, 3: True}
        enabled = {1: True, 2: True, 3: True}
        class_names = {1: "Static", 2: "Edit", 3: "ComboBox"}
        rects = {1: (0, 0, 200, 30), 2: (0, 20, 100, 45), 3: (0, 10, 120, 35)}
        texts = {2: "wrong", 3: "600000"}
        sent = []
        posted = []

        @staticmethod
        def EnumChildWindows(hwnd, callback, arg):
            for child in (1, 2, 3):
                callback(child, arg)

        @classmethod
        def IsWindowVisible(cls, hwnd):
            return cls.visible[hwnd]

        @classmethod
        def IsWindowEnabled(cls, hwnd):
            return cls.enabled[hwnd]

        @classmethod
        def GetClassName(cls, hwnd):
            return cls.class_names[hwnd]

        @classmethod
        def GetWindowRect(cls, hwnd):
            return cls.rects[hwnd]

        @classmethod
        def SendMessage(cls, hwnd, msg, wparam, text):
            cls.sent.append((hwnd, msg, text))

        @classmethod
        def GetWindowText(cls, hwnd):
            return cls.texts.get(hwnd, "")

        @classmethod
        def PostMessage(cls, hwnd, msg, wparam, lparam):
            cls.posted.append((hwnd, msg, wparam))

    monkeypatch.setitem(sys.modules, "win32gui", _Win32Gui)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(WM_SETTEXT=1, WM_KEYDOWN=2, WM_KEYUP=3, VK_RETURN=13))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda *_args: None),
    )

    candidates = ExternalTerminalNavigator._find_input_controls("parent")
    assert [item[0] for item in candidates] == [3, 2]

    ok = ExternalTerminalNavigator(SimpleNamespace())._try_fill_input_control("parent", "600000", "APP")

    assert ok is True
    assert _Win32Gui.sent[0][0] == 3
    assert _Win32Gui.posted


def test_external_terminal_navigator_activate_window_and_type_quote_code(monkeypatch):
    calls = []

    class _User32:
        def __init__(self, iconic):
            self.iconic = iconic

        def IsIconic(self, hwnd):
            return self.iconic

        def ShowWindow(self, hwnd, mode):
            calls.append(("show", mode))

        def SetForegroundWindow(self, hwnd):
            calls.append(("foreground", hwnd))

    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(BringWindowToTop=lambda hwnd: calls.append(("top", hwnd))))
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.time.sleep", lambda _seconds: None)

    ExternalTerminalNavigator._activate_window(_User32(iconic=True), "hwnd")
    ExternalTerminalNavigator._activate_window(_User32(iconic=False), "hwnd")

    assert ("show", 9) in calls
    assert ("show", 5) in calls

    pyautogui_calls = []
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(
            press=lambda key, **kwargs: pyautogui_calls.append(("press", key, kwargs)),
            write=lambda text, **kwargs: pyautogui_calls.append(("write", text, kwargs)),
        ),
    )
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda *_args: None),
    )

    assert ExternalTerminalNavigator._type_quote_code("600000", "APP") is True
    assert ("write", "600000", {"interval": 0.04}) in pyautogui_calls


def test_external_terminal_navigator_thread_entrypoints_run_targets(monkeypatch):
    calls = []

    class _Thread:
        def __init__(self, target, args=(), daemon=False):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            calls.append(("start", self.daemon, self.args))
            self.target(*self.args)

    navigator = ExternalTerminalNavigator(SimpleNamespace())
    monkeypatch.setattr("threading.Thread", _Thread)
    monkeypatch.setattr(navigator, "_launch_tdx_impl", lambda code: calls.append(("tdx", code)))
    monkeypatch.setattr(navigator, "_launch_eastmoney_impl", lambda code: calls.append(("eastmoney", code)))

    navigator.launch_tdx("600000")
    navigator.launch_eastmoney("000001")

    assert calls == [
        ("start", True, ("600000",)),
        ("tdx", "600000"),
        ("start", True, ("000001",)),
        ("eastmoney", "000001"),
    ]


def test_external_terminal_navigator_web_fallback_empty_and_open_error(monkeypatch):
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.webbrowser.open",
        lambda _url: (_ for _ in ()).throw(RuntimeError("browser unavailable")),
    )

    navigator._open_quote_web_fallback("")
    navigator._open_quote_web_fallback("000001")

    assert emitted == [("error", emitted[0][1])]


def test_external_terminal_navigator_window_helpers_cover_error_branches(monkeypatch):
    calls = []

    class _User32:
        def IsIconic(self, hwnd):
            return False

        def ShowWindow(self, hwnd, mode):
            calls.append(("show", mode))

        def SetForegroundWindow(self, hwnd):
            calls.append(("foreground", hwnd))

    class _Win32Gui:
        @staticmethod
        def BringWindowToTop(hwnd):
            raise OSError("not allowed")

    monkeypatch.setitem(sys.modules, "win32gui", _Win32Gui)
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.time.sleep", lambda _seconds: None)

    ExternalTerminalNavigator._activate_window(_User32(), "hwnd")

    assert calls == [("show", 5), ("foreground", "hwnd")]

    class _FilteringWin32Gui:
        @staticmethod
        def EnumChildWindows(hwnd, callback, arg):
            for child in (1, 2, 3, 4, 5):
                callback(child, arg)

        @staticmethod
        def IsWindowVisible(hwnd):
            return hwnd != 1

        @staticmethod
        def IsWindowEnabled(hwnd):
            return hwnd != 2

        @staticmethod
        def GetClassName(hwnd):
            if hwnd == 4:
                raise RuntimeError("class lookup failed")
            return "Edit"

        @staticmethod
        def GetWindowRect(hwnd):
            if hwnd == 3:
                return (0, 0, 40, 10)
            return (0, hwnd * 10, 120, hwnd * 10 + 30)

    monkeypatch.setitem(sys.modules, "win32gui", _FilteringWin32Gui)

    assert ExternalTerminalNavigator._find_input_controls("parent") == [(5, "Edit", 50, 120)]


def test_external_terminal_navigator_fill_input_failure_paths(monkeypatch):
    emitted = []

    class _Win32Gui:
        texts = {1: "other", 2: ""}

        @staticmethod
        def SendMessage(hwnd, msg, wparam, text):
            if hwnd == 2:
                raise OSError("send failed")

        @classmethod
        def GetWindowText(cls, hwnd):
            return cls.texts[hwnd]

        @staticmethod
        def PostMessage(hwnd, msg, wparam, lparam):
            raise AssertionError("return should not be posted")

    monkeypatch.setitem(sys.modules, "win32gui", _Win32Gui)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(WM_SETTEXT=1, WM_KEYDOWN=2, WM_KEYUP=3, VK_RETURN=13))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    monkeypatch.setattr(
        ExternalTerminalNavigator,
        "_find_input_controls",
        staticmethod(lambda hwnd: [(1, "Edit", 0, 100), (2, "Edit", 1, 100)]),
    )

    assert ExternalTerminalNavigator(SimpleNamespace())._try_fill_input_control("parent", "600000", "APP") is False
    assert emitted == []


def test_external_terminal_navigator_input_control_success_and_typing_failure(monkeypatch):
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    monkeypatch.setattr(navigator, "_activate_window", lambda user32, hwnd: None)
    monkeypatch.setattr(navigator, "_try_fill_input_control", lambda hwnd, bare, app_name: True)

    assert navigator._input_quote_code("user32", "hwnd", "600000", "APP") is True

    monkeypatch.setattr(navigator, "_try_fill_input_control", lambda hwnd, bare, app_name: False)
    monkeypatch.setattr(
        ExternalTerminalNavigator,
        "_type_quote_code",
        staticmethod(lambda bare, app_name: (_ for _ in ()).throw(RuntimeError("keyboard blocked"))),
    )

    assert navigator._input_quote_code("user32", "hwnd", "600000", "APP") is False
    assert emitted == [("warn", emitted[0][1])]


class _FakeWinBuffer:
    def __init__(self):
        self.value = ""


class _FakeUser32:
    def __init__(self, hwnds, titles=None, classes=None, visible=None):
        self.hwnds = list(hwnds)
        self.titles = titles or {}
        self.classes = classes or {}
        self.visible = visible or {}
        self.enum_calls = 0

    def EnumWindows(self, callback, arg):
        self.enum_calls += 1
        for hwnd in self.hwnds:
            if callback(hwnd, arg) is False:
                break

    def IsWindowVisible(self, hwnd):
        return self.visible.get(hwnd, True)

    def GetWindowTextLengthW(self, hwnd):
        return len(self.titles.get(hwnd, ""))

    def GetWindowTextW(self, hwnd, buf, length):
        buf.value = self.titles.get(hwnd, "")

    def GetClassNameW(self, hwnd, buf, length):
        buf.value = self.classes.get(hwnd, "")


def _install_fake_external_nav_ctypes(monkeypatch, user32):
    import ctypes

    monkeypatch.delattr(ctypes, "wintypes", raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *_types: (lambda fn: fn), raising=False)
    monkeypatch.setattr(ctypes, "create_unicode_buffer", lambda _size: _FakeWinBuffer())
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)


def test_external_terminal_navigator_lazily_loads_wintypes(monkeypatch):
    import ctypes

    monkeypatch.delattr(ctypes, "wintypes", raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *_types: (lambda fn: fn), raising=False)

    enum_windows_proc = ExternalTerminalNavigator._enum_windows_proc_type(ctypes)

    assert enum_windows_proc(lambda _hwnd, _arg: True)(1, 0) is True
    assert not ExternalTerminalNavigator._null_hwnd(ctypes)


def test_external_terminal_navigator_tdx_missing_path_and_exception_fallbacks(monkeypatch):
    fallbacks = []
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace(data_provider=SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")))

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.os.path.exists", lambda _path: False)
    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )

    navigator._launch_tdx_impl("600000")
    assert fallbacks == [("600000", fallbacks[0][1])]
    assert emitted and emitted[0][0] == "warn"

    import ctypes

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.os.path.exists", lambda _path: True)
    monkeypatch.setattr(
        ctypes,
        "WINFUNCTYPE",
        lambda *_types: (_ for _ in ()).throw(RuntimeError("ctypes broken")),
        raising=False,
    )

    navigator._launch_tdx_impl("000001")

    assert fallbacks[-1][0] == "000001"


def test_external_terminal_navigator_tdx_existing_and_unfound_window_paths(monkeypatch):
    inputs = []
    fallbacks = []
    spawned = []
    navigator = ExternalTerminalNavigator(SimpleNamespace(data_provider=SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")))

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.os.path.exists", lambda _path: True)
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.spawn_process", lambda command: spawned.append(command))
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(navigator, "_input_quote_code", lambda user32, hwnd, code, app: inputs.append((hwnd, code, app)) or True)
    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    _install_fake_external_nav_ctypes(
        monkeypatch,
        _FakeUser32([10], titles={10: "terminal"}, classes={10: "TdxW_MainFrame_Class"}),
    )

    navigator._launch_tdx_impl("600000")

    assert inputs == [(10, "600000", "TDX")]
    assert spawned == []
    assert fallbacks == []

    inputs.clear()
    _install_fake_external_nav_ctypes(monkeypatch, _FakeUser32([]))

    navigator._launch_tdx_impl("000001")

    assert spawned == [["D:/HT/tdxw.exe"]]
    assert inputs == []
    assert fallbacks[-1][0] == "000001"


def test_external_terminal_navigator_tdx_ignores_windows_until_spawned_window_appears(monkeypatch):
    inputs = []
    user32 = _FakeUser32(
        [1, 2, 3],
        titles={1: "hidden", 2: "", 3: "other"},
        classes={1: "TdxW_MainFrame_Class", 2: "TdxW_MainFrame_Class", 3: "OtherClass"},
        visible={1: False},
    )
    navigator = ExternalTerminalNavigator(SimpleNamespace(data_provider=SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")))

    def fake_sleep(_seconds):
        user32.hwnds = [4]
        user32.titles[4] = "terminal"
        user32.classes[4] = "TdxW_MainFrame_Class"

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.os.path.exists", lambda _path: True)
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.spawn_process", lambda _command: None)
    monkeypatch.setattr("infra.navigation.external_terminal_navigator.time.sleep", fake_sleep)
    monkeypatch.setattr(navigator, "_input_quote_code", lambda user32_arg, hwnd, code, app: inputs.append((hwnd, code, app)) or True)
    _install_fake_external_nav_ctypes(monkeypatch, user32)

    navigator._launch_tdx_impl("600000")

    assert inputs == [(4, "600000", "TDX")]


def test_external_terminal_navigator_tdx_input_failure_uses_web_fallback(monkeypatch):
    fallbacks = []
    navigator = ExternalTerminalNavigator(SimpleNamespace(data_provider=SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")))

    monkeypatch.setattr("infra.navigation.external_terminal_navigator.os.path.exists", lambda _path: True)
    monkeypatch.setattr(navigator, "_input_quote_code", lambda user32, hwnd, code, app: False)
    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    _install_fake_external_nav_ctypes(
        monkeypatch,
        _FakeUser32([10], titles={10: "terminal"}, classes={10: "TdxW_MainFrame_Class"}),
    )

    navigator._launch_tdx_impl("600000")

    assert fallbacks == [("600000", fallbacks[0][1])]


def test_external_terminal_navigator_eastmoney_window_paths(monkeypatch):
    inputs = []
    fallbacks = []
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(navigator, "_input_quote_code", lambda user32, hwnd, code, app: inputs.append((hwnd, code, app)) or True)
    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    _install_fake_external_nav_ctypes(monkeypatch, _FakeUser32([20], titles={20: "东方财富"}))

    navigator._launch_eastmoney_impl("000001")

    assert inputs == [(20, "000001", "东方财富")]
    assert fallbacks == []

    inputs.clear()
    _install_fake_external_nav_ctypes(monkeypatch, _FakeUser32([]))

    navigator._launch_eastmoney_impl("600000")

    assert inputs == []
    assert fallbacks[-1][0] == "600000"
    assert emitted[-1][0] == "warn"


def test_external_terminal_navigator_eastmoney_ignores_non_matching_windows(monkeypatch):
    fallbacks = []
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    _install_fake_external_nav_ctypes(
        monkeypatch,
        _FakeUser32([1, 2, 3], titles={1: "hidden", 2: "", 3: "other"}, visible={1: False}),
    )

    navigator._launch_eastmoney_impl("000001")

    assert fallbacks == [("000001", fallbacks[0][1])]
    assert emitted == [("warn", emitted[0][1])]


def test_external_terminal_navigator_eastmoney_input_failure_and_exception(monkeypatch):
    fallbacks = []
    emitted = []
    navigator = ExternalTerminalNavigator(SimpleNamespace())

    monkeypatch.setattr(navigator, "_input_quote_code", lambda user32, hwnd, code, app: False)
    monkeypatch.setattr(navigator, "_open_quote_web_fallback", lambda code, reason="": fallbacks.append((code, reason)))
    monkeypatch.setattr(
        "infra.navigation.external_terminal_navigator.event_bus.sig_system_log",
        SimpleNamespace(emit=lambda level, message: emitted.append((level, message))),
    )
    _install_fake_external_nav_ctypes(monkeypatch, _FakeUser32([20], titles={20: "东方财富"}))

    navigator._launch_eastmoney_impl("000001")

    assert fallbacks == [("000001", fallbacks[0][1])]

    import ctypes

    monkeypatch.setattr(
        ctypes,
        "WINFUNCTYPE",
        lambda *_types: (_ for _ in ()).throw(RuntimeError("ctypes broken")),
        raising=False,
    )

    navigator._launch_eastmoney_impl("600000")

    assert fallbacks[-1][0] == "600000"
    assert emitted[-1][0] == "error"
