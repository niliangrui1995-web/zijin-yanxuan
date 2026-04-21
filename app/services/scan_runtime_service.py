# -*- coding: utf-8 -*-
from __future__ import annotations

from domains.scan import BreakoutMonitorService, IndicatorService
from app.services.scan_engine_facade import VCPEngine
from vcp.models import VCPParams


def create_scan_engine():
    return VCPEngine.get_instance()


def calculate_scan_indicators(df, *, include_chart: bool = True):
    return IndicatorService.calculate_indicators(df, include_chart=include_chart)


def batch_get_finance_info(codes):
    return VCPEngine.batch_get_finance_info(codes)


def batch_check_market_cap(codes, *, close_prices=None):
    return VCPEngine.batch_check_market_cap(codes, close_prices=close_prices)


def precompute_ready_pool(
    all_data,
    rps120_series,
    rps250_series,
    params,
    *,
    sector_manager=None,
    sector_rps_dict=None,
    sector_threshold=70,
    server_pool=None,
    code2name=None,
    progress_callback=None,
    cancelled_checker=None,
):
    return BreakoutMonitorService.precompute_ready_pool(
        all_data,
        rps120_series,
        rps250_series,
        params,
        sector_manager=sector_manager,
        sector_rps_dict=sector_rps_dict,
        sector_threshold=sector_threshold,
        server_pool=server_pool,
        code2name=code2name,
        progress_callback=progress_callback,
        cancelled_checker=cancelled_checker,
    )


def quick_check_breakout(quote, pool_entry):
    return BreakoutMonitorService.rt_quick_check(quote, pool_entry)


def build_rps_matrix(all_data, start_date: str, end_date: str, daily_cache: dict | None = None):
    from vcp.polars_engine import build_rps_matrix_pl

    return build_rps_matrix_pl(all_data, start_date, end_date, daily_cache)
