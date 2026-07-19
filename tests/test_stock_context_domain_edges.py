# -*- coding: utf-8 -*-
from __future__ import annotations

from domains.stock_context import signal_builders as builders
from domains.stock_context.models import (
    StockContextSignalIndex,
    StockContextSnapshot,
    StockSignal,
    coerce_stock_signal,
)


def test_coerce_stock_signal_accepts_supported_inputs_and_rejects_missing_codes():
    existing = StockSignal(
        code=" 000001 ",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="命中",
    )
    assert coerce_stock_signal(existing) is existing
    assert coerce_stock_signal(StockSignal("", "scan", "vcp_scan", "命中")) is None
    assert coerce_stock_signal(None) is None
    assert coerce_stock_signal({"名称": "无代码"}) is None

    explicit_payload = coerce_stock_signal(
        {
            "code": " 000002 ",
            "name": " ",
            "名称": "万科A",
            "source_tab": None,
            "source_label": "自选股",
            "signal_type": "manual",
            "summary": "关注",
            "numeric_value": "12.5",
            "payload": {"标签": ["低估"]},
        }
    )
    assert explicit_payload is not None
    assert explicit_payload.code == "000002"
    assert explicit_payload.name == "万科A"
    assert explicit_payload.source_tab == ""
    assert explicit_payload.numeric_value == 12.5
    assert explicit_payload.payload == {"标签": ["低估"]}

    inferred_payload = coerce_stock_signal(
        {
            "代码": "600000",
            "名称": "浦发银行",
            "source_tab": "watchlist",
            "numeric_value": "不可转换",
            "自定义字段": 7,
        }
    )
    assert inferred_payload is not None
    assert inferred_payload.numeric_value is None
    assert inferred_payload.payload == {"自定义字段": 7}


def test_stock_context_snapshot_freezes_and_thaws_nested_sets():
    snapshot = StockContextSnapshot(
        source_rows={"scan": ({"代码": "000001", "标签": {"强势", "放量"}},)},
        cached_source_rows={"scan": ({"代码": "000002", "标签": frozenset({"回踩"})},)},
    )

    assert snapshot.rows_for("scan")[0]["标签"] == {"强势", "放量"}
    assert snapshot.cached_rows_for("scan")[0]["标签"] == {"回踩"}
    assert snapshot.rows_for("missing") == []


def test_stock_context_signal_index_is_immutable_and_queries_one_code():
    nested = {"dates": ["2026-07-17"]}
    signal = StockSignal(
        code=" 000001 ",
        source_tab="earnings",
        signal_type="earnings",
        summary="业绩异动",
        payload=nested,
    )

    index = StockContextSignalIndex.from_context(
        {"000001": [signal], "": [signal], "noise": [object()]}
    )
    nested["dates"].append("2026-07-18")

    assert index.codes == ("000001",)
    assert index.signals_for("000001")[0].payload["dates"] == ("2026-07-17",)
    assert index.signals_for("missing") == ()
    assert index.signal_count == 1


def test_block_trade_signal_covers_direction_fallback_and_match_rules():
    assert builders.safe_float(object(), 3.5) == 3.5
    assert builders.compact_block_trade_branch("", ("深股通",)) == ""
    assert builders.compact_block_trade_branch("深股通专用席位", ("深股通",)) == "深股通"
    assert builders.compact_block_trade_branch("机构专用席位", ("深股通",)) == "机构专用"
    assert builders.compact_block_trade_branch("普通营业部", ("深股通",)) == ""

    assert builders.build_watchlist_block_trade_signal("买入", "深股通", "", 0, ("深股通",)) == ("", 0.0)
    assert builders.build_watchlist_block_trade_signal("买入", "深股通", "", 100, ("深股通",)) == (
        "深股通买入100万",
        100,
    )
    assert builders.build_watchlist_block_trade_signal("买入", "普通席位", "机构专用", 80, ()) == (
        "机构专用卖出80万",
        80,
    )
    assert builders.build_watchlist_block_trade_signal("买入", "普通席位", "普通席位2", 60, ()) == (
        "",
        0.0,
    )
    assert builders.build_watchlist_block_trade_signal("卖出", "机构专用", "普通席位", 50, ()) == (
        "机构专用买入50万",
        50,
    )
    assert builders.build_watchlist_block_trade_signal("卖出", "", "深股通", 40, ("深股通",)) == (
        "深股通卖出40万",
        40,
    )
    assert builders.build_watchlist_block_trade_signal("无方向", "同一席位", "同一席位", 30, ()) == (
        "大宗对倒 30万",
        30,
    )
    assert builders.build_watchlist_block_trade_signal("无方向", "席位A", "席位B", 20, ()) == ("", 0.0)


