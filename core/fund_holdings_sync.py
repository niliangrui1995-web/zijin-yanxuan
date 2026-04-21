# -*- coding: utf-8 -*-
"""Compatibility alias for the fund holdings sync module."""

from __future__ import annotations

import sys

from domains.fund_holdings import sync as _sync_module

sys.modules[__name__] = _sync_module
