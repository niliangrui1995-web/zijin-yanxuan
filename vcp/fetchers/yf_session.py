# -*- coding: utf-8 -*-
"""yfinance 会话辅助工具。"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time

import certifi
from curl_cffi import requests as curl_requests

try:
    from yfinance.exceptions import YFRateLimitError
except (ImportError, ModuleNotFoundError):  # pragma: no cover - defensive fallback

    class YFRateLimitError(Exception):
        """Fallback placeholder when yfinance exceptions are unavailable."""

        pass


_DEFAULT_YF_RATE_LIMIT_COOLDOWN_SEC = 15 * 60
_YF_RATE_LIMIT_LOCK = threading.Lock()
_YF_RATE_LIMIT_UNTIL_TS = 0.0
_YF_RATE_LIMIT_REASON = ""


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


def is_yf_rate_limit_error(exc: BaseException | None) -> bool:
    """统一识别 Yahoo Finance / yfinance 的限流异常。"""
    if exc is None:
        return False
    if isinstance(exc, YFRateLimitError):
        return True
    error_text = str(exc or "")
    return (
        "Too Many Requests" in error_text
        or "Rate limited" in error_text
        or "YFRateLimitError" in error_text
        or "HTTP 429" in error_text
        or " 429" in error_text
    )


def mark_yf_rate_limited(
    exc: BaseException | str | None = None,
    cooldown_sec: float = _DEFAULT_YF_RATE_LIMIT_COOLDOWN_SEC,
) -> float:
    """记录 Yahoo Finance 进入冷却期，并返回剩余秒数。"""
    global _YF_RATE_LIMIT_REASON
    global _YF_RATE_LIMIT_UNTIL_TS

    now_ts = time.time()
    ttl = max(float(cooldown_sec or 0.0), 1.0)
    reason = str(exc or "Too Many Requests. Rate limited. Try after a while.")
    with _YF_RATE_LIMIT_LOCK:
        _YF_RATE_LIMIT_UNTIL_TS = max(_YF_RATE_LIMIT_UNTIL_TS, now_ts + ttl)
        _YF_RATE_LIMIT_REASON = reason
        return max(0.0, _YF_RATE_LIMIT_UNTIL_TS - now_ts)


def clear_yf_rate_limit() -> None:
    """清理共享冷却状态，供测试或人工恢复使用。"""
    global _YF_RATE_LIMIT_REASON
    global _YF_RATE_LIMIT_UNTIL_TS

    with _YF_RATE_LIMIT_LOCK:
        _YF_RATE_LIMIT_UNTIL_TS = 0.0
        _YF_RATE_LIMIT_REASON = ""


def get_yf_rate_limit_status() -> dict[str, float | str | bool]:
    """读取 Yahoo Finance 当前是否处于冷却期。"""
    with _YF_RATE_LIMIT_LOCK:
        until_ts = _YF_RATE_LIMIT_UNTIL_TS
        reason = _YF_RATE_LIMIT_REASON

    remaining_sec = max(0.0, until_ts - time.time())
    return {
        "active": remaining_sec > 0.0,
        "remaining_sec": remaining_sec,
        "reason": reason,
        "until_ts": until_ts,
    }


def build_yf_session():
    """构建 yfinance 可接受的 curl_cffi Session。"""
    session = curl_requests.Session(impersonate="chrome")
    session.verify = resolve_curl_cffi_verify_path()
    return session
