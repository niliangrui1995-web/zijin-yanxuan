# -*- coding: utf-8 -*-
from datetime import date as real_date

import pytest

from core import fund_holdings_sync as sync_module
from core.task_errors import UserFacingTaskError


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


class _RecordingStore:
    def __init__(self):
        self.qfii_calls = []
        self.ruiyuan_calls = []

    def replace_qfii_quarters(self, *args, **kwargs):
        self.qfii_calls.append((args, kwargs))

    def replace_ruiyuan_quarters(self, *args, **kwargs):
        self.ruiyuan_calls.append((args, kwargs))


def test_sync_qfii_writes_only_available_payloads_with_meta(monkeypatch):
    payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": [
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "Ping An",
                    "HOLDER_NAME": "Holder A",
                    "HOLD_NUM": 1000,
                    "HOLDER_MARKET_CAP": 2000,
                    "FREE_HOLDNUM_RATIO": 0.5,
                    "HOLD_RATIO": 0.1,
                    "UPDATE_DATE": "2026-04-20",
                }
            ],
        },
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": [],
        },
    }
    monkeypatch.setattr(sync_module, "_candidate_qfii_payloads", lambda quarter_key=None: (payloads, "2025Q4"))
    store = _RecordingStore()

    result = sync_module.FundHoldingsSyncService(store=store).sync_qfii("2025Q4")

    assert result["resolved_quarter_key"] == "2025Q4"
    assert result["raw_count"] == 1
    assert result["snapshot_count"] == 1
    assert len(store.qfii_calls) == 1
    _subject, available_payloads = store.qfii_calls[0][0][:2]
    meta = store.qfii_calls[0][1]["payload_meta"]
    assert set(available_payloads) == {"2025Q4"}
    assert len(available_payloads["2025Q4"]["snapshots"]) == 1
    assert meta["raw_counts"] == {"2025Q4": 1, "2025Q3": 0}


def test_sync_ruiyuan_writes_snapshots_and_latest_all_combines_results(monkeypatch):
    ruiyuan_payloads = {
        "2025Q4": {
            "quarter_key": "2025Q4",
            "end_date": "2025-12-31",
            "raw_rows": [
                {
                    "stock_code": "000001",
                    "stock_name": "Ping An",
                    "hold_num_shares": 1000,
                    "hold_market_value_cny": 3000,
                    "net_value_ratio_pct": 2.5,
                    "latest_source_update": "2026-04-20",
                }
            ],
        },
        "2025Q3": {
            "quarter_key": "2025Q3",
            "end_date": "2025-09-30",
            "raw_rows": [],
        },
    }
    monkeypatch.setattr(sync_module, "_candidate_ruiyuan_payloads", lambda quarter_key=None: (ruiyuan_payloads, "2025Q4"))
    monkeypatch.setattr(
        sync_module,
        "_candidate_qfii_payloads",
        lambda quarter_key=None: (
            {
                "2025Q4": {
                    "quarter_key": "2025Q4",
                    "end_date": "2025-12-31",
                    "raw_rows": [
                        {
                            "SECURITY_CODE": "000001",
                            "SECURITY_NAME_ABBR": "Ping An",
                            "HOLDER_NAME": "Holder A",
                            "HOLD_NUM": 1000,
                            "HOLDER_MARKET_CAP": 2000,
                            "FREE_HOLDNUM_RATIO": 0.5,
                            "HOLD_RATIO": 0.1,
                            "UPDATE_DATE": "2026-04-20",
                        }
                    ],
                }
            },
            "2025Q4",
        ),
    )
    store = _RecordingStore()
    service = sync_module.FundHoldingsSyncService(store=store)

    result = service.sync_latest_all()

    assert result["subject_code"] == "ALL"
    assert len(result["results"]) == 2
    assert len(store.qfii_calls) == 1
    assert len(store.ruiyuan_calls) == 1
    _subject, available_payloads = store.ruiyuan_calls[0][0][:2]
    meta = store.ruiyuan_calls[0][1]["payload_meta"]
    assert set(available_payloads) == {"2025Q4"}
    assert len(available_payloads["2025Q4"]["snapshots"]) == 1
    assert meta["raw_counts"] == {"2025Q4": 1, "2025Q3": 0}


