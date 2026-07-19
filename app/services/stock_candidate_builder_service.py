"""Pure row shaping for the comprehensive stock-candidate view."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from domains.stock_context.models import StockSignal

ANCHOR_SOURCE_KEYS = frozenset({"ai_industry_chain", "na_daily"})
ANCHOR_SOURCE_GROUP = "ai_na_anchor"
SOURCE_LABELS = {
    "na_daily": "北美战报",
    "ai_industry_chain": "AI产业链",
    "foreign_block": "大宗交易",
    "earnings": "业绩异动",
    "lhb": "龙虎榜",
    "fund_holdings": "基金持仓",
    "scan": "VCP扫描",
    "watchlist": "关注池",
}


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _source_label(signal: StockSignal, tab_titles: Mapping[str, str]) -> str:
    source = _text(signal.source_tab) or _text(signal.source_label)
    return _text(tab_titles.get(source)) or SOURCE_LABELS.get(source) or source or "--"


def _signal_name(signal: StockSignal) -> str:
    return _text(signal.name) or _text(signal.payload.get("名称")) or _text(signal.payload.get("name"))


def _is_quote_value(value) -> bool:
    return _text(value) not in {"", "--", "-", "None", "nan", "NaN"}


def _first_payload_value(signals: Sequence[StockSignal], keys: Sequence[str]) -> str:
    for signal in signals:
        for key in keys:
            value = signal.payload.get(key)
            if _is_quote_value(value):
                return _text(value)
    return "--"


def _candidate_summary(signal: StockSignal) -> str:
    if _text(signal.signal_type) != "vcp_scan" and _text(signal.source_tab) != "scan":
        return _text(signal.summary)
    trigger_date = _text(signal.payload.get("触发日期")) or _text(signal.observed_at)
    rps = _text(signal.payload.get("RPS强度"))
    parts = [f"触发日期 {trigger_date}" for _ in (0,) if trigger_date]
    parts.extend(f"RPS {rps}" for _ in (0,) if rps)
    return " | ".join(parts) or "VCP扫描命中"


def _signal_sector(signal: StockSignal) -> str:
    keys = ("细分板块", "细分环节", "行业", "板块", "热门板块", "热点板块", "subsector")
    for key in keys:
        value = signal.payload.get(key)
        if _is_quote_value(value):
            return _text(value)
    if _text(signal.signal_type) == "subsector" and _is_quote_value(signal.summary):
        return _text(signal.summary)
    return ""


def _candidate_sector(signals: Sequence[StockSignal]) -> str:
    for source in ("ai_industry_chain", "na_daily"):
        for signal in signals:
            if _text(signal.source_tab) == source and (sector := _signal_sector(signal)):
                return sector
    return ""


def _source_group(signal: StockSignal) -> str:
    source = _text(signal.source_tab)
    return ANCHOR_SOURCE_GROUP if source in ANCHOR_SOURCE_KEYS else source


def _unique_values(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _effective_signal_count(signals: Sequence[StockSignal]) -> int:
    source_groups = [_source_group(signal) for signal in signals]
    anchor_count = min(1, source_groups.count(ANCHOR_SOURCE_GROUP))
    return anchor_count + sum(group != ANCHOR_SOURCE_GROUP for group in source_groups)


def _candidate_identity(signals: Sequence[StockSignal], tab_titles: Mapping[str, str]) -> tuple[str, list[str], list[str]]:
    name = next((name for signal in signals if (name := _signal_name(signal))), "")
    sources = _unique_values(_source_label(signal, tab_titles) for signal in signals)
    source_groups = _unique_values(_source_group(signal) for signal in signals)
    return name, sources, source_groups


def _candidate_summaries(signals: Sequence[StockSignal]) -> list[str]:
    return _unique_values(_candidate_summary(signal) for signal in signals)[:3]


def _candidate_score(signals: Sequence[StockSignal], sources: Sequence[str]) -> int:
    signal_types = {_text(signal.signal_type) for signal in signals if _text(signal.signal_type)}
    return len(sources) * 10 + len(signals) + len(signal_types)


def _candidate_row(
    code: str,
    signals: Sequence[StockSignal],
    tab_titles: Mapping[str, str],
) -> dict | None:
    if not signals or not any(_text(signal.source_tab) in ANCHOR_SOURCE_KEYS for signal in signals):
        return None
    name, sources, source_groups = _candidate_identity(signals, tab_titles)
    if len(source_groups) < 2:
        return None
    return {
        "代码": code,
        "名称": name or code,
        "市价": _first_payload_value(signals, ("市价", "现价", "最新价", "最新", "收盘")),
        "涨幅%": _first_payload_value(signals, ("涨幅%", "涨幅", "涨跌%", "涨跌")),
        "市值": _first_payload_value(signals, ("市值", "总市值")),
        "共振分": _candidate_score(signals, sources),
        "来源数": len(source_groups),
        "信号数": _effective_signal_count(signals),
        "来源": "｜".join(sources),
        "核心信号": "；".join(_candidate_summaries(signals)),
        "最近时间": max((_text(signal.observed_at) or _text(signal.refreshed_at) for signal in signals), default=""),
        "细分板块": _candidate_sector(signals),
        "_signals": list(signals),
    }


def build_stock_candidate_rows(
    context: Mapping[str, Sequence[StockSignal]],
    *,
    tab_titles: Mapping[str, str] | None = None,
) -> list[dict]:
    titles = tab_titles or {}
    rows: list[dict] = []
    for code, raw_signals in sorted((context or {}).items()):
        signals = [signal for signal in (raw_signals or []) if isinstance(signal, StockSignal)]
        row = _candidate_row(code, signals, titles)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: (int(row.get("共振分", 0) or 0), int(row.get("来源数", 0) or 0)), reverse=True)
    return rows


__all__ = ["ANCHOR_SOURCE_GROUP", "ANCHOR_SOURCE_KEYS", "build_stock_candidate_rows"]
