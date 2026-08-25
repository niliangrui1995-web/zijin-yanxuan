from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core.exceptions import CacheIOError, DataFormatError
from vcp import engine_external as module


class _Response:
    def __init__(self, payload, *, close_raises=False):
        self.payload = json.dumps(payload).encode("utf-8")
        self.closed = False
        self.close_raises = close_raises

    def read(self):
        return self.payload

    def close(self):
        self.closed = True
        if self.close_raises:
            raise OSError("close")


def test_finance_small_helpers_cover_normalization_and_local_fallback(monkeypatch):
    assert module._to_eastmoney_secid(" 600001 ") == "1.600001"
    assert module._to_eastmoney_secid("000001") == "0.000001"
    assert module._to_eastmoney_secid("920045") == "0.920045"
    assert module._to_eastmoney_secid("900001") == "1.900001"
    assert module._normalize_stock_codes([1, "1", " 600001 ", "", None, "abc", "1234567"]) == [
        "000001",
        "000001",
        "600001",
    ]
    assert module._has_valid_share_capital(None) is False
    assert module._has_valid_share_capital({"_zongguben": "10"}) is True

    monkeypatch.setattr(module, "_load_tdx_local_config", lambda: "")
    assert module._load_local_tdx_finance_info(["000001"]) == {}
    monkeypatch.setattr(module, "_load_tdx_local_config", lambda: "D:/HT/vipdoc")
    monkeypatch.setattr(module, "load_local_tdx_capital_snapshot", lambda *_args: {"000001": {"zongguben": 1}})
    assert module._load_local_tdx_finance_info(["000001"])["000001"]["zongguben"] == 1
    monkeypatch.setattr(
        module,
        "load_local_tdx_capital_snapshot",
        lambda *_args: (_ for _ in ()).throw(OSError("bad local data")),
    )
    assert module._load_local_tdx_finance_info(["000001"]) == {}


def test_fetch_finance_batches_filters_invalid_rows_and_closes(monkeypatch):
    responses = []

    def fake_open(request, timeout, **kwargs):
        assert timeout == 8
        assert kwargs == {
            "allowed_hosts": module._EASTMONEY_FINANCE_ALLOWED_HOSTS,
            "allow_reserved_tun_for_allowed_hosts": True,
        }
        responses.append(request.full_url)
        payload = {
            "rc": 0,
            "data": {
                "diff": [
                    {"f12": "", "f2": 10, "f18": 9, "f20": 100, "f21": 80},
                    {"f12": "000001", "f2": 0, "f18": 0, "f20": 100, "f21": 80},
                    {"f12": "000002", "f2": 10, "f18": 9, "f20": 0, "f21": 80},
                    {"f12": "600001", "f2": 10, "f18": 9, "f20": 1000, "f21": 800},
                ]
            },
        }
        return _Response(payload, close_raises=True)

    monkeypatch.setattr(module, "urlopen_https", fake_open)
    codes = [f"{index:06d}" for index in range(81)]
    result = module._fetch_eastmoney_finance_info(codes)

    assert len(responses) == 2
    assert result == {
        "600001": {
            "total_shares": 100.0,
            "market_cap": 1000.0,
            "float_market_cap": 800.0,
            "price_base": 10.0,
            "source": "eastmoney",
        }
    }
    assert module._fetch_eastmoney_finance_info([]) == {}


def test_fetch_finance_raises_on_remote_error(monkeypatch):
    monkeypatch.setattr(module, "urlopen_https", lambda *_args, **_kwargs: _Response({"rc": 7}))
    with pytest.raises(RuntimeError, match="rc=7"):
        module._fetch_eastmoney_finance_info(["000001"])


