# -*- coding: utf-8 -*-
"""Pure builders for K-line chart context, theme, and ECharts payload."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from PyQt6.QtCore import QUrl

from app.services.ui_market_calendar_service import MarketCalendar
from ui.kline_summary_payload import (
    build_kline_summary_cards as build_kline_summary_cards,
)
from ui.kline_summary_payload import (
    build_kline_summary_items as build_kline_summary_items,
)
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_TRIGGER_DATE = "\u89e6\u53d1\u65e5\u671f"
SCAN_SOURCE_KEY = "scan"


def merge_kline_context(base: dict, extra: dict, *, overwrite: bool = False) -> dict:
    if not isinstance(extra, dict):
        return base

    for key, value in extra.items():
        if value in (None, "", [], {}):
            continue
        if overwrite or key not in base or base.get(key) in (None, "", [], {}):
            base[key] = value
    return base


def _signal_value(signal, key: str, default=""):
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _signal_matches_code(signal, code: str) -> bool:
    signal_code = str(_signal_value(signal, "code") or _signal_value(signal, KEY_CODE) or "").strip()
    return not signal_code or signal_code == str(code).strip()


def _signal_scan_identity(signal) -> tuple[str, str]:
    source_tab = str(_signal_value(signal, "source_tab") or "").strip()
    signal_type = str(_signal_value(signal, "signal_type") or "").strip()
    return source_tab, signal_type


def _is_scan_signal(source_tab: str, signal_type: str) -> bool:
    return source_tab == SCAN_SOURCE_KEY or signal_type == "vcp_scan"


def _build_scan_signal_payload(signal, code: str, source_tab: str, signal_type: str) -> dict:
    raw_payload = _signal_value(signal, "payload", {}) or {}
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    payload.setdefault(KEY_CODE, code)

    signal_name = str(_signal_value(signal, "name") or "").strip()
    if signal_name:
        payload.setdefault(KEY_NAME, signal_name)

    observed_at = str(_signal_value(signal, "observed_at") or "").strip()
    if observed_at:
        payload.setdefault(KEY_TRIGGER_DATE, observed_at)

    payload["source_tab"] = source_tab or SCAN_SOURCE_KEY
    payload["signal_type"] = signal_type or "vcp_scan"
    payload["_vcp_overlay_allowed"] = True
    return payload


def _extract_scan_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    for signal in item_data.get("_signals") or []:
        if not _signal_matches_code(signal, code):
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
        "vcp_guide": t["KLINE_VCP_GUIDE"],
        "vcp_breakout_bg": t["KLINE_VCP_BREAKOUT_BG"],
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


def build_kline_html(title: str, echarts_data: dict, echarts_js_path: str, theme_colors: dict) -> str:
    js_url = QUrl.fromLocalFile(echarts_js_path).toString()
    data_json = json.dumps(echarts_data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="{js_url}"></script>
    <style>
        :root {{
            --bg-canvas: {theme_colors["bg_canvas"]};
            --bg-toolbar: {theme_colors["bg_toolbar"]};
            --border: {theme_colors["border"]};
            --text-primary: {theme_colors["text_primary"]};
            --text-secondary: {theme_colors["text_secondary"]};
            --text-muted: {theme_colors["text_muted"]};
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
        body, #chart, .top-toolbar, .info-val, .ma-display {{ font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }}
        body {{ margin: 0; padding: 0; background-color: var(--bg-canvas); color: var(--text-secondary); font-family: var(--font-family); overflow: hidden; transition: background-color 180ms ease, color 180ms ease; }}
        #chart {{ width: 100vw; height: calc(100vh - 30px); margin-top: 30px; }}
        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: var(--scrollbar-handle); border-radius: 5px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--scrollbar-handle-hover); }}
        ::-webkit-scrollbar-thumb:active {{ background: var(--scrollbar-handle-pressed); }}

        .top-toolbar {{ position: absolute; top: 0; left: 0; right: 0; height: 30px; background: var(--bg-toolbar); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 12px; z-index: 100; gap: 10px; transition: background-color 180ms ease, border-color 180ms ease; }}
        .info-item {{ font-size: 11px; color: var(--text-muted); white-space: nowrap; transition: color 180ms ease; }}
        .info-val {{ font-family: var(--mono-font-family); font-size: 11px; font-weight: 700; margin-left: 2px; color: var(--text-secondary); transition: color 180ms ease; }}
        .ma-display {{ margin-left: auto; font-family: var(--mono-font-family); font-size: 11px; font-weight: 700; display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap; }}
        .ma-display span.ma10 {{ color: var(--ma10); }}
        .ma-display span.ma20 {{ color: var(--ma20); }}
        .ma-display span.ma50 {{ color: var(--ma50); }}
        .ma-display span.ma150 {{ color: var(--ma150); }}
        .ma-display span.ma200 {{ color: var(--ma200); }}
    </style>
</head>
<body>
    <div class="top-toolbar" id="toolbar">
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
    <div id="chart"></div>

        <script>
        let rawData = {data_json};

        let themeState = {json.dumps(theme_colors, ensure_ascii=False)};
        let upColor = themeState.up_color;
        let downColor = themeState.down_color;

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

        const chart = echarts.init(document.getElementById('chart'));

        chart.on('updateAxisPointer', function (event) {{
            const axisInfo = event.axesInfo[0];
            if (axisInfo) {{
                const idx = axisInfo.value;
                if (idx >= 0 && idx < rawData.dates.length) {{
                    const dt = rawData.dates[idx];
                    const kline = rawData.klines[idx];

                    document.getElementById('v-date').innerText = dt;
                    document.getElementById('v-open').innerText = kline[0].toFixed(2);
                    document.getElementById('v-close').innerText = kline[1].toFixed(2);
                    document.getElementById('v-low').innerText = kline[2].toFixed(2);
                    document.getElementById('v-high').innerText = kline[3].toFixed(2);

                    let prevClose = idx > 0 ? rawData.klines[idx-1][1] : kline[0];
                    let pct = ((kline[1] - prevClose) / prevClose * 100);
                    let pctStr = pct >= 0 ? '+' + pct.toFixed(2) + '%' : pct.toFixed(2) + '%';

                    document.getElementById('v-pct').innerText = pctStr;
                    document.getElementById('v-pct').style.color = pct >= 0 ? upColor : downColor;

                    const vol = rawData.vols[idx].value || rawData.vols[idx];
                    document.getElementById('v-vol').innerText = (vol / 10000).toFixed(0) + '万';

                    const maKeys = ['ma10', 'ma20', 'ma50', 'ma150', 'ma200'];
                    for (const key of maKeys) {{
                        const el = document.getElementById('v-' + key);
                        if (el && rawData[key]) {{
                            const val = rawData[key][idx];
                            el.innerText = (val !== null && val !== undefined) ? Number(val).toFixed(2) : '-';
                        }}
                    }}
                }}
            }}
        }});

        function splitData(rawData) {{
            return {{
                categoryData: rawData.dates,
                values: rawData.klines,
                volumes: rawData.vols
            }};
        }}

        function buildVcpEffectData() {{
            return (rawData.vcpMarkers || []).map((item) => ({{
                value: item.coord,
                symbol: item.symbol || 'circle',
                symbolSize: item.symbolSize || 10,
                symbolOffset: item.symbolOffset || [0, -10],
                itemStyle: item.itemStyle,
                label: item.label
            }}));
        }}

        function buildOption() {{
            const data = splitData(rawData);
            return {{
                animation: false,
                backgroundColor: themeState.bg_canvas,
                legend: {{
                    show: false
                }},
                axisPointer: {{
                    link: [{{ xAxisIndex: 'all' }}],
                    lineStyle: {{
                        color: themeState.crosshair_line,
                        width: 0.8,
                        type: 'dashed',
                        opacity: 0.4
                    }},
                    crossStyle: {{
                        color: themeState.crosshair_line,
                        width: 0.8,
                        opacity: 0.4
                    }},
                    label: {{
                        backgroundColor: themeState.pointer_bg,
                        color: themeState.tooltip_text,
                        fontFamily: themeState.mono_font_family,
                        borderRadius: 4,
                        padding: [3, 6],
                        shadowBlur: 8,
                        shadowColor: themeState.crosshair_line,
                        shadowOffsetY: 0
                    }}
                }},
                tooltip: {{
                    trigger: 'axis',
                    showContent: false,
                    axisPointer: {{
                        type: 'cross',
                        lineStyle: {{ color: themeState.crosshair_line, width: 0.8, opacity: 0.4 }},
                        crossStyle: {{ color: themeState.crosshair_line, width: 0.8, opacity: 0.4 }}
                    }},
                    backgroundColor: themeState.tooltip_bg,
                    borderWidth: 0,
                    textStyle: {{ color: themeState.tooltip_text, fontFamily: themeState.mono_font_family }}
                }},
                grid: [
                    {{ left: '7%', right: '2%', top: 18, height: '56%' }},
                    {{ left: '7%', right: '2%', top: '67%', height: '11%' }},
                    {{ left: '7%', right: '2%', top: '81%', height: '12%' }}
                ],
                xAxis: [
                    {{
                        type: 'category',
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        axisLabel: {{ color: themeState.axis_label, fontFamily: themeState.mono_font_family }},
                        splitLine: {{ show: false }},
                        min: 'dataMin',
                        max: 'dataMax'
                    }},
                    {{
                        type: 'category',
                        gridIndex: 1,
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        axisLabel: {{ show: false }},
                        axisTick: {{ show: false }},
                        splitLine: {{ show: false }},
                        min: 'dataMin',
                        max: 'dataMax'
                    }},
                    {{
                        type: 'category',
                        gridIndex: 2,
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        axisLabel: {{ color: themeState.axis_label, fontFamily: themeState.mono_font_family }},
                        min: 'dataMin',
                        max: 'dataMax'
                    }}
                ],
                yAxis: [
                    {{
                        scale: true,
                        splitArea: {{ show: false }},
                        splitLine: {{ lineStyle: {{ color: themeState.grid_line, type: [4, 4] }} }},
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        axisLabel: {{ color: themeState.axis_label, fontFamily: themeState.mono_font_family }}
                    }},
                    {{
                        scale: true,
                        gridIndex: 1,
                        splitNumber: 2,
                        axisLabel: {{ color: themeState.axis_label, fontFamily: themeState.mono_font_family }},
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        splitLine: {{ show: false }}
                    }},
                    {{
                        scale: true,
                        gridIndex: 2,
                        splitNumber: 2,
                        axisLabel: {{ color: themeState.axis_label, fontFamily: themeState.mono_font_family }},
                        axisLine: {{ lineStyle: {{ color: themeState.axis_line }} }},
                        splitLine: {{ show: false }}
                    }}
                ],
                dataZoom: [
                    {{
                        type: 'inside',
                        xAxisIndex: [0, 1, 2],
                        start: 55,
                        end: 100
                    }},
                    {{
                        show: false,
                        xAxisIndex: [0, 1, 2],
                        type: 'slider',
                        top: '94%',
                        start: 55,
                        end: 100,
                        backgroundColor: themeState.datazoom_bg,
                        fillerColor: themeState.datazoom_fill,
                        borderColor: themeState.border,
                        handleStyle: {{
                            color: themeState.datazoom_handle,
                            borderColor: themeState.datazoom_handle
                        }},
                        textStyle: {{
                            color: themeState.axis_label,
                            fontFamily: themeState.mono_font_family
                        }}
                    }}
                ],
                series: [
                    {{
                        name: '日K',
                        type: 'candlestick',
                        data: data.values,
                        itemStyle: {{
                            color: upColor,
                            color0: downColor,
                            borderColor: upColor,
                            borderColor0: downColor
                        }},
                        markLine: rawData.vcpLines ? {{
                            symbol: ['none', 'none'],
                            silent: true,
                            animation: false,
                            label: {{ show: false }},
                            lineStyle: {{
                                color: themeState.vcp_line,
                                width: 1.2,
                                type: 'solid'
                            }},
                            data: rawData.vcpLines
                        }} : undefined,
                        markArea: rawData.vcpArea ? {{
                            silent: true,
                            animation: false,
                            itemStyle: {{
                                color: themeState.vcp_area
                            }},
                            data: rawData.vcpArea
                        }} : undefined
                    }},
                    {{
                        name: 'VCP Breakout',
                        type: 'effectScatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: buildVcpEffectData(),
                        showEffectOn: 'render',
                        rippleEffect: {{
                            period: 4,
                            scale: 3.2,
                            brushType: 'stroke',
                            color: themeState.vcp_star
                        }},
                        z: 12,
                        animation: true,
                        itemStyle: {{
                            color: themeState.vcp_star,
                            shadowBlur: 12,
                            shadowColor: themeState.vcp_star
                        }}
                    }},
                    {{
                        name: 'MA10',
                        type: 'line',
                        data: rawData.ma10,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: themeState.ma10 }}
                    }},
                    {{
                        name: 'MA20',
                        type: 'line',
                        data: rawData.ma20,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: themeState.ma20 }}
                    }},
                    {{
                        name: 'MA50',
                        type: 'line',
                        data: rawData.ma50,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: themeState.ma50 }}
                    }},
                    {{
                        name: 'MA150',
                        type: 'line',
                        data: rawData.ma150,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: themeState.ma150 }}
                    }},
                    {{
                        name: 'MA200',
                        type: 'line',
                        data: rawData.ma200,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: themeState.ma200 }}
                    }},
                    {{
                        name: 'Volume',
                        type: 'bar',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        data: data.volumes
                    }},
                    {{
                        name: 'VOL-MA20',
                        type: 'line',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        data: rawData.volMa20,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: {{ width: 1.1, color: themeState.vol_ma20 }}
                    }},
                    {{
                        name: 'MACD',
                        type: 'bar',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.macd
                    }},
                    {{
                        name: 'DIFF',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.diff,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: {{ width: 1.1, color: themeState.macd_diff }}
                    }},
                    {{
                        name: 'DEA',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.dea,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: {{ width: 1.1, color: themeState.macd_dea }}
                    }}
                ]
            }};
        }}

        chart.setOption(buildOption());

        window.applyTheme = function (payload) {{
            const t = payload && payload.theme ? payload.theme : payload;
            if (!t) return false;
            themeState = t;
            if (t.up_color) upColor = t.up_color;
            if (t.down_color) downColor = t.down_color;
            _applyCssTheme(themeState);

            const currentOption = chart.getOption ? chart.getOption() : null;
            const dataZoom = currentOption && currentOption.dataZoom ? currentOption.dataZoom : null;
            const nextOption = buildOption();
            if (dataZoom) {{
                nextOption.dataZoom = dataZoom;
            }}
            chart.setOption(nextOption, true, true);
            chart.resize();
            return true;
        }};

        window.replaceKlineData = function (payload) {{
            if (!payload || !payload.data) return false;
            rawData = payload.data;
            if (payload.title) {{
                document.title = payload.title;
            }}

            const currentOption = chart.getOption ? chart.getOption() : null;
            const dataZoom = currentOption && currentOption.dataZoom ? currentOption.dataZoom : null;
            const nextOption = buildOption();
            if (dataZoom) {{
                nextOption.dataZoom = dataZoom;
            }}
            chart.setOption(nextOption, true, true);
            chart.resize();
            return true;
        }};

        window.updateLastBar = function (payload) {{
            if (!payload || !payload.date) return;

            const lastIndex = rawData.dates.length - 1;
            const isSameDay = lastIndex >= 0 && rawData.dates[lastIndex] === payload.date;
            const klineEntry = [payload.open, payload.close, payload.low, payload.high];
            const volEntry = {{
                value: payload.vol || 0,
                itemStyle: {{
                    color: payload.close >= payload.open ? upColor : downColor
                }}
            }};

            if (isSameDay) {{
                rawData.klines[lastIndex] = klineEntry;
                rawData.vols[lastIndex] = volEntry;
            }} else {{
                rawData.dates.push(payload.date);
                rawData.klines.push(klineEntry);
                rawData.vols.push(volEntry);
            }}

            chart.setOption({{
                xAxis: [
                    {{ data: rawData.dates }},
                    {{ data: rawData.dates }},
                    {{ data: rawData.dates }}
                ],
                series: [
                    {{ data: rawData.klines }},
                    {{ data: rawData.ma10 }},
                    {{ data: rawData.ma20 }},
                    {{ data: rawData.ma50 }},
                    {{ data: rawData.ma150 }},
                    {{ data: rawData.ma200 }},
                    {{ data: rawData.vols }},
                    {{ data: rawData.volMa20 }},
                    {{ data: rawData.macd }},
                    {{ data: rawData.diff }},
                    {{ data: rawData.dea }}
                ]
            }}, false, true);
        }};

        window.addEventListener('resize', function () {{
            chart.resize();
        }});
    </script>
</body>
</html>'''


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


def _build_vcp_markers(data: dict, trigger_idx: int, theme: dict) -> list:
    if trigger_idx == -1:
        return []

    kline = data["klines"][trigger_idx]
    return [
        {
            "coord": [trigger_idx, kline[3]],
            "symbol": "circle",
            "symbolSize": 10,
            "symbolOffset": [0, -10],
            "itemStyle": {
                "color": theme["KLINE_VCP_STAR"],
                "borderColor": theme["KLINE_VCP_LINE"],
                "borderWidth": 1,
                "shadowBlur": 0,
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
    vcp_lines = []
    vertical_indices = peak_indices[:]
    if x_end not in vertical_indices:
        vertical_indices.append(x_end)
    for xi in sorted(set(vertical_indices)):
        if x_start <= xi <= x_end:
            vcp_lines.append(
                [
                    {"xAxis": dates[xi], "yAxis": box_low},
                    {"xAxis": dates[xi], "yAxis": box_high},
                ]
            )

    vcp_lines.append(
        [
            {"xAxis": dates[x_start], "yAxis": box_high},
            {"xAxis": dates[x_end], "yAxis": box_high},
        ]
    )
    vcp_lines.append(
        [
            {"xAxis": dates[x_start], "yAxis": box_low},
            {"xAxis": dates[x_end], "yAxis": box_low},
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
            ma = pd.Series(closes).rolling(period).mean()
            ma_data[key] = [round(value, 2) if not np.isnan(value) else None for value in ma.values]
        else:
            ma_data[key] = [None] * len(closes)
    return ma_data


def _build_macd_payload(df: pd.DataFrame, up_color: str, down_color: str) -> tuple[list, list, list]:
    macd_bars = []
    diff_line = []
    dea_line = []
    if "MACD" not in df.columns:
        return macd_bars, diff_line, dea_line

    for _, row in df.iterrows():
        macd_val = float(row.get("MACD", 0) or 0)
        signal_val = float(row.get("MACD_Signal", 0) or 0)
        hist_val = float(row.get("MACD_Hist", 0) or 0)

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
    vol_ma20 = pd.Series(vol_values).rolling(20).mean()
    return [round(value, 0) if not np.isnan(value) else None for value in vol_ma20.values]


def inject_vcp_overlays(data: dict, dates: list, vcp_data: dict | None) -> None:
    payload = vcp_data or {}
    if not (_is_vcp_scan_source(payload) or _has_vcp_overlay_fields(payload)):
        return

    trigger_date = str(_pick_payload_value(payload, "触发日期", "日期", "时间", "trigger_date", default=""))[:10]
    theme = theme_manager.current_theme
    date_to_idx = {d: i for i, d in enumerate(dates)}
    trigger_idx = _find_date_idx(trigger_date, date_to_idx)

    markers = _build_vcp_markers(data, trigger_idx, theme)
    raw_box_high = _to_float(_pick_payload_value(payload, "区间最高价", "box_high", default=0))
    raw_box_low = _to_float(_pick_payload_value(payload, "区间最低点", "box_low", default=0))

    peak_indices = _valid_vcp_peak_indices(_vcp_peak_dates(payload), date_to_idx)
    _apply_vcp_box_overlay(data, dates, peak_indices, trigger_idx, raw_box_low, raw_box_high)
    _store_vcp_markers(data, markers)


def build_kline_echarts_payload(df: pd.DataFrame, *, code: str, name: str, vcp_data: dict | None) -> dict:
    theme = theme_manager.current_theme
    up_color = theme["KLINE_UP_COLOR"]
    down_color = theme["KLINE_DOWN_COLOR"]

    dates, klines, vols = _build_ohlcv_payload(df, up_color, down_color)
    closes = df["close"].values
    ma_data = _build_moving_average_data(closes)
    macd_bars, diff_line, dea_line = _build_macd_payload(df, up_color, down_color)
    vol_ma20_data = _build_volume_ma20(vols)

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
        "vcpMarkers": None,
        "vcpLines": None,
        "vcpArea": None,
    }

    if vcp_data:
        inject_vcp_overlays(result, dates, vcp_data)

    return result
