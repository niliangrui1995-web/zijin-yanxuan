"""Infrastructure adapters around the shrinking legacy :mod:`vcp` surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcp import data_provider_local as _data_provider_local

if TYPE_CHECKING:
    from vcp.sector import SectorManager as SectorManager


def load_local_tdx_capital_snapshot(*args, **kwargs):
    return _data_provider_local.load_local_tdx_capital_snapshot(*args, **kwargs)


def batch_check_institution(*args, **kwargs):
    from vcp import engine_external as _engine_external

    return _engine_external.batch_check_institution(*args, **kwargs)


def batch_check_market_cap(*args, **kwargs):
    from vcp import engine_external as _engine_external

    return _engine_external.batch_check_market_cap(*args, **kwargs)


def batch_get_finance_info(*args, **kwargs):
    from vcp import engine_external as _engine_external

    return _engine_external.batch_get_finance_info(*args, **kwargs)


def build_prices_matrix_fast(*args, **kwargs):
    from vcp import polars_engine as _polars_engine

    return _polars_engine.build_prices_matrix_fast(*args, **kwargs)


def build_rps_matrix_pl(*args, **kwargs):
    from vcp import polars_engine as _polars_engine

    return _polars_engine.build_rps_matrix_pl(*args, **kwargs)


def __getattr__(name: str):
    if name == "SectorManager":
        from vcp.sector import SectorManager

        return SectorManager
    raise AttributeError(name)

__all__ = [
    "SectorManager",  # noqa: F822 - resolved lazily by module __getattr__
    "batch_check_institution",
    "batch_check_market_cap",
    "batch_get_finance_info",
    "build_prices_matrix_fast",
    "build_rps_matrix_pl",
    "load_local_tdx_capital_snapshot",
]
