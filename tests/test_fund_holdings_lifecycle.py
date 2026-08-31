from __future__ import annotations

import pytest

from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.tabs import fund_holdings_payload as payload_module
from ui.tabs import fund_holdings_tab as fund_holdings_tab_module


class _PayloadStore:
    def __init__(self, token: CancellationToken):
        self.token = token
        self.query_calls = 0

    def get_latest_quarter_map(self):
        self.token.cancel("after_quarter_map")
        return {"QFII": "2025Q4"}

    def get_latest_sync_map(self):
        raise AssertionError("cancelled payload must not continue to next store stage")

    def query_change_rows(self, **_kwargs):
        self.query_calls += 1
        return []


def test_fund_holdings_payload_stops_between_store_stages():
    token = CancellationToken()
    store = _PayloadStore(token)

    with pytest.raises(TaskCancelledError, match="after_quarter_map"):
        payload_module.load_fund_holdings_view_payload(
            quarter_scope="latest",
            stock_universe_provider=lambda: set(),
            chain_context_provider=lambda: {},
            capital_attribute_labels={},
            store=store,
            cancellation_token=token,
        )

    assert store.query_calls == 0


def test_fund_holdings_payload_does_not_resolve_default_store_after_cancellation(monkeypatch):
    token = CancellationToken()
    token.cancel("before_default_store")
    calls = []
    monkeypatch.setattr(
        payload_module,
        "_resolve_fund_holdings_store",
        lambda: calls.append("store") or object(),
    )

    with pytest.raises(TaskCancelledError, match="before_default_store"):
        payload_module.load_fund_holdings_view_payload(
            quarter_scope="latest",
            stock_universe_provider=lambda: set(),
            chain_context_provider=lambda: {},
            capital_attribute_labels={},
            cancellation_token=token,
        )

    assert calls == []


def test_fund_holdings_tab_payload_worker_checks_cancellation_before_store_resolution(monkeypatch):
    token = CancellationToken()
    token.cancel("before_tab_payload_store")
    calls = []
    monkeypatch.setattr(
        payload_module,
        "_resolve_fund_holdings_store",
        lambda: calls.append("store") or object(),
    )

    with pytest.raises(TaskCancelledError, match="before_tab_payload_store"):
        fund_holdings_tab_module.FundHoldingsTab._load_view_payload(
            object(),
            cancellation_token=token,
        )

    assert calls == []


