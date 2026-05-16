# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import datetime as dt
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent import futures
from pathlib import Path
from typing import Mapping
from urllib.parse import urljoin

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.event_ops import (
    _PRIORITY_RANK as _PRIORITY_RANK,
)
from domains.global_earnings_calendar.event_ops import (
    _SOURCE_RANK as _SOURCE_RANK,
)
from domains.global_earnings_calendar.event_ops import (
    COMPANY_IR_SOURCE as COMPANY_IR_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    DART_SOURCE as DART_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    JPX_SOURCE as JPX_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    KIND_SOURCE as KIND_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    MOPS_SOURCE as MOPS_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    SEC_6K_SOURCE as SEC_6K_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    TDNET_SOURCE as TDNET_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    _date_text as _date_text,
)
from domains.global_earnings_calendar.event_ops import (
    _is_us_after_hours_event as _is_us_after_hours_event,
)
from domains.global_earnings_calendar.event_ops import (
    _mark_yfinance_date_conflicts as _mark_yfinance_date_conflicts,
)
from domains.global_earnings_calendar.event_ops import (
    _time_label_rank as _time_label_rank,
)
from domains.global_earnings_calendar.event_ops import (
    build_demo_events as build_demo_events,
)
from domains.global_earnings_calendar.event_ops import (
    event_calendar_date as event_calendar_date,
)
from domains.global_earnings_calendar.event_ops import (
    event_sort_key as event_sort_key,
)
from domains.global_earnings_calendar.event_ops import (
    events_by_date as events_by_date,
)
from domains.global_earnings_calendar.event_ops import (
    merge_events as merge_events,
)
from domains.global_earnings_calendar.event_ops import (
    sorted_events as sorted_events,
)
from domains.global_earnings_calendar.models import (
    CONFIRMED_STATUS,
    YFINANCE_SOURCE,
    YFINANCE_UNVERIFIED_STATUS,
    ConfirmedEventWriteError,
    EarningsCalendarEvent,
    OligarchCompany,
    _events_match_identity,
    _normalize_status_value,
)
from domains.global_earnings_calendar.models import (
    YFINANCE_CONFLICT_STATUS as YFINANCE_CONFLICT_STATUS,
)
from domains.global_earnings_calendar.models import (
    is_yfinance_date_conflict_event as is_yfinance_date_conflict_event,
)
from domains.global_earnings_calendar.models import (
    is_yfinance_estimate_event as is_yfinance_estimate_event,
)
from domains.global_earnings_calendar.models import (
    normalize_event_status as normalize_event_status,
)
from domains.global_earnings_calendar.rules import (
    beijing_time_from_local as _beijing_time_from_local,
)
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
    label_matches_company as _label_matches_company,
)
from domains.global_earnings_calendar.rules import (
    local_code_from_ticker as _local_code_from_ticker,
)
from domains.global_earnings_calendar.rules import (
    market_from_ticker,
)
from domains.global_earnings_calendar.rules import (
    text_has_any as _text_has_any,
)

log = get_logger(__name__)

CACHE_KEY = "global_earnings_calendar"
DEFAULT_HORIZON = "3month"
DEFAULT_LOOKAHEAD_DAYS = 45
DEFAULT_CONFIRMED_EVENTS_PATH = Path(__file__).with_name("confirmed_events.json")
DEFAULT_COMPANY_IR_SOURCES_PATH = Path(__file__).with_name("company_ir_sources.json")
_JP_EARNINGS_KEYWORDS = (
    "\u6c7a\u7b97\u77ed\u4fe1",
    "\u56db\u534a\u671f\u6c7a\u7b97\u77ed\u4fe1",
    "\u6c7a\u7b97\u8aac\u660e\u8cc7\u6599",
    "\u6c7a\u7b97\u767a\u8868",
)
_KR_EARNINGS_KEYWORDS = (
    "\uc601\uc5c5(\uc7a0\uc815)\uc2e4\uc801",
    "\uc7a0\uc815\uc2e4\uc801",
    "\ubd84\uae30\ubcf4\uace0\uc11c",
    "\ubc18\uae30\ubcf4\uace0\uc11c",
    "\uc0ac\uc5c5\ubcf4\uace0\uc11c",
    "\uacb0\uc0b0\uc2e4\uc801",
    "\ub9e4\ucd9c\uc561\ub610\ub294\uc190\uc775\uad6c\uc870",
)
_MOPS_EARNINGS_KEYWORDS = (
    "earnings conference",
    "financial statements",
    "financial report",
    "quarterly results",
    "annual results",
    "results",
)
_SEC_6K_KEYWORDS = (
    "earnings",
    "financial result",
    "quarterly result",
    "annual result",
    "financial statement",
)
def _response_text(response, *, encoding: str | None = None) -> str:
    if encoding and hasattr(response, "encoding"):
        try:
            response.encoding = encoding
        except (AttributeError, TypeError, ValueError):
            pass
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode(encoding or "utf-8", errors="replace")
    return str(text or "")