def test_fetch_text_closes_response_and_wraps_network_errors(monkeypatch):
    class _Response:
        def __init__(self):
            self.closed = False

        def read(self, size=-1):
            return "成功".encode("utf-8")

        def close(self):
            self.closed = True

    response = _Response()
    monkeypatch.setattr(sync_module, "urlopen_https", lambda request, timeout=15: response)

    assert sync_module._fetch_text("https://example.test", params={"a": "1"}) == "成功"
    assert response.closed is True

    monkeypatch.setattr(sync_module, "urlopen_https", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(UserFacingTaskError):
        sync_module._fetch_text("https://example.test")


def test_fetch_text_rejects_oversized_response(monkeypatch):
    class _Response:
        def read(self, size=-1):
            return b"a" * (size + 1)

        def close(self):
            pass

    monkeypatch.setattr(sync_module, "urlopen_https", lambda request, timeout=15: _Response())
    monkeypatch.setattr(sync_module, "_MAX_RESPONSE_BYTES", 8)

    with pytest.raises(UserFacingTaskError):
        sync_module._fetch_text("https://example.test")


def test_fetch_json_wraps_decode_errors(monkeypatch):
    monkeypatch.setattr(sync_module, "_fetch_text", lambda *_args, **_kwargs: "{bad")

    with pytest.raises(UserFacingTaskError) as exc_info:
        sync_module._fetch_json("https://example.test", params={})
    assert "JSON" in exc_info.value.log_message


def test_parse_ruiyuan_sections_cleans_js_html_and_numbers():
    html = (
        "<h4 class='t'>截止至：<font class='px12'>2025-12-31</font>2025年4季度股票投资明细</h4>"
        "<table><tr>"
        "<td>1</td><td><a>000001</a></td><td><a>平安银行</a></td>"
        "<td>x</td><td class='tor'>2.5%</td><td class='tor'>1,200</td><td class='tor'>3,400</td>"
        "</tr></table>"
    )
    raw = 'var apidata={content:"' + html.replace('"', '\\"') + '",arryear:[]};'

    payloads = sync_module._parse_ruiyuan_sections(raw)

    assert list(payloads) == ["2025Q4"]
    row = payloads["2025Q4"]["raw_rows"][0]
    assert row["stock_code"] == "000001"
    assert row["stock_name"] == "平安银行"
    assert row["net_value_ratio_pct"] == 2.5
    assert row["hold_num_shares"] == 12_000_000.0
    assert row["hold_market_value_cny"] == 34_000_000.0


def test_parse_ruiyuan_sections_rejects_missing_content():
    with pytest.raises(UserFacingTaskError):
        sync_module._parse_ruiyuan_sections("no content")


def test_fetch_qfii_quarter_paginates_until_last_page(monkeypatch):
    calls = []

    def fake_fetch_json(_url, *, params, referer=""):
        calls.append(params["pageNumber"])
        if params["pageNumber"] == "1":
            return {"result": {"pages": 2, "data": [{"SECURITY_CODE": "000001"}]}}
        return {"result": {"pages": 2, "data": [{"SECURITY_CODE": "000002"}]}}

    monkeypatch.setattr(sync_module, "_fetch_json", fake_fetch_json)

    payload = sync_module._fetch_qfii_quarter("2025Q4")

    assert calls == ["1", "2"]
    assert payload["quarter_key"] == "2025Q4"
    assert [row["SECURITY_CODE"] for row in payload["raw_rows"]] == ["000001", "000002"]


def test_fetch_qfii_quarter_rejects_excessive_pages(monkeypatch):
    calls = []

    def fake_fetch_json(_url, *, params, referer=""):
        calls.append(params["pageNumber"])
        return {"result": {"pages": sync_module._QFII_MAX_PAGES + 1, "data": [{"SECURITY_CODE": "000001"}]}}

    monkeypatch.setattr(sync_module, "_fetch_json", fake_fetch_json)

    with pytest.raises(UserFacingTaskError):
        sync_module._fetch_qfii_quarter("2025Q4")

    assert calls == ["1"]


def test_fetch_qfii_quarter_rejects_excessive_rows(monkeypatch):
    def fake_fetch_json(_url, *, params, referer=""):
        return {"result": {"pages": 1, "data": [{"SECURITY_CODE": "000001"}, {"SECURITY_CODE": "000002"}]}}

    monkeypatch.setattr(sync_module, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(sync_module, "_QFII_MAX_ROWS", 1)

    with pytest.raises(UserFacingTaskError):
        sync_module._fetch_qfii_quarter("2025Q4")


def test_candidate_specific_payloads_raise_when_current_quarter_missing(monkeypatch):
    monkeypatch.setattr(
        sync_module,
        "_fetch_qfii_quarter",
        lambda quarter_key: {
            "quarter_key": quarter_key,
            "end_date": sync_module.quarter_end_date_text(quarter_key),
            "raw_rows": [],
        },
    )
    with pytest.raises(UserFacingTaskError):
        sync_module._candidate_qfii_payloads("2025Q4")

    monkeypatch.setattr(sync_module, "_fetch_ruiyuan_year", lambda year: {})
    with pytest.raises(UserFacingTaskError):
        sync_module._candidate_ruiyuan_payloads("2025Q4")
