from __future__ import annotations

import json
import struct
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from core.exceptions import CacheIOError, DataFormatError
from vcp import data_provider_local as local


def _write_dbf(path, records: list[bytes], *, fields=None, declared_count: int | None = None):
    fields = fields or [("GPDM", "C", 6, 0), ("ZGB", "N", 14, 2)]
    header_len = 32 + len(fields) * 32 + 1
    record_len = 1 + sum(field[2] for field in fields)
    header = bytearray(32)
    header[0] = 0x03
    header[4:8] = struct.pack("<I", len(records) if declared_count is None else declared_count)
    header[8:10] = struct.pack("<H", header_len)
    header[10:12] = struct.pack("<H", record_len)
    descriptors = bytearray()
    for name, field_type, length, decimals in fields:
        descriptor = bytearray(32)
        descriptor[: len(name)] = name.encode("ascii")
        descriptor[11] = ord(field_type)
        descriptor[16] = length
        descriptor[17] = decimals
        descriptors.extend(descriptor)
    path.write_bytes(bytes(header) + bytes(descriptors) + b"\r" + b"".join(records) + b"\x1a")


def _gbbq_paths(tmp_path):
    vipdoc = tmp_path / "vipdoc"
    gbbq = tmp_path / "T0002" / "hq_cache" / "gbbq"
    gbbq.parent.mkdir(parents=True)
    gbbq.write_bytes(b"source")
    return str(vipdoc), gbbq, str(tmp_path / "gbbq_cache.json"), str(tmp_path / "legacy.json")


def test_gbbq_serialization_and_deserialization_skip_empty_invalid_values():
    frame = pd.DataFrame([{"datetime": 20260714, "category": 1}])
    serialized = local.serialize_gbbq_cache({"000001": frame, "none": None, "empty": pd.DataFrame()})
    assert serialized == {"000001": [{"datetime": 20260714, "category": 1}]}

    restored = local.deserialize_gbbq_cache({"000001": serialized["000001"], "bad": {"not": "rows"}})
    assert list(restored) == ["000001"]
    assert restored["000001"].iloc[0]["category"] == 1
    assert local.serialize_gbbq_cache({}) == {}
    assert local.deserialize_gbbq_cache({}) == {}


def test_json_array_scanner_handles_nested_strings_escapes_and_incomplete_payload():
    payload = '[{"text": "] and \\"quoted\\"", "nested": [1, 2]}] trailing'
    end = local._find_json_array_end(payload, 0)
    assert json.loads(payload[:end])[0]["nested"] == [1, 2]
    with pytest.raises(ValueError, match="incomplete"):
        local._find_json_array_end('[{"x": 1}', 0)


def test_gbbq_mtime_reader_and_single_code_loader_cover_match_mismatch_and_missing(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                "data": {
                    "000001": [{"text": "bracket ] in string", "category": 1}],
                    "600000": [],
                },
                "mtime": 12.5,
            }
        ),
        encoding="utf-8",
    )
    assert local._read_gbbq_cache_mtime(str(cache)) == 12.5
    assert local._load_gbbq_cache_rows_for_code(str(cache), "000001", 12.5)[0]["category"] == 1
    assert local._load_gbbq_cache_rows_for_code(str(cache), "missing", 12.5) == []
    with pytest.raises(ValueError, match="mtime mismatch"):
        local._load_gbbq_cache_rows_for_code(str(cache), "000001", 99.0)

    no_array = tmp_path / "no_array.json"
    no_array.write_text('{"000001": null, "mtime": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="array is missing"):
        local._load_gbbq_cache_rows_for_code(str(no_array), "000001", None)

    no_mtime = tmp_path / "no_mtime.json"
    no_mtime.write_text('{"000001": []}', encoding="utf-8")
    assert local._read_gbbq_cache_mtime(str(no_mtime)) is None
    assert local._read_gbbq_cache_mtime(str(tmp_path / "missing.json")) is None

    large = tmp_path / "large.json"
    large.write_bytes(b"x" * (local._GBBQ_CACHE_MTIME_TAIL_BYTES + 10) + b'\n{"mtime": 5}')
    assert local._read_gbbq_cache_mtime(str(large)) == 5.0

    monkeypatch.setattr(local, "_read_gbbq_cache_mtime", lambda _path: None)
    fallback = tmp_path / "fallback.json"
    fallback.write_text('{"000001": [], "mtime": 3}', encoding="utf-8")
    with pytest.raises(ValueError, match="mtime mismatch"):
        local._load_gbbq_cache_rows_for_code(str(fallback), "000001", 4.0)


def test_parse_base_dbf_caches_missing_open_error_and_invalid_schema(tmp_path, monkeypatch):
    missing = str(tmp_path / "missing.dbf")
    monkeypatch.setattr(local, "_dbf_signature", lambda path: None if path == missing else (1, 1))
    assert local._parse_tdx_base_dbf(missing) == {}
    assert local._parse_tdx_base_dbf(missing) == {}

    unreadable = str(tmp_path / "unreadable.dbf")
    real_open = open
    monkeypatch.setattr(
        "builtins.open",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("locked")) if path == unreadable else real_open(path, *args, **kwargs)
        ),
    )
    assert local._parse_tdx_base_dbf(unreadable) == {}

    invalid = tmp_path / "invalid.dbf"
    _write_dbf(invalid, [], fields=[("OTHER", "C", 6, 0)])
    assert local._parse_tdx_base_dbf(str(invalid)) == {}


