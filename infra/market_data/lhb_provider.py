from __future__ import annotations

from typing import Any, cast

import akshare as ak
import pandas as pd

from core.logger import get_logger
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded

log = get_logger(__name__)


def _raise_if_cancelled(cancellation_token=None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _series_value(row: pd.Series, name: str, default: Any = None) -> Any:
    return cast(Any, row.get(name, default))


def _series_scalar(row: pd.Series, name: str, default: Any = 0) -> Any:
    value = _series_value(row, name, default)
    return default if bool(pd.isna(value)) else value


FOREIGN_KEYWORDS = [
    "深股通",
    "沪股通",
    "陆股通",  # 北向通
    "高盛",
    "摩根大通",
    "摩根士丹利",
    "瑞银",
    "法巴",
    "渣打",
    "野村",
    "汇丰",
    "星展",
    "大和",
]

DEFAULT_JG_INFO = {
    "买方机构数": 0,
    "卖方机构数": 0,
    "机构买入总额": 0.0,
    "机构卖出总额": 0.0,
    "机构买入净额": 0.0,
}


def _format_wan_amount(amount_wan: float) -> str:
    amount = abs(float(amount_wan or 0))
    if amount >= 10000:
        text = f"{amount / 10000:.2f}".rstrip("0").rstrip(".")
        return f"{text}亿"
    if amount >= 100:
        return f"{amount:.0f}万"
    if amount >= 10:
        return f"{amount:.1f}万"
    return f"{amount:.2f}万"


def _foreign_amount_summary(amount: float) -> str:
    if amount > 0.01:
        return f"净买{_format_wan_amount(amount)}"
    if amount < -0.01:
        return f"净卖{_format_wan_amount(amount)}"
    return "平衡"


def _foreign_short_part(branch: str, amount: float) -> str:
    if amount > 0.01:
        return f"{branch}+{_format_wan_amount(amount)}"
    if amount < -0.01:
        return f"{branch}-{_format_wan_amount(amount)}"
    return f"{branch}±0"


def _foreign_tooltip_line(branch: str, amount: float) -> str:
    direction = _foreign_amount_summary(amount).split(_format_wan_amount(amount), 1)[0]
    return f"{branch}：{direction}{_format_wan_amount(amount)}"


def _build_foreign_display(branch_details_map: dict[str, float]) -> tuple[str, str]:
    if not branch_details_map:
        return "未现身", "当日未发现外资席位上榜"

    sorted_items = sorted(branch_details_map.items(), key=lambda item: (-abs(item[1]), item[0]))
    total = sum(amount for _, amount in sorted_items)
    summary = _foreign_amount_summary(total)
    short_parts = [_foreign_short_part(branch, amount) for branch, amount in sorted_items]
    tooltip_lines = [f"外资合计：{summary}"]
    tooltip_lines.extend(_foreign_tooltip_line(branch, amount) for branch, amount in sorted_items)
    return f"{summary} | {' / '.join(short_parts)}", "\n".join(tooltip_lines)


def _normalize_reason_key(reason: str) -> str:
    text = str(reason or "").replace("｜", "|").strip()
    if not text:
        return ""
    parts = [part.strip() for part in text.split("|") if part.strip()]
    if not parts:
        return ""
    return " | ".join(sorted(dict.fromkeys(parts)))


def _reason_tokens(reason: str) -> set[str]:
    reason_key = _normalize_reason_key(reason)
    if not reason_key:
        return set()
    return {part.strip() for part in reason_key.split("|") if part.strip()}


def _build_jg_info(row: pd.Series) -> dict:
    info = dict(DEFAULT_JG_INFO)
    info.update(
        {
            "买方机构数": int(_series_scalar(row, "买方机构数")),
            "卖方机构数": int(_series_scalar(row, "卖方机构数")),
            "机构买入总额": float(_series_scalar(row, "机构买入总额")),
            "机构卖出总额": float(_series_scalar(row, "机构卖出总额")),
            "机构买入净额": float(_series_scalar(row, "机构买入净额")),
        }
    )
    return info


def _jg_info_signature(info: dict) -> tuple:
    return (
        int(info.get("买方机构数", 0)),
        int(info.get("卖方机构数", 0)),
        round(float(info.get("机构买入总额", 0.0) or 0.0), 2),
        round(float(info.get("机构卖出总额", 0.0) or 0.0), 2),
        round(float(info.get("机构买入净额", 0.0) or 0.0), 2),
    )


def _merge_jg_candidates(candidates: list[dict]) -> dict:
    if not candidates:
        return dict(DEFAULT_JG_INFO)

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        signature = _jg_info_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        unique_candidates.append(candidate)

    if len(unique_candidates) == 1:
        return dict(unique_candidates[0])

    return dict(
        max(
            unique_candidates,
            key=lambda item: (
                abs(float(item.get("机构买入净额", 0.0) or 0.0)),
                int(item.get("买方机构数", 0)) + int(item.get("卖方机构数", 0)),
            ),
        )
    )


def _is_close_number(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(float(left or 0.0) - float(right or 0.0)) <= tolerance


def _price_pct_jg_matches(candidates: list[dict], close_p: float, pct: float) -> list[dict]:
    return [
        candidate
        for candidate in candidates
        if _is_close_number(candidate.get("_收盘价", 0.0), close_p)
        and _is_close_number(candidate.get("_涨跌幅", 0.0), pct)
    ]


def _reason_token_jg_matches(candidates: list[dict], reason_key: str) -> list[dict]:
    reason_tokens = {part.strip() for part in reason_key.split("|") if part.strip()}
    matches = []
    for candidate in candidates:
        candidate_tokens = candidate.get("_上榜原因_tokens", set())
        if candidate_tokens and (
            candidate_tokens.issubset(reason_tokens) or reason_tokens.issubset(candidate_tokens)
        ):
            matches.append(candidate)
    return matches


def _resolve_jg_info(
    code: str,
    reason: str,
    close_p: float,
    pct: float,
    jg_reason_dict: dict[tuple[str, str], list[dict]],
    jg_candidates: dict[str, list[dict]],
) -> dict:
    reason_key = _normalize_reason_key(reason)
    if reason_key:
        exact_matches = jg_reason_dict.get((code, reason_key), [])
        if exact_matches:
            return _merge_jg_candidates(exact_matches)

    candidates = jg_candidates.get(code, [])
    if not candidates:
        return dict(DEFAULT_JG_INFO)
    if len(candidates) == 1:
        return dict(candidates[0])

    price_pct_matches = _price_pct_jg_matches(candidates, close_p, pct)
    if price_pct_matches:
        return _merge_jg_candidates(price_pct_matches)

    if reason_key:
        token_matches = _reason_token_jg_matches(candidates, reason_key)
        if token_matches:
            return _merge_jg_candidates(token_matches)

    return _merge_jg_candidates(candidates)


def _match_foreign_keyword(branch_name: str) -> str | None:
    return next((kw for kw in FOREIGN_KEYWORDS if kw in str(branch_name or "")), None)


def _build_foreign_row_key(row: pd.Series, *, include_reason: bool = True) -> tuple:
    def _num(name: str) -> float:
        value = _series_value(row, name, 0)
        if bool(pd.isna(value)):
            return 0.0
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    row_key = (
        str(_series_value(row, "交易营业部名称", "")).strip(),
        _num("买入金额"),
        _num("卖出金额"),
        _num("净额"),
    )
    if include_reason:
        row_key += (_normalize_reason_key(str(_series_value(row, "类型", ""))),)
    return row_key


def _foreign_reason_matches(detail_reason: str, target_reason: str) -> bool:
    target_tokens = _reason_tokens(target_reason)
    if not target_tokens:
        return True

    detail_tokens = _reason_tokens(detail_reason)
    if not detail_tokens:
        return False

    return (
        detail_tokens == target_tokens
        or detail_tokens.issubset(target_tokens)
        or target_tokens.issubset(detail_tokens)
        or bool(detail_tokens & target_tokens)
    )


def _collect_foreign_branch_details(detail_df: pd.DataFrame, reason: str) -> dict[str, float]:
    if detail_df is None or detail_df.empty:
        return {}

    branch_details_map: dict[str, float] = {}
    seen_detail_rows: set[tuple] = set()

    for _, s_row in detail_df.iterrows():
        yyb_name = str(s_row.get("交易营业部名称", ""))
        matched_kw = _match_foreign_keyword(yyb_name)
        if not matched_kw:
            continue
        if not _foreign_reason_matches(str(_series_value(s_row, "类型", "")), reason):
            continue

        # 同一席位明细会在买入/卖出接口重复出现；多原因合并行也可能重复返回同一笔数据。
        detail_row_key = _build_foreign_row_key(s_row, include_reason=False)
        if detail_row_key in seen_detail_rows:
            continue
        seen_detail_rows.add(detail_row_key)

        net_str = str(s_row.get("净额", "0"))
        try:
            net_val = float(net_str)
        except (TypeError, ValueError):
            net_val = 0.0
        net_wan = net_val / 10000.0
        branch_details_map[matched_kw] = branch_details_map.get(matched_kw, 0.0) + net_wan

    return branch_details_map


def _load_lhb_detail_frame(date_str: str, *, cancellation_token=None) -> tuple[pd.DataFrame, str, str]:
    """加载并按池口径去重单日龙虎榜基础明细。"""
    try:
        _raise_if_cancelled(cancellation_token)
        df_detail = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        _raise_if_cancelled(cancellation_token)
        if df_detail is None or df_detail.empty:
            message = f"[龙虎榜抓取] {date_str} 基础榜单为空，可能无数据或尚未发布。"
            log.info(message)
            return pd.DataFrame(), "empty", message

        # 同一天某只股票可能因为多种原因上榜，合并其原因并保留唯一行。
        # 注意：必须同时根据“代码”和“龙虎榜买卖净额”作为复合主键去重，
        # 否则会把三日榜和单日榜误合并。
        if all(c in df_detail.columns for c in ["代码", "上榜原因", "龙虎榜净买额"]):
            group_keys = ["代码", "龙虎榜净买额"]
            df_detail["上榜原因"] = df_detail.groupby(group_keys)["上榜原因"].transform(
                lambda x: " | ".join(x.dropna().astype(str).unique())
            )
            df_detail = df_detail.drop_duplicates(subset=group_keys, keep="first")

        return df_detail, "ok", f"[龙虎榜抓取] {date_str} 基础榜单 {len(df_detail)} 条"
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        message = f"[龙虎榜抓取] {date_str} 基础榜单异常: {e}"
        log.warning(message)
        return pd.DataFrame(), "error", message


def _load_jg_lookups(
    date_str: str,
    *,
    cancellation_token=None,
) -> tuple[dict[tuple[str, str], list[dict]], dict[str, list[dict]]]:
    try:
        _raise_if_cancelled(cancellation_token)
        df_jg = ak.stock_lhb_jgmmtj_em(start_date=date_str, end_date=date_str)
        _raise_if_cancelled(cancellation_token)
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜抓取] 机构买卖详情抓取失败: {exc}")
        df_jg = pd.DataFrame()

    jg_reason_dict: dict[tuple[str, str], list[dict]] = {}
    jg_candidates: dict[str, list[dict]] = {}
    if df_jg is None or df_jg.empty:
        return jg_reason_dict, jg_candidates

    for _, row in df_jg.iterrows():
        _raise_if_cancelled(cancellation_token)
        code = str(row.get("代码", "")).zfill(6)
        reason_key = _normalize_reason_key(str(_series_value(row, "上榜原因", "")))
        jg_info = _build_jg_info(row)
        jg_info["_上榜原因_key"] = reason_key
        jg_info["_上榜原因_tokens"] = {part.strip() for part in reason_key.split("|") if part.strip()}
        jg_info["_收盘价"] = float(_series_scalar(row, "收盘价"))
        jg_info["_涨跌幅"] = float(_series_scalar(row, "涨跌幅"))
        jg_candidates.setdefault(code, []).append(jg_info)
        if reason_key:
            jg_reason_dict.setdefault((code, reason_key), []).append(jg_info)
    return jg_reason_dict, jg_candidates


def _add_foreign_stock_names(target: dict[str, set[str]], names, branch: str) -> None:
    for stock_name in str(names or "").split():
        normalized = stock_name.strip()
        if normalized:
            target.setdefault(normalized, set()).add(branch)


def _build_foreign_presence_maps(df_yyb: pd.DataFrame, cancellation_token=None):
    foreign_buys: dict[str, set[str]] = {}
    foreign_sells: dict[str, set[str]] = {}
    for _, row in df_yyb.iterrows():
        _raise_if_cancelled(cancellation_token)
        branch = _match_foreign_keyword(str(_series_value(row, "营业部名称", "")))
        if branch:
            _add_foreign_stock_names(foreign_buys, _series_value(row, "买入股票", ""), branch)
            _add_foreign_stock_names(foreign_sells, _series_value(row, "卖出股票", ""), branch)
    return foreign_buys, foreign_sells


def _load_foreign_presence(
    date_str: str,
    *,
    cancellation_token=None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    try:
        _raise_if_cancelled(cancellation_token)
        df_yyb = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
        _raise_if_cancelled(cancellation_token)
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜抓取] 活跃营业部抓取失败: {exc}")
        df_yyb = pd.DataFrame()

    if df_yyb is None or df_yyb.empty:
        return {}, {}
    return _build_foreign_presence_maps(df_yyb, cancellation_token)


def _load_stock_foreign_details(code: str, date_str: str, *, cancellation_token=None) -> pd.DataFrame:
    frames = []
    for flag in ("买入", "卖出"):
        try:
            _raise_if_cancelled(cancellation_token)
            detail = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag=flag)
            _raise_if_cancelled(cancellation_token)
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            continue
        if detail is not None and not detail.empty:
            frames.append(detail)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _foreign_details_for_reason(
    code: str,
    date_str: str,
    reason: str,
    detail_cache: dict[str, pd.DataFrame],
    aggregate_cache: dict[tuple[str, str], dict[str, float]],
    *,
    cancellation_token=None,
) -> dict[str, float]:
    cache_key = (code, _normalize_reason_key(reason))
    if cache_key in aggregate_cache:
        return aggregate_cache[cache_key]
    if code not in detail_cache:
        detail_cache[code] = _load_stock_foreign_details(
            code,
            date_str,
            cancellation_token=cancellation_token,
        )
    aggregate_cache[cache_key] = _collect_foreign_branch_details(detail_cache[code], reason)
    return aggregate_cache[cache_key]


