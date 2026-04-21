# -*- coding: utf-8 -*-
"""Infrastructure-facing export for UI signal hub access."""

from __future__ import annotations

from core.ui_signals import UISignals, ui_signals

UiSignalHub = UISignals
ui_signal_hub = ui_signals

__all__ = ["UiSignalHub", "ui_signal_hub"]
