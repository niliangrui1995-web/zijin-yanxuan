from __future__ import annotations

from app.services.ui_market_calendar_service import MarketCalendar

_MARKET_STATUS_ICON = {
    "交易中": "🟢",
    "开盘集合竞价": "🟡",
    "开市前时段": "🟡",
    "收盘集合竞价": "🟡",
    "收市竞价": "🟡",
    "午休": "🟡",
    "盘前": "🟡",
    "盘前委托": "🟡",
    "盘后定价申报": "🟡",
    "盘后定价": "🟡",
    "盘后": "🔴",
    "休市": "🔴",
}

_MARKET_STATUS_LABEL = {
    "交易中": "交易中",
    "开盘集合竞价": "开盘集合竞价",
    "开市前时段": "开市前时段",
    "收盘集合竞价": "收盘集合竞价",
    "收市竞价": "收市竞价",
    "午休": "午休",
    "盘前": "盘前",
    "盘前委托": "盘前委托",
    "盘后定价申报": "盘后定价申报",
    "盘后定价": "盘后定价",
    "盘后": "盘后",
    "休市": "休市",
}

_MARKET_STATUS_OVERRIDES = {
    "HK": {"午休": "午间休市"},
    "T": {"午休": "午间休市", "收盘集合竞价": "收盘竞价"},
    "KS": {"开盘集合竞价": "开盘竞价", "收盘集合竞价": "收盘竞价"},
    "TW": {"午休": "午间休市"},
}

_STATUS_OPEN_KEYWORDS = ("盘中", "交易中", "开盘")
_STATUS_CLOSED_KEYWORDS = ("休市", "收盘", "闭市", "盘后")
_RUNTIME_STATUS_LABEL = {
    "idle": "静默",
    "realtime": "实时",
    "cache": "本地缓存",
    "fallback": "远端失败沿用",
    "error": "错误",
    "working": "处理中",
}


def format_status_summary(primary: str, *segments: str) -> str:
    parts = []

    primary_text = str(primary or "").strip()
    if primary_text:
        parts.append(primary_text)

    for segment in segments:
        text = str(segment or "").strip()
        if text:
            parts.append(text)

    return " | ".join(parts)


def format_workspace_status(
    primary: str,
    *,
    result: str = "",
    freshness: str = "",
    current_filter: str = "",
    next_step: str = "",
    extra_segments: tuple[str, ...] | list[str] | None = None,
) -> str:
    segments: list[str] = []

    result_text = str(result or "").strip()
    if result_text:
        segments.append(f"结果 {result_text}")

    freshness_text = str(freshness or "").strip()
    if freshness_text:
        segments.append(f"时效 {freshness_text}")

    filter_text = str(current_filter or "").strip()
    if filter_text:
        segments.append(f"筛选 {filter_text}")

    next_text = str(next_step or "").strip()
    if next_text:
        segments.append(f"下一步 {next_text}")

    for segment in extra_segments or ():
        text = str(segment or "").strip()
        if text:
            segments.append(text)

    return format_status_summary(primary, *segments)


_STRUCTURED_STATUS_PREFIXES = (
    "结果",
    "时效",
    "筛选",
    "下一步",
    "来源",
    "数据",
    "说明",
    "命中",
    "日期",
    "席位",
    "上次成功",
)


def split_status_segment(segment: str) -> tuple[str, str]:
    text = str(segment or "").strip()
    if not text:
        return "", ""

    for prefix in _STRUCTURED_STATUS_PREFIXES:
        marker = f"{prefix} "
        if text.startswith(marker):
            return prefix, text[len(marker) :].strip()

    return "", text


def parse_status_summary(text: str) -> dict[str, object]:
    parts = [part.strip() for part in str(text or "").split("|") if part.strip()]
    if not parts:
        return {"primary": "", "segments": []}

    return {
        "primary": parts[0],
        "segments": [
            {"label": label, "value": value, "raw": segment}
            for segment in parts[1:]
            for label, value in [split_status_segment(segment)]
            if value
        ],
    }


def join_semantic_badges(*badge_groups) -> str:
    badges: list[str] = []
    for group in badge_groups:
        if isinstance(group, (list, tuple, set)):
            iterable = group
        else:
            iterable = [group]

        for badge in iterable:
            text = str(badge or "").strip()
            if text and text not in badges:
                badges.append(text)

    return "｜".join(badges)


def resolve_market_status_badge(raw_status: str, market: str) -> dict[str, str]:
    canonical_market = MarketCalendar.normalize_market(market)
    label = _MARKET_STATUS_OVERRIDES.get(canonical_market, {}).get(
        raw_status,
        _MARKET_STATUS_LABEL.get(raw_status, "休市"),
    )
    icon = _MARKET_STATUS_ICON.get(raw_status, "🔴")
    return {
        "icon": icon,
        "label": label,
        "text": f"{icon} {label}",
    }


def resolve_status_tone_name(text: str, header: str | None = None) -> str | None:
    status_text = str(text or "").strip()
    if not status_text or status_text in {"--", "-"}:
        return None

    if header == "状态":
        if any(keyword in status_text for keyword in _STATUS_OPEN_KEYWORDS):
            return "success"
        if any(keyword in status_text for keyword in _STATUS_CLOSED_KEYWORDS):
            return "warning"
        return "info"

    if header == "买点":
        if any(keyword in status_text for keyword in ("触发", "确认", "✅")):
            return "rise_strong"
        return "rise"

    if "假突破" in status_text or "缩量" in status_text:
        return "error"
    if "临近" in status_text or "关注" in status_text:
        return "warning"
    if "突破" in status_text:
        return "success"
    return None


def resolve_kline_runtime_badges(*, info_tone: str, is_offline: bool, market: str) -> dict[str, str]:
    if is_offline and market == "CN":
        return {
            "feed_text": "本地缓存",
            "feed_tone": "stale",
            "session_text": "离线",
            "session_tone": "stale",
        }

    if info_tone == "realtime":
        feed_text, feed_tone = "实时链路", "realtime"
    elif info_tone == "loading":
        feed_text, feed_tone = "同步中", "focus"
    elif info_tone == "success":
        feed_text, feed_tone = "已同步", "success"
    else:
        feed_text, feed_tone = "日线工作区", "info"

    if MarketCalendar.is_market_active(market):
        session_text, session_tone = "盘中", "realtime"
    else:
        session_text, session_tone = "收盘", "neutral"

    return {
        "feed_text": feed_text,
        "feed_tone": feed_tone,
        "session_text": session_text,
        "session_tone": session_tone,
    }


def format_runtime_status_text(
    state: str,
    detail: str = "",
    next_step: str = "",
) -> str:
    status_label = _RUNTIME_STATUS_LABEL.get(str(state or "").strip(), "处理中")
    parts = [f"状态 {status_label}"]

    detail_text = str(detail or "").strip()
    if detail_text:
        parts.append(f"说明 {detail_text}")

    next_text = str(next_step or "").strip()
    if next_text:
        parts.append(f"下一步 {next_text}")

    return format_status_summary(*parts)
