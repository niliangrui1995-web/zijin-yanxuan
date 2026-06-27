# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pandas as pd
import pytest

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


def test_select_profit_columns_prefers_matching_profit_basis():
    columns = [
        "报告期",
        "净利润",
        "归属于母公司所有者的净利润",
        "扣除非经常性损益后的净利润",
    ]

    assert engine_module._select_profit_columns(columns, is_koufei=True) == ["扣除非经常性损益后的净利润"]
    assert engine_module._select_profit_columns(columns, is_koufei=False) == ["归属于母公司所有者的净利润"]


def test_parse_amount_handles_units_and_invalid_values():
    assert engine_module._parse_amount("1.5万") == 15000.0
    assert engine_module._parse_amount("2亿") == 200000000.0
    assert engine_module._parse_amount("-1,234.50") == -1234.5
    assert pd.isna(engine_module._parse_amount(""))
    assert pd.isna(engine_module._parse_amount("--"))
    assert pd.isna(engine_module._parse_amount(None))


def test_single_quarter_metrics_cover_all_report_periods():
    values = {
        "2026-06-30": (50.0, False),
        "2026-03-31": (20.0, False),
        "2025-09-30": (75.0, False),
        "2025-06-30": (40.0, False),
        "2027-03-31": (30.0, True),
        "2026-12-31": (90.0, False),
        "2026-09-30": (65.0, True),
        "2026-06-30-q2": (60.0, False),
        "2026-03-31-q2": (25.0, False),
    }

    def _get_q3_profit_with_quick(target_date, basis_desc):
        return values.get(target_date, (engine_module.np.nan, False))

    q3_metrics, q3_error = engine_module._compute_single_quarter_metrics(2026, 9, 90.0, _get_q3_profit_with_quick)
    assert q3_error is None
    assert q3_metrics.current_single == 40.0
    assert q3_metrics.last_single == 30.0
    assert q3_metrics.yoy_base_single == 35.0
    assert q3_metrics.last_single_basis == "财报"

    def _get_q2_profit_with_quick(target_date, basis_desc):
        aliases = {
            "2027-03-31": values["2027-03-31"],
            "2026-06-30": values["2026-06-30-q2"],
            "2026-03-31": values["2026-03-31-q2"],
        }
        return aliases.get(target_date, (engine_module.np.nan, False))

    q2_metrics, q2_error = engine_module._compute_single_quarter_metrics(2027, 6, 70.0, _get_q2_profit_with_quick)
    assert q2_error is None
    assert q2_metrics.current_single == 40.0
    assert q2_metrics.last_single == 30.0
    assert q2_metrics.yoy_base_single == 35.0
    assert q2_metrics.last_single_basis == "快报净利润回填"

    def _get_q1_profit_with_quick(target_date, basis_desc):
        return {
            "2026-12-31": (90.0, False),
            "2026-09-30": (65.0, True),
            "2026-03-31": (30.0, False),
        }.get(target_date, (engine_module.np.nan, False))

    q1_metrics, q1_error = engine_module._compute_single_quarter_metrics(2027, 3, 40.0, _get_q1_profit_with_quick)
    assert q1_error is None
    assert q1_metrics.current_single == 40.0
    assert q1_metrics.last_single == 25.0
    assert q1_metrics.yoy_base_single == 30.0
    assert q1_metrics.last_single_basis == "快报净利润回填"

    unknown_metrics, unknown_error = engine_module._compute_single_quarter_metrics(2027, 5, 40.0, _get_q1_profit_with_quick)
    assert unknown_error is None
    assert pd.isna(unknown_metrics.current_single)
    assert pd.isna(unknown_metrics.last_single)
    assert unknown_metrics.last_single_basis == "财报"


