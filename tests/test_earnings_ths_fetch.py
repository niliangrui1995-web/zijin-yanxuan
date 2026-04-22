# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time

import pandas as pd
import pytest

from earnings import engine as engine_module


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def _clear_ths_cache():
    engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()


def test_fetch_stock_financial_benefit_ths_parses_valid_payload(monkeypatch):
    _clear_ths_cache()

    payload = {
        "flashData": json.dumps(
            {
                "title": [
                    "科目时间",
                    ["扣除非经常性损益后的净利润", "元"],
                    ["归属于母公司所有者的净利润", "元"],
                ],
                "report": [
                    ["2025-12-31", "2025-09-30"],
                    ["1.20亿", "0.80亿"],
                    ["1.30亿", "0.90亿"],
                ],
            },
            ensure_ascii=False,
        )
    }

    monkeypatch.setattr(
        engine_module.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(json.dumps(payload, ensure_ascii=False)),
    )

    df = engine_module._fetch_stock_financial_benefit_ths("300197")

    assert list(df.columns) == [
        "报告期",
        "扣除非经常性损益后的净利润",
        "归属于母公司所有者的净利润",
    ]
    assert df.iloc[0]["报告期"] == "2025-12-31"
    assert df.iloc[0]["扣除非经常性损益后的净利润"] == "1.20亿"
    assert df.iloc[1]["归属于母公司所有者的净利润"] == "0.90亿"


def test_fetch_stock_financial_benefit_ths_raises_readable_error_on_empty_payload(monkeypatch):
    _clear_ths_cache()

    monkeypatch.setattr(
        engine_module.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(""),
    )

    with pytest.raises(ValueError, match="响应体为空"):
        engine_module._fetch_stock_financial_benefit_ths("300197")


def test_safe_ak_fetch_uses_recent_ths_cache_as_fallback(monkeypatch):
    _clear_ths_cache()
    cached_df = pd.DataFrame(
        [
            {
                "报告期": "2025-12-31",
                "扣除非经常性损益后的净利润": "1.20亿",
            }
        ]
    )
    cache_key = engine_module._ths_financial_benefit_cache_key("300197", "按报告期")
    engine_module._THS_FINANCIAL_BENEFIT_CACHE[cache_key] = (time.time() - 10, cached_df.copy())

    monkeypatch.setattr(
        engine_module,
        "_fetch_stock_financial_benefit_ths",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("THS 返回异常: symbol=300197, 响应体为空")),
    )
    monkeypatch.setattr(engine_module.time, "sleep", lambda _: None)

    result = engine_module.safe_ak_fetch(engine_module.ak.stock_financial_benefit_ths, symbol="300197")

    assert result.to_dict("records") == cached_df.to_dict("records")
