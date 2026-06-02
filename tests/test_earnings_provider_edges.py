from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import yfinance as yf

from domains.global_earnings_calendar.models import OligarchCompany
from domains.global_earnings_calendar.providers.company_ir import CompanyIrEarningsCalendarProvider
from domains.global_earnings_calendar.providers.sec import SecSixKEarningsProvider
from domains.global_earnings_calendar.providers.yfinance import YFinanceEarningsCalendarProvider


class _JsonResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
        self.encoding = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_sec_provider_resolves_adr_cik_and_fetches_6k_event(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test-sec-agent")
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "company_tickers" in url:
            return _JsonResponse({"0": {"ticker": "TSM", "cik_str": 1234}})
        return _JsonResponse(
            {
                "filings": {
                    "recent": {
                        "form": ["6-K", "8-K"],
                        "filingDate": ["2026-05-10", "2026-05-11"],
                        "accessionNumber": ["0001234567-26-000001"],
                        "primaryDocument": ["earnings.htm"],
                        "primaryDocDescription": ["Quarterly financial results"],
                    }
                }
            }
        )

    provider = SecSixKEarningsProvider(session=SimpleNamespace(get=fake_get), local_adr_tickers={"2330.TW": "TSM"})
    universe = {"2330.TW": OligarchCompany("TSMC", "2330.TW", "Foundry", "super", "TW")}

    events = provider.fetch(universe, today=dt.date(2026, 5, 1), lookahead_days=20)

    assert len(events) == 1
    assert events[0].ticker == "2330.TW"
    assert events[0].report_date == "2026-05-10"
    assert events[0].status == "confirmed"
    assert "Archives/edgar/data/1234" in events[0].conference_url
    assert len(calls) == 2
    assert calls[0][0] == "https://www.sec.gov/files/company_tickers.json"
    assert calls[0][1]["headers"]["Host"] == "www.sec.gov"
    assert calls[0][1]["headers"]["User-Agent"] == "test-sec-agent"
    assert calls[0][1]["timeout"] == (5, 20)
    assert calls[1][0] == "https://data.sec.gov/submissions/CIK0000001234.json"
    assert calls[1][1]["headers"]["Host"] == "data.sec.gov"
    assert calls[1][1]["headers"]["User-Agent"] == "test-sec-agent"
    assert calls[1][1]["timeout"] == (5, 20)


def test_sec_parse_submissions_filters_payload_shape_dates_and_keywords():
    company = OligarchCompany("TSMC", "2330.TW", "Foundry", "super", "TW")

    assert SecSixKEarningsProvider.parse_submissions([], company, cik="1", today=dt.date(2026, 5, 1), lookahead_days=5) == []
    assert (
        SecSixKEarningsProvider.parse_submissions(
            {"filings": {"recent": {"form": ["6-K"], "filingDate": ["2026-06-01"], "primaryDocument": ["x.htm"]}}},
            company,
            cik="1",
            today=dt.date(2026, 5, 1),
            lookahead_days=5,
        )
        == []
    )
    assert (
        SecSixKEarningsProvider.parse_submissions(
            {
                "filings": {
                    "recent": {
                        "form": ["6-K"],
                        "filingDate": ["2026-05-02"],
                        "primaryDocument": ["notice.htm"],
                        "primaryDocDescription": ["Annual result announcement"],
                    }
                }
            },
            company,
            cik="1",
            today=dt.date(2026, 5, 1),
            lookahead_days=5,
        )[0].call_time_source_type
        == "sec_6k"
    )


def test_company_ir_provider_loads_rules_file_and_fetches_matching_event(tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "sources": {
                    "ALP": [
                        {
                            "url": "https://example.test/ir",
                            "include_keywords": ["results"],
                            "fiscal_period": "Q1",
                            "time_label": "盘后",
                            "encoding": "utf-8",
                        }
                    ],
                    "BAD": {"url": "ignored"},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _JsonResponse(text="<html>Q1 results May 4, 2026</html>")

    session = SimpleNamespace(get=fake_get)
    provider = CompanyIrEarningsCalendarProvider(session=session, rules_path=rules_path)
    universe = {"ALP": OligarchCompany("Alpha", "ALP", "AI", "normal", "US")}

    events = provider.fetch(universe, today=dt.date(2026, 5, 1), lookahead_days=10)

    assert len(events) == 1
    assert events[0].ticker == "ALP"
    assert events[0].report_date == "2026-05-04"
    assert events[0].fiscal_period == "Q1"
    assert events[0].source
    assert calls[0][0] == "https://example.test/ir"
    assert calls[0][1]["headers"]["User-Agent"] == "Mozilla/5.0"
    assert calls[0][1]["timeout"] == (5, 20)


def test_company_ir_parse_page_uses_explicit_dates_and_filters_keywords():
    company = OligarchCompany("Alpha", "ALP", "AI", "normal", "US")

    assert (
        CompanyIrEarningsCalendarProvider.parse_page(
            "<html>unrelated</html>",
            company,
            {"url": "https://example.test/ir", "include_keywords": ["results"]},
            today=dt.date(2026, 5, 1),
            lookahead_days=10,
        )
        == []
    )
    event = CompanyIrEarningsCalendarProvider.parse_page(
        "<html>anything</html>",
        company,
        {
            "url": "https://example.test/ir",
            "report_date": "2026-05-06",
            "conference_url": "https://example.test/call",
            "source_type": "official",
        },
        today=dt.date(2026, 5, 1),
        lookahead_days=10,
    )[0]

    assert event.report_date == "2026-05-06"
    assert event.conference_url == "https://example.test/call"
    assert event.call_time_source_type == "official"


def test_yfinance_provider_fetch_one_accepts_calendar_method_and_get_calendar():
    company = OligarchCompany("Tokyo Electron", "8035.T", "Semi", "normal", "JP")
    today = dt.date(2026, 5, 1)

    direct = YFinanceEarningsCalendarProvider._fetch_one(
        lambda ticker: SimpleNamespace(calendar=lambda: {"Earnings Date": [dt.date(2026, 5, 3)]}),
        company,
        today,
        10,
    )
    fallback = YFinanceEarningsCalendarProvider._fetch_one(
        lambda ticker: SimpleNamespace(calendar=None, get_calendar=lambda: {"Earnings Dates": dt.date(2026, 5, 4)}),
        company,
        today,
        10,
    )

    assert [event.report_date for event in direct + fallback] == ["2026-05-03", "2026-05-04"]


def test_company_ir_provider_rule_loading_and_fetch_edges(tmp_path):
    company = OligarchCompany("Alpha", "ALP", "AI", "normal", "US")

    configured = CompanyIrEarningsCalendarProvider(rules={"alp": [{"url": "https://example.test"}]})
    assert configured._load_rules() == {"ALP": [{"url": "https://example.test"}]}

    assert CompanyIrEarningsCalendarProvider(rules_path=tmp_path / "missing.json").fetch({"ALP": company}) == []

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    assert CompanyIrEarningsCalendarProvider(rules_path=bad_json)._load_rules() == {}

    list_json = tmp_path / "list.json"
    list_json.write_text(json.dumps(["bad"]), encoding="utf-8")
    assert CompanyIrEarningsCalendarProvider(rules_path=list_json)._load_rules() == {}

    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps({"sources": {"MISS": [{"url": "https://example.test"}], "ALP": ["bad", {}]}}),
        encoding="utf-8",
    )
    assert CompanyIrEarningsCalendarProvider(rules_path=rules_path).fetch({"ALP": company}) == []


def test_company_ir_parse_page_rejects_missing_or_out_of_window_dates():
    company = OligarchCompany("Alpha", "ALP", "AI", "normal", "US")

    assert (
        CompanyIrEarningsCalendarProvider.parse_page(
            "<html>Quarterly results without a date</html>",
            company,
            {"url": "https://example.test/ir", "include_keywords": ["Quarterly results"]},
            today=dt.date(2026, 5, 1),
            lookahead_days=10,
        )
        == []
    )
    assert (
        CompanyIrEarningsCalendarProvider.parse_page(
            "<html>Quarterly results May 30, 2026</html>",
            company,
            {"url": "https://example.test/ir", "include_keywords": ["Quarterly results"]},
            today=dt.date(2026, 5, 1),
            lookahead_days=10,
        )
        == []
    )


def test_yfinance_provider_fetch_and_factory_error_edges(monkeypatch):
    us_only = {"AMD": OligarchCompany("AMD", "AMD", "AI", "normal", "US")}
    assert YFinanceEarningsCalendarProvider(include_us=False).fetch(us_only, today=dt.date(2026, 5, 1)) == []

    non_us = {"8035.T": OligarchCompany("Tokyo Electron", "8035.T", "Semi", "normal", "JP")}
    provider = YFinanceEarningsCalendarProvider(ticker_factory=lambda ticker: (_ for _ in ()).throw(RuntimeError("bad ticker")))
    assert provider.fetch(non_us, today=dt.date(2026, 5, 1), lookahead_days=10) == []

    calls = []
    monkeypatch.setattr("vcp.fetchers.yf_session.build_yf_session", lambda: (_ for _ in ()).throw(RuntimeError("session")))
    monkeypatch.setattr("domains.global_earnings_calendar.providers.yfinance._ensure_ascii_ca_bundle", lambda: calls.append("bundle"))
    monkeypatch.setattr(yf, "Ticker", lambda ticker: ("ticker", ticker))

    factory = YFinanceEarningsCalendarProvider._load_yfinance_ticker_factory()

    assert factory("8035.T") == ("ticker", "8035.T")
    assert calls == ["bundle"]


def test_yfinance_fetch_one_rejects_bad_calendar_shapes_and_out_of_window_dates():
    company = OligarchCompany("Tokyo Electron", "8035.T", "Semi", "normal", "JP")

    assert (
        YFinanceEarningsCalendarProvider._fetch_one(
            lambda ticker: SimpleNamespace(calendar=None, get_calendar=lambda: []),
            company,
            dt.date(2026, 5, 1),
            10,
        )
        == []
    )
    assert (
        YFinanceEarningsCalendarProvider._fetch_one(
            lambda ticker: SimpleNamespace(calendar={"Earnings Date": [dt.date(2026, 6, 1), "bad"]}),
            company,
            dt.date(2026, 5, 1),
            10,
        )
        == []
    )
