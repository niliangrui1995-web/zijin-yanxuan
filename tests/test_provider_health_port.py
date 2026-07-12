# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services import ui_quote_service
from infra.market_data import provider_ports
from infra.market_data.tdx_data_provider import TdxDataProvider
from ui.tabs.base_stock_tab import BaseStockTab
from ui.tabs.fund_holdings_tab import FundHoldingsTab
from ui.tabs.lhb_tab import LhbTab
from ui.tabs.scan_tab import ScanTab
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.tabs.watchlist_tab import WatchlistTab


def _snapshot(**overrides):
    snapshot_type = provider_ports.ProviderHealthSnapshot
    values = {
        "request_stats": {"recent_status": "runtime_cache_hit"},
        "runtime_stats": {"state": "ready"},
        "eastmoney_cooldown_until": 123.5,
        "eastmoney_last_error": "edge timeout",
    }
    values.update(overrides)
    return snapshot_type(**values)


def test_provider_health_snapshot_is_deeply_read_only():
    snapshot = _snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.eastmoney_last_error = "changed"
    with pytest.raises(TypeError):
        snapshot.request_stats["recent_status"] = "changed"
    with pytest.raises(TypeError):
        snapshot.runtime_stats["state"] = "changed"

    assert snapshot.as_dict() == {
        "request_stats": {"recent_status": "runtime_cache_hit"},
        "runtime_stats": {"state": "ready"},
        "eastmoney_cooldown_until": 123.5,
        "eastmoney_last_error": "edge timeout",
    }


def test_provider_health_snapshot_freezes_nested_collections():
    snapshot = _snapshot(
        request_stats={"recent_batches": [{"codes": ["000001"]}]},
        runtime_stats={"workers": {"quote"}},
    )

    with pytest.raises(TypeError):
        snapshot.request_stats["recent_batches"][0]["codes"][0] = "000002"
    with pytest.raises(AttributeError):
        snapshot.runtime_stats["workers"].add("poller")

    payload = snapshot.as_dict()
    payload["request_stats"]["recent_batches"][0]["codes"][0] = "000002"
    assert snapshot.request_stats["recent_batches"][0]["codes"][0] == "000001"


def test_tdx_provider_exposes_typed_provider_health_snapshot():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider.get_quote_request_stats = lambda: {"recent_status": "network_success"}
    provider.get_realtime_runtime_stats = lambda: {"state": "ready", "inflight": 0}
    provider._rt_eastmoney_cooldown_until = 456.0
    provider._rt_eastmoney_last_error = ""

    snapshot = provider.read_provider_health()

    assert isinstance(snapshot, provider_ports.ProviderHealthSnapshot)
    assert snapshot.request_stats["recent_status"] == "network_success"
    assert snapshot.runtime_stats["inflight"] == 0
    assert snapshot.eastmoney_cooldown_until == 456.0


def test_tdx_provider_exposes_public_adjustment_metadata_port():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    calls = []
    provider._load_local_gbbq = lambda force=False: calls.append(force) or {"000001": "adjusted"}

    assert provider.ensure_adjustment_metadata(force=True) == {"000001": "adjusted"}
    assert calls == [True]


def test_tdx_provider_exposes_public_quote_policy_and_offline_quote_ports():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider._rt_api_call_timeout_sec = 3.5
    provider._rt_quote_batch_size = 12
    provider._build_offline_quotes = lambda codes: {code: {"close": 10.0} for code in codes}

    policy = provider.read_realtime_quote_request_policy()

    assert policy == provider_ports.RealtimeQuoteRequestPolicy(api_call_timeout_sec=3.5, batch_size=12)
    assert provider.build_offline_quotes(["000001"]) == {"000001": {"close": 10.0}}


def test_ui_quote_service_uses_public_quote_policy_and_offline_quote_ports():
    expected_policy = provider_ports.RealtimeQuoteRequestPolicy(api_call_timeout_sec=2.0, batch_size=5)

    class PublicProvider:
        @staticmethod
        def read_realtime_quote_request_policy():
            return expected_policy

        @staticmethod
        def build_offline_quotes(codes):
            return {code: {"close": 9.5} for code in codes}

    provider = PublicProvider()

    assert ui_quote_service.read_realtime_quote_request_policy(provider) == expected_policy
    assert ui_quote_service.build_offline_quotes(provider, ["000001"]) == {"000001": {"close": 9.5}}


def test_ui_quote_health_reader_returns_empty_snapshot_on_port_failure():
    class BrokenProvider:
        @staticmethod
        def read_provider_health():
            raise RuntimeError("provider is closing")

    snapshot = ui_quote_service.read_provider_health(BrokenProvider())

    assert isinstance(snapshot, provider_ports.ProviderHealthSnapshot)
    assert snapshot.as_dict() == {
        "request_stats": {},
        "runtime_stats": {},
        "eastmoney_cooldown_until": 0.0,
        "eastmoney_last_error": "",
    }


def test_base_stock_tab_reads_only_public_provider_health_port(qt_application):
    class PublicProvider:
        @staticmethod
        def read_provider_health():
            return _snapshot()

        @property
        def _rt_eastmoney_cooldown_until(self):
            raise AssertionError("UI must not read provider private fields")

        @property
        def _rt_eastmoney_last_error(self):
            raise AssertionError("UI must not read provider private fields")

    tab = BaseStockTab(data_provider=PublicProvider())
    try:
        assert tab._read_provider_status() == _snapshot().as_dict()
    finally:
        tab.deleteLater()


@pytest.mark.parametrize(
    "tab_type",
    [FundHoldingsTab, LhbTab, ScanTab, StockCandidateTab, WatchlistTab],
)
def test_stock_tabs_inherit_one_provider_health_reader(tab_type):
    assert "_read_provider_status" not in tab_type.__dict__
    assert tab_type._read_provider_status is BaseStockTab._read_provider_status