def test_compute_single_quarter_metrics_uses_q4_cumulative_bases():
    values = {
        "2025-09-30": (80.0, False),
        "2025-06-30": (50.0, True),
        "2024-12-31": (100.0, False),
        "2024-09-30": (70.0, False),
    }

    def _get_cum_profit_with_quick(target_date, basis_desc):
        return values.get(target_date, (engine_module.np.nan, False))

    metrics, error = engine_module._compute_single_quarter_metrics(
        2025,
        12,
        120.0,
        _get_cum_profit_with_quick,
    )

    assert error is None
    assert metrics.current_single == 40.0
    assert metrics.last_single == 30.0
    assert metrics.yoy_base_single == 30.0
    assert metrics.last_single_basis == "快报净利润回填"


def test_compute_single_quarter_metrics_preserves_missing_record_error():
    def _get_cum_profit_with_quick(target_date, basis_desc):
        return engine_module.np.nan, False

    metrics, error = engine_module._compute_single_quarter_metrics(
        2025,
        6,
        120.0,
        _get_cum_profit_with_quick,
    )

    assert error == "缺记录"
    assert pd.isna(metrics.current_single)
    assert pd.isna(metrics.last_single)


def test_ths_financial_cache_preview_and_error_format(monkeypatch):
    original_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)
    df = pd.DataFrame({"报告期": ["2026-03-31"], "净利润": [100.0]})
    try:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module.time, "time", lambda: 1000.0)

        assert engine_module._preview_remote_text("  alpha\n beta  ", limit=20) == "alpha beta"
        assert engine_module._preview_remote_text("", limit=20) == "<empty>"
        assert engine_module._preview_remote_text("abcdef", limit=3) == "abc..."
        assert engine_module._ths_financial_benefit_cache_key("7", "按报告期") == "000007::按报告期"

        returned = engine_module._set_cached_ths_financial_benefit("7", "按报告期", df)
        assert returned.equals(df)
        returned.loc[0, "净利润"] = 200.0

        monkeypatch.setattr(engine_module.time, "time", lambda: 1010.0)
        cached, age_sec = engine_module._get_cached_ths_financial_benefit("000007", "按报告期", max_age_sec=60)
        assert age_sec == 10.0
        assert cached.loc[0, "净利润"] == 100.0

        cached.loc[0, "净利润"] = 300.0
        cached_again, _ = engine_module._get_cached_ths_financial_benefit("000007", "按报告期", max_age_sec=60)
        assert cached_again.loc[0, "净利润"] == 100.0

        stale, stale_age = engine_module._get_cached_ths_financial_benefit("000007", "按报告期", max_age_sec=5)
        assert stale is None
        assert stale_age is None

        formatted = engine_module._format_ths_payload_error("7", "  alpha\n beta  ", "bad payload", status_code=502)
        assert "symbol=000007" in formatted
        assert "status=502" in formatted
        assert "preview=alpha beta" in formatted
    finally:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_cache)


def test_fetch_stock_financial_benefit_ths_parses_payload_and_uses_cache(monkeypatch):
    original_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)
    calls = []

    class Response:
        status_code = 200
        text = json.dumps(
            {
                "flashData": json.dumps(
                    {
                        "title": [["报告期"], ["2026-03-31"], ["2025-12-31"]],
                        "report": [["指标", "数值"], ["净利润", "100"], ["扣非净利润", "80"]],
                    },
                    ensure_ascii=False,
                )
            },
            ensure_ascii=False,
        )

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    try:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module, "requests_get_https", fake_get)

        result = engine_module._fetch_stock_financial_benefit_ths("7", indicator="按报告期")
        assert result["报告期"].tolist() == ["指标", "数值"]
        assert "2026-03-31" in result.columns
        assert calls[0][0] == "https://basic.10jqka.com.cn/api/stock/finance/000007_benefit.json"
        assert calls[0][1]["timeout"] == engine_module._THS_REQUEST_TIMEOUT

        cached = engine_module._fetch_stock_financial_benefit_ths("000007", indicator="按报告期")
        assert cached.equals(result)
        assert len(calls) == 1

        with pytest.raises(ValueError):
            engine_module._fetch_stock_financial_benefit_ths("7", indicator="bad")
    finally:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_cache)


