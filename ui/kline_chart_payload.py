# -*- coding: utf-8 -*-
"""Pure builders for K-line chart context, theme, and ECharts payload."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QUrl

if TYPE_CHECKING:
    import pandas as pd

from app.services.kline_open_service import (
    KEY_CODE,
    KEY_DISCOVERED_AT,
    KEY_EARNINGS_MARK_DATE,
    KEY_EARNINGS_TEXT,
    KEY_NAME,
    KEY_REVEAL_DATE,
    KEY_TRIGGER_DATE,
    _build_scan_signal_payload,
    _is_scan_signal,
    _signal_matches_code,
    _signal_scan_identity,
)
from app.services.ui_market_calendar_service import MarketCalendar
from ui.kline_summary_payload import (
    build_kline_summary_cards as build_kline_summary_cards,
)
from ui.kline_summary_payload import (
    build_kline_summary_items as build_kline_summary_items,
)
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

_KLINE_SCRIPT_DIR = Path(__file__).resolve().parent / "assets" / "kline"


def _pandas_module():
    import pandas

    return pandas


@lru_cache(maxsize=None)
def _load_kline_script(filename: str) -> str:
    return (_KLINE_SCRIPT_DIR / filename).read_text(encoding="utf-8")


def dumps_json_for_script(value, **kwargs) -> str:
    text = json.dumps(value, ensure_ascii=False, **kwargs)
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )



def merge_kline_context(base: dict, extra: dict, *, overwrite: bool = False) -> dict:
    if not isinstance(extra, dict):
        return base

    for key, value in extra.items():
        if value in (None, "", [], {}):
            continue
        if overwrite or key not in base or base.get(key) in (None, "", [], {}):
            base[key] = value
    return base


def _extract_scan_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    for signal in item_data.get("_signals") or []:
        if not _signal_matches_code(signal, str(code).strip()):
            continue

        source_tab, signal_type = _signal_scan_identity(signal)
        if not _is_scan_signal(source_tab, signal_type):
            continue

        return _build_scan_signal_payload(signal, code, source_tab, signal_type)

    return {}


def _is_vcp_scan_source(payload: dict) -> bool:
    source_key = str(payload.get("__source_tab_key") or "").strip()
    source_tab = str(payload.get("source_tab") or "").strip()
    signal_type = str(payload.get("signal_type") or "").strip()
    return (
        bool(payload.get("_vcp_overlay_allowed"))
        or source_key == "scan"
        or source_tab == "scan"
        or signal_type == "vcp_scan"
    )


def _has_vcp_overlay_fields(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(
        payload.get(key) not in (None, "", [], {}) for key in ("VCP达标", "VCP收缩详情", "_model_name", "_peak_dates")
    ):
        return True
    if payload.get("_high1_date") or payload.get("_high2_date") or payload.get("_high3_date"):
        return True
    return (payload.get("区间最高价") not in (None, "") and payload.get("区间最低点") not in (None, "")) or (
        payload.get("box_high") not in (None, "") and payload.get("box_low") not in (None, "")
    )


def _matching_scan_result(scan_results: list | None, code: str) -> dict | None:
    code_text = str(code).strip()
    for scan_res in scan_results or []:
        if isinstance(scan_res, dict) and str(scan_res.get(KEY_CODE, "")).strip() == code_text:
            return scan_res
    return None


def _merge_scan_result_if_needed(resolved: dict, scan_results: list | None, code: str) -> None:
    if not _is_vcp_scan_source(resolved) or _has_vcp_overlay_fields(resolved):
        return

    scan_result = _matching_scan_result(scan_results, code)
    if scan_result is not None:
        merge_kline_context(resolved, scan_result, overwrite=True)
        resolved["_vcp_overlay_allowed"] = True


def resolve_kline_vcp_context(
    code: str,
    name: str,
    item_data: dict = None,
    watchlist_entry: dict = None,
    scan_results: list = None,
) -> dict:
    resolved = {
        "代码": code,
        "名称": name,
        "code": code,
        "name": name,
    }
    merge_kline_context(resolved, item_data or {}, overwrite=True)
    merge_kline_context(resolved, watchlist_entry or {}, overwrite=False)

    embedded_scan = _extract_scan_signal_payload(item_data or {}, code)
    if embedded_scan:
        merge_kline_context(resolved, embedded_scan, overwrite=False)
    else:
        _merge_scan_result_if_needed(resolved, scan_results, code)

    resolved[KEY_CODE] = str(resolved.get(KEY_CODE) or code)
    resolved[KEY_NAME] = str(resolved.get(KEY_NAME) or name or code)
    resolved["code"] = resolved[KEY_CODE]
    resolved["name"] = resolved[KEY_NAME]
    return resolved


def build_kline_theme_colors() -> dict:
    t = theme_manager.current_theme
    tokens = build_ui_tokens(t)
    chart_tokens = tokens.get("chart") or {}
    font_tokens = tokens.get("font") or {}
    text_tokens = tokens.get("text") or {}
    border_tokens = tokens.get("border") or {}
    is_dark = bool(tokens.get("is_dark"))

    colors = {
        "up_color": t["KLINE_UP_COLOR"],
        "down_color": t["KLINE_DOWN_COLOR"],
        "up_gradient_top": t.get("KLINE_UP_GRADIENT_TOP", t["KLINE_UP_COLOR"]),
        "up_gradient_bottom": t.get("KLINE_UP_GRADIENT_BOTTOM", t["KLINE_UP_COLOR"]),
        "up_border": t.get("KLINE_UP_BORDER", t["KLINE_UP_COLOR"]),
        "down_gradient_top": t.get("KLINE_DOWN_GRADIENT_TOP", t["KLINE_DOWN_COLOR"]),
        "down_gradient_bottom": t.get("KLINE_DOWN_GRADIENT_BOTTOM", t["KLINE_DOWN_COLOR"]),
        "down_border": t.get("KLINE_DOWN_BORDER", t["KLINE_DOWN_COLOR"]),
        "ma10": t["KLINE_MA10"],
        "ma20": t["KLINE_MA20"],
        "ma50": t["KLINE_MA50"],
        "ma150": t["KLINE_MA150"],
        "ma200": t["KLINE_MA200"],
        "vol_ma20": t["KLINE_VOL_MA20"],
        "grid_line": chart_tokens.get("grid_line") or t["KLINE_GRID_LINE"],
        "axis_line": chart_tokens.get("axis_line") or t["KLINE_AXIS_LINE"],
        "axis_label": chart_tokens.get("axis_label") or t["KLINE_AXIS_LABEL"],
        "pointer_bg": chart_tokens.get("crosshair_bg") or t["KLINE_POINTER_BG"],
        "vcp_star": t["KLINE_VCP_STAR"],
        "vcp_line": t["KLINE_VCP_LINE"],
        "vcp_line_soft": t["KLINE_VCP_LINE_SOFT"],
        "vcp_area": t["KLINE_VCP_AREA"],
        "vcp_area_top": t.get("KLINE_VCP_AREA_TOP", t["KLINE_VCP_AREA"]),
        "vcp_area_bottom": t.get("KLINE_VCP_AREA_BOTTOM", t["KLINE_VCP_AREA"]),
        "vcp_area_border": t.get("KLINE_VCP_AREA_BORDER", t["KLINE_VCP_LINE_SOFT"]),
        "vcp_guide": t["KLINE_VCP_GUIDE"],
        "vcp_breakout_bg": t["KLINE_VCP_BREAKOUT_BG"],
        "earnings_marker": t.get("KLINE_EARNINGS_MARKER", t.get("COLOR_WARNING", t["KLINE_VCP_STAR"])),
        "earnings_marker_bg": t.get("KLINE_EARNINGS_MARKER_BG", t.get("ACCENT_SUBTLE", "rgba(49, 95, 134, 0.10)")),
        "earnings_marker_border": t.get("KLINE_EARNINGS_MARKER_BORDER", t.get("ACCENT_BORDER", t["KLINE_VCP_LINE"])),
        "ma_ribbon_up": t.get("KLINE_MA_RIBBON_UP", "rgba(242, 54, 69, 0.08)"),
        "ma_ribbon_down": t.get("KLINE_MA_RIBBON_DOWN", "rgba(8, 153, 129, 0.08)"),
        "volume_dry": t.get("KLINE_VOLUME_DRY", "rgba(126, 142, 160, 0.22)"),
        "volume_spike": t.get("KLINE_VOLUME_SPIKE", t["KLINE_VCP_STAR"]),
        "volume_spike_shadow": t.get("KLINE_VOLUME_SPIKE_SHADOW", t["KLINE_VCP_STAR"]),
        "volume_spike_top": t.get("KLINE_VOLUME_SPIKE_TOP", t.get("KLINE_VOLUME_SPIKE_SHADOW", t["KLINE_VCP_STAR"])),
        "depth_line": t.get("KLINE_DEPTH_LINE", "rgba(255, 255, 255, 0.05)" if is_dark else "rgba(15, 23, 42, 0.04)"),
        "tooltip_bg": t.get("KLINE_TOOLTIP_BG", "rgba(17, 24, 39, 0.92)"),
        "tooltip_text": t.get("KLINE_TOOLTIP_TEXT", "#F3F4F6"),
        "macd_diff": t.get("KLINE_MACD_DIFF", "#FBBF24"),
        "macd_dea": t.get("KLINE_MACD_DEA", "#60A5FA"),
        "crosshair_line": t.get("KLINE_CROSSHAIR_LINE", t["KLINE_AXIS_LABEL"]),
        "datazoom_bg": t.get("KLINE_DATAZOOM_BG", t.get("KLINE_BG_TOOLBAR", t["BG_ELEVATED"])),
        "datazoom_fill": t.get("KLINE_DATAZOOM_FILL", t.get("KLINE_VCP_AREA", t["SELECTION_BG"])),
        "datazoom_handle": t.get("SCROLLBAR_HANDLE", t.get("KLINE_DATAZOOM_HANDLE", t["KLINE_AXIS_LINE"])),
        "scrollbar_handle": t.get("SCROLLBAR_HANDLE", border_tokens.get("default", t["BORDER_DEFAULT"])),
        "scrollbar_handle_hover": t.get("SCROLLBAR_HANDLE_HOVER", text_tokens.get("muted", t["TEXT_MUTED"])),
        "scrollbar_handle_pressed": t.get("SCROLLBAR_HANDLE_PRESSED", t.get("ACCENT_PRIMARY", t["BRAND_PRIMARY"])),
        "font_family": t.get(
            "KLINE_FONT_FAMILY",
            font_tokens.get("family") or '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
        ),
        "mono_font_family": t.get(
            "KLINE_MONO_FONT_FAMILY",
            font_tokens.get("mono_family")
            or '"JetBrains Mono", "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace',
        ),
    }

    colors.update(
        {
            "bg_canvas": t.get("KLINE_BG_CANVAS", "#0A0A0A" if is_dark else chart_tokens.get("panel_bg", t["BG_ELEVATED"])),
            "bg_toolbar": t.get(
                "KLINE_BG_TOOLBAR",
                "#0A0A0A" if is_dark else chart_tokens.get("toolbar_bg", t["BG_ELEVATED"]),
            ),
            "text_primary": t.get("KLINE_WIDGET_TEXT", "#FFF" if is_dark else text_tokens.get("primary", t["TEXT_PRIMARY"])),
            "text_secondary": text_tokens.get("secondary", t["TEXT_SECONDARY"]),
            "text_muted": t.get("KLINE_INFO_COLOR", "#A0A0A0" if is_dark else text_tokens.get("muted", t["TEXT_MUTED"])),
            "border": t.get("KLINE_TOOLBAR_BORDER", "#222" if is_dark else border_tokens.get("default", t["BORDER_DEFAULT"])),
        }
    )

    return colors


def build_kline_window_palette(theme: dict = None, is_dark: bool | None = None) -> dict:
    if theme is None:
        theme = theme_manager.current_theme
    if is_dark is None:
        is_dark = theme.get("appearance") == "dark"

    if is_dark:
        return {
            "widget_bg": theme.get("KLINE_WIDGET_BG", "#0C1016"),
            "widget_text": theme.get("KLINE_WIDGET_TEXT", "#F5F7FA"),
            "toolbar_bg": theme.get("KLINE_TOOLBAR_BG", "#11161D"),
            "toolbar_border": theme.get("KLINE_TOOLBAR_BORDER", "#222A33"),
            "summary_bg": theme.get("KLINE_SUMMARY_BG", "#0F141B"),
            "info_color": theme.get("KLINE_INFO_COLOR", "#8B98A8"),
            "btn_border": theme.get("KLINE_BTN_BORDER", "#303947"),
            "btn_hover_bg": theme.get("KLINE_BTN_HOVER_BG", "rgba(255,255,255,0.05)"),
            "btn_hover_text": theme.get("KLINE_BTN_HOVER_TEXT", "#F5F7FA"),
            "btn_disabled_text": theme.get("KLINE_BTN_DISABLED_TEXT", "#5A6573"),
            "btn_disabled_border": theme.get("KLINE_BTN_DISABLED_BORDER", "#262E39"),
            "chart_bg": theme.get("KLINE_CHART_BG", "#0B0F14"),
            "nav_bg": theme.get("KLINE_NAV_BG", "rgba(255,255,255,0.04)"),
            "badge_bg": theme.get("KLINE_BADGE_BG", "rgba(239, 68, 68, 0.10)"),
            "badge_fg": theme.get("KLINE_BADGE_FG", "#FCA5A5"),
            "summary_border": theme.get("KLINE_SUMMARY_BORDER", "rgba(148, 163, 184, 0.10)"),
        }

    unified_bg = theme["BG_ELEVATED"]
    return {
        "widget_bg": theme.get("KLINE_WIDGET_BG", unified_bg),
        "widget_text": theme.get("KLINE_WIDGET_TEXT", theme["TEXT_PRIMARY"]),
        "toolbar_bg": theme.get("KLINE_TOOLBAR_BG", unified_bg),
        "toolbar_border": theme.get("KLINE_TOOLBAR_BORDER", theme["BORDER_DEFAULT"]),
        "summary_bg": theme.get("KLINE_SUMMARY_BG", unified_bg),
        "info_color": theme.get("KLINE_INFO_COLOR", theme["TEXT_MUTED"]),
        "btn_border": theme.get("KLINE_BTN_BORDER", theme["BORDER_STRONG"]),
        "btn_hover_bg": theme.get("KLINE_BTN_HOVER_BG", theme["TAB_HOVER_BG"]),
        "btn_hover_text": theme.get("KLINE_BTN_HOVER_TEXT", theme["TEXT_PRIMARY"]),
        "btn_disabled_text": theme.get("KLINE_BTN_DISABLED_TEXT", theme["TEXT_DISABLED"]),
        "btn_disabled_border": theme.get("KLINE_BTN_DISABLED_BORDER", theme["BORDER_DEFAULT"]),
        "chart_bg": theme.get("KLINE_CHART_BG", unified_bg),
        "nav_bg": theme.get("KLINE_NAV_BG", theme["BG_BUTTON"]),
        "badge_bg": theme.get("KLINE_BADGE_BG", "rgba(239, 68, 68, 0.10)"),
        "badge_fg": theme.get("KLINE_BADGE_FG", theme["BRAND_DEEP"]),
        "summary_border": theme.get("KLINE_SUMMARY_BORDER", theme["BORDER_SUBTLE"]),
    }


def format_kline_market_badge(code: str) -> str:
    market = MarketCalendar.infer_market(code)
    return {
        "CN": "A股",
        "HK": "港股",
        "TW": "台股",
        "TWO": "台股",
        "T": "日股",
        "JP": "日股",
        "KS": "韩股",
        "US": "美股",
    }.get(market, market or "市场")


def build_kline_market_state(code: str) -> dict:
    market = MarketCalendar.infer_market(code)
    try:
        status = MarketCalendar.get_market_status(market)
        active = MarketCalendar.is_market_active(market)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        status = ""
        active = False
    return {
        "market": market,
        "status": status,
        "active": bool(active),
        "live": bool(active),
    }


def _build_kline_style_block(theme_colors: dict) -> str:
    return f'''        :root {{
            --bg-canvas: {theme_colors["bg_canvas"]};
            --bg-toolbar: {theme_colors["bg_toolbar"]};
            --border: {theme_colors["border"]};
            --text-primary: {theme_colors["text_primary"]};
            --text-secondary: {theme_colors["text_secondary"]};
            --text-muted: {theme_colors["text_muted"]};
            --depth-line: {theme_colors["depth_line"]};
            --ma10: {theme_colors["ma10"]};
            --ma20: {theme_colors["ma20"]};
            --ma50: {theme_colors["ma50"]};
            --ma150: {theme_colors["ma150"]};
            --ma200: {theme_colors["ma200"]};
            --scrollbar-handle: {theme_colors["scrollbar_handle"]};
            --scrollbar-handle-hover: {theme_colors["scrollbar_handle_hover"]};
            --scrollbar-handle-pressed: {theme_colors["scrollbar_handle_pressed"]};
            --font-family: {theme_colors["font_family"]};
            --mono-font-family: {theme_colors["mono_font_family"]};
        }}

        * {{ scrollbar-width: thin; scrollbar-color: var(--scrollbar-handle) transparent; }}
        html {{ background: transparent; }}
        body, #chart, .top-toolbar, .info-val, .ma-display {{ font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }}
        body {{ margin: 0; padding: 0; background-color: var(--bg-canvas); color: var(--text-secondary); font-family: var(--font-family); overflow: hidden; transition: background-color 180ms ease, color 180ms ease; }}
        body.glass-fused {{ background-color: transparent; }}
        #chart {{ width: 100vw; height: calc(100vh - 30px); margin-top: 30px; transition: filter 180ms ease; }}
        body.market-live #chart {{ filter: saturate(1.06) brightness(1.035); }}
        body.market-sleeping #chart {{ filter: none; }}
        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: var(--scrollbar-handle); border-radius: 5px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--scrollbar-handle-hover); }}
        ::-webkit-scrollbar-thumb:active {{ background: var(--scrollbar-handle-pressed); }}

        .top-toolbar {{ position: absolute; top: 0; left: 0; right: 0; height: 30px; background: var(--bg-toolbar); border-bottom: 1px solid var(--depth-line); box-shadow: 0 2px 10px rgba(0, 0, 0, 0.10); display: flex; align-items: center; padding: 0 12px; z-index: 100; gap: 10px; transition: background-color 180ms ease, border-color 180ms ease; }}
        .top-toolbar.is-updating .info-val {{ opacity: 0.55; }}
        .info-item {{ font-size: 11px; color: var(--text-muted); white-space: nowrap; transition: color 180ms ease; }}
        .info-val {{ font-family: var(--mono-font-family); font-size: 11px; font-weight: 700; margin-left: 2px; color: var(--text-secondary); transition: color 180ms ease, opacity 50ms ease; }}
        .ma-display {{ margin-left: auto; font-family: var(--mono-font-family); font-size: 11px; font-weight: 700; display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap; }}
        .ma-display span.ma10 {{ color: var(--ma10); }}
        .ma-display span.ma20 {{ color: var(--ma20); }}
        .ma-display span.ma50 {{ color: var(--ma50); }}
        .ma-display span.ma150 {{ color: var(--ma150); }}
        .ma-display span.ma200 {{ color: var(--ma200); }}
'''


def _build_kline_toolbar_html() -> str:
    return '''    <div class="top-toolbar" id="toolbar">
        <div class="info-item">日期: <span id="v-date" class="info-val" style="color: var(--text-primary)">-</span></div>
        <div class="info-item">开: <span id="v-open" class="info-val">-</span></div>
        <div class="info-item">高: <span id="v-high" class="info-val">-</span></div>
        <div class="info-item">低: <span id="v-low" class="info-val">-</span></div>
        <div class="info-item">收: <span id="v-close" class="info-val">-</span></div>
        <div class="info-item">涨幅: <span id="v-pct" class="info-val">-</span></div>
        <div class="info-item">成交量: <span id="v-vol" class="info-val">-</span></div>

        <div class="ma-display">
            <span class="ma10">MA10: <span id="v-ma10">-</span></span>
            <span class="ma20">MA20: <span id="v-ma20">-</span></span>
            <span class="ma50">MA50: <span id="v-ma50">-</span></span>
            <span class="ma150">MA150: <span id="v-ma150">-</span></span>
            <span class="ma200">MA200: <span id="v-ma200">-</span></span>
        </div>
    </div>
'''


def _build_kline_bootstrap_script(data_json: str, theme_json: str) -> str:
    return f'''        let rawData = {data_json};

        let themeState = {theme_json};
        let upColor = themeState.up_color;
        let downColor = themeState.down_color;
        const MA_LINE_WIDTH_SCALE = 1.18;
        const VOLUME_SPIKE_RATIO = 2.5;
        const KLINE_GRID_LEFT = 88;
        const KLINE_GRID_RIGHT = 38;
        let glassFused = false;

        function _setCssVar(name, value) {{
            if (value === undefined || value === null) return;
            document.documentElement.style.setProperty(name, String(value));
        }}

        function _applyCssTheme(t) {{
            if (!t) return;
            _setCssVar('--bg-canvas', t.bg_canvas);
            _setCssVar('--bg-toolbar', t.bg_toolbar);
            _setCssVar('--border', t.border);
            _setCssVar('--text-primary', t.text_primary);
            _setCssVar('--text-secondary', t.text_secondary);
            _setCssVar('--text-muted', t.text_muted);
            _setCssVar('--depth-line', t.depth_line);
            _setCssVar('--ma10', t.ma10);
            _setCssVar('--ma20', t.ma20);
            _setCssVar('--ma50', t.ma50);
            _setCssVar('--ma150', t.ma150);
            _setCssVar('--ma200', t.ma200);
            _setCssVar('--scrollbar-handle', t.scrollbar_handle);
            _setCssVar('--scrollbar-handle-hover', t.scrollbar_handle_hover);
            _setCssVar('--scrollbar-handle-pressed', t.scrollbar_handle_pressed);
            _setCssVar('--font-family', t.font_family);
            _setCssVar('--mono-font-family', t.mono_font_family);
        }}

        function _marketState() {{
            return rawData.marketState || {{ market: '', status: '', active: false, live: false }};
        }}

        function _isLiveMarket() {{
            const state = _marketState();
            return !!(state && (state.live || state.active));
        }}

        function _chartBackgroundColor() {{
            return glassFused ? 'rgba(0, 0, 0, 0)' : themeState.bg_canvas;
        }}

        function _applyMarketChrome() {{
            const live = _isLiveMarket();
            document.body.classList.toggle('market-live', live);
            document.body.classList.toggle('market-sleeping', !live);
            document.body.classList.toggle('glass-fused', !!glassFused);
        }}
'''


def _build_kline_interaction_script() -> str:
    return _load_kline_script("interaction.js")


def _build_kline_data_builder_script() -> str:
    return _load_kline_script("data_builder.js")


def _build_kline_option_script() -> str:
    return _load_kline_script("option.js")


def _build_kline_window_api_script() -> str:
    return _load_kline_script("window_api.js")


def _build_kline_runtime_script() -> str:
    return "\n".join(
        [
            _build_kline_interaction_script(),
            _build_kline_data_builder_script(),
            _build_kline_option_script(),
            _build_kline_window_api_script(),
        ]
    )


def build_kline_html(title: str, echarts_data: dict, echarts_js_path: str, theme_colors: dict) -> str:
    js_url = QUrl.fromLocalFile(echarts_js_path).toString()
    data_json = dumps_json_for_script(echarts_data)
    theme_json = dumps_json_for_script(theme_colors)
    style_block = _build_kline_style_block(theme_colors)
    toolbar_html = _build_kline_toolbar_html()
    bootstrap_script = _build_kline_bootstrap_script(data_json, theme_json)
    runtime_script = _build_kline_runtime_script()

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="{js_url}"></script>
    <style>
{style_block}
    </style>
</head>
<body>
{toolbar_html}
    <div id="chart"></div>

        <script>
{bootstrap_script}
{runtime_script}
    </script>
</body>
</html>'''


def build_kline_shell_html(title: str, echarts_js_path: str, theme_colors: dict) -> str:
    """Build the reusable WebEngine document with an intentionally empty data set."""
    empty_data = {
        "title": str(title or "K线"),
        "dates": [],
        "klines": [],
        "vols": [],
        "volMa20": [],
        "macd": [],
        "diff": [],
        "dea": [],
        "ma10": [],
        "ma20": [],
        "ma50": [],
        "ma150": [],
        "ma200": [],
        "maStyles": {},
        "marketState": {"market": "", "status": "", "active": False, "live": False},
        "vcpMarkers": None,
        "vcpLines": None,
        "vcpArea": None,
        "earningsMarkers": None,
    }
    return build_kline_html(title, empty_data, echarts_js_path, theme_colors)


def _build_kline_preheat_data(title: str, theme_colors: dict) -> dict:
    point_count = 250
    dates = [f"preheat-{index:03d}" for index in range(point_count)]
    closes = [round(10.0 + (index % 11) * 0.01, 2) for index in range(point_count)]
    up_color = str(theme_colors["up_color"])
    volumes = [
        {
            "value": 1_000.0,
            "itemStyle": {"color": up_color},
        }
        for _index in range(point_count)
    ]
    macd = [{"value": 0.0, "itemStyle": {"color": up_color}}] * point_count
    return {
        "title": str(title or "K线"),
        "dates": dates,
        "klines": [[value, value, value - 0.01, value + 0.01] for value in closes],
        "vols": volumes,
        "volMa20": [1_000.0] * point_count,
        "macd": macd,
        "diff": [0.0] * point_count,
        "dea": [0.0] * point_count,
        "ma10": list(closes),
        "ma20": list(closes),
        "ma50": list(closes),
        "ma150": list(closes),
        "ma200": list(closes),
        "maStyles": {},
        "marketState": {"market": "", "status": "", "active": False, "live": False},
        "vcpMarkers": None,
        "vcpLines": None,
        "vcpArea": None,
        "earningsMarkers": None,
    }


def build_kline_preheated_shell_html(
    title: str,
    echarts_js_path: str,
    theme_colors: dict,
) -> str:
    """Build the hidden pool shell with one full-size non-business render."""
    preheat_data = _build_kline_preheat_data(title, theme_colors)
    return build_kline_html(title, preheat_data, echarts_js_path, theme_colors)


def _pick_payload_value(payload: dict, *keys, default=""):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_date_idx(value, date_to_idx: dict) -> int:
    date_text = str(value or "").strip()[:10]
    if not date_text:
        return -1
    if date_text in date_to_idx:
        return date_to_idx[date_text]
    date_no_dash = date_text.replace("-", "")
    for date_key, idx in date_to_idx.items():
        if date_key.replace("-", "") == date_no_dash or date_text in date_key:
            return idx
    return -1


def _event_date_key(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    head = text[:10].replace("/", "-").replace(".", "-")
    compact = head.replace("-", "")
    if len(compact) >= 8 and compact[:8].isdigit():
        return compact[:8]
    fallback = text[:8]
    if len(fallback) == 8 and fallback.isdigit():
        return fallback
    try:
        pd = _pandas_module()
        timestamp = pd.to_datetime(text, errors="coerce")
    except (TypeError, ValueError):
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime("%Y%m%d")


def _event_date_text(value) -> str:
    key = _event_date_key(value)
    if not key:
        return ""
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def _find_last_visible_date_idx_on_or_before(value, dates: list) -> int:
    target_key = _event_date_key(value)
    if not target_key:
        return -1

    keyed_dates = [(_event_date_key(date), idx) for idx, date in enumerate(dates)]
    keyed_dates = [(key, idx) for key, idx in keyed_dates if key]
    if not keyed_dates or target_key < keyed_dates[0][0]:
        return -1

    marker_idx = -1
    for key, idx in keyed_dates:
        if key > target_key:
            break
        marker_idx = idx
    return marker_idx


def _vcp_peak_dates(payload: dict) -> list:
    peak_dates = _pick_payload_value(payload, "_peak_dates", "peak_dates", default=[]) or []
    if isinstance(peak_dates, str):
        peak_dates = [peak_dates]
    if peak_dates:
        return peak_dates
    return [payload[key] for key in ["_high1_date", "_high2_date", "_high3_date"] if payload.get(key)]


def _store_vcp_markers(data: dict, markers: list) -> None:
    if markers:
        data["vcpMarkers"] = markers


def _store_earnings_markers(data: dict, markers: list) -> None:
    if markers:
        data["earningsMarkers"] = markers


def _build_vcp_markers(data: dict, trigger_idx: int, theme: dict) -> list:
    if trigger_idx == -1:
        return []

    kline = data["klines"][trigger_idx]
    star_symbol = "path://M0 -13 L3 -3 L13 0 L3 3 L0 13 L-3 3 L-13 0 L-3 -3 Z"
    return [
        {
            "coord": [trigger_idx, kline[3]],
            "symbol": star_symbol,
            "symbolSize": 18,
            "symbolOffset": [0, -10],
            "itemStyle": {
                "color": theme["KLINE_VCP_STAR"],
                "borderColor": theme.get("KLINE_VCP_AREA_BORDER", theme["KLINE_VCP_LINE"]),
                "borderWidth": 1,
                "shadowBlur": 14,
                "shadowColor": theme["KLINE_VCP_STAR"],
            },
            "label": {
                "show": True,
                "formatter": "VCP",
                "position": "top",
                "distance": 6,
                "padding": [2, 6],
                "borderRadius": 6,
                "backgroundColor": theme.get("KLINE_VCP_BREAKOUT_BG", "rgba(217, 163, 74, 0.14)"),
                "borderColor": theme["KLINE_VCP_LINE"],
                "borderWidth": 1,
                "color": theme["KLINE_VCP_STAR"],
                "fontSize": 10,
                "fontWeight": 700,
            },
        }
    ]


def _earnings_summary_text(payload: dict) -> str:
    summary = _pick_payload_value(payload, KEY_EARNINGS_TEXT, "earnings", default="")
    if summary:
        return str(summary)

    qoq = _pick_payload_value(payload, "\u73af\u6bd4%", "qoq_pct", default="")
    if qoq in (None, ""):
        return ""
    try:
        qoq_text = f"{float(qoq):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        qoq_text = str(qoq)
    return f"\u73af\u6bd4 {qoq_text}%"


def _earnings_change_text(payload: dict, *keys) -> str:
    value = _pick_payload_value(payload, *keys, default="")
    if value in (None, ""):
        return ""
    text = str(value).strip().replace(",", "")
    if not text:
        return ""
    try:
        number = float(text.replace("%", "").replace("+", ""))
    except (TypeError, ValueError):
        return text
    sign = "+" if number > 0 else ""
    formatted = f"{sign}{number:.2f}".rstrip("0").rstrip(".")
    return f"{formatted}%"


def _build_earnings_markers(data: dict, dates: list, payload: dict | None) -> list:
    source = payload or {}
    mark_date = _pick_payload_value(
        source,
        KEY_EARNINGS_MARK_DATE,
        KEY_REVEAL_DATE,
        "\u516c\u544a\u65e5\u671f",
        KEY_TRIGGER_DATE,
        "\u6e90\u516c\u544a\u65e5\u671f",
        KEY_DISCOVERED_AT,
        "discovered_at",
        default="",
    )
    marker_idx = _find_last_visible_date_idx_on_or_before(mark_date, dates)
    if marker_idx == -1:
        return []

    kline = data["klines"][marker_idx]
    lows = [float(item[2]) for item in data["klines"] if item and len(item) > 3 and item[2] is not None]
    highs = [float(item[3]) for item in data["klines"] if item and len(item) > 3 and item[3] is not None]
    if not lows or not highs:
        return []

    low = float(kline[2])
    price_span = max(max(highs) - min(lows), abs(low) * 0.02, 0.01)
    marker_y = max(0.01, low - price_span * 0.03)
    summary = _earnings_summary_text(source)
    qoq_text = _earnings_change_text(source, "\u73af\u6bd4%", "qoq_pct", "\u73af\u6bd4\u589e\u901f_\u767e\u5206\u6bd4")
    yoy_text = _earnings_change_text(source, "\u540c\u6bd4%", "yoy_pct", "\u540c\u6bd4\u589e\u901f_\u767e\u5206\u6bd4")
    event_date = _event_date_text(mark_date)
    marker_date = dates[marker_idx]
    return [
        {
            "coord": [marker_idx, round(marker_y, 4)],
            "label": "\u4e1a\u7ee9\u65e5",
            "sourceDate": event_date,
            "date": marker_date,
            "summary": summary,
            "qoqText": qoq_text,
            "yoyText": yoy_text,
            "symbolSize": 11,
            "symbolOffset": [0, 8],
        }
    ]


def _valid_vcp_peak_indices(peak_dates: list, date_to_idx: dict) -> list[int]:
    valid_indices = []
    for item in peak_dates:
        idx = _find_date_idx(item, date_to_idx)
        if idx != -1:
            valid_indices.append(idx)
    return sorted(set(valid_indices))


def _vcp_range_bounds(peak_indices: list[int], trigger_idx: int, data_len: int) -> tuple[int, int] | None:
    if not peak_indices:
        return None

    x_start = min(peak_indices)
    x_end = max(peak_indices)
    if trigger_idx > x_end:
        x_end = trigger_idx
    if x_start < 0 or x_end >= data_len:
        return None
    return x_start, x_end


def _vcp_box_bounds(
    data: dict, x_start: int, x_end: int, raw_box_low: float, raw_box_high: float
) -> tuple[float, float] | None:
    box_slice = data["klines"][x_start : x_end + 1]
    derived_lows = [float(item[2]) for item in box_slice if item and len(item) > 3 and item[2] is not None]
    derived_highs = [float(item[3]) for item in box_slice if item and len(item) > 3 and item[3] is not None]
    box_low = min(derived_lows) if derived_lows else raw_box_low
    box_high = max(derived_highs) if derived_highs else raw_box_high
    if box_high <= 0 or box_low <= 0:
        return None
    return box_low, box_high


def _build_vcp_lines(
    dates: list,
    peak_indices: list[int],
    x_start: int,
    x_end: int,
    box_low: float,
    box_high: float,
) -> list:
    vcp_lines = [
        [{"xAxis": dates[xi], "yAxis": y} for y in (box_low, box_high)]
        for xi in sorted({*peak_indices, x_end})
        if x_start <= xi <= x_end
    ]
    vcp_lines.extend(
        [
            [{"xAxis": dates[x_start], "yAxis": y}, {"xAxis": dates[x_end], "yAxis": y}]
            for y in (box_high, box_low)
        ]
    )
    return vcp_lines


def _apply_vcp_box_overlay(
    data: dict,
    dates: list,
    peak_indices: list[int],
    trigger_idx: int,
    raw_box_low: float,
    raw_box_high: float,
) -> None:
    bounds = _vcp_range_bounds(peak_indices, trigger_idx, len(data["klines"]))
    if bounds is None:
        return

    x_start, x_end = bounds
    box_bounds = _vcp_box_bounds(data, x_start, x_end, raw_box_low, raw_box_high)
    if box_bounds is None:
        return

    box_low, box_high = box_bounds
    data["vcpArea"] = [
        [
            {"xAxis": dates[x_start], "yAxis": box_low},
            {"xAxis": dates[x_end], "yAxis": box_high},
        ]
    ]
    data["vcpLines"] = _build_vcp_lines(dates, peak_indices, x_start, x_end, box_low, box_high)


def _build_ohlcv_payload(df: pd.DataFrame, up_color: str, down_color: str) -> tuple[list, list, list]:
    dates = []
    klines = []
    vols = []

    for dt, row in df.iterrows():
        open_price = float(row["open"])
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        volume = float(row.get("volume", 0))

        dates.append(dt.strftime("%Y-%m-%d"))
        klines.append([open_price, close_price, low_price, high_price])
        vols.append(
            {
                "value": volume,
                "itemStyle": {"color": up_color if close_price >= open_price else down_color},
            }
        )

    return dates, klines, vols


def _build_moving_average_data(closes) -> dict:
    ma_data = {}
    for period, key in (
        (10, "ma10"),
        (20, "ma20"),
        (50, "ma50"),
        (150, "ma150"),
        (200, "ma200"),
    ):
        if len(closes) >= period:
            ma_data[key] = _rolling_mean(closes, period, digits=2)
        else:
            ma_data[key] = [None] * len(closes)
    return ma_data


def _prepared_numeric_series(df: pd.DataFrame, column: str, *, digits: int) -> list:
    values = []
    for value in df[column].values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            values.append(None)
            continue
        values.append(None if math.isnan(number) else round(number, digits))
    return values


def _moving_average_data(df: pd.DataFrame, closes) -> dict:
    columns = ("ma10", "ma20", "ma50", "ma150", "ma200")
    if not all(column in df.columns for column in columns):
        return _build_moving_average_data(closes)
    return {column: _prepared_numeric_series(df, column, digits=2) for column in columns}


def _rolling_mean(values, period: int, *, digits: int) -> list:
    numbers = [float(value) for value in values]
    result = [None] * len(numbers)
    for index in range(period - 1, len(numbers)):
        window = numbers[index - period + 1 : index + 1]
        if any(math.isnan(value) for value in window):
            continue
        result[index] = round(sum(window) / period, digits)
    return result


def _last_finite(values) -> float | None:
    if values is None:
        return None
    for value in reversed(list(values)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isnan(number):
            return number
    return None


def _finite_close_ma_pairs(closes, ma_values: list) -> list[tuple[float, float]]:
    pairs = []
    for close, ma_value in zip(closes, ma_values, strict=False):
        try:
            close_number = float(close)
            ma_number = float(ma_value)
        except (TypeError, ValueError):
            continue
        if math.isnan(close_number) or math.isnan(ma_number):
            continue
        pairs.append((close_number, ma_number))
    return pairs


def _crossed_ma200(closes, ma_values: list) -> bool:
    pairs = _finite_close_ma_pairs(closes, ma_values)
    if len(pairs) < 2:
        return False
    prev_close, prev_ma = pairs[-2]
    last_close, last_ma = pairs[-1]
    prev_delta = prev_close - prev_ma
    last_delta = last_close - last_ma
    if prev_delta == 0:
        return last_delta != 0
    return prev_delta * last_delta < 0


def _build_ma_line_styles(ma_data: dict, closes) -> dict:
    defaults = {
        "ma10": {"width": 1.0, "opacity": 0.72},
        "ma20": {"width": 1.0, "opacity": 0.76},
        "ma50": {"width": 1.7, "opacity": 0.90},
        "ma150": {"width": 1.2, "opacity": 0.46, "type": "dashed"},
        "ma200": {"width": 1.2, "opacity": 0.46, "type": "dashed"},
    }
    styles = {key: dict(value) for key, value in defaults.items()}
    latest_close = _last_finite(closes)
    latest_ma = {key: _last_finite(values) for key, values in ma_data.items()}
    latest_values = [value for value in latest_ma.values() if value is not None]

    if latest_close and len(latest_values) >= 3:
        spread = (max(latest_values) - min(latest_values)) / max(abs(latest_close), 0.01)
        if spread <= 0.025:
            for key, value in latest_ma.items():
                if value is not None:
                    styles[key]["width"] = 0.8
                    styles[key]["opacity"] = 0.2

    if _crossed_ma200(closes, ma_data.get("ma200") or []):
        styles["ma200"]["width"] = 2.0
        styles["ma200"]["opacity"] = 1.0
        styles["ma200"]["emphasis"] = "break"

    return styles


def _ema_values(values, span: int) -> list[float]:
    numbers = [float(value) for value in values]
    if not numbers:
        return []
    alpha = 2.0 / (span + 1.0)
    result = [numbers[0]]
    for value in numbers[1:]:
        result.append(value * alpha + result[-1] * (1.0 - alpha))
    return result


def _computed_macd_columns(df: pd.DataFrame) -> tuple[list[float], list[float], list[float]]:
    closes = [float(value) for value in df["close"].values]
    ema_fast = _ema_values(closes, 12)
    ema_slow = _ema_values(closes, 26)
    diff = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    signal = _ema_values(diff, 9)
    histogram = [value - average for value, average in zip(diff, signal)]
    return diff, signal, histogram


def _macd_columns(df: pd.DataFrame) -> tuple[list[float], list[float], list[float]]:
    required = {"MACD", "MACD_Signal", "MACD_Hist"}
    if not required.issubset(df.columns):
        return _computed_macd_columns(df)
    return (
        [float(value or 0) for value in df["MACD"].values],
        [float(value or 0) for value in df["MACD_Signal"].values],
        [float(value or 0) for value in df["MACD_Hist"].values],
    )


def _build_macd_payload(df: pd.DataFrame, up_color: str, down_color: str) -> tuple[list, list, list]:
    macd_bars = []
    diff_line = []
    dea_line = []
    diff_values, signal_values, hist_values = _macd_columns(df)
    for macd_val, signal_val, hist_val in zip(diff_values, signal_values, hist_values):
        macd_bars.append(
            {
                "value": hist_val,
                "itemStyle": {"color": up_color if hist_val >= 0 else down_color},
            }
        )
        diff_line.append(round(macd_val, 4))
        dea_line.append(round(signal_val, 4))

    return macd_bars, diff_line, dea_line


def _build_volume_ma20(vols: list) -> list:
    vol_values = [value["value"] if isinstance(value, dict) else value for value in vols]
    return _rolling_mean(vol_values, 20, digits=0)


def _volume_ma20_data(df: pd.DataFrame, vols: list) -> list:
    if "volMa20" in df.columns:
        return _prepared_numeric_series(df, "volMa20", digits=0)
    return _build_volume_ma20(vols)


def inject_vcp_overlays(data: dict, dates: list, vcp_data: dict | None, *, theme: dict | None = None) -> None:
    payload = vcp_data or {}
    if not (_is_vcp_scan_source(payload) or _has_vcp_overlay_fields(payload)):
        return

    trigger_date = str(_pick_payload_value(payload, "触发日期", "日期", "时间", "trigger_date", default=""))[:10]
    theme = theme or theme_manager.current_theme
    date_to_idx = {d: i for i, d in enumerate(dates)}
    trigger_idx = _find_date_idx(trigger_date, date_to_idx)

    markers = _build_vcp_markers(data, trigger_idx, theme)
    raw_box_high = _to_float(_pick_payload_value(payload, "区间最高价", "box_high", default=0))
    raw_box_low = _to_float(_pick_payload_value(payload, "区间最低点", "box_low", default=0))

    peak_indices = _valid_vcp_peak_indices(_vcp_peak_dates(payload), date_to_idx)
    _apply_vcp_box_overlay(data, dates, peak_indices, trigger_idx, raw_box_low, raw_box_high)
    _store_vcp_markers(data, markers)


def inject_earnings_markers(data: dict, dates: list, vcp_data: dict | None) -> None:
    markers = _build_earnings_markers(data, dates, vcp_data)
    _store_earnings_markers(data, markers)


def build_kline_echarts_payload(
    df: pd.DataFrame,
    *,
    code: str,
    name: str,
    vcp_data: dict | None,
    theme: dict | None = None,
) -> dict:
    theme = theme or theme_manager.current_theme
    up_color = theme["KLINE_UP_COLOR"]
    down_color = theme["KLINE_DOWN_COLOR"]

    dates, klines, vols = _build_ohlcv_payload(df, up_color, down_color)
    closes = df["close"].values
    ma_data = _moving_average_data(df, closes)
    macd_bars, diff_line, dea_line = _build_macd_payload(df, up_color, down_color)
    vol_ma20_data = _volume_ma20_data(df, vols)

    result = {
        "title": f"{name} ({code}) 日线",
        "dates": dates,
        "klines": klines,
        "vols": vols,
        "volMa20": vol_ma20_data,
        "macd": macd_bars,
        "diff": diff_line,
        "dea": dea_line,
        **ma_data,
        "maStyles": _build_ma_line_styles(ma_data, closes),
        "marketState": build_kline_market_state(code),
        "vcpMarkers": None,
        "vcpLines": None,
        "vcpArea": None,
        "earningsMarkers": None,
    }

    if vcp_data:
        inject_vcp_overlays(result, dates, vcp_data, theme=theme)
        inject_earnings_markers(result, dates, vcp_data)

    return result
