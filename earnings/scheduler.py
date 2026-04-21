# -*- coding: utf-8 -*-
"""Compatibility alias for the earnings scheduler module."""

from __future__ import annotations

import sys

from domains.earnings import scheduler as _scheduler_module

sys.modules[__name__] = _scheduler_module
