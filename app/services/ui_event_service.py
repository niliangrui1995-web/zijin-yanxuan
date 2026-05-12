# -*- coding: utf-8 -*-
"""UI-facing event bus entrypoints."""

from __future__ import annotations

from domains.runtime import domain_events
from infra.events import ui_signal_hub

ui_signals = ui_signal_hub

__all__ = ["domain_events", "ui_signal_hub", "ui_signals"]
