# -*- coding: utf-8 -*-
"""Application facade for North-America daily-report refresh state."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from PyQt6.QtCore import QObject

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_json_cache_service import load_json_file, save_json_file
from core.exceptions import CacheIOError, DataFormatError
from domains.na_daily import parse_battle_report, parse_recommendations
from infra.storage.na_daily_repository import (
    NA_DAILY_CACHE_FILE,
    NA_DAILY_OUTPUT_DIR,
    PROJECT_ROOT,
    NADailyReportRepository,
)


def _repository(output_dir: str | Path | None = None) -> NADailyReportRepository:
    return NADailyReportRepository(output_dir or NA_DAILY_OUTPUT_DIR)


def project_root() -> str:
    return str(PROJECT_ROOT)


def na_daily_output_dir() -> str:
    return str(NA_DAILY_OUTPUT_DIR)


def parse_report_identity(path: str) -> tuple[str, int, str]:
    return _repository().parse_report_identity(path)


def signature_for_report_files(report_files: list[str]) -> tuple[str, ...]:
    return _repository().signature_for(report_files)


def list_recent_report_files(output_dir: str | None = None, *, limit: int = 5) -> list[str]:
    return _repository(output_dir).list_recent_report_files(limit=limit)


def list_report_history(output_dir: str | None = None) -> list[dict[str, object]]:
    return _repository(output_dir).list_report_history()


def load_report_payload(path: str) -> tuple[list[dict], dict[str, dict]]:
    return _repository().load_report_payload(path)


def _display_row(stock: dict, *, report_date: str, report_ts: int, row_rank: int) -> dict | None:
    code = str(stock.get("代码", "") or "").strip()
    if not code:
        return None
    raw_elasticity = stock.get("弹性", "")
    clean_elasticity = re.split(r"[（(]", raw_elasticity)[0].strip() if raw_elasticity else ""
    clean_elasticity = "".join(
        character for character in clean_elasticity if character.isalnum() or "\u4e00" <= character <= "\u9fa5"
    )
    raw_risk = str(stock.get("风控", ""))
    clean_risk = "".join(character for character in raw_risk if character in "\U0001f7e1\U0001f534\U0001f7e2")
    return {
        "代码": code,
        "名称": stock.get("名称", ""),
        "现价": "--",
        "涨幅%": "--",
        "市值": "--",
        "日报时间": report_date,
        "细分板块": stock.get("行业", ""),
        "股价弹性": clean_elasticity,
        "催化剂": stock.get("催化剂", ""),
        "风控": clean_risk,
        "评级": "",
        "_report_ts": report_ts,
        "_report_row_rank": row_rank,
    }


def _finalize_rows(rows_by_code: dict[str, dict], recommendations: dict[str, dict]) -> list[dict]:
    rows = []
    for code, row in rows_by_code.items():
        item = dict(row)
        item["评级"] = (recommendations.get(code) or {}).get("priority", "")
        rows.append(item)
    rows.sort(
        key=lambda row: (
            -int(row.get("日报时间", "0") or 0),
            -int(row.get("_report_ts", 0) or 0),
            int(row.get("_report_row_rank", 0) or 0),
            str(row.get("代码", "") or ""),
        )
    )
    return rows


def _payload_text(payload: dict, key: str, default: str = "") -> str:
    return str(payload.get(key) or default).strip()


def _cache_status(status: str) -> str:
    return "full" if status in {"", "success"} else status


def build_na_daily_rows(report_files: list[str]) -> tuple[list[dict], list[str], tuple[str, ...]]:
    if not report_files:
        return [], [], ()
    latest_rows: dict[str, dict] = {}
    latest_recommendations: dict[str, dict] = {}
    repository = _repository()
    for path in report_files:
        report_date, report_ts, _ = repository.parse_report_identity(path)
        stocks, recommendations = repository.load_report_payload(path)
        for row_rank, stock in enumerate(stocks):
            row = _display_row(stock, report_date=report_date, report_ts=report_ts, row_rank=row_rank)
            if row is not None:
                latest_rows[row["代码"]] = row
        for code, recommendation in recommendations.items():
            latest_recommendations[str(code).strip()] = recommendation
    return (
        _finalize_rows(latest_rows, latest_recommendations),
        report_files,
        repository.signature_for(report_files),
    )


def build_na_daily_refresh_payload(output_dir: str | None = None, *, limit: int = 5) -> dict:
    report_files = list_recent_report_files(output_dir, limit=limit)
    if not report_files:
        return {
            "job_key": "na_daily_full",
            "status": "skipped",
            "message": "no report files",
            "rows": [],
            "report_files": [],
            "report_signature": (),
            "cache_file": NA_DAILY_CACHE_FILE,
        }
    rows, resolved_files, report_signature = build_na_daily_rows(report_files)
    return {
        "job_key": "na_daily_full",
        "status": "success",
        "rows": list(rows),
        "records": len(rows),
        "report_files": list(resolved_files),
        "report_signature": tuple(report_signature),
        "cache_file": NA_DAILY_CACHE_FILE,
    }


def build_na_daily_history_payload(output_dir: str | None, report_date: str) -> dict:
    normalized_date = str(report_date or "").strip()
    history = list_report_history(output_dir)
    entry = next((item for item in history if item.get("date") == normalized_date), None)
    if entry is None:
        return {
            "job_key": "na_daily_history",
            "status": "absent",
            "message": "未发现该日期的本地输出",
            "report_date": normalized_date,
            "rows": [],
            "report_files": [],
            "report_signature": (),
            "cache_file": NA_DAILY_CACHE_FILE,
        }

    state = str(entry.get("state") or "missing").strip()
    report_files = list(entry.get("report_files") or [])
    if state != "available":
        return {
            "job_key": "na_daily_history",
            "status": state,
            "message": str(entry.get("message") or "未发现战报文件").strip(),
            "report_date": normalized_date,
            "manifest_status": str(entry.get("manifest_status") or "").strip(),
            "rows": [],
            "report_files": [],
            "report_signature": (),
            "cache_file": NA_DAILY_CACHE_FILE,
        }

    # 同日多次运行时，以时间戳最新的文件作为该日期的最终战报，避免拼接重跑内容。
    rows, resolved_files, report_signature = build_na_daily_rows(report_files[-1:])
    return {
        "job_key": "na_daily_history",
        "status": "success",
        "message": "",
        "report_date": normalized_date,
        "rows": list(rows),
        "records": len(rows),
        "report_files": list(resolved_files),
        "report_signature": tuple(report_signature),
        "cache_file": NA_DAILY_CACHE_FILE,
    }


class NADailyRefreshService(QObject):
    """Own cached rows and coordinate background refresh payloads."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._report_files: list[str] = []
        self._report_signature: tuple[str, ...] = ()
        self._last_result: dict = {}
        self.load_cache()

    @property
    def rows(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    @property
    def report_files(self) -> list[str]:
        return list(self._report_files)

    @property
    def report_signature(self) -> tuple[str, ...]:
        return tuple(self._report_signature)

    def latest_result(self) -> dict:
        return dict(self._last_result)

    @staticmethod
    def cache_file() -> str:
        return NA_DAILY_CACHE_FILE

    _project_root = staticmethod(project_root)

    def _get_na_daily_output_dir(self) -> str:
        return na_daily_output_dir()

    def _list_recent_report_files(self, limit: int = 5) -> list[str]:
        return list_recent_report_files(self._get_na_daily_output_dir(), limit=limit)

    _signature_for = staticmethod(signature_for_report_files)

    def load_cache(self) -> dict:
        try:
            payload = load_json_file(NA_DAILY_CACHE_FILE)
        except (CacheIOError, DataFormatError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        self._rows = list(payload.get("rows") or [])
        self._report_files = list(payload.get("report_files") or [])
        self._report_signature = tuple(payload.get("report_signature") or ())
        self._last_result = self._result_payload(job_key="na_daily", status="cache")
        return self.latest_result()

    def _result_payload(self, *, job_key: str, status: str, message: str = "") -> dict:
        return {
            "job_key": job_key,
            "status": status,
            "message": message,
            "records": len(self._rows),
            "report_files": list(self._report_files),
            "report_signature": tuple(self._report_signature),
            "cache_file": NA_DAILY_CACHE_FILE,
        }

    def _save_cache(self, *, status: str) -> None:
        save_json_file(
            NA_DAILY_CACHE_FILE,
            {
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
                "status": str(status or "").strip(),
                "rows": self._rows,
                "report_files": self._report_files,
                "report_signature": list(self._report_signature),
                "latest_report_date": self._latest_report_date(),
            },
        )

    def _latest_report_date(self) -> str:
        dates = [
            str(row.get("日报时间", "") or "").strip()
            for row in self._rows
            if isinstance(row, dict) and str(row.get("日报时间", "") or "").strip()
        ]
        return max(dates) if dates else ""

    def _apply_empty_refresh(self, payload: dict, status: str) -> dict:
        if not self._rows and not self._report_files:
            self.load_cache()
        message = str(payload.get("message") or "no report files")
        self._last_result = self._result_payload(
            job_key="na_daily_full",
            status=status or "skipped",
            message=message,
        )
        return self.latest_result()

    def apply_refresh_payload(self, payload: dict, *, emit_event: bool = True) -> dict:
        payload = dict(payload) if payload else {}
        status = _payload_text(payload, "status")
        report_files = list(payload.get("report_files") or [])
        if not report_files:
            return self._apply_empty_refresh(payload, status)
        self._rows = list(payload.get("rows") or [])
        self._report_files = report_files
        self._report_signature = tuple(payload.get("report_signature") or ())
        self._save_cache(status=_cache_status(status))
        self._last_result = self._result_payload(
            job_key=_payload_text(payload, "job_key", "na_daily_full"),
            status=status or "success",
        )
        if emit_event:
            event_bus.sig_na_daily_updated.emit()
        return self.latest_result()

    def refresh_full(self, *, emit_event: bool = True) -> dict:
        rows, report_files, report_signature = self._build_na_daily_rows()
        payload = {
            "job_key": "na_daily_full",
            "status": "success" if report_files else "skipped",
            "message": "" if report_files else "no report files",
            "rows": list(rows),
            "records": len(rows),
            "report_files": list(report_files),
            "report_signature": tuple(report_signature),
            "cache_file": NA_DAILY_CACHE_FILE,
        }
        return self.apply_refresh_payload(payload, emit_event=emit_event)

    def refresh_incremental(self, *, emit_event: bool = True) -> dict:
        report_files = self._list_recent_report_files(limit=5)
        if not report_files:
            self._last_result = self._result_payload(
                job_key="na_daily_incremental",
                status="skipped",
                message="no report files",
            )
            return self.latest_result()
        report_signature = self._signature_for(report_files)
        if not self._report_signature:
            self.load_cache()
        if tuple(self._report_signature) == tuple(report_signature):
            self._last_result = self._result_payload(
                job_key="na_daily_incremental",
                status="skipped",
                message="unchanged",
            )
            return self.latest_result()
        result = self.refresh_full(emit_event=emit_event)
        result.update(job_key="na_daily_incremental", message="updated")
        self._last_result = dict(result)
        return self.latest_result()

    def _build_na_daily_rows(self) -> tuple[list[dict], list[str], tuple[str, ...]]:
        report_files = self._list_recent_report_files(limit=5)
        return build_na_daily_rows(report_files)

__all__ = [
    "NA_DAILY_CACHE_FILE",
    "NADailyRefreshService",
    "build_na_daily_refresh_payload",
    "build_na_daily_history_payload",
    "build_na_daily_rows",
    "list_report_history",
    "list_recent_report_files",
    "load_report_payload",
    "na_daily_output_dir",
    "parse_battle_report",
    "parse_recommendations",
    "parse_report_identity",
    "project_root",
    "signature_for_report_files",
]
