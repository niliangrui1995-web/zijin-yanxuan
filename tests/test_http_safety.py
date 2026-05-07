import urllib.request

import pytest

from infra.http_safety import ensure_https_request


def test_ensure_https_request_accepts_https_request_object():
    request = urllib.request.Request("https://example.com/path")

    assert ensure_https_request(request) is request


@pytest.mark.parametrize("url", ["http://example.com/path", "file:///C:/secret.txt", "/relative/path"])
def test_ensure_https_request_rejects_non_https_urls(url):
    with pytest.raises(ValueError):
        ensure_https_request(url)
