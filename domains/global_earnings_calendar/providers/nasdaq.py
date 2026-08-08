# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from typing import Mapping

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import sorted_events
from domains.global_earnings_calendar.models import EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.providers._utils import _collect_daemon_task_results
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
        self.last_degradation: dict[str, object] | None = None

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        self.last_degradation = None
        us_symbols = {ticker for ticker, company in universe.items() if company.market == "US"}
        if not us_symbols:
            return []

        days = [today + dt.timedelta(days=offset) for offset in range(max(0, int(lookahead_days)) + 1)]
        events: list[EarningsCalendarEvent] = []
        failed_days: dict[str, str] = {}
        tasks = [(day, lambda day=day: self._fetch_day(day, universe, us_symbols)) for day in days]
        for day, rows, error in _collect_daemon_task_results(
            tasks,
            max_workers=min(self.max_workers, len(days)),
            thread_name_prefix="global-earnings-nasdaq-day",
        ):
            if error is None:
                events.extend(rows or [])
                continue
            if not isinstance(error, (requests.RequestException, OSError, RuntimeError, TypeError, ValueError)):
                raise error
            failed_days[day.isoformat()] = str(error)
            log.debug(f"[global earnings calendar] Nasdaq skip {day}: {error}")
        sorted_result = sorted_events(events)
        if failed_days:
            requested_days = [day.isoformat() for day in days]
            failed_day_values = sorted(failed_days)
            all_days_failed = len(failed_day_values) == len(days)
            self.last_degradation = {
                "provider": "Nasdaq",
                "reason": "day_fetch_failed",
                "failed_days": failed_day_values,
                "failed_count": len(failed_day_values),
                "requested_days": requested_days,
                "requested_count": len(requested_days),
                "returned_events": len(sorted_result),
                "all_days_failed": all_days_failed,
                "sample_error": failed_days[failed_day_values[0]],
            }
            if all_days_failed:
                log.warning(
                    "[global earnings calendar] Nasdaq degraded: all %s requested days failed; cached snapshot should be reused",
                    len(requested_days),
                )
        return sorted_result

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