def test_prepare_earnings_cache_rows_normalizes_existing_and_raw_records():
    assert builders.prepare_earnings_cache_rows(None) == []
    assert builders.prepare_earnings_cache_rows({"records": "bad"}) == []

    rows = builders.prepare_earnings_cache_rows(
        {
            "records": [
                None,
                {"代码": "000001", "揭晓日": "2026-07-15"},
                {"代码": "000002", "发现时间": "2026-07-16"},
                {
                    "股票代码": "3",
                    "股票名称": "测试股份",
                    "环比增速_百分比": "8.5",
                    "数据类型": "业绩快报",
                    "报告期": "2026Q2",
                    "公告日期": "2026-07-17",
                },
                {"股票代码": ""},
            ]
        },
        "2026-07-17T09:30:00",
    )

    assert rows[0]["发现时间"] == "2026-07-17T09:30:00"
    assert rows[0]["揭晓日"] == "2026-07-15"
    assert rows[1]["发现时间"] == "2026-07-16"
    assert "揭晓日" not in rows[1]
    assert rows[2] == {
        "股票代码": "3",
        "股票名称": "测试股份",
        "环比增速_百分比": "8.5",
        "数据类型": "业绩快报",
        "报告期": "2026Q2",
        "公告日期": "2026-07-17",
        "代码": "000003",
        "名称": "测试股份",
        "环比%": "8.5",
        "类型": "业绩快报",
        "报告名称": "业绩快报",
        "揭晓日": "2026-07-17",
        "发现时间": "2026-07-17T09:30:00",
        "触发日期": "2026-07-17",
    }


def test_earnings_discovery_lookup_skips_invalid_rows_and_builds_fallback_keys():
    assert builders.earnings_discovery_lookup(None) == {}
    assert builders.earnings_discovery_lookup({"records": "bad"}) == {}

    lookup = builders.earnings_discovery_lookup(
        {
            "records": [
                None,
                {"报告期": "2026Q1"},
                {"代码": "1", "报告期": "2026Q1"},
                {
                    "股票代码": "2",
                    "报告期": "2026Q2",
                    "数据类型": "业绩预告",
                    "发现时间": "2026-07-16",
                },
                {"代码": "000003", "发现时间": "2026-07-15"},
            ]
        }
    )

    assert lookup[("000002", "2026Q2", "业绩预告")] == "2026-07-16"
    assert lookup[("000002", "2026Q2", "")] == "2026-07-16"
    assert lookup[("000002", "", "")] == "2026-07-16"
    assert lookup[("000003", "", "")] == "2026-07-15"
    assert builders.lookup_earnings_discovery(
        lookup,
        code="000002",
        report_period="2026Q2",
        report_type="未知类型",
    ) == "2026-07-16"
    assert builders.lookup_earnings_discovery(
        lookup,
        code="999999",
        report_period="",
        report_type="",
    ) == ""
    assert builders.earnings_report_label({"财报名称": "业绩预告"}) == "业绩预告"
    assert builders.earnings_report_label({"报告期": "2026-05-01"}) == "2026-05-01"


