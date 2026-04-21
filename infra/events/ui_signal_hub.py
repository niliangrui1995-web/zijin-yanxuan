# -*- coding: utf-8 -*-
"""Compatibility export for the canonical UI signal hub."""

from __future__ import annotations

from core.ui_signals import UISignalBus, ui_signals

UiSignalHub = UISignalBus
ui_signal_hub = ui_signals

__all__ = ["UiSignalHub", "ui_signal_hub"]
