# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace

import pytest

from core.exceptions import CacheIOError, DataFormatError
from infra.storage import asian_market_cache
from infra.storage import foreign_block_repository as foreign_repo
from infra.storage import json_cache_repository as json_cache
from infra.storage import na_daily_repository as na_repo
from infra.storage import stock_context_repository as stock_repo


def test_json_cache_normalizes_special_values_and_round_trips(tmp_path):
    class Scalar:
        @staticmethod
        def item():
            return {"nested": (1, 2)}

    class DateLike:
        @staticmethod
        def isoformat():
            return "2026-07-12"

    class Fallback:
        def item(self):
            raise ValueError("not scalar")

        def isoformat(self):
            raise TypeError("not a date")

        def __str__(self):
            return "fallback"

    path = tmp_path / "nested" / "cache.json"
    json_cache.save_json_file(
        str(path),
        {"scalar": Scalar(), "date": DateLike(), "set": {3, 4}, "fallback": Fallback()},
    )

    payload = json_cache.load_json_file(str(path))
    assert payload["scalar"] == {"nested": [1, 2]}
    assert payload["date"] == "2026-07-12"
    assert sorted(payload["set"]) == [3, 4]
    assert payload["fallback"] == "fallback"
    assert json_cache.cache_file_exists(str(path)) is True
    assert json_cache.cache_file_mtime(str(path)) > 0
    assert json_cache.cache_file_signature(str(path))[1] == path.stat().st_size