def test_ths_financial_cache_prunes_oldest_entry(monkeypatch):
    original_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)
    df = pd.DataFrame({"报告期": ["2026-03-31"]})
    try:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module, "_THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES", 1)
        monkeypatch.setattr(engine_module.time, "time", lambda: 1000.0)
        engine_module._set_cached_ths_financial_benefit("1", "按报告期", df)
        monkeypatch.setattr(engine_module.time, "time", lambda: 1001.0)
        engine_module._set_cached_ths_financial_benefit("2", "按报告期", df)

        assert list(engine_module._THS_FINANCIAL_BENEFIT_CACHE) == ["000002::按报告期"]
    finally:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_cache)


def test_fetch_stock_financial_benefit_ths_rejects_oversized_response_and_flash_data(monkeypatch):
    original_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)

    class Response:
        status_code = 200

        def __init__(self, text):
            self.text = text

    try:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module, "_THS_MAX_RESPONSE_CHARS", 5)
        monkeypatch.setattr(engine_module, "requests_get_https", lambda *args, **kwargs: Response('{"flashData": "{}"}'))
        with pytest.raises(ValueError, match="response too large"):
            engine_module._fetch_stock_financial_benefit_ths("7")

        monkeypatch.setattr(engine_module, "_THS_MAX_RESPONSE_CHARS", 1000)
        monkeypatch.setattr(engine_module, "_THS_MAX_FLASHDATA_CHARS", 5)
        payload = {"flashData": "x" * 20}
        monkeypatch.setattr(
            engine_module,
            "requests_get_https",
            lambda *args, **kwargs: Response(json.dumps(payload, ensure_ascii=False)),
        )
        with pytest.raises(ValueError, match="flashData too large"):
            engine_module._fetch_stock_financial_benefit_ths("7")
    finally:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_cache)


def test_fetch_stock_financial_benefit_ths_rejects_oversized_table_shape(monkeypatch):
    original_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)

    class Response:
        status_code = 200
        text = json.dumps(
            {
                "flashData": json.dumps(
                    {
                        "title": [["报告期"], ["2026-03-31"], ["2025-12-31"]],
                        "report": [["指标"], ["净利润"], ["扣非净利润"]],
                    },
                    ensure_ascii=False,
                )
            },
            ensure_ascii=False,
        )

    try:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module, "_THS_MAX_SECTION_ROWS", 1)
        monkeypatch.setattr(engine_module, "requests_get_https", lambda *args, **kwargs: Response())

        with pytest.raises(ValueError, match="rows exceed limit"):
            engine_module._fetch_stock_financial_benefit_ths("7")
    finally:
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_cache)


def test_safe_ak_fetch_reuses_pool_cache_and_ths_stale_cache(monkeypatch):
    original_pool_cache = dict(engine_module._POOL_CACHE)
    original_ths_cache = dict(engine_module._THS_FINANCIAL_BENEFIT_CACHE)
    calls = []
    df = pd.DataFrame({"value": [1]})

    def fake_fetch(date=None):
        calls.append(date)
        return df.copy()

    try:
        engine_module._POOL_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        monkeypatch.setattr(engine_module.time, "time", lambda: 1000.0)

        first = engine_module.safe_ak_fetch(fake_fetch, date="20260331")
        first.loc[0, "value"] = 2
        second = engine_module.safe_ak_fetch(fake_fetch, date="20260331")
        assert second.loc[0, "value"] == 1
        assert calls == ["20260331"]

        def fake_stock_financial_benefit_ths(*args, **kwargs):
            return pd.DataFrame()

        stale_df = pd.DataFrame({"报告期": ["2026-03-31"], "净利润": [100.0]})
        engine_module._THS_FINANCIAL_BENEFIT_CACHE["000007::按报告期"] = (900.0, stale_df)
        monkeypatch.setattr(engine_module.ak, "stock_financial_benefit_ths", fake_stock_financial_benefit_ths)
        monkeypatch.setattr(
            engine_module,
            "_fetch_stock_financial_benefit_ths",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("offline")),
        )
        monkeypatch.setattr(engine_module.time, "sleep", lambda _seconds: None)

        fallback = engine_module.safe_ak_fetch(engine_module.ak.stock_financial_benefit_ths, symbol="7", indicator="按报告期")
        assert fallback.equals(stale_df)
    finally:
        engine_module._POOL_CACHE.clear()
        engine_module._POOL_CACHE.update(original_pool_cache)
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.clear()
        engine_module._THS_FINANCIAL_BENEFIT_CACHE.update(original_ths_cache)


