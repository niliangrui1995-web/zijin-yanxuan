from __future__ import annotations

import datetime as dt
import io

import openpyxl
import pytest

import domains.global_earnings_calendar.providers.asia_disclosures as asia_module
from domains.global_earnings_calendar.models import OligarchCompany
from domains.global_earnings_calendar.providers.asia_disclosures import (
    DartEarningsDisclosureProvider,
    JpxFinancialAnnouncementProvider,
    KindEarningsDisclosureProvider,
    MopsEarningsDisclosureProvider,
    TdnetEarningsDisclosureProvider,
)


class _Response:
    def __init__(self, *, text="", content=b"", status_code=200, payload=None):
        self.text = text
        self.content = content
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)


def _company(name, ticker, market):
    return OligarchCompany(name, ticker, "sector", "normal", market)


def _jpx_workbook_bytes():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Scheduled Dates", "Code", "Fiscal Year/Quarter", "Fiscal Year-end"])
    sheet.append([dt.date(2026, 5, 15), "8035", "Q1", dt.date(2026, 3, 31)])
    sheet.append([dt.date(2026, 5, 20), "9999", "Q1", dt.date(2026, 3, 31)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_jpx_fetch_discovers_workbook_links_and_filters_window():
    universe = {
        "8035.T": _company("Tokyo Electron", "8035.T", "JP"),
        "NVDA": _company("NVIDIA", "NVDA", "US"),
    }
    session = _Session(
        get_responses=[
            _Response(text='<a href="/files/a.xlsx">A</a><a href="/files/a.xlsx">dup</a>'),
            _Response(content=_jpx_workbook_bytes()),
        ]
    )
    provider = JpxFinancialAnnouncementProvider(session=session, page_url="https://www.jpx.co.jp/listing/index.html")

    assert provider.fetch({"NVDA": universe["NVDA"]}) == []
    events = provider.fetch(universe, today=dt.date(2026, 5, 1), lookahead_days=20)

    assert [event.ticker for event in events] == ["8035.T"]
    assert events[0].fiscal_period == "Q1 / 2026-03-31"
    assert session.get_calls[1][0] == "https://www.jpx.co.jp/files/a.xlsx"
    assert JpxFinancialAnnouncementProvider.parse_workbook(b"", universe) == []


def test_jpx_workbook_links_reject_off_origin_and_unsafe_targets():
    html = """
    <a href="/files/a.xlsx">A</a>
    <a href="https://evil.example/a.xlsx">external</a>
    <a href="http://www.jpx.co.jp/insecure.xlsx">insecure</a>
    <a href="//127.0.0.1/private.xlsx">private</a>
    """

    links = JpxFinancialAnnouncementProvider._parse_workbook_links(
        html,
        "https://www.jpx.co.jp/listing/index.html",
    )

    assert links == ["https://www.jpx.co.jp/files/a.xlsx"]


def test_jpx_parse_workbook_rejects_oversized_workbook(monkeypatch):
    universe = {"8035.T": _company("Tokyo Electron", "8035.T", "JP")}
    monkeypatch.setattr(asia_module, "_JPX_MAX_WORKBOOK_BYTES", 1)

    with pytest.raises(ValueError, match="too large"):
        JpxFinancialAnnouncementProvider.parse_workbook(_jpx_workbook_bytes(), universe)


def test_jpx_parse_workbook_rejects_excessive_rows(monkeypatch):
    universe = {"8035.T": _company("Tokyo Electron", "8035.T", "JP")}
    monkeypatch.setattr(asia_module, "_JPX_MAX_WORKSHEET_ROWS", 1)

    with pytest.raises(ValueError, match="too many rows"):
        JpxFinancialAnnouncementProvider.parse_workbook(_jpx_workbook_bytes(), universe)


def test_tdnet_fetch_skips_404_and_parse_html_filters_rows():
    universe = {"8035.T": _company("Tokyo Electron", "8035.T", "JP")}
    html = f"""
    <table>
      <tr><td>short</td></tr>
      <tr><td>15:00</td><td>1</td><td>x</td><td>{asia_module._JP_EARNINGS_KEYWORDS[0]}</td></tr>
      <tr><td>15:01</td><td>9999</td><td>x</td><td>{asia_module._JP_EARNINGS_KEYWORDS[0]}</td></tr>
      <tr><td>15:02</td><td>8035</td><td>x</td><td>Other notice</td></tr>
      <tr><td>15:03</td><td>8035</td><td>x</td><td>{asia_module._JP_EARNINGS_KEYWORDS[0]}</td></tr>
    </table>
    """
    session = _Session(get_responses=[_Response(status_code=404), _Response(text=html)])
    provider = TdnetEarningsDisclosureProvider(
        session=session,
        base_url_template="https://tdnet/{date}.html",
        max_forward_days=1,
    )

    assert provider.fetch({"NVDA": _company("NVIDIA", "NVDA", "US")}, today=dt.date(2026, 5, 1)) == []
    events = provider.fetch(universe, today=dt.date(2026, 5, 1), lookahead_days=10)

    assert len(events) == 1
    assert events[0].ticker == "8035.T"
    assert events[0].call_time_source_url == "https://tdnet/20260502.html"


def test_dart_fetch_handles_key_market_and_total_page_edges():
    kr_company = _company("Hanmi", "042700.KS", "KR")
    payload = {
        "total_page": ["bad"],
        "list": [
            "bad row",
            {"stock_code": "000000", "report_nm": asia_module._KR_EARNINGS_KEYWORDS[0], "rcept_dt": "20260506"},
            {"stock_code": "042700", "report_nm": "other", "rcept_dt": "20260506"},
            {"stock_code": "042700", "report_nm": asia_module._KR_EARNINGS_KEYWORDS[0], "rcept_dt": "bad"},
            {
                "stock_code": "042700",
                "report_nm": asia_module._KR_EARNINGS_KEYWORDS[0],
                "rcept_dt": "20260506",
                "rcept_no": "",
            },
        ],
    }
    session = _Session(get_responses=[_Response(payload=payload)])

    assert DartEarningsDisclosureProvider(api_key="").fetch({"042700.KS": kr_company}) == []
    assert DartEarningsDisclosureProvider(api_key="key").fetch({"NVDA": _company("NVIDIA", "NVDA", "US")}) == []

    events = DartEarningsDisclosureProvider(api_key="key", session=session, max_pages=2).fetch(
        {"042700.KS": kr_company},
        today=dt.date(2026, 5, 1),
        lookahead_days=10,
    )

    assert len(events) == 1
    assert events[0].ticker == "042700.KS"
    assert events[0].call_time_source_url == "https://dart.fss.or.kr/"


def test_kind_fetch_and_parse_html_filter_edges():
    kr_company = _company("Hanmi", "042700.KS", "KR")
    html = f"""
    <table>
      <tr><td>short</td></tr>
      <tr><td>15:00</td><td>name</td><td>{asia_module._KR_EARNINGS_KEYWORDS[0]}</td></tr>
      <tr><td>15:01</td><td><a onclick="companysummary_open('99999')">x</a></td><td>{asia_module._KR_EARNINGS_KEYWORDS[0]}</td></tr>
      <tr><td>15:02</td><td><a onclick="companysummary_open('04270')">x</a></td><td>other</td></tr>
      <tr><td>15:03</td><td><a onclick="companysummary_open('04270')">x</a></td><td>{asia_module._KR_EARNINGS_KEYWORDS[0]}</td></tr>
    </table>
    """
    session = _Session(post_responses=[_Response(text=html)])
    provider = KindEarningsDisclosureProvider(session=session)

    assert provider.fetch({"NVDA": _company("NVIDIA", "NVDA", "US")}) == []
    assert KindEarningsDisclosureProvider._kind_code_matches("", "042700") is False
    assert KindEarningsDisclosureProvider._kind_code_matches("4270", "") is False
    assert KindEarningsDisclosureProvider._kind_code_matches("04270", "042700") is True

    events = provider.fetch({"042700.KS": kr_company}, today=dt.date(2026, 5, 6))

    assert len(events) == 1
    assert events[0].ticker == "042700.KS"
    assert events[0].call_time_source_url == "https://kind.krx.co.kr/"


def test_mops_session_get_fetch_and_parse_edges(monkeypatch):
    tw_company = _company("TSMC", "2330.TW", "TW")
    html = """
    <table>
      <tr><td>short</td></tr>
      <tr><td>bad-date</td><td>18:00</td><td>financial report</td></tr>
      <tr><td>2026/04/01</td><td>18:00</td><td>other notice</td></tr>
      <tr><td>2026/04/01</td><td>18:00</td><td>financial report on June 1, 2026</td></tr>
      <tr><td>2026/04/16</td><td>18:00</td><td>financial report</td></tr>
    </table>
    """
    session = _Session(get_responses=[_Response(text=html)])
    provider = MopsEarningsDisclosureProvider(session=session, base_url="https://mops/query")

    assert provider.fetch({"NVDA": _company("NVIDIA", "NVDA", "US")}) == []
    assert MopsEarningsDisclosureProvider._detail_url("<tr></tr>", "https://mops/base") == "https://mops/base"

    events = provider.fetch({"2330.TW": tw_company}, today=dt.date(2026, 4, 1), lookahead_days=30)

    assert len(events) == 1
    assert events[0].ticker == "2330.TW"
    assert events[0].conference_url == ""
    assert events[0].beijing_time == "2026-04-16 18:00"
    assert session.get_calls[0][1]["params"]["TYPEK"] == "sii"

    class _FallbackSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(kwargs)
            if "impersonate" in kwargs:
                raise TypeError("unsupported")
            return _Response(text="")

    fallback_session = _FallbackSession()
    assert MopsEarningsDisclosureProvider(session=fallback_session)._get("https://mops").text == ""
    assert "impersonate" in fallback_session.calls[0]
    assert "impersonate" not in fallback_session.calls[1]

    monkeypatch.setattr(asia_module, "_ensure_ascii_ca_bundle", lambda: None)
    monkeypatch.setattr(
        "builtins.__import__",
        lambda name, *args, **kwargs: (_ for _ in ()).throw(ImportError(name))
        if name == "curl_cffi.requests"
        else __import__(name, *args, **kwargs),
    )

    assert MopsEarningsDisclosureProvider._default_session() is asia_module.requests
