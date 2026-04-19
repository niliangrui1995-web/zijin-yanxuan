# -*- coding: utf-8 -*-
from datetime import date as real_date

from core import fund_holdings_sync as sync_module


class _FakeQ2Date(real_date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 10)


class _FakeQ1Date(real_date):
    @classmethod
    def today(cls):
        return cls(2026, 2, 10)


def test_candidate_qfii_payloads_only_fetches_current_and_previous_quarter(monkeypatch):
    calls = []

    def _fake_fetch(quarter_key: str) -> dict:
        calls.append(quarter_key)
        return {
            "quarter_key": quarter_key,
            "end_date": sync_module.quarter_end_date_text(quarter_key),
            "raw_rows": [],
        }

    monkeypatch.setattr(sync_module, "date", _FakeQ2Date)
    monkeypatch.setattr(sync_module, "_fetch_qfii_quarter", _fake_fetch)

    quarter_payloads, resolved = sync_module._candidate_qfii_payloads()

    assert resolved == "2026Q2"
    assert list(quarter_payloads.keys()) == ["2026Q2", "2026Q1"]
    assert calls == ["2026Q2", "2026Q1"]


def test_candidate_ruiyuan_payloads_only_keeps_current_and_previous_quarter(monkeypatch):
    calls = []

    def _fake_fetch_year(year: int) -> dict[str, dict]:
        calls.append(year)
        if year == 2026:
            return {
                "2026Q1": {
                    "quarter_key": "2026Q1",
                    "end_date": "2026-03-31",
                    "raw_rows": [],
                }
            }
        if year == 2025:
            return {
                "2025Q4": {
                    "quarter_key": "2025Q4",
                    "end_date": "2025-12-31",
                    "raw_rows": [{"stock_code": "000001"}],
                },
                "2025Q3": {
                    "quarter_key": "2025Q3",
                    "end_date": "2025-09-30",
                    "raw_rows": [{"stock_code": "000002"}],
                },
            }
        return {}

    monkeypatch.setattr(sync_module, "date", _FakeQ1Date)
    monkeypatch.setattr(sync_module, "_fetch_ruiyuan_year", _fake_fetch_year)

    quarter_payloads, resolved = sync_module._candidate_ruiyuan_payloads()

    assert resolved == "2026Q1"
    assert list(quarter_payloads.keys()) == ["2026Q1", "2025Q4"]
    assert calls == [2026, 2025]
    assert quarter_payloads["2026Q1"]["raw_rows"] == []
    assert len(quarter_payloads["2025Q4"]["raw_rows"]) == 1
