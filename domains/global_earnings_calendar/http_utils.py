# -*- coding: utf-8 -*-
from __future__ import annotations

import requests


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
        call_raise_for_status()
        return
    status_code = int(getattr(response, "status_code", 200) or 200)
    if status_code >= 400:
        raise requests.HTTPError(f"http {status_code}")

