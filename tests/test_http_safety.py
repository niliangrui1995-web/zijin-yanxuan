import socket
import urllib.request

import pytest

from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT, ensure_https_request, requests_get_https, urlopen_https


@pytest.fixture(autouse=True)
def _resolve_hostnames_to_public_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


def test_ensure_https_request_accepts_https_request_object():
    request = urllib.request.Request("https://example.com/path")

    assert ensure_https_request(request) is request


def test_ensure_https_request_rejects_non_string_request_url():
    with pytest.raises(TypeError):
        ensure_https_request(object())


@pytest.mark.parametrize("url", ["http://example.com/path", "file:///C:/secret.txt", "/relative/path"])
def test_ensure_https_request_rejects_non_https_urls(url):
    with pytest.raises(ValueError):
        ensure_https_request(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/path",
        "https://127.0.0.1/path",
        "https://10.0.0.8/path",
        "https://198.18.0.67/path",
        "https://host.local/path",
    ],
)
def test_ensure_https_request_rejects_private_or_local_hosts(url):
    with pytest.raises(ValueError):
        ensure_https_request(url)


def test_ensure_https_request_rejects_hostname_with_any_cgnat_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 0)),
        ],
    )

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        ensure_https_request("https://dns-rebinding.example/path")


def test_ensure_https_request_allows_only_exact_allowlisted_hostname_at_benchmark_resolver_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.67", 0))],
    )
    allowed_hosts = {"fuyao.aicubes.cn"}
    benchmark_resolver_addresses = {"fuyao.aicubes.cn": {"198.18.0.67"}}

    assert ensure_https_request(
        "https://fuyao.aicubes.cn/api/a-share/prices/snapshot",
        allowed_hosts=allowed_hosts,
        benchmark_resolver_addresses=benchmark_resolver_addresses,
    )

    with pytest.raises(ValueError):
        ensure_https_request(
            "https://evil.example/path",
            allowed_hosts=allowed_hosts,
            benchmark_resolver_addresses=benchmark_resolver_addresses,
        )
    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        ensure_https_request(
            "https://fuyao.aicubes.cn:8443/path",
            allowed_hosts=allowed_hosts,
            benchmark_resolver_addresses=benchmark_resolver_addresses,
        )
    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        ensure_https_request(
            "https://fuyao.aicubes.cn/path",
            benchmark_resolver_addresses=benchmark_resolver_addresses,
        )
    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        ensure_https_request(
            "https://198.18.0.67/path",
            allowed_hosts={"198.18.0.67"},
            benchmark_resolver_addresses={"198.18.0.67": {"198.18.0.67"}},
        )


@pytest.mark.parametrize(
    "resolved_addresses",
    [
        ["198.18.0.67", "198.18.0.68"],
        ["198.18.0.67", "93.184.216.34"],
        ["198.18.0.67", "2001:4860:4860::8888"],
    ],
)
def test_ensure_https_request_rejects_mixed_or_unpinned_benchmark_dns_answers(monkeypatch, resolved_addresses):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in resolved_addresses
        ],
    )

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        ensure_https_request(
            "https://fuyao.aicubes.cn/api/a-share/prices/snapshot",
            allowed_hosts={"fuyao.aicubes.cn"},
            benchmark_resolver_addresses={"fuyao.aicubes.cn": {"198.18.0.67"}},
        )


def test_ensure_https_request_enforces_allowed_hosts():
    assert ensure_https_request("https://www.jpx.co.jp/files/a.xlsx", allowed_hosts={"www.jpx.co.jp"})

    with pytest.raises(ValueError):
        ensure_https_request("https://evil.example/files/a.xlsx", allowed_hosts={"www.jpx.co.jp"})


def test_requests_get_https_uses_session_and_default_user_agent():
    calls = []

    class DummySession:
        def get(self, url, *, headers=None, timeout=None, allow_redirects=None):
            calls.append((url, headers, timeout, allow_redirects))
            return object()

    response = requests_get_https("https://example.com/data", session=DummySession(), timeout=(1, 2))

    assert response is not None
    assert calls == [
        (
            "https://example.com/data",
            {"User-Agent": DEFAULT_REQUESTS_USER_AGENT},
            (1, 2),
            False,
        )
    ]


def test_requests_get_https_preserves_custom_user_agent():
    calls = []

    class DummySession:
        def get(self, url, *, headers=None, timeout=None, allow_redirects=None):
            calls.append((headers, allow_redirects))
            return object()

    requests_get_https("https://example.com/data", session=DummySession(), headers={"User-Agent": "custom"})

    assert calls == [({"User-Agent": "custom"}, False)]


