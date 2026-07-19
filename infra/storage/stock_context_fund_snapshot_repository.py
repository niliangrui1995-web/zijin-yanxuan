# -*- coding: utf-8 -*-
"""Atomic filesystem protocol for isolated stock-context fund snapshots."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from infra.storage.json_cache_repository import load_json_file, save_json_file


class StockContextFundSnapshotRepository:
    def __init__(self, job_dir: str | os.PathLike[str]) -> None:
        self.job_dir = Path(job_dir).resolve()
        self.request_path = self.job_dir / "request.json"
        self.result_path = self.job_dir / "result.json"
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def write_request(self, payload: dict[str, Any]) -> None:
        save_json_file(str(self.request_path), payload)

    def read_request(self) -> dict[str, Any]:
        payload = load_json_file(str(self.request_path))
        if not isinstance(payload, dict):
            raise ValueError("stock-context fund request payload must be an object")
        return payload

    def write_result(self, payload: dict[str, Any]) -> None:
        save_json_file(str(self.result_path), payload)

    def read_result(self) -> dict[str, Any] | None:
        if not self.result_path.is_file():
            return None
        payload = load_json_file(str(self.result_path))
        if not isinstance(payload, dict):
            raise ValueError("stock-context fund result payload must be an object")
        return payload


__all__ = ["StockContextFundSnapshotRepository"]