def _raise_for_status(response) -> None:
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
        return
    status_code = int(getattr(response, "status_code", 200) or 200)
    if status_code >= 400:
        raise requests.HTTPError(f"http {status_code}")


def _ensure_industry_module_path() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pipeline_dir = project_root.parent / "\u6bcf\u65e5\u6218\u62a5" / "\u6bcf\u65e5\u6218\u62a5"
    pipeline_text = str(pipeline_dir)
    if pipeline_dir.is_dir() and pipeline_text not in sys.path:
        sys.path.insert(0, pipeline_text)


def _load_industry_module():
    _ensure_industry_module_path()
    try:
        return importlib.import_module("industry_dict")
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[global earnings calendar] industry_dict unavailable: {exc}")
        return None


def build_oligarch_universe(industry_module=None) -> dict[str, OligarchCompany]:
    module = industry_module or _load_industry_module()
    if module is None:
        return {}

    oligarch_dict = getattr(module, "OLIGARCH_DICT", {}) or {}
    tickers = getattr(module, "VANGUARD_TICKERS", {}) or {}
    super_giants = set(getattr(module, "SUPER_GIANTS", set()) or set())
    strategic_giants = set(getattr(module, "STRATEGIC_GIANTS", set()) or set())

    sector_by_company: dict[str, str] = {}
    for sector, companies in dict(oligarch_dict).items():
        for raw_company in list(companies or []):
            for ticker_company in tickers:
                if _label_matches_company(str(raw_company), str(ticker_company)):
                    sector_by_company.setdefault(str(ticker_company), str(sector))

    universe: dict[str, OligarchCompany] = {}
    for company, ticker in dict(tickers).items():
        ticker_text = str(ticker or "").strip().upper()
        if not ticker_text:
            continue
        company_text = str(company or "").strip()
        if company_text in super_giants:
            priority = "super_giant"
        elif company_text in strategic_giants:
            priority = "strategic_giant"
        else:
            priority = "normal"
        universe[ticker_text] = OligarchCompany(
            company=company_text,
            ticker=ticker_text,
            sector=sector_by_company.get(company_text, ""),
            priority=priority,
            market=market_from_ticker(ticker_text),
        )
    return universe


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
        response = self.session.get(
            self.base_url,
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


class ConfirmedEarningsEventsProvider:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_CONFIRMED_EVENTS_PATH

    def fetch(self, universe: Mapping[str, OligarchCompany], **_kwargs) -> list[EarningsCalendarEvent]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(f"[global earnings calendar] confirmed events unavailable: {exc}")
            return []

        rows = payload.get("events") if isinstance(payload, Mapping) else payload
        events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            event = EarningsCalendarEvent.from_dict(row)
            if event is None:
                continue
            ticker = event.ticker.strip().upper()
            company = universe.get(ticker)
            if company is None:
                continue
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=ticker,
                    sector=company.sector or event.sector,
                    report_date=event.report_date,
                    fiscal_period=event.fiscal_period,
                    time_label=event.time_label,
                    beijing_time=event.beijing_time,
                    status=event.status or "confirmed",
                    source=event.source or "confirmed",
                    priority=company.priority or event.priority,
                    conference_url=event.conference_url,
                    market=company.market or event.market,
                    original_call_time_text=event.original_call_time_text,
                    original_timezone=event.original_timezone,
                    call_time_source_url=event.call_time_source_url,
                    call_time_source_type=event.call_time_source_type,
                )
            )
        return sorted_events(events)

    def upsert(self, event: EarningsCalendarEvent) -> None:
        rows: list[dict] = []
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConfirmedEventWriteError(f"confirmed_json_read_failed: {exc}") from exc
            raw_rows = payload.get("events") if isinstance(payload, Mapping) else payload
            if not isinstance(raw_rows, list):
                raise ConfirmedEventWriteError("confirmed_json_events_not_list")
            rows = [dict(row) for row in raw_rows if isinstance(row, Mapping)]

        event_payload = event.to_dict()
        updated = False
        for idx, row in enumerate(rows):
            existing = EarningsCalendarEvent.from_dict(row)
            if existing is not None and _events_match_identity(existing, event):
                rows[idx] = event_payload
                updated = True
                break
        if not updated:
            rows.append(event_payload)

        payload = {"events": rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(self.path)
        except OSError as exc:
            raise ConfirmedEventWriteError(f"confirmed_json_write_failed: {exc}") from exc


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

        days = [
            today + dt.timedelta(days=offset)
            for offset in range(max(0, int(lookahead_days)) + 1)
        ]
        events: list[EarningsCalendarEvent] = []
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(days))) as executor:
            future_map = {
                executor.submit(self._fetch_day, day, universe, us_symbols): day
                for day in days
            }
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
        response = self.session.get(
            self.base_url,
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


class JpxFinancialAnnouncementProvider:
    def __init__(
        self,
        *,
        session=None,
        page_url: str = "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html",
        timeout: tuple[int, int] = (5, 20),
    ):
        self.session = session or requests
        self.page_url = page_url
        self.timeout = timeout

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        jp_symbols = {ticker for ticker, company in universe.items() if company.market == "JP"}
        if not jp_symbols:
            return []

        response = self.session.get(
            self.page_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        _raise_for_status(response)
        workbook_links = self._parse_workbook_links(_response_text(response, encoding="utf-8"), self.page_url)
        events: list[EarningsCalendarEvent] = []
        for workbook_url in workbook_links:
            workbook_response = self.session.get(
                workbook_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": self.page_url},
                timeout=self.timeout,
            )
            _raise_for_status(workbook_response)
            events.extend(
                self.parse_workbook(
                    getattr(workbook_response, "content", b""),
                    universe,
                    allowed_symbols=jp_symbols,
                    source_url=workbook_url,
                )
            )
        return merge_events(self._filter_window(events, today=today, lookahead_days=lookahead_days))

    @staticmethod
    def _parse_workbook_links(html_text: str, page_url: str) -> list[str]:
        from lxml import html

        tree = html.fromstring(html_text or "")
        links: list[str] = []
        for href in tree.xpath('//a[contains(translate(@href, "XLSX", "xlsx"), ".xlsx")]/@href'):
            full_url = urljoin(page_url, str(href))
            if full_url not in links:
                links.append(full_url)
        return links

    @staticmethod
    def _header_index(headers: list[str], *needles: str) -> int | None:
        for idx, header in enumerate(headers):
            normalized = str(header or "")
            if any(needle in normalized for needle in needles):
                return idx
        return None

    @classmethod
    def parse_workbook(
        cls,
        workbook_bytes: bytes,
        universe: Mapping[str, OligarchCompany],
        *,
        allowed_symbols: set[str] | None = None,
        source_url: str = "",
    ) -> list[EarningsCalendarEvent]:
        if not workbook_bytes:
            return []
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=True)
        events: list[EarningsCalendarEvent] = []
        for worksheet in workbook.worksheets:
            header_indexes: tuple[int, int, int | None, int | None] | None = None
            for row in worksheet.iter_rows(values_only=True):
                values = list(row or [])
                if header_indexes is None:
                    headers = [str(value or "") for value in values]
                    date_idx = cls._header_index(headers, "Scheduled Dates", "\u6c7a\u7b97\u767a\u8868\u4e88\u5b9a\u65e5")
                    code_idx = cls._header_index(headers, "Code", "\u30b3\u30fc\u30c9")
                    fiscal_idx = cls._header_index(headers, "Fiscal Year/Quarter", "\u7a2e\u5225")
                    fiscal_end_idx = cls._header_index(headers, "Fiscal Year-end", "\u6c7a\u7b97\u671f\u672b")
                    if date_idx is not None and code_idx is not None:
                        header_indexes = (date_idx, code_idx, fiscal_idx, fiscal_end_idx)
                    continue

                date_idx, code_idx, fiscal_idx, fiscal_end_idx = header_indexes
                if max(date_idx, code_idx) >= len(values):
                    continue
                report_day = _date_from_any(values[date_idx])
                code_digits = re.sub(r"\D", "", str(values[code_idx] or ""))
                if not code_digits:
                    continue
                ticker = f"{code_digits[:4]}.T"
                if allowed_symbols is not None and ticker not in allowed_symbols:
                    continue
                company = universe.get(ticker)
                if company is None or report_day is None:
                    continue
                fiscal_parts = []
                if fiscal_idx is not None and fiscal_idx < len(values) and values[fiscal_idx]:
                    fiscal_parts.append(str(values[fiscal_idx]).strip())
                if fiscal_end_idx is not None and fiscal_end_idx < len(values):
                    fiscal_end = _date_from_any(values[fiscal_end_idx])
                    if fiscal_end is not None:
                        fiscal_parts.append(fiscal_end.isoformat())
                events.append(
                    EarningsCalendarEvent(
                        company=company.company,
                        ticker=ticker,
                        sector=company.sector,
                        report_date=report_day.isoformat(),
                        fiscal_period=" / ".join(fiscal_parts),
                        time_label="\u5f85\u786e\u8ba4",
                        status=CONFIRMED_STATUS,
                        source=JPX_SOURCE,
                        priority=company.priority,
                        market=company.market,
                        call_time_source_url=source_url,
                        call_time_source_type="jpx_financial_announcement_schedule",
                    )
                )
        return sorted_events(events)

    @staticmethod
    def _filter_window(
        events: list[EarningsCalendarEvent],
        *,
        today: dt.date,
        lookahead_days: int,
    ) -> list[EarningsCalendarEvent]:
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        return sorted_events(
            event
            for event in events
            if (day := _date_from_any(event.report_date)) is not None and today <= day <= end
        )


class TdnetEarningsDisclosureProvider:
    def __init__(
        self,
        *,
        session=None,
        base_url_template: str = "https://www.release.tdnet.info/inbs/I_list_001_{date}.html",
        timeout: tuple[int, int] = (5, 20),
        max_forward_days: int = 0,
    ):
        self.session = session or requests
        self.base_url_template = base_url_template
        self.timeout = timeout
        self.max_forward_days = max(0, int(max_forward_days or 0))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        jp_codes = {_local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "JP"}
        if not jp_codes:
            return []
        forward_days = min(max(0, int(lookahead_days)), self.max_forward_days)
        events: list[EarningsCalendarEvent] = []
        for offset in range(forward_days + 1):
            day = today + dt.timedelta(days=offset)
            url = self.base_url_template.format(date=day.strftime("%Y%m%d"))
            response = self.session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.release.tdnet.info/inbs/"},
                timeout=self.timeout,
            )
            if int(getattr(response, "status_code", 200) or 200) == 404:
                continue
            _raise_for_status(response)
            events.extend(self.parse_html(_response_text(response, encoding="utf-8"), day, universe, jp_codes, source_url=url))
        return sorted_events(events)

    @staticmethod
    def parse_html(
        html_text: str,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        jp_codes: Mapping[str, str],
        *,
        source_url: str = "",
    ) -> list[EarningsCalendarEvent]:
        from lxml import html

        tree = html.fromstring(html_text or "")
        events: list[EarningsCalendarEvent] = []
        for row in tree.xpath("//tr"):
            cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td")]
            if len(cells) < 4:
                continue
            code_digits = re.sub(r"\D", "", cells[1])
            if len(code_digits) < 4:
                continue
            ticker = jp_codes.get(code_digits[:4])
            if not ticker:
                continue
            title = cells[3]
            if not _text_has_any(title, _JP_EARNINGS_KEYWORDS):
                continue
            company = universe.get(ticker)
            if company is None:
                continue
            href = ""
            link_nodes = row.xpath(".//a/@href")
            if link_nodes:
                href = urljoin(source_url, str(link_nodes[0]))
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=ticker,
                    sector=company.sector,
                    report_date=day.isoformat(),
                    fiscal_period="",
                    time_label="\u5f85\u786e\u8ba4",
                    beijing_time=_beijing_time_from_local(day, cells[0], utc_offset_hours=9),
                    status=CONFIRMED_STATUS,
                    source=TDNET_SOURCE,
                    priority=company.priority,
                    conference_url=href,
                    market=company.market,
                    original_call_time_text=f"{cells[0]} JST {title}".strip(),
                    original_timezone="Asia/Tokyo",
                    call_time_source_url=href or source_url,
                    call_time_source_type="tdnet_disclosure",
                )
            )
        return sorted_events(events)


