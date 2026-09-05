from __future__ import annotations

import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Collection, Mapping
from contextlib import suppress
from types import ModuleType
from urllib.parse import urljoin, urlsplit

DEFAULT_REQUESTS_USER_AGENT = "vcp-hunter/1.0"
DEFAULT_MAX_HTTPS_REDIRECTS = 5
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
# Hosts with application-owned request adapters that may be carried by the
# user's optional fake-IP TUN.  This is intentionally an exact allowlist, not
# a global exemption for 198.18.0.0/15 or arbitrary caller-provided hosts.
_DYNAMIC_TUN_ALLOWED_HOSTS = frozenset(
    {
        "api.nasdaq.com",
        "basic.10jqka.com.cn",
        "data.sec.gov",
        "datacenter-web.eastmoney.com",
        "datacenter.eastmoney.com",
        "date.nager.at",
        "emops.twse.com.tw",
        "emweb.securities.eastmoney.com",
        "finance.naver.com",
        "finance.yahoo.co.jp",
        "fundf10.eastmoney.com",
        "fuyao.aicubes.cn",
        "hq.sinajs.cn",
        "kabutan.jp",
        "kind.krx.co.kr",
        "m.stock.naver.com",
        "mis.twse.com.tw",
        "opendart.fss.or.kr",
        "polling.finance.naver.com",
        "push2.eastmoney.com",
        "push2delay.eastmoney.com",
        "qt.gtimg.cn",
        "web.ifzq.gtimg.cn",
        "www.alphavantage.co",
        "www.jpx.co.jp",
        "www.release.tdnet.info",
        "www.sec.gov",
        "www.tpex.org.tw",
        "www.twse.com.tw",
        "88.push2.eastmoney.com",
    }
)
_SENSITIVE_CROSS_HOST_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "referer"})
_SENSITIVE_HEADER_NAME_TOKENS = ("apikey", "token", "secret")
_CROSS_HOST_CREDENTIAL_KWARGS = frozenset({"auth", "cert", "cookies", "data", "files", "json", "params"})


class BlockedHttpsHostError(ValueError):
    """Raised when a HTTPS destination fails the non-local-address guard."""


def _request_url(request) -> str:
    url = getattr(request, "full_url", request)
    if not isinstance(url, str):
        raise TypeError("request URL must be a string")
    return url


def _normalized_host(hostname: str | None) -> str:
    return str(hostname or "").strip().rstrip(".").lower()


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return (parts.scheme.lower(), _normalized_host(parts.hostname), port or 443)


