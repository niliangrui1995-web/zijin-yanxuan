import json
import os

import pandas as pd

import infra.market_data.tdx_data_provider as provider_module
import vcp.data_provider_local as data_provider_local
from infra.market_data.tdx_data_provider import TdxDataProvider
from vcp.data_provider_local import load_local_gbbq_for_code


def _write_gbbq_fixture(tmp_path):
    tdx_root = tmp_path / "tdx"
    gbbq_path = tdx_root / "T0002" / "hq_cache" / "gbbq"
    gbbq_path.parent.mkdir(parents=True)
    gbbq_path.write_bytes(b"fixture")
    vipdoc = tdx_root / "vipdoc"
    vipdoc.mkdir()
    cache_file = tmp_path / "gbbq_parsed.json"
    legacy_file = tmp_path / "gbbq_parsed.pkl"
    payload = {
        "data": {
            "000001": [
                {
                    "market": 0,
                    "code": "000001",
                    "datetime": 20240101,
                    "category": 1,
                    "hongli_panqianliutong": 1.0,
                    "peigujia_qianzongguben": 0.0,
                    "songgu_qianzongguben": 0.0,
                    "peigu_houzongguben": 0.0,
                }
            ],
            "600000": [
                {
                    "market": 1,
                    "code": "600000",
                    "datetime": 20240102,
                    "category": 1,
                    "hongli_panqianliutong": 2.0,
                    "peigujia_qianzongguben": 0.0,
                    "songgu_qianzongguben": 0.0,
                    "peigu_houzongguben": 0.0,
                }
            ],
        },
        "mtime": os.path.getmtime(gbbq_path),
        "records": 2,
    }
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return vipdoc, cache_file, legacy_file


def test_load_local_gbbq_for_code_reads_only_requested_code(tmp_path):
    vipdoc, cache_file, legacy_file = _write_gbbq_fixture(tmp_path)

    result = load_local_gbbq_for_code(
        str(vipdoc),
        str(cache_file),
        str(legacy_file),
        {},
        "000001",
    )

    assert sorted(result) == ["000001"]
    assert len(result["000001"]) == 1
    assert result["000001"].iloc[0]["datetime"] == 20240101


def test_load_local_gbbq_for_code_keeps_existing_cache_when_cache_is_stale(tmp_path, monkeypatch):
    vipdoc, cache_file, legacy_file = _write_gbbq_fixture(tmp_path)
    tdx_root = tmp_path / "tdx"
    gbbq_path = tdx_root / "T0002" / "hq_cache" / "gbbq"
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["mtime"] = os.path.getmtime(gbbq_path) - 1000
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    existing_frame = pd.DataFrame([{"code": "600000", "datetime": 20240102, "category": 1}])

    def fake_full_load(*_args, **_kwargs):
        raise AssertionError("single-code stale cache must not rebuild gbbq synchronously")

    monkeypatch.setattr(data_provider_local, "load_local_gbbq", fake_full_load)

    result = load_local_gbbq_for_code(
        str(vipdoc),
        str(cache_file),
        str(legacy_file),
        {"600000": existing_frame},
        "000001",
    )

    assert sorted(result) == ["600000"]
    assert result["600000"] is existing_frame


def test_load_local_gbbq_for_code_can_explicitly_rebuild_when_cache_is_stale(tmp_path, monkeypatch):
    vipdoc, cache_file, legacy_file = _write_gbbq_fixture(tmp_path)
    tdx_root = tmp_path / "tdx"
    gbbq_path = tdx_root / "T0002" / "hq_cache" / "gbbq"
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["mtime"] = os.path.getmtime(gbbq_path) - 1000
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    fallback_frame = pd.DataFrame([{"code": "000001", "datetime": 20240103, "category": 1}])
    calls = []

    def fake_full_load(tdx_vipdoc, gbbq_cache_file, legacy_gbbq_cache_file, local_gbbq, *, force=False):
        calls.append(
            {
                "tdx_vipdoc": tdx_vipdoc,
                "gbbq_cache_file": gbbq_cache_file,
                "legacy_gbbq_cache_file": legacy_gbbq_cache_file,
                "local_gbbq": local_gbbq,
                "force": force,
            }
        )
        return {"000001": fallback_frame}

    monkeypatch.setattr(data_provider_local, "load_local_gbbq", fake_full_load)

    result = load_local_gbbq_for_code(
        str(vipdoc),
        str(cache_file),
        str(legacy_file),
        {},
        "000001",
        fallback_to_full_load=True,
    )

    assert sorted(result) == ["000001"]
    assert result["000001"] is fallback_frame
    assert calls == [
        {
            "tdx_vipdoc": str(vipdoc),
            "gbbq_cache_file": str(cache_file),
            "legacy_gbbq_cache_file": str(legacy_file),
            "local_gbbq": {},
            "force": True,
        }
    ]


def test_provider_local_fetch_uses_single_code_gbbq_cache(monkeypatch):
    provider = TdxDataProvider(offline=True)
    provider.tdx_vipdoc = "D:/fake/vipdoc"
    provider._local_gbbq = {}
    provider._local_gbbq_loaded = False
    captured = {}

    def fake_load_one(code):
        return {code: pd.DataFrame([{"code": code, "datetime": 20240101, "category": 1}])}

    def fake_fetch(code, **kwargs):
        captured["local_gbbq"] = kwargs["local_gbbq"]
        return pd.DataFrame({"close": [1.0]}), False

    monkeypatch.setattr(provider, "_load_local_gbbq_for_code", fake_load_one)
    monkeypatch.setattr(provider_module, "fetch_from_local_tdx", fake_fetch)

    provider._fetch_from_local_tdx("000001")

    assert sorted(captured["local_gbbq"]) == ["000001"]
    assert provider._local_gbbq_loaded is False
