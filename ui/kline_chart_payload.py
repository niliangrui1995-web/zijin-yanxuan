# -*- coding: utf-8 -*-
"""Pure builders for K-line chart context, theme, and ECharts payload."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from PyQt6.QtCore import QUrl

from app.services.ui_runtime_service import MarketCalendar
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


def _signal_value(signal, key: str, default=""):
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _extract_scan_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    for signal in item_data.get("_signals") or []:
        signal_code = str(_signal_value(signal, "code") or _signal_value(signal, "代码") or "").strip()
        if signal_code and signal_code != str(code).strip():
            continue

        source_tab = str(_signal_value(signal, "source_tab") or "").strip()
        signal_type = str(_signal_value(signal, "signal_type") or "").strip()
        if source_tab != "scan" and signal_type != "vcp_scan":
            continue

        raw_payload = _signal_value(signal, "payload", {}) or {}
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        payload.setdefault("代码", code)
        signal_name = str(_signal_value(signal, "name") or "").strip()
        if signal_name:
            payload.setdefault("名称", signal_name)
        observed_at = str(_signal_value(signal, "observed_at") or "").strip()
        if observed_at:
            payload.setdefault("触发日期", observed_at)
        payload["source_tab"] = source_tab or "scan"
        payload["signal_type"] = signal_type or "vcp_scan"
        payload["_vcp_overlay_allowed"] = True
        return payload

    return {}


def _is_vcp_scan_source(payload: dict) -> bool:
    source_key = str(payload.get("__source_tab_key") or "").strip()
    source_tab = str(payload.get("source_tab") or "").strip()
    signal_type = str(payload.get("signal_type") or "").strip()
    return bool(payload.get("_vcp_overlay_allowed")) or source_key == "scan" or source_tab == "scan" or signal_type == "vcp_scan"


def _has_vcp_overlay_fields(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(payload.get(key) not in (None, "", [], {}) for key in ("VCP达标", "VCP收缩详情", "_model_name", "_peak_dates")):
        return True
    if payload.get("_high1_date") or payload.get("_high2_date") or payload.get("_high3_date"):
        return True
    return (
        payload.get("区间最高价") not in (None, "")
        and payload.get("区间最低点") not in (None, "")
    ) or (
        payload.get("box_high") not in (None, "")
        and payload.get("box_low") not in (None, "")
    )


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
    elif _is_vcp_scan_source(resolved):
        for scan_res in scan_results or []:
            if isinstance(scan_res, dict) and str(scan_res.get("代码", "")).strip() == str(code).strip():
                merge_kline_context(resolved, scan_res, overwrite=True)
                resolved["_vcp_overlay_allowed"] = True
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
        "tooltip_bg": t.get("KLINE_TOOLTIP_BG", "rgba(17, 24, 39, 0.92)"),
        "tooltip_text": t.get("KLINE_TOOLTIP_TEXT", "#F3F4F6"),
        "macd_diff": t.get("KLINE_MACD_DIFF", "#FBBF24"),
        "macd_dea": t.get("KLINE_MACD_DEA", "#60A5FA"),
    }

    colors.update({
        "bg_canvas": t.get("KLINE_BG_CANVAS", "#0A0A0A" if is_dark else t["BG_ELEVATED"]),
        "bg_toolbar": t.get("KLINE_BG_TOOLBAR", "#0A0A0A" if is_dark else t["BG_ELEVATED"]),
        "text_primary": t.get("KLINE_WIDGET_TEXT", "#FFF" if is_dark else t["TEXT_PRIMARY"]),
        "text_secondary": t["TEXT_SECONDARY"],
        "text_muted": t.get("KLINE_INFO_COLOR", "#A0A0A0" if is_dark else t["TEXT_MUTED"]),
        "border": t.get("KLINE_TOOLBAR_BORDER", "#222" if is_dark else t["BORDER_DEFAULT"]),
    })

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


def _summary_clean_text(value, default: str = "--") -> str:
    if value in (None, "", [], {}):
        return default

    text = str(value).strip()
    if not text:
        return default

    normalized = text.lower()
    if normalized in {"--", "-", "nan", "none", "null", "nat", "nan/nan"}:
        return default
    return text


def _summary_pick(payload: dict, *keys, default: str = "--") -> str:
    for key in keys:
        if key not in payload:
            continue
        value = _summary_clean_text(payload.get(key), default="")
        if value:
            return value
    return default


def _summary_pick_pct(payload: dict, *keys, default: str = "--") -> str:
    raw = _summary_pick(payload, *keys, default=default)
    if raw == default:
        return default
    if "%" in raw:
        return raw
    try:
        return f"{float(raw):.2f}%"
    except (TypeError, ValueError):
        return raw


def _summary_join(*values, default: str = "--", sep: str = " / ") -> str:
    parts = []
    for value in values:
        clean = _summary_clean_text(value, default="")
        if clean:
            parts.append(clean)
    if not parts:
        return default
    return sep.join(dict.fromkeys(parts))


def _summary_compact_list(*values, default: str = "--", max_items: int = 2, sep: str = "/") -> str:
    parts = []
    for value in values:
        clean = _summary_clean_text(value, default="")
        if clean:
            parts.append(clean)
    unique_parts = list(dict.fromkeys(parts))
    if not unique_parts:
        return default
    visible = unique_parts[:max_items]
    hidden_count = len(unique_parts) - len(visible)
    text = sep.join(visible)
    if hidden_count > 0:
        text += f"+{hidden_count}"
    return text


def _summary_active_labels(payload: dict, mappings: list[tuple[str, tuple[str, ...]]], *, default: str = "--", max_items: int = 2) -> str:
    labels = []
    for label, keys in mappings:
        value = _summary_pick(payload, *keys, default="")
        if value:
            labels.append(label)
    return _summary_compact_list(*labels, default=default, max_items=max_items)


def _summary_parse_float(value) -> float | None:
    if value in (None, "", [], {}):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _summary_format_wan_amount(value) -> str:
    number = _summary_parse_float(value)
    if number is None:
        return "--"
    text = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{text}万"


def _summary_format_signed_wan_amount(value, *, positive_label: str = "净买", negative_label: str = "净卖") -> str:
    number = _summary_parse_float(value)
    if number is None:
        return "--"
    label = positive_label if number >= 0 else negative_label
    return f"{label}{_summary_format_wan_amount(abs(number))}"


def _summary_format_pct_value(value) -> str:
    number = _summary_parse_float(value)
    if number is None:
        return "--"
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _build_watchlist_event_text(payload: dict) -> str:
    parts = []

    block_amount = payload.get("大宗交易金额(万)")
    if _summary_parse_float(block_amount) is not None:
        parts.append(f"大宗 {_summary_format_wan_amount(block_amount)}")
    elif _summary_pick(payload, "大宗交易", default=""):
        parts.append("大宗")

    earnings_qoq = payload.get("业绩环比%")
    if _summary_parse_float(earnings_qoq) is not None:
        parts.append(f"环比 {_summary_format_pct_value(earnings_qoq)}")
    elif _summary_pick(payload, "业绩异动", default=""):
        parts.append("业绩")

    if _summary_pick(payload, "龙虎榜", default=""):
        lhb_net = payload.get("龙虎榜净额(万)")
        if _summary_parse_float(lhb_net) is not None:
            parts.append(f"龙虎 {_summary_format_signed_wan_amount(lhb_net)}")
        else:
            parts.append("龙虎榜")

    if not parts:
        return "--"
    return " | ".join(dict.fromkeys(parts))


def _summary_row(label: str, value, *, highlight: bool = False, limit: int = 12) -> dict:
    raw_value = _summary_clean_text(value)
    return {
        "label": label,
        "value": raw_value,
        "raw_value": raw_value,
        "highlight": highlight,
    }


def _summary_option_row(payload: dict, options: list[tuple[str, tuple[str, ...]]], *, limit: int = 12) -> dict:
    for label, keys in options:
        value = _summary_pick(payload, *keys, default="")
        if value:
            return _summary_row(label, value, limit=limit)
    fallback_label = options[0][0] if options else "--"
    return _summary_row(fallback_label, "--", limit=limit)


def _build_scan_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "扫描信号",
            "rows": [
                _summary_row("评分", _summary_pick(payload, "评分")),
                _summary_row("距突破", _summary_pick(payload, "距突破")),
            ],
        },
        {
            "title": "题材画像",
            "rows": [
                _summary_row("市值", _summary_pick(payload, "市值")),
                _summary_option_row(
                    payload,
                    [
                        ("热门板块", ("热门板块",)),
                        ("热点板块", ("热点板块",)),
                        ("行业概念", ("所属行业与概念",)),
                    ],
                ),
            ],
        },
    ]


def _build_rt_monitor_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "盘中状态",
            "rows": [
                _summary_row("涨幅", _summary_pick_pct(payload, "涨幅%", "涨幅")),
                _summary_row("时间", _summary_pick(payload, "时间")),
            ],
        },
        {
            "title": "策略信号",
            "rows": [
                _summary_row("评分", _summary_pick(payload, "评分")),
                _summary_option_row(
                    payload,
                    [
                        ("突破状态", ("突破状态",)),
                        ("热点板块", ("热点板块", "热门板块")),
                    ],
                ),
            ],
        },
    ]


def _build_watchlist_summary_cards(payload: dict) -> list[dict]:
    source_text = _summary_pick(payload, "来源", default="")
    if not source_text:
        source_tags = payload.get("来源标签")
        if isinstance(source_tags, (list, tuple, set)):
            source_text = _summary_compact_list(*source_tags)

    event_text = _build_watchlist_event_text(payload)

    return [
        {
            "title": "关注画像",
            "rows": [
                _summary_row("来源", source_text),
                _summary_option_row(
                    payload,
                    [
                        ("细分板块", ("细分板块",)),
                        ("热门板块", ("热门板块", "热点板块")),
                        ("行业概念", ("所属行业与概念",)),
                    ],
                ),
            ],
        },
        {
            "title": "事件跟踪",
            "rows": [
                _summary_option_row(
                    payload,
                    [
                        ("催化剂", ("催化剂",)),
                        ("美股日报", ("美股日报",)),
                    ],
                ),
                _summary_row("异动", event_text),
            ],
        },
    ]


def _build_lhb_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "龙虎榜信号",
            "rows": [
                _summary_row("买点", _summary_pick(payload, "买点")),
                _summary_row("上榜次数", _summary_pick(payload, "上榜次数")),
            ],
        },
        {
            "title": "席位动向",
            "rows": [
                _summary_row("机构净买", _summary_pick(payload, "机构净买(万)")),
                _summary_option_row(
                    payload,
                    [
                        ("外资净买", ("外资净买入",)),
                        ("上榜原因", ("上榜原因",)),
                    ],
                ),
            ],
        },
    ]


def _build_foreign_block_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "大宗交易",
            "rows": [
                _summary_row("交易日期", _summary_pick(payload, "交易日期")),
                _summary_row("交易详情", _summary_pick(payload, "交易详情")),
            ],
        },
        {
            "title": "成交信息",
            "rows": [
                _summary_row("折溢价", _summary_pick(payload, "折/溢价率(%)")),
                _summary_row("成交额", _summary_pick(payload, "成交金额(万元)")),
            ],
        },
    ]


def _build_earnings_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "业绩摘要",
            "rows": [
                _summary_row(
                    "类型",
                    _summary_join(
                        _summary_pick(payload, "类型", default=""),
                        _summary_pick(payload, "报告期", default=""),
                        sep=" · ",
                    ),
                ),
                _summary_row("揭晓日", _summary_pick(payload, "揭晓日")),
            ],
        },
        {
            "title": "财务变化",
            "rows": [
                _summary_row("环比", _summary_pick(payload, "环比%")),
                _summary_option_row(
                    payload,
                    [
                        ("同比", ("同比%",)),
                        ("基调", ("基调",)),
                    ],
                ),
            ],
        },
    ]


def _build_na_daily_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "北美情报",
            "rows": [
                _summary_row("细分板块", _summary_pick(payload, "细分板块")),
                _summary_row("股价弹性", _summary_pick(payload, "股价弹性")),
            ],
        },
        {
            "title": "催化风控",
            "rows": [
                _summary_row("催化剂", _summary_pick(payload, "催化剂")),
                _summary_row(
                    "评级/风控",
                    _summary_join(
                        _summary_pick(payload, "评级", default=""),
                        _summary_pick(payload, "风控", default=""),
                    ),
                ),
            ],
        },
    ]


def _build_asian_market_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "市场画像",
            "rows": [
                _summary_row("市场", _summary_pick(payload, "市场")),
                _summary_row("角色", _summary_pick(payload, "角色定位")),
            ],
        },
        {
            "title": "趋势跟踪",
            "rows": [
                _summary_row("赛道", _summary_pick(payload, "赛道")),
                _summary_row("20日涨跌", _summary_pick_pct(payload, "20日涨跌%")),
            ],
        },
    ]


def _build_fund_holdings_summary_cards(payload: dict) -> list[dict]:
    return [
        {
            "title": "机构持仓",
            "rows": [
                _summary_row("主体", _summary_pick(payload, "主体")),
                _summary_row("季度", _summary_pick(payload, "季度")),
            ],
        },
        {
            "title": "仓位变化",
            "rows": [
                _summary_row("变化类型", _summary_pick(payload, "变化类型")),
                _summary_row("持股变化", _summary_pick(payload, "持股变化")),
            ],
        },
    ]


def _build_generic_summary_cards(payload: dict) -> list[dict]:
    pct_value = _summary_pick(payload, "涨幅%", "涨幅", default="")
    return [
        {
            "title": "交易快照",
            "rows": [
                _summary_option_row(
                    payload,
                    [
                        ("现价", ("现价", "市价")),
                        ("价格", ("收盘", "close")),
                    ],
                ),
                (
                    _summary_row("涨幅", _summary_pick_pct(payload, "涨幅%", "涨幅"))
                    if pct_value
                    else _summary_option_row(payload, [("状态", ("状态",))])
                ),
            ],
        },
        {
            "title": "补充信息",
            "rows": [
                _summary_option_row(
                    payload,
                    [
                        ("市值", ("市值",)),
                        ("市场", ("市场",)),
                    ],
                ),
                _summary_option_row(
                    payload,
                    [
                        ("板块", ("热门板块", "热点板块", "细分板块")),
                        ("赛道", ("赛道",)),
                        ("行业", ("所属行业与概念",)),
                    ],
                ),
            ],
        },
    ]


def build_kline_summary_cards(vcp_data: dict | None, is_fav: bool = False) -> list[dict]:
    payload = vcp_data or {}
    source_tab_key = _summary_pick(payload, "__source_tab_key", default="").strip()

    builders = {
        "scan": _build_scan_summary_cards,
        "rt_monitor": _build_rt_monitor_summary_cards,
        "watchlist": _build_watchlist_summary_cards,
        "lhb": _build_lhb_summary_cards,
        "foreign_block": _build_foreign_block_summary_cards,
        "earnings": _build_earnings_summary_cards,
        "na_daily": _build_na_daily_summary_cards,
        "asian_market": _build_asian_market_summary_cards,
        "fund_holdings": _build_fund_holdings_summary_cards,
    }

    cards = builders.get(source_tab_key, _build_generic_summary_cards)(payload)
    cards = list(cards[:2])
    while len(cards) < 2:
        cards.append({"title": "--", "rows": [_summary_row("--", "--"), _summary_row("--", "--")]})

    rps_raw = _summary_pick(payload, "RPS强度", "rps_str", default="--")
    cards.append(
        {
            "title": "强度跟踪",
            "rows": [
                _summary_row("RPS", rps_raw),
                _summary_row("关注", "已关注" if is_fav else "未关注", highlight=is_fav),
            ],
        }
    )
    return cards


def build_kline_summary_items(vcp_data: dict | None, is_fav: bool = False) -> dict[str, str]:
    payload = vcp_data or {}

    trigger_text = _summary_pick(payload, "触发日期", "日期", "时间", "trigger_date", default="--")
    trigger_date = trigger_text[:10] if trigger_text != "--" else "--"

    high_price = _summary_parse_float(
        _summary_pick(payload, "区间最高价", "box_high", default="")
    )
    low_price = _summary_parse_float(
        _summary_pick(payload, "区间最低点", "box_low", default="")
    )
    if high_price is not None and low_price is not None:
        range_text = f"{low_price:.2f} - {high_price:.2f}"
    else:
        range_text = "--"

    return {
        "形态": _summary_pick(payload, "突破状态", "形态", default="--"),
        "触发": trigger_date,
        "区间": range_text,
        "振幅": _summary_pick_pct(payload, "区间振幅", "振幅", default="--"),
        "RPS": _summary_pick(payload, "RPS强度", "rps_str", default="--"),
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
        let rawData = {data_json};

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

        function buildOption() {{
            const data = splitData(rawData);
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
                    backgroundColor: '{theme_colors['tooltip_bg']}',
                    borderWidth: 0,
                    textStyle: {{ color: '{theme_colors['tooltip_text']}' }}
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
                        lineStyle: {{ width: 1.1, color: '{theme_colors['macd_diff']}' }}
                    }},
                    {{
                        name: 'DEA',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.dea,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: {{ width: 1.1, color: '{theme_colors['macd_dea']}' }}
                    }}
                ]
            }};
        }}

        chart.setOption(buildOption());

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


def inject_vcp_overlays(data: dict, dates: list, vcp_data: dict | None) -> None:
    payload = vcp_data or {}
    if not (_is_vcp_scan_source(payload) or _has_vcp_overlay_fields(payload)):
        return

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
    theme = theme_manager.current_theme
    date_to_idx = {d: i for i, d in enumerate(dates)}

    def _find_date_idx(value) -> int:
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

    trigger_idx = _find_date_idx(trigger_date)

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

    raw_box_high = _to_float(_pick("区间最高价", "box_high", default=0))
    raw_box_low = _to_float(_pick("区间最低点", "box_low", default=0))

    peak_dates = _pick("_peak_dates", "peak_dates", default=[]) or []
    if isinstance(peak_dates, str):
        peak_dates = [peak_dates]
    if not peak_dates:
        for key in ["_high1_date", "_high2_date", "_high3_date"]:
            if payload.get(key):
                peak_dates.append(payload[key])

    if peak_dates:
        valid_indices = []
        for d in peak_dates:
            idx = _find_date_idx(d)
            if idx != -1:
                valid_indices.append(idx)

        if valid_indices:
            x_start = min(valid_indices)
            x_end = max(valid_indices)
            x_end = max(x_start, x_end)
            box_slice = data["klines"][x_start:x_end + 1]
            derived_lows = [float(item[2]) for item in box_slice if item and len(item) > 3 and item[2] is not None]
            derived_highs = [float(item[3]) for item in box_slice if item and len(item) > 3 and item[3] is not None]
            box_low = min(derived_lows) if derived_lows else raw_box_low
            box_high = max(derived_highs) if derived_highs else raw_box_high
            if box_high <= 0 or box_low <= 0:
                if markers:
                    data["vcpMarkers"] = markers
                return

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