def _is_sensitive_cross_host_header(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    compact_name = normalized.replace("-", "").replace("_", "")
    return normalized in _SENSITIVE_CROSS_HOST_HEADERS or any(
        token in compact_name for token in _SENSITIVE_HEADER_NAME_TOKENS
    )


def _headers_without_cross_host_credentials(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(name): str(value) for name, value in headers.items() if not _is_sensitive_cross_host_header(name)}


def _request_kwargs_without_cross_host_credentials(request_kwargs: Mapping[str, object]) -> dict[str, object]:
    """Drop caller-supplied credentials and payloads before a cross-host GET."""
    return {key: value for key, value in request_kwargs.items() if key not in _CROSS_HOST_CREDENTIAL_KWARGS}


def _session_has_cross_host_credentials(session) -> bool:
    if session is None or isinstance(session, ModuleType):
        return False
    if any(getattr(session, name, None) for name in ("auth", "cert", "cookies", "params")):
        return True
    return any(_is_sensitive_cross_host_header(name) for name in (getattr(session, "headers", None) or {}))


def _redirect_request_parameters(method_name, session, current_url, redirect_url, request_headers, request_kwargs):
    if _url_origin(current_url) == _url_origin(redirect_url):
        return request_headers, request_kwargs
    if method_name != "get":
        raise ValueError("cross-host HTTPS redirects are only supported for GET requests")
    if _session_has_cross_host_credentials(session):
        # Per-call filtering cannot suppress credentials merged back in by
        # the caller's session. Do not mutate a potentially shared session.
        raise ValueError("cross-host HTTPS redirects with session credentials are not allowed")
    # A redirect Location supplies the next URL and query string. Do not reapply
    # caller-owned query credentials, cookies, auth, or payloads to another origin.
    return (
        _headers_without_cross_host_credentials(request_headers),
        _request_kwargs_without_cross_host_credentials(request_kwargs),
    )


def _strip_cross_host_request_credentials(request) -> None:
    for header_store in (getattr(request, "headers", None), getattr(request, "unredirected_hdrs", None)):
        if isinstance(header_store, dict):
            for name in list(header_store):
                if _is_sensitive_cross_host_header(name):
                    header_store.pop(name, None)


def https_url_host_allowlist(url: str) -> frozenset[str]:
    """Return the single normalized hostname embedded in a configured HTTPS URL.

    The caller still receives a fake-IP TUN exemption only if that hostname is
    in the internal dynamic-TUN allowlist. This keeps configurable public
    endpoints compatible without widening the 198.18.0.0/15 exception.
    """
    host = _normalized_host(urlsplit(_request_url(url)).hostname)
    return frozenset({host}) if host else frozenset()


def _normalized_allowed_hosts(allowed_hosts: Collection[str] | str | None) -> set[str]:
    if allowed_hosts is None:
        return set()
    if isinstance(allowed_hosts, str):
        allowed_hosts = [allowed_hosts]
    return {host for host in (_normalized_host(item) for item in allowed_hosts) if host}


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_blocked_address(address.ipv4_mapped)
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            isinstance(address, ipaddress.IPv6Address) and address.is_site_local,
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


def ensure_https_request(
    request,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
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
    addresses = _host_addresses(host)
    if not addresses:
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    allow_fake_ip_tun = (
        allow_reserved_tun_for_allowed_hosts
        and host in allowed_host_set
        and host in _DYNAMIC_TUN_ALLOWED_HOSTS
    )
    if allow_fake_ip_tun:
        # Fake-IP TUN responses are accepted for the app's fixed HTTPS/443
        # vendor endpoints. No address pin, DNS-result comparison, or local
        # route probe is performed, so VPN switching cannot be blocked here.
        if port in (None, 443) and all(
            address in _BENCHMARK_NETWORK or not _is_blocked_address(address)
            for address in addresses
        ):
            return request
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    if any(_is_blocked_address(address) for address in addresses):
        raise BlockedHttpsHostError(f"private or local HTTPS hosts are not allowed: {url!r}")
    return request


def _validated_https_url(
    url: str,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
) -> str:
    ensure_https_request(
        url,
        allowed_hosts=allowed_hosts,
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
    )
    return url


def _validated_redirect_url(
    current_url: str,
    redirect_location: str,
    *,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
) -> str:
    redirect_url = urljoin(current_url, str(redirect_location or ""))
    ensure_https_request(
        redirect_url,
        allowed_hosts=allowed_hosts,
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
    )
    return redirect_url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        allowed_hosts: Collection[str] | str | None = None,
        allow_reserved_tun_for_allowed_hosts: bool = False,
    ):
        self._allowed_hosts = allowed_hosts
        self._allow_reserved_tun_for_allowed_hosts = bool(allow_reserved_tun_for_allowed_hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = _validated_redirect_url(
            req.full_url,
            newurl,
            allowed_hosts=self._allowed_hosts,
            allow_reserved_tun_for_allowed_hosts=self._allow_reserved_tun_for_allowed_hosts,
        )
        redirected_request = super().redirect_request(req, fp, code, msg, headers, redirect_url)
        if redirected_request is not None and _url_origin(req.full_url) != _url_origin(redirect_url):
            _strip_cross_host_request_credentials(redirected_request)
        return redirected_request


def _rebuilt_verified_request(request, verified_url: str):
    """Return an immutable-at-call-boundary urllib request after URL validation."""
    if not isinstance(request, urllib.request.Request):
        return request
    return urllib.request.Request(
        verified_url,
        data=request.data,
        headers=dict(request.header_items()),
        origin_req_host=request.origin_req_host,
        unverifiable=request.unverifiable,
        method=request.get_method(),
    )


def _is_retryable_urlopen_get(request, args: tuple[object, ...], kwargs: Mapping[str, object]) -> bool:
    if args or kwargs.get("data") is not None:
        return False
    if isinstance(request, urllib.request.Request):
        return request.get_method().upper() == "GET" and request.data is None
    return True


def urlopen_https(
    request,
    *args,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
    **kwargs,
):
    if not isinstance(request, (str, urllib.request.Request)):
        raise TypeError("HTTPS request must be a URL string or urllib.request.Request")
    verified_url = _request_url(request)
    ensure_https_request(
        request,
        allowed_hosts=allowed_hosts,
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
    )
    request = _rebuilt_verified_request(request, verified_url)
    tls_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    tls_context.verify_mode = ssl.CERT_REQUIRED
    tls_context.check_hostname = True
    opener = urllib.request.build_opener(
        _ValidatingRedirectHandler(
            allowed_hosts,
            allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
        ),
        urllib.request.HTTPSHandler(context=tls_context),
    )
    # URL scheme is validated above; redirects are validated by the local opener handler.
    # A VPN teardown can invalidate one in-flight GET. Re-resolve and reissue it
    # once without treating fake-IP DNS as a local-policy failure.
    retry_transport_once = allow_reserved_tun_for_allowed_hosts and _is_retryable_urlopen_get(request, args, kwargs)
    for attempt in range(2 if retry_transport_once else 1):
        try:
            return opener.open(request, *args, **kwargs)
        except OSError as exc:
            if not retry_transport_once or attempt or isinstance(exc, urllib.error.HTTPError):
                raise
            ensure_https_request(
                verified_url,
                allowed_hosts=allowed_hosts,
                allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
            )
            request = _rebuilt_verified_request(request, verified_url)
    raise RuntimeError("HTTPS GET retry did not run")


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


def _ensure_tls_verification_enabled(session, request_kwargs: Mapping[str, object]) -> None:
    """Reject only explicit certificate-verification disablement.

    A CA-bundle path remains valid.  This guard is independent from the
    optional fake-IP TUN support, so changing VPN state never weakens TLS.
    """
    if "verify" in request_kwargs and not request_kwargs["verify"]:
        raise ValueError("HTTPS certificate verification cannot be disabled")
    if session is not None and "verify" not in request_kwargs and not getattr(session, "verify", True):
        raise ValueError("HTTPS certificate verification cannot be disabled")


def _requests_https(
    method_name: str,
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
    max_redirects: int = DEFAULT_MAX_HTTPS_REDIRECTS,
    **kwargs,
):
    current_url = _validated_https_url(
        url,
        allowed_hosts=allowed_hosts,
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
    )
    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_REQUESTS_USER_AGENT)
    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)
    _ensure_tls_verification_enabled(session, request_kwargs)

    import requests

    requester = getattr(session if session is not None else requests, method_name)
    retry_transport_once = method_name == "get" and allow_reserved_tun_for_allowed_hosts
    for redirect_count in range(max_redirects + 1):
        for transport_attempt in range(2 if retry_transport_once else 1):
            try:
                response = requester(
                    current_url,
                    headers=request_headers,
                    timeout=timeout,
                    allow_redirects=False,
                    **request_kwargs,
                )
                break
            except (requests.RequestException, OSError):
                if not retry_transport_once or transport_attempt:
                    raise
                # The supplied session belongs to the caller. Do not close it here:
                # curl_cffi sessions are not reusable after close(). Revalidate and
                # reissue one idempotent GET so the current DNS can take effect.
                current_url = _validated_https_url(
                    current_url,
                    allowed_hosts=allowed_hosts,
                    allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
                )
        redirect_location = _response_redirect_location(response)
        if redirect_location is None:
            return response
        _close_redirect_response(response)
        if redirect_count >= max_redirects:
            raise ValueError(f"too many HTTPS redirects: {url!r}")
        redirect_url = _validated_redirect_url(
            current_url,
            redirect_location,
            allowed_hosts=allowed_hosts,
            allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
        )
        request_headers, request_kwargs = _redirect_request_parameters(
            method_name, session, current_url, redirect_url, request_headers, request_kwargs
        )
        current_url = redirect_url
    raise ValueError(f"too many HTTPS redirects: {url!r}")


def requests_get_https(
    url: str,
    *,
    session=None,
    headers: Mapping[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    allowed_hosts: Collection[str] | str | None = None,
    allow_reserved_tun_for_allowed_hosts: bool = False,
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
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
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
    allow_reserved_tun_for_allowed_hosts: bool = False,
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
        allow_reserved_tun_for_allowed_hosts=allow_reserved_tun_for_allowed_hosts,
        max_redirects=max_redirects,
        **kwargs,
    )