def _row_float(row: pd.Series, column: str) -> float:
    return float(_series_scalar(row, column))


def _foreign_net_for_record(
    *,
    has_foreign: bool,
    code: str,
    date_str: str,
    reason: str,
    detail_cache: dict[str, pd.DataFrame],
    aggregate_cache: dict[tuple[str, str], dict[str, float]],
    cancellation_token=None,
) -> tuple[dict[str, float], float]:
    if not has_foreign:
        return {}, 0.0
    details = _foreign_details_for_reason(
        code,
        date_str,
        reason,
        detail_cache,
        aggregate_cache,
        cancellation_token=cancellation_token,
    )
    detail_frame = detail_cache.get(code)
    return details, sum(details.values()) if detail_frame is not None and not detail_frame.empty else 0.0


def _passes_lhb_filter(
    *,
    strict_filter: bool,
    has_jg: bool,
    has_foreign: bool,
    pct: float,
    jg_net: float,
    foreign_net: float,
) -> bool:
    if not strict_filter:
        return True
    has_participation = (has_jg or has_foreign) and pct > 0
    has_net_buy = (has_jg and jg_net > 0) or (has_foreign and foreign_net > 0)
    return has_participation and has_net_buy


def _lhb_record_payload(
    *,
    code: str,
    name: str,
    date_str: str,
    close_p: float,
    pct: float,
    market_cap: float,
    net_buy: float,
    turnover: float,
    reason: str,
    jg_info: dict,
    foreign_net_sum: float,
    branch_details: dict[str, float],
) -> dict:
    foreign_str, foreign_tooltip = _build_foreign_display(branch_details)
    return {
        "代码": code,
        "名称": name,
        "现价": round(close_p, 2),
        "涨幅%": round(pct, 2),
        "市值": round(market_cap / 100000000.0, 2) if market_cap > 0 else "--",
        "上榜日期": date_str,
        "上榜净买额(万)": round(net_buy / 10000.0, 2),
        "机构净买(万)": round(jg_info["机构买入净额"] / 10000.0, 2),
        "外资净买(万)": round(foreign_net_sum, 2),
        "外资净买入": foreign_str,
        "_外资净买入_tooltip": foreign_tooltip,
        "换手率%": round(turnover, 2),
        "上榜原因": reason,
    }


