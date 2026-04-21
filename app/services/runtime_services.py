# -*- coding: utf-8 -*-
from __future__ import annotations

from core.startup_orchestrator import StartupOrchestrator
from infra.market_data.tdx_data_provider import TdxDataProvider


def create_data_provider(*, offline: bool = True) -> TdxDataProvider:
    provider = TdxDataProvider(offline=offline)
    provider.code2name = provider.ensure_code_name_map()
    return provider


def create_startup_orchestrator(main_window, job_runner=None) -> StartupOrchestrator:
    return StartupOrchestrator(main_window, job_runner=job_runner)