def test_inject_sectors_uses_ai_industry_chain_context(monkeypatch):
    engine = _build_engine()
    monkeypatch.setattr(
        engine_module,
        "load_ai_industry_chain_context_map",
        lambda: {"300308": "光模块 | 800G"},
    )
    records = [
        {"股票代码": "300308", "股票名称": "中际旭创"},
        {"股票代码": "600000", "股票名称": "浦发银行"},
    ]

    result = engine._inject_sectors(records)

    assert result is records
    assert records[0]["所属行业与概念"] == "光模块 | 800G"
    assert records[1]["所属行业与概念"] == "--"


def test_earnings_date_filters_and_candidate_builders(monkeypatch):
    engine = _build_engine()

    assert EarningsEngine._normalize_publish_date("2026-04-16 19:30:00") == "2026-04-16"
    assert EarningsEngine._normalize_publish_date(None) == ""
    assert EarningsEngine._next_trade_date("bad-date") is None

    trade_days = {pd.Timestamp("2026-04-18").date()}
    monkeypatch.setattr(engine_module.MarketCalendar, "is_trade_day", classmethod(lambda cls, day, market="CN": day in trade_days))
    assert EarningsEngine._next_trade_date("2026-04-16") == "2026-04-18"

    monkeypatch.setattr(
        engine_module.MarketCalendar,
        "today",
        classmethod(lambda cls, market="CN": pd.Timestamp("2026-04-16").date()),
    )
    monkeypatch.setattr(EarningsEngine, "_next_trade_date", classmethod(lambda cls, trade_date: "2026-04-17"))
    assert EarningsEngine._resolve_allowed_publish_dates("2026-04-16", "财报") == {"2026-04-16", "2026-04-17"}
    assert EarningsEngine._resolve_allowed_publish_dates("2026-04-15", "财报") == {"2026-04-15"}
    assert EarningsEngine._resolve_allowed_publish_dates("2026-04-16", "预告") == {"2026-04-16"}

    df = pd.DataFrame({"公告日期": ["2026-04-16 19:00:00", "2026-04-18"], "股票代码": ["000001", "000002"]})
    filtered = EarningsEngine._filter_candidates_by_publish_date(df, "公告日期", "2026-04-16", "预告")
    assert filtered["股票代码"].tolist() == ["000001"]
    assert EarningsEngine._filter_candidates_by_publish_date(df, "missing", "2026-04-16", "预告").empty

    assert EarningsEngine._resolve_guidance_est_profit({"预测数值": 12.0, "预测指标": "扣非净利润"}) == (12.0, "扣非净利润")
    fallback_profit, fallback_metric = EarningsEngine._resolve_guidance_est_profit(
        {
            "预测数值": engine_module.np.nan,
            "预计扣非净利润-下限": 100.0,
            "预计扣非净利润-上限": 300.0,
            "预计净利润-下限": 50.0,
            "预计净利润-上限": 70.0,
        }
    )
    assert fallback_profit == 200.0
    assert fallback_metric == "扣非"

    guidance = engine._build_guidance_candidate(
        {
            "股票代码": "7",
            "股票简称": "Alpha",
            "公告日期": "2026-04-17 21:00:00",
            "预测数值": 100.0,
            "预测指标": "扣非净利润",
            "预告类型": "预增",
        },
        "20260331",
        "2026-04-16",
    )
    assert guidance["股票代码"] == "000007"
    assert guidance["源公告日期"] == "2026-04-17"
    assert guidance["is_koufei"] is True
    assert engine._build_guidance_candidate({"预测数值": engine_module.np.nan}, "20260331", "2026-04-16") is None
    assert engine._build_guidance_candidate({"预测数值": 100.0, "预测指标": "净利润"}, "20260331", "2026-04-16") is None

    report = engine._build_report_candidate(
        {"股票代码": "8", "股票简称": "Beta", "最新公告日期": "2026-04-17 20:00:00", "净利润-净利润": 200.0},
        report_date="20260331",
        target_publish_date="2026-04-16",
        data_type="财报",
        date_col="最新公告日期",
        tone="正式出炉",
    )
    assert report["股票代码"] == "000008"
    assert report["源公告日期"] == "2026-04-17"
    assert report["is_koufei"] is False
    assert engine._build_report_candidate(
        {"股票代码": "8", "净利润-净利润": engine_module.np.nan},
        report_date="20260331",
        target_publish_date="2026-04-16",
        data_type="财报",
        date_col="最新公告日期",
        tone="正式出炉",
    ) is None