def _lhb_row_values(row: pd.Series):
    return (
        str(_series_value(row, "代码", "")).zfill(6),
        str(_series_value(row, "名称", "")),
        _row_float(row, "龙虎榜净买额"),
        _row_float(row, "收盘价"),
        _row_float(row, "涨跌幅"),
        _row_float(row, "换手率"),
        _row_float(row, "流通市值"),
        str(_series_value(row, "上榜原因", "")),
    )


def _build_lhb_record(row: pd.Series, date_str: str, strict_filter: bool, context: dict, cancellation_token=None):
    _raise_if_cancelled(cancellation_token)
    code, name, net_buy, close_p, pct, turnover, market_cap, reason = _lhb_row_values(row)

    jg_info = _resolve_jg_info(
        code=code,
        reason=reason,
        close_p=close_p,
        pct=pct,
        jg_reason_dict=context["jg_reason_dict"],
        jg_candidates=context["jg_candidates"],
    )
    has_jg = jg_info["买方机构数"] > 0 or jg_info["卖方机构数"] > 0
    has_foreign = bool(context["foreign_buys"].get(name) or context["foreign_sells"].get(name))
    branch_details, foreign_net_sum = _foreign_net_for_record(
        has_foreign=has_foreign,
        code=code,
        date_str=date_str,
        reason=reason,
        detail_cache=context["foreign_detail_cache"],
        aggregate_cache=context["foreign_aggregate_cache"],
        cancellation_token=cancellation_token,
    )
    if not _passes_lhb_filter(
        strict_filter=strict_filter,
        has_jg=has_jg,
        has_foreign=has_foreign,
        pct=pct,
        jg_net=float(jg_info["机构买入净额"]),
        foreign_net=foreign_net_sum,
    ):
        return None
    return _lhb_record_payload(
        code=code,
        name=name,
        date_str=date_str,
        close_p=close_p,
        pct=pct,
        market_cap=market_cap,
        net_buy=net_buy,
        turnover=turnover,
        reason=reason,
        jg_info=jg_info,
        foreign_net_sum=foreign_net_sum,
        branch_details=branch_details,
    )


