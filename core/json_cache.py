"""Compatibility exports for the JSON cache repository."""

from infra.storage.json_cache_repository import (
    cache_file_exists,
    cache_file_mtime,
    cache_file_signature,
    load_json_file,
    remove_cache_file,
    save_json_file,
)

__all__ = [
    "cache_file_exists",
    "cache_file_mtime",
    "cache_file_signature",
    "load_json_file",
    "remove_cache_file",
    "save_json_file",
]
