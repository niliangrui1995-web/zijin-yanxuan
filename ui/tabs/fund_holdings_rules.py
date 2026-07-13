# -*- coding: utf-8 -*-
from __future__ import annotations

FUND_DISPLAY_PLACEHOLDER = "--"
FUND_CHANGE_TYPE_OPTIONS = ("新进", "增持", "减持", "退出", "持平")
FUND_DAILY_AUTO_SYNC_HOUR = 20
FUND_DAILY_AUTO_SYNC_MINUTE = 30
FUND_DAILY_AUTO_SYNC_DATE_KEY = "daily_auto_sync_2030_last_date"

AI_CONCEPT_EXCLUDE_NAMES = {
    "AI营销",
}

AI_CONCEPT_INCLUDE_NAMES = {
    "AIGC概念",
    "AI医疗",
    "AI手机PC",
    "AI智能体",
    "AI眼镜",
    "ChatGPT",
    "DeepSeek",
    "EDA概念",
    "东数西算",
    "云计算",
    "人工智能",
    "人形机器",
    "先进封装",
    "光刻机",
    "光通信",
    "华为海思",
    "华为算力",
    "国资云",
    "多模态AI",
    "存储芯片",
    "数据中心",
    "智谱AI",
    "智能机器",
    "机器视觉",
    "液冷服务",
    "算力租赁",
    "英伟达",
    "边缘计算",
}

AI_CONCEPT_DISPLAY_ALIASES = {
    "\u6db2\u51b7\u670d\u52a1": "\u6db2\u51b7",
    "CPO\u6982\u5ff5": "CPO",
}

AI_CONCEPT_INCLUDE_KEYWORDS = (
    "AI",
    "AIGC",
    "GPT",
    "DeepSeek",
    "智谱",
    "人工智能",
    "算力",
    "CPO",
    "光通信",
    "铜缆",
    "液冷",
    "芯片",
    "半导",
    "存储",
    "封装",
    "EDA",
    "PCB",
    "光刻机",
)


def normalize_auto_sync_date(value) -> str:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    return text[:8] if len(text) >= 8 else text


def should_trigger_daily_auto_sync(
    now,
    *,
    last_auto_sync_date: str,
    pending_auto_sync_date: str,
    trigger_hour: int = FUND_DAILY_AUTO_SYNC_HOUR,
    trigger_minute: int = FUND_DAILY_AUTO_SYNC_MINUTE,
) -> bool:
    today_compact = now.strftime("%Y%m%d")
    if pending_auto_sync_date == today_compact:
        return False
    if normalize_auto_sync_date(last_auto_sync_date) == today_compact:
        return False
    return not (now.hour, now.minute) < (trigger_hour, trigger_minute)


def capital_attribute_label(value: str, labels: dict[str, str], *, placeholder: str = FUND_DISPLAY_PLACEHOLDER) -> str:
    text = str(value or "").strip()
    if not text:
        return placeholder
    return labels.get(text, text)


def is_ai_related_concept(concept_name: str) -> bool:
    text = str(concept_name or "").strip()
    if not text or text in AI_CONCEPT_EXCLUDE_NAMES:
        return False
    if text in AI_CONCEPT_INCLUDE_NAMES:
        return True
    return any(keyword in text for keyword in AI_CONCEPT_INCLUDE_KEYWORDS)


def normalize_ai_concept_display(concept_name: str) -> str:
    text = str(concept_name or "").strip()
    if not text:
        return ""
    return AI_CONCEPT_DISPLAY_ALIASES.get(text, text)


def filter_ai_related_concepts(concepts) -> list[str]:
    filtered = []
    for concept_name in concepts or []:
        text = str(concept_name or "").strip()
        if not is_ai_related_concept(text):
            continue
        filtered.append(normalize_ai_concept_display(text))
    return list(dict.fromkeys(filtered))


def format_pct(value, *, show: bool, signed: bool = False, placeholder: str = FUND_DISPLAY_PLACEHOLDER) -> str:
    if not show:
        return placeholder
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return placeholder
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.2f}%"


def format_amount(
    value,
    *,
    divisor: float,
    show: bool,
    signed: bool = False,
    placeholder: str = FUND_DISPLAY_PLACEHOLDER,
) -> str:
    if not show:
        return placeholder
    try:
        number = float(value or 0) / divisor
    except (TypeError, ValueError):
        return placeholder
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:,.2f}"
