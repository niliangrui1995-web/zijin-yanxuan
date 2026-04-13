import pandas as pd

from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_summary_items,
    build_kline_theme_colors,
    build_kline_window_palette,
    format_kline_market_badge,
    resolve_kline_vcp_context,
)
from ui.theme import THEME_YUEBAI


def test_resolve_kline_vcp_context_merges_watchlist_and_scan_results():
    item_data = {"代码": "300308", "名称": "中际旭创"}
    watchlist_entry = {
        "区间最高价": 155.8,
        "区间最低点": 132.4,
        "_peak_dates": ["2026-03-18", "2026-03-27"],
        "触发日期": "2026-04-01",
    }
    scan_results = [
        {
            "代码": "300308",
            "名称": "中际旭创",
            "触发日期": "2026-04-09",
            "_peak_dates": ["2026-03-20", "2026-04-02"],
        }
    ]

    resolved = resolve_kline_vcp_context(
        code="300308",
        name="中际旭创",
        item_data=item_data,
        watchlist_entry=watchlist_entry,
        scan_results=scan_results,
    )

    assert resolved["代码"] == "300308"
    assert resolved["名称"] == "中际旭创"
    assert resolved["区间最高价"] == 155.8
    assert resolved["区间最低点"] == 132.4
    assert resolved["触发日期"] == "2026-04-09"
    assert resolved["_peak_dates"] == ["2026-03-20", "2026-04-02"]


def test_kline_market_badge_formats_common_markets():
    assert format_kline_market_badge("300308") == "A股"
    assert format_kline_market_badge("2330.TW") == "台股"
    assert format_kline_market_badge("AAPL") == "美股"


def test_build_kline_summary_items_formats_range_and_rps():
    summary = build_kline_summary_items(
        {
            "突破状态": "放量突破",
            "触发日期": "2026-04-09 10:31:00",
            "区间最高价": 155.8,
            "区间最低点": 132.4,
            "区间振幅": "17.7%",
            "RPS强度": "98/99",
        },
        is_fav=True,
    )

    assert summary["形态"] == "放量突破"
    assert summary["触发"] == "2026-04-09"
    assert summary["区间"] == "132.40 - 155.80"
    assert summary["振幅"] == "17.7%"
    assert summary["RPS"] == "98/99"
    assert summary["关注"] == "已关注"


def test_kline_theme_colors_include_vcp_overlay_tokens():
    colors = build_kline_theme_colors()

    assert colors["vcp_line"]
    assert colors["vcp_line_soft"]
    assert colors["vcp_area"]
    assert colors["vcp_guide"]
    assert colors["vcp_breakout_bg"]


def test_kline_window_palette_keeps_yuebai_top_area_unified():
    palette = build_kline_window_palette(THEME_YUEBAI, is_dark=False)

    assert palette["toolbar_bg"] == THEME_YUEBAI["BG_ELEVATED"]
    assert palette["toolbar_bg"] == palette["widget_bg"]
    assert palette["toolbar_bg"] == palette["summary_bg"]
    assert palette["toolbar_bg"] == palette["chart_bg"]


def test_build_kline_echarts_payload_includes_vcp_overlay():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.8, 11.2, 11.8],
            "low": [9.9, 10.2, 10.7],
            "close": [10.6, 11.0, 11.6],
            "volume": [10000, 12000, 15000],
            "MACD": [0.1, 0.2, 0.3],
            "MACD_Signal": [0.05, 0.15, 0.25],
            "MACD_Hist": [0.05, 0.05, 0.05],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
    )

    payload = build_kline_echarts_payload(
        df,
        code="300308",
        name="中际旭创",
        vcp_data={
            "触发日期": "2026-04-09",
            "区间最高价": 11.8,
            "区间最低点": 9.9,
            "_peak_dates": ["2026-04-07", "2026-04-08"],
        },
    )

    assert payload["title"] == "中际旭创 (300308) 日线"
    assert payload["dates"][-1] == "2026-04-09"
    assert payload["klines"][-1] == [11.0, 11.6, 10.7, 11.8]
    assert payload["vcpMarkers"]
    assert payload["vcpLines"]
    assert payload["vcpArea"]
