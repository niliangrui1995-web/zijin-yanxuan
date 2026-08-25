# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Mapping

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_COMPANY_IR_SOURCES_PATH, DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import COMPANY_IR_SOURCE, sorted_events
from domains.global_earnings_calendar.http_utils import raise_for_status as _raise_for_status
from domains.global_earnings_calendar.http_utils import response_text as _response_text
from domains.global_earnings_calendar.models import CONFIRMED_STATUS, EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.rules import (
    date_from_any as _date_from_any,
)
from domains.global_earnings_calendar.rules import (
    date_from_compact_text as _date_from_compact_text,
)
from domains.global_earnings_calendar.rules import (
    date_from_english_text as _date_from_english_text,
)
from domains.global_earnings_calendar.rules import (
    text_has_any as _text_has_any,
)
from infra.http_safety import https_url_host_allowlist, requests_get_https

log = get_logger(__name__)


class CompanyIrEarningsCalendarProvider:
    def __init__(
        self,
        *,
        session=None,
        rules: Mapping[str, list[Mapping]] | None = None,
        rules_path: str | Path | None = None,
        timeout: tuple[int, int] = (5, 20),
    ):
        self.session = session or requests
        self.rules = {str(k).upper(): list(v or []) for k, v in dict(rules or {}).items()}
        self.rules_path = Path(rules_path) if rules_path is not None else DEFAULT_COMPANY_IR_SOURCES_PATH
        self.timeout = timeout

    def _load_rules(self) -> dict[str, list[Mapping]]:
        if self.rules:
            return dict(self.rules)
        if not self.rules_path.is_file():
            return {}
        try:
            payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(f"[global earnings calendar] company IR rules unavailable: {exc}")
            return {}
        rows = payload.get("sources") if isinstance(payload, Mapping) else payload
        if not isinstance(rows, Mapping):
            return {}
        return {str(k).upper(): list(v or []) for k, v in rows.items() if isinstance(v, list)}

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        rules = self._load_rules()
        if not rules:
            return []
        events: list[EarningsCalendarEvent] = []
        for ticker, ticker_rules in rules.items():
            company = universe.get(ticker)
            if company is None:
                continue
            for rule in ticker_rules:
                if not isinstance(rule, Mapping):
                    continue
                url = str(rule.get("url", "") or "").strip()
                if not url:
                    continue
                response = requests_get_https(
                    url,
                    session=self.session,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=self.timeout,
                    allowed_hosts=https_url_host_allowlist(url),
                )
                _raise_for_status(response)
                events.extend(
                    self.parse_page(
                        _response_text(response, encoding=str(rule.get("encoding") or "utf-8")),
                        company,
                        rule,
                        today=today,
                        lookahead_days=lookahead_days,
                    )
                )
        return sorted_events(events)

    @staticmethod
    def parse_page(
        html_text: str,
        company: OligarchCompany,
        rule: Mapping,
        *,
        today: dt.date,
        lookahead_days: int,
    ) -> list[EarningsCalendarEvent]:
        from lxml import html

        url = str(rule.get("url", "") or "").strip()
        source_type = str(rule.get("source_type", "") or "").strip() or "official_ir_calendar"
        include_keywords = tuple(str(item) for item in (rule.get("include_keywords") or []))
        text = " ".join(html.fromstring(html_text or "").text_content().split())
        explicit_report_day = _date_from_any(rule.get("report_date"))
        if explicit_report_day is None:
            explicit_report_day = _date_from_english_text(str(rule.get("report_date", "") or ""))
        if explicit_report_day is None:
            explicit_report_day = _date_from_compact_text(str(rule.get("report_date", "") or ""))
        if not include_keywords and explicit_report_day is None:
            return []
        if include_keywords and not _text_has_any(text, include_keywords):
            return []
        report_day = explicit_report_day or _date_from_english_text(text) or _date_from_compact_text(text)
        if report_day is None:
            return []
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        if not (today <= report_day <= end):
            return []
        original_call_time_text = str(rule.get("original_call_time_text", "") or rule.get("label", "") or "").strip()
        return [
            EarningsCalendarEvent(
                company=company.company,
                ticker=company.ticker,
                sector=company.sector,
                report_date=report_day.isoformat(),
                fiscal_period=str(rule.get("fiscal_period", "") or "").strip(),
                time_label=str(rule.get("time_label", "") or "").strip() or "\u5f85\u786e\u8ba4",
                beijing_time=str(rule.get("beijing_time", "") or "").strip(),
                status=CONFIRMED_STATUS,
                source=COMPANY_IR_SOURCE,
                priority=company.priority,
                conference_url=str(rule.get("conference_url", "") or "").strip() or url,
                market=company.market,
                original_call_time_text=original_call_time_text,
                original_timezone=str(rule.get("original_timezone", "") or "").strip(),
                call_time_source_url=url,
                call_time_source_type=source_type,
            )
        ]
