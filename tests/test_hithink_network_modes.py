from __future__ import annotations

import json
import socket
import ssl
import urllib.request
from types import SimpleNamespace

import pytest
import requests

import infra.http_safety as http_safety
from infra.http_safety import BlockedHttpsHostError, ensure_https_request, requests_get_https, urlopen_https
from vcp import data_provider_quotes

_HITHINK_HOST = "fuyao.aicubes.cn"
_HITHINK_URL = f"https://{_HITHINK_HOST}/api/a-share/prices/snapshot"
_HITHINK_ALLOWED_HOSTS = {_HITHINK_HOST}


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
        "allow_reserved_tun_for_allowed_hosts": True,
    }


@pytest.mark.parametrize(
    "resolved_addresses",
    [
        ["198.18.0.67"],
        ["198.18.0.87"],
        ["198.19.255.254"],
        ["198.18.0.67", "198.18.0.87"],
        ["198.18.0.87", "93.184.216.34"],
    ],
)
def test_hithink_allows_fake_ip_tun_dns_without_local_route_validation(monkeypatch, resolved_addresses):
    _patch_dns(monkeypatch, {_HITHINK_HOST: resolved_addresses})
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local TUN route must not be probed")),
    )

    assert ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs()) == _HITHINK_URL


@pytest.mark.parametrize("public_address", ["93.184.216.34", "2001:4860:4860::8888"])
def test_hithink_allows_direct_public_dns(monkeypatch, public_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: [public_address]})

    assert ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs()) == _HITHINK_URL


@pytest.mark.parametrize(
    "blocked_address",
    ["127.0.0.1", "10.0.0.8", "100.64.0.1", "169.254.10.1", "::1", "fe80::1"],
)
def test_hithink_still_rejects_private_local_and_cgnat_addresses(monkeypatch, blocked_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: [blocked_address]})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs())


@pytest.mark.parametrize("resolved_address", ["93.184.216.34", "198.18.0.87"])
def test_hithink_still_requires_default_https_port(monkeypatch, resolved_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: [resolved_address]})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(f"https://{_HITHINK_HOST}:8443/api/a-share/prices/snapshot", **_hithink_request_kwargs())


def test_hithink_still_rejects_non_allowlisted_hostname(monkeypatch):
    _patch_dns(monkeypatch, {"evil.example": ["93.184.216.34"]})

    with pytest.raises(ValueError, match="HTTPS host is not allowed"):
        ensure_https_request("https://evil.example/steal", **_hithink_request_kwargs())


def test_fake_ip_tun_dns_stays_limited_to_trusted_vendor_hosts(monkeypatch):
    _patch_dns(monkeypatch, {"evil.example": ["198.18.0.87"]})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(
            "https://evil.example/steal",
            allowed_hosts={"evil.example"},
            allow_reserved_tun_for_allowed_hosts=True,
        )


@pytest.mark.parametrize("private_address", ["127.0.0.1", "10.0.0.8", "100.64.0.1"])
def test_fake_ip_and_private_dns_answer_still_fails(monkeypatch, private_address):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.87", private_address]})

    with pytest.raises(BlockedHttpsHostError, match="private or local HTTPS hosts"):
        ensure_https_request(_HITHINK_URL, **_hithink_request_kwargs())


