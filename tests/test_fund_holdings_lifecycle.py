from __future__ import annotations

import pytest

from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.tabs import fund_holdings_payload as payload_module


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
