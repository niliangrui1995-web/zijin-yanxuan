from __future__ import annotations

import datetime as dt
import json
import logging

import pytest
import requests

from domains.global_earnings_calendar.http_utils import (
    raise_for_status,
    redact_sensitive_data,
    redact_sensitive_text,
)
from domains.global_earnings_calendar.models import OligarchCompany
from domains.global_earnings_calendar.providers.alpha_vantage import AlphaVantageEarningsCalendarProvider
from domains.global_earnings_calendar.providers.asia_disclosures import DartEarningsDisclosureProvider
from domains.global_earnings_calendar.service import GlobalEarningsCalendarService

ALPHA_SECRET = "ALPHA_SECRET_123"
DART_SECRET = "DART_SECRET_456"


class _MemoryStore:
    def __init__(self) -> None:
        self.data = {}

    def load_json(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default), ensure_ascii=False))

    def save_json(self, key, data) -> None:
        self.data[key] = json.loads(json.dumps(data, ensure_ascii=False))


class _EmptyProvider:
    def fetch(self, *_args, **_kwargs):
        return []


class _LeakingResponse:
    status_code = 401

    def raise_for_status(self) -> None:
        raise requests.HTTPError(
            "401 Client Error: url: "
            f"https://example.test/query?apikey={ALPHA_SECRET}&crtfc_key={DART_SECRET}&symbol=NVDA"
        )


def test_http_error_and_nested_cache_payload_are_redacted():
    with pytest.raises(requests.HTTPError) as caught:
        raise_for_status(_LeakingResponse())

    error_text = str(caught.value)
    assert ALPHA_SECRET not in error_text
    assert DART_SECRET not in error_text
    assert "<redacted>" in error_text

    nested = redact_sensitive_data(
        {
            "sample_error": str(_LeakingResponse().raise_for_status),
            "details": [
                {
                    "api_key": ALPHA_SECRET,
                    "X-API-Key": ALPHA_SECRET,
                    "crtfc_key": DART_SECRET,
                    "message": f"token={DART_SECRET}",
                }
            ],
        }
    )
    serialized = json.dumps(nested, ensure_ascii=False)
    assert ALPHA_SECRET not in serialized
    assert DART_SECRET not in serialized
    assert redact_sensitive_text(f"Authorization: Bearer {ALPHA_SECRET}").endswith("<redacted>")


def test_provider_request_keeps_secret_for_upstream_but_redacts_failure(public_dns_resolution):
    class _Session:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _LeakingResponse()

    alpha_session = _Session()
    alpha = AlphaVantageEarningsCalendarProvider(
        api_key=ALPHA_SECRET,
        session=alpha_session,
        base_url="https://example.test/query",
    )
    alpha_universe = {
        "NVDA": OligarchCompany("Nvidia", "NVDA", "AI", "strategic", "US")
    }

    with pytest.raises(requests.HTTPError) as alpha_error:
        alpha.fetch(alpha_universe)

    assert alpha_session.calls[0][1]["params"]["apikey"] == ALPHA_SECRET
    assert ALPHA_SECRET not in str(alpha_error.value)
    assert DART_SECRET not in str(alpha_error.value)

    dart_session = _Session()
    dart = DartEarningsDisclosureProvider(
        api_key=DART_SECRET,
        session=dart_session,
        base_url="https://example.test/list.json",
    )
    dart_universe = {
        "005930.KS": OligarchCompany("Samsung", "005930.KS", "Memory", "strategic", "KR")
    }

    with pytest.raises(requests.HTTPError) as dart_error:
        dart.fetch(dart_universe, today=dt.date(2026, 7, 12), lookahead_days=1)

    assert dart_session.calls[0][1]["params"]["crtfc_key"] == DART_SECRET
    assert ALPHA_SECRET not in str(dart_error.value)
    assert DART_SECRET not in str(dart_error.value)


def test_service_redacts_provider_logs_and_persisted_cache_state():
    class _LeakingProvider:
        def fetch(self, *_args, **_kwargs):
            _LeakingResponse().raise_for_status()

    store = _MemoryStore()
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={},
        confirmed_provider=_EmptyProvider(),
        nasdaq_provider=_EmptyProvider(),
        provider=_EmptyProvider(),
        yfinance_provider=_EmptyProvider(),
        official_providers=[("Leaking", _LeakingProvider())],
    )

    messages: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger(GlobalEarningsCalendarService.__module__)
    handler = _CaptureHandler()
    logger.addHandler(handler)
    try:
        assert service.refresh_events(today=dt.date(2026, 7, 12), lookahead_days=1) == []
    finally:
        logger.removeHandler(handler)

    persisted = json.dumps(store.data, ensure_ascii=False)
    log_text = "\n".join(messages)
    assert ALPHA_SECRET not in persisted
    assert DART_SECRET not in persisted
    assert ALPHA_SECRET not in log_text
    assert DART_SECRET not in log_text
    assert "<redacted>" in persisted
    assert "<redacted>" in log_text

    state = service.mark_refresh_failed(
        RuntimeError(f"retry https://example.test?api_key={ALPHA_SECRET}&token={DART_SECRET}")
    )
    serialized_state = json.dumps(state, ensure_ascii=False)
    assert ALPHA_SECRET not in serialized_state
    assert DART_SECRET not in serialized_state
    assert "<redacted>" in serialized_state


def test_service_rewrites_historical_cache_that_contains_credentials():
    store = _MemoryStore()
    store.data["global_earnings_calendar"] = {
        "events": [],
        "cache_state": {
            "api_key": ALPHA_SECRET,
            "sample_error": f"failed https://example.test?crtfc_key={DART_SECRET}",
        },
    }
    service = GlobalEarningsCalendarService(
        data_store=store,
        universe={},
        confirmed_provider=_EmptyProvider(),
        nasdaq_provider=_EmptyProvider(),
        provider=_EmptyProvider(),
        yfinance_provider=_EmptyProvider(),
        official_providers=[],
    )

    status = service.load_cache_status()

    persisted = json.dumps(store.data["global_earnings_calendar"], ensure_ascii=False)
    assert ALPHA_SECRET not in persisted
    assert DART_SECRET not in persisted
    assert status["api_key"] == "<redacted>"
    assert status["sample_error"].endswith("crtfc_key=<redacted>")
