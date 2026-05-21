# -*- coding: utf-8 -*-
"""Domain task categories used by orchestration and infrastructure layers."""

from __future__ import annotations

from enum import Enum


class TaskCategory(str, Enum):
    STARTUP = "startup"
    NETWORK = "network"
    QUOTES = "quotes"
    WINDOW = "window"
    WORKSPACE = "workspace"
