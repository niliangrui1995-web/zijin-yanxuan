# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import os
from typing import Mapping

import requests

from domains.global_earnings_calendar.constants import DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import SEC_6K_SOURCE, sorted_events
from domains.global_earnings_calendar.http_utils import raise_for_status as _raise_for_status
from domains.global_earnings_calendar.models import CONFIRMED_STATUS, EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.rules import date_from_any as _date_from_any
from domains.global_earnings_calendar.rules import text_has_any as _text_has_any
from infra.http_safety import https_url_host_allowlist, requests_get_https

_SEC_6K_KEYWORDS = (
    "earnings",
    "financial result",
    "quarterly result",
    "annual result",
    "financial statement",
)


class SecSixKEarningsProvider:
    DEFAULT_LOCAL_ADR_TICKERS = {
        "2330.TW": "TSM",
        "3711.TW": "ASX",
    }

    def __init__(
        self,
        *,
        session=None,
        local_adr_tickers: Mapping[str, str] | None = None,
        ticker_ciks: Mapping[str, str] | None = None,
        company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json",
        submissions_url_template: str = "https://data.sec.gov/submissions/CIK{cik}.json",
        timeout: tuple[int, int] = (5, 20),
    ):
        self.session = session or requests
        self.local_adr_tickers = dict(local_adr_tickers or self.DEFAULT_LOCAL_ADR_TICKERS)
        self.ticker_ciks = {str(k).upper(): str(v).zfill(10) for k, v in dict(ticker_ciks or {}).items()}
        self.company_tickers_url = company_tickers_url
        self.submissions_url_template = submissions_url_template
        self.timeout = timeout

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": os.environ.get("SEC_USER_AGENT", "vcp-hunter-local/1.0 contact@example.com"),
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        tw_universe = {ticker: company for ticker, company in universe.items() if company.market == "TW"}
        target_tickers = [
            ticker for ticker in tw_universe if ticker in self.local_adr_tickers or ticker in self.ticker_ciks
        ]
        if not target_tickers:
            return []
        cik_map = self._resolve_cik_map(target_tickers)
        if not cik_map:
            return []
        events: list[EarningsCalendarEvent] = []
        for ticker in target_tickers:
            cik = cik_map.get(ticker)
            company = tw_universe.get(ticker)
            if not cik or company is None:
                continue
            response = requests_get_https(
                self.submissions_url_template.format(cik=str(cik).zfill(10)),
                session=self.session,
                headers=self._headers(),
                timeout=self.timeout,
                allowed_hosts=https_url_host_allowlist(self.submissions_url_template.format(cik=str(cik).zfill(10))),
                allow_reserved_tun_for_allowed_hosts=True,
            )
            _raise_for_status(response)
            events.extend(
                self.parse_submissions(
                    response.json(),
                    company,
                    cik=str(cik).zfill(10),
                    today=today,
                    lookahead_days=lookahead_days,
                )
            )
        return sorted_events(events)

    def _resolve_cik_map(self, target_tickers: list[str]) -> dict[str, str]:
        resolved = {ticker: self.ticker_ciks[ticker] for ticker in target_tickers if ticker in self.ticker_ciks}
        missing = [ticker for ticker in target_tickers if ticker not in resolved and ticker in self.local_adr_tickers]
        if not missing:
            return resolved
        response = requests_get_https(
            self.company_tickers_url,
            session=self.session,
            headers={**self._headers(), "Host": "www.sec.gov"},
            timeout=self.timeout,
            allowed_hosts=https_url_host_allowlist(self.company_tickers_url),
            allow_reserved_tun_for_allowed_hosts=True,
        )
        _raise_for_status(response)
        payload = response.json()
        adr_to_cik: dict[str, str] = {}
        rows = payload.values() if isinstance(payload, Mapping) else payload or []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = str(row.get("ticker", "") or "").strip().upper()
            cik = str(row.get("cik_str", "") or "").strip()
            if ticker and cik:
                adr_to_cik[ticker] = cik.zfill(10)
        for local_ticker in missing:
            adr_ticker = str(self.local_adr_tickers.get(local_ticker, "") or "").upper()
            if adr_ticker in adr_to_cik:
                resolved[local_ticker] = adr_to_cik[adr_ticker]
        return resolved

    @staticmethod
    def parse_submissions(
        payload,
        company: OligarchCompany,
        *,
        cik: str,
        today: dt.date,
        lookahead_days: int,
    ) -> list[EarningsCalendarEvent]:
        if not isinstance(payload, Mapping):
            return []
        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), Mapping) else {}
        if not isinstance(recent, Mapping):
            return []
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accession_numbers = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []
        events: list[EarningsCalendarEvent] = []
        for idx, form in enumerate(forms):
            if str(form or "").strip().upper() != "6-K":
                continue
            filing_day = _date_from_any(filing_dates[idx] if idx < len(filing_dates) else "")
            if filing_day is None or not (today <= filing_day <= end):
                continue
            primary_doc = str(primary_docs[idx] if idx < len(primary_docs) else "" or "").strip()
            description = str(descriptions[idx] if idx < len(descriptions) else "" or "").strip()
            descriptor = f"{primary_doc} {description}"
            if not _text_has_any(descriptor, _SEC_6K_KEYWORDS):
                continue
            accession = str(accession_numbers[idx] if idx < len(accession_numbers) else "" or "").strip()
            source_url = "https://www.sec.gov/edgar/search/"
            if accession and primary_doc:
                source_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"
                )
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=company.ticker,
                    sector=company.sector,
                    report_date=filing_day.isoformat(),
                    fiscal_period="",
                    time_label="\u5f85\u786e\u8ba4",
                    status=CONFIRMED_STATUS,
                    source=SEC_6K_SOURCE,
                    priority=company.priority,
                    conference_url=source_url,
                    market=company.market,
                    original_call_time_text=description or primary_doc,
                    original_timezone="America/New_York",
                    call_time_source_url=source_url,
                    call_time_source_type="sec_6k",
                )
            )
        return sorted_events(events)
