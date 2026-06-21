# -*- coding: utf-8 -*-
from __future__ import annotations

from vcp.fetchers import yf_session as _legacy_yf_session


def build_yf_session():
    return _legacy_yf_session.build_yf_session()


def get_yf_rate_limit_status():
    return _legacy_yf_session.get_yf_rate_limit_status()


def is_yf_rate_limit_error(exc: BaseException | None) -> bool:
    return _legacy_yf_session.is_yf_rate_limit_error(exc)


def mark_yf_rate_limited(exc=None, cooldown_sec=None):
    if cooldown_sec is None:
        return _legacy_yf_session.mark_yf_rate_limited(exc)
    return _legacy_yf_session.mark_yf_rate_limited(exc, cooldown_sec=cooldown_sec)


__all__ = [
    "build_yf_session",
    "get_yf_rate_limit_status",
    "is_yf_rate_limit_error",
    "mark_yf_rate_limited",
]
