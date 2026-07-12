# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.bootstrap.startup_orchestrator import StartupHostAdapter, StartupOrchestrator
from infra.market_data.tdx_data_provider import TdxDataProvider
from infra.market_data.vcp_scan_adapter import load_local_tdx_capital_snapshot as _load_snapshot


def create_data_provider(*, offline: bool = True) -> TdxDataProvider:
    provider = TdxDataProvider(offline=offline)
    cached_code_names: dict = {}
    load_cached_code_name_map = getattr(provider, "load_cached_code_name_map", None)
    if callable(load_cached_code_name_map):
        loaded = load_cached_code_name_map()
        cached_code_names = loaded if isinstance(loaded, dict) else {}
    if cached_code_names:
        provider.code2name = cached_code_names
    else:
        provider_code_names = provider.ensure_code_name_map()
        provider.code2name = provider_code_names if isinstance(provider_code_names, dict) else {}
    return provider


def create_startup_orchestrator(main_window: Any, job_runner: Any = None) -> StartupOrchestrator:
    return StartupOrchestrator(job_runner=job_runner, host=StartupHostAdapter(main_window))


def load_local_tdx_capital_snapshot(codes: Iterable[str], tdx_vipdoc: str | None) -> dict[str, dict]:
    return _load_snapshot(codes, tdx_vipdoc)
