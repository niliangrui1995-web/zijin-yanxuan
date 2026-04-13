# -*- coding: utf-8 -*-
"""yfinance 会话辅助工具。

将 Yahoo Finance 的域名改写限制在自定义 curl_cffi Session 内部，
避免通过 monkey patch 改写全局 requests / curl_cffi 行为。
"""

from __future__ import annotations

from curl_cffi import requests as curl_requests

YF_HIJACK_DOMAINS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
YF_HIJACK_TO = "yf.niliangrui.cloud"


def rewrite_yfinance_url(url: str) -> str:
    """仅改写 yfinance 访问的核心 Yahoo API 域名。"""
    if not isinstance(url, str):
        return url
    for domain in YF_HIJACK_DOMAINS:
        if domain in url:
            return url.replace(domain, YF_HIJACK_TO)
    return url


class CfTunnelSession(curl_requests.Session):
    """只在当前 Session 内部做 URL 改写，不影响进程内其它网络请求。"""

    def request(self, method, url, *args, **kwargs):
        return super().request(method, rewrite_yfinance_url(url), *args, **kwargs)


def build_yf_session(use_cf_proxy: bool = True):
    """构建 yfinance 可接受的 curl_cffi Session。"""
    session_cls = CfTunnelSession if use_cf_proxy else curl_requests.Session
    return session_cls(impersonate="chrome")
