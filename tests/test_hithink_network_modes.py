from __future__ import annotations

import socket
import ssl
import urllib.request

import pytest

from infra.http_safety import BlockedHttpsHostError, ensure_https_request, urlopen_https

_HITHINK_HOST = "fuyao.aicubes.cn"
_HITHINK_URL = f"https://{_HITHINK_HOST}/api/a-share/prices/snapshot"
_HITHINK_ALLOWED_HOSTS = {_HITHINK_HOST}
# This is an optional VPN TUN address, not a public-vendor IP pin.
_HITHINK_RESERVED_TUN_HOST_ADDRESSES = {_HITHINK_HOST: {"198.18.0.67"}}


def _patch_dns(monkeypatch, addresses_by_host: dict[str, list[str]]) -> None:
    def resolve(hostname, *_args, **_kwargs):
        addresses = addresses_by_host.get(str(hostname).rstrip(".").lower(), ["93.184.216.34"])
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 0, 0, 0) if ":" in address else (address, 0),
            )
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _hithink_request_kwargs() -> dict:
    return {
        "allowed_hosts": _HITHINK_ALLOWED_HOSTS,
        "reserved_tun_host_addresses": _HITHINK_RESERVED_TUN_HOST_ADDRESSES,
    }


def test_hithink_allows_only_the_exact_configured_reserved_tun_address(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.67"]})

    assert ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs()) == _HITHINK_URL


@pytest.mark.parametrize(
    "resolved_addresses",
    [
        ["198.18.0.68"],
        ["198.18.0.67", "198.18.0.68"],
        ["198.18.0.67", "93.184.216.34"],
    ],
)
def test_hithink_rejects_mismatched_or_mixed_reserved_tun_addresses(monkeypatch, resolved_addresses):
    _patch_dns(monkeypatch, {_HITHINK_HOST: resolved_addresses})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs())


@pytest.mark.parametrize("public_address", ["93.184.216.34", "2001:4860:4860::8888"])
def test_hithink_allows_direct_public_dns_for_its_allowlisted_https_host(monkeypatch, public_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: [public_address]})

    assert ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs()) == _HITHINK_URL


@pytest.mark.parametrize(
    "blocked_address",
    ["127.0.0.1", "10.0.0.8", "100.64.0.1", "169.254.10.1", "198.18.0.68", "::1", "fe80::1"],
)
def test_hithink_rejects_private_local_cgnat_and_unapproved_reserved_addresses(monkeypatch, blocked_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: [blocked_address]})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs())


def test_hithink_direct_public_path_still_rejects_a_non_allowlisted_hostname(monkeypatch):
    _patch_dns(monkeypatch, {"evil.example": ["93.184.216.34"]})

    with pytest.raises(ValueError, match="HTTPS host is not allowed"):
        ensure_https_request("https://evil.example/steal", **_hithink_request_kwargs())


def test_hithink_legacy_benchmark_parameter_remains_compatible(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.67"]})

    assert ensure_https_request(
        _HITHINK_URL,
        allowed_hosts=_HITHINK_ALLOWED_HOSTS,
        benchmark_resolver_addresses=_HITHINK_RESERVED_TUN_HOST_ADDRESSES,
    ) == _HITHINK_URL


def test_urlopen_https_keeps_tun_policy_across_redirects_and_uses_verified_tls_context(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["93.184.216.34"]})
    captured_handlers = []
    response = object()

    class DummyOpener:
        def __init__(self, redirect_handler):
            self._redirect_handler = redirect_handler

        def open(self, request, *args, **kwargs):
            original = urllib.request.Request(request) if isinstance(request, str) else request
            redirected = self._redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "/next",
            )
            assert redirected.full_url == f"https://{_HITHINK_HOST}/next"
            return response

    def build_opener(*handlers):
        captured_handlers.extend(handlers)
        redirect_handler = next(handler for handler in handlers if hasattr(handler, "redirect_request"))
        return DummyOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    assert urlopen_https(_HITHINK_URL, **_hithink_request_kwargs()) is response

    https_handler = next(handler for handler in captured_handlers if isinstance(handler, urllib.request.HTTPSHandler))
    context = https_handler._context
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_urlopen_https_rejects_redirect_to_non_allowlisted_public_host(monkeypatch):
    _patch_dns(
        monkeypatch,
        {
            _HITHINK_HOST: ["93.184.216.34"],
            "evil.example": ["93.184.216.34"],
        },
    )

    class DummyOpener:
        def __init__(self, redirect_handler):
            self._redirect_handler = redirect_handler

        def open(self, request, *args, **kwargs):
            original = urllib.request.Request(request) if isinstance(request, str) else request
            self._redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "https://evil.example/redirected",
            )
            raise AssertionError("redirect target must be rejected before opening it")

    def build_opener(*handlers):
        redirect_handler = next(handler for handler in handlers if hasattr(handler, "redirect_request"))
        return DummyOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    with pytest.raises(ValueError, match="HTTPS host is not allowed"):
        urlopen_https(_HITHINK_URL, **_hithink_request_kwargs())


def test_hithink_network_validation_error_does_not_expose_api_key_header(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.68"]})
    secret = "hithink-test-secret"
    request = urllib.request.Request(_HITHINK_URL, headers={"X-api-key": secret})

    with pytest.raises(BlockedHttpsHostError) as exc_info:
        ensure_https_request(request, **_hithink_request_kwargs())

    assert secret not in str(exc_info.value)
