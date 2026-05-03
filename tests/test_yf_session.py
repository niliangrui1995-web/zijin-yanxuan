from pathlib import Path

from curl_cffi import requests as curl_requests
from yfinance.exceptions import YFRateLimitError

from vcp.fetchers.yf_session import (
    build_yf_session,
    clear_yf_rate_limit,
    get_yf_rate_limit_status,
    is_yf_rate_limit_error,
    mark_yf_rate_limited,
    resolve_curl_cffi_verify_path,
    rewrite_yfinance_url,
)


def test_rewrite_yfinance_url_only_updates_target_domains():
    assert rewrite_yfinance_url(
        "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW"
    ) == "https://yf.niliangrui.cloud/v8/finance/chart/2330.TW"
    assert rewrite_yfinance_url(
        "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL"
    ) == "https://yf.niliangrui.cloud/v10/finance/quoteSummary/AAPL"
    assert rewrite_yfinance_url("https://fc.yahoo.com") == "https://fc.yahoo.com"


def test_build_yf_session_with_cf_proxy_rewrites_only_local_session(monkeypatch):
    captured = {}

    def fake_request(self, method, url, *args, **kwargs):
        captured["url"] = url
        return {"ok": True}

    monkeypatch.setattr(curl_requests.Session, "request", fake_request)

    session = build_yf_session(use_cf_proxy=True)
    result = session.request("GET", "https://query1.finance.yahoo.com/v8/finance/chart/8035.T")

    assert result == {"ok": True}
    assert captured["url"] == "https://yf.niliangrui.cloud/v8/finance/chart/8035.T"


def test_build_yf_session_without_cf_proxy_keeps_original_url(monkeypatch):
    captured = {}

    def fake_request(self, method, url, *args, **kwargs):
        captured["url"] = url
        return {"ok": True}

    monkeypatch.setattr(curl_requests.Session, "request", fake_request)

    session = build_yf_session(use_cf_proxy=False)
    result = session.request("GET", "https://query1.finance.yahoo.com/v8/finance/chart/8035.T")

    assert result == {"ok": True}
    assert captured["url"] == "https://query1.finance.yahoo.com/v8/finance/chart/8035.T"


def test_build_yf_session_default_keeps_original_yahoo_domain(monkeypatch):
    captured = {}

    def fake_request(self, method, url, *args, **kwargs):
        captured["url"] = url
        return {"ok": True}

    monkeypatch.setattr(curl_requests.Session, "request", fake_request)

    session = build_yf_session()
    result = session.request("GET", "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL")

    assert result == {"ok": True}
    assert captured["url"] == "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL"


def test_resolve_curl_cffi_verify_path_copies_non_ascii_bundle(tmp_path, monkeypatch):
    source_dir = tmp_path / "中文目录"
    source_dir.mkdir()
    source_bundle = source_dir / "cacert.pem"
    source_bundle.write_text("dummy-cert", encoding="utf-8")

    monkeypatch.setattr("vcp.fetchers.yf_session.certifi.where", lambda: str(source_bundle))
    monkeypatch.setattr("vcp.fetchers.yf_session.tempfile.gettempdir", lambda: str(tmp_path / "ascii_tmp"))

    resolved = resolve_curl_cffi_verify_path()

    assert Path(resolved).exists()
    assert Path(resolved).read_text(encoding="utf-8") == "dummy-cert"
    resolved.encode("ascii")


def test_is_yf_rate_limit_error_matches_yfinance_and_429_messages():
    assert is_yf_rate_limit_error(YFRateLimitError()) is True
    assert is_yf_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests")) is True
    assert is_yf_rate_limit_error(ValueError("plain error")) is False


def test_mark_yf_rate_limited_tracks_shared_cooldown():
    clear_yf_rate_limit()
    try:
        remaining = mark_yf_rate_limited("HTTP 429 Too Many Requests", cooldown_sec=12)
        status = get_yf_rate_limit_status()

        assert remaining > 0
        assert status["active"] is True
        assert 0 < status["remaining_sec"] <= 12
        assert "429" in status["reason"]
    finally:
        clear_yf_rate_limit()
