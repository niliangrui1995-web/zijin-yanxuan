# -*- coding: utf-8 -*-
"""Legacy compatibility alias for the canonical UI-signal module."""

from __future__ import annotations

import importlib
import sys

_ui_signal_bus_module = importlib.import_module("ui.signals.ui_signal_bus")

sys.modules[__name__] = _ui_signal_bus_module