def test_fund_holding_store_rows_filter_and_format_latest_eligible_changes():
    assert builders.format_fund_holding_pct("1.236") == "1.24%"
    assert builders.format_fund_holding_pct("bad") == "--"
    assert builders.format_fund_holding_amount(25000) == "+2.50"
    assert builders.format_fund_holding_amount(-10000) == "-1.00"
    assert builders.format_fund_holding_amount("bad") == "--"

    formatted = builders.format_fund_holding_store_rows(
        {"QFII": "2026Q2", "FUND": "2026Q2"},
        [
            {"stock_code": "", "subject_code": "QFII", "quarter_key": "2026Q2", "change_type": "新进"},
            {"stock_code": "000001", "subject_code": "QFII", "quarter_key": "2026Q2", "change_type": "减持"},
            {"stock_code": "000001", "subject_code": "QFII", "quarter_key": "2026Q1", "change_type": "增持"},
            {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "subject_code": "QFII",
                "subject_name": "QFII机构",
                "capital_attribute": "",
                "quarter_key": "2026Q2",
                "change_type": "新进",
                "curr_ratio_pct": 1.5,
                "delta_hold_num_shares": 20000,
            },
            {
                "stock_code": "000002",
                "stock_name": "万科A",
                "subject_code": "FUND",
                "subject_name": "公募基金",
                "capital_attribute": "长期资金",
                "quarter_key": "2026Q2",
                "change_type": "增持",
                "curr_ratio_pct": None,
                "delta_hold_num_shares": -10000,
            },
        ],
        qfii_subject_code="QFII",
        unmarked_capital_attribute="未标注",
    )

    assert formatted == [
        {
            "代码": "000001",
            "名称": "平安银行",
            "主体": "QFII机构",
            "资金属性": "",
            "主体代码": "QFII",
            "季度": "2026Q2",
            "变化类型": "新进",
            "本期占比": "1.50%",
            "持股变化": "+2.00",
            "_is_latest_subject_quarter": True,
        },
        {
            "代码": "000002",
            "名称": "万科A",
            "主体": "公募基金",
            "资金属性": "长期资金",
            "主体代码": "FUND",
            "季度": "2026Q2",
            "变化类型": "增持",
            "本期占比": "0.00%",
            "持股变化": "-1.00",
            "_is_latest_subject_quarter": True,
        },
    ]


def test_lhb_rows_keep_raw_date_and_signals_skip_blank_or_duplicate_codes():
    normalized = builders.normalize_lhb_pool_rows(
        [
            {"代码": "000001", "最近上榜": "20260717", "上榜净买额(万)": -20},
            {"代码": "000002", "最近上榜": "07-16"},
        ]
    )
    assert normalized[0]["_最近上榜_raw"] == "20260717"
    assert normalized[0]["最近上榜"] == "07-17"
    assert normalized[1]["最近上榜"] == "07-16"

    signals = builders.build_lhb_signals(
        [
            {"代码": ""},
            normalized[0],
            {**normalized[0], "上榜净买额(万)": 99},
            normalized[1],
        ]
    )
    assert [signal.code for signal in signals] == ["000001", "000002"]
    assert signals[0].summary.startswith("07-17 | 净卖20万")
    assert signals[0].observed_at == "20260717"


def test_watchlist_radar_target_filter_and_empty_signal_handlers():
    empty_lhb = StockSignal(
        code="000001",
        source_tab="lhb",
        signal_type="lhb",
        summary="",
    )
    unsupported = StockSignal(
        code="000001",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="命中",
    )
    included = StockSignal(
        code="000002",
        source_tab="na_daily",
        signal_type="subsector",
        summary="算力",
    )

    remark, subsector, block, earnings, lhb, bundle = builders.build_watchlist_radar_data(
        [empty_lhb, unsupported, included],
        target_codes={"000001"},
        rps_bundle={"rps250": {}},
    )
    assert (remark, subsector, block, earnings, lhb) == ({}, {}, {}, {}, {})
    assert bundle == {"rps250": {}}