def test_parse_base_dbf_skips_deleted_invalid_negative_and_truncated_records(tmp_path, monkeypatch):
    dbf = tmp_path / "base.dbf"
    records = [
        b"*" + b"000001" + f"{100:14.2f}".encode("ascii"),
        b" " + b"ABCDEF" + f"{100:14.2f}".encode("ascii"),
        b" " + b"000002" + b" " * 14,
        b" " + b"000003" + f"{-1:14.2f}".encode("ascii"),
        b" " + b"000004" + f"{123:14.2f}".encode("ascii"),
    ]
    _write_dbf(dbf, records, declared_count=6)
    monkeypatch.setattr(local, "_dbf_signature", lambda _path: (10, 20))

    parsed = local._parse_tdx_base_dbf(str(dbf))

    assert parsed == {"000004": {"total_shares": 1_230_000.0, "source": "tdx_base"}}
    assert local._parse_tdx_base_dbf(str(dbf)) is parsed


def test_capital_snapshot_normalizes_codes_and_handles_empty_inputs(monkeypatch):
    assert local.load_local_tdx_capital_snapshot([], "vipdoc") == {}
    assert local.load_local_tdx_capital_snapshot(["1"], None) == {}
    monkeypatch.setattr(
        local,
        "_parse_tdx_base_dbf",
        lambda _path: {"000001": {"zongguben": 100.0, "source": "tdx_base"}},
    )
    assert local.load_local_tdx_capital_snapshot([1, "bad", "000001"], "D:\\HT\\vipdoc") == {
        "000001": {"zongguben": 100.0, "source": "tdx_base"}
    }


def test_load_local_gbbq_returns_current_for_missing_path_and_loads_matching_cache(tmp_path, monkeypatch):
    current = {"old": pd.DataFrame([{"category": 1}])}
    assert local.load_local_gbbq(None, "cache", "legacy", current) == current

    vipdoc = str(tmp_path / "vipdoc")
    assert local.load_local_gbbq(vipdoc, "cache", "legacy", current) == current

    vipdoc, gbbq, cache_file, legacy = _gbbq_paths(tmp_path)
    cached_payload = {
        "data": {"000001": [{"datetime": 20260714, "category": 1}]},
        "mtime": gbbq.stat().st_mtime,
        "records": 1,
    }
    removed = []
    monkeypatch.setattr(local, "load_json_file", lambda _path: cached_payload)
    monkeypatch.setattr(local, "remove_cache_file", removed.append)
    monkeypatch.setattr(local.os.path, "exists", lambda path: path in {str(gbbq), cache_file})
    restored = local.load_local_gbbq(vipdoc, cache_file, legacy)
    assert list(restored) == ["000001"]
    assert removed == [legacy]