def _cache_entry(info, *, days_ago=0, date=None):
    stamp = date or (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {"date": stamp, "info": info}


def test_batch_finance_empty_invalid_cache_and_online_empty_fallback(monkeypatch, tmp_path):
    assert module.batch_get_finance_info([]) == {}
    cache_file = tmp_path / "finance.json"
    cache_file.write_text("{}", encoding="utf-8")
    cache = {
        "000001": _cache_entry({"zongguben": 1}, date="not-a-date"),
        "000002": _cache_entry({"zongguben": 2}, days_ago=40),
        "000003": _cache_entry({"zongguben": 3}, days_ago=0),
    }
    monkeypatch.setattr(module, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(module, "load_json_file", lambda *_args: cache)
    monkeypatch.setattr(module, "_load_local_tdx_finance_info", lambda *_args: {})
    monkeypatch.setattr(module, "_fetch_eastmoney_finance_info", lambda *_args: {})

    result = module.batch_get_finance_info(["000001", "000002", "000003"])

    assert result == {
        "000001": {"total_shares": 1},
        "000002": {"total_shares": 2},
        "000003": {"total_shares": 3},
    }

    monkeypatch.setattr(module, "load_json_file", lambda *_args: (_ for _ in ()).throw(DataFormatError("bad")))
    assert module.batch_get_finance_info(["000004"]) == {}


def test_batch_finance_network_failure_uses_stale_valid_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "finance.json"
    cache_file.write_text("{}", encoding="utf-8")
    cache = {
        "000001": _cache_entry({"zongguben": 5}, days_ago=100),
        "000002": _cache_entry({"zongguben": 0}, days_ago=100),
    }
    monkeypatch.setattr(module, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(module, "load_json_file", lambda *_args: cache)
    monkeypatch.setattr(module, "_load_local_tdx_finance_info", lambda *_args: {})
    monkeypatch.setattr(
        module,
        "_fetch_eastmoney_finance_info",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    assert module.batch_get_finance_info(["000001", "000002"]) == {"000001": {"total_shares": 5}}


def test_batch_finance_saves_online_results_in_batches_and_tolerates_write_error(monkeypatch, tmp_path):
    cache_file = tmp_path / "finance.json"
    monkeypatch.setattr(module, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(module, "_load_local_tdx_finance_info", lambda *_args: {})
    codes = [f"{index:06d}" for index in range(81)]
    monkeypatch.setattr(
        module, "_fetch_eastmoney_finance_info", lambda requested: {code: {"zongguben": 1} for code in requested}
    )
    sleeps = []
    monkeypatch.setattr(module._time, "sleep", sleeps.append)
    monkeypatch.setattr(module, "save_json_file", lambda *_args: (_ for _ in ()).throw(CacheIOError("full")))

    result = module.batch_get_finance_info(codes)

    assert len(result) == 81
    assert sleeps == [0.1]


def test_market_cap_covers_missing_direct_and_share_capital_paths(monkeypatch):
    monkeypatch.setattr(
        module,
        "batch_get_finance_info",
        lambda _codes: {
            "direct_scaled": {"market_cap": 1000, "price_base": 10, "zongguben": 1},
            "direct_zero_close": {"market_cap": 2000, "price_base": 20},
            "direct_no_base": {"market_cap": 3000, "price_base": 0},
            "shares_scaled": {"market_cap": 0, "zongguben": 100},
            "shares_plain": {"zongguben": 200},
            "invalid": {"zongguben": 0},
        },
    )
    codes = [
        "missing",
        "direct_scaled",
        "direct_zero_close",
        "direct_no_base",
        "shares_scaled",
        "shares_plain",
        "invalid",
    ]
    result = module.batch_check_market_cap(
        codes,
        close_prices={
            "direct_scaled": 12,
            "direct_zero_close": 0,
            "direct_no_base": 7,
            "shares_scaled": 3,
        },
    )
    assert result == {
        "direct_scaled": 1200,
        "direct_zero_close": 2000,
        "direct_no_base": 3000,
        "shares_scaled": 300,
        "shares_plain": 200,
    }


@pytest.mark.parametrize(
    ("code", "prefix"),
    [("600001", "SH"), ("000001", "SZ"), ("800001", "BJ"), ("200001", "SZ")],
)
def test_institutional_shareholders_prefixes_and_no_data(monkeypatch, code, prefix):
    seen = []

    def fake_open(request, timeout, **kwargs):
        assert kwargs == {
            "allowed_hosts": module._EASTMONEY_SHAREHOLDER_ALLOWED_HOSTS,
            "allow_reserved_tun_for_allowed_hosts": True,
        }
        seen.append(request.full_url)
        return _Response({"sdltgd": []})

    monkeypatch.setattr(module, "urlopen_https", fake_open)
    result = module.check_institutional_shareholders(code)
    assert result[0] is False
    assert f"code={prefix}{code}" in seen[0]


def test_institutional_shareholders_formats_top_three_and_no_match(monkeypatch):
    rows = [
        {"HOLDER_NAME": "Very Long Institution Holder Name", "HOLDER_TYPE": "QFII"},
        {"HOLDER_NAME": "Fund Two", "HOLDER_TYPE": "\u57fa\u91d1"},
        {"HOLDER_NAME": "Insurance Three", "HOLDER_TYPE": "\u4fdd\u9669"},
        {"HOLDER_NAME": "Broker Four", "HOLDER_TYPE": "\u5238\u5546"},
    ]
    monkeypatch.setattr(module, "urlopen_https", lambda *_args, **_kwargs: _Response({"sdltgd": rows}))
    found, detail = module.check_institutional_shareholders("600001")
    assert found is True
    assert detail.count("/") == 2

    monkeypatch.setattr(
        module,
        "urlopen_https",
        lambda *_args, **_kwargs: _Response({"sdltgd": [{"HOLDER_NAME": "person", "HOLDER_TYPE": "person"}]}),
    )
    assert module.check_institutional_shareholders("000001")[0] is False
    monkeypatch.setattr(module, "urlopen_https", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")))
    failed = module.check_institutional_shareholders("000001")
    assert failed[0] is False and "down" in failed[1]


def test_batch_institution_cache_query_sleep_and_save(monkeypatch, tmp_path):
    cache_file = tmp_path / "holders.json"
    cache_file.write_text("{}", encoding="utf-8")
    fresh = datetime.now().strftime("%Y-%m-%d")
    cache = {
        "fresh": {"date": fresh, "has_institution": True, "detail": "cached"},
        "stale": {"date": "2000-01-01", "has_institution": False, "detail": "old"},
        "broken": {"date": "bad"},
    }
    monkeypatch.setattr(module, "SHAREHOLDER_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(module, "load_json_file", lambda *_args: cache)
    calls = []
    monkeypatch.setattr(
        module, "check_institutional_shareholders", lambda code: calls.append(code) or (code == "stale", code)
    )
    sleeps = []
    monkeypatch.setattr(module._time, "sleep", sleeps.append)
    saved = []
    monkeypatch.setattr(module, "save_json_file", lambda path, payload: saved.append((path, payload.copy())))

    result = module.batch_check_institution(["fresh", "stale", "broken"])

    assert result["fresh"]["detail"] == "cached"
    assert calls == ["stale", "broken"]
    assert sleeps == [0.3]
    assert saved and saved[0][0] == str(cache_file)


def test_batch_institution_tolerates_cache_and_write_failures(monkeypatch, tmp_path):
    cache_file = tmp_path / "holders.json"
    cache_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "SHAREHOLDER_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(module, "load_json_file", lambda *_args: (_ for _ in ()).throw(CacheIOError("read")))
    monkeypatch.setattr(module, "check_institutional_shareholders", lambda _code: (False, "none"))
    monkeypatch.setattr(module, "save_json_file", lambda *_args: (_ for _ in ()).throw(CacheIOError("write")))

    result = module.batch_check_institution(["000001"])

    assert result["000001"]["has_institution"] is False
