# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pytest

from ui import kline_chart_payload as chart
from ui import kline_summary_payload as summary


def test_summary_scalar_list_and_format_helpers_cover_empty_invalid_and_duplicates():
    assert summary._summary_clean_text("   ") == "--"
    assert summary._summary_clean_text("NaN") == "--"
    assert summary._summary_clean_text(" value ") == "value"
    assert summary._summary_pick({}, "missing") == "--"
    assert summary._summary_pick_pct({}, "missing") == "--"
    assert summary._summary_pick_pct({"x": "12%"}, "x") == "12%"
    assert summary._summary_pick_pct({"x": "plain"}, "x") == "plain"
    assert summary._summary_join(None, "", "--") == "--"
    assert summary._summary_join("a", "a", "b", sep="|") == "a|b"
    assert summary._summary_compact_list(None, "", "--") == "--"
    assert summary._summary_compact_list("a", "b", "c", "a", max_items=2) == "a/b+1"
    assert summary._summary_parse_float(" ") is None
    assert summary._summary_parse_float("oops") is None
    assert summary._summary_parse_float("1,234.5%") == 1234.5
    assert summary._summary_format_wan_amount("bad") == "--"
    assert summary._summary_format_signed_wan_amount(None) == "--"
    assert summary._summary_format_signed_wan_amount(-5).endswith("5万")
    assert summary._summary_format_pct_value("bad") == "--"


def test_summary_event_and_option_fallback_branches():
    assert summary._build_watchlist_event_text({}) == "--"
    event = summary._build_watchlist_event_text(
        {
            "大宗交易": "present",
            "业绩异动": "present",
            "龙虎榜": "present",
            "龙虎榜净额(万)": "bad",
        }
    )
    assert event
    fallback = summary._summary_option_row({}, [])
    assert fallback["label"] == "--" and fallback["value"] == "--"


@pytest.mark.parametrize(
    "source",
    ["scan", "watchlist", "lhb", "foreign_block", "earnings", "na_daily", "asian_market", "fund_holdings"],
)
def test_every_summary_card_builder_has_stable_three_card_contract(source):
    cards = summary.build_kline_summary_cards({"__source_tab_key": source}, is_fav=True)
    assert len(cards) == 3
    assert cards[-1]["rows"][-1]["highlight"] is True


def test_watchlist_source_tag_fallback_and_padding_builder(monkeypatch):
    cards = summary.build_kline_summary_cards({"__source_tab_key": "watchlist", "来源标签": ["one", "two", "three"]})
    assert "+1" in cards[0]["rows"][0]["value"]
    monkeypatch.setattr(summary, "_build_generic_summary_cards", lambda payload: [])
    cards = summary.build_kline_summary_cards({"__source_tab_key": "unknown"})
    assert cards[0]["title"] == "--" and cards[1]["title"] == "--"


def test_summary_items_range_present_and_missing():
    items = summary.build_kline_summary_items(
        {
            "trigger_date": "2026-07-15 08:00",
            "box_high": "12.5",
            "box_low": "9.5",
            "振幅": "bad",
        },
        is_fav=True,
    )
    assert items["触发"] == "2026-07-15"
    assert items["区间"] == "9.50 - 12.50"
    assert items["振幅"] == "bad"
    missing = summary.build_kline_summary_items({"box_high": "bad", "box_low": 1})
    assert missing["区间"] == "--"


def test_chart_json_merge_context_and_signal_helpers(monkeypatch):
    encoded = chart.dumps_json_for_script({"x": "<&>\u2028\u2029"})
    assert "\\u003c" in encoded and "\\u0026" in encoded and "\\u2028" in encoded
    base = {"keep": 1, "empty": ""}
    assert chart.merge_kline_context(base, None) is base
    chart.merge_kline_context(base, {"keep": 2, "empty": 3, "skip": []})
    assert base == {"keep": 1, "empty": 3}
    chart.merge_kline_context(base, {"keep": 2}, overwrite=True)
    assert base["keep"] == 2

    assert chart._extract_scan_signal_payload(None, "1") == {}
    monkeypatch.setattr(chart, "_signal_matches_code", lambda signal, code: signal.get("match", False))
    monkeypatch.setattr(chart, "_signal_scan_identity", lambda signal: (signal.get("tab"), signal.get("kind")))
    monkeypatch.setattr(chart, "_is_scan_signal", lambda tab, kind: tab == "scan")
    monkeypatch.setattr(chart, "_build_scan_signal_payload", lambda *args: {"built": True})
    signals = {"_signals": [{"match": False}, {"match": True, "tab": "other"}, {"match": True, "tab": "scan"}]}
    assert chart._extract_scan_signal_payload(signals, "1") == {"built": True}
    assert chart._extract_scan_signal_payload({"_signals": [{"match": False}]}, "1") == {}


