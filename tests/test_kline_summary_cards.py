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
            "RPS强度": "83/90",
        },
        is_fav=True,
    )

    event_row = cards[1]["rows"][1]

    assert event_row["label"] == "异动"
    assert "大宗 2,709万" in event_row["value"]
    assert "环比 32.5%" in event_row["value"]
    assert "龙虎榜" in event_row["value"]
