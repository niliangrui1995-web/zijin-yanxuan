# -*- coding: utf-8 -*-
"""Streaming size and SHA-256 fingerprints for immutable runtime artifacts."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_CHUNK_SIZE = 1024 * 1024


class FileIntegrityError(ValueError):
    """Raised when an artifact no longer matches its sealed fingerprint."""


@dataclass(frozen=True)
class FileFingerprint:
    size_bytes: int
    sha256: str


def is_sha256_hexdigest(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_PATTERN.fullmatch(value))


def fingerprint_file(path: str | Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> FileFingerprint:
    digest = hashlib.sha256()
    size_bytes = 0
    with Path(path).open("rb") as file_obj:
        while chunk := file_obj.read(max(1, int(chunk_size))):
            digest.update(chunk)
            size_bytes += len(chunk)
    return FileFingerprint(size_bytes=size_bytes, sha256=digest.hexdigest())


def verify_file_fingerprint(
    path: str | Path,
    *,
    expected_size_bytes: int,
    expected_sha256: str,
) -> FileFingerprint:
    artifact_path = Path(path)
    if (
        not isinstance(expected_size_bytes, int)
        or isinstance(expected_size_bytes, bool)
        or expected_size_bytes <= 0
        or not is_sha256_hexdigest(expected_sha256)
    ):
        raise FileIntegrityError(f"artifact fingerprint is invalid: {artifact_path}")
    try:
        actual = fingerprint_file(artifact_path)
    except (OSError, TypeError, ValueError) as exc:
        raise FileIntegrityError(f"artifact is unreadable: {artifact_path}") from exc
    if actual.size_bytes != expected_size_bytes:
        raise FileIntegrityError(
            "artifact size mismatch: "
            f"{artifact_path} expected={expected_size_bytes} actual={actual.size_bytes}"
        )
    if not hmac.compare_digest(actual.sha256, expected_sha256):
        raise FileIntegrityError(f"artifact SHA-256 mismatch: {artifact_path}")
    return actual


__all__ = [
    "FileFingerprint",
    "FileIntegrityError",
    "fingerprint_file",
    "is_sha256_hexdigest",
    "verify_file_fingerprint",
]