def test_chart_vcp_source_overlay_matching_and_merge(monkeypatch):
    assert chart._is_vcp_scan_source({"_vcp_overlay_allowed": 1})
    assert chart._is_vcp_scan_source({"__source_tab_key": "scan"})
    assert chart._is_vcp_scan_source({"source_tab": "scan"})
    assert chart._is_vcp_scan_source({"signal_type": "vcp_scan"})
    assert not chart._is_vcp_scan_source({})
    assert not chart._has_vcp_overlay_fields(None)
    assert chart._has_vcp_overlay_fields({"_model_name": "model"})
    assert chart._has_vcp_overlay_fields({"_high2_date": "2026-01-01"})
    assert chart._has_vcp_overlay_fields({"box_high": 2, "box_low": 1})
    assert not chart._has_vcp_overlay_fields({"box_high": 2})
    assert chart._matching_scan_result([None, {chart.KEY_CODE: " 1 "}], "1")[chart.KEY_CODE].strip() == "1"
    assert chart._matching_scan_result([], "1") is None

    resolved = {"__source_tab_key": "other"}
    chart._merge_scan_result_if_needed(resolved, [{chart.KEY_CODE: "1"}], "1")
    assert "_vcp_overlay_allowed" not in resolved
    resolved = {"__source_tab_key": "scan", "box_high": 2, "box_low": 1}
    chart._merge_scan_result_if_needed(resolved, [{chart.KEY_CODE: "1"}], "1")
    assert "_vcp_overlay_allowed" not in resolved
    resolved = {"__source_tab_key": "scan"}
    chart._merge_scan_result_if_needed(resolved, [{chart.KEY_CODE: "1", "box_high": 2}], "1")
    assert resolved["_vcp_overlay_allowed"] is True and resolved["box_high"] == 2


