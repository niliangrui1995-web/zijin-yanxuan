# -*- coding: utf-8 -*-
"""Compatibility alias for :mod:`app.services.na_daily_service`."""

from __future__ import annotations

import sys

import app.services.na_daily_service as _service

sys.modules[__name__] = _service
