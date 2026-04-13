from curl_cffi import requests as curl_requests

from vcp.fetchers.yf_session import build_yf_session, rewrite_yfinance_url


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
