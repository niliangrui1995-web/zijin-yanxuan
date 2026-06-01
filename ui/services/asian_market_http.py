# -*- coding: utf-8 -*-
"""HTTP helpers for Asian market fallback fetches."""

from __future__ import annotations

from collections.abc import Mapping

import requests

ASIAN_MARKET_HTTP_TIMEOUT_SEC = 15
ASIAN_MARKET_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def asian_market_headers(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    if not overrides:
        return dict(ASIAN_MARKET_HTTP_HEADERS)
    return {**ASIAN_MARKET_HTTP_HEADERS, **dict(overrides)}


def response_status_code(response, default: int = 200) -> int:
    try:
        return int(getattr(response, "status_code", default) or default)
    except (TypeError, ValueError):
        return default


def is_http_success(response) -> bool:
    return response_status_code(response) < 400


def asian_market_get(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: int = ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    retries: int = 0,
):
    getter = session.get if session is not None else requests.get
    attempts = max(0, int(retries or 0)) + 1
    last_exc = None
    for _attempt in range(attempts):
        try:
            return getter(url, headers=dict(headers or ASIAN_MARKET_HTTP_HEADERS), timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    return getter(url, headers=dict(headers or ASIAN_MARKET_HTTP_HEADERS), timeout=timeout)
