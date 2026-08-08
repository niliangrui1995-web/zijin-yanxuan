# -*- coding: utf-8 -*-
import json

from domains.global_earnings_calendar import refresh_cache
from domains.global_earnings_calendar.constants import BACKGROUND_REFRESH_PROVIDER_DEADLINE_SEC


def test_refresh_cache_main_prints_success_summary(monkeypatch, capsys):
    refresh_kwargs = {}

    class FakeService:
        def refresh_events(self, **_kwargs):
            refresh_kwargs.update(_kwargs)
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

        def load_cache_status(self):
            return {}

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {"status": "success", "events": 2}
    assert refresh_kwargs == {"provider_timeout_sec": BACKGROUND_REFRESH_PROVIDER_DEADLINE_SEC}


def test_refresh_cache_main_prints_degraded_summary(monkeypatch, capsys):
    class FakeService:
        def refresh_events(self, **_kwargs):
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

        def load_cache_status(self):
            return {
                "status": "degraded",
                "providers": ["MOPS"],
                "failed_tickers": ["3711.TW"],
                "reused_event_count": 1,
            }

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "status": "degraded",
        "events": 2,
        "providers": ["MOPS"],
        "failed_tickers": ["3711.TW"],
        "reused_event_count": 1,
    }


def test_refresh_cache_main_returns_failure_for_all_provider_failure(monkeypatch, capsys):
    class FakeService:
        def refresh_events(self, **_kwargs):
            return [{"symbol": "NVDA"}]

        def load_cache_status(self):
            return {
                "status": "failed",
                "reason": "all_providers_failed",
                "providers": ["Nasdaq", "Yahoo Finance"],
                "retryable": True,
                "all_providers_failed": True,
                "reused_event_count": 1,
            }

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "status": "failed",
        "events": 1,
        "providers": ["Nasdaq", "Yahoo Finance"],
        "reason": "all_providers_failed",
        "retryable": True,
        "reused_event_count": 1,
        "all_providers_failed": True,
    }


def test_refresh_cache_main_marks_exception_as_retryable_degraded(monkeypatch, capsys):
    marked = []

    class FakeService:
        def refresh_events(self, **_kwargs):
            raise RuntimeError("sqlite busy\nretry later")

        def mark_refresh_failed(self, exc):
            marked.append(str(exc))
            return {
                "status": "degraded",
                "reason": "refresh_exception",
                "error": "sqlite busy",
                "retryable": True,
                "reused_event_count": 2,
            }

        def load_events(self, **kwargs):
            assert kwargs == {"allow_network": False}
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    output_lines = capsys.readouterr().out.splitlines()
    payload = json.loads([line for line in output_lines if line.startswith("{")][-1])

    assert marked == ["sqlite busy\nretry later"]
    assert payload["status"] == "degraded"
    assert payload["events"] == 2
    assert payload["reason"] == "refresh_exception"
    assert payload["error"] == "sqlite busy | retry later"
    assert payload["retryable"] is True
    assert payload["reused_event_count"] == 2


def test_refresh_cache_cli_redacts_secrets_from_logs_and_json(monkeypatch, capfd):
    alpha_secret = "ALPHA_CLI_SECRET_123"
    dart_secret = "DART_CLI_SECRET_456"

    class FakeService:
        def refresh_events(self, **_kwargs):
            raise RuntimeError(f"failed https://example.test?apikey={alpha_secret}")

        def mark_refresh_failed(self, _exc):
            raise RuntimeError(f"mark failed token={dart_secret}")

        def load_events(self, **_kwargs):
            raise RuntimeError(f"load failed crtfc_key={dart_secret}")

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    output = capfd.readouterr().out
    payload = json.loads([line for line in output.splitlines() if line.startswith("{")][-1])

    assert alpha_secret not in output
    assert dart_secret not in output
    assert "<redacted>" in output
    assert alpha_secret not in json.dumps(payload, ensure_ascii=False)
    assert dart_secret not in json.dumps(payload, ensure_ascii=False)
    assert payload["error"].endswith("apikey=<redacted>")