def test_earnings_universe_fingerprints_and_threshold_edges():
    engine = _build_engine()

    assert engine._record_to_fingerprint({"股票代码": "7", "报告期": "20260331", "数据类型": "财报"}) == "SHOCK_000007_20260331_财报"
    assert engine._record_to_fingerprint({"代码": "8", "报告期": "20260331", "类型": "快报"}) == "SHOCK_000008_20260331_快报"
    assert engine._record_to_fingerprint({}) is None

    engine.stock_universe_provider = lambda: ["7", "300308", "", None]
    assert engine._resolve_stock_universe_codes() == {"000007", "300308"}
    engine.stock_universe_provider = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
    assert engine._resolve_stock_universe_codes() == set()
    engine.stock_universe_provider = None
    assert engine._filter_records_to_stock_universe([{"股票代码": "000007"}]) == [{"股票代码": "000007"}]

    engine.stock_universe_provider = lambda: ["000007"]
    assert engine._filter_records_to_stock_universe([{"股票代码": "000007"}, {"股票代码": "000008"}]) == [{"股票代码": "000007"}]

    engine.seen_fingerprints = {"SHOCK_000007_20260331_财报"}
    pending = engine._pending_surprise_candidates(
        [
            {"股票代码": "000007", "报告期": "20260331", "数据类型": "财报"},
            {"股票代码": "000008", "报告期": "20260331", "数据类型": "财报"},
            {"股票代码": "900001", "报告期": "20260331", "数据类型": "财报"},
            {"股票代码": "300308", "报告期": "20260331", "数据类型": "财报"},
        ],
        {"000008", "300308"},
    )
    assert [candidate["股票代码"] for candidate in pending] == ["000008", "300308"]

    assert EarningsEngine._surprise_result_passes_threshold(
        {"环比增速_百分比": 30.0, "单季净利润_新增": 1.0, "同比增速_百分比": 1.0}
    ) is True
    assert EarningsEngine._surprise_result_passes_threshold(
        {"环比增速_百分比": 29.9, "单季净利润_新增": 1.0, "同比增速_百分比": 1.0}
    ) is False
    assert EarningsEngine._surprise_result_passes_threshold(
        {"环比增速_百分比": 30.0, "单季净利润_新增": 0.0, "同比增速_百分比": 1.0}
    ) is False
    assert EarningsEngine._surprise_result_passes_threshold(
        {"环比增速_百分比": 30.0, "单季净利润_新增": 1.0, "同比增速_百分比": 0.0}
    ) is False


def test_quick_report_cum_profit_uses_latest_revision_and_cache(monkeypatch):
    engine = _build_engine()
    calls = []
    quick_df = pd.DataFrame(
        [
            {"股票代码": "000007", "净利润-净利润": "100", "公告日期": "2026-04-01"},
            {"股票代码": "7", "净利润-净利润": "120", "公告日期": "2026-04-02"},
            {"股票代码": "000008", "净利润-净利润": "88", "公告日期": "2026-04-01"},
        ]
    )

    def fake_safe_fetch(fetch_func, *args, **kwargs):
        calls.append(kwargs["date"])
        return quick_df.copy()

    monkeypatch.setattr(engine_module, "safe_ak_fetch", fake_safe_fetch)

    assert engine._get_quick_report_cum_profit("7", "20260331") == 120.0
    assert engine._get_quick_report_cum_profit("000008", "20260331") == 88.0
    assert pd.isna(engine._get_quick_report_cum_profit("000009", "20260331"))
    assert calls == ["20260331"]


