# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app.bootstrap as bootstrap_package
import app.services as services_package
from app.services import asian_market_cache_service as asian_cache
from app.services import earnings_cache_query_service as earnings_cache
from app.services import f5_snapshot_service as f5_snapshot
from app.services import stock_context_anchor_service as anchor_service
from app.services import stock_context_snapshot_service as stock_snapshot
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError


def test_asian_mapping_and_ticker_index_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(asian_cache, "read_json_cache", lambda _path, default: ["invalid"])
    assert asian_cache.read_mapping_cache("cache.json") == {}

    monkeypatch.setattr(asian_cache, "read_json_cache", lambda _path, default: {"ok": True})
    assert asian_cache.read_mapping_cache("cache.json") == {"ok": True}
    assert asian_cache._cache_signature(str(tmp_path / "missing.json")) is None
    assert asian_cache.load_cached_asian_stock(str(tmp_path / "missing.json"), "2330.tw") is None

    cache_path = tmp_path / "asian.json"
    cache_path.write_text("{}", encoding="utf-8")
    signature = asian_cache._cache_signature(str(cache_path))
    assert signature is not None

    asian_cache.clear_asian_ticker_index_cache()
    monkeypatch.setattr(asian_cache, "read_json_cache", lambda _path, default: {"stocks": "invalid"})
    assert asian_cache._load_asian_ticker_index(signature) == {}

    asian_cache.clear_asian_ticker_index_cache()
    monkeypatch.setattr(
        asian_cache,
        "read_json_cache",
        lambda _path, default: {
            "stocks": [None, {"ticker": ""}, {"ticker": " 2330.tw ", "klines": [{"close": 100}]}]
        },
    )
    indexed = asian_cache._load_asian_ticker_index(signature)
    assert set(indexed) == {"2330.TW"}


