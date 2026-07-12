"""Compatibility alias for the canonical industry-chain application facade."""

import sys

import app.services.ui_industry_chain_service as _implementation

sys.modules[__name__] = _implementation