def test_requests_get_https_requires_redirect_control_from_custom_session():
    class LegacySession:
        def get(self, _url, *, headers=None, timeout=None):
            raise AssertionError("request must not run without redirect control")

    with pytest.raises(TypeError, match="allow_redirects"):
        requests_get_https("https://example.com/data", session=LegacySession())


def test_requests_get_https_validates_safe_redirect_chain():
    calls = []

    class DummyResponse:
        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}
            self.closed = False

        def close(self):
            self.closed = True

    redirect = DummyResponse(302, {"Location": "/next"})
    final = DummyResponse(200)
    responses = [redirect, final]

    class DummySession:
        def get(self, url, *, headers=None, timeout=None, allow_redirects=None):
            calls.append((url, allow_redirects))
            return responses.pop(0)

    response = requests_get_https("https://example.com/data", session=DummySession())

    assert response is final
    assert redirect.closed is True
    assert calls == [("https://example.com/data", False), ("https://example.com/next", False)]


def test_requests_get_https_rejects_unsafe_redirect_target():
    class DummyResponse:
        status_code = 302
        headers = {"Location": "https://127.0.0.1/private"}

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    redirect = DummyResponse()

    class DummySession:
        def get(self, url, *, headers=None, timeout=None, allow_redirects=None):
            return redirect

    with pytest.raises(ValueError):
        requests_get_https("https://example.com/data", session=DummySession())

    assert redirect.closed is True


def test_requests_get_https_rejects_redirect_hostname_with_cgnat_address(monkeypatch):
    def resolve(hostname, *_args, **_kwargs):
        address = "100.64.0.1" if hostname == "dns-rebinding.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    class DummyResponse:
        status_code = 302
        headers = {"Location": "https://dns-rebinding.example/private"}

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    redirect = DummyResponse()

    class DummySession:
        @staticmethod
        def get(_url, *, headers=None, timeout=None, allow_redirects=None):
            return redirect

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        requests_get_https("https://example.com/data", session=DummySession())

    assert redirect.closed is True


def test_urlopen_https_uses_local_opener_without_installing_global_opener(monkeypatch):
    calls = []
    response = object()

    class DummyOpener:
        def open(self, request, *args, **kwargs):
            calls.append((request, args, kwargs))
            return response

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: DummyOpener())
    monkeypatch.setattr(
        urllib.request,
        "install_opener",
        lambda _opener: (_ for _ in ()).throw(AssertionError("global opener must not be modified")),
    )

    request = urllib.request.Request("https://example.com/data")
    assert urlopen_https(request, timeout=3) is response
    assert calls == [(request, (), {"timeout": 3})]


def test_urlopen_https_rejects_unsafe_redirect_target(monkeypatch):
    class DummyOpener:
        def __init__(self, redirect_handler):
            self._redirect_handler = redirect_handler

        def open(self, request, *args, **kwargs):
            request = urllib.request.Request(request) if isinstance(request, str) else request
            self._redirect_handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://127.0.0.1/private",
            )
            raise AssertionError("unsafe redirect should fail before opener returns")

    def build_opener(*handlers):
        redirect_handler = next(handler for handler in handlers if hasattr(handler, "redirect_request"))
        return DummyOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(
        urllib.request,
        "install_opener",
        lambda _opener: (_ for _ in ()).throw(AssertionError("global opener must not be modified")),
    )

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        urlopen_https("https://example.com/data")


def test_urlopen_https_rejects_benchmark_hostname_redirect_to_nondefault_port(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.67", 0))],
    )

    class DummyOpener:
        def __init__(self, redirect_handler):
            self._redirect_handler = redirect_handler

        def open(self, request, *args, **kwargs):
            request = urllib.request.Request(request) if isinstance(request, str) else request
            self._redirect_handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://fuyao.aicubes.cn:8443/redirected",
            )
            raise AssertionError("unsafe redirect should fail before opener returns")

    def build_opener(*handlers):
        redirect_handler = next(handler for handler in handlers if hasattr(handler, "redirect_request"))
        return DummyOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        urlopen_https(
            "https://fuyao.aicubes.cn/api/a-share/prices/snapshot",
            allowed_hosts={"fuyao.aicubes.cn"},
            benchmark_resolver_addresses={"fuyao.aicubes.cn": {"198.18.0.67"}},
        )
