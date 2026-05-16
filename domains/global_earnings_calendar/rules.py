# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import re

ALNUM_RE = re.compile(r"[a-z0-9]+")
BEIJING_DATE_RE = re.compile(r"(?:(?P<year>\d{4})[-/])?(?P<month>\d{1,2})[-/](?P<day>\d{1,2})")
BEIJING_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")
COMPACT_DATE_RE = re.compile(r"(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})")
ENGLISH_DATE_RE = re.compile(
    r"\b(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(?P<day>\d{1,2})(?:,)?\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

ENGLISH_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

MARKET_SUFFIXES = (
    (".TWO", "TW"),
    (".TW", "TW"),
    (".KS", "KR"),
    (".T", "JP"),
    (".HK", "HK"),
    (".AS", "NL"),
    (".PA", "FR"),
    (".L", "UK"),
    (".DE", "DE"),
    (".MI", "IT"),
    (".ST", "SE"),
)


def normalize_text(value: str) -> str:
    return "".join(ALNUM_RE.findall(str(value or "").lower()))


def label_matches_company(label: str, company: str) -> bool:
    label_norm = normalize_text(label)
    company_norm = normalize_text(company)
    if not label_norm or not company_norm:
        return False
    if label_norm == company_norm:
        return True
    label_head = str(label or "").split("(")[0].strip()
    head_norm = normalize_text(label_head)
    return bool(head_norm and (head_norm == company_norm or label_norm.startswith(company_norm)))


def market_from_ticker(ticker: str) -> str:
    ticker_text = str(ticker or "").strip().upper()
    for suffix, market in MARKET_SUFFIXES:
        if ticker_text.endswith(suffix):
            return market
    return "US"


def local_code_from_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper().split(".")[0]


def date_from_compact_text(value: str) -> dt.date | None:
    text = str(value or "").strip()
    match = COMPACT_DATE_RE.search(text)
    if match is not None:
        try:
            return dt.date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return None
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        try:
            return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def date_from_english_text(value: str) -> dt.date | None:
    match = ENGLISH_DATE_RE.search(str(value or ""))
    if match is None:
        return None
    month = ENGLISH_MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    try:
        return dt.date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def date_from_any(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        try:
            result = date_method()
            if isinstance(result, dt.date):
                return result
        except (TypeError, ValueError):
            pass
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def text_has_any(value: str, keywords: tuple[str, ...]) -> bool:
    text = str(value or "").casefold()
    return any(keyword.casefold() in text for keyword in keywords)


def beijing_time_from_local(day: dt.date, time_text: str, *, utc_offset_hours: int) -> str:
    match = BEIJING_TIME_RE.search(str(time_text or ""))
    if match is None:
        return ""
    try:
        local_time = dt.time(hour=int(match.group("hour")), minute=int(match.group("minute")))
    except ValueError:
        return ""
    beijing_dt = dt.datetime.combine(day, local_time) - dt.timedelta(hours=utc_offset_hours - 8)
    return beijing_dt.strftime("%Y-%m-%d %H:%M")


def date_from_beijing_time(value: str, report_date: str) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = BEIJING_DATE_RE.search(text)
    if match is None:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    year_text = match.group("year")
    if year_text:
        try:
            return dt.date(int(year_text), month, day)
        except ValueError:
            return None

    base = date_from_any(report_date)
    if base is None:
        return None
    candidates = []
    for year in (base.year - 1, base.year, base.year + 1):
        try:
            candidates.append(dt.date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs((candidate - base).days))


def datetime_from_beijing_time(value: str, report_date: str) -> dt.datetime | None:
    day = date_from_beijing_time(value, report_date)
    if day is None:
        return None
    match = BEIJING_TIME_RE.search(str(value or ""))
    if match is None:
        return None
    try:
        return dt.datetime.combine(day, dt.time(hour=int(match.group("hour")), minute=int(match.group("minute"))))
    except ValueError:
        return None
