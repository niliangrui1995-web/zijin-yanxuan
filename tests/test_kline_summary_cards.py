# -*- coding: utf-8 -*-

from ui.kline_chart_payload import build_kline_summary_cards


def test_watchlist_summary_card_prefers_structured_amount_and_qoq_metrics():
    cards = build_kline_summary_cards(
        {
            "__source_tab_key": "watchlist",
            "来源": "战报",
            "细分板块": "晶圆制造",
            "催化剂": "CMP抛光垫国产替代突破",
            "大宗交易": "机构专用买入2709万",
            "大宗交易金额(万)": 2709,
            "业绩异动": "32.5%",
            "业绩环比%": 32.5,
            "龙虎榜": "04-20 | 净买1200万",
            "龙虎榜净额(万)": 1200,
            "RPS强度": "83/90",
        },
        is_fav=True,
    )

    event_row = cards[1]["rows"][1]

    assert event_row["label"] == "异动"
    assert "大宗 2,709万" in event_row["value"]
    assert "环比 32.5%" in event_row["value"]
    assert "龙虎 净买1,200万" in event_row["value"]


def test_generic_kline_summary_formats_plain_pct_value():
    cards = build_kline_summary_cards(
        {
            "代码": "002384",
            "名称": "东山精密",
            "现价": "12.61",
            "涨幅": 6.6988159322922,
        }
    )

    pct_row = cards[0]["rows"][1]

    assert pct_row["label"] == "涨幅"
    assert pct_row["value"] == "6.70%"


def test_stock_candidate_kline_summary_shows_sector_from_context():
    cards = build_kline_summary_cards(
        {
            "__source_tab_key": "stock_candidates",
            "代码": "688629",
            "名称": "华丰科技",
            "市值": "283亿",
            "细分板块": "高速连接器",
        }
    )

    sector_row = cards[1]["rows"][1]

    assert sector_row["label"] == "板块"
    assert sector_row["value"] == "高速连接器"
