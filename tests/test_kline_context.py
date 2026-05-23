import pandas as pd

from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_html,
    build_kline_summary_items,
    build_kline_theme_colors,
    build_kline_window_palette,
    format_kline_market_badge,
    resolve_kline_vcp_context,
)
from ui.theme import THEME_YAOHEI, THEME_YUEBAI, theme_manager
from ui.workspaces.stock_signal import StockSignal


def test_resolve_kline_vcp_context_keeps_non_scan_source_isolated():
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
    assert resolved["触发日期"] == "2026-04-01"
    assert resolved["_peak_dates"] == ["2026-03-18", "2026-03-27"]


def test_resolve_kline_vcp_context_merges_scan_results_for_scan_source():
    resolved = resolve_kline_vcp_context(
        code="300308",
        name="中际旭创",
        item_data={"代码": "300308", "名称": "中际旭创", "__source_tab_key": "scan"},
        watchlist_entry={},
        scan_results=[
            {
                "代码": "300308",
                "名称": "中际旭创",
                "触发日期": "2026-04-09",
                "_peak_dates": ["2026-03-20", "2026-04-02"],
            }
        ],
    )

    assert resolved["触发日期"] == "2026-04-09"
    assert resolved["_peak_dates"] == ["2026-03-20", "2026-04-02"]
    assert resolved["_vcp_overlay_allowed"] is True


def test_resolve_kline_vcp_context_uses_watchlist_vcp_fields_without_scan_cache():
    resolved = resolve_kline_vcp_context(
        code="002975",
        name="博杰股份",
        item_data={
            "代码": "002975",
            "名称": "博杰股份",
            "__source_tab_key": "watchlist",
            "来源标签": ["业绩", "AI产业链"],
            "RPS强度": "97/90",
        },
        watchlist_entry={
            "代码": "002975",
            "名称": "博杰股份",
            "区间最高价": 88.0,
            "区间最低点": 70.0,
            "_peak_dates": ["20260407", "20260410"],
        },
        scan_results=[
            {
                "代码": "002975",
                "名称": "博杰股份",
                "RPS强度": "93/95",
                "触发日期": "20260416",
                "区间最高价": 91.0,
                "区间最低点": 65.6,
                "_peak_dates": ["20251127", "20260123", "20260226"],
            }
        ],
    )

    assert resolved["RPS强度"] == "97/90"
    assert resolved["来源标签"] == ["业绩", "AI产业链"]
    assert resolved["区间最高价"] == 88.0
    assert resolved["区间最低点"] == 70.0
    assert resolved["_peak_dates"] == ["20260407", "20260410"]
    assert "_vcp_overlay_allowed" not in resolved


def test_resolve_kline_vcp_context_does_not_pull_scan_cache_for_plain_watchlist():
    resolved = resolve_kline_vcp_context(
        code="002975",
        name="博杰股份",
        item_data={
            "代码": "002975",
            "名称": "博杰股份",
            "__source_tab_key": "watchlist",
            "来源标签": ["业绩", "AI产业链"],
            "RPS强度": "97/90",
        },
        watchlist_entry={},
        scan_results=[
            {
                "代码": "002975",
                "名称": "博杰股份",
                "RPS强度": "93/95",
                "触发日期": "20260416",
                "区间最高价": 91.0,
                "区间最低点": 65.6,
                "_peak_dates": ["20251127", "20260123", "20260226"],
            }
        ],
    )

    assert resolved["RPS强度"] == "97/90"
    assert "区间最高价" not in resolved
    assert "区间最低点" not in resolved
    assert "_peak_dates" not in resolved


def test_resolve_kline_vcp_context_uses_embedded_stock_detail_scan_signal():
    scan_signal = StockSignal(
        code="002975",
        name="博杰股份",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="VCP扫描",
        observed_at="20260416",
        payload={
            "代码": "002975",
            "名称": "博杰股份",
            "RPS强度": "93/95",
            "区间最高价": 91.0,
            "区间最低点": 65.6,
            "_peak_dates": ["20251127", "20260123", "20260226"],
        },
    )

    resolved = resolve_kline_vcp_context(
        code="002975",
        name="博杰股份",
        item_data={
            "代码": "002975",
            "名称": "博杰股份",
            "__source_tab_key": "watchlist",
            "来源标签": ["业绩", "AI产业链"],
            "RPS强度": "97/90",
            "_signals": [scan_signal],
        },
        watchlist_entry={},
        scan_results=[],
    )

    assert resolved["RPS强度"] == "97/90"
    assert resolved["source_tab"] == "scan"
    assert resolved["signal_type"] == "vcp_scan"
    assert resolved["触发日期"] == "20260416"
    assert resolved["区间最高价"] == 91.0
    assert resolved["区间最低点"] == 65.6
    assert resolved["_vcp_overlay_allowed"] is True


