from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import domains.global_earnings_calendar.models as calendar_models
from app.services import asian_market_service
from domains.global_earnings_calendar import event_ops
from domains.global_earnings_calendar.models import ConfirmedEventWriteError, EarningsCalendarEvent, OligarchCompany
from domains.global_earnings_calendar.providers import _utils as provider_utils
from domains.global_earnings_calendar.providers.alpha_vantage import AlphaVantageEarningsCalendarProvider
from domains.global_earnings_calendar.providers.nasdaq import NasdaqEarningsCalendarProvider
from domains.global_earnings_calendar.storage import ConfirmedEarningsEventsProvider
from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT


def test_asian_market_service_delegates_to_fetcher_and_rate_limit_modules(monkeypatch):
    calls = []

    assert asian_market_service.yf_session_module.__name__ == "infra.market_data.yfinance_session"
    assert asian_market_service.asian_fetcher_module.__name__ == "infra.market_data.asian_kline_provider"

    monkeypatch.setattr(
        asian_market_service.yf_session_module,
        "build_yf_session",
        lambda: calls.append(("session",)) or "session",
    )
    monkeypatch.setattr(
        asian_market_service.yf_session_module,
        "get_yf_rate_limit_status",
        lambda: calls.append(("status",)) or {"limited": False},
    )
    monkeypatch.setattr(
        asian_market_service.yf_session_module,
        "is_yf_rate_limit_error",
        lambda exc: calls.append(("is_limit", exc)) or True,
    )
    monkeypatch.setattr(
        asian_market_service.yf_session_module,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: calls.append(("mark", exc, cooldown_sec)) or "marked",
    )
    monkeypatch.setattr(
        asian_market_service.asian_fetcher_module,
        "fetch_single_kline",
        lambda name, ticker, **kwargs: calls.append(("fetch", name, ticker, kwargs)) or {"ticker": ticker},
    )
    monkeypatch.setattr(
        asian_market_service.asian_fetcher_module,
        "filter_asian_tickers",
        lambda market_filter=None: calls.append(("filter", market_filter)) or {"HSI": "Hong Kong"},
    )
    monkeypatch.setattr(
        asian_market_service.asian_fetcher_module,
        "find_asian_track",
        lambda ticker: calls.append(("track", ticker)) or "HK",
    )
    monkeypatch.setattr(
        asian_market_service.asian_fetcher_module,
        "sync_asian_kline_cache",
        lambda **kwargs: calls.append(("sync", kwargs)) or {"ok": True},
    )

    assert asian_market_service.build_yf_session() == "session"
    assert asian_market_service.get_yf_rate_limit_status() == {"limited": False}
    assert asian_market_service.is_yf_rate_limit_error(RuntimeError("x")) is True
    assert asian_market_service.mark_yf_rate_limited("err") == "marked"
    assert asian_market_service.mark_yf_rate_limited("err", cooldown_sec=30) == "marked"
    assert asian_market_service.fetch_single_kline("Hang Seng", "^HSI", session="s") == {
        "ticker": "^HSI"
    }
    assert asian_market_service.filter_asian_tickers("HK") == {"HSI": "Hong Kong"}
    assert asian_market_service.find_asian_track("^HSI") == "HK"
    assert asian_market_service.sync_asian_kline_cache(market_filter="HK", single_ticker="^HSI") == {"ok": True}

    assert [call[0] for call in calls] == [
        "session",
        "status",
        "is_limit",
        "mark",
        "mark",
        "fetch",
        "filter",
        "track",
        "sync",
    ]


def test_confirmed_events_provider_fetches_only_universe_events(tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {"ticker": "lite", "company": "Old", "sector": "old", "report_date": "2026-05-01"},
                    {"ticker": "MISS", "company": "Missing", "report_date": "2026-05-02"},
                    {"ticker": "", "report_date": "2026-05-03"},
                ]
            }
        ),
        encoding="utf-8",
    )
    universe = {"LITE": OligarchCompany("Lumentum", "LITE", "Optics", "strategic", "US")}

    events = ConfirmedEarningsEventsProvider(path).fetch(universe)

    assert len(events) == 1
    assert events[0].company == "Lumentum"
    assert events[0].sector == "Optics"
    assert events[0].source == "confirmed"
    assert events[0].priority == "strategic"


def test_confirmed_events_provider_upsert_replaces_matching_identity(tmp_path):
    path = tmp_path / "confirmed.json"
    provider = ConfirmedEarningsEventsProvider(path)
    first = EarningsCalendarEvent("Alpha", "ALP", "sector", "2026-05-01", fiscal_period="Q1")
    replacement = EarningsCalendarEvent("Alpha New", "ALP", "new", "2026-05-01", fiscal_period="Q1")
    second = EarningsCalendarEvent("Beta", "BET", "sector", "2026-05-02")

    provider.upsert(first)
    provider.upsert(second)
    provider.upsert(replacement)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [row["ticker"] for row in payload["events"]] == ["ALP", "BET"]
    assert payload["events"][0]["company"] == "Alpha New"