class DartEarningsDisclosureProvider:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        session=None,
        base_url: str = "https://opendart.fss.or.kr/api/list.json",
        timeout: tuple[int, int] = (5, 20),
        page_count: int = 100,
        max_pages: int = 10,
    ):
        self.api_key = (api_key if api_key is not None else os.environ.get("OPENDART_API_KEY") or os.environ.get("DART_API_KEY") or "").strip()
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout
        self.page_count = max(1, int(page_count or 100))
        self.max_pages = max(1, int(max_pages or 1))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        if not self.api_key:
            return []
        today = today or dt.date.today()
        kr_codes = {_local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "KR"}
        if not kr_codes:
            return []
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        events: list[EarningsCalendarEvent] = []
        for page_no in range(1, self.max_pages + 1):
            response = self.session.get(
                self.base_url,
                params={
                    "crtfc_key": self.api_key,
                    "bgn_de": today.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "page_no": page_no,
                    "page_count": self.page_count,
                    "sort": "date",
                    "sort_mth": "desc",
                },
                timeout=self.timeout,
            )
            _raise_for_status(response)
            payload = response.json()
            events.extend(self.parse_payload(payload, universe, kr_codes))
            try:
                total_page = int(payload.get("total_page") or 1)
            except (TypeError, ValueError, AttributeError):
                total_page = 1
            if page_no >= total_page:
                break
        return sorted_events(events)

    @staticmethod
    def parse_payload(
        payload,
        universe: Mapping[str, OligarchCompany],
        kr_codes: Mapping[str, str],
    ) -> list[EarningsCalendarEvent]:
        if not isinstance(payload, Mapping):
            return []
        rows = payload.get("list") or []
        events: list[EarningsCalendarEvent] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            stock_code = re.sub(r"\D", "", str(row.get("stock_code", "") or ""))
            ticker = kr_codes.get(stock_code)
            if not ticker:
                continue
            title = str(row.get("report_nm", "") or "").strip()
            if not _text_has_any(title, _KR_EARNINGS_KEYWORDS):
                continue
            report_day = _date_from_compact_text(str(row.get("rcept_dt", "") or ""))
            company = universe.get(ticker)
            if company is None or report_day is None:
                continue
            receipt_no = str(row.get("rcept_no", "") or "").strip()
            source_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}" if receipt_no else "https://dart.fss.or.kr/"
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=ticker,
                    sector=company.sector,
                    report_date=report_day.isoformat(),
                    fiscal_period="",
                    time_label="\u5f85\u786e\u8ba4",
                    status=CONFIRMED_STATUS,
                    source=DART_SOURCE,
                    priority=company.priority,
                    conference_url=source_url,
                    market=company.market,
                    original_call_time_text=title,
                    original_timezone="Asia/Seoul",
                    call_time_source_url=source_url,
                    call_time_source_type="dart_disclosure",
                )
            )
        return sorted_events(events)


