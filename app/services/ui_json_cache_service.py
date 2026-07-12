"""Narrow application facade for JSON cache persistence and metadata."""

from __future__ import annotations

from typing import Any

from infra.storage import json_cache_repository as _repository


def load_json_file(path: str) -> Any:
    return _repository.load_json_file(path)


def save_json_file(path: str, payload: object) -> None:
    _repository.save_json_file(path, payload)


def remove_cache_file(path: str) -> None:
    _repository.remove_cache_file(path)


def cache_file_signature(path: str) -> tuple[int, int] | None:
    return _repository.cache_file_signature(path)


def cache_file_mtime(path: str) -> float:
    return _repository.cache_file_mtime(path)


def cache_file_exists(path: str) -> bool:
    return _repository.cache_file_exists(path)


__all__ = [
    "cache_file_exists",
    "cache_file_mtime",
    "cache_file_signature",
    "load_json_file",
    "remove_cache_file",
    "save_json_file",
]
