from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit


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
