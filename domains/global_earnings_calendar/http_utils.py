# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from collections.abc import Mapping

import requests

_REDACTED = "<redacted>"
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "crtfckey",
        "password",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "setcookie",
        "token",
    }
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|crtfc[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"authorization|cookie|client[_-]?secret|password)=)([^&#\s\"']*)"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)(?:(?:bearer|basic)\s+)?[^\s,;]+"
)
_COOKIE_HEADER_RE = re.compile(r"(?i)(\b(?:cookie|set-cookie)\b\s*[:=]\s*)[^\r\n]+")
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|crtfc[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"client[_-]?secret|password|secret)\b\s*[:=]\s*)([\"']?)([^\"'\s,;}&]+)([\"']?)"
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value or "").casefold())


def _is_sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    compact = normalized.replace("_", "")
    return (
        compact in _SENSITIVE_KEY_NAMES
        or compact.endswith("apikey")
        or compact.endswith("token")
        or compact.endswith("secret")
    )


def redact_sensitive_text(value: object) -> str:
    """Remove credentials from URLs, exception text, and header-like strings."""

    text = str(value or "")
    text = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    text = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    return _NAMED_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(4)}",
        text,
    )


def redact_sensitive_data(value):
    """Recursively sanitize data before it crosses a log or persistence boundary."""

    if isinstance(value, Mapping):
        return {
            key: _REDACTED if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return type(value)(redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _sanitized_exception(exc: Exception) -> Exception:
    error_text = redact_sensitive_text(exc)
    try:
        return type(exc)(error_text)
    except Exception:  # noqa: BLE001 - third-party exception constructors have inconsistent signatures.
        if isinstance(exc, requests.RequestException):
            return requests.RequestException(error_text)
        return RuntimeError(error_text)


def response_text(response, *, encoding: str | None = None) -> str:
    if encoding and hasattr(response, "encoding"):
        try:
            response.encoding = encoding
        except (AttributeError, TypeError, ValueError):
            pass
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode(encoding or "utf-8", errors="replace")
    return str(text or "")


def raise_for_status(response) -> None:
    call_raise_for_status = getattr(response, "raise_for_status", None)
    if callable(call_raise_for_status):
        sanitized_error = None
        try:
            call_raise_for_status()
        except Exception as exc:  # noqa: BLE001 - sanitize third-party exception types at the HTTP boundary.
            sanitized_error = _sanitized_exception(exc)
        if sanitized_error is not None:
            raise sanitized_error
        return
    status_code = int(getattr(response, "status_code", 200) or 200)
    if status_code >= 400:
        raise requests.HTTPError(f"http {status_code}")
