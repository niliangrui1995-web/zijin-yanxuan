# -*- coding: utf-8 -*-
"""UI-facing configuration and settings entrypoints."""

from __future__ import annotations

from core.app_config import app_config
from infra.settings import TableViewStateStore

__all__ = ["TableViewStateStore", "app_config"]
