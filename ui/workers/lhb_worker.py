import akshare as ak
import pandas as pd

from core.logger import get_logger

log = get_logger(__name__)


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


def _build_foreign_display(branch_details_map: dict[str, float]) -> tuple[str, str]:
    if not branch_details_map:
        return "未现身", "当日未发现外资席位上榜"

    sorted_items = sorted(branch_details_map.items(), key=lambda item: (-abs(item[1]), item[0]))
    total = sum(amount for _, amount in sorted_items)

    if total > 0.01:
        summary = f"净买{_format_wan_amount(total)}"
    elif total < -0.01:
        summary = f"净卖{_format_wan_amount(total)}"
    else:
        summary = "平衡"

    short_parts = []
    for branch, amount in sorted_items:
        if amount > 0.01:
            short_parts.append(f"{branch}+{_format_wan_amount(amount)}")
        elif amount < -0.01:
            short_parts.append(f"{branch}-{_format_wan_amount(amount)}")
        else:
            short_parts.append(f"{branch}±0")

    display = summary
    if short_parts:
        display = f"{summary} | {' / '.join(short_parts)}"

    tooltip_lines = [f"外资合计：{summary}"]
    for branch, amount in sorted_items:
        if amount > 0.01:
            direction = "净买"
        elif amount < -0.01:
            direction = "净卖"
        else:
            direction = "平衡"
        tooltip_lines.append(f"{branch}：{direction}{_format_wan_amount(amount)}")

    return display, "\n".join(tooltip_lines)


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
            "买方机构数": int(row.get("买方机构数", 0) if pd.notna(row.get("买方机构数")) else 0),
            "卖方机构数": int(row.get("卖方机构数", 0) if pd.notna(row.get("卖方机构数")) else 0),
            "机构买入总额": float(row.get("机构买入总额", 0) if pd.notna(row.get("机构买入总额")) else 0),
            "机构卖出总额": float(row.get("机构卖出总额", 0) if pd.notna(row.get("机构卖出总额")) else 0),
            "机构买入净额": float(row.get("机构买入净额", 0) if pd.notna(row.get("机构买入净额")) else 0),
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

    price_pct_matches = [
        candidate
        for candidate in candidates
        if _is_close_number(candidate.get("_收盘价", 0.0), close_p)
        and _is_close_number(candidate.get("_涨跌幅", 0.0), pct)
    ]
    if price_pct_matches:
        return _merge_jg_candidates(price_pct_matches)

    if reason_key:
        reason_tokens = {part.strip() for part in reason_key.split("|") if part.strip()}
        token_matches = []
        for candidate in candidates:
            candidate_tokens = candidate.get("_上榜原因_tokens", set())
            if candidate_tokens and (
                candidate_tokens.issubset(reason_tokens) or reason_tokens.issubset(candidate_tokens)
            ):
                token_matches.append(candidate)
        if token_matches:
            return _merge_jg_candidates(token_matches)

    return _merge_jg_candidates(candidates)


def _match_foreign_keyword(branch_name: str) -> str | None:
    return next((kw for kw in FOREIGN_KEYWORDS if kw in str(branch_name or "")), None)


def _build_foreign_row_key(row: pd.Series, *, include_reason: bool = True) -> tuple:
    def _num(name: str) -> float:
        value = row.get(name, 0)
        if pd.isna(value):
            return 0.0
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    row_key = (
        str(row.get("交易营业部名称", "")).strip(),
        _num("买入金额"),
        _num("卖出金额"),
        _num("净额"),
    )
    if include_reason:
        row_key += (_normalize_reason_key(row.get("类型", "")),)
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
        if not _foreign_reason_matches(s_row.get("类型", ""), reason):
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


def _load_lhb_detail_frame(date_str: str) -> tuple[pd.DataFrame, str, str]:
    """加载并按池口径去重单日龙虎榜基础明细。"""
    try:
        df_detail = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
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
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        message = f"[龙虎榜抓取] {date_str} 基础榜单异常: {e}"
        log.warning(message)
        return pd.DataFrame(), "error", message


