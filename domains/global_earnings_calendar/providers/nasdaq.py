# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from concurrent import futures
from typing import Mapping

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import sorted_events
from domains.global_earnings_calendar.models import EarningsCalendarEvent, OligarchCompany
from infra.http_safety import requests_get_https

log = get_logger(__name__)


class NasdaqEarningsCalendarProvider:
    _TIME_LABELS = {
        "time-after-hours": "\u76d8\u540e",
        "time-pre-market": "\u76d8\u524d",
        "time-during-market": "\u76d8\u4e2d",
        "time-not-supplied": "\u5f85\u786e\u8ba4",
    }

    def __init__(
        self,
        *,
        session=None,
        base_url: str = "https://api.nasdaq.com/api/calendar/earnings",
        timeout: tuple[int, int] = (5, 20),
        max_workers: int = 8,
    ):
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout
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
        us_symbols = {ticker for ticker, company in universe.items() if company.market == "US"}
        if not us_symbols:
            return []

        days = [today + dt.timedelta(days=offset) for offset in range(max(0, int(lookahead_days)) + 1)]
        events: list[EarningsCalendarEvent] = []
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(days))) as executor:
            future_map = {executor.submit(self._fetch_day, day, universe, us_symbols): day for day in days}
            for future in futures.as_completed(future_map):
                day = future_map[future]
                try:
                    events.extend(future.result())
                except (requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                    log.debug(f"[global earnings calendar] Nasdaq skip {day}: {exc}")
        return sorted_events(events)

    def _fetch_day(
        self,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        us_symbols: set[str],
    ) -> list[EarningsCalendarEvent]:
        response = requests_get_https(
            self.base_url,
            session=self.session,
            params={"date": day.strftime("%Y-%m-%d")},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._parse_payload(response.json(), day, universe, us_symbols)

    def _parse_payload(
        self,
        payload,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        allowed_symbols: set[str],
    ) -> list[EarningsCalendarEvent]:
        data = payload.get("data") if isinstance(payload, Mapping) else {}
        rows = data.get("rows") if isinstance(data, Mapping) else []
        events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol", "") or "").strip().upper()
            if symbol not in allowed_symbols:
                continue
            company = universe[symbol]
            time_code = str(row.get("time", "") or "").strip()
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=symbol,
                    sector=company.sector,
                    report_date=day.strftime("%Y-%m-%d"),
                    fiscal_period=str(row.get("fiscalQuarterEnding", "") or "").strip(),
                    time_label=self._TIME_LABELS.get(time_code, "\u5f85\u786e\u8ba4"),
                    status="estimated",
                    source="Nasdaq",
                    priority=company.priority,
                    market=company.market,
                )
            )
        return events
