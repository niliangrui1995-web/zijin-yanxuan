# -*- coding: utf-8 -*-
"""Stable JSON contract for isolated stock-context fund snapshot jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

STOCK_CONTEXT_FUND_SNAPSHOT_SCHEMA_VERSION = 1


def _validate_schema_version(payload: Mapping[str, Any], contract_name: str) -> None:
    version = int(payload.get("schema_version") or 0)
    if version != STOCK_CONTEXT_FUND_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unsupported {contract_name} schema_version: {version}")


def _stock_codes(value: Sequence[object] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(str(code or "").strip() for code in value if str(code or "").strip())


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} must not be blank")
    return value


def _result_rows(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_rows = payload.get("rows")
    if raw_rows is None:
        return ()
    if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
        raise ValueError("rows must be an array")
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise ValueError("each result row must be an object")
        rows.append(dict(row))
    return tuple(rows)


@dataclass(frozen=True)
class StockContextFundSnapshotRequest:
    request_id: str
    database_path: str
    stock_codes: tuple[str, ...] | None = None
    schema_version: int = STOCK_CONTEXT_FUND_SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        database_path: str,
        stock_codes: Sequence[object] | None = None,
    ) -> "StockContextFundSnapshotRequest":
        raw_path = str(database_path or "").strip()
        if not raw_path:
            raise ValueError("database_path must not be blank")
        normalized_path = str(Path(raw_path).expanduser().resolve())
        return cls(
            request_id=uuid.uuid4().hex,
            database_path=normalized_path,
            stock_codes=_stock_codes(stock_codes),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StockContextFundSnapshotRequest":
        _validate_schema_version(payload, "stock-context fund request")
        request_id = _required_text(payload, "request_id")
        database_path = _required_text(payload, "database_path")
        stock_codes = payload.get("stock_codes")
        if stock_codes is not None and (
            isinstance(stock_codes, (str, bytes)) or not isinstance(stock_codes, Sequence)
        ):
            raise ValueError("stock_codes must be an array or null")
        return cls(
            request_id=request_id,
            database_path=str(Path(database_path).expanduser().resolve()),
            stock_codes=_stock_codes(stock_codes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "database_path": self.database_path,
            "stock_codes": list(self.stock_codes) if self.stock_codes is not None else None,
            "schema_version": self.schema_version,
        }


class StockContextFundSnapshotStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class StockContextFundSnapshotResult:
    request_id: str
    status: StockContextFundSnapshotStatus
    rows: tuple[dict[str, Any], ...] = ()
    error_code: str = ""
    error_message: str = ""
    schema_version: int = STOCK_CONTEXT_FUND_SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def succeeded(
        cls,
        request: StockContextFundSnapshotRequest,
        rows: Sequence[Mapping[str, Any]],
    ) -> "StockContextFundSnapshotResult":
        return cls(
            request_id=request.request_id,
            status=StockContextFundSnapshotStatus.SUCCEEDED,
            rows=tuple(dict(row) for row in rows),
        )

    @classmethod
    def failed(
        cls,
        request: StockContextFundSnapshotRequest,
        *,
        error_code: str,
        error_message: str,
    ) -> "StockContextFundSnapshotResult":
        return cls(
            request_id=request.request_id,
            status=StockContextFundSnapshotStatus.FAILED,
            error_code=str(error_code or "worker_failed"),
            error_message=str(error_message or "stock-context fund snapshot worker failed"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StockContextFundSnapshotResult":
        _validate_schema_version(payload, "stock-context fund result")
        return cls(
            request_id=_required_text(payload, "request_id"),
            status=StockContextFundSnapshotStatus(_required_text(payload, "status")),
            rows=_result_rows(payload),
            error_code=str(payload.get("error_code") or ""),
            error_message=str(payload.get("error_message") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "rows": [dict(row) for row in self.rows],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "schema_version": self.schema_version,
        }


__all__ = [
    "STOCK_CONTEXT_FUND_SNAPSHOT_SCHEMA_VERSION",
    "StockContextFundSnapshotRequest",
    "StockContextFundSnapshotResult",
    "StockContextFundSnapshotStatus",
]
