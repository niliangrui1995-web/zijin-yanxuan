from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "canonical_name", "export_name"),
    [
        ("core.startup_orchestrator", "app.bootstrap.startup_orchestrator", "StartupOrchestrator"),
        ("core.lhb_pool_manager", "app.services.ui_lhb_pool_service", "LhbPoolManager"),
        ("core.ai_industry_chain_pool", "app.services.ui_industry_chain_service", "load_ai_industry_chain_rows"),
        ("core.fund_holdings_sync", "domains.fund_holdings.sync", "FundHoldingsSyncService"),
        ("vcp.engine", "app.services.scan_engine_facade", "VCPEngine"),
        ("vcp.data_provider", "infra.market_data.tdx_data_provider", "TdxDataProvider"),
        ("ui.services.na_daily_service", "app.services.na_daily_service", "NADailyRefreshService"),
    ],
)
def test_legacy_facade_keeps_its_own_module_identity(legacy_name, canonical_name, export_name):
    legacy_module = importlib.import_module(legacy_name)
    canonical_module = importlib.import_module(canonical_name)

    assert legacy_module is not canonical_module
    assert getattr(legacy_module, export_name) is getattr(canonical_module, export_name)