class KindEarningsDisclosureProvider:
    def __init__(
        self,
        *,
        session=None,
        base_url: str = "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
        timeout: tuple[int, int] = (5, 20),
        max_forward_days: int = 0,
    ):
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout
        self.max_forward_days = max(0, int(max_forward_days or 0))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        kr_codes = {_local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "KR"}
        if not kr_codes:
            return []
        forward_days = min(max(0, int(lookahead_days)), self.max_forward_days)
        events: list[EarningsCalendarEvent] = []
        for offset in range(forward_days + 1):
            day = today + dt.timedelta(days=offset)
            response = self.session.post(
                self.base_url,
                data={
                    "method": "searchTodayDisclosureSub",
                    "currentPageSize": "100",
                    "pageIndex": "1",
                    "orderMode": "0",
                    "orderStat": "D",
                    "forward": "todaydisclosure_sub",
                    "chose": "S",
                    "shose": "S",
                    "todayFlag": "N",
                    "selDate": day.strftime("%Y-%m-%d"),
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do?method=searchTodayDisclosureMain",
                },
                timeout=self.timeout,
            )
            _raise_for_status(response)
            events.extend(self.parse_html(_response_text(response, encoding="utf-8"), day, universe, kr_codes))
        return sorted_events(events)

    @staticmethod
    def _kind_code_matches(kind_code: str, stock_code: str) -> bool:
        raw = re.sub(r"\D", "", str(kind_code or ""))
        code = re.sub(r"\D", "", str(stock_code or ""))
        if not raw or not code:
            return False
        return raw.zfill(6) == code or f"{raw}0".zfill(6) == code

    @classmethod
    def parse_html(
        cls,
        html_text: str,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        kr_codes: Mapping[str, str],
    ) -> list[EarningsCalendarEvent]:
        from lxml import html

        tree = html.fromstring(html_text or "")
        events: list[EarningsCalendarEvent] = []
        for row in tree.xpath("//tr"):
            cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td")]
            if len(cells) < 3:
                continue
            row_html = html.tostring(row, encoding="unicode")
            code_match = re.search(r"companysummary_open\('([^']+)'", row_html)
            if code_match is None:
                continue
            matched_ticker = None
            for stock_code, ticker in kr_codes.items():
                if cls._kind_code_matches(code_match.group(1), stock_code):
                    matched_ticker = ticker
                    break
            if matched_ticker is None:
                continue
            title = cells[2]
            if not _text_has_any(title, _KR_EARNINGS_KEYWORDS):
                continue
            company = universe.get(matched_ticker)
            if company is None:
                continue
            receipt_match = re.search(r"openDisclsViewer\('([^']+)'", row_html)
            receipt_no = receipt_match.group(1) if receipt_match else ""
            source_url = (
                f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={receipt_no}"
                if receipt_no
                else "https://kind.krx.co.kr/"
            )
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=matched_ticker,
                    sector=company.sector,
                    report_date=day.isoformat(),
                    fiscal_period="",
                    time_label="\u5f85\u786e\u8ba4",
                    beijing_time=_beijing_time_from_local(day, cells[0], utc_offset_hours=9),
                    status=CONFIRMED_STATUS,
                    source=KIND_SOURCE,
                    priority=company.priority,
                    conference_url=source_url,
                    market=company.market,
                    original_call_time_text=f"{cells[0]} KST {title}".strip(),
                    original_timezone="Asia/Seoul",
                    call_time_source_url=source_url,
                    call_time_source_type="kind_disclosure",
                )
            )
        return sorted_events(events)


