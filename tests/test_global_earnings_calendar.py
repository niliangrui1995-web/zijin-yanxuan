# -*- coding: utf-8 -*-
import datetime as dt
from types import SimpleNamespace

from domains.global_earnings_calendar.service import (
    AlphaVantageEarningsCalendarProvider,
    ConfirmedEarningsEventsProvider,
    EarningsCalendarEvent,
    GlobalEarningsCalendarService,
    NasdaqEarningsCalendarProvider,
    OligarchCompany,
    YFinanceEarningsCalendarProvider,
    build_demo_events,
    build_oligarch_universe,
    events_by_date,
    sorted_events,
)


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
    )

    universe = build_oligarch_universe(module)

    assert universe["NVDA"].company == "NVIDIA"
    assert universe["NVDA"].sector == "AI加速芯片与定制ASIC"
    assert universe["NVDA"].priority == "super_giant"
    assert universe["AVGO"].sector == "AI加速芯片与定制ASIC"
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
        "AMAT": SimpleNamespace(company="Applied Materials", ticker="AMAT", sector="前道晶圆设备与量测", priority="normal"),
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
        def get(self, *args, **kwargs):
            return FakeResponse()

    provider = NasdaqEarningsCalendarProvider(session=FakeSession())
    universe = {
        "LITE": OligarchCompany("Lumentum", "LITE", "光芯片与硅光", "normal", "US"),
    }

    events = provider.fetch(universe, today=dt.date(2026, 5, 5), lookahead_days=0)

    assert len(events) == 1
    assert events[0].ticker == "LITE"
    assert events[0].report_date == "2026-05-05"
    assert events[0].time_label == "盘后"
    assert events[0].source == "Nasdaq"


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
    assert events[0].source == "Yahoo Finance"


def test_events_by_date_groups_events_with_super_giants_first():
    events = [
        EarningsCalendarEvent("Applied Materials", "AMAT", "前道晶圆设备与量测", "2026-05-13", priority="normal"),
        EarningsCalendarEvent("NVIDIA", "NVDA", "AI加速芯片与定制ASIC", "2026-05-13", priority="super_giant"),
    ]

    grouped = events_by_date(events)

    assert list(grouped) == ["2026-05-13"]
    assert [event.ticker for event in grouped["2026-05-13"]] == ["NVDA", "AMAT"]


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
    )

    events = service.refresh_events(today=dt.date(2026, 5, 4), lookahead_days=90)

    assert [event.ticker for event in events] == ["LITE", "2330.TW"]
    assert "示例" not in {event.source for event in events}


def test_build_demo_events_are_relative_to_current_month():
    events = build_demo_events(dt.date(2026, 5, 4))

    assert events[0].report_date == "2026-05-07"
    assert events[0].beijing_time == "05-08 05:00"
    assert events[0].status == "confirmed"
