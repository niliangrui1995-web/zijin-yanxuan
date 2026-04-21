# -*- coding: utf-8 -*-
"""Legacy compatibility alias for the canonical domain-event module."""

from __future__ import annotations

import importlib
import sys

_domain_events_module = importlib.import_module("domains.runtime.domain_events")

sys.modules[__name__] = _domain_events_module
