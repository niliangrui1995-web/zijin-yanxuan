# -*- coding: utf-8 -*-
"""Deprecated compatibility import; use app.services.asian_market_http_service."""

from __future__ import annotations

from app.services.asian_market_http_service import (
    ASIAN_MARKET_HTTP_HEADERS,
    ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    RequestException,
    asian_market_get,
    asian_market_headers,
    is_http_success,
    response_status_code,
)

__all__ = [
    "ASIAN_MARKET_HTTP_HEADERS",
    "ASIAN_MARKET_HTTP_TIMEOUT_SEC",
    "RequestException",
    "asian_market_get",
    "asian_market_headers",
    "is_http_success",
    "response_status_code",
]