def test_resolve_kline_vcp_context_keeps_premerged_scan_signal_payload():
    key_code = "\u4ee3\u7801"
    key_name = "\u540d\u79f0"
    key_rps = "RPS\u5f3a\u5ea6"
    key_trigger = "\u89e6\u53d1\u65e5\u671f"
    key_high = "\u533a\u95f4\u6700\u9ad8\u4ef7"
    key_low = "\u533a\u95f4\u6700\u4f4e\u70b9"

    resolved = resolve_kline_vcp_context(
        code="002975",
        name="BoJie",
        item_data={
            key_code: "002975",
            key_name: "BoJie",
            "__source_tab_key": "watchlist",
            "source_tab": "scan",
            "signal_type": "vcp_scan",
            "_vcp_overlay_allowed": True,
            key_rps: "97/90",
            key_trigger: "20260416",
            key_high: 91.0,
            key_low: 65.6,
            "_peak_dates": ["20251127", "20260123", "20260226", "20260410"],
        },
        watchlist_entry={},
        scan_results=[
            {
                key_code: "002975",
                key_name: "BoJie",
                key_rps: "93/95",
                key_trigger: "20260410",
                key_high: 999.0,
                key_low: 111.0,
                "_peak_dates": ["20260410"],
            }
        ],
    )

    assert resolved[key_rps] == "97/90"
    assert resolved[key_trigger] == "20260416"
    assert resolved[key_high] == 91.0
    assert resolved[key_low] == 65.6
    assert resolved["_peak_dates"] == ["20251127", "20260123", "20260226", "20260410"]


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
    assert colors["scrollbar_handle"]
    assert colors["scrollbar_handle_hover"]


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


def test_build_kline_echarts_payload_matches_compact_vcp_dates():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.8, 11.2, 11.8],
            "low": [9.9, 10.2, 10.7],
            "close": [10.6, 11.0, 11.6],
            "volume": [10000, 12000, 15000],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
    )

    payload = build_kline_echarts_payload(
        df,
        code="300308",
        name="中际旭创",
        vcp_data={
            "触发日期": "20260409",
            "区间最高价": 99.0,
            "区间最低点": 1.0,
            "_peak_dates": ["20260407", "20260408"],
        },
    )

    assert payload["vcpMarkers"][0]["coord"] == [2, 11.8]
    assert payload["vcpArea"][0][0]["yAxis"] == 9.9
    assert payload["vcpArea"][0][1]["xAxis"] == "2026-04-09"
    assert payload["vcpArea"][0][1]["yAxis"] == 11.8


def test_build_kline_echarts_payload_marks_final_vcp_window_to_trigger_date():
    key_trigger = "\u89e6\u53d1\u65e5\u671f"
    key_high = "\u533a\u95f4\u6700\u9ad8\u4ef7"
    key_low = "\u533a\u95f4\u6700\u4f4e\u70b9"
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5, 10.2, 12.8],
            "high": [11.0, 10.8, 10.6, 14.0],
            "low": [9.8, 10.0, 9.9, 12.5],
            "close": [10.6, 10.1, 10.4, 13.8],
            "volume": [10000, 9000, 8000, 30000],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"]),
    )

    payload = build_kline_echarts_payload(
        df,
        code="002975",
        name="BoJie",
        vcp_data={
            key_trigger: "20260410",
            key_high: 99.0,
            key_low: 1.0,
            "_peak_dates": ["20260407", "20260409"],
            "_vcp_overlay_allowed": True,
        },
    )

    assert payload["vcpMarkers"][0]["coord"] == [3, 14.0]
    assert payload["vcpArea"][0][0]["xAxis"] == "2026-04-07"
    assert payload["vcpArea"][0][0]["yAxis"] == 9.8
    assert payload["vcpArea"][0][1]["xAxis"] == "2026-04-10"
    assert payload["vcpArea"][0][1]["yAxis"] == 14.0
    horizontal_lines = [line for line in payload["vcpLines"] if line[0]["yAxis"] == line[1]["yAxis"]]
    assert horizontal_lines == [
        [
            {"xAxis": "2026-04-07", "yAxis": 14.0},
            {"xAxis": "2026-04-10", "yAxis": 14.0},
        ],
        [
            {"xAxis": "2026-04-07", "yAxis": 9.8},
            {"xAxis": "2026-04-10", "yAxis": 9.8},
        ],
    ]