def _load_jg_lookups(date_str: str) -> tuple[dict[tuple[str, str], list[dict]], dict[str, list[dict]]]:
    try:
        df_jg = ak.stock_lhb_jgmmtj_em(start_date=date_str, end_date=date_str)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜抓取] 机构买卖详情抓取失败: {exc}")
        df_jg = pd.DataFrame()

    jg_reason_dict: dict[tuple[str, str], list[dict]] = {}
    jg_candidates: dict[str, list[dict]] = {}
    if df_jg is None or df_jg.empty:
        return jg_reason_dict, jg_candidates

    for _, row in df_jg.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        reason_key = _normalize_reason_key(row.get("上榜原因", ""))
        jg_info = _build_jg_info(row)
        jg_info["_上榜原因_key"] = reason_key
        jg_info["_上榜原因_tokens"] = {part.strip() for part in reason_key.split("|") if part.strip()}
        jg_info["_收盘价"] = float(row.get("收盘价", 0) if pd.notna(row.get("收盘价")) else 0)
        jg_info["_涨跌幅"] = float(row.get("涨跌幅", 0) if pd.notna(row.get("涨跌幅")) else 0)
        jg_candidates.setdefault(code, []).append(jg_info)
        if reason_key:
            jg_reason_dict.setdefault((code, reason_key), []).append(jg_info)
    return jg_reason_dict, jg_candidates


def _load_foreign_presence(date_str: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    try:
        df_yyb = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜抓取] 活跃营业部抓取失败: {exc}")
        df_yyb = pd.DataFrame()

    foreign_buys: dict[str, set[str]] = {}
    foreign_sells: dict[str, set[str]] = {}
    if df_yyb is None or df_yyb.empty:
        return foreign_buys, foreign_sells

    for _, row in df_yyb.iterrows():
        branch = _match_foreign_keyword(row.get("营业部名称", ""))
        if not branch:
            continue
        for stock_name in str(row.get("买入股票", "")).split():
            if stock_name.strip():
                foreign_buys.setdefault(stock_name.strip(), set()).add(branch)
        for stock_name in str(row.get("卖出股票", "")).split():
            if stock_name.strip():
                foreign_sells.setdefault(stock_name.strip(), set()).add(branch)
    return foreign_buys, foreign_sells


def _load_stock_foreign_details(code: str, date_str: str) -> pd.DataFrame:
    frames = []
    for flag in ("买入", "卖出"):
        try:
            detail = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag=flag)
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
) -> dict[str, float]:
    cache_key = (code, _normalize_reason_key(reason))
    if cache_key in aggregate_cache:
        return aggregate_cache[cache_key]
    if code not in detail_cache:
        detail_cache[code] = _load_stock_foreign_details(code, date_str)
    aggregate_cache[cache_key] = _collect_foreign_branch_details(detail_cache[code], reason)
    return aggregate_cache[cache_key]


def _row_float(row: pd.Series, column: str) -> float:
    value = row.get(column, 0)
    return float(value if pd.notna(value) else 0)


