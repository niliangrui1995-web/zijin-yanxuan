"""Compatibility alias for the canonical LHB application facade."""

import sys

import app.services.ui_lhb_pool_service as _implementation

sys.modules[__name__] = _implementation
