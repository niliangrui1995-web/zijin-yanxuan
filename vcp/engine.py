# -*- coding: utf-8 -*-
"""Compatibility alias for the application-layer VCP engine facade."""

from __future__ import annotations

import sys

from app.services import scan_engine_facade as _engine_module

sys.modules[__name__] = _engine_module
