# -*- coding: utf-8 -*-
"""Compatibility alias for the earnings engine module."""

from __future__ import annotations

import sys

from domains.earnings import engine as _engine_module

sys.modules[__name__] = _engine_module