def _build_lhb_record(
    row: pd.Series,
    *,
    date_str: str,
    strict_filter: bool,
    jg_reason_dict: dict[tuple[str, str], list[dict]],
    jg_candidates: dict[str, list[dict]],
    foreign_buys: dict[str, set[str]],
    foreign_sells: dict[str, set[str]],
    foreign_detail_cache: dict[str, pd.DataFrame],
    foreign_aggregate_cache: dict[tuple[str, str], dict[str, float]],
) -> dict | None:
    code = str(row.get("代码", "")).zfill(6)
    name = str(row.get("名称", ""))
    net_buy = _row_float(row, "龙虎榜净买额")
    close_p = _row_float(row, "收盘价")
    pct = _row_float(row, "涨跌幅")
    turnover = _row_float(row, "换手率")
    market_cap = _row_float(row, "流通市值")
    reason = str(row.get("上榜原因", ""))

    jg_info = _resolve_jg_info(
        code=code,
        reason=reason,
        close_p=close_p,
        pct=pct,
        jg_reason_dict=jg_reason_dict,
        jg_candidates=jg_candidates,
    )
    has_jg = jg_info["买方机构数"] > 0 or jg_info["卖方机构数"] > 0
    has_foreign = bool(foreign_buys.get(name) or foreign_sells.get(name))
    if strict_filter and not ((has_jg or has_foreign) and pct > 0):
        return None

    branch_details = {}
    foreign_net_sum = 0.0
    if has_foreign:
        branch_details = _foreign_details_for_reason(
            code,
            date_str,
            reason,
            foreign_detail_cache,
            foreign_aggregate_cache,
        )
        if not foreign_detail_cache[code].empty:
            foreign_net_sum = sum(branch_details.values())
    has_any_net_buy = (has_jg and jg_info["机构买入净额"] > 0) or (has_foreign and foreign_net_sum > 0)
    if strict_filter and not has_any_net_buy:
        return None

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


def fetch_lhb_data_for_date(
    date_str: str,
    strict_filter: bool = True,
    emit_success_log: bool = True,
    return_meta: bool = False,
) -> list[dict] | dict:
    """
    抓取指定日期的龙虎榜数据，并将 基础详情、机构统计、外资/知名游资参与情况聚合返回。
    """
    # 1. 抓取每日龙虎榜总表
    df_detail, detail_status, detail_message = _load_lhb_detail_frame(date_str)
    if detail_status != "ok":
        if return_meta:
            return {"records": [], "count": 0, "status": detail_status, "message": detail_message}
        return []

    jg_reason_dict, jg_candidates = _load_jg_lookups(date_str)
    foreign_buys, foreign_sells = _load_foreign_presence(date_str)
    foreign_detail_cache: dict[str, pd.DataFrame] = {}
    foreign_aggregate_cache: dict[tuple[str, str], dict[str, float]] = {}
    results = []
    for _, row in df_detail.iterrows():
        record = _build_lhb_record(
            row,
            date_str=date_str,
            strict_filter=strict_filter,
            jg_reason_dict=jg_reason_dict,
            jg_candidates=jg_candidates,
            foreign_buys=foreign_buys,
            foreign_sells=foreign_sells,
            foreign_detail_cache=foreign_detail_cache,
            foreign_aggregate_cache=foreign_aggregate_cache,
        )
        if record is not None:
            results.append(record)

    message = f"[龙虎榜抓取] {date_str} 成功拉取 {len(results)} 条数据"
    if emit_success_log:
        log.info(message)

    if return_meta:
        return {"records": results, "count": len(results), "status": "ok", "message": message}

    return results


def probe_lhb_detail_count_for_date(
    date_str: str,
    return_meta: bool = False,
) -> int | dict:
    """轻量探针：只返回基础榜单去重条数，用于校验历史缓存是否脏。"""
    df_detail, status, message = _load_lhb_detail_frame(date_str)
    count = len(df_detail) if status == "ok" else 0

    if return_meta:
        return {"count": count, "status": status, "message": message}

    return count


def fetch_lhb_pool_for_date(
    date_str: str,
    emit_success_log: bool = True,
    return_meta: bool = False,
) -> list[dict] | dict:
    """为 30 日关注池抓取指定日期的龙虎榜数据。
    现在直接复用完整提取器（strict_filter=False），彻底解决旧版历史记录外资和共振数据全部强行涂 0 的重大 BUG。
    """
    return fetch_lhb_data_for_date(
        date_str,
        strict_filter=False,
        emit_success_log=emit_success_log,
        return_meta=return_meta,
    )
