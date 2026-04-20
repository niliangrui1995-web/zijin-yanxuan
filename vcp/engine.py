# engine.py - 策略中台（VCP 引擎）
from __future__ import annotations

import pandas as pd
import polars as pl

from vcp.breakout_monitor_service import BreakoutMonitorService
from vcp.engine_external import (
    batch_check_institution,
    batch_check_market_cap,
    batch_get_finance_info,
)
from vcp.indicator_service import IndicatorService
from vcp.models import VCPParams
from vcp.rps_service import RpsService
from vcp.vcp_scanner_service import VcpScannerService


class VCPEngine:
    _instance = None

    @classmethod
    def get_instance(cls) -> "VCPEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._rps_service = RpsService()

    @property
    def rps_service(self) -> RpsService:
        service = getattr(self, "_rps_service", None)
        if service is None:
            service = RpsService()
            self._rps_service = service
        return service

    def set_precomputed_rps(self, cache_date: str, rps120, rps250) -> None:
        self.rps_service.set_precomputed_rps(cache_date, rps120, rps250)

    def get_precomputed_rps(self) -> dict | None:
        return self.rps_service.get_precomputed_rps()

    @staticmethod
    def calculate_indicators(
        df: pd.DataFrame | pl.DataFrame,
        include_chart: bool = True,
    ) -> pd.DataFrame | pl.DataFrame:
        return IndicatorService.calculate_indicators(df, include_chart=include_chart)

    @staticmethod
    def _build_prices_matrix(
        data_dict: dict[str, pd.DataFrame],
        min_start: pd.Timestamp,
        end_ts: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        return RpsService.build_prices_matrix(data_dict, min_start, end_ts)

    def build_rps_matrix(
        self,
        data_dict: dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> dict:
        return self.rps_service.build_rps_matrix(data_dict, start_date, end_date)

    @staticmethod
    def _calculate_flexible_peaks(pldf: pl.DataFrame, curr_idx: int, params: VCPParams) -> tuple[list | None, str]:
        return VcpScannerService.calculate_flexible_peaks(pldf, curr_idx, params)

    @staticmethod
    def _check_ma_slope(pldf: pl.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        return VcpScannerService.check_ma_slope(pldf, curr_idx, params)

    @staticmethod
    def evaluate_conditions(
        df: pd.DataFrame | pl.DataFrame,
        current_day,
        rps120: float,
        rps250: float,
        _rps_history: dict | None = None,
        params: VCPParams | None = None,
        skip_red_check: bool = False,
    ) -> tuple[bool, str, dict]:
        return VcpScannerService.evaluate_conditions(
            df,
            current_day,
            rps120,
            rps250,
            _rps_history,
            params,
            skip_red_check,
        )

    @staticmethod
    def batch_get_finance_info(codes):
        return batch_get_finance_info(codes)

    @staticmethod
    def batch_check_market_cap(codes: list[str], close_prices: dict[str, float] | None = None) -> dict[str, float]:
        return batch_check_market_cap(codes, close_prices)

    @staticmethod
    def batch_check_institution(codes):
        return batch_check_institution(codes)

    @staticmethod
    def precompute_ready_pool(
        all_data,
        rps120_series,
        rps250_series,
        params,
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

    @staticmethod
    def rt_quick_check(quote, pool_entry):
        return BreakoutMonitorService.rt_quick_check(quote, pool_entry)

    @staticmethod
    def _estimate_full_day_volume(current_volume):
        return BreakoutMonitorService.estimate_full_day_volume(current_volume)
