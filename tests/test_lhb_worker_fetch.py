# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd
import pytest

import infra.market_data.lhb_provider as lhb_worker
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError


def _detail_frame(*, pct: float = 3.5) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "收盘价": 12.34,
                "涨跌幅": pct,
                "换手率": 6.7,
                "流通市值": 25000000000.0,
                "龙虎榜净买额": 80000000.0,
                "上榜原因": "日涨幅偏离值达到7%的前5只证券",
            }
        ]
    )


def _install_base_apis(monkeypatch, *, jg=None, yyb=None) -> list[tuple[str, str]]:
    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_detail_em", lambda **_kwargs: _detail_frame())
    monkeypatch.setattr(
        lhb_worker.ak,
        "stock_lhb_jgmmtj_em",
        lambda **_kwargs: pd.DataFrame() if jg is None else jg.copy(),
    )
    monkeypatch.setattr(
        lhb_worker.ak,
        "stock_lhb_hyyyb_em",
        lambda **_kwargs: pd.DataFrame() if yyb is None else yyb.copy(),
    )
    detail_calls = []

    def stock_detail(*, symbol, date, flag):
        detail_calls.append((symbol, flag))
        return pd.DataFrame()

    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_stock_detail_em", stock_detail)
    return detail_calls


def test_fetch_lhb_strict_filter_skips_rows_without_participation(monkeypatch):
    detail_calls = _install_base_apis(monkeypatch)

    result = lhb_worker.fetch_lhb_data_for_date("20260421", strict_filter=True, emit_success_log=False)

    assert result == []
    assert detail_calls == []


def test_fetch_lhb_non_strict_returns_default_record_and_meta(monkeypatch):
    detail_calls = _install_base_apis(monkeypatch)

    payload = lhb_worker.fetch_lhb_data_for_date(
        "20260421",
        strict_filter=False,
        emit_success_log=False,
        return_meta=True,
    )

    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["records"][0] == {
        "代码": "000001",
        "名称": "平安银行",
        "现价": 12.34,
        "涨幅%": 3.5,
        "市值": 250.0,
        "上榜日期": "20260421",
        "上榜净买额(万)": 8000.0,
        "机构净买(万)": 0.0,
        "外资净买(万)": 0.0,
        "外资净买入": "未现身",
        "_外资净买入_tooltip": "当日未发现外资席位上榜",
        "换手率%": 6.7,
        "上榜原因": "日涨幅偏离值达到7%的前5只证券",
    }
    assert detail_calls == []


def test_fetch_lhb_strict_filter_accepts_exact_institution_match_when_branch_api_fails(monkeypatch):
    jg = pd.DataFrame(
        [
            {
                "代码": "000001",
                "上榜原因": "日涨幅偏离值达到7%的前5只证券",
                "收盘价": 12.34,
                "涨跌幅": 3.5,
                "买方机构数": 1,
                "卖方机构数": 0,
                "机构买入总额": 9000000.0,
                "机构卖出总额": 1000000.0,
                "机构买入净额": 8000000.0,
            }
        ]
    )
    _install_base_apis(monkeypatch, jg=jg)
    monkeypatch.setattr(
        lhb_worker.ak,
        "stock_lhb_hyyyb_em",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("branch unavailable")),
    )

    result = lhb_worker.fetch_lhb_data_for_date("20260421", strict_filter=True, emit_success_log=False)

    assert len(result) == 1
    assert result[0]["机构净买(万)"] == 800.0
    assert result[0]["外资净买入"] == "未现身"


def test_fetch_lhb_uses_surviving_foreign_detail_side(monkeypatch):
    yyb = pd.DataFrame(
        [
            {
                "营业部名称": "深股通专用",
                "买入股票": "平安银行",
                "卖出股票": "",
            }
        ]
    )
    detail_calls = _install_base_apis(monkeypatch, yyb=yyb)

    def stock_detail(*, symbol, date, flag):
        detail_calls.append((symbol, flag))
        if flag == "买入":
            raise OSError("buy side unavailable")
        return pd.DataFrame(
            [
                {
                    "交易营业部名称": "深股通专用",
                    "买入金额": 3000000.0,
                    "卖出金额": 1000000.0,
                    "净额": 2000000.0,
                    "类型": "日涨幅偏离值达到7%的前5只证券",
                }
            ]
        )

    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_stock_detail_em", stock_detail)

    result = lhb_worker.fetch_lhb_data_for_date("20260421", strict_filter=True, emit_success_log=False)

    assert detail_calls == [("000001", "买入"), ("000001", "卖出")]
    assert len(result) == 1
    assert result[0]["外资净买(万)"] == 200.0


def test_pool_fetch_uses_daily_foreign_cache_without_per_stock_requests(monkeypatch):
    yyb = pd.DataFrame([{"营业部名称": "深股通专用", "买入股票": "平安银行", "卖出股票": ""}])
    detail_calls = _install_base_apis(monkeypatch, yyb=yyb)
    daily_cache = {
        "000001": pd.DataFrame(
            [
                {
                    "交易营业部名称": "深股通专用",
                    "买入金额": 3000000.0,
                    "卖出金额": 1000000.0,
                    "净额": 2000000.0,
                    "类型": "日涨幅偏离值达到7%的前5只证券",
                }
            ]
        )
    }
    monkeypatch.setattr(lhb_worker, "_load_daily_foreign_detail_cache", lambda *_args, **_kwargs: daily_cache)

    result = lhb_worker.fetch_lhb_pool_for_date("20260421", emit_success_log=False)

    assert detail_calls == []
    assert result[0]["外资净买(万)"] == 200.0


def test_fetch_lhb_honors_owner_cancellation_before_provider_call(monkeypatch):
    token = CancellationToken()
    token.cancel("owner_shutdown")
    monkeypatch.setattr(
        lhb_worker.ak,
        "stock_lhb_detail_em",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled task must not call AkShare")),
    )

    with pytest.raises(TaskCancelledError, match="owner_shutdown"):
        lhb_worker.fetch_lhb_data_for_date(
            "20260421",
            strict_filter=False,
            cancellation_token=token,
        )
