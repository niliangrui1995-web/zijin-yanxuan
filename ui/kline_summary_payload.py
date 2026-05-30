# -*- coding: utf-8 -*-
"""Summary-card payload builders for K-line windows."""

from __future__ import annotations


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


def _summary_active_labels(
    payload: dict, mappings: list[tuple[str, tuple[str, ...]]], *, default: str = "--", max_items: int = 2
) -> str:
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
                        ("备注", ("摘要", "备注")),
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

    high_price = _summary_parse_float(_summary_pick(payload, "区间最高价", "box_high", default=""))
    low_price = _summary_parse_float(_summary_pick(payload, "区间最低点", "box_low", default=""))
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


__all__ = ["build_kline_summary_cards", "build_kline_summary_items"]
