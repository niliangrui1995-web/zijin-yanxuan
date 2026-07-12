# -*- coding: utf-8 -*-
"""Deprecated compatibility imports for account trade records."""

from app.services.ui_trade_record_service import (
    LEGACY_TRADE_RECORD_DIR,
    TRADE_RECORD_DIR,
    load_all_trade_records,
    load_trade_records_for_security,
    migrate_legacy_trade_records,
    normalize_trade_date,
    resolve_trade_record_dir,
)

__deprecated__ = "Use app.services.ui_trade_record_service from UI code."

__all__ = [
    "LEGACY_TRADE_RECORD_DIR",
    "TRADE_RECORD_DIR",
    "load_all_trade_records",
    "load_trade_records_for_security",
    "migrate_legacy_trade_records",
    "normalize_trade_date",
    "resolve_trade_record_dir",
]