def test_confirmed_events_provider_serializes_concurrent_upserts(monkeypatch, tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text('{"events": []}', encoding="utf-8")
    real_read_text = Path.read_text
    start = threading.Barrier(8)
    errors = []

    def delayed_read_text(self, *args, **kwargs):
        text = real_read_text(self, *args, **kwargs)
        if self == path:
            time.sleep(0.03)
        return text

    def writer(index):
        try:
            start.wait(timeout=2)
            ConfirmedEarningsEventsProvider(path).upsert(
                EarningsCalendarEvent(f"Company {index}", f"T{index:02d}", "sector", "2026-05-01")
            )
        except Exception as exc:  # pragma: no cover - assertion below reports worker failures
            errors.append(exc)

    monkeypatch.setattr(Path, "read_text", delayed_read_text)
    threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    payload = json.loads(real_read_text(path, encoding="utf-8"))
    assert {row["ticker"] for row in payload["events"]} == {f"T{index:02d}" for index in range(8)}


def test_confirmed_events_provider_rejects_invalid_events_shape(tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps({"events": {"ticker": "ALP"}}), encoding="utf-8")

    with pytest.raises(ConfirmedEventWriteError, match="confirmed_json_events_not_list"):
        ConfirmedEarningsEventsProvider(path).upsert(EarningsCalendarEvent("Alpha", "ALP", "sector", "2026-05-01"))


def test_confirmed_events_provider_handles_read_and_write_failures(monkeypatch, tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text("{bad json", encoding="utf-8")
    assert ConfirmedEarningsEventsProvider(path).fetch({}) == []

    with pytest.raises(ConfirmedEventWriteError, match="confirmed_json_read_failed"):
        ConfirmedEarningsEventsProvider(path).upsert(EarningsCalendarEvent("Alpha", "ALP", "sector", "2026-05-01"))

    path.unlink()
    monkeypatch.setattr(Path, "write_text", lambda self, *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(ConfirmedEventWriteError, match="confirmed_json_write_failed"):
        ConfirmedEarningsEventsProvider(path).upsert(EarningsCalendarEvent("Alpha", "ALP", "sector", "2026-05-01"))


def test_confirmed_events_provider_logs_unavailable_path(monkeypatch, tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text("{bad json", encoding="utf-8")
    warnings = []
    monkeypatch.setattr("domains.global_earnings_calendar.storage.log.warning", warnings.append)

    assert ConfirmedEarningsEventsProvider(path).fetch({}) == []

    assert warnings
    assert str(path) in warnings[0]


def test_confirmed_events_provider_validates_temp_json_before_replace(monkeypatch, tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text('{"events":[]}', encoding="utf-8")
    real_write_text = Path.write_text

    def corrupt_temp_json(self, text, *args, **kwargs):
        if self.name.endswith(".tmp"):
            return real_write_text(self, "{bad json", encoding=kwargs.get("encoding", "utf-8"))
        return real_write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", corrupt_temp_json)

    with pytest.raises(ConfirmedEventWriteError, match="confirmed_json_write_validation_failed"):
        ConfirmedEarningsEventsProvider(path).upsert(
            EarningsCalendarEvent("Alpha", "ALP", "sector", "2026-05-01")
        )

    assert json.loads(path.read_text(encoding="utf-8")) == {"events": []}
    assert not path.with_name("confirmed.json.tmp").exists()


def test_earnings_calendar_models_cover_invalid_and_yfinance_status_paths():
    assert EarningsCalendarEvent.from_dict(None) is None

    event = EarningsCalendarEvent.from_dict(
        {
            "ticker": "NVDA",
            "report_date": "2026-05-20T00:00:00",
            "source": calendar_models.YFINANCE_SOURCE,
            "status": "estimated",
        }
    )

    assert event is not None
    assert event.company == "NVDA"
    assert event.status == calendar_models.YFINANCE_UNVERIFIED_STATUS
    assert calendar_models.is_yfinance_estimate_event(event) is True
    assert calendar_models.is_yfinance_date_conflict_event(event) is False

    confirmed = EarningsCalendarEvent("Nvidia", "NVDA", "AI", "2026-05-20", status="confirmed")
    assert calendar_models.normalize_event_status(confirmed) is confirmed
    assert calendar_models._events_match_identity(confirmed, EarningsCalendarEvent("Nvidia", "NVDA", "AI", "2026-05-21")) is False


def test_earnings_event_ops_cover_sort_and_merge_edges():
    events = [
        EarningsCalendarEvent("Unknown", "UNK", "AI", "2026-05-03", time_label="unknown", priority="normal"),
        EarningsCalendarEvent("Pre", "PRE", "AI", "2026-05-03", time_label="pre-market", priority="normal"),
        EarningsCalendarEvent("During", "DUR", "AI", "2026-05-03", time_label="during market", priority="normal"),
    ]
    assert [event.ticker for event in event_ops.sorted_events(events)] == ["PRE", "DUR", "UNK"]

    yfinance_bad_date = EarningsCalendarEvent(
        "Estimate",
        "EST",
        "AI",
        "bad-date",
        source=calendar_models.YFINANCE_SOURCE,
        status="estimated",
    )
    other_bad_date = EarningsCalendarEvent("Confirmed", "EST", "AI", "also-bad", source="Company IR", status="confirmed")
    assert [event.ticker for event in event_ops.merge_events([yfinance_bad_date, other_bad_date])]

    alpha = EarningsCalendarEvent("Alpha", "ALP", "AI", "2026-05-01", source="Alpha Vantage")
    company = EarningsCalendarEvent("Alpha", "ALP", "AI", "2026-05-01", source="Company IR")
    merged = event_ops.merge_events([alpha, company])
    assert merged == [company]


def test_provider_utils_skips_ascii_ca_bundle(monkeypatch, tmp_path):
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(tmp_path / "cacert.pem")))

    provider_utils._ensure_ascii_ca_bundle()

    assert "CURL_CA_BUNDLE" not in provider_utils.os.environ
    assert "REQUESTS_CA_BUNDLE" not in provider_utils.os.environ


def test_provider_utils_copies_non_ascii_ca_bundle(monkeypatch, tmp_path):
    source_dir = tmp_path / "中文"
    source_dir.mkdir()
    source = source_dir / "cacert.pem"
    source.write_text("cert", encoding="utf-8")
    target_base = tmp_path / "ascii-temp"
    copied = []

    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(source)))
    monkeypatch.setattr(provider_utils.tempfile, "gettempdir", lambda: str(target_base))
    monkeypatch.setattr(provider_utils.shutil, "copyfile", lambda src, dst: copied.append((src, dst)) or None)

    provider_utils._ensure_ascii_ca_bundle()

    expected_target = target_base / "codex_certifi" / "cacert.pem"
    assert copied == [(str(source), expected_target)]
    assert provider_utils.os.environ["CURL_CA_BUNDLE"] == str(expected_target)
    assert provider_utils.os.environ["REQUESTS_CA_BUNDLE"] == str(expected_target)


def test_provider_utils_handles_missing_certifi_and_copy_failure(monkeypatch, tmp_path):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    provider_utils._ensure_ascii_ca_bundle()

    source_dir = tmp_path / "中文"
    source_dir.mkdir()
    source = source_dir / "cacert.pem"
    source.write_text("cert", encoding="utf-8")
    monkeypatch.setattr("builtins.__import__", real_import)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(source)))
    monkeypatch.setattr(provider_utils.tempfile, "gettempdir", lambda: str(tmp_path / "ascii-temp"))
    monkeypatch.setattr(provider_utils.shutil, "copyfile", lambda *_args: (_ for _ in ()).throw(OSError("copy failed")))

    provider_utils._ensure_ascii_ca_bundle()


