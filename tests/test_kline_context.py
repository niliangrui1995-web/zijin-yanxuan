from ui.kline_window_qt import (
    _build_kline_theme_colors,
    _build_kline_summary_items,
    _format_kline_market_badge,
    resolve_kline_vcp_context,
)


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
    assert _format_kline_market_badge("300308") == "A股"
    assert _format_kline_market_badge("2330.TW") == "台股"
    assert _format_kline_market_badge("AAPL") == "美股"


def test_build_kline_summary_items_formats_range_and_rps():
    summary = _build_kline_summary_items(
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
    colors = _build_kline_theme_colors()

    assert colors["vcp_line"]
    assert colors["vcp_line_soft"]
    assert colors["vcp_area"]
    assert colors["vcp_guide"]
    assert colors["vcp_breakout_bg"]