def test_load_local_gbbq_rebuilds_filters_and_contains_save_and_reader_errors(tmp_path, monkeypatch):
    vipdoc, gbbq, cache_file, legacy = _gbbq_paths(tmp_path)
    source = pd.DataFrame(
        [
            {"code": "000001", "category": 1, "datetime": 20260714},
            {"code": "000001", "category": 2, "datetime": 20260715},
            {"code": "600000", "category": 1, "datetime": 20260714},
        ]
    )

    class Reader:
        def get_df(self, path):
            assert path == str(gbbq)
            return source

    fake_reader_module = SimpleNamespace(GbbqReader=lambda: Reader())
    monkeypatch.setitem(sys.modules, "pytdx.reader", fake_reader_module)
    monkeypatch.setattr(local, "load_json_file", lambda _path: (_ for _ in ()).throw(DataFormatError("stale")))
    saved = []
    removed = []
    monkeypatch.setattr(local, "save_json_file", lambda path, payload: saved.append((path, payload)))
    monkeypatch.setattr(local, "remove_cache_file", removed.append)

    rebuilt = local.load_local_gbbq(vipdoc, cache_file, legacy)
    assert set(rebuilt) == {"000001", "600000"}
    assert saved[0][1]["records"] == 2
    assert removed == [legacy]

    monkeypatch.setattr(local, "save_json_file", lambda *_args: (_ for _ in ()).throw(CacheIOError("readonly")))
    assert set(local.load_local_gbbq(vipdoc, cache_file, legacy, force=True)) == {"000001", "600000"}

    monkeypatch.setitem(
        sys.modules,
        "pytdx.reader",
        SimpleNamespace(GbbqReader=lambda: SimpleNamespace(get_df=lambda _path: pd.DataFrame())),
    )
    assert local.load_local_gbbq(vipdoc, cache_file, legacy, {"old": pd.DataFrame()}, force=True).keys() == {"old"}

    monkeypatch.setitem(
        sys.modules,
        "pytdx.reader",
        SimpleNamespace(GbbqReader=lambda: (_ for _ in ()).throw(RuntimeError("parse failed"))),
    )
    assert local.load_local_gbbq(vipdoc, cache_file, legacy, {"old": pd.DataFrame()}, force=True).keys() == {"old"}


def test_load_one_gbbq_code_covers_shortcuts_cache_success_and_fallbacks(tmp_path, monkeypatch):
    vipdoc, gbbq, cache_file, legacy = _gbbq_paths(tmp_path)
    current = {"000001": pd.DataFrame([{"category": 1}])}
    assert local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, current, "") == current
    assert local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, current, "000001") == current

    full_calls = []
    monkeypatch.setattr(
        local, "load_local_gbbq", lambda *args, **kwargs: full_calls.append((args, kwargs)) or {"full": pd.DataFrame()}
    )
    assert "full" in local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, {}, "000002", force=True)
    assert local.load_local_gbbq_for_code(None, cache_file, legacy, {}, "000002") == {}

    monkeypatch.setattr(local.os.path, "exists", lambda path: path == str(gbbq))
    assert local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, {}, "000002") == {}
    assert "full" in local.load_local_gbbq_for_code(
        vipdoc,
        cache_file,
        legacy,
        {},
        "000002",
        fallback_to_full_load=True,
    )

    monkeypatch.setattr(local.os.path, "exists", lambda path: path in {str(gbbq), cache_file})
    monkeypatch.setattr(local.os.path, "getmtime", lambda _path: 1.0)
    removed = []
    monkeypatch.setattr(local, "remove_cache_file", removed.append)
    monkeypatch.setattr(local, "_load_gbbq_cache_rows_for_code", lambda *_args: [{"category": 1}])
    loaded = local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, {}, "000002")
    assert loaded["000002"].iloc[0]["category"] == 1
    assert removed == [legacy]

    monkeypatch.setattr(local, "_load_gbbq_cache_rows_for_code", lambda *_args: [])
    assert local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, {}, "000003") == {}

    monkeypatch.setattr(
        local,
        "_load_gbbq_cache_rows_for_code",
        lambda *_args: (_ for _ in ()).throw(ValueError("corrupt")),
    )
    assert local.load_local_gbbq_for_code(vipdoc, cache_file, legacy, {}, "000004") == {}
    assert "full" in local.load_local_gbbq_for_code(
        vipdoc,
        cache_file,
        legacy,
        {},
        "000004",
        fallback_to_full_load=True,
    )


def test_market_code_and_day_paths_cover_shanghai_shenzhen_and_beijing():
    assert local.get_market_code("600000") == 1
    assert local.get_market_code("900001") == 1
    assert local.get_market_code("000001") == 0
    assert local.get_market_code("920045") == 2
    assert local.tdx_day_path("D:\\HT\\vipdoc", "600000").endswith("sh600000.day")
    assert local.tdx_day_path("D:\\HT\\vipdoc", "920045").endswith("bj920045.day")
    assert local.tdx_day_path(None, "000001").endswith("sz000001.day")


