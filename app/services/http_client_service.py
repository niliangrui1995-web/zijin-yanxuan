# -*- coding: utf-8 -*-
"""Application-layer HTTP helpers exposed to UI modules."""

from __future__ import annotations

from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT, requests_get_https

__all__ = ["DEFAULT_REQUESTS_USER_AGENT", "requests_get_https"]

