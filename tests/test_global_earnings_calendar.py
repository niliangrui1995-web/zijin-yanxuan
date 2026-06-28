# -*- coding: utf-8 -*-
import datetime as dt
import io
import json
from types import SimpleNamespace

import yfinance as yf

from domains.global_earnings_calendar.service import (
    AlphaVantageEarningsCalendarProvider,
    CompanyIrEarningsCalendarProvider,
    ConfirmedEarningsEventsProvider,
    ConfirmedEventWriteError,
    DartEarningsDisclosureProvider,
    EarningsCalendarEvent,
    GlobalEarningsCalendarService,
    JpxFinancialAnnouncementProvider,
    KindEarningsDisclosureProvider,
    MopsEarningsDisclosureProvider,
    NasdaqEarningsCalendarProvider,
    OligarchCompany,
    SecSixKEarningsProvider,
    TdnetEarningsDisclosureProvider,
    YFinanceEarningsCalendarProvider,
    build_demo_events,
    build_oligarch_universe,
    events_by_date,
    is_yfinance_date_conflict_event,
    is_yfinance_estimate_event,
    sorted_events,
)
from vcp.fetchers import yf_session


def test_build_oligarch_universe_maps_sector_and_priority():
    module = SimpleNamespace(
        OLIGARCH_DICT={
            "AI加速芯片与定制ASIC": ["NVIDIA", "Broadcom (博通)"],
            "先进制程代工": ["TSMC (台积电)"],
        },
        VANGUARD_TICKERS={
            "NVIDIA": "NVDA",
            "Broadcom": "AVGO",
            "TSMC": "TSM",
        },
        SUPER_GIANTS={"NVIDIA", "TSMC"},
        STRATEGIC_GIANTS={"Broadcom"},
    )

    universe = build_oligarch_universe(module)

    assert universe["NVDA"].company == "NVIDIA"
    assert universe["NVDA"].sector == "AI加速芯片与定制ASIC"
    assert universe["NVDA"].priority == "super_giant"
    assert universe["AVGO"].sector == "AI加速芯片与定制ASIC"
    assert universe["AVGO"].priority == "strategic_giant"
    assert universe["TSM"].sector == "先进制程代工"


def test_build_oligarch_universe_classifies_cross_market_suffixes():
    module = SimpleNamespace(
        OLIGARCH_DICT={"global": ["Lumentum", "Alchip", "SK Hynix", "Tokyo Electron"]},
        VANGUARD_TICKERS={
            "Lumentum": "LITE",
            "Alchip": "3661.TW",
            "SK Hynix": "000660.KS",
            "Tokyo Electron": "8035.T",
        },
        SUPER_GIANTS=set(),
    )

    universe = build_oligarch_universe(module)

    assert universe["LITE"].market == "US"
    assert universe["3661.TW"].market == "TW"
    assert universe["000660.KS"].market == "KR"
    assert universe["8035.T"].market == "JP"


def test_alpha_vantage_provider_filters_universe_and_sorts():
    csv_text = (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
        "AMAT,Applied Materials,2026-05-13,2026-04-30,2.12,USD\n"
        "XYZ,Other Co,2026-05-08,2026-03-31,1.00,USD\n"
        "NVDA,NVIDIA,2026-05-07,2026-04-30,5.50,USD\n"
    )
    provider = AlphaVantageEarningsCalendarProvider(api_key="demo")
    universe = {
        "NVDA": SimpleNamespace(company="NVIDIA", ticker="NVDA", sector="AI加速芯片与定制ASIC", priority="super_giant"),
        "AMAT": SimpleNamespace(
            company="Applied Materials", ticker="AMAT", sector="前道晶圆设备与量测", priority="normal"
        ),
    }

    events = provider.parse_csv(csv_text, universe)

    assert [event.ticker for event in events] == ["NVDA", "AMAT"]
    assert events[0].report_date == "2026-05-07"
    assert events[0].fiscal_period == "2026-04-30"
    assert events[0].status == "estimated"
    assert events[0].source == "Alpha Vantage"


def test_nasdaq_provider_parses_lite_after_hours_calendar_row():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "rows": [
                        {
                            "time": "time-after-hours",
                            "symbol": "LITE",
                            "name": "Lumentum Holdings Inc.",
                            "fiscalQuarterEnding": "Mar/2026",
                        },
                        {
                            "time": "time-after-hours",
                            "symbol": "AMD",
                            "name": "Advanced Micro Devices, Inc.",
                            "fiscalQuarterEnding": "Mar/2026",
                        },
                    ]
                }
            }

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return FakeResponse()

    session = FakeSession()
    provider = NasdaqEarningsCalendarProvider(session=session)
    universe = {
        "LITE": OligarchCompany("Lumentum", "LITE", "光芯片与硅光", "normal", "US"),
    }

    events = provider.fetch(universe, today=dt.date(2026, 5, 5), lookahead_days=0)

    assert len(events) == 1
    assert events[0].ticker == "LITE"
    assert events[0].report_date == "2026-05-05"
    assert events[0].time_label == "盘后"
    assert events[0].source == "Nasdaq"
    assert session.calls[0][0] == ("https://api.nasdaq.com/api/calendar/earnings",)
    assert session.calls[0][1]["params"] == {"date": "2026-05-05"}
    assert session.calls[0][1]["headers"]["User-Agent"] == "Mozilla/5.0"
    assert session.calls[0][1]["timeout"] == (5, 20)


