# -*- coding: utf-8 -*-
"""Compatibility alias for the canonical application startup orchestrator."""

import sys

import app.bootstrap.startup_orchestrator as _implementation

sys.modules[__name__] = _implementation
