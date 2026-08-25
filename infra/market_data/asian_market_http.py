"""HTTP transport for Asian-market fallback providers."""

from __future__ import annotations

from collections.abc import Mapping

import requests

from infra.http_safety import requests_get_https
from infra.tasks.lifecycle import bounded_io_timeout, raise_if_cancelled

ASIAN_MARKET_HTTP_TIMEOUT_SEC = 15
ASIAN_MARKET_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
ASIAN_MARKET_ALLOWED_HOSTS = frozenset(
    {
        "finance.naver.com",
        "finance.yahoo.co.jp",
        "kabutan.jp",
        "mis.twse.com.tw",
        "polling.finance.naver.com",
        "qt.gtimg.cn",
        "www.tpex.org.tw",
        "www.twse.com.tw",
    }
)
RequestException = requests.RequestException
requests_module = requests


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
    timeout: float = ASIAN_MARKET_HTTP_TIMEOUT_SEC,
    retries: int = 0,
    cancellation_token=None,
):
    attempts = max(0, int(retries or 0)) + 1
    last_exc = None
    for _attempt in range(attempts):
        raise_if_cancelled(cancellation_token)
        try:
            response = requests_get_https(
                url,
                session=session,
                headers=dict(headers or ASIAN_MARKET_HTTP_HEADERS),
                timeout=bounded_io_timeout(timeout, cancellation_token),
                allowed_hosts=ASIAN_MARKET_ALLOWED_HOSTS,
                allow_reserved_tun_for_allowed_hosts=True,
            )
            raise_if_cancelled(cancellation_token)
            return response
        except RequestException as exc:
            last_exc = exc
            raise_if_cancelled(cancellation_token)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Asian-market HTTP request did not run")


__all__ = [
    "ASIAN_MARKET_ALLOWED_HOSTS",
    "ASIAN_MARKET_HTTP_HEADERS",
    "ASIAN_MARKET_HTTP_TIMEOUT_SEC",
    "RequestException",
    "asian_market_get",
    "asian_market_headers",
    "is_http_success",
    "response_status_code",
    "requests_module",
]