def test_build_kline_echarts_payload_skips_vcp_overlay_for_generic_event_date():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.8, 11.2, 11.8],
            "low": [9.9, 10.2, 10.7],
            "close": [10.6, 11.0, 11.6],
            "volume": [10000, 12000, 15000],
        },
        index=pd.to_datetime(["2026-04-07", "2026-04-08", "2026-04-09"]),
    )

    payload = build_kline_echarts_payload(
        df,
        code="002975",
        name="博杰股份",
        vcp_data={
            "日期": "2026-04-09",
            "来源标签": ["业绩", "AI产业链"],
            "业绩异动": "一季度 63.04%",
        },
    )

    assert payload["vcpMarkers"] is None
    assert payload["vcpLines"] is None
    assert payload["vcpArea"] is None


def test_build_kline_html_hides_echarts_tooltip_panel():
    html = build_kline_html(
        title="测试",
        echarts_data={
            "dates": [],
            "klines": [],
            "vols": [],
            "ma10": [],
            "ma20": [],
            "ma50": [],
            "ma150": [],
            "ma200": [],
            "volMa20": [],
            "macd": [],
            "diff": [],
            "dea": [],
        },
        echarts_js_path=r"D:\fake\echarts.min.js",
        theme_colors=build_kline_theme_colors(),
    )

    assert "showContent: false" in html
    assert "width: 1.2" in html
    assert "opacity: 0.92" in html
    assert "borderRadius: 4" in html
    assert "type: [4, 4]" in html
    assert "zoomOnMouseWheel: false" in html
    assert "requestAnimationFrame" in html
    assert "id: 'pointerClose'" in html
    assert "stateAnimation: { duration: 0 }" in html
    assert "barMaxWidth: 18" in html
    assert html.count("smooth: false") >= 5
    assert "const trendColor = pct >= 0 ? upColor : downColor;" in html
    assert "closeEl.style.color = trendColor;" in html
    assert "pctEl.style.color = trendColor;" in html
    assert "splitLine: { show: false }" in html
    assert "type: 'effectScatter'" in html
    assert "rippleEffect" in html
    assert "markPoint:" not in html


def test_yaohei_kline_html_syncs_canvas_scrollbar_and_tabular_nums(monkeypatch):
    monkeypatch.setattr(theme_manager, "_current_name", THEME_YAOHEI["name"])
    colors = build_kline_theme_colors()
    html = build_kline_html(
        title="test",
        echarts_data={
            "dates": [],
            "klines": [],
            "vols": [],
            "ma10": [],
            "ma20": [],
            "ma50": [],
            "ma150": [],
            "ma200": [],
            "volMa20": [],
            "macd": [],
            "diff": [],
            "dea": [],
        },
        echarts_js_path=r"D:\fake\echarts.min.js",
        theme_colors=colors,
    )

    assert colors["bg_canvas"] == THEME_YAOHEI["BG_CANVAS"] == "#0B1017"
    assert colors["datazoom_handle"] == THEME_YAOHEI["SCROLLBAR_HANDLE"]
    assert colors["scrollbar_handle"] == THEME_YAOHEI["SCROLLBAR_HANDLE"]
    assert colors["scrollbar_handle_hover"] == THEME_YAOHEI["SCROLLBAR_HANDLE_HOVER"]
    assert "font-variant-numeric: tabular-nums" in html
    assert 'font-feature-settings: "tnum" 1' in html
    assert "::-webkit-scrollbar" in html
    assert "--scrollbar-handle:" in html
    assert "scrollbar_handle" in html
    assert "backgroundColor: themeState.bg_canvas" in html
