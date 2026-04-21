# -*- coding: utf-8 -*-
"""Compatibility alias for the market-data runtime facade."""

from __future__ import annotations

import sys

from infra.market_data import tdx_data_provider as _provider_module

sys.modules[__name__] = _provider_module
