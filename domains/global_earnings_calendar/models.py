# -*- coding: utf-8 -*-
"""Shared models and status helpers for the global earnings calendar."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping

YFINANCE_SOURCE = "Yahoo Finance"
YFINANCE_UNVERIFIED_STATUS = "estimated_unverified"
YFINANCE_CONFLICT_STATUS = "estimated_conflict"
CONFIRMED_STATUS = "confirmed"


def _normalize_status_value(status: str, source: str) -> str:
    status_text = str(status or "").strip() or "estimated"
    source_text = str(source or "").strip()
    if source_text == YFINANCE_SOURCE and status_text != CONFIRMED_STATUS:
        return (
            status_text
            if status_text in {YFINANCE_UNVERIFIED_STATUS, YFINANCE_CONFLICT_STATUS}
            else YFINANCE_UNVERIFIED_STATUS
        )
    return status_text


@dataclass(frozen=True)
class OligarchCompany:
    company: str
    ticker: str
    sector: str = ""
    priority: str = "normal"
    market: str = "US"


@dataclass(frozen=True)
class EarningsCalendarEvent:
    company: str
    ticker: str
    sector: str
    report_date: str
    fiscal_period: str = ""
    time_label: str = ""
    beijing_time: str = ""
    status: str = "estimated"
    source: str = ""
    priority: str = "normal"
    conference_url: str = ""
    market: str = ""
    original_call_time_text: str = ""
    original_timezone: str = ""
    call_time_source_url: str = ""
    call_time_source_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping | None) -> "EarningsCalendarEvent | None":
        if not isinstance(payload, Mapping):
            return None
        ticker = str(payload.get("ticker", "") or "").strip()
        report_date = str(payload.get("report_date", "") or "").strip()
        if not ticker or not report_date:
            return None
        source = str(payload.get("source", "") or "").strip()
        status = _normalize_status_value(str(payload.get("status", "") or "").strip(), source)
        return cls(
            company=str(payload.get("company", "") or "").strip() or ticker,
            ticker=ticker,
            sector=str(payload.get("sector", "") or "").strip(),
            report_date=report_date[:10],
            fiscal_period=str(payload.get("fiscal_period", "") or "").strip(),
            time_label=str(payload.get("time_label", "") or "").strip(),
            beijing_time=str(payload.get("beijing_time", "") or "").strip(),
            status=status,
            source=source,
            priority=str(payload.get("priority", "") or "").strip() or "normal",
            conference_url=str(payload.get("conference_url", "") or "").strip(),
            market=str(payload.get("market", "") or "").strip(),
            original_call_time_text=str(payload.get("original_call_time_text", "") or "").strip(),
            original_timezone=str(payload.get("original_timezone", "") or "").strip(),
            call_time_source_url=str(payload.get("call_time_source_url", "") or "").strip(),
            call_time_source_type=str(payload.get("call_time_source_type", "") or "").strip(),
        )


def _hydrate_event_from_company(
    event: EarningsCalendarEvent,
    company: OligarchCompany,
    **changes,
) -> EarningsCalendarEvent:
    return replace(
        event,
        company=company.company,
        ticker=event.ticker.strip().upper(),
        sector=company.sector or event.sector,
        priority=company.priority or event.priority,
        market=company.market or event.market,
        **changes,
    )


class ConfirmedEventWriteError(RuntimeError):
    pass


def normalize_event_status(event: EarningsCalendarEvent) -> EarningsCalendarEvent:
    normalized = _normalize_status_value(event.status, event.source)
    if normalized == event.status:
        return event
    return replace(event, status=normalized)


def is_yfinance_estimate_event(event: EarningsCalendarEvent) -> bool:
    return str(event.source or "").strip() == YFINANCE_SOURCE and str(event.status or "").strip() != CONFIRMED_STATUS


def is_yfinance_date_conflict_event(event: EarningsCalendarEvent) -> bool:
    return (
        str(event.source or "").strip() == YFINANCE_SOURCE
        and str(event.status or "").strip() == YFINANCE_CONFLICT_STATUS
    )


def _events_match_identity(existing: EarningsCalendarEvent, candidate: EarningsCalendarEvent) -> bool:
    if existing.ticker.strip().upper() != candidate.ticker.strip().upper():
        return False
    if existing.report_date[:10] != candidate.report_date[:10]:
        return False
    existing_period = str(existing.fiscal_period or "").strip()
    candidate_period = str(candidate.fiscal_period or "").strip()
    return not existing_period or not candidate_period or existing_period == candidate_period


__all__ = [
    "CONFIRMED_STATUS",
    "ConfirmedEventWriteError",
    "EarningsCalendarEvent",
    "OligarchCompany",
    "YFINANCE_CONFLICT_STATUS",
    "YFINANCE_SOURCE",
    "YFINANCE_UNVERIFIED_STATUS",
    "is_yfinance_date_conflict_event",
    "is_yfinance_estimate_event",
    "normalize_event_status",
]
