# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import foreign_block_market_data_service as service
from core.task_errors import UserFacingTaskError
from infra.tasks.lifecycle import TaskCancelledError


class _WaitToken:
    def __init__(self, *, cancelled: bool):
        self.cancelled = cancelled
        self.calls = []

    def wait(self, seconds):
        self.calls.append(("wait", seconds))
        return self.cancelled

    def raise_if_cancelled(self):
        self.calls.append(("raise",))
        raise TaskCancelledError("cancelled_during_wait")


def test_cooperative_wait_supports_sleep_wait_and_cancellation(monkeypatch):
    sleeps = []
    monkeypatch.setattr(service.time, "sleep", sleeps.append)

    service._cooperative_wait(None, 0.25)
    assert sleeps == [0.25]

    active = _WaitToken(cancelled=False)
    service._cooperative_wait(active, 0.5)
    assert active.calls == [("wait", 0.5)]

    cancelled = _WaitToken(cancelled=True)
    with pytest.raises(TaskCancelledError, match="cancelled_during_wait"):
        service._cooperative_wait(cancelled, 1.0)
    assert cancelled.calls == [("wait", 1.0), ("raise",)]


def test_ai_chain_filter_resolves_once_and_fails_closed(monkeypatch):
    from app.services import ui_industry_chain_service

    calls = []

    def _filter(rows, **kwargs):
        calls.append((rows, kwargs))
        return list(rows)

    monkeypatch.setattr(ui_industry_chain_service, "filter_rows_to_ai_chain_codes", _filter)
    monkeypatch.setattr(service, "_filter_rows_to_ai_chain_codes", None)

    assert service.filter_foreign_block_rows_to_ai_chain([{"代码": "000001"}]) == [{"代码": "000001"}]
    assert service._resolve_filter_rows_to_ai_chain_codes() is _filter
    assert len(calls) == 1

    monkeypatch.setattr(
        service,
        "_filter_rows_to_ai_chain_codes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("pool unavailable")),
    )
    assert service.filter_foreign_block_rows_to_ai_chain([{"代码": "000001"}]) == []


def test_trade_date_and_numeric_normalization_covers_invalid_inputs():
    assert service.normalize_trade_date_value(None) == ""
    assert service.normalize_trade_date_value(pd.NaT) == ""
    assert service.normalize_trade_date_value(dt.date(2026, 7, 16)) == "2026-07-16"
    assert service.normalize_trade_date_value("  ") == ""
    assert service.normalize_trade_date_value("NaN") == ""
    assert service.normalize_trade_date_value("123") == "123"
    assert service.normalize_trade_date_value("99999999") == "99999999"
    assert service.normalize_trade_date_value("not-a-date") == "not-a-date"
    assert service._safe_float("bad-number") == 0.0
    assert service._safe_float(None) == 0.0


def test_cache_row_builder_rejects_incomplete_provider_contracts():
    assert service.build_foreign_block_cache_rows([]) == []

    with pytest.raises(ValueError, match="missing columns"):
        service.build_foreign_block_cache_rows([{"交易日期": "2026-07-16"}])

    required_only = {
        "交易日期": "2026-07-16",
        "证券代码": "1",
        "买方营业部": "高盛上海营业部",
        "卖方营业部": "普通营业部",
        "证券简称": "测试",
    }
    with pytest.raises(ValueError, match="missing numeric columns"):
        service.build_foreign_block_cache_rows([required_only])


@pytest.mark.parametrize(
    ("error", "expected_days"),
    [
        (service.ProcessTimeoutError("calendar", 1), 15),
        (ValueError("bad calendar"), 15),
    ],
)
def test_start_date_falls_back_to_natural_days_on_calendar_failure(monkeypatch, error, expected_days):
    end = dt.datetime(2026, 7, 16, 20, 0)
    monkeypatch.setattr(service, "fetch_trade_calendar", lambda **_kwargs: (_ for _ in ()).throw(error))

    start = service._resolve_start_date(end, 10, service.time.monotonic() + 30)

    assert (end - start).days == expected_days


