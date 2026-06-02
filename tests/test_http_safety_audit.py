# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.http_safety_audit import build_report, scan_direct_http


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_http_safety_audit_rejects_new_direct_requests_calls(tmp_path):
    _write(
        tmp_path / "app" / "bad_http.py",
        """
import requests


def fetch(session):
    requests.get("https://example.com")
    session.post("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "fail"
    assert report["disallowed_count"] == 2
    assert {finding["line"] for finding in report["findings"]} == {6, 7}


def test_http_safety_audit_allows_safety_wrapper_calls(tmp_path):
    _write(
        tmp_path / "app" / "good_http.py",
        """
from infra.http_safety import requests_get_https


def fetch():
    return requests_get_https("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "ok"
    assert report["findings"] == []


def test_http_safety_audit_tracks_legacy_allowlisted_direct_calls(tmp_path):
    _write(
        tmp_path / "infra" / "http_safety.py",
        """
import urllib.request


def fetch(request):
    return urllib.request.urlopen(request)
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("infra",)))

    assert report["status"] == "ok"
    assert report["allowed_count"] == 1
    assert report["findings"][0]["allowed"] is True
    assert "central HTTPS wrapper" in report["findings"][0]["reason"]