def test_fund_holdings_row_builder_stops_inside_row_loop():
    token = CancellationToken()

    class _CancellingRow(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if key == "stock_code":
                token.cancel("row_cancel")
            return value

    class _ForbiddenRow(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("cancelled row builder must not touch later rows")

    with pytest.raises(TaskCancelledError, match="row_cancel"):
        payload_module.build_fund_holdings_view_rows(
            [
                _CancellingRow(stock_code="000001"),
                _ForbiddenRow(stock_code="000002"),
            ],
            latest_quarter_map={},
            chain_context_map={},
            concept_sector_cache={},
            capital_attribute_labels={},
            cancellation_token=token,
        )


def test_fund_holdings_query_pushes_stock_universe_into_store():
    calls = []

    class _Store:
        def query_change_rows(self, *, quarter_keys, stock_codes):
            calls.append((quarter_keys, stock_codes))
            return [
                {"stock_code": "000001", "quarter_key": "2026Q1"},
                {"stock_code": "600000", "quarter_key": "2026Q1"},
            ]

    rows = payload_module.query_change_rows_for_scope(
        {"2026Q1"},
        stock_universe_provider=lambda: {1, "300750.0"},
        store=_Store(),
    )

    assert calls == [({"2026Q1"}, {"000001", "300750"})]
    assert rows == [{"stock_code": "000001", "quarter_key": "2026Q1"}]


def test_fund_holdings_query_keeps_legacy_store_fallback():
    calls = []

    class _LegacyStore:
        def query_change_rows(self, quarter_keys=None):
            calls.append(quarter_keys)
            return [
                {"stock_code": "000001", "quarter_key": "2026Q1"},
                {"stock_code": "600000", "quarter_key": "2026Q1"},
            ]

    rows = payload_module.query_change_rows_for_scope(
        {"2026Q1"},
        stock_universe_provider=lambda: {"000001"},
        store=_LegacyStore(),
    )

    assert calls == [{"2026Q1"}]
    assert rows == [{"stock_code": "000001", "quarter_key": "2026Q1"}]


def test_fund_holdings_payload_keeps_bse_code_when_subject_data_and_ai_pool_match():
    class _Store:
        def get_latest_quarter_map(self):
            return {"QFII": "2026Q2"}

        def get_latest_sync_map(self):
            return {"QFII": {"resolved_quarter_key": "2026Q2"}}

        def query_change_rows(self, *, quarter_keys, stock_codes):
            assert quarter_keys == {"2026Q2"}
            assert stock_codes == {"920045"}
            return [
                {
                    "stock_code": "920045",
                    "stock_name": "蘅东光",
                    "subject_code": "QFII",
                    "subject_name": "示例QFII",
                    "quarter_key": "2026Q2",
                    "change_type": "新进",
                    "curr_hold_num_shares": 12_000,
                    "curr_ratio_pct": 0.12,
                }
            ]

    payload = payload_module.load_fund_holdings_view_payload(
        quarter_scope="latest",
        stock_universe_provider=lambda: {"920045"},
        chain_context_provider=lambda: {"920045": "AI光模块"},
        capital_attribute_labels={"未标注": "未标注"},
        store=_Store(),
    )

    assert len(payload["view_rows"]) == 1
    row = payload["view_rows"][0]
    assert row["代码"] == "920045"
    assert row["名称"] == "蘅东光"
    assert row["主体代码"] == "QFII"
    assert row["概念板块"] == "AI光模块"


def test_qfii_sync_cancellation_before_store_does_not_commit(monkeypatch):
    from domains.fund_holdings import sync as sync_module

    token = CancellationToken()
    store_calls = []

    class _Store:
        def replace_qfii_quarters(self, *_args, **_kwargs):
            store_calls.append("replace")

    monkeypatch.setattr(
        sync_module,
        "_candidate_qfii_payloads",
        lambda _quarter, *, cancellation_token=None: (
            {
                "2025Q4": {
                    "quarter_key": "2025Q4",
                    "end_date": "2025-12-31",
                    "raw_rows": [{"SECURITY_CODE": "000001"}],
                }
            },
            "2025Q4",
        ),
    )

    def _build(*_args, **_kwargs):
        token.cancel("before_store")
        return []

    monkeypatch.setattr(sync_module, "build_qfii_snapshots", _build)
    service = sync_module.FundHoldingsSyncService(store=_Store())

    with pytest.raises(TaskCancelledError, match="before_store"):
        service.sync_qfii("2025Q4", cancellation_token=token)

    assert store_calls == []


def test_sync_latest_all_stops_before_second_provider_after_cancel(monkeypatch):
    from domains.fund_holdings import sync as sync_module

    token = CancellationToken()
    service = sync_module.FundHoldingsSyncService(store=object())
    calls = []

    def _sync_qfii(*, cancellation_token=None):
        calls.append("qfii")
        cancellation_token.cancel("between_providers")
        return {"message": "qfii"}

    def _sync_ruiyuan(*, cancellation_token=None):
        calls.append("ruiyuan")
        return {"message": "ruiyuan"}

    monkeypatch.setattr(service, "sync_qfii", _sync_qfii)
    monkeypatch.setattr(service, "sync_ruiyuan", _sync_ruiyuan)

    with pytest.raises(TaskCancelledError, match="between_providers"):
        service.sync_latest_all(cancellation_token=token)

    assert calls == ["qfii"]
