# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import io
import os
import re
import zipfile
from typing import Mapping
from urllib.parse import urljoin

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_LOOKAHEAD_DAYS
from domains.global_earnings_calendar.event_ops import (
    DART_SOURCE,
    JPX_SOURCE,
    KIND_SOURCE,
    MOPS_SOURCE,
    TDNET_SOURCE,
    merge_events,
    sorted_events,
)
from domains.global_earnings_calendar.http_utils import raise_for_status as _raise_for_status
from domains.global_earnings_calendar.http_utils import response_text as _response_text
from domains.global_earnings_calendar.models import CONFIRMED_STATUS, EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.providers._utils import _ensure_ascii_ca_bundle
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
    local_code_from_ticker as _local_code_from_ticker,
)
from domains.global_earnings_calendar.rules import (
    text_has_any as _text_has_any,
)
from infra.http_safety import ensure_https_request, requests_get_https, requests_post_https

log = get_logger(__name__)

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

_JPX_ALLOWED_HOSTS = frozenset({"www.jpx.co.jp"})
_JPX_MAX_WORKBOOK_BYTES = 8 * 1024 * 1024
_JPX_MAX_WORKBOOK_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_JPX_MAX_WORKBOOK_FILES = 256
_JPX_MAX_WORKSHEETS = 12
_JPX_MAX_WORKSHEET_ROWS = 20_000


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

        response = requests_get_https(
            self.page_url,
            session=self.session,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
            allowed_hosts=_JPX_ALLOWED_HOSTS,
        )
        _raise_for_status(response)
        workbook_links = self._parse_workbook_links(_response_text(response, encoding="utf-8"), self.page_url)
        events: list[EarningsCalendarEvent] = []
        for workbook_url in workbook_links:
            workbook_response = requests_get_https(
                workbook_url,
                session=self.session,
                headers={"User-Agent": "Mozilla/5.0", "Referer": self.page_url},
                timeout=self.timeout,
                allowed_hosts=_JPX_ALLOWED_HOSTS,
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
            try:
                ensure_https_request(full_url, allowed_hosts=_JPX_ALLOWED_HOSTS)
            except ValueError:
                continue
            if full_url not in links:
                links.append(full_url)
        return links

    @staticmethod
    def _validate_workbook_bytes(workbook_bytes: bytes) -> None:
        if len(workbook_bytes) > _JPX_MAX_WORKBOOK_BYTES:
            raise ValueError(f"JPX workbook is too large: bytes={len(workbook_bytes)}")
        try:
            with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
                members = archive.infolist()
                if len(members) > _JPX_MAX_WORKBOOK_FILES:
                    raise ValueError(f"JPX workbook has too many files: files={len(members)}")
                total_size = sum(max(0, item.file_size) for item in members)
                if total_size > _JPX_MAX_WORKBOOK_UNCOMPRESSED_BYTES:
                    raise ValueError(f"JPX workbook expands too large: bytes={total_size}")
        except zipfile.BadZipFile:
            return

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
        cls._validate_workbook_bytes(workbook_bytes)
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True, data_only=True)
        events: list[EarningsCalendarEvent] = []
        try:
            for worksheet_index, worksheet in enumerate(workbook.worksheets, start=1):
                if worksheet_index > _JPX_MAX_WORKSHEETS:
                    raise ValueError(f"JPX workbook has too many worksheets: sheets={worksheet_index}")
                header_indexes: tuple[int, int, int | None, int | None] | None = None
                for row_index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                    if row_index > _JPX_MAX_WORKSHEET_ROWS:
                        raise ValueError(f"JPX worksheet has too many rows: rows={row_index}")
                    values = list(row or [])
                    if header_indexes is None:
                        headers = [str(value or "") for value in values]
                        date_idx = cls._header_index(
                            headers, "Scheduled Dates", "\u6c7a\u7b97\u767a\u8868\u4e88\u5b9a\u65e5"
                        )
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
        finally:
            workbook.close()
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
            event for event in events if (day := _date_from_any(event.report_date)) is not None and today <= day <= end
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
        jp_codes = {
            _local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "JP"
        }
        if not jp_codes:
            return []
        forward_days = min(max(0, int(lookahead_days)), self.max_forward_days)
        events: list[EarningsCalendarEvent] = []
        for offset in range(forward_days + 1):
            day = today + dt.timedelta(days=offset)
            url = self.base_url_template.format(date=day.strftime("%Y%m%d"))
            response = requests_get_https(
                url,
                session=self.session,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.release.tdnet.info/inbs/"},
                timeout=self.timeout,
            )
            if int(getattr(response, "status_code", 200) or 200) == 404:
                continue
            _raise_for_status(response)
            events.extend(
                self.parse_html(_response_text(response, encoding="utf-8"), day, universe, jp_codes, source_url=url)
            )
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
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get("OPENDART_API_KEY") or os.environ.get("DART_API_KEY") or ""
        ).strip()
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
        kr_codes = {
            _local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "KR"
        }
        if not kr_codes:
            return []
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        events: list[EarningsCalendarEvent] = []
        for page_no in range(1, self.max_pages + 1):
            response = requests_get_https(
                self.base_url,
                session=self.session,
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
            except TypeError, ValueError, AttributeError:
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
            source_url = (
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
                if receipt_no
                else "https://dart.fss.or.kr/"
            )
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
        kr_codes = {
            _local_code_from_ticker(ticker): ticker for ticker, company in universe.items() if company.market == "KR"
        }
        if not kr_codes:
            return []
        forward_days = min(max(0, int(lookahead_days)), self.max_forward_days)
        events: list[EarningsCalendarEvent] = []
        for offset in range(forward_days + 1):
            day = today + dt.timedelta(days=offset)
            response = requests_post_https(
                self.base_url,
                session=self.session,
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
            return requests_get_https(url, session=self.session, impersonate="chrome", **kwargs)
        except TypeError:
            return requests_get_https(url, session=self.session, **kwargs)

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
            try:
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
            except (requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[global earnings calendar] MOPS stop after {company.ticker}: {exc}")
                break
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
