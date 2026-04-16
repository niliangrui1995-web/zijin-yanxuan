# -*- coding: utf-8 -*-
"""Pure builders for K-line chart context, theme, and ECharts payload."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from PyQt6.QtCore import QUrl

from core.market_calendar import MarketCalendar
from ui.theme import theme_manager


def merge_kline_context(base: dict, extra: dict, *, overwrite: bool = False) -> dict:
    if not isinstance(extra, dict):
        return base

    for key, value in extra.items():
        if value in (None, "", [], {}):
            continue
        if overwrite or key not in base or base.get(key) in (None, "", [], {}):
            base[key] = value
    return base


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

    for scan_res in scan_results or []:
        if isinstance(scan_res, dict) and str(scan_res.get("代码", "")).strip() == str(code).strip():
            merge_kline_context(resolved, scan_res, overwrite=True)
            break

    resolved["代码"] = str(resolved.get("代码") or code)
    resolved["名称"] = str(resolved.get("名称") or name or code)
    resolved["code"] = resolved["代码"]
    resolved["name"] = resolved["名称"]
    return resolved


def build_kline_theme_colors() -> dict:
    t = theme_manager.current_theme
    is_dark = theme_manager.is_dark()

    colors = {
        "up_color": t["KLINE_UP_COLOR"],
        "down_color": t["KLINE_DOWN_COLOR"],
        "ma10": t["KLINE_MA10"],
        "ma20": t["KLINE_MA20"],
        "ma50": t["KLINE_MA50"],
        "ma150": t["KLINE_MA150"],
        "ma200": t["KLINE_MA200"],
        "vol_ma20": t["KLINE_VOL_MA20"],
        "grid_line": t["KLINE_GRID_LINE"],
        "axis_line": t["KLINE_AXIS_LINE"],
        "axis_label": t["KLINE_AXIS_LABEL"],
        "pointer_bg": t["KLINE_POINTER_BG"],
        "vcp_star": t["KLINE_VCP_STAR"],
        "vcp_line": t["KLINE_VCP_LINE"],
        "vcp_line_soft": t["KLINE_VCP_LINE_SOFT"],
        "vcp_area": t["KLINE_VCP_AREA"],
        "vcp_guide": t["KLINE_VCP_GUIDE"],
        "vcp_breakout_bg": t["KLINE_VCP_BREAKOUT_BG"],
    }

    if is_dark:
        colors.update({
            "bg_canvas": "#0A0A0A",
            "bg_toolbar": "#0A0A0A",
            "text_primary": "#FFF",
            "text_secondary": "#D1D4DC",
            "text_muted": "#A0A0A0",
            "border": "#222",
        })
    else:
        colors.update({
            "bg_canvas": t["BG_ELEVATED"],
            "bg_toolbar": t["BG_ELEVATED"],
            "text_primary": t["TEXT_PRIMARY"],
            "text_secondary": t["TEXT_SECONDARY"],
            "text_muted": t["TEXT_MUTED"],
            "border": t["BORDER_DEFAULT"],
        })

    return colors


def build_kline_window_palette(theme: dict = None, is_dark: bool | None = None) -> dict:
    if theme is None:
        theme = theme_manager.current_theme
    if is_dark is None:
        is_dark = theme.get("name") == "墨渊"

    if is_dark:
        return {
            "widget_bg": "#0C1016",
            "widget_text": "#F5F7FA",
            "toolbar_bg": "#11161D",
            "toolbar_border": "#222A33",
            "summary_bg": "#0F141B",
            "info_color": "#8B98A8",
            "btn_border": "#303947",
            "btn_hover_bg": "rgba(255,255,255,0.05)",
            "btn_hover_text": "#F5F7FA",
            "btn_disabled_text": "#5A6573",
            "btn_disabled_border": "#262E39",
            "chart_bg": "#0B0F14",
            "nav_bg": "rgba(255,255,255,0.04)",
            "badge_bg": "rgba(239, 68, 68, 0.10)",
            "badge_fg": "#FCA5A5",
            "summary_border": "rgba(148, 163, 184, 0.10)",
        }

    unified_bg = theme["BG_ELEVATED"]
    return {
        "widget_bg": unified_bg,
        "widget_text": theme["TEXT_PRIMARY"],
        "toolbar_bg": unified_bg,
        "toolbar_border": theme["BORDER_DEFAULT"],
        "summary_bg": unified_bg,
        "info_color": theme["TEXT_MUTED"],
        "btn_border": theme["BORDER_STRONG"],
        "btn_hover_bg": theme["TAB_HOVER_BG"],
        "btn_hover_text": theme["TEXT_PRIMARY"],
        "btn_disabled_text": theme["TEXT_DISABLED"],
        "btn_disabled_border": theme["BORDER_DEFAULT"],
        "chart_bg": unified_bg,
        "nav_bg": theme["BG_BUTTON"],
        "badge_bg": "rgba(239, 68, 68, 0.10)",
        "badge_fg": theme["BRAND_DEEP"],
        "summary_border": theme["BORDER_SUBTLE"],
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


def build_kline_summary_items(vcp_data: dict | None, is_fav: bool = False) -> dict:
    payload = vcp_data or {}

    def _pick(*keys, default="--"):
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return value
        return default

    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    box_high = _to_float(_pick("区间最高价", "box_high", default=None))
    box_low = _to_float(_pick("区间最低点", "box_low", "box_low_price", default=None))
    if box_high is not None and box_low is not None:
        range_text = f"{box_low:.2f} - {box_high:.2f}"
    else:
        range_text = "--"

    rps_raw = str(_pick("RPS强度", "rps_str", default="--")).strip()
    if rps_raw in {"", "-", "nan/nan"}:
        rps_raw = "--"

    return {
        "形态": str(_pick("突破状态", "状态", default="--")),
        "触发": str(_pick("触发日期", "日期", "trigger_date", default="--"))[:10] or "--",
        "区间": range_text,
        "振幅": str(_pick("区间振幅", "振幅", default="--")),
        "RPS": rps_raw,
        "关注": "已关注" if is_fav else "未关注",
    }


def build_kline_html(title: str, echarts_data: dict, echarts_js_path: str, theme_colors: dict) -> str:
    js_url = QUrl.fromLocalFile(echarts_js_path).toString()
    data_json = json.dumps(echarts_data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="{js_url}"></script>
    <style>
        body {{ margin: 0; padding: 0; background-color: {theme_colors['bg_canvas']}; color: {theme_colors['text_secondary']}; font-family: "Microsoft YaHei UI", sans-serif; overflow: hidden; }}
        #chart {{ width: 100vw; height: calc(100vh - 30px); margin-top: 30px; }}

        .top-toolbar {{ position: absolute; top: 0; left: 0; right: 0; height: 30px; background: {theme_colors['bg_toolbar']}; border-bottom: 1px solid {theme_colors['border']}; display: flex; align-items: center; padding: 0 12px; z-index: 100; gap: 10px; }}
        .info-item {{ font-size: 11px; color: {theme_colors['text_muted']}; white-space: nowrap; }}
        .info-val {{ font-size: 11px; font-weight: 700; margin-left: 2px; color: {theme_colors['text_secondary']}; }}
        .ma-display {{ margin-left: auto; font-size: 11px; font-weight: 700; display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap; }}
        .ma-display span.ma10 {{ color: {theme_colors['ma10']}; }}
        .ma-display span.ma20 {{ color: {theme_colors['ma20']}; }}
        .ma-display span.ma50 {{ color: {theme_colors['ma50']}; }}
        .ma-display span.ma150 {{ color: {theme_colors['ma150']}; }}
        .ma-display span.ma200 {{ color: {theme_colors['ma200']}; }}
    </style>
</head>
<body>
    <div class="top-toolbar" id="toolbar">
        <div class="info-item">日期: <span id="v-date" class="info-val" style="color: {theme_colors['text_primary']}">-</span></div>
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
        const rawData = {data_json};

        const upColor = '{theme_colors['up_color']}';
        const downColor = '{theme_colors['down_color']}';

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

        const data = splitData(rawData);

        function buildOption() {{
            return {{
                animation: false,
                backgroundColor: '{theme_colors['bg_canvas']}',
                legend: {{
                    show: false
                }},
                axisPointer: {{
                    link: [{{ xAxisIndex: 'all' }}],
                    label: {{
                        backgroundColor: '{theme_colors['pointer_bg']}'
                    }}
                }},
                tooltip: {{
                    trigger: 'axis',
                    showContent: false,
                    axisPointer: {{
                        type: 'cross'
                    }},
                    backgroundColor: 'rgba(17, 24, 39, 0.92)',
                    borderWidth: 0,
                    textStyle: {{ color: '#F3F4F6' }}
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
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
                        axisLabel: {{ color: '{theme_colors['axis_label']}' }},
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
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
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
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
                        axisLabel: {{ color: '{theme_colors['axis_label']}' }},
                        min: 'dataMin',
                        max: 'dataMax'
                    }}
                ],
                yAxis: [
                    {{
                        scale: true,
                        splitArea: {{ show: false }},
                        splitLine: {{ lineStyle: {{ color: '{theme_colors['grid_line']}' }} }},
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
                        axisLabel: {{ color: '{theme_colors['axis_label']}' }}
                    }},
                    {{
                        scale: true,
                        gridIndex: 1,
                        splitNumber: 2,
                        axisLabel: {{ color: '{theme_colors['axis_label']}' }},
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
                        splitLine: {{ show: false }}
                    }},
                    {{
                        scale: true,
                        gridIndex: 2,
                        splitNumber: 2,
                        axisLabel: {{ color: '{theme_colors['axis_label']}' }},
                        axisLine: {{ lineStyle: {{ color: '{theme_colors['axis_line']}' }} }},
                        splitLine: {{ lineStyle: {{ color: '{theme_colors['grid_line']}' }} }}
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
                        end: 100
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
                        markPoint: rawData.vcpMarkers ? {{
                            symbolKeepAspect: true,
                            data: rawData.vcpMarkers
                        }} : undefined,
                        markLine: rawData.vcpLines ? {{
                            symbol: ['none', 'none'],
                            silent: true,
                            animation: false,
                            label: {{ show: false }},
                            lineStyle: {{
                                color: '{theme_colors['vcp_line']}',
                                width: 1.2,
                                type: 'solid'
                            }},
                            data: rawData.vcpLines
                        }} : undefined,
                        markArea: rawData.vcpArea ? {{
                            silent: true,
                            animation: false,
                            itemStyle: {{
                                color: '{theme_colors['vcp_area']}'
                            }},
                            data: rawData.vcpArea
                        }} : undefined
                    }},
                    {{
                        name: 'MA10',
                        type: 'line',
                        data: rawData.ma10,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: '{theme_colors['ma10']}' }}
                    }},
                    {{
                        name: 'MA20',
                        type: 'line',
                        data: rawData.ma20,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: '{theme_colors['ma20']}' }}
                    }},
                    {{
                        name: 'MA50',
                        type: 'line',
                        data: rawData.ma50,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: '{theme_colors['ma50']}' }}
                    }},
                    {{
                        name: 'MA150',
                        type: 'line',
                        data: rawData.ma150,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: '{theme_colors['ma150']}' }}
                    }},
                    {{
                        name: 'MA200',
                        type: 'line',
                        data: rawData.ma200,
                        smooth: true,
                        showSymbol: false,
                        lineStyle: {{ width: 1.2, color: '{theme_colors['ma200']}' }}
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
                        lineStyle: {{ width: 1.1, color: '{theme_colors['vol_ma20']}' }}
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
                        lineStyle: {{ width: 1.1, color: '#FBBF24' }}
                    }},
                    {{
                        name: 'DEA',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.dea,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: {{ width: 1.1, color: '#60A5FA' }}
                    }}
                ]
            }};
        }}

        chart.setOption(buildOption());

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


def inject_vcp_overlays(data: dict, dates: list, vcp_data: dict | None) -> None:
    payload = vcp_data or {}

    def _pick(*keys, default=""):
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

    trigger_date = str(_pick("触发日期", "日期", "时间", "trigger_date", default=""))[:10]
    trigger_idx = -1
    theme = theme_manager.current_theme
    date_to_idx = {d: i for i, d in enumerate(dates)}

    if trigger_date:
        for d, idx in date_to_idx.items():
            if trigger_date in d:
                trigger_idx = idx
                break

    markers = []
    if trigger_idx != -1:
        kline = data["klines"][trigger_idx]
        markers = [{
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
        }]

    box_high = _to_float(_pick("区间最高价", "box_high", default=0))
    box_low = _to_float(_pick("区间最低点", "box_low", default=0))

    peak_dates = _pick("_peak_dates", "peak_dates", default=[]) or []
    if isinstance(peak_dates, str):
        peak_dates = [peak_dates]
    if not peak_dates:
        for key in ["_high1_date", "_high2_date", "_high3_date"]:
            if payload.get(key):
                peak_dates.append(payload[key])

    if box_high > 0 and box_low > 0 and peak_dates:
        valid_indices = []
        for d in peak_dates:
            d_short = str(d)[:10]
            if d_short in date_to_idx:
                valid_indices.append(date_to_idx[d_short])
            else:
                d_no_dash = d_short.replace("-", "")
                for date_key, idx in date_to_idx.items():
                    if date_key.replace("-", "") == d_no_dash:
                        valid_indices.append(idx)
                        break

        if valid_indices:
            x_start = min(valid_indices)
            x_end = trigger_idx if trigger_idx != -1 else len(dates) - 1
            x_end = max(x_start, x_end)

            data["vcpArea"] = [[
                {"xAxis": dates[x_start], "yAxis": box_low},
                {"xAxis": dates[x_end], "yAxis": box_high},
            ]]

            vcp_lines = []
            peak_seen = set()
            for xi in valid_indices:
                if xi in peak_seen or xi < 0 or xi >= len(data["klines"]):
                    continue
                peak_seen.add(xi)
                vcp_lines.append([
                    {"xAxis": dates[xi], "yAxis": box_low},
                    {"xAxis": dates[xi], "yAxis": box_high},
                ])

            vcp_lines.append([
                {"xAxis": dates[x_start], "yAxis": box_high},
                {"xAxis": dates[x_end], "yAxis": box_high},
            ])
            vcp_lines.append([
                {"xAxis": dates[x_start], "yAxis": box_low},
                {"xAxis": dates[x_end], "yAxis": box_low},
            ])
            data["vcpLines"] = vcp_lines

    if markers:
        data["vcpMarkers"] = markers


def build_kline_echarts_payload(df: pd.DataFrame, *, code: str, name: str, vcp_data: dict | None) -> dict:
    dates = []
    klines = []
    vols = []

    theme = theme_manager.current_theme
    up_color = theme["KLINE_UP_COLOR"]
    down_color = theme["KLINE_DOWN_COLOR"]

    for dt, row in df.iterrows():
        o = float(row["open"])
        c = float(row["close"])
        h = float(row["high"])
        low_price = float(row["low"])
        v = float(row.get("volume", 0))

        date_str = dt.strftime("%Y-%m-%d")
        dates.append(date_str)
        klines.append([o, c, low_price, h])

        vols.append({
            "value": v,
            "itemStyle": {"color": up_color if c >= o else down_color},
        })

    closes = df["close"].values
    ma_config = [
        (10, "ma10"),
        (20, "ma20"),
        (50, "ma50"),
        (150, "ma150"),
        (200, "ma200"),
    ]
    ma_data = {}
    for period, key in ma_config:
        if len(closes) >= period:
            ma = pd.Series(closes).rolling(period).mean()
            ma_data[key] = [round(v, 2) if not np.isnan(v) else None for v in ma.values]
        else:
            ma_data[key] = [None] * len(closes)

    macd_bars = []
    diff_line = []
    dea_line = []
    if "MACD" in df.columns:
        for _, row in df.iterrows():
            macd_val = float(row.get("MACD", 0) or 0)
            signal_val = float(row.get("MACD_Signal", 0) or 0)
            hist_val = float(row.get("MACD_Hist", 0) or 0)

            macd_bars.append({
                "value": hist_val,
                "itemStyle": {"color": up_color if hist_val >= 0 else down_color},
            })
            diff_line.append(round(macd_val, 4))
            dea_line.append(round(signal_val, 4))

    vol_values = [v["value"] if isinstance(v, dict) else v for v in vols]
    vol_ma20 = pd.Series(vol_values).rolling(20).mean()
    vol_ma20_data = [round(v, 0) if not np.isnan(v) else None for v in vol_ma20.values]

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
