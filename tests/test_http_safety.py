import urllib.request

import pytest

from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT, ensure_https_request, requests_get_https, urlopen_https


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


@pytest.mark.parametrize("url", ["https://localhost/path", "https://127.0.0.1/path", "https://10.0.0.8/path", "https://host.local/path"])
def test_ensure_https_request_rejects_private_or_local_hosts(url):
    with pytest.raises(ValueError):
        ensure_https_request(url)


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

    def build_opener(redirect_handler):
        return DummyOpener(redirect_handler)

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(
        urllib.request,
        "install_opener",
        lambda _opener: (_ for _ in ()).throw(AssertionError("global opener must not be modified")),
    )

    with pytest.raises(ValueError, match="private or local HTTPS hosts"):
        urlopen_https("https://example.com/data")