def test_jpx_provider_parses_financial_announcement_workbook():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "List"
    sheet.append(["title"])
    sheet.append(
        [
            "決算発表予定日\nScheduled Dates for Earnings Announcements",
            "コード\nCode",
            "会社名",
            "Issue Name",
            "決算期末\nFiscal Year-end",
            "業種名",
            "Industry",
            "種別",
            "Fiscal Year/Quarter",
        ]
    )
    sheet.append(
        [
            dt.datetime(2026, 5, 15),
            8035,
            "東京エレクトロン",
            "Tokyo Electron",
            dt.datetime(2026, 3, 31),
            "",
            "",
            "本決算",
            "Fiscal year",
        ]
    )
    sheet.append(
        [dt.datetime(2026, 5, 16), 9999, "Other", "Other", dt.datetime(2026, 3, 31), "", "", "本決算", "Fiscal year"]
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    universe = {
        "8035.T": OligarchCompany("Tokyo Electron", "8035.T", "前道晶圆设备与量测", "normal", "JP"),
    }

    events = JpxFinancialAnnouncementProvider.parse_workbook(
        buffer.getvalue(),
        universe,
        allowed_symbols={"8035.T"},
        source_url="https://www.jpx.co.jp/kessan.xlsx",
    )

    assert len(events) == 1
    assert events[0].ticker == "8035.T"
    assert events[0].report_date == "2026-05-15"
    assert events[0].status == "confirmed"
    assert events[0].source == "JPX"
    assert events[0].call_time_source_type == "jpx_financial_announcement_schedule"


def test_tdnet_provider_parses_official_earnings_disclosure_html():
    html = """
    <table>
      <tr><td>15:30</td><td>80350</td><td>東京エレクトロン</td><td><a href="140120260506000001.pdf">決算短信</a></td></tr>
      <tr><td>15:31</td><td>99990</td><td>Other</td><td><a href="x.pdf">決算短信</a></td></tr>
    </table>
    """
    universe = {
        "8035.T": OligarchCompany("Tokyo Electron", "8035.T", "前道晶圆设备与量测", "normal", "JP"),
    }

    events = TdnetEarningsDisclosureProvider.parse_html(
        html,
        dt.date(2026, 5, 6),
        universe,
        {"8035": "8035.T"},
        source_url="https://www.release.tdnet.info/inbs/I_list_001_20260506.html",
    )

    assert len(events) == 1
    assert events[0].ticker == "8035.T"
    assert events[0].beijing_time == "2026-05-06 14:30"
    assert events[0].source == "TDnet"
    assert events[0].call_time_source_url.endswith("140120260506000001.pdf")


def test_dart_provider_parses_official_earnings_disclosure_payload():
    payload = {
        "status": "000",
        "list": [
            {
                "stock_code": "042700",
                "corp_name": "한미반도체",
                "report_nm": "\uc601\uc5c5(\uc7a0\uc815)\uc2e4\uc801",
                "rcept_dt": "20260506",
                "rcept_no": "20260506802414",
            }
        ],
    }
    universe = {
        "042700.KS": OligarchCompany("Hanmi Semi", "042700.KS", "Packaging equipment", "normal", "KR"),
    }

    events = DartEarningsDisclosureProvider.parse_payload(payload, universe, {"042700": "042700.KS"})

    assert len(events) == 1
    assert events[0].ticker == "042700.KS"
    assert events[0].report_date == "2026-05-06"
    assert events[0].source == "DART"
    assert events[0].call_time_source_url.endswith("rcpNo=20260506802414")


def test_kind_provider_parses_official_earnings_disclosure_html():
    html = """
    <table>
      <tr>
        <td>15:00</td>
        <td><a onclick="companysummary_open('04270'); return false;">한미반도체</a></td>
        <td><a onclick="openDisclsViewer('20260506001149','')">\uc601\uc5c5(\uc7a0\uc815)\uc2e4\uc801</a></td>
        <td>한미반도체</td>
      </tr>
    </table>
    """
    universe = {
        "042700.KS": OligarchCompany("Hanmi Semi", "042700.KS", "Packaging equipment", "normal", "KR"),
    }

    events = KindEarningsDisclosureProvider.parse_html(
        html,
        dt.date(2026, 5, 6),
        universe,
        {"042700": "042700.KS"},
    )

    assert len(events) == 1
    assert events[0].ticker == "042700.KS"
    assert events[0].beijing_time == "2026-05-06 14:00"
    assert events[0].source == "KIND"
    assert events[0].call_time_source_type == "kind_disclosure"


def test_mops_provider_parses_earnings_conference_material_information():
    html = """
    <table width="90%">
      <tr><td>Provided by: Taiwan Semiconductor Manufacturing Co., Ltd.</td></tr>
      <tr>
        <td>2026/03/27</td>
        <td>18:09:32</td>
        <td>TSMC will hold the First Quarter 2026 Earnings Conference on April 16, 2026</td>
        <td><a href='javascript:gotoURL("/server-java/t05st01_e?step=1&co_id=2330&spoke_date=20260327&spoke_time=180932&seq_no=2");'>More&gt;&gt;</a></td>
      </tr>
    </table>
    """
    company = OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW")

    events = MopsEarningsDisclosureProvider.parse_html(
        html,
        company,
        today=dt.date(2026, 4, 1),
        lookahead_days=30,
        source_base_url="https://emops.twse.com.tw/server-java/t05st01_e",
    )

    assert len(events) == 1
    assert events[0].ticker == "2330.TW"
    assert events[0].report_date == "2026-04-16"
    assert events[0].source == "MOPS"
    assert "spoke_date=20260327" in events[0].conference_url


def test_mops_provider_stops_after_transport_failure_without_raising():
    class FailingSession:
        def __init__(self):
            self.calls = []

        def get(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise RuntimeError("connect timeout")

    session = FailingSession()
    provider = MopsEarningsDisclosureProvider(session=session)
    universe = {
        "2330.TW": OligarchCompany("TSMC", "2330.TW", "foundry", "super_giant", "TW"),
        "3008.TW": OligarchCompany("Largan", "3008.TW", "optics", "normal", "TW"),
    }

    events = provider.fetch(universe, today=dt.date(2026, 4, 1), lookahead_days=30)

    assert events == []
    assert len(session.calls) == 1


def test_sec_6k_provider_parses_financial_results_filing():
    payload = {
        "filings": {
            "recent": {
                "form": ["6-K", "6-K"],
                "filingDate": ["2026-04-16", "2026-04-18"],
                "accessionNumber": ["0001046179-26-000010", "0001046179-26-000011"],
                "primaryDocument": ["q1-2026-financial-results.htm", "monthly-revenue.htm"],
                "primaryDocDescription": ["Q1 2026 financial results", "Monthly revenue"],
            }
        }
    }
    company = OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW")

    events = SecSixKEarningsProvider.parse_submissions(
        payload,
        company,
        cik="0001046179",
        today=dt.date(2026, 4, 1),
        lookahead_days=30,
    )

    assert len(events) == 1
    assert events[0].ticker == "2330.TW"
    assert events[0].source == "SEC EDGAR 6-K"
    assert "q1-2026-financial-results.htm" in events[0].call_time_source_url


def test_company_ir_provider_parses_configured_official_page():
    html = "<html><title>Q1 Results April 16, 2026</title><body>Quarterly Results April 16, 2026</body></html>"
    company = OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW")

    events = CompanyIrEarningsCalendarProvider.parse_page(
        html,
        company,
        {
            "url": "https://investor.tsmc.com/english/quarterly-results/2026/q1",
            "include_keywords": ["Quarterly Results"],
            "source_type": "official_ir_results_page",
        },
        today=dt.date(2026, 4, 1),
        lookahead_days=30,
    )

    assert len(events) == 1
    assert events[0].report_date == "2026-04-16"
    assert events[0].source == "Company IR"
    assert events[0].call_time_source_type == "official_ir_results_page"


def test_company_ir_provider_requires_keywords_or_explicit_report_date():
    html = "<html><body>Copyright 2026. Updated May 1, 2026.</body></html>"
    company = OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW")

    events = CompanyIrEarningsCalendarProvider.parse_page(
        html,
        company,
        {
            "url": "https://investor.tsmc.com/english/events",
            "source_type": "official_ir_calendar",
        },
        today=dt.date(2026, 4, 1),
        lookahead_days=60,
    )

    assert events == []


def test_company_ir_provider_preserves_explicit_time_metadata():
    html = "<html><body>Investor calendar</body></html>"
    company = OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW")

    events = CompanyIrEarningsCalendarProvider.parse_page(
        html,
        company,
        {
            "url": "https://investor.tsmc.com/english/events",
            "report_date": "2026-04-16",
            "beijing_time": "2026-04-16 14:00",
            "time_label": "盘中",
            "original_call_time_text": "April 16, 2026 14:00 CST",
            "original_timezone": "Asia/Taipei",
            "source_type": "official_ir_calendar",
        },
        today=dt.date(2026, 4, 1),
        lookahead_days=60,
    )

    assert len(events) == 1
    assert events[0].report_date == "2026-04-16"
    assert events[0].beijing_time == "2026-04-16 14:00"
    assert events[0].time_label == "盘中"
    assert events[0].original_timezone == "Asia/Taipei"


def test_confirmed_provider_loads_lite_official_event(tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text(
        """
{
  "events": [
    {
      "ticker": "LITE",
      "report_date": "2026-05-05",
      "time_label": "盘后",
      "beijing_time": "05-06 05:00",
      "status": "confirmed",
      "source": "Lumentum IR"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    provider = ConfirmedEarningsEventsProvider(path)
    universe = {"LITE": OligarchCompany("Lumentum", "LITE", "光芯片与硅光", "normal", "US")}

    events = provider.fetch(universe)

    assert len(events) == 1
    assert events[0].company == "Lumentum"
    assert events[0].ticker == "LITE"
    assert events[0].report_date == "2026-05-05"
    assert events[0].beijing_time == "05-06 05:00"
    assert events[0].status == "confirmed"


def test_yfinance_provider_parses_non_us_calendar_dates():
    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def calendar(self):
            return {"Earnings Date": [dt.date(2026, 7, 16)]}

    provider = YFinanceEarningsCalendarProvider(ticker_factory=FakeTicker)
    universe = {
        "2330.TW": OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW"),
    }

    events = provider.fetch(universe, today=dt.date(2026, 5, 4), lookahead_days=90)

    assert len(events) == 1
    assert events[0].ticker == "2330.TW"
    assert events[0].market == "TW"
    assert events[0].report_date == "2026-07-16"
    assert events[0].status == "estimated_unverified"
    assert events[0].source == "Yahoo Finance"


def test_yfinance_provider_factory_uses_project_session(monkeypatch):
    session = object()
    captured = {}

    def fake_build_session():
        return session

    def fake_ticker(ticker, session=None):
        captured["ticker"] = ticker
        captured["session"] = session
        return object()

    monkeypatch.setattr(yf_session, "build_yf_session", fake_build_session)
    monkeypatch.setattr(yf, "Ticker", fake_ticker)

    ticker_factory = YFinanceEarningsCalendarProvider._load_yfinance_ticker_factory()
    ticker_factory("8035.T")

    assert captured == {"ticker": "8035.T", "session": session}


def test_events_by_date_groups_events_with_super_giants_first():
    events = [
        EarningsCalendarEvent("Applied Materials", "AMAT", "前道晶圆设备与量测", "2026-05-13", priority="normal"),
        EarningsCalendarEvent("AMD", "AMD", "AI加速芯片与定制ASIC", "2026-05-13", priority="strategic_giant"),
        EarningsCalendarEvent("NVIDIA", "NVDA", "AI加速芯片与定制ASIC", "2026-05-13", priority="super_giant"),
    ]

    grouped = events_by_date(events)

    assert list(grouped) == ["2026-05-13"]
    assert [event.ticker for event in grouped["2026-05-13"]] == ["NVDA", "AMD", "AMAT"]


def test_events_by_date_uses_beijing_calendar_date_for_us_after_hours():
    events = [
        EarningsCalendarEvent(
            "AMD",
            "AMD",
            "AI加速芯片与定制ASIC",
            "2026-05-05",
            time_label="盘后",
            market="US",
        ),
        EarningsCalendarEvent(
            "Lumentum",
            "LITE",
            "光芯片与硅光",
            "2026-05-05",
            time_label="盘后",
            beijing_time="05-06 05:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "Eaton",
            "ETN",
            "数据中心电力与配电",
            "2026-05-05",
            time_label="盘前",
            beijing_time="05-05 23:00",
            market="US",
        ),
    ]

    grouped = events_by_date(events)

    assert [event.ticker for event in grouped["2026-05-05"]] == ["ETN"]
    assert [event.ticker for event in grouped["2026-05-06"]] == ["LITE", "AMD"]


def test_sorted_events_orders_same_beijing_calendar_date_by_beijing_time():
    events = [
        EarningsCalendarEvent(
            "ADTRAN",
            "ADTN",
            "光纤光缆与宽带接入",
            "2026-05-04",
            beijing_time="05-05 20:30",
            market="US",
        ),
        EarningsCalendarEvent(
            "Eaton",
            "ETN",
            "数据中心电力与配电",
            "2026-05-05",
            beijing_time="05-05 23:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "Fabrinet",
            "FN",
            "光模块与光引擎",
            "2026-05-04",
            beijing_time="05-05 05:00",
            market="US",
        ),
        EarningsCalendarEvent(
            "GlobalFoundries",
            "GFS",
            "先进制程代工",
            "2026-05-05",
            beijing_time="05-05 20:30",
            market="US",
        ),
    ]

    assert [event.ticker for event in sorted_events(events)] == ["FN", "ADTN", "GFS", "ETN"]
    assert [event.ticker for event in events_by_date(events)["2026-05-05"]] == ["FN", "ADTN", "GFS", "ETN"]


def test_service_filter_window_uses_beijing_calendar_date_for_confirmed_events():
    events = [
        EarningsCalendarEvent(
            "Fabrinet",
            "FN",
            "光模块与光引擎",
            "2026-05-04",
            time_label="盘后",
            beijing_time="05-05 05:00",
            market="US",
        )
    ]

    filtered = GlobalEarningsCalendarService._filter_window(
        events,
        today=dt.date(2026, 5, 5),
        lookahead_days=0,
    )

    assert [event.ticker for event in filtered] == ["FN"]


def test_cached_events_are_rehydrated_from_current_universe():
    class MemoryStore:
        def load_json(self, key, default=None):
            return {
                "events": [
                    EarningsCalendarEvent(
                        "Wus",
                        "2313.TW",
                        "old sector",
                        "2026-05-07",
                        source="Yahoo Finance",
                        market="TW",
                    ).to_dict()
                ]
            }

    service = GlobalEarningsCalendarService(
        data_store=MemoryStore(),
        universe={
            "2313.TW": OligarchCompany("Compeq", "2313.TW", "高频PCB与覆铜板材料", "normal", "TW"),
        },
    )

    events = service._load_cached_events()

    assert len(events) == 1
    assert events[0].company == "Compeq"
    assert events[0].sector == "高频PCB与覆铜板材料"


def test_legacy_cached_yfinance_estimate_is_normalized():
    class MemoryStore:
        def load_json(self, key, default=None):
            return {
                "events": [
                    {
                        "company": "Hanmi Semi",
                        "ticker": "042700.KS",
                        "sector": "Packaging equipment",
                        "report_date": "2026-05-06",
                        "status": "estimated",
                        "source": "Yahoo Finance",
                        "market": "KR",
                    }
                ]
            }

    service = GlobalEarningsCalendarService(
        data_store=MemoryStore(),
        universe={
            "042700.KS": OligarchCompany("Hanmi Semi", "042700.KS", "Packaging equipment", "normal", "KR"),
        },
    )

    events = service._load_cached_events()

    assert len(events) == 1
    assert events[0].status == "estimated_unverified"
    assert is_yfinance_estimate_event(events[0]) is True


def test_merge_events_marks_yfinance_date_conflict_against_higher_source():
    from domains.global_earnings_calendar.service import merge_events

    events = [
        EarningsCalendarEvent(
            "Hanmi Semi",
            "042700.KS",
            "Packaging equipment",
            "2026-05-06",
            status="estimated_unverified",
            source="Yahoo Finance",
            market="KR",
        ),
        EarningsCalendarEvent(
            "Hanmi Semi",
            "042700.KS",
            "Packaging equipment",
            "2026-05-13",
            status="estimated",
            source="Alpha Vantage",
            market="KR",
        ),
    ]

    reconciled = merge_events(events)

    yahoo = next(event for event in reconciled if event.source == "Yahoo Finance")
    assert yahoo.status == "estimated_conflict"
    assert is_yfinance_date_conflict_event(yahoo) is True


def test_sync_unverified_yfinance_cache_updates_legacy_rows_only():
    class MemoryStore:
        def __init__(self):
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        {
                            "company": "Hanmi Semi",
                            "ticker": "042700.KS",
                            "sector": "Packaging equipment",
                            "report_date": "2026-05-06",
                            "status": "estimated",
                            "source": "Yahoo Finance",
                            "market": "KR",
                        },
                        {
                            "company": "AMD",
                            "ticker": "AMD",
                            "sector": "Accelerators",
                            "report_date": "2026-05-05",
                            "status": "estimated",
                            "source": "Nasdaq",
                            "market": "US",
                        },
                    ],
                }
            }
            self.saved = None

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = key
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={
            "042700.KS": OligarchCompany("Hanmi Semi", "042700.KS", "Packaging equipment", "normal", "KR"),
            "AMD": OligarchCompany("AMD", "AMD", "Accelerators", "normal", "US"),
        },
    )

    changed = service.sync_unverified_yfinance_cache()

    assert changed == 1
    assert store.saved == "global_earnings_calendar"
    rows = store.data["global_earnings_calendar"]["events"]
    assert rows[0]["status"] == "estimated_unverified"
    assert rows[1]["status"] == "estimated"


def test_upsert_confirmed_event_writes_json_and_cache_without_touching_trade_dates(tmp_path):
    class MemoryStore:
        def __init__(self):
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "CoreWeave",
                            "CRWV",
                            "云巨头/算力租赁/数据基础设施",
                            "2026-05-07",
                            time_label="盘后",
                            status="estimated",
                            source="Nasdaq",
                            market="US",
                        ).to_dict()
                    ],
                },
                "trade_dates": {"month": "2026-05", "dates": ["2026-05-04"]},
            }
            self.saved_keys = []

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved_keys.append(key)
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    confirmed_path = tmp_path / "confirmed_events.json"
    confirmed_path.write_text('{"events":[]}', encoding="utf-8")
    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={
            "CRWV": OligarchCompany("CoreWeave", "CRWV", "云巨头/算力租赁/数据基础设施", "normal", "US"),
        },
        confirmed_provider=ConfirmedEarningsEventsProvider(confirmed_path),
    )

    service.upsert_confirmed_event(
        EarningsCalendarEvent(
            "CoreWeave",
            "CRWV",
            "云巨头/算力租赁/数据基础设施",
            "2026-05-07",
            fiscal_period="Mar/2026",
            time_label="盘后",
            beijing_time="05-08 05:00",
            status="confirmed",
            source="confirmed",
            conference_url="https://investors.coreweave.com/events-and-presentations/event-details/2026/CoreWeave-First-Quarter-2026-Earnings-Conference-Call/default.aspx",
            market="US",
            original_call_time_text="May 7, 2026 5:00 PM ET",
            original_timezone="America/New_York",
            call_time_source_url="https://investors.coreweave.com/events-and-presentations/event-details/2026/CoreWeave-First-Quarter-2026-Earnings-Conference-Call/default.aspx",
            call_time_source_type="official_ir_event",
        )
    )

    confirmed_payload = json.loads(confirmed_path.read_text(encoding="utf-8"))
    confirmed_event = confirmed_payload["events"][0]
    assert confirmed_event["ticker"] == "CRWV"
    assert confirmed_event["beijing_time"] == "05-08 05:00"
    assert confirmed_event["call_time_source_type"] == "official_ir_event"
    assert store.saved_keys == ["global_earnings_calendar"]
    assert store.data["trade_dates"] == {"month": "2026-05", "dates": ["2026-05-04"]}

    events = service.load_events(today=dt.date(2026, 5, 8), lookahead_days=0, allow_network=False)
    assert [(event.ticker, event.beijing_time, event.status) for event in events] == [
        ("CRWV", "05-08 05:00", "confirmed")
    ]


def test_service_returns_empty_when_all_real_sources_are_empty(monkeypatch, tmp_path):
    class EmptyStore:
        def load_json(self, key, default=None):
            return default

        def save_json(self, key, data):
            raise AssertionError("empty real sources should not write cache")

    service = GlobalEarningsCalendarService(
        data_store=EmptyStore(),
        api_key="",
        confirmed_provider=ConfirmedEarningsEventsProvider(tmp_path / "missing.json"),
    )

    events = service.load_events(today=dt.date(2026, 5, 4))

    assert events == []


def test_service_merges_confirmed_and_network_events_without_demo(tmp_path):
    class EmptyStore:
        saved = None

        def load_json(self, key, default=None):
            return default

        def save_json(self, key, data):
            self.saved = data

    class FakeProvider:
        def fetch(self, universe, **kwargs):
            return [
                EarningsCalendarEvent(
                    "TSMC",
                    "2330.TW",
                    "先进制程代工",
                    "2026-07-16",
                    source="Yahoo Finance",
                    market="TW",
                    priority="super_giant",
                )
            ]

    path = tmp_path / "confirmed.json"
    path.write_text(
        '{"events":[{"ticker":"LITE","report_date":"2026-05-05","source":"Lumentum IR","status":"confirmed"}]}',
        encoding="utf-8",
    )
    store = EmptyStore()
    universe = {
        "LITE": OligarchCompany("Lumentum", "LITE", "光芯片与硅光", "normal", "US"),
        "2330.TW": OligarchCompany("TSMC", "2330.TW", "先进制程代工", "super_giant", "TW"),
    }
    service = GlobalEarningsCalendarService(
        data_store=store,
        api_key="",
        universe=universe,
        confirmed_provider=ConfirmedEarningsEventsProvider(path),
        nasdaq_provider=FakeProvider(),
        yfinance_provider=FakeProvider(),
        official_providers=[],
    )

    events = service.refresh_events(today=dt.date(2026, 5, 4), lookahead_days=90)

    assert [event.ticker for event in events] == ["LITE", "2330.TW"]
    assert "示例" not in {event.source for event in events}


def test_refresh_events_preserves_cached_provider_events_when_network_fails(tmp_path):
    class MemoryStore:
        def __init__(self):
            self.saved = None
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "Applied Materials",
                            "AMAT",
                            "前道晶圆设备与量测",
                            "2026-05-14",
                            source="Nasdaq",
                            market="US",
                        ).to_dict()
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = (key, data)

    class FailingProvider:
        def fetch(self, *_args, **_kwargs):
            raise RuntimeError("network down")

    confirmed_path = tmp_path / "confirmed.json"
    confirmed_path.write_text(
        '{"events":[{"ticker":"CRWV","report_date":"2026-05-07","beijing_time":"2026-05-08 05:00","source":"confirmed","status":"confirmed"}]}',
        encoding="utf-8",
    )
    store = MemoryStore()
    universe = {
        "CRWV": OligarchCompany("CoreWeave", "CRWV", "云巨头/算力租赁/数据基础设施", "normal", "US"),
        "AMAT": OligarchCompany("Applied Materials", "AMAT", "前道晶圆设备与量测", "strategic_giant", "US"),
    }
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe=universe,
        confirmed_provider=ConfirmedEarningsEventsProvider(confirmed_path),
        nasdaq_provider=FailingProvider(),
        provider=FailingProvider(),
        yfinance_provider=FailingProvider(),
        official_providers=[("Company IR", FailingProvider())],
    )

    events = service.refresh_events(today=dt.date(2026, 5, 8), lookahead_days=10)

    assert [(event.ticker, event.source) for event in events] == [
        ("CRWV", "confirmed"),
        ("AMAT", "Nasdaq"),
    ]
    assert store.saved is None


def test_refresh_events_marks_degraded_nasdaq_week_and_reuses_cached_snapshot():
    class MemoryStore:
        def __init__(self):
            self.saved = None
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "Applied Materials",
                            "AMAT",
                            "Semiconductor equipment",
                            "2026-06-26",
                            source="Nasdaq",
                            market="US",
                        ).to_dict()
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = key
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    class DegradedNasdaqProvider:
        def __init__(self):
            self.last_degradation = None

        def fetch(self, *_args, today, lookahead_days, **_kwargs):
            failed_days = [
                (today + dt.timedelta(days=offset)).isoformat()
                for offset in range(int(lookahead_days) + 1)
            ]
            self.last_degradation = {
                "provider": "Nasdaq",
                "reason": "day_fetch_failed",
                "failed_days": failed_days,
                "failed_count": len(failed_days),
                "requested_days": failed_days,
                "requested_count": len(failed_days),
                "returned_events": 0,
                "all_days_failed": True,
                "sample_error": "read timeout",
            }
            return []

    class EmptyProvider:
        def fetch(self, *_args, **_kwargs):
            return []

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={"AMAT": OligarchCompany("Applied Materials", "AMAT", "Semiconductor equipment", "normal", "US")},
        confirmed_provider=ConfirmedEarningsEventsProvider("missing.json"),
        nasdaq_provider=DegradedNasdaqProvider(),
        provider=EmptyProvider(),
        yfinance_provider=EmptyProvider(),
        official_providers=[],
    )

    events = service.refresh_events(today=dt.date(2026, 6, 19), lookahead_days=7)

    assert [(event.ticker, event.report_date, event.source) for event in events] == [
        ("AMAT", "2026-06-26", "Nasdaq")
    ]
    assert store.saved == "global_earnings_calendar"
    payload = store.data["global_earnings_calendar"]
    assert payload["source"] == "stale_cache"
    assert payload["cache_state"]["status"] == "degraded"
    assert payload["cache_state"]["providers"] == ["Nasdaq"]
    assert payload["cache_state"]["failed_days"] == [
        "2026-06-19",
        "2026-06-20",
        "2026-06-21",
        "2026-06-22",
        "2026-06-23",
        "2026-06-24",
        "2026-06-25",
        "2026-06-26",
    ]
    assert payload["cache_state"]["stale_cache_reused"] is True
    assert payload["cache_state"]["reused_event_count"] == 1
    assert service.load_cache_status()["status"] == "degraded"


def test_mark_refresh_failed_preserves_cached_snapshot_and_sets_retryable_state():
    class MemoryStore:
        def __init__(self):
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "Applied Materials",
                            "AMAT",
                            "Semiconductor equipment",
                            "2026-06-26",
                            source="Nasdaq",
                            market="US",
                        ).to_dict()
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={"AMAT": OligarchCompany("Applied Materials", "AMAT", "Semiconductor equipment", "normal", "US")},
        confirmed_provider=ConfirmedEarningsEventsProvider("missing.json"),
        official_providers=[],
    )

    state = service.mark_refresh_failed(RuntimeError("sqlite busy"))

    payload = store.data["global_earnings_calendar"]
    assert payload["source"] == "stale_cache"
    assert [row["ticker"] for row in payload["events"]] == ["AMAT"]
    assert state["status"] == "degraded"
    assert state["reason"] == "refresh_exception"
    assert state["retryable"] is True
    assert state["stale_cache_reused"] is True
    assert state["reused_event_count"] == 1
    assert "sqlite busy" in state["error"]


def test_refresh_events_keeps_failed_day_cache_when_same_nasdaq_ticker_refreshes_later():
    class MemoryStore:
        def __init__(self):
            self.saved = None
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "Applied Materials",
                            "AMAT",
                            "Semiconductor equipment",
                            "2026-06-20",
                            source="Nasdaq",
                            market="US",
                        ).to_dict()
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = key
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    class PartiallyDegradedNasdaqProvider:
        def __init__(self):
            self.last_degradation = None

        def fetch(self, universe, **_kwargs):
            self.last_degradation = {
                "provider": "Nasdaq",
                "reason": "day_fetch_failed",
                "failed_days": ["2026-06-20"],
                "failed_count": 1,
                "requested_days": ["2026-06-20", "2026-06-23"],
                "requested_count": 2,
                "returned_events": 1,
                "all_days_failed": False,
                "sample_error": "read timeout",
            }
            company = universe["AMAT"]
            return [
                EarningsCalendarEvent(
                    company.company,
                    company.ticker,
                    company.sector,
                    "2026-06-23",
                    source="Nasdaq",
                    market=company.market,
                )
            ]

    class EmptyProvider:
        def fetch(self, *_args, **_kwargs):
            return []

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={"AMAT": OligarchCompany("Applied Materials", "AMAT", "Semiconductor equipment", "normal", "US")},
        confirmed_provider=ConfirmedEarningsEventsProvider("missing.json"),
        nasdaq_provider=PartiallyDegradedNasdaqProvider(),
        provider=EmptyProvider(),
        yfinance_provider=EmptyProvider(),
        official_providers=[],
    )

    events = service.refresh_events(today=dt.date(2026, 6, 19), lookahead_days=7)

    assert [(event.ticker, event.report_date, event.source) for event in events] == [
        ("AMAT", "2026-06-20", "Nasdaq"),
        ("AMAT", "2026-06-23", "Nasdaq"),
    ]
    assert store.saved == "global_earnings_calendar"
    payload = store.data["global_earnings_calendar"]
    assert payload["source"] == "provider"
    assert payload["cache_state"]["status"] == "degraded"
    assert payload["cache_state"]["failed_days"] == ["2026-06-20"]
    assert payload["cache_state"]["reused_event_count"] == 1


def test_refresh_events_marks_degraded_mops_partial_stop_and_reuses_failed_ticker_cache():
    class MemoryStore:
        def __init__(self):
            self.saved = None
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "ASE",
                            "3711.TW",
                            "advanced packaging",
                            "2026-06-30",
                            source="MOPS",
                            market="TW",
                        ).to_dict()
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = key
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    class PartiallyStoppedMopsProvider:
        def __init__(self):
            self.last_degradation = None

        def fetch(self, universe, **_kwargs):
            self.last_degradation = {
                "provider": "MOPS",
                "reason": "ticker_fetch_stopped",
                "failed_tickers": ["3711.TW"],
                "failed_count": 1,
                "requested_tickers": ["2330.TW", "3711.TW"],
                "requested_count": 2,
                "returned_events": 1,
                "all_tickers_failed": False,
                "stop_after_ticker": "3711.TW",
                "sample_error": "TLS connect error: invalid library",
            }
            company = universe["2330.TW"]
            return [
                EarningsCalendarEvent(
                    company.company,
                    company.ticker,
                    company.sector,
                    "2026-06-27",
                    source="MOPS",
                    market=company.market,
                )
            ]

    class EmptyProvider:
        def fetch(self, *_args, **_kwargs):
            return []

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={
            "2330.TW": OligarchCompany("TSMC", "2330.TW", "foundry", "super_giant", "TW"),
            "3711.TW": OligarchCompany("ASE", "3711.TW", "advanced packaging", "normal", "TW"),
        },
        confirmed_provider=ConfirmedEarningsEventsProvider("missing.json"),
        nasdaq_provider=EmptyProvider(),
        provider=EmptyProvider(),
        yfinance_provider=EmptyProvider(),
        official_providers=[("MOPS", PartiallyStoppedMopsProvider())],
    )

    events = service.refresh_events(today=dt.date(2026, 6, 25), lookahead_days=10)

    assert [(event.ticker, event.report_date, event.source) for event in events] == [
        ("2330.TW", "2026-06-27", "MOPS"),
        ("3711.TW", "2026-06-30", "MOPS"),
    ]
    assert store.saved == "global_earnings_calendar"
    payload = store.data["global_earnings_calendar"]
    assert payload["source"] == "provider"
    assert payload["cache_state"]["status"] == "degraded"
    assert payload["cache_state"]["providers"] == ["MOPS"]
    assert payload["cache_state"]["failed_tickers"] == ["3711.TW"]
    assert payload["cache_state"]["reused_event_count"] == 1
    assert payload["cache_state"]["stale_cache_reused"] is True


def test_refresh_events_merges_cached_tickers_that_were_not_refreshed():
    class MemoryStore:
        def __init__(self):
            self.data = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        EarningsCalendarEvent(
                            "Old Nasdaq",
                            "OLD",
                            "legacy",
                            "2026-05-11",
                            source="Nasdaq",
                            market="US",
                        ).to_dict(),
                        EarningsCalendarEvent(
                            "Applied Materials",
                            "AMAT",
                            "legacy",
                            "2026-05-13",
                            source="Nasdaq",
                            market="US",
                        ).to_dict(),
                        EarningsCalendarEvent(
                            "KYEC",
                            "2449.TW",
                            "封测",
                            "2026-05-08",
                            source="Yahoo Finance",
                            status="estimated_unverified",
                            market="TW",
                        ).to_dict(),
                    ],
                }
            }
            self.saved = None

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = key
            self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))

    class NasdaqProvider:
        def fetch(self, universe, **_kwargs):
            company = universe["AMAT"]
            return [
                EarningsCalendarEvent(
                    company.company,
                    company.ticker,
                    company.sector,
                    "2026-05-14",
                    source="Nasdaq",
                    market=company.market,
                )
            ]

    class EmptyProvider:
        def fetch(self, *_args, **_kwargs):
            return []

    store = MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={
            "AMAT": OligarchCompany("Applied Materials", "AMAT", "前道晶圆设备与量测", "strategic_giant", "US"),
            "2449.TW": OligarchCompany("KYEC", "2449.TW", "封测", "normal", "TW"),
            "OLD": OligarchCompany("Old Nasdaq", "OLD", "legacy", "normal", "US"),
        },
        confirmed_provider=ConfirmedEarningsEventsProvider("missing.json"),
        nasdaq_provider=NasdaqProvider(),
        provider=EmptyProvider(),
        yfinance_provider=EmptyProvider(),
        official_providers=[],
    )

    events = service.refresh_events(today=dt.date(2026, 5, 8), lookahead_days=10)

    assert store.saved == "global_earnings_calendar"
    assert [(event.ticker, event.source) for event in events] == [
        ("2449.TW", "Yahoo Finance"),
        ("OLD", "Nasdaq"),
        ("AMAT", "Nasdaq"),
    ]
    assert next(event for event in events if event.ticker == "AMAT").report_date == "2026-05-14"


def test_filter_window_clamps_negative_lookahead_to_today():
    events = [
        EarningsCalendarEvent("Today", "TODAY", "sector", "2026-05-08"),
        EarningsCalendarEvent("Tomorrow", "NEXT", "sector", "2026-05-09"),
    ]

    filtered = GlobalEarningsCalendarService._filter_window(
        events,
        today=dt.date(2026, 5, 8),
        lookahead_days=-3,
    )

    assert [event.ticker for event in filtered] == ["TODAY"]


def test_build_demo_events_are_relative_to_current_month():
    events = build_demo_events(dt.date(2026, 5, 4))

    assert events[0].report_date == "2026-05-07"
    assert events[0].beijing_time == "05-08 05:00"
    assert events[0].status == "confirmed"


def test_build_oligarch_universe_handles_missing_module_and_empty_tickers(monkeypatch):
    monkeypatch.setattr(
        "domains.global_earnings_calendar.service.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("missing")),
    )
    assert build_oligarch_universe(None) == {}

    module = SimpleNamespace(
        OLIGARCH_DICT={"sector": ["EmptyTicker", "NormalCo"]},
        VANGUARD_TICKERS={"EmptyTicker": "", "NormalCo": "NORM"},
        SUPER_GIANTS=set(),
        STRATEGIC_GIANTS=set(),
    )
    universe = build_oligarch_universe(module)
    assert set(universe) == {"NORM"}


def test_service_lazy_data_store_and_load_events_network_fallback(monkeypatch):
    store = SimpleNamespace(load_json=lambda key, default=None: default, save_json=lambda key, data: None)
    monkeypatch.setattr("core.data_store.data_store", store)

    service = GlobalEarningsCalendarService(
        data_store=None,
        universe={"LITE": OligarchCompany("Lumentum", "LITE", "sector", "normal", "US")},
        confirmed_provider=SimpleNamespace(fetch=lambda universe: []),
        official_providers=[],
        nasdaq_provider=SimpleNamespace(fetch=lambda *_args, **_kwargs: []),
        provider=SimpleNamespace(fetch=lambda *_args, **_kwargs: []),
        yfinance_provider=SimpleNamespace(fetch=lambda *_args, **_kwargs: []),
    )
    refreshed = [EarningsCalendarEvent("Lumentum", "LITE", "sector", "2026-05-05", market="US")]
    service.refresh_events = lambda **_kwargs: refreshed

    assert service.data_store is store
    assert service.load_events(today=dt.date(2026, 5, 5), allow_network=True) == refreshed


def test_service_cached_and_confirmed_fallback_edges():
    class Store:
        def __init__(self):
            self.saved = None
            self.payload = {
                "global_earnings_calendar": {
                    "source": "provider",
                    "events": [
                        None,
                        EarningsCalendarEvent("Other", "OTHER", "legacy", "2026-05-10", source="Nasdaq").to_dict(),
                    ],
                }
            }

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.payload.get(key, default), ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = (key, data)
            self.payload[key] = json.loads(json.dumps(data, ensure_ascii=False))

    class BadConfirmedProvider:
        @staticmethod
        def fetch(universe):
            raise RuntimeError("confirmed broken")

        @staticmethod
        def upsert(event):
            return None

    store = Store()
    universe = {
        "LITE": OligarchCompany("Lumentum", "LITE", "sector", "normal", "US"),
        "OTHER": OligarchCompany("Other", "OTHER", "legacy", "normal", "US"),
    }
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe=universe,
        confirmed_provider=BadConfirmedProvider(),
        official_providers=[],
    )

    assert service._hydrate_event_from_universe(EarningsCalendarEvent("Unknown", "MISS", "x", "2026-05-05")) is None
    assert service._load_confirmed_events() == []

    event = EarningsCalendarEvent("Lumentum", "LITE", "sector", "2026-05-05", source="confirmed", status="confirmed")
    service._sync_cached_confirmed_event(event)
    saved_events = store.saved[1]["events"]
    assert {row["ticker"] for row in saved_events} == {"LITE", "OTHER"}

    try:
        service.upsert_confirmed_event(EarningsCalendarEvent("Unknown", "MISS", "x", "2026-05-05"))
    except ConfirmedEventWriteError as exc:
        assert "unknown_ticker" in str(exc)
    else:
        raise AssertionError("unknown ticker should fail")


def test_service_sync_cache_shape_edges_and_filter_invalid_dates():
    class Store:
        def __init__(self, payload):
            self.payload = payload
            self.saved = None

        def load_json(self, key, default=None):
            return json.loads(json.dumps(self.payload, ensure_ascii=False))

        def save_json(self, key, data):
            self.saved = (key, data)
            self.payload = json.loads(json.dumps(data, ensure_ascii=False))

    assert GlobalEarningsCalendarService(data_store=Store({"events": "bad"}), universe={}, official_providers=[]).sync_unverified_yfinance_cache() == 0

    store = Store(
        {
            "source": "provider",
            "events": [
                "raw-row",
                {
                    "company": "TSMC",
                    "ticker": "2330.TW",
                    "sector": "foundry",
                    "report_date": "2026-07-16",
                    "status": "estimated",
                    "source": "Yahoo Finance",
                    "market": "TW",
                },
            ],
        }
    )
    service = GlobalEarningsCalendarService(data_store=store, universe={}, official_providers=[])
    assert service.sync_unverified_yfinance_cache() == 1
    assert store.payload["events"][0] == "raw-row"
    assert store.payload["events"][1]["status"] == "estimated_unverified"

    invalid = EarningsCalendarEvent("Bad", "BAD", "sector", "not-a-date")
    assert GlobalEarningsCalendarService._filter_window([invalid], today=dt.date(2026, 5, 5), lookahead_days=10) == []