def test_asian_quote_serialization_and_latest_market_dates(monkeypatch, tmp_path):
    writes = []
    monkeypatch.setattr(asian_cache, "write_json_cache", lambda path, payload: writes.append((path, payload)))

    asian_cache.write_realtime_quote_cache(
        {
            "2330.TW": {
                "date": "2026-07-16",
                "close": 100,
                "pct": "bad",
                "pct_5": "1.236%",
                "pct_10": None,
                "pct_20": -2.345,
                "source": "cache",
            }
        },
        path="realtime.json",
    )

    quote = writes[0][1]["2330.TW"]
    assert writes[0][0] == "realtime.json"
    assert quote["pct"] == 0.0
    assert quote["pct_5"] == 1.24
    assert quote["pct_10"] == 0.0
    assert quote["pct_20"] == -2.35

    assert asian_cache._latest_trade_date_item(None) is None
    assert asian_cache._latest_trade_date_item({"ticker": "2330", "klines": []}) is None
    assert asian_cache._latest_trade_date_item({"ticker": "BAD.TW", "klines": [{"date": "bad"}]}) is None

    cache_path = tmp_path / "asian-klines.json"
    cache_path.write_text(
        json.dumps(
            {
                "stocks": [
                    {"ticker": "2330.TW", "klines": [{"date": "2026-07-15T00:00:00"}]},
                    {"ticker": "2317.TW", "klines": [{"date": "2026-07-16"}]},
                    {"ticker": "BAD.TW", "klines": [{"date": "invalid"}]},
                    {"ticker": "EMPTY.TW", "klines": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    asian_cache.clear_asian_ticker_index_cache()
    monkeypatch.setattr(
        asian_cache.MarketCalendar,
        "normalize_market",
        classmethod(lambda _cls, market: str(market)),
    )

    assert asian_cache.load_latest_trade_dates(str(cache_path)) == {"TW": dt.date(2026, 7, 16)}

    first = asian_cache.load_cached_asian_stock(str(cache_path), "2330.tw")
    first["klines"][0]["date"] = "mutated"
    assert asian_cache.load_cached_asian_stock(str(cache_path), "2330.TW")["klines"][0]["date"] == (
        "2026-07-15T00:00:00"
    )


def test_stock_context_snapshot_reads_success_and_failure_paths(monkeypatch):
    monkeypatch.setattr(stock_snapshot, "coerce_cache_rows", lambda rows: list(rows or []))
    monkeypatch.setattr(
        stock_snapshot.ai_pool_module,
        "load_cached_ai_industry_chain_rows",
        lambda: [{"代码": "000001"}],
    )
    assert stock_snapshot.load_ai_chain_cache_rows() == [{"代码": "000001"}]

    monkeypatch.setattr(
        stock_snapshot.ai_pool_module,
        "load_cached_ai_industry_chain_rows",
        lambda: (_ for _ in ()).throw(OSError("cache unavailable")),
    )
    assert stock_snapshot.load_ai_chain_cache_rows() == []

    class _LhbManager:
        def compute_pool(self, *, data_provider, engine):
            assert data_provider is None
            assert engine == "engine"
            return [{"代码": "600001"}]

    monkeypatch.setattr(stock_snapshot.lhb_pool_module, "LhbPoolManager", _LhbManager)
    assert stock_snapshot.load_lhb_pool_rows(engine="engine") == [{"代码": "600001"}]

    class _BrokenLhbManager:
        def compute_pool(self, **_kwargs):
            raise RuntimeError("pool unavailable")

    monkeypatch.setattr(stock_snapshot.lhb_pool_module, "LhbPoolManager", _BrokenLhbManager)
    assert stock_snapshot.load_lhb_pool_rows() == []


def test_fund_holding_snapshot_is_atomic_across_store_queries():
    class _Store:
        @staticmethod
        def get_latest_quarter_map():
            return {"QFII": "2026Q1"}

        @staticmethod
        def query_change_rows(*, stock_codes):
            assert stock_codes == ["000001"]
            return [{"代码": "000001", "变化类型": "新进"}]

    assert stock_snapshot.load_fund_holding_snapshot(stock_codes=["000001"], store=_Store()) == (
        {"QFII": "2026Q1"},
        [{"代码": "000001", "变化类型": "新进"}],
    )

    quarter_failure = SimpleNamespace(
        get_latest_quarter_map=lambda: (_ for _ in ()).throw(OSError("locked")),
        query_change_rows=lambda **_kwargs: [{"unexpected": True}],
    )
    assert stock_snapshot.load_fund_holding_snapshot(store=quarter_failure) == ({}, [])

    row_failure = SimpleNamespace(
        get_latest_quarter_map=lambda: {"QFII": "2026Q1"},
        query_change_rows=lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad rows")),
    )
    assert stock_snapshot.load_fund_holding_snapshot(store=row_failure) == ({}, [])


def test_anchor_cache_warmup_preserves_dependency_order(monkeypatch, tmp_path):
    calls = []
    default_root = tmp_path / "project"
    monkeypatch.setattr(anchor_service, "raise_if_cancelled", lambda token: calls.append(("check", token)))
    monkeypatch.setattr(
        anchor_service,
        "load_ai_chain_cache_rows",
        lambda: calls.append(("ai",)) or [{"代码": "000001"}],
    )
    monkeypatch.setattr(
        anchor_service,
        "load_named_cache_rows",
        lambda name, *, root: calls.append(("na", name, root)) or [{"代码": "600001"}, {"代码": "300001"}],
    )
    monkeypatch.setattr(anchor_service, "project_root", lambda: default_root)

    state = anchor_service.warm_stock_context_anchor_caches(cancellation_token="token")

    assert state == anchor_service.StockContextAnchorCacheState(ai_row_count=1, na_row_count=2)
    assert calls == [
        ("check", "token"),
        ("ai",),
        ("check", "token"),
        ("na", "na_daily_latest.json", default_root),
        ("check", "token"),
    ]


def _earnings_record(**overrides):
    record = {
        "股票代码": "000001",
        "单季净利润_新增": 100.0,
        "环比增速_百分比": 50.0,
        "同比增速_百分比": 20.0,
        "公告日期": "2026-07-15",
    }
    record.update(overrides)
    return record


def test_earnings_cache_query_rejects_invalid_records_and_honours_cancellation():
    now = dt.datetime(2026, 7, 16, 9, 30)
    assert earnings_cache._china_now().tzinfo is None
    assert earnings_cache._eligible_cached_record(
        _earnings_record(**{"单季净利润_新增": "bad"}),
        now=now,
        keep_days=30,
    ) is False
    assert earnings_cache._eligible_cached_record(
        _earnings_record(**{"公告日期": "invalid"}),
        now=now,
        keep_days=30,
    ) is False
    assert earnings_cache._sort_key({"揭晓日": "2026-07-15", "环比增速_百分比": "bad"}) == (
        "2026-07-15",
        0.0,
    )
    assert earnings_cache._normalize_allowed_codes(None) is None
    assert earnings_cache._normalize_allowed_codes(["", "1"]) == {"000001"}
    assert earnings_cache._normalize_context_map({"1": " AI ", "": "ignored", "2": ""}) == {
        "000001": "AI"
    }
    assert earnings_cache._prepare_cached_record(
        "invalid",
        allowed_codes=None,
        context_map={},
        updated_at="",
        now=now,
        keep_days=30,
    ) is None
    assert earnings_cache._prepare_cached_record(
        _earnings_record(),
        allowed_codes={"600001"},
        context_map={},
        updated_at="",
        now=now,
        keep_days=30,
    ) is None
    assert earnings_cache.prepare_cached_earnings_rows({"records": {"not": "a-list"}}, now=now) == []

    empty_dates = {}
    earnings_cache._normalize_record_dates(empty_dates, "")
    assert empty_dates == {}

    token = CancellationToken()
    token.cancel("query_cancelled")
    with pytest.raises(TaskCancelledError, match="query_cancelled"):
        earnings_cache.prepare_cached_earnings_rows(
            {"records": [_earnings_record()]},
            now=now,
            cancellation_token=token,
        )


def test_earnings_read_only_state_uses_repository_then_legacy_file(monkeypatch, tmp_path):
    from core import runtime_paths
    from infra.storage import stock_context_repository

    monkeypatch.setattr(
        stock_context_repository,
        "load_earnings_state_payload",
        lambda: ({"records": [{"股票代码": "000001"}]}, "repository-time"),
    )
    assert earnings_cache._load_read_only_state() == (
        {"records": [{"股票代码": "000001"}]},
        "repository-time",
    )

    monkeypatch.setattr(stock_context_repository, "load_earnings_state_payload", lambda: ({}, ""))
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", str(tmp_path))
    cache_path = tmp_path / "data" / "earnings_state.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"records": []}', encoding="utf-8")
    payload, updated_at = earnings_cache._load_read_only_state()
    assert payload == {"records": []}
    assert updated_at

    cache_path.write_text("[]", encoding="utf-8")
    assert earnings_cache._load_read_only_state() == ({}, "")
    cache_path.write_text("{broken", encoding="utf-8")
    assert earnings_cache._load_read_only_state() == ({}, "")


def test_earnings_cached_dependency_loaders_fail_closed(monkeypatch):
    from app.services import ui_industry_chain_service

    monkeypatch.setattr(
        ui_industry_chain_service,
        "load_cached_ai_industry_chain_stock_codes",
        lambda: {"000001"},
    )
    monkeypatch.setattr(
        ui_industry_chain_service,
        "load_cached_ai_industry_chain_context_map",
        lambda: {"000001": "AI"},
    )
    assert earnings_cache._load_cached_stock_codes() == {"000001"}
    assert earnings_cache._load_cached_context_map() == {"000001": "AI"}

    monkeypatch.setattr(
        ui_industry_chain_service,
        "load_cached_ai_industry_chain_stock_codes",
        lambda: (_ for _ in ()).throw(OSError("cache unavailable")),
    )
    monkeypatch.setattr(
        ui_industry_chain_service,
        "load_cached_ai_industry_chain_context_map",
        lambda: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )
    assert earnings_cache._load_cached_stock_codes() == set()
    assert earnings_cache._load_cached_context_map() == {}


def test_active_f5_snapshot_facade_uses_consistent_read_boundary(monkeypatch):
    boundaries = []

    @contextmanager
    def _boundary():
        boundaries.append("enter")
        try:
            yield
        finally:
            boundaries.append("exit")

    class _Repository:
        @staticmethod
        def resolve_rps_path(fallback):
            return f"active-rps:{fallback}"

        @staticmethod
        def resolve_sector_rps_path(fallback):
            return f"active-sector:{fallback}"

    monkeypatch.setattr(f5_snapshot, "f5_snapshot_read_boundary", _boundary)
    monkeypatch.setattr(f5_snapshot, "get_default_f5_snapshot_repository", _Repository)
    monkeypatch.setattr(f5_snapshot, "cache_file_exists", lambda path: "missing" not in path)
    monkeypatch.setattr(f5_snapshot, "cache_file_mtime", lambda path: 123.5 if path.startswith("active-rps:") else 0.0)
    monkeypatch.setattr(
        f5_snapshot,
        "load_json_file",
        lambda path: {"path": path},
    )

    assert f5_snapshot.resolve_active_rps_path("fallback.json") == "active-rps:fallback.json"
    assert f5_snapshot.resolve_active_sector_rps_path("fallback-sector.json") == "active-sector:fallback-sector.json"
    assert f5_snapshot.read_active_rps_bundle("missing.json") == ("active-rps:missing.json", None)
    assert f5_snapshot.read_active_rps_bundle("rps.json") == (
        "active-rps:rps.json",
        {"path": "active-rps:rps.json"},
    )
    assert f5_snapshot.read_active_sector_rps_bundle("missing.json") == ("active-sector:missing.json", None)
    assert f5_snapshot.read_active_sector_rps_bundle("sector.json") == (
        "active-sector:sector.json",
        {"path": "active-sector:sector.json"},
    )
    assert f5_snapshot.active_rps_cache_mtime("rps.json") == 123.5
    assert f5_snapshot.load_active_rps_payload("rps.json") == {"path": "active-rps:rps.json"}
    assert f5_snapshot.load_active_sector_rps_payload("sector.json") == {"path": "active-sector:sector.json"}
    assert boundaries.count("enter") == boundaries.count("exit")

    with pytest.raises(ValueError, match="must be an object"):
        f5_snapshot._require_object_payload([], label="RPS")


@pytest.mark.parametrize(
    ("package", "export_name"),
    [
        (services_package, "_coverage_service_export"),
        (bootstrap_package, "_coverage_bootstrap_export"),
    ],
)
def test_lazy_package_exports_cache_resolved_values_and_reject_unknown(monkeypatch, package, export_name):
    monkeypatch.setitem(package._EXPORTS, export_name, ("fake.module", "answer"))
    monkeypatch.setattr(package, "import_module", lambda _module_name: SimpleNamespace(answer=42))
    monkeypatch.delattr(package, export_name, raising=False)

    assert package.__getattr__(export_name) == 42
    assert getattr(package, export_name) == 42
    assert export_name in package.__dir__()
    with pytest.raises(AttributeError, match="unknown_export"):
        package.__getattr__("unknown_export")