class MopsEarningsDisclosureProvider:
    def __init__(
        self,
        *,
        session=None,
        base_url: str = "https://emops.twse.com.tw/server-java/t05st01_e",
        timeout: tuple[int, int] = (5, 20),
    ):
        self.session = session or self._default_session()
        self.base_url = base_url
        self.timeout = timeout

    @staticmethod
    def _default_session():
        _ensure_ascii_ca_bundle()
        try:
            import curl_cffi.requests as curl_requests

            return curl_requests
        except ImportError:
            return requests

    def _get(self, url: str, **kwargs):
        try:
            return self.session.get(url, impersonate="chrome", **kwargs)
        except TypeError:
            return self.session.get(url, **kwargs)

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        tw_companies = [company for company in universe.values() if company.market == "TW"]
        if not tw_companies:
            return []
        events: list[EarningsCalendarEvent] = []
        for company in tw_companies:
            typek = "otc" if company.ticker.endswith(".TWO") else "sii"
            response = self._get(
                self.base_url,
                params={
                    "TYPEK": typek,
                    "co_id": _local_code_from_ticker(company.ticker),
                    "year": str(today.year),
                    "month": "all",
                    "step": "0",
                    "query": "co",
                    "colorchg": "1",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.timeout,
            )
            _raise_for_status(response)
            events.extend(
                self.parse_html(
                    _response_text(response, encoding="big5"),
                    company,
                    today=today,
                    lookahead_days=lookahead_days,
                    source_base_url=self.base_url,
                )
            )
        return sorted_events(events)

    @staticmethod
    def _detail_url(row_html: str, source_base_url: str) -> str:
        match = re.search(r'gotoURL\("([^"]+)"\)', row_html)
        if match is None:
            return source_base_url
        return urljoin(source_base_url, match.group(1))

    @classmethod
    def parse_html(
        cls,
        html_text: str,
        company: OligarchCompany,
        *,
        today: dt.date,
        lookahead_days: int,
        source_base_url: str,
    ) -> list[EarningsCalendarEvent]:
        from lxml import html

        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        tree = html.fromstring(html_text or "")
        events: list[EarningsCalendarEvent] = []
        for row in tree.xpath("//tr"):
            cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td|./th")]
            if len(cells) < 3:
                continue
            announcement_day = _date_from_compact_text(cells[0])
            if announcement_day is None:
                continue
            subject = cells[2]
            if not _text_has_any(subject, _MOPS_EARNINGS_KEYWORDS):
                continue
            report_day = _date_from_english_text(subject) or announcement_day
            if not (today <= report_day <= end):
                continue
            row_html = html.tostring(row, encoding="unicode")
            detail_url = cls._detail_url(row_html, source_base_url)
            is_conference = "conference" in subject.casefold()
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=company.ticker,
                    sector=company.sector,
                    report_date=report_day.isoformat(),
                    fiscal_period="",
                    time_label="\u5f85\u786e\u8ba4",
                    beijing_time=(
                        _beijing_time_from_local(announcement_day, cells[1], utc_offset_hours=8)
                        if report_day == announcement_day and len(cells) > 1
                        else ""
                    ),
                    status=CONFIRMED_STATUS,
                    source=MOPS_SOURCE,
                    priority=company.priority,
                    conference_url=detail_url if is_conference else "",
                    market=company.market,
                    original_call_time_text=f"{cells[0]} {cells[1] if len(cells) > 1 else ''} {subject}".strip(),
                    original_timezone="Asia/Shanghai",
                    call_time_source_url=detail_url,
                    call_time_source_type="mops_material_information",
                )
            )
        return sorted_events(events)


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
        target_tickers = [ticker for ticker in tw_universe if ticker in self.local_adr_tickers or ticker in self.ticker_ciks]
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
            response = self.session.get(
                self.submissions_url_template.format(cik=str(cik).zfill(10)),
                headers=self._headers(),
                timeout=self.timeout,
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
        response = self.session.get(
            self.company_tickers_url,
            headers={**self._headers(), "Host": "www.sec.gov"},
            timeout=self.timeout,
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
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession.replace('-', '')}/{primary_doc}"
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
                response = self.session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=self.timeout)
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
        original_call_time_text = str(
            rule.get("original_call_time_text", "") or rule.get("label", "") or ""
        ).strip()
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
        companies = [
            company
            for company in universe.values()
            if self.include_us or company.market != "US"
        ]
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
    def _fetch_one(ticker_factory, company: OligarchCompany, today: dt.date, lookahead_days: int) -> list[EarningsCalendarEvent]:
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


