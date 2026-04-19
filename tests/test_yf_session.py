from pathlib import Path

from curl_cffi import requests as curl_requests

from vcp.fetchers.yf_session import (
    build_yf_session,
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
