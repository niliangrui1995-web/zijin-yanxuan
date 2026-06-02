from __future__ import annotations

import urllib.request
from collections.abc import Mapping
from urllib.parse import urlsplit

DEFAULT_REQUESTS_USER_AGENT = "vcp-hunter/1.0"


def _request_url(request) -> str:
    url = getattr(request, "full_url", request)
    if not isinstance(url, str):
        raise TypeError("request URL must be a string")
    return url


def ensure_https_request(request):
    url = _request_url(request)
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise ValueError(f"only https URLs are allowed: {url!r}")
    return request


def urlopen_https(request, *args, **kwargs):
    ensure_https_request(request)
    # URL scheme is validated above.
    return urllib.request.urlopen(request, *args, **kwargs)  # nosec B310


def requests_get_https(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout=15,
    **kwargs,
):
    ensure_https_request(url)
    import requests

    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_REQUESTS_USER_AGENT)
    getter = session.get if session is not None else requests.get
    return getter(url, headers=request_headers, timeout=timeout, **kwargs)


def requests_post_https(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout=15,
    **kwargs,
):
    ensure_https_request(url)
    import requests

    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_REQUESTS_USER_AGENT)
    poster = session.post if session is not None else requests.post
    return poster(url, headers=request_headers, timeout=timeout, **kwargs)