def test_start_date_propagates_cooperative_cancellation(monkeypatch):
    error = TaskCancelledError("calendar_cancelled")
    monkeypatch.setattr(service, "fetch_trade_calendar", lambda **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(TaskCancelledError, match="calendar_cancelled"):
        service._resolve_start_date(dt.datetime(2026, 7, 16), 10, service.time.monotonic() + 30)


def test_fetch_chunk_distinguishes_deadline_timeout_failure_and_cancellation(monkeypatch):
    window = (dt.datetime(2026, 7, 1), dt.datetime(2026, 7, 15))
    assert service._chunk_key(window) == "20260701-20260715"

    rows, status = service._fetch_chunk(window, deadline=service.time.monotonic() - 1)
    assert rows == []
    assert status == "timeout"

    monkeypatch.setattr(service, "BLOCK_TRADE_MAX_RETRIES", 2)
    waits = []
    monkeypatch.setattr(service, "_cooperative_wait", lambda token, seconds: waits.append((token, seconds)))
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    monkeypatch.setattr(
        service,
        "fetch_block_trades",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(service.ProcessTimeoutError("block", 1)),
    )
    rows, status = service._fetch_chunk(window, deadline=service.time.monotonic() + 30, cancellation_token=token)
    assert (rows, status) == ([], "timeout")
    assert waits == [(token, 1)]

    monkeypatch.setattr(
        service,
        "fetch_block_trades",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
    )
    rows, status = service._fetch_chunk(window, deadline=service.time.monotonic() + 30)
    assert (rows, status) == ([], "failed")

    monkeypatch.setattr(
        service,
        "fetch_block_trades",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskCancelledError("chunk_cancelled")),
    )
    with pytest.raises(TaskCancelledError, match="chunk_cancelled"):
        service._fetch_chunk(window, deadline=service.time.monotonic() + 30)


def test_fetch_records_tracks_mixed_chunk_outcomes_and_deadline(monkeypatch):
    windows = [
        (dt.datetime(2026, 7, 1), dt.datetime(2026, 7, 5)),
        (dt.datetime(2026, 7, 6), dt.datetime(2026, 7, 10)),
        (dt.datetime(2026, 7, 11), dt.datetime(2026, 7, 15)),
    ]
    monkeypatch.setattr(service, "_deadline", lambda _token=None: 100.0)
    monkeypatch.setattr(service, "_resolve_start_date", lambda *_args, **_kwargs: windows[0][0])
    monkeypatch.setattr(service, "_chunk_windows", lambda *_args, **_kwargs: windows)
    monkeypatch.setattr(service.time, "monotonic", lambda: 0.0)
    outcomes = iter(
        [
            ([{"证券代码": "000001"}], "ok"),
            ([], "timeout"),
            ([], "failed"),
        ]
    )
    monkeypatch.setattr(service, "_fetch_chunk", lambda *_args, **_kwargs: next(outcomes))

    payload = service._fetch_records(days_to_fetch=15, latest_first=True)

    assert payload == {
        "records": [{"证券代码": "000001"}],
        "timeout_chunks": ["20260706-20260710"],
        "failed_chunks": ["20260711-20260715"],
    }

    monkeypatch.setattr(service, "_fetch_chunk", lambda *_args, **_kwargs: ([], "failed"))
    with pytest.raises(UserFacingTaskError):
        service._fetch_records(days_to_fetch=15, latest_first=True)

    monkeypatch.setattr(service, "_deadline", lambda _token=None: -1.0)
    deadline_payload = service._fetch_records(days_to_fetch=15, latest_first=True)
    assert deadline_payload["records"] == []
    assert deadline_payload["timeout_chunks"] == [service._chunk_key(window) for window in windows]
    assert deadline_payload["failed_chunks"] == []


def test_payload_projection_and_incomplete_message(monkeypatch):
    monkeypatch.setattr(
        service,
        "_fetch_records",
        lambda **_kwargs: {
            "records": [{"证券代码": "000001"}],
            "timeout_chunks": ["a"],
            "failed_chunks": [],
        },
    )
    monkeypatch.setattr(
        service,
        "build_foreign_block_trade_rows",
        lambda records: ([{"代码": records[0]["证券代码"]}], 1),
    )

    payload = service.fetch_foreign_block_payload(30)

    assert payload["row_data"] == [{"代码": "000001"}]
    assert payload["grouped_count"] == 1
    assert service.format_incomplete_message([], []) == ""
    assert "1 个区间超时" in service.format_incomplete_message(["a"], [])
    assert "2 个区间失败" in service.format_incomplete_message([], ["a", "b"])
    message = service.format_incomplete_message(["a"], ["b"])
    assert "超时" in message and "失败" in message
