import threading
from datetime import date
from pathlib import Path

import pandas as pd

from core.runtime_paths import MARKET_SYNC_WORKERS
from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin, _resolve_market_sync_workers


class _DummyProvider(TdxDataProviderHistoryMixin):
    def __init__(self, local_df=None):
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.tdx_vipdoc = "D:\\HT\\vipdoc"
        self._local_df = local_df
        self.fetch_calls = 0

    def _fetch_from_local_tdx(self, code):
        self.fetch_calls += 1
        return self._local_df


def test_get_data_falls_back_to_local_tdx_when_runtime_cache_is_empty():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5],
            "high": [10.8, 10.9],
            "low": [9.9, 10.2],
            "close": [10.6, 10.7],
            "amount": [1000.0, 1100.0],
            "volume": [100, 120],
        },
        index=pd.to_datetime(["2026-04-15", "2026-04-16"]),
    )

    provider = _DummyProvider(local_df=df)

    result = provider.get_data("000001")

    assert result is df
    assert provider.fetch_calls == 1
    assert provider.cache_data["000001"] is df


def test_get_data_prefers_existing_runtime_cache_without_local_reload():
    cached = pd.DataFrame(
        {
            "open": [20.0],
            "high": [21.0],
            "low": [19.5],
            "close": [20.8],
            "amount": [2200.0],
            "volume": [180],
        },
        index=pd.to_datetime(["2026-04-16"]),
    )

    provider = _DummyProvider(local_df=None)
    provider.cache_data["600000"] = cached

    result = provider.get_data("600000")

    assert result is cached
    assert provider.fetch_calls == 0


def test_resolve_market_sync_workers_clamps_explicit_f5_budget():
    assert _resolve_market_sync_workers(offline=True, requested_max_workers=6) == 6
    assert _resolve_market_sync_workers(offline=True, requested_max_workers=99) == 20
    assert _resolve_market_sync_workers(offline=True, requested_max_workers=0) == 20
    assert _resolve_market_sync_workers(offline=False, requested_max_workers=99) == MARKET_SYNC_WORKERS


def test_sync_market_data_refreshes_stale_nonempty_runtime_cache(monkeypatch):
    from core.market_calendar import MarketCalendar
    from vcp import data_provider_history_mixin as history_mixin
    from vcp import polars_engine

    cached = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-07-09"]))
    refreshed = pd.DataFrame({"close": [11.0]}, index=pd.to_datetime(["2026-07-10"]))
    provider = _DummyProvider()
    provider.cache_data["000001"] = cached
    provider._offline = True
    provider._is_before_930_today = lambda: False
    provider._is_trading_day = lambda: True
    provider._downcast_memory = lambda: None
    provider.legacy_cache_file = ""
    provider.legacy_fallback_cache_file = ""
    worker_calls = []
    provider._worker_fetch = (
        lambda code, force_refresh, existing: worker_calls.append((code, force_refresh, existing))
        or (code, refreshed, "OK")
    )
    info_logs = []
    error_logs = []
    monkeypatch.setattr(history_mixin._log, "info", info_logs.append)
    monkeypatch.setattr(history_mixin._log, "error", error_logs.append)

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )
    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda cache_data, trade_date: True)
    monkeypatch.setattr(history_mixin, "remove_cache_file", lambda _path: None)

    assert provider.sync_market_data(["000001"], max_workers=1) is True
    assert [(code, force_refresh) for code, force_refresh, _existing in worker_calls] == [("000001", False)]
    assert worker_calls[0][2] is cached
    assert provider.cache_data["000001"] is refreshed
    assert provider._market_data_snapshot_trade_date == "20260710"
    assert any("[缓存] 阶段1完成" in message for message in info_logs)
    assert not any("[缓存] 阶段1完成" in message for message in error_logs)


