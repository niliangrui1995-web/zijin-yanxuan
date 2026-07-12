"""Infrastructure adapters around the shrinking legacy :mod:`vcp` surface."""

from __future__ import annotations

from vcp import data_provider_local as _data_provider_local
from vcp import engine_external as _engine_external
from vcp import polars_engine as _polars_engine
from vcp.sector import SectorManager


def load_local_tdx_capital_snapshot(*args, **kwargs):
    return _data_provider_local.load_local_tdx_capital_snapshot(*args, **kwargs)


def batch_check_institution(*args, **kwargs):
    return _engine_external.batch_check_institution(*args, **kwargs)


def batch_check_market_cap(*args, **kwargs):
    return _engine_external.batch_check_market_cap(*args, **kwargs)


def batch_get_finance_info(*args, **kwargs):
    return _engine_external.batch_get_finance_info(*args, **kwargs)


def build_prices_matrix_fast(*args, **kwargs):
    return _polars_engine.build_prices_matrix_fast(*args, **kwargs)


def build_rps_matrix_pl(*args, **kwargs):
    return _polars_engine.build_rps_matrix_pl(*args, **kwargs)

__all__ = [
    "SectorManager",
    "batch_check_institution",
    "batch_check_market_cap",
    "batch_get_finance_info",
    "build_prices_matrix_fast",
    "build_rps_matrix_pl",
    "load_local_tdx_capital_snapshot",
]