def _load_lhb_enrichment_context(date_str: str, cancellation_token=None) -> dict:
    jg_reason_dict, jg_candidates = _load_jg_lookups(
        date_str,
        cancellation_token=cancellation_token,
    )
    foreign_buys, foreign_sells = _load_foreign_presence(
        date_str,
        cancellation_token=cancellation_token,
    )
    return {
        "jg_reason_dict": jg_reason_dict,
        "jg_candidates": jg_candidates,
        "foreign_buys": foreign_buys,
        "foreign_sells": foreign_sells,
        "foreign_detail_cache": {},
        "foreign_aggregate_cache": {},
    }


def _build_lhb_records(
    df_detail: pd.DataFrame,
    *,
    date_str: str,
    strict_filter: bool,
    context: dict,
    cancellation_token=None,
) -> list[dict]:
    results = []
    for _, row in df_detail.iterrows():
        _raise_if_cancelled(cancellation_token)
        record = _build_lhb_record(
            row,
            date_str,
            strict_filter,
            context,
            cancellation_token,
        )
        if record is not None:
            results.append(record)
    return results


def fetch_lhb_data_for_date(
    date_str: str,
    strict_filter: bool = True,
    emit_success_log: bool = True,
    return_meta: bool = False,
    cancellation_token=None,
) -> list[dict] | dict:
    """
    抓取指定日期的龙虎榜数据，并将 基础详情、机构统计、外资/知名游资参与情况聚合返回。
    """
    # 1. 抓取每日龙虎榜总表
    _raise_if_cancelled(cancellation_token)
    df_detail, detail_status, detail_message = _load_lhb_detail_frame(
        date_str,
        cancellation_token=cancellation_token,
    )
    if detail_status != "ok":
        if return_meta:
            return {"records": [], "count": 0, "status": detail_status, "message": detail_message}
        return []

    context = _load_lhb_enrichment_context(date_str, cancellation_token)
    results = _build_lhb_records(
        df_detail,
        date_str=date_str,
        strict_filter=strict_filter,
        context=context,
        cancellation_token=cancellation_token,
    )

    message = f"[龙虎榜抓取] {date_str} 成功拉取 {len(results)} 条数据"
    if emit_success_log:
        log.info(message)

    if return_meta:
        return {"records": results, "count": len(results), "status": "ok", "message": message}

    return results


def probe_lhb_detail_count_for_date(
    date_str: str,
    return_meta: bool = False,
    cancellation_token=None,
) -> int | dict:
    """轻量探针：只返回基础榜单去重条数，用于校验历史缓存是否脏。"""
    df_detail, status, message = _load_lhb_detail_frame(
        date_str,
        cancellation_token=cancellation_token,
    )
    count = len(df_detail) if status == "ok" else 0

    if return_meta:
        return {"count": count, "status": status, "message": message}

    return count


def fetch_lhb_pool_for_date(
    date_str: str,
    emit_success_log: bool = True,
    return_meta: bool = False,
    cancellation_token=None,
) -> list[dict] | dict:
    """为 30 日关注池抓取指定日期的龙虎榜数据。
    现在直接复用完整提取器（strict_filter=False），彻底解决旧版历史记录外资和共振数据全部强行涂 0 的重大 BUG。
    """
    return fetch_lhb_data_for_date(
        date_str,
        strict_filter=False,
        emit_success_log=emit_success_log,
        return_meta=return_meta,
        cancellation_token=cancellation_token,
    )
