# -*- coding: utf-8 -*-
"""Filesystem repository for North-America daily battle reports."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.exceptions import CacheIOError, DataFormatError
from core.runtime_paths import CACHE_DIR
from domains.na_daily import parse_battle_report, parse_recommendations, parse_structured_report
from infra.storage.json_cache_repository import load_json_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NA_DAILY_OUTPUT_DIR = PROJECT_ROOT.parent / "每日战报" / "每日热点输出"
NA_DAILY_CACHE_FILE = str(Path(CACHE_DIR) / "na_daily_latest.json")
_REPORT_IDENTITY_RE = re.compile(r"战报_(\d{8})(\d{0,6})")


class NADailyReportRepository:
    def __init__(self, output_dir: str | Path = NA_DAILY_OUTPUT_DIR) -> None:
        self.output_dir = Path(output_dir)

    @staticmethod
    def project_root() -> str:
        return str(PROJECT_ROOT)

    def report_output_dir(self) -> str:
        return str(self.output_dir)

    @staticmethod
    def parse_report_identity(path: str | Path) -> tuple[str, int, str]:
        report_path = Path(path)
        matched = _REPORT_IDENTITY_RE.search(report_path.name)
        if matched is None:
            report_dt = dt.datetime.fromtimestamp(report_path.stat().st_mtime)
            report_date = report_dt.strftime("%Y%m%d")
        else:
            report_date = matched.group(1)
            time_part = matched.group(2)
            if time_part:
                padded_time = (time_part + "000000")[:6]
                report_dt = dt.datetime.strptime(report_date + padded_time, "%Y%m%d%H%M%S")
            else:
                report_dt = dt.datetime.fromtimestamp(report_path.stat().st_mtime)
                report_date = report_dt.strftime("%Y%m%d")
        report_ts = int(report_dt.strftime("%Y%m%d%H%M%S"))
        return report_date, report_ts, report_path.name

    @staticmethod
    def signature_for(paths: Sequence[str | Path]) -> tuple[str, ...]:
        return tuple(f"{Path(path).name}:{int(Path(path).stat().st_mtime)}" for path in paths)

    def list_recent_report_files(self, *, limit: int = 5) -> list[str]:
        files = [path for path in self.output_dir.rglob("战报_*.md") if path.is_file()]
        files.sort(key=lambda path: (self.parse_report_identity(path)[1], str(path)))
        return [str(path) for path in files[-limit:]]

    @staticmethod
    def _load_structured_payload(json_path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]] | None:
        try:
            data = load_json_file(str(json_path))
        except (CacheIOError, DataFormatError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return parse_structured_report(data)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def load_report_payload(path: str | Path) -> tuple[list[dict], dict[str, dict]]:
        report_path = Path(path)
        structured = NADailyReportRepository._load_structured_payload(report_path.with_suffix(".json"))
        if structured is not None:
            return structured
        try:
            content = report_path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            return [], {}
        return parse_battle_report(content), parse_recommendations(content)


__all__ = [
    "NA_DAILY_CACHE_FILE",
    "NA_DAILY_OUTPUT_DIR",
    "NADailyReportRepository",
    "PROJECT_ROOT",
]
