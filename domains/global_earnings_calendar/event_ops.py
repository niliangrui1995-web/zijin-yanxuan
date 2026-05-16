# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from domains.global_earnings_calendar.models import (
    YFINANCE_CONFLICT_STATUS,
    YFINANCE_SOURCE,
    EarningsCalendarEvent,
    is_yfinance_estimate_event,
    normalize_event_status,
)
from domains.global_earnings_calendar.rules import (
    date_from_any as _date_from_any,
)
from domains.global_earnings_calendar.rules import (
    date_from_beijing_time as _date_from_beijing_time,
)
from domains.global_earnings_calendar.rules import (
    datetime_from_beijing_time as _datetime_from_beijing_time,
)

_PRIORITY_RANK = {"super_giant": 0, "strategic_giant": 1, "normal": 2}
COMPANY_IR_SOURCE = "Company IR"
JPX_SOURCE = "JPX"
TDNET_SOURCE = "TDnet"
DART_SOURCE = "DART"
KIND_SOURCE = "KIND"
MOPS_SOURCE = "MOPS"
SEC_6K_SOURCE = "SEC EDGAR 6-K"
_SOURCE_RANK = {
    "Lumentum IR": 0,
    "confirmed": 0,
    COMPANY_IR_SOURCE: 0,
    JPX_SOURCE: 0,
    TDNET_SOURCE: 0,
    DART_SOURCE: 0,
    KIND_SOURCE: 0,
    MOPS_SOURCE: 0,
    SEC_6K_SOURCE: 0,
    "Nasdaq": 1,
    "Alpha Vantage": 2,
    YFINANCE_SOURCE: 3,
}


def _is_us_after_hours_event(event: EarningsCalendarEvent) -> bool:
    market = str(event.market or "").strip().upper()
    if market not in {"US", "NASDAQ", "NYSE", "AMEX"}:
        return False
    time_label = str(event.time_label or "").strip()
    time_label_lower = time_label.lower()
    return time_label == "盘后" or "after" in time_label_lower


def event_calendar_date(event: EarningsCalendarEvent) -> str:
    beijing_day = _date_from_beijing_time(event.beijing_time, event.report_date)
    if beijing_day is not None:
        return beijing_day.isoformat()
    report_day = _date_from_any(event.report_date)
    if report_day is not None and _is_us_after_hours_event(event):
        return (report_day + dt.timedelta(days=1)).isoformat()
    return str(event.report_date or "").strip()[:10]


def _time_label_rank(event: EarningsCalendarEvent) -> int:
    time_label = str(event.time_label or "").strip()
    time_label_lower = time_label.lower()
    if time_label == "盘前" or "pre" in time_label_lower:
        return 0
    if time_label == "盘中" or "during" in time_label_lower:
        return 1
    if time_label == "盘后" or "after" in time_label_lower:
        return 2
    return 3


def event_sort_key(event: EarningsCalendarEvent) -> tuple:
    calendar_day = event_calendar_date(event)
    exact_time = _datetime_from_beijing_time(event.beijing_time, event.report_date)
    priority = _PRIORITY_RANK.get(event.priority, 9)
    if exact_time is not None:
        return (calendar_day, 0, exact_time.hour * 60 + exact_time.minute, priority, event.ticker)
    time_label = str(event.time_label or "").strip()
    if time_label and time_label not in {"待确认", "未知", "-"}:
        return (calendar_day, 1, _time_label_rank(event), priority, event.ticker)
    return (calendar_day, 2, 0, priority, event.ticker)


def sorted_events(events: list[EarningsCalendarEvent]) -> list[EarningsCalendarEvent]:
    return sorted(list(events or []), key=event_sort_key)


def events_by_date(events: list[EarningsCalendarEvent]) -> dict[str, list[EarningsCalendarEvent]]:
    grouped: dict[str, list[EarningsCalendarEvent]] = {}
    for event in sorted_events(events):
        day = event_calendar_date(event)
        if day:
            grouped.setdefault(day, []).append(event)
    return grouped


def _mark_yfinance_date_conflicts(events: list[EarningsCalendarEvent]) -> list[EarningsCalendarEvent]:
    normalized = [normalize_event_status(event) for event in events or []]
    by_ticker: dict[str, list[EarningsCalendarEvent]] = {}
    for event in normalized:
        by_ticker.setdefault(str(event.ticker or "").strip().upper(), []).append(event)

    reconciled: list[EarningsCalendarEvent] = []
    for event in normalized:
        if not is_yfinance_estimate_event(event):
            reconciled.append(event)
            continue
        event_day = _date_from_any(event.report_date)
        if event_day is None:
            reconciled.append(event)
            continue
        has_conflict = False
        for other in by_ticker.get(str(event.ticker or "").strip().upper(), []):
            if other is event or is_yfinance_estimate_event(other):
                continue
            other_day = _date_from_any(other.report_date)
            if other_day is None:
                continue
            if abs((event_day - other_day).days) > 1:
                has_conflict = True
                break
        if has_conflict:
            reconciled.append(replace(event, status=YFINANCE_CONFLICT_STATUS))
        else:
            reconciled.append(event)
    return reconciled


def merge_events(events: list[EarningsCalendarEvent]) -> list[EarningsCalendarEvent]:
    events = _mark_yfinance_date_conflicts(list(events or []))
    selected: list[EarningsCalendarEvent] = []
    for event in sorted_events(events):
        event_day = _date_from_any(event.report_date)
        replace_index = None
        for idx, existing in enumerate(selected):
            if existing.ticker != event.ticker:
                continue
            existing_day = _date_from_any(existing.report_date)
            same_window = (
                event_day is not None
                and existing_day is not None
                and abs((event_day - existing_day).days) <= 1
            )
            if existing.report_date == event.report_date or same_window:
                existing_rank = _SOURCE_RANK.get(existing.source, 9)
                event_rank = _SOURCE_RANK.get(event.source, 9)
                if event_rank < existing_rank:
                    replace_index = idx
                else:
                    replace_index = -1
                break
        if replace_index is None:
            selected.append(event)
        elif replace_index >= 0:
            selected[replace_index] = event
    return sorted_events(selected)


def _date_text(day: dt.date) -> str:
    return day.strftime("%Y-%m-%d")


def build_demo_events(today: dt.date | None = None) -> list[EarningsCalendarEvent]:
    base = today or dt.date.today()
    samples = [
        (3, "NVIDIA", "NVDA", "AI加速芯片与定制ASIC", "\u76d8\u540e", "05-08 05:00", "confirmed", "super_giant"),
        (5, "TSMC", "TSM", "先进制程代工", "\u76d8\u524d", "05-09 14:30", "confirmed", "super_giant"),
        (9, "Applied Materials", "AMAT", "前道晶圆设备与量测", "\u76d8\u540e", "05-14 05:30", "estimated", "normal"),
        (16, "Synopsys", "SNPS", "EDA与底层IP", "", "05-20 06:00", "estimated", "normal"),
    ]
    events = []
    for offset, company, ticker, sector, time_label, beijing_time, status, priority in samples:
        events.append(
            EarningsCalendarEvent(
                company=company,
                ticker=ticker,
                sector=sector,
                report_date=_date_text(base + dt.timedelta(days=offset)),
                time_label=time_label,
                beijing_time=beijing_time,
                status=status,
                source="\u793a\u4f8b",
                priority=priority,
            )
        )
    return events