def test_sync_market_data_can_skip_disk_bootstrap_for_isolated_f5_full_reread(monkeypatch):
    from core.market_calendar import MarketCalendar
    from vcp import data_provider_history_mixin as history_mixin
    from vcp import polars_engine

    provider = _DummyProvider()
    provider._offline = True
    provider._is_before_930_today = lambda: False
    provider._is_trading_day = lambda: True
    provider._downcast_memory = lambda: None
    provider.legacy_cache_file = ""
    provider.legacy_fallback_cache_file = ""
    loaded = []
    provider.load_cache_from_disk = lambda: loaded.append(True) or provider.cache_data.update({"old": object()})
    provider._worker_fetch = lambda code, force, existing: (code, pd.DataFrame({"close": [1.0]}), "OK")

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )
    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda *_args: True)
    monkeypatch.setattr(history_mixin, "remove_cache_file", lambda _path: None)

    assert provider.sync_market_data(
        ["000001"],
        force_refresh=True,
        max_workers=1,
        load_cached_snapshot_if_empty=False,
    ) is True
    assert loaded == []
    assert set(provider.cache_data) == {"000001"}


def test_sync_market_data_checks_every_requested_code_instead_of_global_latest(monkeypatch):
    from core.market_calendar import MarketCalendar
    from vcp import data_provider_history_mixin as history_mixin
    from vcp import polars_engine

    stale = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-07-09"]))
    current = pd.DataFrame({"close": [20.0]}, index=pd.to_datetime(["2026-07-10"]))
    provider = _DummyProvider()
    provider.cache_data.update(
        {
            "000001": stale,
            "000002": current,
            "600000": current,  # 无关代码已是最新，不能掩盖请求集合内的旧数据。
        }
    )
    provider._offline = True
    provider._is_before_930_today = lambda: False
    provider._is_trading_day = lambda: True
    provider._downcast_memory = lambda: None
    provider.legacy_cache_file = ""
    provider.legacy_fallback_cache_file = ""
    worker_calls = []
    provider._worker_fetch = (
        lambda code, force_refresh, existing: worker_calls.append((code, force_refresh, existing))
        or (code, current, "OK")
    )

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )
    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda cache_data, trade_date: True)
    monkeypatch.setattr(history_mixin, "remove_cache_file", lambda _path: None)

    assert provider.sync_market_data(["000001", "000002"], max_workers=1) is True
    assert {code for code, _force_refresh, _existing in worker_calls} == {"000001", "000002"}


def test_sync_market_data_trusts_current_snapshot_for_covered_suspended_code(monkeypatch):
    from core.market_calendar import MarketCalendar

    provider = _DummyProvider()
    provider.cache_data.update(
        {
            "000001": pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-07-10"])),
            "600000": pd.DataFrame({"close": [8.0]}, index=pd.to_datetime(["2026-06-30"])),
        }
    )
    provider._market_data_snapshot_trade_date = "20260710"
    provider._worker_fetch = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fetch"))

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )

    assert provider.sync_market_data(["000001", "600000"], max_workers=1) is True


def test_sync_market_data_refreshes_when_current_snapshot_misses_requested_code(monkeypatch):
    from core.market_calendar import MarketCalendar
    from vcp import data_provider_history_mixin as history_mixin
    from vcp import polars_engine

    current = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-07-10"]))
    provider = _DummyProvider()
    provider.cache_data["000001"] = current
    provider._market_data_snapshot_trade_date = "20260710"
    provider._offline = True
    provider._is_before_930_today = lambda: False
    provider._is_trading_day = lambda: True
    provider._downcast_memory = lambda: None
    provider.legacy_cache_file = ""
    provider.legacy_fallback_cache_file = ""
    worker_calls = []
    provider._worker_fetch = (
        lambda code, force_refresh, existing: worker_calls.append((code, force_refresh, existing))
        or (code, current, "OK")
    )

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )
    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda cache_data, trade_date: True)
    monkeypatch.setattr(history_mixin, "remove_cache_file", lambda _path: None)

    assert provider.sync_market_data(["000001", "600000"], max_workers=1) is True
    assert "600000" in {code for code, _force_refresh, _existing in worker_calls}


