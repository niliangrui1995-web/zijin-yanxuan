from __future__ import annotations

import builtins
import sqlite3
from contextlib import closing

import pytest
import requests

from core import market_calendar_holidays as module
from core.exceptions import BusinessRuleError, CacheIOError, DataFormatError, NetworkServiceError


class _Response:
    def __init__(self, payload=None, status_code=200, *, json_error=False):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("invalid json")
        return self.payload


def test_date_helpers_cover_default_years_and_empty_inputs():
    assert module.is_iso_date(" 2026-01-01 ") is True
    assert module.is_iso_date("2026-1-1") is False
    assert module.normalize_holiday_days(None) == set()
    assert module._nth_weekday(2026, 1, 0, 2).isoformat() == "2026-01-12"
    assert module._japan_vernal_equinox_day(1979) == 20
    assert module._japan_autumnal_equinox_day(2100) == 23
    assert module._korea_exchange_holiday_supplements(2025) == set()
    assert module.apply_market_holiday_supplements("unknown", 2026, ["2026-01-01"]) == {"2026-01-01"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (" ", None),
        ("2026-01-02 03:04:05", (2026, 1, 2, 3, 4, 5)),
        ("2026-01-02T03:04:05", (2026, 1, 2, 3, 4, 5)),
        ("invalid", None),
    ],
)
def test_parse_sqlite_timestamp_variants(raw, expected):
    parsed = module.parse_sqlite_ts(raw)
    assert (
        None if parsed is None else (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second)
    ) == expected


def test_holiday_store_update_and_corrupt_rows(tmp_path):
    root = str(tmp_path)
    module.save_holidays_to_store(root, "HK", 2026, {"2026-01-01"})
    module.save_holidays_to_store(root, "HK", 2026, {"2026-02-01"})
    db = module.holiday_db_path(root)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO market_holiday_cache(market, year, days_json, updated_at) VALUES(?,?,?,?)",
            ("HK", 2027, "not-json", "not-a-time"),
        )
        conn.execute(
            "INSERT INTO market_holiday_cache(market, year, days_json, updated_at) VALUES(?,?,?,?)",
            ("HK", 2028, "", "2028-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO market_holiday_cache(market, year, days_json, updated_at) VALUES(?,?,?,?)",
            ("BAD", "not-year", "[]", ""),
        )
        conn.commit()

    rows = module.load_holidays_from_store(root, "HK")

    assert rows[0][1] == {"2026-02-01"}
    assert rows[1][1] == set() and rows[1][2] is None
    assert rows[2][1] == set() and rows[2][2] is not None
    assert module.load_holidays_from_store(root, "BAD") == []


@pytest.mark.parametrize("operation", ["ensure", "load", "save"])
def test_holiday_store_wraps_sqlite_errors(monkeypatch, tmp_path, operation):
    if operation in {"load", "save"}:
        module.ensure_holiday_table(str(tmp_path))
        monkeypatch.setattr(module, "ensure_holiday_table", lambda _root: None)
    monkeypatch.setattr(
        module.sqlite3, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.Error("locked"))
    )

    with pytest.raises(CacheIOError):
        if operation == "ensure":
            module.ensure_holiday_table(str(tmp_path))
        elif operation == "load":
            module.load_holidays_from_store(str(tmp_path), "HK")
        else:
            module.save_holidays_to_store(str(tmp_path), "HK", 2026, {"2026-01-01"})


@pytest.mark.parametrize(
    ("name", "desc", "include", "exclude", "expected"),
    [
        ("", "", ("holiday",), (), False),
        ("holiday", "makeup", ("holiday",), ("makeup",), False),
        ("holiday", "closed", ("holiday",), ("makeup",), True),
        ("workday", "open", ("holiday",), (), False),
    ],
)
def test_twse_row_filter(name, desc, include, exclude, expected):
    assert module.is_twse_holiday_row(name, desc, include, exclude) is expected


