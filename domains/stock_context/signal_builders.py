"""Pure row-to-signal transformations for stock-context sources.

The functions in this module deliberately know nothing about Qt, widgets,
files, databases, engines, or background-task runners.  They are the golden
business contract used by the single StockContextQueryService pipeline.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from domains.stock_context.models import StockSignal

KEY_CODE = "代码"
KEY_NAME = "名称"
KEY_CATALYST = "催化剂"
KEY_CATALYST_EMOJI = "📠催化剂"
KEY_SUBSECTOR = "细分板块"
KEY_OLD_CHAIN_SEGMENT = "细分环节"
KEY_REMARK = "备注"
KEY_DETAIL = "交易详情"
KEY_BUY_BRANCH = "买方营业部"
KEY_SELL_BRANCH = "卖方营业部"
KEY_AMOUNT_WAN = "成交金额(万元)"
KEY_QOQ_PCT = "环比%"
KEY_REPORT_PERIOD = "报告期"
KEY_REPORT_TYPE = "类型"
KEY_REPORT_NAME = "财报名称"
KEY_REPORT_TITLE = "报告名称"
KEY_REVEAL_DATE = "揭晓日"
KEY_DISCOVERED_AT = "发现时间"
KEY_EARNINGS_MARK_DATE = "业绩日"
KEY_EARNINGS_TEXT = "业绩异动"
KEY_LAST_LISTED_RAW = "_最近上榜_raw"
KEY_LAST_LISTED = "最近上榜"
KEY_NET_WAN = "上榜净买额(万)"
KEY_INST_WAN = "机构净买(万)"
KEY_FOREIGN_WAN = "外资净买(万)"
KEY_TRIGGER_DATE = "触发日期"
KEY_SCORE = "评分"
KEY_RPS_STRENGTH = "RPS强度"
KEY_BREAK_DISTANCE = "距突破"
KEY_BREAK_STATUS = "突破状态"
KEY_HOT_SECTOR = "热门板块"
KEY_SUBJECT = "主体"
KEY_SUBJECT_CODE = "主体代码"
KEY_CAPITAL_ATTRIBUTE = "资金属性"
KEY_QUARTER = "季度"
KEY_CHANGE_TYPE = "变化类型"
KEY_CURRENT_RATIO = "本期占比"
KEY_HOLDING_DELTA = "持股变化"

RAW_STOCK_CODE = "股票代码"
RAW_STOCK_NAME = "股票名称"
RAW_STOCK_SHORT_NAME = "股票简称"
RAW_QOQ_PCT = "环比增速_百分比"
RAW_DISCLOSURE_DATE = "公告日期"
RAW_DATA_TYPE = "数据类型"

TEXT_BUY = "买入"
TEXT_SELL = "卖出"
TEXT_INST_ONLY = "机构专用"
TEXT_BLOCK_TRADE_MATCH = "大宗对倒"
TEXT_INST_NET_BUY = "机构净买"
TEXT_INST_NET_SELL = "机构净卖"
TEXT_FOREIGN_NET_BUY = "外资净买"
TEXT_FOREIGN_NET_SELL = "外资净卖"
TEXT_NET_BUY = "净买"
TEXT_NET_SELL = "净卖"

SIGNAL_CATALYST = "catalyst"
SIGNAL_SUBSECTOR = "subsector"
SIGNAL_BLOCK_TRADE = "block_trade"
SIGNAL_EARNINGS = "earnings"
SIGNAL_LHB = "lhb"
SIGNAL_VCP_SCAN = "vcp_scan"
SIGNAL_FUND_HOLDING = "fund_holding"

FUND_HOLDING_ALLOWED_CHANGE_TYPES = frozenset({"新进", "增持"})
DEFAULT_SOURCE_ORDER = (
    "scan",
    "ai_industry_chain",
    "na_daily",
    "foreign_block",
    "earnings",
    "fund_holdings",
    "lhb",
)
RADAR_SOURCE_KEYS = frozenset({"ai_industry_chain", "na_daily", "foreign_block", "earnings", "lhb"})
KLINE_SOURCE_KEYS = frozenset({"scan", "earnings"})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value or "").replace(",", "").strip()
        if not text:
            return float(default)
        return float(text)
    except (TypeError, ValueError):
        return float(default)


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _first_text(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _append_unique(values_by_code: dict[str, list[str]], code: str, value: str) -> None:
    values = values_by_code.setdefault(code, [])
    if value and value not in values:
        values.append(value)


def compact_block_trade_branch(branch: str, foreign_keywords: Sequence[str]) -> str:
    text = str(branch or "").strip()
    if not text:
        return ""
    for keyword in foreign_keywords:
        if keyword in text:
            return keyword
    if TEXT_INST_ONLY in text:
        return TEXT_INST_ONLY
    return ""


def _block_trade_for_buy(buy_label: str, sell_label: str, amount: float) -> tuple[str, float] | None:
    if buy_label:
        return f"{buy_label}{TEXT_BUY}{amount:.0f}万", amount
    if sell_label:
        return f"{sell_label}{TEXT_SELL}{amount:.0f}万", amount
    return None


def _block_trade_for_sell(buy_label: str, sell_label: str, amount: float) -> tuple[str, float] | None:
    if sell_label:
        return f"{sell_label}{TEXT_SELL}{amount:.0f}万", amount
    if buy_label:
        return f"{buy_label}{TEXT_BUY}{amount:.0f}万", amount
    return None


def _directional_block_trade(
    detail: str,
    buy_label: str,
    sell_label: str,
    amount: float,
) -> tuple[str, float] | None:
    if TEXT_BUY in detail:
        return _block_trade_for_buy(buy_label, sell_label, amount)
    if TEXT_SELL in detail:
        return _block_trade_for_sell(buy_label, sell_label, amount)
    return None


def build_watchlist_block_trade_signal(
    detail: str,
    buy: str,
    sell: str,
    amount: float,
    foreign_keywords: Sequence[str],
) -> tuple[str, float]:
    if amount < 0.01:
        return "", 0.0

    buy_label = compact_block_trade_branch(buy, foreign_keywords)
    sell_label = compact_block_trade_branch(sell, foreign_keywords)
    directional = _directional_block_trade(str(detail or ""), buy_label, sell_label, amount)
    if directional is not None:
        return directional
    if buy == sell and bool(buy):
        return f"{TEXT_BLOCK_TRADE_MATCH} {amount:.0f}万", amount
    return "", 0.0


def build_na_daily_signals(rows: Sequence[Mapping[str, Any]]) -> list[StockSignal]:
    signals: list[StockSignal] = []
    for row_idx, raw_row in enumerate(rows):
        row = dict(raw_row)
        code = str(row.get(KEY_CODE, "")).strip()
        if not code:
            continue
        name = str(row.get(KEY_NAME, "") or "")
        catalyst = str(row.get(KEY_CATALYST, "") or row.get(KEY_CATALYST_EMOJI, "") or "").strip()
        if catalyst:
            signals.append(
                StockSignal(
                    code=code,
                    name=name,
                    source_tab="na_daily",
                    source_label="na_daily",
                    signal_type=SIGNAL_CATALYST,
                    summary=catalyst,
                    row_ref=row_idx,
                    payload=row,
                )
            )
        subsector = str(row.get(KEY_SUBSECTOR, "") or "").strip()
        if subsector:
            signals.append(
                StockSignal(
                    code=code,
                    name=name,
                    source_tab="na_daily",
                    source_label="na_daily",
                    signal_type=SIGNAL_SUBSECTOR,
                    summary=subsector,
                    row_ref=row_idx,
                    payload=row,
                )
            )
    return signals


def _ai_row_values(row: Mapping[str, Any]) -> tuple[str, str, str] | None:
    code = _text(row.get(KEY_CODE))
    if not code:
        return None
    segment = _first_text(row, (KEY_SUBSECTOR, KEY_OLD_CHAIN_SEGMENT))
    remark = _text(row.get(KEY_REMARK))
    if not segment and not remark:
        return None
    return code, segment, remark


def _ai_signal(
    code: str,
    row: Mapping[str, Any],
    row_ref: int | None,
    segments: Sequence[str],
    remarks: Sequence[str],
) -> StockSignal:
    payload = dict(row)
    payload[KEY_SUBSECTOR] = " / ".join(segments)
    payload[KEY_REMARK] = " / ".join(remarks)
    return StockSignal(
        code=code,
        name=_text(row.get(KEY_NAME)),
        source_tab="ai_industry_chain",
        source_label="ai_industry_chain",
        signal_type=SIGNAL_SUBSECTOR,
        summary=payload[KEY_SUBSECTOR],
        row_ref=row_ref,
        payload=payload,
    )


def build_ai_chain_signals(rows: Sequence[Mapping[str, Any]]) -> list[StockSignal]:
    rows_by_code: dict[str, dict[str, Any]] = {}
    row_refs_by_code: dict[str, int] = {}
    segments_by_code: dict[str, list[str]] = {}
    remarks_by_code: dict[str, list[str]] = {}
    for row_idx, raw_row in enumerate(rows):
        row = dict(raw_row)
        values = _ai_row_values(row)
        if values is None:
            continue
        code, segment, remark = values
        rows_by_code.setdefault(code, row)
        row_refs_by_code.setdefault(code, row_idx)
        _append_unique(segments_by_code, code, segment)
        _append_unique(remarks_by_code, code, remark)

    return [
        _ai_signal(
            code,
            row,
            row_refs_by_code.get(code),
            segments_by_code.get(code, ()),
            remarks_by_code.get(code, ()),
        )
        for code, row in rows_by_code.items()
    ]


def _scan_summary(row: Mapping[str, Any]) -> str:
    labeled_fields = (
        (KEY_TRIGGER_DATE, "触发"),
        (KEY_SCORE, "评分"),
        (KEY_RPS_STRENGTH, "RPS"),
        (KEY_BREAK_DISTANCE, "距突破"),
    )
    parts = [f"{prefix}{value}" for key, prefix in labeled_fields if (value := _text(row.get(key)))]
    parts.extend(value for key in (KEY_BREAK_STATUS, KEY_HOT_SECTOR) if (value := _text(row.get(key))))
    return " | ".join(parts) or "VCP扫描命中"


def _scan_signal(row: Mapping[str, Any], row_idx: int) -> StockSignal | None:
    code = _text(row.get(KEY_CODE))
    if not code:
        return None
    score_text = _text(row.get(KEY_SCORE))
    return StockSignal(
        code=code,
        name=_text(row.get(KEY_NAME)),
        source_tab="scan",
        source_label="scan",
        signal_type=SIGNAL_VCP_SCAN,
        summary=_scan_summary(row),
        numeric_value=safe_float(score_text) if score_text else None,
        observed_at=_text(row.get(KEY_TRIGGER_DATE)),
        row_ref=row_idx,
        payload=dict(row),
    )


def build_scan_signals(rows: Sequence[Mapping[str, Any]]) -> list[StockSignal]:
    signals: list[StockSignal] = []
    for row_idx, raw_row in enumerate(rows):
        row = dict(raw_row)
        signal = _scan_signal(row, row_idx)
        if signal is not None:
            signals.append(signal)
    return signals


def _block_candidate(
    row: Mapping[str, Any],
    row_idx: int,
    foreign_keywords: Sequence[str],
) -> tuple[str, dict[str, Any]] | None:
    code = _text(row.get(KEY_CODE))
    if not code:
        return None
    amount = safe_float(row.get(KEY_AMOUNT_WAN, 0))
    signal_text, signal_amount = build_watchlist_block_trade_signal(
        _text(row.get(KEY_DETAIL)),
        _text(row.get(KEY_BUY_BRANCH)),
        _text(row.get(KEY_SELL_BRANCH)),
        amount,
        foreign_keywords,
    )
    if not signal_text:
        return None
    return code, {
        "best_text": signal_text,
        "best_amount": signal_amount,
        "name": _text(row.get(KEY_NAME)),
        "row_ref": row_idx,
        "row": dict(row),
    }


def _keep_best_block_candidate(aggregates: dict[str, dict[str, Any]], code: str, candidate: dict[str, Any]) -> None:
    current = aggregates.get(code)
    current_amount = safe_float(current.get("best_amount")) if current is not None else -1.0
    if safe_float(candidate.get("best_amount")) >= current_amount:
        aggregates[code] = candidate


def _block_signal(code: str, stats: Mapping[str, Any]) -> StockSignal:
    amount = safe_float(stats.get("best_amount"))
    return StockSignal(
        code=code,
        name=_text(stats.get("name")),
        source_tab="foreign_block",
        source_label="foreign_block",
        signal_type=SIGNAL_BLOCK_TRADE,
        summary=_text(stats.get("best_text")),
        numeric_value=amount,
        row_ref=stats.get("row_ref"),
        payload={**dict(stats.get("row") or {}), "amount_wan": amount},
    )


def build_block_trade_signals(
    rows: Sequence[Mapping[str, Any]],
    foreign_keywords: Sequence[str] = (),
) -> list[StockSignal]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row_idx, raw_row in enumerate(rows):
        row = dict(raw_row)
        result = _block_candidate(row, row_idx, foreign_keywords)
        if result is None:
            continue
        code, candidate = result
        _keep_best_block_candidate(aggregates, code, candidate)
    return [_block_signal(code, stats) for code, stats in aggregates.items()]


def normalize_earnings_code(row: Mapping[str, Any]) -> str:
    code = str(row.get(KEY_CODE) or row.get(RAW_STOCK_CODE) or "").strip()
    if code.isdigit() and len(code) <= 6:
        code = code.zfill(6)
    return code


def earnings_report_type(row: Mapping[str, Any]) -> str:
    return str(row.get(RAW_DATA_TYPE) or row.get(KEY_REPORT_TYPE) or row.get(KEY_REPORT_TITLE) or "").strip()


def earnings_discovered_at(row: Mapping[str, Any], fallback: str = "") -> str:
    return str(row.get(KEY_DISCOVERED_AT) or row.get("discovered_at") or fallback or "").strip()


def earnings_reveal_date(row: Mapping[str, Any]) -> str:
    return str(
        row.get(KEY_REVEAL_DATE)
        or row.get(KEY_EARNINGS_MARK_DATE)
        or row.get(RAW_DISCLOSURE_DATE)
        or row.get(KEY_TRIGGER_DATE)
        or row.get("源公告日期")
        or ""
    ).strip()


def earnings_capture_time(row: Mapping[str, Any], fallback: str = "") -> str:
    return str(row.get(KEY_DISCOVERED_AT) or row.get("discovered_at") or fallback or "").strip()


def _earnings_records(payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not payload:
        return []
    records = payload.get("records", [])
    if not isinstance(records, (list, tuple)):
        return []
    return [row for row in records if isinstance(row, Mapping)]


def _existing_earnings_cache_row(row: Mapping[str, Any], state_updated_at: str) -> dict[str, Any]:
    normalized = dict(row)
    capture_time = earnings_capture_time(row, state_updated_at)
    reveal_date = earnings_reveal_date(row)
    if capture_time:
        normalized[KEY_DISCOVERED_AT] = capture_time
    if reveal_date:
        normalized[KEY_REVEAL_DATE] = reveal_date
    return normalized


def _raw_earnings_cache_row(row: Mapping[str, Any], state_updated_at: str) -> dict[str, Any] | None:
    code = normalize_earnings_code(row)
    if not code:
        return None
    report_type = _text(row.get(RAW_DATA_TYPE, row.get(KEY_REPORT_TITLE, "")))
    return {
        **row,
        KEY_CODE: code,
        KEY_NAME: _first_text(row, (RAW_STOCK_NAME, RAW_STOCK_SHORT_NAME, KEY_NAME)),
        KEY_QOQ_PCT: row.get(RAW_QOQ_PCT, row.get(KEY_QOQ_PCT, "")),
        KEY_REPORT_PERIOD: _text(row.get(KEY_REPORT_PERIOD)),
        KEY_REPORT_TYPE: report_type,
        KEY_REPORT_TITLE: report_type,
        KEY_REVEAL_DATE: earnings_reveal_date(row),
        KEY_DISCOVERED_AT: earnings_capture_time(row, state_updated_at),
        KEY_TRIGGER_DATE: _text(row.get(RAW_DISCLOSURE_DATE)),
    }


def _prepare_earnings_cache_row(row: Mapping[str, Any], state_updated_at: str) -> dict[str, Any] | None:
    if row.get(KEY_CODE):
        return _existing_earnings_cache_row(row, state_updated_at)
    return _raw_earnings_cache_row(row, state_updated_at)


def prepare_earnings_cache_rows(payload: Mapping[str, Any] | None, state_updated_at: str = "") -> list[dict]:
    rows = (_prepare_earnings_cache_row(row, state_updated_at) for row in _earnings_records(payload))
    return [row for row in rows if row is not None]


def earnings_discovery_lookup(
    payload: Mapping[str, Any] | None,
    state_updated_at: str = "",
) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    if not payload:
        return lookup
    records = payload.get("records", [])
    if not isinstance(records, (list, tuple)):
        return lookup
    for row in records:
        if not isinstance(row, Mapping):
            continue
        code = normalize_earnings_code(row)
        if not code:
            continue
        discovered_at = earnings_discovered_at(row, state_updated_at)
        if not discovered_at:
            continue
        report_period = str(row.get(KEY_REPORT_PERIOD, "") or "").strip()
        report_type = earnings_report_type(row)
        lookup[(code, report_period, report_type)] = discovered_at
        if report_period:
            lookup[(code, report_period, "")] = discovered_at
        lookup[(code, "", "")] = discovered_at
    return lookup


def lookup_earnings_discovery(
    lookup: Mapping[tuple[str, str, str], str],
    *,
    code: str,
    report_period: str,
    report_type: str,
) -> str:
    return (
        lookup.get((code, report_period, report_type))
        or lookup.get((code, report_period, ""))
        or lookup.get((code, "", ""))
        or ""
    )


def _earnings_quarter(period: str) -> int | None:
    compact = period.replace("/", "-").replace(".", "-")
    endings = {
        1: ("-03-31", "0331"),
        2: ("-06-30", "0630"),
        3: ("-09-30", "0930"),
        4: ("-12-31", "1231"),
    }
    for quarter, suffixes in endings.items():
        if f"Q{quarter}" in compact.upper() or compact.endswith(suffixes):
            return quarter
    return None


def earnings_report_label(row: Mapping[str, Any]) -> str:
    for key in (KEY_REPORT_NAME, KEY_REPORT_TITLE):
        label = str(row.get(key, "") or "").strip()
        if label:
            return label
    period = str(row.get(KEY_REPORT_PERIOD, "") or "").strip()
    if not period:
        return ""
    labels = {1: "一季度", 2: "半年报", 3: "三季度", 4: "年报"}
    return labels.get(_earnings_quarter(period), period)


def _earnings_signal_payload(
    row: Mapping[str, Any],
    *,
    summary: str,
    qoq_value: float,
    report_type: str,
    reveal_date: str,
    discovered_at: str,
) -> dict[str, Any]:
    payload = {**row, "qoq_pct": qoq_value, KEY_EARNINGS_TEXT: summary}
    if report_type:
        payload.setdefault(KEY_REPORT_TYPE, report_type)
    if reveal_date:
        payload.setdefault(KEY_REVEAL_DATE, reveal_date)
        payload[KEY_EARNINGS_MARK_DATE] = reveal_date
    if discovered_at:
        payload[KEY_DISCOVERED_AT] = discovered_at
    return payload


def _earnings_signal(
    row: Mapping[str, Any],
    row_idx: int,
    lookup: Mapping[tuple[str, str, str], str],
) -> StockSignal | None:
    code = _text(row.get(KEY_CODE))
    qoq_raw = row.get(KEY_QOQ_PCT)
    if not code or not _text(qoq_raw):
        return None
    qoq_value = safe_float(qoq_raw)
    qoq_display = f"{qoq_value:.2f}".rstrip("0").rstrip(".")
    report_label = earnings_report_label(row)
    summary = f"{report_label} {qoq_display}%" if report_label else f"{qoq_display}%"
    report_type = earnings_report_type(row)
    discovered_at = earnings_discovered_at(row) or lookup_earnings_discovery(
        lookup,
        code=code,
        report_period=_text(row.get(KEY_REPORT_PERIOD)),
        report_type=report_type,
    )
    reveal_date = earnings_reveal_date(row) or discovered_at
    return StockSignal(
        code=code,
        name=_text(row.get(KEY_NAME)),
        source_tab="earnings",
        source_label="earnings",
        signal_type=SIGNAL_EARNINGS,
        summary=summary,
        numeric_value=qoq_value,
        observed_at=discovered_at or reveal_date,
        row_ref=row_idx,
        payload=_earnings_signal_payload(
            row,
            summary=summary,
            qoq_value=qoq_value,
            report_type=report_type,
            reveal_date=reveal_date,
            discovered_at=discovered_at,
        ),
    )


def build_earnings_signals(
    rows: Sequence[Mapping[str, Any]],
    discovery_lookup: Mapping[tuple[str, str, str], str] | None = None,
) -> list[StockSignal]:
    lookup = discovery_lookup or {}
    signals: list[StockSignal] = []
    for row_idx, raw_row in enumerate(rows):
        signal = _earnings_signal(dict(raw_row), row_idx, lookup)
        if signal is not None:
            signals.append(signal)
    return signals


def latest_fund_holding_quarters(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    latest_by_subject: dict[str, str] = {}
    for row in rows:
        quarter = str(row.get(KEY_QUARTER, "") or "").strip()
        if not quarter:
            continue
        subject_key = str(row.get(KEY_SUBJECT_CODE, "") or row.get(KEY_SUBJECT, "") or "__all__").strip()
        if quarter > latest_by_subject.get(subject_key, ""):
            latest_by_subject[subject_key] = quarter
    return latest_by_subject


def is_latest_fund_holding_row(row: Mapping[str, Any], latest_by_subject: Mapping[str, str]) -> bool:
    if "_is_latest_subject_quarter" in row:
        return bool(row.get("_is_latest_subject_quarter"))
    quarter = str(row.get(KEY_QUARTER, "") or "").strip()
    if not quarter:
        return False
    subject_key = str(row.get(KEY_SUBJECT_CODE, "") or row.get(KEY_SUBJECT, "") or "__all__").strip()
    return quarter == latest_by_subject.get(subject_key)


def _fund_holding_summary(row: Mapping[str, Any]) -> str:
    base_keys = (KEY_SUBJECT, KEY_CAPITAL_ATTRIBUTE, KEY_CHANGE_TYPE, KEY_QUARTER)
    parts = [value for key in base_keys if (value := _text(row.get(key)))]
    labeled_keys = ((KEY_CURRENT_RATIO, "占比"), (KEY_HOLDING_DELTA, "变化"))
    parts.extend(f"{label}{value}" for key, label in labeled_keys if (value := _text(row.get(key))))
    return " | ".join(parts) or "基金持仓变动"


def _fund_holding_signal(
    row: Mapping[str, Any],
    row_idx: int,
    latest_by_subject: Mapping[str, str],
) -> StockSignal | None:
    code = _text(row.get(KEY_CODE))
    change_type = _text(row.get(KEY_CHANGE_TYPE))
    if not code or change_type not in FUND_HOLDING_ALLOWED_CHANGE_TYPES:
        return None
    if not is_latest_fund_holding_row(row, latest_by_subject):
        return None
    return StockSignal(
        code=code,
        name=_text(row.get(KEY_NAME)),
        source_tab="fund_holdings",
        source_label="fund_holdings",
        signal_type=SIGNAL_FUND_HOLDING,
        summary=_fund_holding_summary(row),
        observed_at=_text(row.get(KEY_QUARTER)),
        row_ref=row_idx,
        payload=dict(row),
    )


def build_fund_holding_signals(rows: Sequence[Mapping[str, Any]]) -> list[StockSignal]:
    latest_by_subject = latest_fund_holding_quarters(rows)
    signals = (
        _fund_holding_signal(dict(row), row_idx, latest_by_subject)
        for row_idx, row in enumerate(rows)
    )
    return [signal for signal in signals if signal is not None]


def format_fund_holding_pct(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f}%"
    except (TypeError, ValueError):
        return "--"


def format_fund_holding_amount(value: Any, *, divisor: float = 10000.0) -> str:
    try:
        number = float(value or 0) / divisor
    except (TypeError, ValueError):
        return "--"
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:,.2f}"


def _fund_store_row(
    row: Mapping[str, Any],
    *,
    qfii_subject_code: str,
    unmarked_capital_attribute: str,
) -> dict[str, Any]:
    subject_code = _text(row.get("subject_code"))
    capital_attribute = _text(row.get("capital_attribute"))
    if subject_code == qfii_subject_code and not capital_attribute:
        capital_attribute = unmarked_capital_attribute
    return {
        KEY_CODE: _text(row.get("stock_code")),
        KEY_NAME: _text(row.get("stock_name")),
        KEY_SUBJECT: _text(row.get("subject_name")),
        KEY_CAPITAL_ATTRIBUTE: "" if capital_attribute == unmarked_capital_attribute else capital_attribute,
        KEY_SUBJECT_CODE: subject_code,
        KEY_QUARTER: _text(row.get("quarter_key")),
        KEY_CHANGE_TYPE: _text(row.get("change_type")),
        KEY_CURRENT_RATIO: format_fund_holding_pct(row.get("curr_ratio_pct")),
        KEY_HOLDING_DELTA: format_fund_holding_amount(row.get("delta_hold_num_shares")),
        "_is_latest_subject_quarter": True,
    }


def format_fund_holding_store_rows(
    latest_quarter_map: Mapping[str, str],
    change_rows: Sequence[Mapping[str, Any]],
    *,
    qfii_subject_code: str = "",
    unmarked_capital_attribute: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in change_rows:
        stock_code = str(row.get("stock_code") or "").strip()
        subject_code = str(row.get("subject_code") or "").strip()
        quarter = str(row.get("quarter_key") or "").strip()
        change_type = str(row.get("change_type") or "").strip()
        if not stock_code or change_type not in FUND_HOLDING_ALLOWED_CHANGE_TYPES:
            continue
        if quarter != latest_quarter_map.get(subject_code):
            continue
        rows.append(
            _fund_store_row(
                row,
                qfii_subject_code=qfii_subject_code,
                unmarked_capital_attribute=unmarked_capital_attribute,
            )
        )
    return rows


def normalize_lhb_pool_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        raw_date = str(row.get(KEY_LAST_LISTED, "") or "").strip()
        if len(raw_date) == 8:
            row[KEY_LAST_LISTED_RAW] = raw_date
            row[KEY_LAST_LISTED] = f"{raw_date[4:6]}-{raw_date[6:8]}"
        normalized.append(row)
    return normalized


def _lhb_date_label(raw_date: str) -> str:
    if len(raw_date) == 8:
        return f"{raw_date[4:6]}-{raw_date[6:8]}"
    if "-" not in raw_date:
        return raw_date
    parts = raw_date.split("-")
    return "-".join(parts[-2:]) if len(parts) >= 2 else raw_date


def _signed_amount_text(value: float, positive_label: str, negative_label: str) -> str:
    if value < 0:
        return f"{negative_label}{abs(value):.0f}万"
    return f"{positive_label}{value:.0f}万"


def _lhb_signal(row: Mapping[str, Any], row_idx: int, code: str) -> StockSignal:
    raw_date = _first_text(row, (KEY_LAST_LISTED_RAW, KEY_LAST_LISTED))
    net = safe_float(row.get(KEY_NET_WAN, 0))
    inst = safe_float(row.get(KEY_INST_WAN, 0))
    foreign = safe_float(row.get(KEY_FOREIGN_WAN, 0))
    parts = (
        _lhb_date_label(raw_date),
        _signed_amount_text(net, TEXT_NET_BUY, TEXT_NET_SELL),
        _signed_amount_text(inst, TEXT_INST_NET_BUY, TEXT_INST_NET_SELL),
        _signed_amount_text(foreign, TEXT_FOREIGN_NET_BUY, TEXT_FOREIGN_NET_SELL),
    )
    return StockSignal(
        code=code,
        name=_text(row.get(KEY_NAME)),
        source_tab="lhb",
        source_label="lhb",
        signal_type=SIGNAL_LHB,
        summary=" | ".join(parts),
        numeric_value=net,
        observed_at=raw_date,
        row_ref=row_idx,
        payload={**row, "date": raw_date, "net_wan": net, "inst_wan": inst, "foreign_wan": foreign},
    )


def build_lhb_signals(rows: Sequence[Mapping[str, Any]]) -> list[StockSignal]:
    signals: list[StockSignal] = []
    seen_codes: set[str] = set()
    for row_idx, raw_row in enumerate(rows):
        row = dict(raw_row)
        code = _text(row.get(KEY_CODE))
        if not code:
            continue
        if code in seen_codes:
            continue
        seen_codes.add(code)
        signals.append(_lhb_signal(row, row_idx, code))
    return signals


def build_signals_for_source(
    source: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    foreign_keywords: Sequence[str] = (),
    discovery_lookup: Mapping[tuple[str, str, str], str] | None = None,
) -> list[StockSignal]:
    builders = {
        "scan": build_scan_signals,
        "ai_industry_chain": build_ai_chain_signals,
        "na_daily": build_na_daily_signals,
        "fund_holdings": build_fund_holding_signals,
        "lhb": build_lhb_signals,
    }
    if source == "foreign_block":
        return build_block_trade_signals(rows, foreign_keywords)
    if source == "earnings":
        return build_earnings_signals(rows, discovery_lookup)
    builder = builders.get(source)
    return builder(rows) if builder is not None else []


def index_signals_by_code(signals: Iterable[StockSignal]) -> dict[str, list[StockSignal]]:
    indexed: dict[str, list[StockSignal]] = defaultdict(list)
    for signal in signals:
        code = signal.normalized_code()
        if code:
            indexed[code].append(signal)
    return dict(indexed)


def _new_radar_state() -> dict[str, dict]:
    return {
        "remark": {},
        "ai_subsector": {},
        "na_subsector": {},
        "block": {},
        "earnings": {},
        "lhb": {},
    }


def _radar_na(state: dict[str, dict], code: str, signal: StockSignal) -> None:
    state["na_subsector"].setdefault(code, signal.summary)


def _radar_ai(state: dict[str, dict], code: str, signal: StockSignal) -> None:
    if signal.summary:
        state["ai_subsector"].setdefault(code, signal.summary)
    remark = _text(signal.payload.get(KEY_REMARK))
    if remark:
        state["remark"].setdefault(code, remark)


def _radar_block(state: dict[str, dict], code: str, signal: StockSignal) -> None:
    if signal.summary:
        state["block"][code] = {
            "text": signal.summary,
            "amount_wan": signal.payload.get("amount_wan", signal.numeric_value or 0.0),
        }


def _radar_earnings(state: dict[str, dict], code: str, signal: StockSignal) -> None:
    if signal.summary:
        state["earnings"][code] = {
            "text": signal.summary,
            "qoq_pct": signal.payload.get("qoq_pct", signal.numeric_value or 0.0),
        }


def _radar_lhb(state: dict[str, dict], code: str, signal: StockSignal) -> None:
    if not signal.summary:
        return
    state["lhb"][code] = {
        "text": signal.summary,
        "date": signal.payload.get("date", signal.observed_at),
        "net_wan": signal.payload.get("net_wan", signal.numeric_value or 0.0),
        "inst_wan": signal.payload.get("inst_wan", 0.0),
        "foreign_wan": signal.payload.get("foreign_wan", 0.0),
        "buy_point": _text(signal.payload.get("买点")),
    }


RADAR_SOURCE_HANDLERS = {
    ("na_daily", SIGNAL_SUBSECTOR): _radar_na,
    ("ai_industry_chain", SIGNAL_SUBSECTOR): _radar_ai,
}
RADAR_TYPE_HANDLERS = {
    SIGNAL_BLOCK_TRADE: _radar_block,
    SIGNAL_EARNINGS: _radar_earnings,
    SIGNAL_LHB: _radar_lhb,
}


def _apply_radar_signal(state: dict[str, dict], signal: StockSignal) -> None:
    handler = RADAR_SOURCE_HANDLERS.get((signal.source_tab, signal.signal_type))
    if handler is None:
        handler = RADAR_TYPE_HANDLERS.get(signal.signal_type)
    if handler is not None:
        handler(state, signal.normalized_code(), signal)


def build_watchlist_radar_data(
    signals: Iterable[StockSignal],
    *,
    rps_bundle: Any = None,
    target_codes: set[str] | frozenset[str] | None = None,
) -> tuple[dict, dict, dict, dict, dict, Any]:
    state = _new_radar_state()
    for signal in signals:
        if target_codes is not None and signal.normalized_code() not in target_codes:
            continue
        _apply_radar_signal(state, signal)
    subsector_data = dict(state["ai_subsector"])
    for code, summary in state["na_subsector"].items():
        subsector_data.setdefault(code, summary)
    return state["remark"], subsector_data, state["block"], state["earnings"], state["lhb"], rps_bundle


__all__ = [name for name in globals() if name.isupper() or name.startswith("build_")]
