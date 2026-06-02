# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import io
from typing import Mapping

import requests

from domains.global_earnings_calendar.constants import DEFAULT_HORIZON
from domains.global_earnings_calendar.event_ops import sorted_events
from domains.global_earnings_calendar.models import EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.rules import market_from_ticker
from infra.http_safety import requests_get_https


class AlphaVantageEarningsCalendarProvider:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        session=None,
        base_url: str = "https://www.alphavantage.co/query",
        timeout: tuple[int, int] = (5, 20),
    ):
        self.api_key = (api_key or "").strip()
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        horizon: str = DEFAULT_HORIZON,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        if not self.api_key:
            return []
        response = requests_get_https(
            self.base_url,
            session=self.session,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": self.api_key,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self.parse_csv(response.text or "", universe)

    def parse_csv(
        self,
        csv_text: str,
        universe: Mapping[str, OligarchCompany],
    ) -> list[EarningsCalendarEvent]:
        if not csv_text.strip():
            return []
        reader = csv.DictReader(io.StringIO(csv_text))
        events: list[EarningsCalendarEvent] = []
        for row in reader:
            symbol = str(row.get("symbol", "") or "").strip().upper()
            if not symbol or symbol not in universe:
                continue
            report_date = str(row.get("reportDate", "") or "").strip()[:10]
            if not report_date:
                continue
            company = universe[symbol]
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=symbol,
                    sector=company.sector,
                    report_date=report_date,
                    fiscal_period=str(row.get("fiscalDateEnding", "") or "").strip()[:10],
                    time_label="\u5f85\u786e\u8ba4",
                    status="estimated",
                    source="Alpha Vantage",
                    priority=company.priority,
                    market=getattr(company, "market", market_from_ticker(symbol)),
                )
            )
        return sorted_events(events)
