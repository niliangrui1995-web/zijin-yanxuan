# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from core.market_snapshot_dates import (
    _frame_latest_trade_date,
    infer_effective_trade_date,
    normalize_trade_date,
)


class _BrokenDate:
    def strftime(self, _format):
        raise ValueError("invalid date")


class _BrokenDatetimeFrame:
    columns = ("datetime",)
    index = SimpleNamespace(max=lambda: "2026-07-16")

    def __getitem__(self, _key):
        raise KeyError("datetime")


class _BrokenIndexFrame:
    columns = ()

    @property
    def index(self):
        raise RuntimeError("index unavailable")


def test_normalize_trade_date_fails_closed_for_missing_and_invalid_date_values():
    assert normalize_trade_date(None) == ""
    assert normalize_trade_date(_BrokenDate()) == ""


def test_frame_latest_trade_date_uses_index_when_datetime_column_is_absent():
    frame = SimpleNamespace(
        columns=(),
        index=SimpleNamespace(max=lambda: "2026-07-17"),
    )

    assert _frame_latest_trade_date(frame) == "20260717"


def test_frame_latest_trade_date_falls_back_when_datetime_column_cannot_be_read():
    assert _frame_latest_trade_date(_BrokenDatetimeFrame()) == "20260716"


def test_frame_latest_trade_date_fails_closed_when_index_is_unavailable():
    assert _frame_latest_trade_date(_BrokenIndexFrame()) == ""


def test_infer_effective_trade_date_handles_empty_cache_and_latest_tie():
    assert infer_effective_trade_date(None) == ""

    older = SimpleNamespace(columns=(), index=SimpleNamespace(max=lambda: "2026-07-16"))
    newer = SimpleNamespace(columns=(), index=SimpleNamespace(max=lambda: "2026-07-17"))

    assert infer_effective_trade_date({"older": older, "newer": newer}) == "20260717"