def test_json_cache_stable_errors_cleanup_and_metadata_fallbacks(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(CacheIOError, match="cache read failed"):
        json_cache.load_json_file(str(missing))

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(DataFormatError, match="json payload invalid"):
        json_cache.load_json_file(str(invalid))

    target = tmp_path / "write.json"
    monkeypatch.setattr(json_cache.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(CacheIOError, match="cache write failed"):
        json_cache.save_json_file(str(target), {"value": 1})
    assert not (tmp_path / "write.json.tmp").exists()

    json_cache.remove_cache_file(str(invalid))
    assert not invalid.exists()
    monkeypatch.setattr(json_cache.os.path, "exists", lambda *_args: True)
    monkeypatch.setattr(json_cache.os, "remove", lambda *_args: (_ for _ in ()).throw(OSError("remove")))
    json_cache.remove_cache_file("ignored")

    assert json_cache.cache_file_signature(None) is None
    assert json_cache.cache_file_mtime(None) == 0.0
    assert json_cache.cache_file_exists(None) is False


def test_stock_context_repository_store_legacy_and_named_cache_paths(monkeypatch, tmp_path):
    class Store:
        payload = None

        def load_json(self, *_args, **_kwargs):
            return self.payload

    store = Store()
    monkeypatch.setattr(stock_repo.data_store_module, "DataStore", lambda: store)

    store.payload = {"results": [{"code": "000001"}, None]}
    assert stock_repo.load_scan_cache_rows(root=tmp_path) == [{"code": "000001"}]
    store.payload = [{"code": "600000"}, "bad"]
    assert stock_repo.load_scan_cache_rows(root=tmp_path) == [{"code": "600000"}]

    store.payload = None
    legacy = tmp_path / "data" / "scan_cache.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"results": [{"code": "300750"}]}), encoding="utf-8")
    assert stock_repo.load_scan_cache_rows(root=tmp_path) == [{"code": "300750"}]
    legacy.write_text("{", encoding="utf-8")
    assert stock_repo.load_scan_cache_rows(root=tmp_path) == []

    named = tmp_path / "data" / "Cache" / "named.json"
    named.parent.mkdir(parents=True)
    named.write_text(json.dumps({"items": [{"name": "row"}, 1]}), encoding="utf-8")
    assert stock_repo.load_named_cache_rows("named.json", root=tmp_path, payload_key="items") == [{"name": "row"}]
    named.write_text("[]", encoding="utf-8")
    assert stock_repo.load_named_cache_rows("named.json", root=tmp_path) == []
    assert stock_repo.load_named_cache_rows("absent.json", root=tmp_path) == []


def test_stock_context_repository_earnings_and_lhb_signatures(monkeypatch, tmp_path):
    store = SimpleNamespace(
        load_earnings_state=lambda: {"records": [1]},
        fetch_one=lambda *_args, **_kwargs: {"updated_at": " 2026-07-12 "},
    )
    monkeypatch.setattr(stock_repo.data_store_module, "data_store", store)
    assert stock_repo.load_earnings_state_payload() == ({"records": [1]}, "2026-07-12")

    store.load_earnings_state = lambda: ["bad"]
    store.fetch_one = lambda *_args, **_kwargs: "bad"
    assert stock_repo.load_earnings_state_payload() == ({}, "")
    store.load_earnings_state = lambda: (_ for _ in ()).throw(RuntimeError("closed"))
    assert stock_repo.load_earnings_state_payload() == ({}, "")

    cache_dir = tmp_path / "data" / "Cache"
    cache_dir.mkdir(parents=True)
    legacy = cache_dir / "lhb_pool_20d.json"
    legacy.write_text("{}", encoding="utf-8")
    signature = stock_repo.lhb_pool_cache_signature(root=tmp_path)
    assert signature[0] == str(legacy)
    legacy.unlink()
    assert stock_repo.lhb_pool_cache_signature(root=tmp_path) is None


def test_na_daily_repository_identity_listing_and_payload_fallbacks(monkeypatch, tmp_path):
    report = tmp_path / "战报_20260712153045.md"
    report.write_text("plain report", encoding="utf-8")
    old = tmp_path / "战报_misc.md"
    old.write_text("old report", encoding="utf-8")
    os.utime(old, (1_700_000_000, 1_700_000_000))

    repo = na_repo.NADailyReportRepository(tmp_path)
    assert repo.parse_report_identity(report)[:2] == ("20260712", 20260712153045)
    assert repo.parse_report_identity(old)[0] == dt.datetime.fromtimestamp(1_700_000_000).strftime("%Y%m%d")
    assert len(repo.signature_for([report, old])) == 2
    assert repo.list_recent_report_files(limit=1) == [str(report)]

    structured = report.with_suffix(".json")
    structured.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(na_repo, "parse_structured_report", lambda _data: ([{"code": "A"}], {"A": {}}))
    assert repo.load_report_payload(report) == ([{"code": "A"}], {"A": {}})
    monkeypatch.setattr(na_repo, "parse_structured_report", lambda _data: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(na_repo, "parse_battle_report", lambda text: [{"text": text}])
    monkeypatch.setattr(na_repo, "parse_recommendations", lambda text: {"raw": text})
    assert repo.load_report_payload(report) == ([{"text": "plain report"}], {"raw": "plain report"})
    report.unlink()
    assert repo.load_report_payload(report) == ([], {})


def test_asian_cache_repository_success_failure_and_cleanup(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "asian.json"
    asian_market_cache.write_json_cache(str(path), {"rows": [1]})
    assert asian_market_cache.read_json_cache(str(path)) == {"rows": [1]}
    assert asian_market_cache.cache_mtime(str(path)) > 0
    path.write_text("{", encoding="utf-8")
    assert asian_market_cache.read_json_cache(str(path), default={"fallback": True}) == {"fallback": True}
    assert asian_market_cache.cache_mtime(None) == 0.0

    target = tmp_path / "failed.json"
    monkeypatch.setattr(asian_market_cache.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(OSError, match="replace"):
        asian_market_cache.write_json_cache(str(target), [1])
    assert not (tmp_path / "failed.json.tmp").exists()


def test_foreign_block_repository_schema_edges_and_round_trip(monkeypatch, tmp_path):
    path = tmp_path / "foreign.json"
    monkeypatch.setattr(foreign_repo, "_CACHE_FILE", path)

    with pytest.raises(DataFormatError, match="rows invalid"):
        foreign_repo._normalize_rows(None)
    with pytest.raises(DataFormatError, match="row invalid"):
        foreign_repo._normalize_rows([{}, "bad"])
    with pytest.raises(DataFormatError, match="days_to_fetch invalid"):
        foreign_repo._normalize_days_to_fetch(object())

    foreign_repo.json_cache_repository.save_json_file(str(path), [])
    with pytest.raises(DataFormatError, match="payload invalid"):
        foreign_repo.load_foreign_block_cache_payload()

    payload = {
        "saved_at": " 2026-07-12 ",
        "days_to_fetch": 5,
        "latest_trade_date": " 2026-07-11 ",
        "rows": [{"代码": "000001"}],
    }
    foreign_repo.save_foreign_block_cache_payload(payload)
    assert foreign_repo.load_foreign_block_cache_payload() == {
        "saved_at": "2026-07-12",
        "days_to_fetch": 5,
        "latest_trade_date": "2026-07-11",
        "rows": [{"代码": "000001"}],
    }
