# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd

from earnings import engine as engine_module
from earnings.engine import EarningsEngine


def _build_engine() -> EarningsEngine:
    engine = EarningsEngine.__new__(EarningsEngine)
    engine.keep_days = 30
    engine.cache_file = ""
    engine.seen_fingerprints = set()
    engine.local_records = []
    engine.last_sync_date = "2026-04-15"
    engine._quick_report_profit_cache = {}
    return engine


def test_prune_retryable_seen_fingerprints_removes_active_orphans(monkeypatch):
    engine = _build_engine()
    engine.seen_fingerprints = {
        "SHOCK_300308_20251231_财报",
        "SHOCK_000001_20251231_财报",
        "SHOCK_999999_20240930_财报",
    }
    engine.local_records = [
        {"股票代码": "000001", "报告期": "20251231", "数据类型": "财报"},
    ]

    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20251231", "20260331"])

    changed = engine._prune_retryable_seen_fingerprints()

    assert changed is True
    assert "SHOCK_300308_20251231_财报" not in engine.seen_fingerprints
    assert "SHOCK_000001_20251231_财报" in engine.seen_fingerprints
    assert "SHOCK_999999_20240930_财报" in engine.seen_fingerprints


def test_fetch_daily_surprises_does_not_mark_seen_when_candidate_fails_threshold(monkeypatch):
    engine = _build_engine()

    candidate_df = pd.DataFrame(
        [
            {
                "股票代码": "300308",
                "股票简称": "中际旭创",
                "最新公告日期": "2026-04-16",
                "净利润-净利润": 1000000,
            }
        ]
    )

    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20251231"])
    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (
            candidate_df.copy() if fetch_func.__name__ == "stock_yjbb_em" else pd.DataFrame()
        ),
    )
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)
    monkeypatch.setattr(engine, "_save_cache", lambda: None)
    monkeypatch.setattr(
        engine,
        "compute_single_quarter_qoq",
        lambda *args, **kwargs: {
            "单季净利润_新增": 1.0,
            "单季净利润_上期": 1.0,
            "环比增速_百分比": 16.9,
            "同比增速_百分比": 10.0,
            "error": None,
        },
    )

    result = engine.fetch_daily_surprises(target_publish_date="2026-04-16")

    assert result.empty
    assert engine.seen_fingerprints == set()
    assert engine.local_records == []


def test_fetch_daily_surprises_marks_seen_only_after_valid_record(monkeypatch):
    engine = _build_engine()

    candidate_df = pd.DataFrame(
        [
            {
                "股票代码": "300308",
                "股票简称": "中际旭创",
                "最新公告日期": "2026-04-16",
                "净利润-净利润": 1000000,
            }
        ]
    )

    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20251231"])
    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (
            candidate_df.copy() if fetch_func.__name__ == "stock_yjbb_em" else pd.DataFrame()
        ),
    )
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)
    monkeypatch.setattr(engine, "_save_cache", lambda: None)
    monkeypatch.setattr(
        engine,
        "compute_single_quarter_qoq",
        lambda *args, **kwargs: {
            "单季净利润_新增": 1.0,
            "单季净利润_上期": 1.0,
            "环比增速_百分比": 35.0,
            "同比增速_百分比": 12.0,
            "error": None,
        },
    )

    result = engine.fetch_daily_surprises(target_publish_date="2026-04-16")

    assert len(result) == 1
    assert "SHOCK_300308_20251231_财报" in engine.seen_fingerprints
    assert len(engine.local_records) == 1


def test_fetch_daily_surprises_filters_candidates_to_ai_industry_chain_pool(monkeypatch):
    engine = _build_engine()
    engine.stock_universe_provider = lambda: {"300308"}

    candidate_df = pd.DataFrame(
        [
            {
                "股票代码": "300308",
                "股票简称": "中际旭创",
                "最新公告日期": "2026-04-16",
                "净利润-净利润": 1000000,
            },
            {
                "股票代码": "600000",
                "股票简称": "浦发银行",
                "最新公告日期": "2026-04-16",
                "净利润-净利润": 1000000,
            },
        ]
    )

    checked_codes = []
    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20251231"])
    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (
            candidate_df.copy() if fetch_func.__name__ == "stock_yjbb_em" else pd.DataFrame()
        ),
    )
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)
    monkeypatch.setattr(engine, "_save_cache", lambda: None)

    def _compute(code, *args, **kwargs):
        checked_codes.append(code)
        return {
            "单季净利润_新增": 1.0,
            "单季净利润_上期": 1.0,
            "环比增速_百分比": 35.0,
            "同比增速_百分比": 12.0,
            "error": None,
        }

    monkeypatch.setattr(engine, "compute_single_quarter_qoq", _compute)

    result = engine.fetch_daily_surprises(target_publish_date="2026-04-16")

    assert checked_codes == ["300308"]
    assert result["股票代码"].tolist() == ["300308"]
    assert "SHOCK_600000_20251231_财报" not in engine.seen_fingerprints