def test_sync_market_data_before_open_rejects_future_metadata_and_overly_stale_cache(monkeypatch):
    from core.market_calendar import MarketCalendar
    from vcp import data_provider_history_mixin as history_mixin
    from vcp import polars_engine

    stale = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-07-08"]))
    refreshed = pd.DataFrame({"close": [11.0]}, index=pd.to_datetime(["2026-07-10"]))
    provider = _DummyProvider()
    provider.cache_data["000001"] = stale
    provider._market_data_snapshot_trade_date = "20260711"
    provider._offline = True
    provider._is_before_930_today = lambda: True
    provider._is_trading_day = lambda: True
    provider._downcast_memory = lambda: None
    provider.legacy_cache_file = ""
    provider.legacy_fallback_cache_file = ""
    worker_calls = []
    provider._worker_fetch = (
        lambda code, force_refresh, existing: worker_calls.append((code, force_refresh, existing))
        or (code, refreshed, "OK")
    )

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 10)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(
            lambda cls, market="CN", ref_date=None: date(2026, 7, 9)
            if ref_date == date(2026, 7, 9)
            else date(2026, 7, 10)
        ),
    )
    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda cache_data, trade_date: True)
    monkeypatch.setattr(history_mixin, "remove_cache_file", lambda _path: None)

    assert provider.sync_market_data(["000001"], max_workers=1) is True
    assert [code for code, _force_refresh, _existing in worker_calls] == ["000001"]


def test_load_cache_from_disk_records_trusted_snapshot_trade_date(monkeypatch):
    from vcp import data_provider_history_mixin as history_mixin

    provider = _DummyProvider()

    def _load(provider_arg, *, logger):
        del logger
        provider_arg.cache_data = {
            "600000": pd.DataFrame({"close": [8.0]}, index=pd.to_datetime(["2026-06-30"]))
        }
        return "2026-07-10"

    monkeypatch.setattr(history_mixin, "load_cache_from_disk", _load)

    assert provider.load_cache_from_disk() == "2026-07-10"
    assert provider._market_data_snapshot_trade_date == "20260710"


def test_sync_market_data_accepts_latest_trading_day_cache_on_weekend(monkeypatch):
    from core.market_calendar import MarketCalendar

    provider = _DummyProvider()
    provider.cache_data["000001"] = pd.DataFrame(
        {"close": [10.0]},
        index=pd.to_datetime(["2026-07-10"]),
    )
    provider._worker_fetch = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fetch"))

    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 7, 12)))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: date(2026, 7, 10)),
    )

    assert provider.sync_market_data(["000001"], max_workers=1) is True


def _build_tnf_record(code: str, name: str) -> bytes:
    record = bytearray(TdxDataProviderHistoryMixin._TNF_RECORD_SIZE)
    record[19:23] = b"FZDM"
    record[TdxDataProviderHistoryMixin._TNF_CODE_OFFSET : TdxDataProviderHistoryMixin._TNF_CODE_OFFSET + 6] = (
        code.encode("ascii")
    )
    name_bytes = name.encode("gbk")
    start = TdxDataProviderHistoryMixin._TNF_NAME_OFFSET
    record[start : start + len(name_bytes)] = name_bytes
    return bytes(record)


def test_parse_tnf_name_file_extracts_a_share_names(tmp_path):
    tnf_path = Path(tmp_path) / "szs.tnf"
    tnf_path.write_bytes(
        b"".join(
            [
                _build_tnf_record("300093", "*ST金刚"),
                _build_tnf_record("300236", "上海新阳"),
                _build_tnf_record("002709", "天赐材料"),
            ]
        )
    )

    parsed = TdxDataProviderHistoryMixin._parse_tnf_name_file(str(tnf_path))

    assert parsed["300093"] == "*ST金刚"
    assert parsed["300236"] == "上海新阳"
    assert parsed["002709"] == "天赐材料"