def test_alpha_vantage_provider_fetch_and_parse_edges():
    class Response:
        text = "symbol,reportDate,fiscalDateEnding\nNVDA,2026-05-20,2026-04-30\n"

        def __init__(self):
            self.raised = False

        def raise_for_status(self):
            self.raised = True

    class Session:
        def __init__(self):
            self.response = Response()
            self.calls = []

        def get(self, url, *, headers, params, timeout):
            self.calls.append((url, headers, params, timeout))
            return self.response

    session = Session()
    provider = AlphaVantageEarningsCalendarProvider(api_key="key", session=session, base_url="https://example.test")
    universe = {"NVDA": OligarchCompany("Nvidia", "NVDA", "AI", "strategic", "US")}

    events = provider.fetch(universe, horizon="3month")

    assert session.response.raised is True
    assert session.calls[0][1]["User-Agent"] == DEFAULT_REQUESTS_USER_AGENT
    assert session.calls[0][2]["apikey"] == "key"
    assert events[0].ticker == "NVDA"
    assert AlphaVantageEarningsCalendarProvider().fetch(universe) == []
    assert provider.parse_csv("", universe) == []
    assert provider.parse_csv("symbol,reportDate\nMISS,\nNVDA,\n", universe) == []


def test_nasdaq_provider_fetch_and_parse_edges(monkeypatch):
    provider = NasdaqEarningsCalendarProvider(max_workers=1)
    assert provider.fetch({"TSM": OligarchCompany("TSMC", "TSM", "AI", "strategic", "TW")}) == []
    assert provider.last_degradation is None

    monkeypatch.setattr(
        provider,
        "_fetch_day",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(provider.session.RequestException("network failed")),
    )
    assert (
        provider.fetch(
            {"NVDA": OligarchCompany("Nvidia", "NVDA", "AI", "strategic", "US")},
            today=dt.date(2026, 5, 1),
            lookahead_days=0,
        )
        == []
    )
    assert provider.last_degradation is not None
    assert provider.last_degradation["provider"] == "Nasdaq"
    assert provider.last_degradation["failed_days"] == ["2026-05-01"]
    assert provider.last_degradation["all_days_failed"] is True

    events = provider._parse_payload(
        {"data": {"rows": [None, {"symbol": "MISS"}, {"symbol": "NVDA", "time": "time-during-market"}]}},
        dt.date(2026, 5, 1),
        {"NVDA": OligarchCompany("Nvidia", "NVDA", "AI", "strategic", "US")},
        {"NVDA"},
    )
    assert events[0].time_label
