# -*- coding: utf-8 -*-
import pandas as pd
import pytest

import infra.market_data.lhb_provider as lhb_worker


def test_fetch_lhb_data_for_date_splits_foreign_net_by_reason(monkeypatch):
    detail_df = pd.DataFrame(
        [
            {
                "代码": "002361",
                "名称": "神剑股份",
                "收盘价": 16.85,
                "涨跌幅": 9.99,
                "换手率": 34.06,
                "流通市值": 13634000000.0,
                "龙虎榜净买额": 100000000.0,
                "上榜原因": "日换手率达到20%的前5只证券",
            },
            {
                "代码": "002361",
                "名称": "神剑股份",
                "收盘价": 16.85,
                "涨跌幅": 9.99,
                "换手率": 34.06,
                "流通市值": 13634000000.0,
                "龙虎榜净买额": 100000000.0,
                "上榜原因": "日涨幅偏离值达到7%的前5只证券",
            },
            {
                "代码": "002361",
                "名称": "神剑股份",
                "收盘价": 16.85,
                "涨跌幅": 9.99,
                "换手率": 34.06,
                "流通市值": 13634000000.0,
                "龙虎榜净买额": 200000000.0,
                "上榜原因": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
            },
        ]
    )

    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_detail_em", lambda start_date, end_date: detail_df.copy())
    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_jgmmtj_em", lambda start_date, end_date: pd.DataFrame())
    monkeypatch.setattr(
        lhb_worker.ak,
        "stock_lhb_hyyyb_em",
        lambda start_date, end_date: pd.DataFrame(
            [
                {
                    "营业部名称": "深股通专用",
                    "买入股票": "神剑股份",
                    "卖出股票": "",
                }
            ]
        ),
    )

    detail_rows = [
        {
            "交易营业部名称": "深股通专用",
            "买入金额": 539753736.10,
            "卖出金额": 179090502.39,
            "净额": 360663233.71,
            "类型": "日换手率达到20%的前5只证券",
        },
        {
            "交易营业部名称": "深股通专用",
            "买入金额": 539753736.10,
            "卖出金额": 179090502.39,
            "净额": 360663233.71,
            "类型": "日涨幅偏离值达到7%的前5只证券",
        },
        {
            "交易营业部名称": "深股通专用",
            "买入金额": 722090628.25,
            "卖出金额": 436883931.07,
            "净额": 285206697.18,
            "类型": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        },
    ]
    stock_detail_calls: list[str] = []

    def _stock_lhb_stock_detail_em(symbol, date, flag):
        assert symbol == "002361"
        assert date == "20260421"
        stock_detail_calls.append(flag)
        return pd.DataFrame(detail_rows)

    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_stock_detail_em", _stock_lhb_stock_detail_em)

    rows = lhb_worker.fetch_lhb_data_for_date(
        "20260421",
        strict_filter=False,
        emit_success_log=False,
    )

    assert len(rows) == 2
    assert stock_detail_calls == ["买入", "卖出"]

    row_by_reason = {lhb_worker._normalize_reason_key(row["上榜原因"]): row for row in rows}

    single_day_key = lhb_worker._normalize_reason_key("日换手率达到20%的前5只证券 | 日涨幅偏离值达到7%的前5只证券")
    multi_day_key = lhb_worker._normalize_reason_key("连续三个交易日内，涨幅偏离值累计达到20%的证券")

    assert row_by_reason[single_day_key]["外资净买(万)"] == pytest.approx(36066.32, abs=0.01)
    assert row_by_reason[single_day_key]["外资净买入"] == "净买3.61亿 | 深股通+3.61亿"

    assert row_by_reason[multi_day_key]["外资净买(万)"] == pytest.approx(28520.67, abs=0.01)
    assert row_by_reason[multi_day_key]["外资净买入"] == "净买2.85亿 | 深股通+2.85亿"