def _ensure_ascii_ca_bundle() -> None:
    try:
        import certifi
    except ImportError:
        return
    ca_path = certifi.where()
    try:
        ca_path.encode("ascii")
        return
    except UnicodeEncodeError:
        pass

    target_dir = Path(tempfile.gettempdir()) / "codex_certifi"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cacert.pem"
    try:
        if not target.is_file() or target.stat().st_size != Path(ca_path).stat().st_size:
            shutil.copyfile(ca_path, target)
    except OSError as exc:
        log.debug(f"[global earnings calendar] unable to prepare ascii CA bundle: {exc}")
        return
    os.environ.setdefault("CURL_CA_BUNDLE", str(target))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(target))


class GlobalEarningsCalendarService:
    def __init__(
        self,
        *,
        data_store=None,
        provider: AlphaVantageEarningsCalendarProvider | None = None,
        api_key: str | None = None,
        universe: Mapping[str, OligarchCompany] | None = None,
        confirmed_provider: ConfirmedEarningsEventsProvider | None = None,
        nasdaq_provider: NasdaqEarningsCalendarProvider | None = None,
        yfinance_provider: YFinanceEarningsCalendarProvider | None = None,
        jpx_provider: JpxFinancialAnnouncementProvider | None = None,
        tdnet_provider: TdnetEarningsDisclosureProvider | None = None,
        dart_provider: DartEarningsDisclosureProvider | None = None,
        kind_provider: KindEarningsDisclosureProvider | None = None,
        mops_provider: MopsEarningsDisclosureProvider | None = None,
        sec_provider: SecSixKEarningsProvider | None = None,
        company_ir_provider: CompanyIrEarningsCalendarProvider | None = None,
        official_providers: list[tuple[str, object]] | None = None,
    ):
        self._data_store = data_store
        self.universe = dict(universe or build_oligarch_universe())
        resolved_api_key = (
            api_key
            if api_key is not None
            else os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY") or ""
        )
        self.confirmed_provider = confirmed_provider or ConfirmedEarningsEventsProvider()
        self.nasdaq_provider = nasdaq_provider or NasdaqEarningsCalendarProvider()
        self.provider = provider or AlphaVantageEarningsCalendarProvider(resolved_api_key)
        self.yfinance_provider = yfinance_provider or YFinanceEarningsCalendarProvider()
        if official_providers is None:
            self.company_ir_provider = company_ir_provider or CompanyIrEarningsCalendarProvider()
            self.jpx_provider = jpx_provider or JpxFinancialAnnouncementProvider()
            self.tdnet_provider = tdnet_provider or TdnetEarningsDisclosureProvider()
            self.dart_provider = dart_provider or DartEarningsDisclosureProvider()
            self.kind_provider = kind_provider or KindEarningsDisclosureProvider()
            self.mops_provider = mops_provider or MopsEarningsDisclosureProvider()
            self.sec_provider = sec_provider or SecSixKEarningsProvider()
            self.official_providers = [
                (COMPANY_IR_SOURCE, self.company_ir_provider),
                (JPX_SOURCE, self.jpx_provider),
                (TDNET_SOURCE, self.tdnet_provider),
                (DART_SOURCE, self.dart_provider),
                (KIND_SOURCE, self.kind_provider),
                (MOPS_SOURCE, self.mops_provider),
                (SEC_6K_SOURCE, self.sec_provider),
            ]
        else:
            self.official_providers = list(official_providers or [])

    @property
    def data_store(self):
        if self._data_store is None:
            from core.data_store import data_store

            self._data_store = data_store
        return self._data_store

    def _load_cached_events(self) -> list[EarningsCalendarEvent]:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else None
        events = [event for event in (EarningsCalendarEvent.from_dict(row) for row in rows or []) if event is not None]
        return sorted_events(
            event
            for event in (self._hydrate_event_from_universe(event) for event in events)
            if event is not None
        )

    def _hydrate_event_from_universe(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent | None:
        ticker = event.ticker.strip().upper()
        company = self.universe.get(ticker)
        if company is None:
            return None
        status = _normalize_status_value(event.status, event.source)
        return EarningsCalendarEvent(
            company=company.company,
            ticker=ticker,
            sector=company.sector or event.sector,
            report_date=event.report_date,
            fiscal_period=event.fiscal_period,
            time_label=event.time_label,
            beijing_time=event.beijing_time,
            status=status,
            source=event.source,
            priority=company.priority or event.priority,
            conference_url=event.conference_url,
            market=company.market or event.market,
            original_call_time_text=event.original_call_time_text,
            original_timezone=event.original_timezone,
            call_time_source_url=event.call_time_source_url,
            call_time_source_type=event.call_time_source_type,
        )

    def _load_confirmed_events(self) -> list[EarningsCalendarEvent]:
        try:
            return self.confirmed_provider.fetch(self.universe)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[global earnings calendar] confirmed provider failed: {exc}")
            return []

    def _save_events(self, events: list[EarningsCalendarEvent], source: str) -> None:
        self.data_store.save_json(
            CACHE_KEY,
            {
                "source": source,
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "events": [event.to_dict() for event in sorted_events(events)],
            },
        )

    def sync_unverified_yfinance_cache(self) -> int:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            return 0

        changed = 0
        synced_rows = []
        for row in rows:
            if not isinstance(row, Mapping):
                synced_rows.append(row)
                continue
            row_dict = dict(row)
            source = str(row_dict.get("source", "") or "").strip()
            current_status = str(row_dict.get("status", "") or "").strip()
            normalized_status = _normalize_status_value(current_status, source)
            if source == YFINANCE_SOURCE and normalized_status != current_status:
                row_dict["status"] = normalized_status
                changed += 1
            synced_rows.append(row_dict)

        if changed:
            synced_payload = dict(payload) if isinstance(payload, Mapping) else {}
            synced_payload["events"] = synced_rows
            synced_payload["yfinance_estimate_synced_at"] = dt.datetime.now().isoformat(timespec="seconds")
            self.data_store.save_json(CACHE_KEY, synced_payload)
        return changed

    def upsert_confirmed_event(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent:
        confirmed = self._hydrate_event_from_universe(event)
        if confirmed is None:
            raise ConfirmedEventWriteError(f"unknown_ticker: {event.ticker}")
        confirmed = EarningsCalendarEvent(
            company=confirmed.company,
            ticker=confirmed.ticker,
            sector=confirmed.sector,
            report_date=confirmed.report_date,
            fiscal_period=confirmed.fiscal_period,
            time_label=confirmed.time_label,
            beijing_time=confirmed.beijing_time,
            status="confirmed",
            source="confirmed",
            priority=confirmed.priority,
            conference_url=confirmed.conference_url,
            market=confirmed.market,
            original_call_time_text=confirmed.original_call_time_text,
            original_timezone=confirmed.original_timezone,
            call_time_source_url=confirmed.call_time_source_url,
            call_time_source_type=confirmed.call_time_source_type,
        )
        self.confirmed_provider.upsert(confirmed)
        self._sync_cached_confirmed_event(confirmed)
        return confirmed

    def _sync_cached_confirmed_event(self, event: EarningsCalendarEvent) -> None:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else []
        cached_events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            cached_event = EarningsCalendarEvent.from_dict(row)
            if cached_event is None:
                continue
            hydrated = self._hydrate_event_from_universe(cached_event)
            if hydrated is not None:
                cached_events.append(hydrated)

        updated = False
        merged_events: list[EarningsCalendarEvent] = []
        for cached_event in cached_events:
            if _events_match_identity(cached_event, event):
                merged_events.append(event)
                updated = True
            else:
                merged_events.append(cached_event)
        if not updated:
            merged_events.append(event)

        source = "confirmed_writeback"
        if isinstance(payload, Mapping):
            source = str(payload.get("source") or source)
        self._save_events(merge_events(merged_events), source)

    @staticmethod
    def _filter_window(
        events: list[EarningsCalendarEvent],
        *,
        today: dt.date,
        lookahead_days: int,
    ) -> list[EarningsCalendarEvent]:
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        filtered = []
        for event in events:
            try:
                day = dt.date.fromisoformat(event_calendar_date(event)[:10])
            except ValueError:
                continue
            if today <= day <= end:
                filtered.append(event)
        return sorted_events(filtered)

    def load_events(
        self,
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        allow_network: bool = False,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        local = merge_events(
            self._filter_window(self._load_confirmed_events(), today=today, lookahead_days=lookahead_days)
            + self._filter_window(self._load_cached_events(), today=today, lookahead_days=lookahead_days)
        )
        if local:
            return local

        if allow_network:
            refreshed = self.refresh_events(today=today, lookahead_days=lookahead_days)
            if refreshed:
                return refreshed

        return []

    def refresh_events(
        self,
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        lookahead_days = max(0, int(lookahead_days))
        confirmed_events = self._filter_window(
            self._load_confirmed_events(),
            today=today,
            lookahead_days=lookahead_days,
        )
        cached_events = self._filter_window(
            self._load_cached_events(),
            today=today,
            lookahead_days=lookahead_days,
        )
        network_events: list[EarningsCalendarEvent] = []
        refreshed_sources: set[str] = set()

        provider_calls = (
            tuple(self.official_providers)
            + (
                ("Nasdaq", self.nasdaq_provider),
                ("Alpha Vantage", self.provider),
                ("Yahoo Finance", self.yfinance_provider),
            )
        )
        for provider_name, provider in provider_calls:
            try:
                provider_events = list(provider.fetch(self.universe, today=today, lookahead_days=lookahead_days) or [])
            except (ImportError, requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[global earnings calendar] {provider_name} refresh failed: {exc}")
                continue
            network_events.extend(provider_events)
            refreshed_sources.update(
                str(event.source or provider_name or "").strip()
                for event in provider_events
                if event is not None
            )

        network_events = self._filter_window(network_events, today=today, lookahead_days=lookahead_days)
        if network_events:
            cached_fallback_events = [
                event
                for event in cached_events
                if str(event.source or "").strip() not in refreshed_sources
            ]
            filtered = merge_events(confirmed_events + cached_fallback_events + network_events)
            self._save_events(filtered, "provider")
            return filtered

        return merge_events(confirmed_events + cached_events)
