from __future__ import annotations

import ipaddress
import socket
import ssl
import urllib.request
from collections.abc import Collection, Mapping
from contextlib import suppress
from urllib.parse import urljoin, urlsplit

DEFAULT_REQUESTS_USER_AGENT = "vcp-hunter/1.0"
DEFAULT_MAX_HTTPS_REDIRECTS = 5
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class BlockedHttpsHostError(ValueError):
    """Raised when a HTTPS destination fails the non-local-address guard."""


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
            isinstance(address, ipaddress.IPv4Address) and address in _BENCHMARK_NETWORK,
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


def _host_addresses(hostname: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] | None:
    try:
        return (ipaddress.ip_address(hostname),)
    except ValueError:
        return _resolved_host_addresses(hostname)


def _effective_reserved_tun_host_addresses(
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None,
    benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None,
) -> Mapping[str, Collection[str] | str] | None:
    """Return the explicit reserved-TUN policy, preserving the former keyword as an alias."""
    if reserved_tun_host_addresses is not None and benchmark_resolver_addresses is not None:
        raise ValueError("reserved TUN policy must be provided only once")
    return reserved_tun_host_addresses if reserved_tun_host_addresses is not None else benchmark_resolver_addresses


def _configured_reserved_tun_addresses(
    hostname: str,
    *,
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None,
) -> set[ipaddress.IPv4Address] | None:
    """Return one host's exact reserved-TUN address set, or no special-route policy."""
    host = _normalized_host(hostname)
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if not isinstance(reserved_tun_host_addresses, Mapping):
        return None
    configured_addresses = None
    for configured_host, configured_value in reserved_tun_host_addresses.items():
        if _normalized_host(str(configured_host)) == host:
            configured_addresses = configured_value
            break
    if configured_addresses is None:
        return None
    if isinstance(configured_addresses, str):
        configured_addresses = [configured_addresses]
    expected_addresses: set[ipaddress.IPv4Address] = set()
    try:
        for raw_address in configured_addresses:
            address = ipaddress.ip_address(str(raw_address).strip())
            if not isinstance(address, ipaddress.IPv4Address) or address not in _BENCHMARK_NETWORK:
                raise ValueError("reserved TUN policy must contain IPv4 benchmarking addresses")
            expected_addresses.add(address)
    except (TypeError, ValueError):
        raise ValueError("reserved TUN policy is invalid") from None
    if not expected_addresses:
        raise ValueError("reserved TUN policy is empty")
    return expected_addresses


def ensure_https_request(
    request,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None = None,
    benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None = None,
):
    url = _request_url(request)
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise ValueError(f"only https URLs are allowed: {url!r}")
    host = _normalized_host(parts.hostname)
    if not host:
        raise ValueError(f"HTTPS URL host is required: {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"HTTPS URL port is invalid: {url!r}") from exc
    allowed_host_set = _normalized_allowed_hosts(allowed_hosts)
    if allowed_host_set and host not in allowed_host_set:
        raise ValueError(f"HTTPS host is not allowed: {url!r}")

    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    policy = _effective_reserved_tun_host_addresses(
        reserved_tun_host_addresses,
        benchmark_resolver_addresses,
    )
    expected_tun_addresses = _configured_reserved_tun_addresses(
        host,
        reserved_tun_host_addresses=policy,
    )
    if expected_tun_addresses is not None and host not in allowed_host_set:
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    addresses = _host_addresses(host)
    if not addresses:
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    if expected_tun_addresses is not None:
        # `198.18.0.0/15` is reserved for benchmarking, so it is accepted only
        # as the exact VPN-TUN resolution on HTTPS/443.  A normal public DNS
        # response is also valid for this allowlisted hostname; that route is
        # protected by the local HTTPS handler's hostname/certificate checks,
        # not by a claim that the public address is an IP pin.
        if port in (None, 443) and set(addresses) == expected_tun_addresses:
            return request
        if port in (None, 443) and not any(_is_blocked_address(address) for address in addresses):
            return request
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    if any(_is_blocked_address(address) for address in addresses):
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    return request


def _validated_https_url(
    url: str,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None = None,
    benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None = None,
) -> str:
    ensure_https_request(
        url,
        allowed_hosts=allowed_hosts,
        reserved_tun_host_addresses=reserved_tun_host_addresses,
        benchmark_resolver_addresses=benchmark_resolver_addresses,
    )
    return url


def _validated_redirect_url(
    current_url: str,
    redirect_location: str,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None = None,
    benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None = None,
) -> str:
    redirect_url = urljoin(current_url, str(redirect_location or ""))
    ensure_https_request(
        redirect_url,
        allowed_hosts=allowed_hosts,
        reserved_tun_host_addresses=reserved_tun_host_addresses,
        benchmark_resolver_addresses=benchmark_resolver_addresses,
    )
    return redirect_url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        allowed_hosts: Collection[str] | str | None = None,
        reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None = None,
        benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None = None,
    ):
        self._allowed_hosts = allowed_hosts
        self._reserved_tun_host_addresses = reserved_tun_host_addresses
        self._benchmark_resolver_addresses = benchmark_resolver_addresses

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = _validated_redirect_url(
            req.full_url,
            newurl,
            allowed_hosts=self._allowed_hosts,
            reserved_tun_host_addresses=self._reserved_tun_host_addresses,
            benchmark_resolver_addresses=self._benchmark_resolver_addresses,
        )
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


def urlopen_https(
    request,
    *args,
    allowed_hosts: Collection[str] | str | None = None,
    reserved_tun_host_addresses: Mapping[str, Collection[str] | str] | None = None,
    benchmark_resolver_addresses: Mapping[str, Collection[str] | str] | None = None,
    **kwargs,
):
    ensure_https_request(
        request,
        allowed_hosts=allowed_hosts,
        reserved_tun_host_addresses=reserved_tun_host_addresses,
        benchmark_resolver_addresses=benchmark_resolver_addresses,
    )
    policy = _effective_reserved_tun_host_addresses(
        reserved_tun_host_addresses,
        benchmark_resolver_addresses,
    )
    tls_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    tls_context.verify_mode = ssl.CERT_REQUIRED
    tls_context.check_hostname = True
    opener = urllib.request.build_opener(
        _ValidatingRedirectHandler(allowed_hosts, reserved_tun_host_addresses=policy),
        urllib.request.HTTPSHandler(context=tls_context),
    )
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
