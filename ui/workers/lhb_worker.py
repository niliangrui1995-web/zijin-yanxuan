# -*- coding: utf-8 -*-
"""Deprecated compatibility aliases for LHB market-data access.

Production callers must import :mod:`app.services.lhb_market_data_service`.
"""

from app.services.lhb_market_data_service import (
    fetch_lhb_data_for_date,
    fetch_lhb_pool_for_date,
    probe_lhb_detail_count_for_date,
)

__all__ = [
    "fetch_lhb_data_for_date",
    "fetch_lhb_pool_for_date",
    "probe_lhb_detail_count_for_date",
]
