# -*- coding: utf-8 -*-
"""yfinance 会话辅助工具。

将 Yahoo Finance 的域名改写限制在自定义 curl_cffi Session 内部，
避免通过 monkey patch 改写全局 requests / curl_cffi 行为。
"""

from __future__ import annotations

import os
import shutil
import tempfile

import certifi
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


def _is_ascii_path(path: str) -> bool:
    try:
        str(path or "").encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def resolve_curl_cffi_verify_path() -> str:
    """为 curl_cffi 提供 ASCII 证书路径，避免中文路径下证书加载失败。"""
    ca_path = certifi.where()
    if _is_ascii_path(ca_path):
        return ca_path

    target_dir = os.path.join(tempfile.gettempdir(), "vcp_hunter")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "curl_cffi_cacert.pem")
    if (not os.path.exists(target_path)) or os.path.getmtime(target_path) < os.path.getmtime(ca_path):
        shutil.copyfile(ca_path, target_path)
    return target_path


def build_yf_session(use_cf_proxy: bool = True):
    """构建 yfinance 可接受的 curl_cffi Session。"""
    session_cls = CfTunnelSession if use_cf_proxy else curl_requests.Session
    session = session_cls(impersonate="chrome")
    session.verify = resolve_curl_cffi_verify_path()
    return session