def test_chart_palette_badge_and_market_state_fallback(monkeypatch):
    dark = chart.build_kline_window_palette({}, is_dark=True)
    assert dark["widget_bg"] == "#0C1016"
    light_theme = {
        "BG_ELEVATED": "bg",
        "TEXT_PRIMARY": "text",
        "BORDER_DEFAULT": "border",
        "TEXT_MUTED": "muted",
        "BORDER_STRONG": "strong",
        "TAB_HOVER_BG": "hover",
        "TEXT_DISABLED": "disabled",
        "BG_BUTTON": "button",
        "BRAND_DEEP": "brand",
        "BORDER_SUBTLE": "subtle",
        "appearance": "light",
    }
    assert chart.build_kline_window_palette(light_theme)["widget_bg"] == "bg"
    monkeypatch.setattr(chart.MarketCalendar, "infer_market", lambda code: "UNKNOWN")
    assert chart.format_kline_market_badge("x") == "UNKNOWN"
    monkeypatch.setattr(chart.MarketCalendar, "infer_market", lambda code: "")
    assert chart.format_kline_market_badge("x")
    monkeypatch.setattr(
        chart.MarketCalendar, "get_market_status", lambda market: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    state = chart.build_kline_market_state("x")
    assert state == {"market": "", "status": "", "active": False, "live": False}


def test_chart_date_number_peak_and_marker_helpers(monkeypatch):
    assert chart._pick_payload_value({"a": None, "b": 2}, "a", "b") == 2
    assert chart._to_float("bad", 7) == 7
    dates = {"2026-07-15": 2, "20260716-extra": 3}
    assert chart._find_date_idx(None, dates) == -1
    assert chart._find_date_idx("2026-07-15", dates) == 2
    assert chart._find_date_idx("20260716", dates) == 3
    assert chart._find_date_idx("nope", dates) == -1
    assert chart._event_date_key(None) == ""
    assert chart._event_date_key("2026/07/15") == "20260715"
    assert chart._event_date_key("20260715 anything") == "20260715"
    assert chart._event_date_key("July 15, 2026") == "20260715"
    assert chart._event_date_key("invalid") == ""
    pandas_module = chart._pandas_module()
    monkeypatch.setattr(
        pandas_module,
        "to_datetime",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad")),
    )
    assert chart._event_date_key(object()) == ""
    assert chart._event_date_text(None) == ""
    assert chart._find_last_visible_date_idx_on_or_before(None, ["2026-01-01"]) == -1
    assert chart._find_last_visible_date_idx_on_or_before("2025-01-01", ["2026-01-01"]) == -1
    assert chart._find_last_visible_date_idx_on_or_before("2026-01-02", ["bad", "2026-01-01", "2026-01-03"]) == 1
    assert chart._vcp_peak_dates({"_peak_dates": "2026-01-01"}) == ["2026-01-01"]
    assert chart._vcp_peak_dates({"_high1_date": "2026-01-01"}) == ["2026-01-01"]

    data = {}
    chart._store_vcp_markers(data, [])
    chart._store_earnings_markers(data, [])
    assert data == {}
    assert chart._build_vcp_markers({"klines": []}, -1, {}) == []


def test_chart_earnings_helpers_and_boundaries():
    assert chart._earnings_summary_text({}) == ""
    assert chart._earnings_summary_text({"qoq_pct": 12.30}) == "环比 12.3%"
    assert chart._earnings_summary_text({"qoq_pct": "growth"}) == "环比 growth%"
    assert chart._earnings_change_text({}, "x") == ""
    assert chart._earnings_change_text({"x": " "}, "x") == ""
    assert chart._earnings_change_text({"x": "text"}, "x") == "text"
    assert chart._earnings_change_text({"x": "+1.20%"}, "x") == "+1.2%"
    assert chart._earnings_change_text({"x": -1.2}, "x") == "-1.2%"

    dates = ["2026-07-14", "2026-07-15"]
    assert chart._build_earnings_markers({"klines": [[1, 1, 1, 1]]}, dates[:1], {}) == []
    assert (
        chart._build_earnings_markers(
            {"klines": [[1, 1, None, None]]}, dates[:1], {chart.KEY_EARNINGS_MARK_DATE: dates[0]}
        )
        == []
    )
    markers = chart._build_earnings_markers(
        {"klines": [[1, 1, 9, 11], [1, 1, 10, 12]]},
        dates,
        {chart.KEY_EARNINGS_MARK_DATE: "2026-07-15", "qoq_pct": 5},
    )
    assert markers[0]["coord"][0] == 1


def test_chart_vcp_bounds_overlay_and_ma_helpers():
    assert chart._valid_vcp_peak_indices(["bad"], {"2026-01-01": 0}) == []
    assert chart._vcp_range_bounds([], 0, 1) is None
    assert chart._vcp_range_bounds([0], 2, 2) is None
    assert chart._vcp_range_bounds([0, 1], 2, 3) == (0, 2)
    data = {"klines": [[1, 1, None, None], [1, 1, None, None]]}
    assert chart._vcp_box_bounds(data, 0, 1, 0, 0) is None
    assert chart._vcp_box_bounds(data, 0, 1, 1, 2) == (1, 2)
    target = {"klines": [[1, 1, 1, 2]]}
    chart._apply_vcp_box_overlay(target, ["2026-01-01"], [], -1, 1, 2)
    assert "vcpArea" not in target
    target = {"klines": [[1, 1, None, None]]}
    chart._apply_vcp_box_overlay(target, ["2026-01-01"], [0], 0, 0, 0)
    assert "vcpArea" not in target

    assert chart._last_finite([None, np.nan]) is None
    assert chart._last_finite([None, 2.5]) == 2.5
    assert chart._finite_close_ma_pairs([1, np.nan, 3], [1, 2, None]) == [(1.0, 1.0)]
    assert not chart._crossed_ma200([1], [None])


def test_chart_build_html_script_cache_and_payload_smoke(tmp_path, monkeypatch):
    script = tmp_path / "script.js"
    script.write_text("window.test = true;", encoding="utf-8")
    monkeypatch.setattr(chart, "_KLINE_SCRIPT_DIR", tmp_path)
    chart._load_kline_script.cache_clear()
    assert chart._load_kline_script("script.js") == "window.test = true;"

    theme = {
        "bg_canvas": "black",
        "bg_toolbar": "black",
        "border": "gray",
        "text_primary": "white",
        "text_secondary": "gray",
        "text_muted": "gray",
        "depth_line": "gray",
        "ma10": "a",
        "ma20": "b",
        "ma50": "c",
        "ma150": "d",
        "ma200": "e",
        "scrollbar_handle": "x",
        "scrollbar_handle_hover": "y",
        "scrollbar_handle_pressed": "z",
        "font_family": "sans",
        "mono_font_family": "mono",
    }
    assert "top-toolbar" in chart._build_kline_style_block(theme)
    assert "rawData" in chart._build_kline_bootstrap_script("{}", "{}")
    assert "top-toolbar" in chart._build_kline_toolbar_html()
