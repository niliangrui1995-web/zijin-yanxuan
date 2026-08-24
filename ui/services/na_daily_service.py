"""Deprecated compatibility exports for :mod:`app.services.na_daily_service`."""

from __future__ import annotations

from app.services.na_daily_service import (
    NA_DAILY_CACHE_FILE,
    NADailyRefreshService,
    build_na_daily_history_payload,
    build_na_daily_refresh_payload,
    build_na_daily_rows,
    list_recent_report_files,
    list_report_history,
    load_report_payload,
    na_daily_output_dir,
    parse_battle_report,
    parse_recommendations,
    parse_report_identity,
    project_root,
    signature_for_report_files,
)

__all__ = [
    "NA_DAILY_CACHE_FILE",
    "NADailyRefreshService",
    "build_na_daily_history_payload",
    "build_na_daily_refresh_payload",
    "build_na_daily_rows",
    "list_recent_report_files",
    "list_report_history",
    "load_report_payload",
    "na_daily_output_dir",
    "parse_battle_report",
    "parse_recommendations",
    "parse_report_identity",
    "project_root",
    "signature_for_report_files",
]
