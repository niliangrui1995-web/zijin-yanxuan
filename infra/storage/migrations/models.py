"""SQLite schema migration model."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    """One reversible schema transition between ``version - 1`` and ``version``."""

    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]
    downgrade: Callable[[sqlite3.Connection], None]