def test_compute_single_quarter_qoq_returns_q1_growth(monkeypatch):
    engine = _build_engine()
    financial_df = pd.DataFrame(
        [
            {"报告期": "2025-12-31", "扣除非经常性损益后的净利润": "80"},
            {"报告期": "2025-09-30", "扣除非经常性损益后的净利润": "60"},
            {"报告期": "2025-03-31", "扣除非经常性损益后的净利润": "40"},
        ]
    )

    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: financial_df.copy())

    result = engine.compute_single_quarter_qoq("000007", 100.0, "20260331", is_koufei=True)

    assert result["单季净利润_新增"] == 100.0
    assert result["单季净利润_上期"] == 20.0
    assert result["单季净利润_去年同期"] == 40.0
    assert result["环比增速_百分比"] == 400.0
    assert result["同比增速_百分比"] == 150.0
    assert result["error"] is None
    assert "上季基数口径" not in result


def test_compute_single_quarter_qoq_uses_quick_report_backfill(monkeypatch):
    engine = _build_engine()
    financial_df = pd.DataFrame(
        [
            {"报告期": "2025-06-30", "扣除非经常性损益后的净利润": "60"},
            {"报告期": "2025-03-31", "扣除非经常性损益后的净利润": "25"},
        ]
    )

    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: financial_df.copy())
    monkeypatch.setattr(engine, "_get_quick_report_cum_profit", lambda target_code, report_date: 30.0 if report_date == "20260331" else engine_module.np.nan)

    result = engine.compute_single_quarter_qoq("000007", 70.0, "20260630", is_koufei=True)

    assert result["单季净利润_新增"] == 40.0
    assert result["单季净利润_上期"] == 30.0
    assert result["单季净利润_去年同期"] == 35.0
    assert result["环比增速_百分比"] == pytest.approx(33.33)
    assert result["同比增速_百分比"] == pytest.approx(14.29)
    assert result["上季基数口径"] == "快报净利润回填"
    assert result["error"] is None


def test_compute_single_quarter_qoq_error_edges(monkeypatch):
    engine = _build_engine()

    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: pd.DataFrame())
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331") == {"error": "无历史"}

    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: pd.DataFrame({"报告期": ["2025-12-31"]}))
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331", must_wait_ths=True) == {"error": "无找点字段"}
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331", must_wait_ths=False) == {"error": "无利润字段"}

    no_current_df = pd.DataFrame(
        [
            {"报告期": "2025-12-31", "扣除非经常性损益后的净利润": "80"},
            {"报告期": "2025-09-30", "扣除非经常性损益后的净利润": "60"},
        ]
    )
    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: no_current_df.copy())
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331", must_wait_ths=True) == {"error": "THS_PENDING"}

    zero_base_df = pd.DataFrame(
        [
            {"报告期": "2025-12-31", "扣除非经常性损益后的净利润": "80"},
            {"报告期": "2025-09-30", "扣除非经常性损益后的净利润": "80"},
            {"报告期": "2025-03-31", "扣除非经常性损益后的净利润": "40"},
        ]
    )
    monkeypatch.setattr(engine_module, "safe_ak_fetch", lambda fetch_func, *args, **kwargs: zero_base_df.copy())
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331") == {"error": "基数0"}

    monkeypatch.setattr(
        engine_module,
        "safe_ak_fetch",
        lambda fetch_func, *args, **kwargs: (_ for _ in ()).throw(ValueError("bad fetch")),
    )
    assert engine.compute_single_quarter_qoq("000007", 100.0, "20260331") == {"error": "抛锚"}


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
        "now",
        classmethod(lambda cls, market="CN": pd.Timestamp("2026-04-16 08:31:02").to_pydatetime()),
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
    assert row["揭晓日"] == "2026-04-16"
    assert row["发现时间"] == "2026-04-16T08:31:02"
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
