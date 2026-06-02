import urllib.request

import pytest

from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT, ensure_https_request, requests_get_https


def test_ensure_https_request_accepts_https_request_object():
    request = urllib.request.Request("https://example.com/path")

    assert ensure_https_request(request) is request


@pytest.mark.parametrize("url", ["http://example.com/path", "file:///C:/secret.txt", "/relative/path"])
def test_ensure_https_request_rejects_non_https_urls(url):
    with pytest.raises(ValueError):
        ensure_https_request(url)


def test_requests_get_https_uses_session_and_default_user_agent():
    calls = []

    class DummySession:
        def get(self, url, *, headers=None, timeout=None):
            calls.append((url, headers, timeout))
            return object()

    response = requests_get_https("https://example.com/data", session=DummySession(), timeout=(1, 2))

    assert response is not None
    assert calls == [
        (
            "https://example.com/data",
            {"User-Agent": DEFAULT_REQUESTS_USER_AGENT},
            (1, 2),
        )
    ]


def test_requests_get_https_preserves_custom_user_agent():
    calls = []

    class DummySession:
        def get(self, url, *, headers=None, timeout=None):
            calls.append(headers)
            return object()

    requests_get_https("https://example.com/data", session=DummySession(), headers={"User-Agent": "custom"})

    assert calls == [{"User-Agent": "custom"}]
