# -*- coding: utf-8 -*-
from datetime import datetime

import pytest

from core.json_cache import load_json_file, save_json_file
from vcp import engine_external


class _FakeResponse:
    def __init__(self, payload):
        import json

        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _size=-1):
        return self._payload

    def close(self):
        return None


def test_batch_get_finance_info_uses_eastmoney_market_cap_fields(monkeypatch, tmp_path):
    cache_file = tmp_path / "finance.json"
    seen_urls = []

    payload = {
        "rc": 0,
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f2": 11.19,
                    "f18": 11.17,
                    "f20": 217540343000,
                    "f21": 217536783320,
                }
            ]
        },
    }

    def _fake_urlopen(request, timeout=8):
        del timeout
        seen_urls.append(request.full_url)
        return _FakeResponse(payload)

    monkeypatch.setattr(engine_external, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(engine_external.urllib.request, "urlopen", _fake_urlopen)

    result = engine_external.batch_get_finance_info(["000001"])

    assert seen_urls
    assert "fields=f12,f2,f18,f20,f21" in seen_urls[0]
    assert "secids=0.000001" in seen_urls[0]
    assert result["000001"]["zongguben"] == pytest.approx(217540343000 / 11.19)
    assert result["000001"]["market_cap"] == 217540343000
    assert result["000001"]["price_base"] == 11.19
    assert result["000001"]["source"] == "eastmoney"

    cached = load_json_file(str(cache_file))
    assert cached["000001"]["info"]["source"] == "eastmoney"


def test_batch_get_finance_info_falls_back_to_last_close_when_latest_price_missing(monkeypatch, tmp_path):
    cache_file = tmp_path / "finance.json"

    payload = {
        "rc": 0,
        "data": {
            "diff": [
                {
                    "f12": "600519",
                    "f2": "-",
                    "f18": 1462.07,
                    "f20": 1837706540513,
                    "f21": 1837706540513,
                }
            ]
        },
    }

    monkeypatch.setattr(engine_external, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        engine_external.urllib.request,
        "urlopen",
        lambda request, timeout=8: _FakeResponse(payload),
    )

    result = engine_external.batch_get_finance_info(["600519"])

    assert result["600519"]["zongguben"] == pytest.approx(1837706540513 / 1462.07)
    assert result["600519"]["market_cap"] == 1837706540513


def test_batch_check_market_cap_prefers_direct_market_cap_scaling(monkeypatch):
    monkeypatch.setattr(
        engine_external,
        "batch_get_finance_info",
        lambda codes: {
            "000001": {
                "zongguben": 100.0,
                "market_cap": 1000.0,
                "price_base": 10.0,
                "source": "eastmoney",
            }
        },
    )

    result = engine_external.batch_check_market_cap(["000001"], close_prices={"000001": 12.0})

    assert result == {"000001": 1200.0}


def test_batch_get_finance_info_uses_recent_cache_when_network_fails(monkeypatch, tmp_path):
    cache_file = tmp_path / "finance.json"
    today = datetime.now().strftime("%Y-%m-%d")

    save_json_file(
        str(cache_file),
        {
            "000001": {
                "date": today,
                "info": {
                    "zongguben": 19457582073.27935,
                    "market_cap": 217540343000,
                    "source": "eastmoney",
                },
            }
        },
    )

    monkeypatch.setattr(engine_external, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        engine_external.urllib.request,
        "urlopen",
        lambda request, timeout=8: (_ for _ in ()).throw(RuntimeError("network-down")),
    )

    result = engine_external.batch_get_finance_info(["000001", "000002"])

    assert result == {
        "000001": {
            "zongguben": 19457582073.27935,
            "market_cap": 217540343000,
            "source": "eastmoney",
        }
    }
