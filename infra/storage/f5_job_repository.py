# -*- coding: utf-8 -*-
"""Filesystem protocol used between the F5 controller and its worker process."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from infra.storage.json_cache_repository import load_json_file, save_json_file


def _normalized_offset(offset: int) -> int:
    return max(0, int(offset or 0))


def _read_complete_event_chunk(path: Path, offset: int) -> tuple[bytes, int]:
    start = _normalized_offset(offset)
    try:
        with path.open("rb") as file_obj:
            file_obj.seek(start)
            chunk = file_obj.read()
    except FileNotFoundError:
        return b"", start
    complete_length = chunk.rfind(b"\n") + 1
    if complete_length <= 0:
        return b"", start
    return chunk[:complete_length], start + complete_length


def _decode_event_lines(chunk: bytes) -> list[dict[str, Any]]:
    events = []
    for raw_line in chunk.splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


class F5JobRepository:
    def __init__(self, job_dir: str | os.PathLike[str]) -> None:
        self.job_dir = Path(job_dir).resolve()
        self.request_path = self.job_dir / "request.json"
        self.events_path = self.job_dir / "events.jsonl"
        self.result_path = self.job_dir / "result.json"
        self.cancel_path = self.job_dir / "cancel.request"
        self.log_path = self.job_dir / "worker.log"
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def write_request(self, payload: dict[str, Any]) -> None:
        save_json_file(str(self.request_path), payload)

    def read_request(self) -> dict[str, Any]:
        payload = load_json_file(str(self.request_path))
        if not isinstance(payload, dict):
            raise ValueError("F5 request payload must be an object")
        return payload

    def append_event(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.events_path.open("a", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(line + "\n")
            file_obj.flush()

    def read_events(self, *, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        chunk, next_offset = _read_complete_event_chunk(self.events_path, offset)
        return _decode_event_lines(chunk), next_offset

    def write_result(self, payload: dict[str, Any]) -> None:
        save_json_file(str(self.result_path), payload)

    def read_result(self) -> dict[str, Any] | None:
        if not self.result_path.is_file():
            return None
        payload = load_json_file(str(self.result_path))
        if not isinstance(payload, dict):
            raise ValueError("F5 result payload must be an object")
        return payload

    def request_cancel(self, reason: str = "cancelled") -> None:
        save_json_file(str(self.cancel_path), {"reason": str(reason or "cancelled")})

    def cancel_requested(self) -> bool:
        return self.cancel_path.is_file()

    def cancel_reason(self) -> str:
        if not self.cancel_requested():
            return ""
        payload = load_json_file(str(self.cancel_path))
        return str(payload.get("reason") or "cancelled") if isinstance(payload, dict) else "cancelled"


__all__ = ["F5JobRepository"]
