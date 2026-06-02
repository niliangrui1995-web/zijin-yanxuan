# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from concurrent import futures
from typing import Mapping

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import sorted_events
from domains.global_earnings_calendar.models import (
    YFINANCE_SOURCE,
    YFINANCE_UNVERIFIED_STATUS,
    EarningsCalendarEvent,
    OligarchCompany,
)
from domains.global_earnings_calendar.providers._utils import _ensure_ascii_ca_bundle
from domains.global_earnings_calendar.rules import date_from_any as _date_from_any

log = get_logger(__name__)


class YFinanceEarningsCalendarProvider:
    def __init__(
        self,
        *,
        ticker_factory=None,
        include_us: bool = False,
        max_workers: int = 8,
    ):
        self.ticker_factory = ticker_factory
        self.include_us = include_us
        self.max_workers = max(1, int(max_workers or 1))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        companies = [company for company in universe.values() if self.include_us or company.market != "US"]
        if not companies:
            return []

        ticker_factory = self.ticker_factory or self._load_yfinance_ticker_factory()
        events: list[EarningsCalendarEvent] = []
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(companies))) as executor:
            future_map = {
                executor.submit(self._fetch_one, ticker_factory, company, today, lookahead_days): company
                for company in companies
            }
            for future in futures.as_completed(future_map):
                company = future_map[future]
                try:
                    events.extend(future.result())
                except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    log.debug(f"[global earnings calendar] yfinance skip {company.ticker}: {exc}")
        return sorted_events(events)

    @staticmethod
    def _load_yfinance_ticker_factory():
        import yfinance as yf

        try:
            from vcp.fetchers.yf_session import build_yf_session

            session = build_yf_session()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[global earnings calendar] unable to build project yfinance session: {exc}")
            _ensure_ascii_ca_bundle()
            session = None

        if session is None:
            return yf.Ticker
        return lambda ticker: yf.Ticker(ticker, session=session)

    @staticmethod
    def _fetch_one(
        ticker_factory, company: OligarchCompany, today: dt.date, lookahead_days: int
    ) -> list[EarningsCalendarEvent]:
        ticker = ticker_factory(company.ticker)
        calendar = getattr(ticker, "calendar", None)
        if callable(calendar):
            calendar = calendar()
        if not isinstance(calendar, Mapping):
            get_calendar = getattr(ticker, "get_calendar", None)
            calendar = get_calendar() if callable(get_calendar) else {}
        if not isinstance(calendar, Mapping):
            return []

        raw_dates = calendar.get("Earnings Date") or calendar.get("Earnings Dates") or []
        if not isinstance(raw_dates, (list, tuple, set)):
            raw_dates = [raw_dates]
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        events: list[EarningsCalendarEvent] = []
        for raw_date in raw_dates:
            day = _date_from_any(raw_date)
            if day is None or not (today <= day <= end):
                continue
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=company.ticker,
                    sector=company.sector,
                    report_date=day.strftime("%Y-%m-%d"),
                    time_label="\u5f85\u786e\u8ba4",
                    status=YFINANCE_UNVERIFIED_STATUS,
                    source=YFINANCE_SOURCE,
                    priority=company.priority,
                    market=company.market,
                )
            )
        return events