def test_get_cached_records_filters_to_ai_industry_chain_pool(monkeypatch):
    engine = _build_engine()
    engine.stock_universe_provider = lambda: {"300308"}
    engine.local_records = [
        {
            "股票代码": "300308",
            "股票名称": "中际旭创",
            "公告日期": "2026-04-16",
            "环比增速_百分比": 35.0,
        },
        {
            "股票代码": "600000",
            "股票名称": "浦发银行",
            "公告日期": "2026-04-16",
            "环比增速_百分比": 35.0,
        },
    ]
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)

    result = engine.get_cached_records()

    assert result["股票代码"].tolist() == ["300308"]


def test_fetch_daily_surprises_accepts_next_trade_day_financial_report_on_today_scan(monkeypatch):
    engine = _build_engine()

    candidate_df = pd.DataFrame(
        [
            {
                "股票代码": "300308",
                "股票简称": "中际旭创",
                "最新公告日期": "2026-04-17",
                "净利润-净利润": 1000000,
            }
        ]
    )

    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20260331"])
    monkeypatch.setattr(
        engine_module.MarketCalendar,
        "today",
        classmethod(lambda cls, market="CN": pd.Timestamp("2026-04-16").date()),
    )
    monkeypatch.setattr(
        engine_module.MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260417", "20260416", "20260415"]),
    )
    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (
            candidate_df.copy() if fetch_func.__name__ == "stock_yjbb_em" else pd.DataFrame()
        ),
    )
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)
    monkeypatch.setattr(engine, "_save_cache", lambda: None)
    monkeypatch.setattr(
        engine,
        "compute_single_quarter_qoq",
        lambda *args, **kwargs: {
            "单季净利润_新增": 1.0,
            "单季净利润_上期": 1.0,
            "环比增速_百分比": 57.69,
            "同比增速_百分比": 264.67,
            "error": None,
        },
    )

    result = engine.fetch_daily_surprises(target_publish_date="2026-04-16")

    assert len(result) == 1
    row = result.iloc[0].to_dict()
    assert row["股票代码"] == "300308"
    assert row["公告日期"] == "2026-04-16"
    assert row["源公告日期"] == "2026-04-17"
    assert "SHOCK_300308_20260331_财报" in engine.seen_fingerprints


def test_fetch_daily_surprises_does_not_accept_next_trade_day_financial_report_on_backfill(monkeypatch):
    engine = _build_engine()

    candidate_df = pd.DataFrame(
        [
            {
                "股票代码": "300308",
                "股票简称": "中际旭创",
                "最新公告日期": "2026-04-17",
                "净利润-净利润": 1000000,
            }
        ]
    )

    monkeypatch.setattr(engine_module, "current_active_report_dates", lambda: ["20260331"])
    monkeypatch.setattr(
        engine_module.MarketCalendar,
        "today",
        classmethod(lambda cls, market="CN": pd.Timestamp("2026-04-16").date()),
    )
    monkeypatch.setattr(
        engine_module.MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260417", "20260416", "20260415"]),
    )
    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (
            candidate_df.copy() if fetch_func.__name__ == "stock_yjbb_em" else pd.DataFrame()
        ),
    )
    monkeypatch.setattr(engine, "_inject_sectors", lambda records: records)
    monkeypatch.setattr(engine, "_save_cache", lambda: None)
    monkeypatch.setattr(
        engine,
        "compute_single_quarter_qoq",
        lambda *args, **kwargs: {
            "单季净利润_新增": 1.0,
            "单季净利润_上期": 1.0,
            "环比增速_百分比": 57.69,
            "同比增速_百分比": 264.67,
            "error": None,
        },
    )

    result = engine.fetch_daily_surprises(target_publish_date="2026-04-15")

    assert result.empty
    assert engine.seen_fingerprints == set()
