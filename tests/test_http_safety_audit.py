# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.http_safety_audit import LEGACY_ALLOWED_DIRECT_HTTP_FILES, SOURCE_ROOTS, build_report, scan_direct_http


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


def test_http_safety_audit_rejects_imported_requests_method_aliases(tmp_path):
    _write(
        tmp_path / "app" / "bad_http_alias.py",
        """
from requests import get as raw_get


def fetch():
    return raw_get("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "fail"
    assert report["disallowed_count"] == 1
    assert report["findings"][0]["line"] == 6


def test_http_safety_audit_rejects_inline_requests_session_calls(tmp_path):
    _write(
        tmp_path / "app" / "bad_http_session.py",
        """
import requests
from requests import Session


def fetch():
    requests.Session().get("https://example.com")
    Session().post("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "fail"
    assert report["disallowed_count"] == 2
    assert {finding["line"] for finding in report["findings"]} == {7, 8}


def test_http_safety_audit_rejects_curl_cffi_direct_calls(tmp_path):
    _write(
        tmp_path / "app" / "bad_curl_http.py",
        """
import curl_cffi.requests as curl_requests
from curl_cffi.requests import get as curl_get


def fetch():
    curl_requests.get("https://example.com")
    return curl_get("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "fail"
    assert report["disallowed_count"] == 2
    assert {finding["line"] for finding in report["findings"]} == {7, 8}


def test_http_safety_audit_rejects_curl_cffi_session_calls(tmp_path):
    _write(
        tmp_path / "app" / "bad_curl_session.py",
        """
from curl_cffi import requests as curl_requests
from curl_cffi.requests import Session


def fetch():
    curl_requests.Session().get("https://example.com")
    return Session().post("https://example.com")
""",
    )

    report = build_report(scan_direct_http(tmp_path, ("app",)))

    assert report["status"] == "fail"
    assert report["disallowed_count"] == 2
    assert {finding["line"] for finding in report["findings"]} == {7, 8}


def test_http_safety_audit_allows_safety_wrapper_calls(tmp_path):
    _write(
        tmp_path / "app" / "good_http.py",
        """
from infra.http_safety import requests_get_https, requests_post_https


def fetch():
    requests_get_https("https://example.com")
    return requests_post_https("https://example.com", data={"ok": "1"})
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


def test_http_safety_audit_keeps_migrated_earnings_providers_off_legacy_allowlist():
    assert "domains/global_earnings_calendar/providers/alpha_vantage.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "domains/global_earnings_calendar/providers/company_ir.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "domains/global_earnings_calendar/providers/nasdaq.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "domains/global_earnings_calendar/providers/sec.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "domains/global_earnings_calendar/providers/asia_disclosures.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "ui/tabs/asian_market_workers.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES
    assert "vcp/fetchers/asian_kline_fetcher.py" not in LEGACY_ALLOWED_DIRECT_HTTP_FILES


def test_http_safety_audit_default_roots_include_scripts_and_earnings():
    assert "earnings" in SOURCE_ROOTS
    assert "scripts" in SOURCE_ROOTS