def test_fetch_twse_holidays_parses_valid_rows_and_year_shapes(monkeypatch):
    payload = {
        "stat": "success",
        "queryYear": "2026",
        "title": "115 year schedule",
        "data": [
            ["2026-01-01", "holiday", "closed"],
            ["2026-01-02", "workday", "open"],
            ["bad", "holiday"],
            ["2026-01-03", "holiday", "makeup"],
            ["too-short"],
            "bad-row",
        ],
    }
    monkeypatch.setattr(module, "requests_get_https", lambda *_args, **_kwargs: _Response(payload))

    assert module.fetch_twse_holidays(2026, ("holiday",), ("makeup",)) == {"2026-01-01"}

    payload["queryYear"] = 2026
    payload["title"] = "2026 schedule"
    assert module.fetch_twse_holidays(2026, ("holiday",), ("makeup",)) == {"2026-01-01"}

    payload["queryYear"] = None
    payload["title"] = "115\u5e74 schedule"
    assert module.fetch_twse_holidays(2026, ("holiday",), ("makeup",)) == {"2026-01-01"}


@pytest.mark.parametrize(
    ("payload", "status", "json_error", "error"),
    [
        ({"stat": "ok", "queryYear": 2026, "title": "2026", "data": []}, 500, False, NetworkServiceError),
        (None, 200, True, DataFormatError),
        ([], 200, False, DataFormatError),
        ({"stat": "bad", "data": []}, 200, False, DataFormatError),
        ({"stat": "ok", "queryYear": 2025, "title": "2025", "data": []}, 200, False, BusinessRuleError),
        ({"stat": "ok", "queryYear": None, "title": "2025 schedule", "data": []}, 200, False, BusinessRuleError),
        ({"stat": "ok", "queryYear": 2026, "title": "2026", "data": {}}, 200, False, DataFormatError),
    ],
)
def test_fetch_twse_rejects_bad_payloads(monkeypatch, payload, status, json_error, error):
    monkeypatch.setattr(
        module,
        "requests_get_https",
        lambda *_args, **_kwargs: _Response(payload, status, json_error=json_error),
    )
    with pytest.raises(error):
        module.fetch_twse_holidays(2026, ("holiday",), ())


def test_fetch_twse_invalid_year_and_request_error(monkeypatch):
    with pytest.raises(BusinessRuleError):
        module.fetch_twse_holidays(1900, (), ())
    monkeypatch.setattr(
        module,
        "requests_get_https",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.RequestException("offline")),
    )
    with pytest.raises(NetworkServiceError):
        module.fetch_twse_holidays(2026, (), ())


def test_fetch_twse_dependency_error(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(NetworkServiceError):
        module.fetch_twse_holidays(2026, (), ())


def test_fetch_public_holidays_parses_and_supplements(monkeypatch):
    payload = [
        {"date": "2026-01-01T00:00:00"},
        {"date": "bad"},
        {"other": "missing"},
        "invalid",
    ]
    seen = []

    def fake_get(url, timeout):
        seen.append((url, timeout))
        return _Response(payload)

    monkeypatch.setattr(module, "requests_get_https", fake_get)
    result = module.fetch_public_holidays("T", 2026, {"T": "JP"}, (), ())
    assert "2026-01-01" in result
    assert "2026-05-06" in result
    assert seen[0][1] == 15


@pytest.mark.parametrize(
    ("status", "payload", "json_error", "error"),
    [
        (204, [], False, BusinessRuleError),
        (404, [], False, BusinessRuleError),
        (500, [], False, NetworkServiceError),
        (200, None, True, DataFormatError),
        (200, {}, False, DataFormatError),
    ],
)
def test_fetch_public_holidays_rejects_http_and_payload_errors(monkeypatch, status, payload, json_error, error):
    monkeypatch.setattr(
        module,
        "requests_get_https",
        lambda *_args, **_kwargs: _Response(payload, status, json_error=json_error),
    )
    with pytest.raises(error):
        module.fetch_public_holidays("HK", 2026, {"HK": "HK"}, (), ())


def test_fetch_public_holidays_unsupported_request_and_dependency_errors(monkeypatch):
    with pytest.raises(BusinessRuleError):
        module.fetch_public_holidays("XX", 2026, {}, (), ())

    monkeypatch.setattr(
        module,
        "requests_get_https",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.RequestException("offline")),
    )
    with pytest.raises(NetworkServiceError):
        module.fetch_public_holidays("HK", 2026, {"HK": "HK"}, (), ())

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(NetworkServiceError):
        module.fetch_public_holidays("HK", 2026, {"HK": "HK"}, (), ())
