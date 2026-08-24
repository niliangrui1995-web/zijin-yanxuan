from __future__ import annotations

import ipaddress
import socket
import urllib.request
from collections.abc import Collection, Mapping
from contextlib import suppress
from urllib.parse import urljoin, urlsplit

DEFAULT_REQUESTS_USER_AGENT = "vcp-hunter/1.0"
DEFAULT_MAX_HTTPS_REDIRECTS = 5
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _request_url(request) -> str:
    url = getattr(request, "full_url", request)
    if not isinstance(url, str):
        raise TypeError("request URL must be a string")
    return url


def _normalized_host(hostname: str | None) -> str:
    return str(hostname or "").strip().rstrip(".").lower()


def _normalized_allowed_hosts(allowed_hosts: Collection[str] | str | None) -> set[str]:
    if allowed_hosts is None:
        return set()
    if isinstance(allowed_hosts, str):
        allowed_hosts = [allowed_hosts]
    return {host for host in (_normalized_host(item) for item in allowed_hosts) if host}


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
            isinstance(address, ipaddress.IPv4Address) and address in _CGNAT_NETWORK,
        )
    )


def _resolved_host_addresses(hostname: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] | None:
    try:
        resolved = socket.getaddrinfo(
            hostname,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return None

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _family, _socktype, _protocol, _canonname, sockaddr in resolved:
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except (IndexError, TypeError, ValueError):
            return None
    return tuple(addresses)


def _is_blocked_host(hostname: str) -> bool:
    host = _normalized_host(hostname)
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        return _is_blocked_address(ipaddress.ip_address(host))
    except ValueError:
        addresses = _resolved_host_addresses(host)
        return not addresses or any(_is_blocked_address(address) for address in addresses)


def ensure_https_request(request, *, allowed_hosts: Collection[str] | str | None = None):
    url = _request_url(request)
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise ValueError(f"only https URLs are allowed: {url!r}")
    host = _normalized_host(parts.hostname)
    if not host:
        raise ValueError(f"HTTPS URL host is required: {url!r}")
    allowed_host_set = _normalized_allowed_hosts(allowed_hosts)
    if allowed_host_set and host not in allowed_host_set:
        raise ValueError(f"HTTPS host is not allowed: {url!r}")
    if _is_blocked_host(host):
        raise ValueError(f"private or local HTTPS hosts are not allowed: {url!r}")
    return request


def _validated_https_url(url: str, *, allowed_hosts: Collection[str] | str | None = None) -> str:
    ensure_https_request(url, allowed_hosts=allowed_hosts)
    return url


def _validated_redirect_url(
    current_url: str,
    redirect_location: str,
    *,
    allowed_hosts: Collection[str] | str | None = None,
) -> str:
    redirect_url = urljoin(current_url, str(redirect_location or ""))
    ensure_https_request(redirect_url, allowed_hosts=allowed_hosts)
    return redirect_url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: Collection[str] | str | None = None):
        self._allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = _validated_redirect_url(req.full_url, newurl, allowed_hosts=self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


def urlopen_https(request, *args, allowed_hosts: Collection[str] | str | None = None, **kwargs):
    ensure_https_request(request, allowed_hosts=allowed_hosts)
    opener = urllib.request.build_opener(_ValidatingRedirectHandler(allowed_hosts))
    # URL scheme is validated above; redirects are validated by the local opener handler.
    return opener.open(request, *args, **kwargs)


def _response_redirect_location(response) -> str | None:
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return None
    if status_code < 300 or status_code >= 400:
        return None
    headers = getattr(response, "headers", None) or {}
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    location = getter("Location") or getter("location")
    if not location:
        return None
    return str(location)


def _close_redirect_response(response) -> None:
    with suppress(AttributeError, OSError, RuntimeError, TypeError):
        response.close()


def _requests_https(
    method_name: str,
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    allowed_hosts: Collection[str] | str | None = None,
    max_redirects: int = DEFAULT_MAX_HTTPS_REDIRECTS,
    **kwargs,
):
    current_url = _validated_https_url(url, allowed_hosts=allowed_hosts)
    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_REQUESTS_USER_AGENT)
    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)

    import requests

    requester = getattr(session if session is not None else requests, method_name)
    for redirect_count in range(max_redirects + 1):
        response = requester(
            current_url,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=False,
            **request_kwargs,
        )
        redirect_location = _response_redirect_location(response)
        if redirect_location is None:
            return response
        _close_redirect_response(response)
        if redirect_count >= max_redirects:
            raise ValueError(f"too many HTTPS redirects: {url!r}")
        current_url = _validated_redirect_url(current_url, redirect_location, allowed_hosts=allowed_hosts)
    raise ValueError(f"too many HTTPS redirects: {url!r}")


def requests_get_https(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    allowed_hosts: Collection[str] | str | None = None,
    max_redirects: int = DEFAULT_MAX_HTTPS_REDIRECTS,
    **kwargs,
):
    return _requests_https(
        "get",
        url,
        session=session,
        headers=headers,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
        max_redirects=max_redirects,
        **kwargs,
    )


def requests_post_https(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    allowed_hosts: Collection[str] | str | None = None,
    max_redirects: int = DEFAULT_MAX_HTTPS_REDIRECTS,
    **kwargs,
):
    return _requests_https(
        "post",
        url,
        session=session,
        headers=headers,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
        max_redirects=max_redirects,
        **kwargs,
    )
