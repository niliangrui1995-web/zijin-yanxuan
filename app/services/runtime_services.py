# -*- coding: utf-8 -*-
from __future__ import annotations

from core.startup_orchestrator import StartupOrchestrator
from infra.market_data.tdx_data_provider import TdxDataProvider


def create_data_provider(*, offline: bool = True) -> TdxDataProvider:
    provider = TdxDataProvider(offline=offline)
    cached_code_names = {}
    load_cached_code_name_map = getattr(provider, "load_cached_code_name_map", None)
    if callable(load_cached_code_name_map):
        cached_code_names = load_cached_code_name_map()
    provider.code2name = cached_code_names or provider.ensure_code_name_map()
    return provider


def create_startup_orchestrator(main_window, job_runner=None) -> StartupOrchestrator:
    return StartupOrchestrator(main_window, job_runner=job_runner)


def load_local_tdx_capital_snapshot(codes, tdx_vipdoc: str | None) -> dict[str, dict]:
    from vcp.data_provider_local import load_local_tdx_capital_snapshot as _load_snapshot

    return _load_snapshot(codes, tdx_vipdoc)
