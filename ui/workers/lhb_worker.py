import gc

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

    # 2. 抓取机构买卖追踪
    df_jg = pd.DataFrame()
    try:
        df_jg = ak.stock_lhb_jgmmtj_em(start_date=date_str, end_date=date_str)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        log.warning(f"[龙虎榜抓取] 机构买卖详情抓取失败: {e}")

    # 构建机构速查字典
    jg_reason_dict: dict[tuple[str, str], list[dict]] = {}
    jg_candidates: dict[str, list[dict]] = {}
    if not df_jg.empty:
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

    # 3. 抓取活跃营业部，拦截外资痕迹
    df_yyb = pd.DataFrame()
    try:
        df_yyb = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        log.warning(f"[龙虎榜抓取] 活跃营业部抓取失败: {e}")

    foreign_buys = {}  # code -> [席位...]
    foreign_sells = {}  # code -> [席位...]

    if not df_yyb.empty:
        for _, row in df_yyb.iterrows():
            branch_name = str(row.get("营业部名称", ""))

            # --- 简写外资营业部名称 ---
            matched_kw = None
            for kw in FOREIGN_KEYWORDS:
                if kw in branch_name:
                    matched_kw = kw
                    break

            if not matched_kw:
                continue

            short_branch = matched_kw

            # 解析该外资席位买入了哪些股票
            buy_stocks_str = str(row.get("买入股票", ""))
            sell_stocks_str = str(row.get("卖出股票", ""))

            for s_name in buy_stocks_str.split():
                if not s_name.strip():
                    continue
                foreign_buys.setdefault(s_name.strip(), set()).add(short_branch)

            for s_name in sell_stocks_str.split():
                if not s_name.strip():
                    continue
                foreign_sells.setdefault(s_name.strip(), set()).add(short_branch)

    # 4. 缝合主表
    foreign_detail_cache: dict[str, pd.DataFrame] = {}
    results = []
    for _, row in df_detail.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        name = str(row.get("名称", ""))

        # 提取基本字段
        net_buy = float(row.get("龙虎榜净买额", 0) if pd.notna(row.get("龙虎榜净买额")) else 0)
        close_p = float(row.get("收盘价", 0) if pd.notna(row.get("收盘价")) else 0)
        pct = float(row.get("涨跌幅", 0) if pd.notna(row.get("涨跌幅")) else 0)
        turnover = float(row.get("换手率", 0) if pd.notna(row.get("换手率")) else 0)
        mk_cap = float(row.get("流通市值", 0) if pd.notna(row.get("流通市值")) else 0)
        reason = str(row.get("上榜原因", ""))

        # 关联机构数据
        jg_info = _resolve_jg_info(
            code=code,
            reason=reason,
            close_p=close_p,
            pct=pct,
            jg_reason_dict=jg_reason_dict,
            jg_candidates=jg_candidates,
        )
        has_jg = (jg_info["买方机构数"] > 0) or (jg_info["卖方机构数"] > 0)

        # 关联外资数据 (通过简称匹配)
        f_buys = list(foreign_buys.get(name, set()))
        f_sells = list(foreign_sells.get(name, set()))
        has_foreign = (len(f_buys) > 0) or (len(f_sells) > 0)

        # 核心过滤条件: 只有 (机构参与 或 外资参与) 并且 (涨跌幅 > 0) 才抓取显示
        if strict_filter:
            if not ((has_jg or has_foreign) and (pct > 0)):
                continue

        # 此时确认我们需要这只股票，为了计算精准的外资净买额，再单独拉取双边明细
        branch_details_map = {}  # 记录 kw -> net_amount(万)
        foreign_net_sum = 0.0

        if has_foreign:
            if code not in foreign_detail_cache:
                dfs = []
                try:
                    df_buy = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag="买入")
                    if df_buy is not None and not df_buy.empty:
                        dfs.append(df_buy)
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                    pass

                try:
                    df_sell = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str, flag="卖出")
                    if df_sell is not None and not df_sell.empty:
                        dfs.append(df_sell)
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                    pass

                foreign_detail_cache[code] = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

            detail_df = foreign_detail_cache.get(code, pd.DataFrame())
            if not detail_df.empty:
                branch_details_map = _collect_foreign_branch_details(detail_df, reason)
                foreign_net_sum = sum(branch_details_map.values())
            else:
                # 降级保底：API完全拉不出买卖明细，无法计算净额，归零并标注失败
                foreign_net_sum = 0.0

        # ================= 深度过滤 =================
        # 至少有一方净买入(>0)的情况下才抓取
        has_any_net_buy = False
        if has_jg and (jg_info["机构买入净额"] > 0):
            has_any_net_buy = True
        if has_foreign and (foreign_net_sum > 0):
            has_any_net_buy = True

        if strict_filter:
            if not has_any_net_buy:
                continue

        foreign_str, foreign_tooltip = _build_foreign_display(branch_details_map)

        # 构造给前端的平铺字典字段
        record = {
            "代码": code,
            "名称": name,
            "现价": round(close_p, 2),
            "涨幅%": round(pct, 2),
            "市值": round(mk_cap / 100000000.0, 2) if mk_cap > 0 else "--",
            "上榜日期": date_str,
            "上榜净买额(万)": round(net_buy / 10000.0, 2),
            "机构净买(万)": round(jg_info["机构买入净额"] / 10000.0, 2),
            "外资净买(万)": round(foreign_net_sum, 2),
            "外资净买入": foreign_str,
            "_外资净买入_tooltip": foreign_tooltip,
            "换手率%": round(turnover, 2),
            "上榜原因": reason,
        }
        results.append(record)

    message = f"[龙虎榜抓取] {date_str} 成功拉取 {len(results)} 条数据"
    if emit_success_log:
        log.info(message)

    # 挂机防漏：显式销毁 Pandas 大体积 DataFrame 对象并强制回收内存
    try:
        del df_detail, df_jg, df_yyb
    except (NameError, UnboundLocalError):
        pass
    gc.collect()

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