def test_forward_adjustment_online_fields_no_events_no_dates_and_error_paths():
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-07-10", "2026-07-14"]),
            "open": [10, 12],
            "high": [11, 13],
            "low": [9, 11],
            "close": [10, 12],
            "volume": [100, 200],
        }
    )
    assert local.apply_forward_adjustment(None, 0, "000001", bars, {}) is bars
    assert (
        local.apply_forward_adjustment(SimpleNamespace(get_xdxr_info=lambda *_args: []), 0, "000001", bars, None)
        is bars
    )

    no_dividend = SimpleNamespace(get_xdxr_info=lambda *_args: [{"category": 2, "year": 2026, "month": 7, "day": 14}])
    assert local.apply_forward_adjustment(no_dividend, 0, "000001", bars, None) is bars

    api = SimpleNamespace(
        get_xdxr_info=lambda *_args: [
            {
                "category": 1,
                "year": 2026,
                "month": 7,
                "day": 14,
                "songgu": 1,
                "houzhen": 1,
                "fenhong": 2,
            },
            {
                "category": 1,
                "year": 2020,
                "month": 1,
                "day": 1,
                "songgu": 1,
                "houzhen": 0,
                "fenhong": 0,
            },
        ]
    )
    adjusted = local.apply_forward_adjustment(api, 0, "000001", bars, None)
    assert adjusted.iloc[0]["close"] == pytest.approx((10 - 0.2) / 1.2)
    assert adjusted.iloc[0]["volume"] == pytest.approx(120.0)

    no_datetime = pd.DataFrame({"close": [10.0]})
    unchanged = local.apply_forward_adjustment(api, 0, "000001", no_datetime, None)
    assert unchanged.equals(no_datetime)

    bad_local = {"000001": pd.DataFrame([{"datetime": "20260714", "songgu_qianzongguben": "not-number"}])}
    with pytest.raises(ValueError, match="除权除息因子计算失败"):
        local.apply_forward_adjustment(None, 0, "000001", bars, bad_local)


def test_fetch_from_local_tdx_covers_missing_empty_trim_adjustment_warning_and_online(monkeypatch):
    assert local.fetch_from_local_tdx(
        "000001",
        tdx_vipdoc=None,
        offline=True,
        server_pool=[],
        local_gbbq=None,
        offline_warn_printed=False,
    ) == (None, False)

    monkeypatch.setattr(local, "read_tdx_day_file", lambda _path: None)
    assert local.fetch_from_local_tdx(
        "000001",
        tdx_vipdoc="vipdoc",
        offline=True,
        server_pool=[],
        local_gbbq=None,
        offline_warn_printed=False,
    ) == (None, False)

    rows = local.MAX_HISTORY_BARS + 5
    frame = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=rows), "close": range(rows)})
    monkeypatch.setattr(local, "read_tdx_day_file", lambda _path: frame)
    adjusted = []
    monkeypatch.setattr(local, "apply_forward_adjustment", lambda *_args: adjusted.append(True) or _args[3])
    result, warned = local.fetch_from_local_tdx(
        "000001",
        tdx_vipdoc="vipdoc",
        offline=True,
        server_pool=[],
        local_gbbq={"000001": pd.DataFrame([{"category": 1}])},
        offline_warn_printed=False,
    )
    assert len(result) == local.MAX_HISTORY_BARS and adjusted == [True] and warned is False

    result, warned = local.fetch_from_local_tdx(
        "000001",
        tdx_vipdoc="vipdoc",
        offline=True,
        server_pool=[],
        local_gbbq=None,
        offline_warn_printed=False,
    )
    assert result is not None and warned is True

    adjusted.clear()
    result, warned = local.fetch_from_local_tdx(
        "000001",
        tdx_vipdoc="vipdoc",
        offline=False,
        server_pool=[object()],
        local_gbbq={"000001": pd.DataFrame()},
        offline_warn_printed=True,
    )
    assert result is not None and warned is True and adjusted == []


def test_offline_quotes_skip_missing_and_use_open_for_single_row_with_invalid_date():
    single = pd.DataFrame(
        [{"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "amount": 1000}],
        index=[object()],
    )
    frames = {"missing": None, "empty": pd.DataFrame(), "single": single}
    quotes = local.build_offline_quotes(frames, frames.get)
    assert set(quotes) == {"single"}
    assert quotes["single"]["last_close"] == 10.0
    assert quotes["single"]["date"] is None