def test_urlopen_https_keeps_fake_ip_tun_requests_and_verified_tls(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.87"]})
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
            _HITHINK_HOST: ["198.18.0.87"],
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


@pytest.mark.parametrize("transport_error", [requests.ConnectionError("VPN connection closed"), OSError("VPN connection closed")])
def test_requests_get_reissues_after_vpn_switch_without_local_block(monkeypatch, transport_error):
    addresses = iter(
        (
            (http_safety.ipaddress.ip_address("198.18.0.87"),),
            (http_safety.ipaddress.ip_address("93.184.216.34"),),
        )
    )
    monkeypatch.setattr(http_safety, "_host_addresses", lambda _host: next(addresses))

    class Response:
        status_code = 200
        headers = {}

    class Session:
        def __init__(self):
            self.calls = 0
            self.closed = 0

        def get(self, _url, *, headers=None, timeout=None, allow_redirects=None):
            del headers, timeout, allow_redirects
            self.calls += 1
            if self.calls == 1:
                raise transport_error
            return Response()

        def close(self):
            self.closed += 1

    session = Session()
    response = requests_get_https(_HITHINK_URL, session=session, **_hithink_request_kwargs())

    assert isinstance(response, Response)
    assert session.calls == 2
    assert session.closed == 0


def test_hithink_quote_retries_after_tun_to_direct_switch(monkeypatch):
    addresses = iter(
        (
            (http_safety.ipaddress.ip_address("198.18.0.87"),),
            (http_safety.ipaddress.ip_address("93.184.216.34"),),
        )
    )
    monkeypatch.setattr(http_safety, "_host_addresses", lambda _host: next(addresses))
    calls = []

    class Response:
        def read(self):
            return json.dumps(
                {
                    "code": 0,
                    "data": {
                        "item": [
                            {
                                "thscode": "000001.SZ",
                                "last_price": 10.1,
                                "open_price": 10.0,
                                "high_price": 10.2,
                                "low_price": 9.9,
                                "prev_price": 10.0,
                                "volume": 100,
                                "turnover": 1010,
                            }
                        ]
                    },
                }
            ).encode("utf-8")

        @staticmethod
        def close():
            return None

    def fake_urlopen(request, **kwargs):
        ensure_https_request(
            request,
            allowed_hosts=kwargs["allowed_hosts"],
            allow_reserved_tun_for_allowed_hosts=kwargs["allow_reserved_tun_for_allowed_hosts"],
        )
        calls.append(request.full_url)
        if len(calls) == 1:
            raise OSError("VPN transport closed")
        return Response()

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-only-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", fake_urlopen)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda *_args: None)

    quotes = data_provider_quotes.request_hithink_quote_batch(
        SimpleNamespace(_rt_api_call_timeout_sec=1.0),
        ["000001"],
        "2026-04-15",
    )

    assert len(calls) == 2
    assert quotes["000001"]["source"] == "hithink"


def test_urlopen_get_reissues_after_tun_to_direct_switch(monkeypatch):
    addresses = iter(
        (
            (http_safety.ipaddress.ip_address("198.18.0.87"),),
            (http_safety.ipaddress.ip_address("93.184.216.34"),),
        )
    )
    monkeypatch.setattr(http_safety, "_host_addresses", lambda _host: next(addresses))
    response = object()
    attempts = []

    class DummyOpener:
        def open(self, _request, *_args, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("VPN transport closed")
            return response

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: DummyOpener())

    assert urlopen_https(_HITHINK_URL, **_hithink_request_kwargs()) is response
    assert len(attempts) == 2


@pytest.mark.parametrize(
    ("args", "request_kwargs"),
    [
        ((b"body",), {}),
        ((), {"data": b"body"}),
    ],
)
def test_urlopen_does_not_retry_a_string_url_with_a_request_body(monkeypatch, args, request_kwargs):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.87"]})
    attempts = []

    class DummyOpener:
        def open(self, _request, *_args, **_kwargs):
            attempts.append(True)
            raise OSError("VPN transport closed")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: DummyOpener())

    with pytest.raises(OSError, match="VPN transport closed"):
        urlopen_https(_HITHINK_URL, *args, **_hithink_request_kwargs(), **request_kwargs)

    assert len(attempts) == 1


def test_urlopen_does_not_retry_a_get_request_with_wrapper_body(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["198.18.0.87"]})
    attempts = []

    class DummyOpener:
        def open(self, _request, *_args, **_kwargs):
            attempts.append(True)
            raise OSError("VPN transport closed")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: DummyOpener())
    request = urllib.request.Request(_HITHINK_URL, method="GET")

    with pytest.raises(OSError, match="VPN transport closed"):
        urlopen_https(request, data=b"body", **_hithink_request_kwargs())

    assert len(attempts) == 1


def test_hithink_network_error_does_not_expose_api_key_header(monkeypatch):
    _patch_dns(monkeypatch, {_HITHINK_HOST: ["127.0.0.1"]})
    secret = "hithink-test-secret"
    request = urllib.request.Request(_HITHINK_URL, headers={"X-api-key": secret})

    with pytest.raises(BlockedHttpsHostError) as exc_info:
        ensure_https_request(request, **_hithink_request_kwargs())

    assert secret not in str(exc_info.value)
