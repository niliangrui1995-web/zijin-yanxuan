# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from core.logger import get_logger
from domains.quotes.snapshot import coerce_number
from infra.http_safety import urlopen_https
from infra.tasks.lifecycle import (
    bounded_io_timeout,
    raise_if_cancelled,
    reraise_task_cancellation,
    wait_with_cancellation,
)
from vcp.realtime_quote_batch import normalize_error_text

log = get_logger(__name__)
_CN_TZ = timezone(timedelta(hours=8))
_HITHINK_SNAPSHOT_URL = "https://fuyao.aicubes.cn/api/a-share/prices/snapshot"
_HITHINK_ALLOWED_HOSTS = {"fuyao.aicubes.cn"}
_EASTMONEY_REALTIME_HOSTS = ("push2.eastmoney.com", "88.push2.eastmoney.com")
_EASTMONEY_REALTIME_ALLOWED_HOSTS = frozenset((*_EASTMONEY_REALTIME_HOSTS, "push2delay.eastmoney.com"))
_SINA_REALTIME_ALLOWED_HOSTS = frozenset({"hq.sinajs.cn"})
_TENCENT_REALTIME_ALLOWED_HOSTS = frozenset({"qt.gtimg.cn"})
_HITHINK_RETRY_ATTEMPTS = 3
_HITHINK_RETRY_DELAY_SEC = 0.15
_HITHINK_RETRYABLE_CODES = frozenset((4001, 5001, 5002, 5003))
_HITHINK_RETRYABLE_HTTP_STATUSES = frozenset((421, 429, 500, 501, 502, 503, 504))
_HITHINK_NON_COOLDOWN_ERROR_CODES = frozenset((1001, 1002, 1003, 1004, 3001, 3002, 3004))
_HITHINK_SPLITTABLE_ERROR_CODES = frozenset((1002, 1003, 3001, 3004))
_HITHINK_MAX_ISOLATION_SPLITS = 6
_HITHINK_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
)
_HITHINK_ISOLATABLE_TRANSPORT_ERROR_TOKENS = (
    "handshake operation timed out",
    "read operation timed out",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection closed",
    "unexpected eof",
)


def _read_json_response(response, cancellation_token=None):
    payload = json.loads(response.read().decode("utf-8"))
    raise_if_cancelled(cancellation_token)
    return payload


def _read_text_response(response, encoding: str, cancellation_token=None) -> str:
    text = response.read().decode(encoding, errors="ignore")
    raise_if_cancelled(cancellation_token)
    return text


_EASTMONEY_EDGE_FAILURE_TOKENS = (
    "remote end closed connection without response",
    "connection aborted",
    "connection reset",
    "unexpected eof",
    "badstatusline",
    "10053",
    "10054",
    "http error 502",
    "bad gateway",
    "handshake operation timed out",
    "read operation timed out",
    "timed out",
)


def _is_eastmoney_edge_failure(exc_or_text) -> bool:
    normalized = normalize_error_text(exc_or_text)
    return bool(normalized) and any(token in normalized for token in _EASTMONEY_EDGE_FAILURE_TOKENS)


def ensure_eastmoney_quote_state(provider) -> None:
    if not hasattr(provider, "_rt_eastmoney_cooldown_until"):
        provider._rt_eastmoney_cooldown_until = 0.0
    if not hasattr(provider, "_rt_eastmoney_last_error"):
        provider._rt_eastmoney_last_error = ""
    if not hasattr(provider, "_rt_last_fallback_log_at"):
        provider._rt_last_fallback_log_at = 0.0


def ensure_hithink_quote_state(provider) -> None:
    if not hasattr(provider, "_rt_hithink_cooldown_until"):
        provider._rt_hithink_cooldown_until = 0.0
    if not hasattr(provider, "_rt_hithink_last_error"):
        provider._rt_hithink_last_error = ""


def log_quote_fallback(
    provider,
    message: str,
    *,
    interval_sec: float = 30.0,
    warning: bool = True,
) -> None:
    ensure_eastmoney_quote_state(provider)
    now = time.time()
    if (now - float(provider._rt_last_fallback_log_at or 0.0)) < interval_sec:
        return

    provider._rt_last_fallback_log_at = now
    (log.warning if warning else log.info)(message)


def enter_eastmoney_cooldown(
    provider,
    reason: str,
    *,
    cooldown_sec: float | None = None,
    default_cooldown_sec: float = 120.0,
) -> None:
    ensure_eastmoney_quote_state(provider)
    cooldown = float(cooldown_sec or default_cooldown_sec)
    provider._rt_eastmoney_cooldown_until = time.time() + cooldown
    provider._rt_eastmoney_last_error = reason
    log_quote_fallback(
        provider,
        f"[实时报价] 东方财富链路异常，{int(cooldown)}s 内切换新浪报价: {reason}",
    )


def register_eastmoney_success(provider) -> None:
    ensure_eastmoney_quote_state(provider)
    provider._rt_eastmoney_cooldown_until = 0.0
    provider._rt_eastmoney_last_error = ""


def enter_hithink_cooldown(
    provider,
    reason: str,
    *,
    cooldown_sec: float | None = None,
    default_cooldown_sec: float = 120.0,
) -> None:
    ensure_hithink_quote_state(provider)
    cooldown = float(cooldown_sec or default_cooldown_sec)
    safe_reason = sanitize_hithink_error(reason)
    provider._rt_hithink_cooldown_until = time.time() + cooldown
    provider._rt_hithink_last_error = safe_reason
    log_quote_fallback(
        provider,
        f"[实时报价] 同花顺金融数据链路异常，{int(cooldown)}s 内启用兼容回退: {safe_reason}",
    )


def register_hithink_success(provider) -> None:
    ensure_hithink_quote_state(provider)
    provider._rt_hithink_cooldown_until = 0.0
    provider._rt_hithink_last_error = ""


def to_eastmoney_secid(code: str) -> str:
    code = str(code).strip()
    if code.startswith("92"):
        market = 0
    else:
        market = 1 if code.startswith(("6", "9")) else 0
    return f"{market}.{code}"


def to_hithink_thscode(code: str) -> str:
    code = str(code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return ""
    if code.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif code.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def to_sina_symbol(code: str) -> str:
    code = str(code).strip()
    if code.startswith(("4", "8", "92")):
        prefix = "bj"
    elif code.startswith(("5", "6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    return f"{prefix}{code}"


def to_tencent_symbol(code: str) -> str:
    return to_sina_symbol(code)


def coerce_quote_number(value) -> float:
    return coerce_number(value)


def _quote_volume_lots_to_shares(value) -> float:
    """Normalize legacy fallback quote volumes from lots to canonical shares."""
    return coerce_quote_number(value) * 100.0


def _iso_quote_time_from_epoch(value) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, tz=_CN_TZ).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _iso_quote_time_from_milliseconds(value) -> str:
    try:
        timestamp_ms = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp_ms <= 0:
        return ""
    return _iso_quote_time_from_epoch(timestamp_ms / 1000.0)


def _iso_quote_time_from_parts(date_value, time_value) -> str:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if len(date_text) == 8 and date_text.isdigit():
        date_text = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    if len(time_text) == 6 and time_text.isdigit():
        time_text = f"{time_text[:2]}:{time_text[2:4]}:{time_text[4:6]}"
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return parsed.replace(tzinfo=_CN_TZ).isoformat(timespec="seconds")


def _network_quote_metadata(source: str, quote_time: str) -> dict:
    return {"source": source, "quote_time": quote_time, "quote_freshness": "network"}


def _hithink_api_key() -> str:
    return str(os.environ.get("HITHINK_FINANCE_API_KEY") or "").strip()


def _hithink_timeout_seconds(provider) -> float:
    raw_timeout: object = getattr(provider, "_rt_api_call_timeout_sec", 8.0)
    if not isinstance(raw_timeout, (int, float, str)):
        return 8.0
    try:
        return max(0.1, float(raw_timeout or 8.0))
    except (TypeError, ValueError):
        return 8.0


def sanitize_hithink_error(exc_or_text) -> str:
    text = " ".join(str(exc_or_text or "").split())
    if not text and isinstance(exc_or_text, TimeoutError):
        text = "timeout"
    elif not text and isinstance(exc_or_text, OSError):
        text = f"network transport error ({type(exc_or_text).__name__})"
    elif not text and isinstance(exc_or_text, BaseException):
        text = type(exc_or_text).__name__
    api_key = _hithink_api_key()
    if api_key:
        text = text.replace(api_key, "***")
    for pattern in _HITHINK_SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text[:180]


def _hithink_business_error(payload: dict) -> tuple[int, str]:
    raw_code: object = payload.get("code")
    if not isinstance(raw_code, (int, float, str)):
        raise RuntimeError("同花顺实时报价响应缺少有效业务状态码")
    try:
        code = int(raw_code)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("同花顺实时报价响应缺少有效业务状态码") from exc
    return code, f"code={code}"


def _hithink_retry_after_seconds(error) -> float:
    headers = getattr(error, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return 0.0
    value = getter("Retry-After")
    if not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


class _HithinkBusinessError(RuntimeError):
    def __init__(self, code: int):
        self.code = int(code)
        super().__init__(f"同花顺实时报价业务异常 code={self.code}")


def _request_hithink_snapshot_payload(
    provider,
    thscodes: list[str],
    *,
    cancellation_token=None,
    deadline_monotonic: float | None = None,
) -> dict:
    api_key = _hithink_api_key()
    if not api_key:
        raise RuntimeError("HITHINK_FINANCE_API_KEY 未配置")

    timeout_sec = _hithink_timeout_seconds(provider)
    query = urlencode({"thscodes": ",".join(thscodes)})
    request = urllib.request.Request(
        f"{_HITHINK_SNAPSHOT_URL}?{query}",
        headers={
            "X-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "vcp-hunter/1.0",
        },
    )
    last_error: Exception | None = None
    deadline = (
        float(deadline_monotonic)
        if deadline_monotonic is not None
        else time.monotonic() + max(0.1, timeout_sec)
    )
    for attempt in range(_HITHINK_RETRY_ATTEMPTS):
        raise_if_cancelled(cancellation_token)
        remaining_sec = deadline - time.monotonic()
        if remaining_sec <= 0:
            break
        retry_after_sec = 0.0
        try:
            response = urlopen_https(
                request,
                timeout=bounded_io_timeout(min(timeout_sec, remaining_sec), cancellation_token),
                allowed_hosts=_HITHINK_ALLOWED_HOSTS,
                allow_reserved_tun_for_allowed_hosts=True,
            )
            try:
                payload = _read_json_response(response, cancellation_token)
            finally:
                with suppress(AttributeError, OSError, RuntimeError, TypeError):
                    response.close()
        except urllib.error.HTTPError as exc:
            reraise_task_cancellation(exc)
            status_code = int(exc.code or 0)
            retry_after_sec = _hithink_retry_after_seconds(exc)
            with suppress(AttributeError, OSError, RuntimeError, TypeError):
                exc.close()
            if status_code in _HITHINK_RETRYABLE_HTTP_STATUSES:
                last_error = exc
            else:
                raise RuntimeError(f"同花顺实时报价 HTTP {status_code}") from exc
        except (json.JSONDecodeError, OSError, TimeoutError) as exc:
            reraise_task_cancellation(exc)
            last_error = exc
        else:
            if not isinstance(payload, dict):
                raise RuntimeError("同花顺实时报价响应格式无效")
            code, detail = _hithink_business_error(payload)
            if code == 0:
                return payload
            if code not in _HITHINK_RETRYABLE_CODES:
                raise _HithinkBusinessError(code)
            last_error = RuntimeError(f"同花顺实时报价业务异常 {detail}")

        if attempt + 1 < _HITHINK_RETRY_ATTEMPTS:
            wait_seconds = min(
                max(_HITHINK_RETRY_DELAY_SEC * (attempt + 1), retry_after_sec),
                max(0.0, deadline - time.monotonic()),
            )
            wait_with_cancellation(wait_seconds, cancellation_token)

    if last_error is not None:
        raise RuntimeError(f"同花顺实时报价请求失败: {sanitize_hithink_error(last_error)}") from None
    if time.monotonic() >= deadline:
        raise TimeoutError("hithink request timeout")
    raise RuntimeError("同花顺实时报价请求失败")


def _hithink_code_by_thscode(codes) -> dict[str, str]:
    normalized_codes = [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]
    return {
        thscode: code
        for code in normalized_codes
        if (thscode := to_hithink_thscode(code))
    }


def _hithink_snapshot_code_groups(codes) -> tuple[list[str], list[str]]:
    """Keep Beijing symbols from poisoning a mixed SH/SZ snapshot request."""
    standard_codes: list[str] = []
    beijing_codes: list[str] = []
    for code in dict.fromkeys(str(item).strip() for item in (codes or []) if str(item or "").strip()):
        if to_hithink_thscode(code).endswith(".BJ"):
            beijing_codes.append(code)
        else:
            standard_codes.append(code)
    return standard_codes, beijing_codes


def _hithink_snapshot_required_number(row: dict, field: str) -> float | None:
    """Return an explicit finite snapshot value; missing/null is not a market zero."""
    if field not in row:
        return None
    raw_value = row.get(field)
    if raw_value is None or isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, str) and not raw_value.strip():
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _is_hithink_isolatable_transport_error(exc: BaseException) -> bool:
    """Only preserve a sibling result for a transport-only split failure."""
    reraise_task_cancellation(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, OSError):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    normalized = normalize_error_text(exc)
    return bool(normalized) and any(
        token in normalized for token in _HITHINK_ISOLATABLE_TRANSPORT_ERROR_TOKENS
    )


def _parse_hithink_snapshot(payload: dict, code_by_thscode: dict[str, str], inferred_trade_date: str) -> dict:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("同花顺实时报价响应缺少数据")
    rows = data.get("item")
    if not isinstance(rows, list):
        raise RuntimeError("同花顺实时报价响应缺少行情列表")

    quote_time = _iso_quote_time_from_milliseconds(data.get("timestamp"))
    quotes = {}
    incomplete_matched_row = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        thscode = str(row.get("thscode") or "").strip().upper()
        code = code_by_thscode.get(thscode)
        if not code:
            continue
        required_values = {
            field: _hithink_snapshot_required_number(row, field)
            for field in (
                "last_price",
                "open_price",
                "high_price",
                "low_price",
                "prev_price",
                "volume",
                "turnover",
            )
        }
        if any(value is None for value in required_values.values()):
            incomplete_matched_row = True
            continue
        last_close = required_values["prev_price"]
        close_price = required_values["last_price"]
        open_price = required_values["open_price"]
        high_price = required_values["high_price"]
        low_price = required_values["low_price"]
        quotes[code] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": required_values["volume"],
            "amount": required_values["turnover"],
            "last_close": last_close,
            "change": coerce_quote_number(row.get("price_change")),
            "pct": coerce_quote_number(row.get("price_change_ratio_pct")),
            "date": inferred_trade_date,
            **_network_quote_metadata("hithink", quote_time),
        }
    if not quotes:
        if incomplete_matched_row:
            return {}
        raise RuntimeError("同花顺实时报价返回空结果")
    return quotes


def _request_hithink_quote_codes(
    provider,
    codes: list[str],
    inferred_trade_date: str,
    *,
    cancellation_token=None,
    split_budget: list[int],
    deadline_monotonic: float,
) -> dict:
    code_by_thscode = _hithink_code_by_thscode(codes)
    if not code_by_thscode:
        return {}
    try:
        payload = _request_hithink_snapshot_payload(
            provider,
            list(code_by_thscode),
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
    except _HithinkBusinessError as exc:
        if exc.code not in _HITHINK_NON_COOLDOWN_ERROR_CODES:
            raise
        normalized_codes = list(code_by_thscode.values())
        if (
            exc.code in _HITHINK_SPLITTABLE_ERROR_CODES
            and len(normalized_codes) > 1
            and split_budget[0] > 0
        ):
            split_budget[0] -= 1
            midpoint = len(normalized_codes) // 2
            quotes = {}
            transport_failure = None
            for split_codes in (normalized_codes[:midpoint], normalized_codes[midpoint:]):
                try:
                    quotes.update(
                        _request_hithink_quote_codes(
                            provider,
                            split_codes,
                            inferred_trade_date,
                            cancellation_token=cancellation_token,
                            split_budget=split_budget,
                            deadline_monotonic=deadline_monotonic,
                        )
                    )
                except (OSError, RuntimeError) as split_exc:
                    if not _is_hithink_isolatable_transport_error(split_exc):
                        raise
                    transport_failure = split_exc
                    log.debug("[实时报价] 同花顺拆分子批次传输异常，保留其他子批次结果: %s", split_exc)
            if quotes or transport_failure is None:
                return quotes
            raise transport_failure from None
        return {}
    return _parse_hithink_snapshot(payload, code_by_thscode, inferred_trade_date)


def request_hithink_quote_batch(provider, codes, inferred_trade_date: str, *, cancellation_token=None):
    timeout_sec = _hithink_timeout_seconds(provider)
    deadline_monotonic = time.monotonic() + max(0.1, timeout_sec)
    split_budget = [_HITHINK_MAX_ISOLATION_SPLITS]
    quotes = {}
    for code_group in _hithink_snapshot_code_groups(codes):
        if not code_group:
            continue
        quotes.update(
            _request_hithink_quote_codes(
                provider,
                code_group,
                inferred_trade_date,
                cancellation_token=cancellation_token,
                split_budget=split_budget,
                deadline_monotonic=deadline_monotonic,
            )
        )
    if quotes:
        register_hithink_success(provider)
    return quotes


def _request_hithink_ticker_search_payload(provider, code: str, *, cancellation_token=None) -> dict:
    api_key = _hithink_api_key()
    if not api_key:
        raise RuntimeError("HITHINK_FINANCE_API_KEY 未配置")

    timeout_sec = _hithink_timeout_seconds(provider)
    query = urlencode({"q": code, "asset_type": "a-share", "limit": 1})
    request = urllib.request.Request(
        f"https://fuyao.aicubes.cn/api/meta/tickers/search?{query}",
        headers={
            "X-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "vcp-hunter/1.0",
        },
    )
    deadline = time.monotonic() + max(0.1, timeout_sec)
    last_error: Exception | None = None
    for attempt in range(_HITHINK_RETRY_ATTEMPTS):
        raise_if_cancelled(cancellation_token)
        remaining_sec = deadline - time.monotonic()
        if remaining_sec <= 0:
            break
        retry_after_sec = 0.0
        try:
            response = urlopen_https(
                request,
                timeout=bounded_io_timeout(min(timeout_sec, remaining_sec), cancellation_token),
                allowed_hosts=_HITHINK_ALLOWED_HOSTS,
                allow_reserved_tun_for_allowed_hosts=True,
            )
            try:
                payload = _read_json_response(response, cancellation_token)
            finally:
                with suppress(AttributeError, OSError, RuntimeError, TypeError):
                    response.close()
        except urllib.error.HTTPError as exc:
            reraise_task_cancellation(exc)
            status_code = int(exc.code or 0)
            retry_after_sec = _hithink_retry_after_seconds(exc)
            with suppress(AttributeError, OSError, RuntimeError, TypeError):
                exc.close()
            if status_code in _HITHINK_RETRYABLE_HTTP_STATUSES:
                last_error = exc
            else:
                raise RuntimeError(f"同花顺标的元数据 HTTP {status_code}") from exc
        except (json.JSONDecodeError, OSError, TimeoutError) as exc:
            reraise_task_cancellation(exc)
            last_error = exc
        else:
            if not isinstance(payload, dict):
                raise RuntimeError("同花顺标的元数据响应格式无效")
            try:
                business_code = int(payload.get("code"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("同花顺标的元数据响应缺少有效业务状态码") from exc
            if business_code == 0:
                return payload
            if business_code not in _HITHINK_RETRYABLE_CODES:
                raise _HithinkBusinessError(business_code)
            last_error = RuntimeError(f"同花顺标的元数据业务异常 code={business_code}")

        if attempt + 1 < _HITHINK_RETRY_ATTEMPTS:
            wait_seconds = min(
                max(_HITHINK_RETRY_DELAY_SEC * (attempt + 1), retry_after_sec),
                max(0.0, deadline - time.monotonic()),
            )
            wait_with_cancellation(wait_seconds, cancellation_token)

    if last_error is not None:
        raise RuntimeError(f"同花顺标的元数据请求失败: {sanitize_hithink_error(last_error)}") from None
    raise RuntimeError("同花顺标的元数据请求失败")


def request_hithink_ticker_names(provider, codes, *, cancellation_token=None) -> dict[str, str]:
    """Resolve exact A-share codes through the authoritative metadata endpoint."""
    if not _hithink_api_key():
        return {}

    names: dict[str, str] = {}
    for code in dict.fromkeys(str(item or "").strip() for item in (codes or []) if str(item or "").strip()):
        expected_thscode = to_hithink_thscode(code)
        if not expected_thscode:
            continue
        try:
            payload = _request_hithink_ticker_search_payload(provider, code, cancellation_token=cancellation_token)
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            reraise_task_cancellation(exc)
            log.debug(f"[名称映射] 同花顺元数据未能解析 {code}: {sanitize_hithink_error(exc)}")
            continue

        data = payload.get("data")
        rows = data.get("item") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_code = str(row.get("ticker") or "").strip()
            row_thscode = str(row.get("thscode") or "").strip().upper()
            asset_type = str(row.get("asset_type") or "").strip().lower()
            name = str(row.get("name") or "").strip()
            if row_code == code and row_thscode == expected_thscode and asset_type == "a-share" and name and name != code:
                names[code] = name
                break
    return names


def _tencent_volume_amount(fields: list[str]) -> tuple[float, float]:
    volume = coerce_quote_number(fields[6])
    amount = 0.0
    if len(fields) > 35:
        amount_parts = str(fields[35] or "").split("/")
        if len(amount_parts) >= 3:
            volume = coerce_quote_number(amount_parts[1]) or volume
            amount = coerce_quote_number(amount_parts[2])
    if amount <= 0 and len(fields) > 37:
        amount = coerce_quote_number(fields[37]) * 10000.0
    return _quote_volume_lots_to_shares(volume), amount


def request_eastmoney_quote_batch(provider, codes, inferred_trade_date: str, *, cancellation_token=None):
    normalized_codes = [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]
    if not normalized_codes:
        return {}
    fields = "f12,f13,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f124"
    secids = ",".join(to_eastmoney_secid(code) for code in normalized_codes)
    hosts = [
        host
        for raw_host in dict.fromkeys(
            getattr(provider, "_rt_eastmoney_hosts", _EASTMONEY_REALTIME_HOSTS)
        )
        if (host := str(raw_host or "").strip().lower()) in _EASTMONEY_REALTIME_ALLOWED_HOSTS
    ]
    if not hosts:
        raise RuntimeError("东方财富实时报价未配置受信主机")
    timeout_sec = float(getattr(provider, "_rt_api_call_timeout_sec", 8.0) or 8.0)
    last_error = None

    for host in hosts:
        url = (
            f"https://{host}/api/qt/ulist/get"
            f"?fltt=2&np=3&ut=bd1d9ddb04089700cf9c27f6f7426281"
            f"&invt=2&fields={fields}&secids={secids}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
                "Connection": "close",
            },
        )

        try:
            resp = urlopen_https(
                req,
                timeout=bounded_io_timeout(timeout_sec, cancellation_token),
                allowed_hosts=_EASTMONEY_REALTIME_ALLOWED_HOSTS,
                allow_reserved_tun_for_allowed_hosts=True,
            )
            try:
                payload = _read_json_response(resp, cancellation_token)
            finally:
                with suppress(AttributeError, OSError, RuntimeError, TypeError):
                    resp.close()

            if int(payload.get("rc", 0) or 0) != 0:
                raise RuntimeError(f"东方财富实时报价接口异常 rc={payload.get('rc')}")

            data = payload.get("data") or {}
            diff = data.get("diff")
            if diff is None and data.get("f12"):
                diff = [data]
            if not diff:
                raise RuntimeError("东方财富实时报价返回空结果")

            quotes = {}
            for row in diff:
                code_val = str(row.get("f12") or "").strip()
                if not code_val:
                    continue
                name_val = str(row.get("f14") or "").strip()
                last_close = coerce_quote_number(row.get("f18"))
                close_price = coerce_quote_number(row.get("f2")) or last_close
                open_price = coerce_quote_number(row.get("f17")) or close_price
                high_price = coerce_quote_number(row.get("f15")) or max(open_price, close_price)
                low_price = coerce_quote_number(row.get("f16")) or min(open_price, close_price)
                change_amount = coerce_quote_number(row.get("f4"))
                pct_change = coerce_quote_number(row.get("f3"))
                quotes[code_val] = {
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": _quote_volume_lots_to_shares(row.get("f5")),
                    "amount": coerce_quote_number(row.get("f6")),
                    "last_close": last_close,
                    "change": change_amount,
                    "pct": pct_change,
                    "date": inferred_trade_date,
                    "name": name_val,
                    **_network_quote_metadata("eastmoney", _iso_quote_time_from_epoch(row.get("f124"))),
                }

            if not quotes:
                raise RuntimeError("东方财富实时报价返回空结果")
            register_eastmoney_success(provider)
            return quotes
        except (AttributeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            reraise_task_cancellation(exc)
            last_error = exc
            fast_fail_edge_error = bool(getattr(provider, "_rt_eastmoney_fast_fail_on_edge_error", False)) and (
                _is_eastmoney_edge_failure(exc)
            )
            log.debug(f"[实时报价] 东方财富主机 {host} 失败: {exc}")
            if fast_fail_edge_error:
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("东方财富实时报价返回空结果")


def request_sina_quote_batch(provider, codes, inferred_trade_date: str, *, cancellation_token=None):
    normalized_codes = [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]
    if not normalized_codes:
        return {}

    symbols = ",".join(to_sina_symbol(code) for code in normalized_codes)
    timeout_sec = float(getattr(provider, "_rt_api_call_timeout_sec", 8.0) or 8.0)
    req = urllib.request.Request(
        f"https://hq.sinajs.cn/list={symbols}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
            "Connection": "close",
        },
    )
    resp = urlopen_https(
        req,
        timeout=bounded_io_timeout(timeout_sec, cancellation_token),
        allowed_hosts=_SINA_REALTIME_ALLOWED_HOSTS,
        allow_reserved_tun_for_allowed_hosts=True,
    )
    try:
        text = _read_text_response(resp, "gbk", cancellation_token)
    finally:
        with suppress(AttributeError, OSError, RuntimeError, TypeError):
            resp.close()

    quotes = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "hq_str_" not in line or '="' not in line:
            continue

        left, right = line.split('="', 1)
        symbol = left.split("hq_str_", 1)[-1].strip()
        payload = right.rsplit('";', 1)[0]
        fields = payload.split(",")
        if len(fields) < 32:
            continue

        code_val = symbol[-6:]
        name_val = str(fields[0] or "").strip()
        open_price = coerce_quote_number(fields[1])
        last_close = coerce_quote_number(fields[2])
        close_price = coerce_quote_number(fields[3]) or last_close
        high_price = coerce_quote_number(fields[4]) or max(open_price, close_price)
        low_price = coerce_quote_number(fields[5]) or min(open_price, close_price)
        change_amount = close_price - last_close if (close_price > 0 and last_close > 0) else 0.0
        pct_change = (change_amount / last_close * 100.0) if last_close > 0 else 0.0
        quote_date = fields[30].strip() or inferred_trade_date

        quotes[code_val] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": coerce_quote_number(fields[8]),
            "amount": coerce_quote_number(fields[9]),
            "last_close": last_close,
            "change": change_amount,
            "pct": pct_change,
            "date": quote_date,
            "name": name_val,
            **_network_quote_metadata("sina", _iso_quote_time_from_parts(quote_date, fields[31])),
        }

    if not quotes:
        raise RuntimeError("新浪实时报价返回空结果")
    return quotes


def request_tencent_quote_batch(provider, codes, inferred_trade_date: str, *, cancellation_token=None):
    normalized_codes = [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]
    if not normalized_codes:
        return {}

    symbols = ",".join(to_tencent_symbol(code) for code in normalized_codes)
    timeout_sec = float(getattr(provider, "_rt_api_call_timeout_sec", 8.0) or 8.0)
    req = urllib.request.Request(
        f"https://qt.gtimg.cn/q={symbols}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
            "Connection": "close",
        },
    )
    resp = urlopen_https(
        req,
        timeout=bounded_io_timeout(timeout_sec, cancellation_token),
        allowed_hosts=_TENCENT_REALTIME_ALLOWED_HOSTS,
        allow_reserved_tun_for_allowed_hosts=True,
    )
    try:
        text = _read_text_response(resp, "gbk", cancellation_token)
    finally:
        with suppress(AttributeError, OSError, RuntimeError, TypeError):
            resp.close()

    quotes = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("v_") or '="' not in line:
            continue

        left, right = line.split('="', 1)
        symbol = left.split("v_", 1)[-1].strip()
        payload = right.rsplit('";', 1)[0]
        fields = payload.split("~")
        if len(fields) < 35:
            continue

        code_val = str(fields[2] or symbol[-6:]).strip()
        if not code_val:
            continue

        name_val = str(fields[1] or "").strip()
        close_price = coerce_quote_number(fields[3])
        last_close = coerce_quote_number(fields[4])
        open_price = coerce_quote_number(fields[5]) or close_price
        high_price = coerce_quote_number(fields[33]) or max(open_price, close_price)
        low_price = coerce_quote_number(fields[34]) or min(open_price, close_price)
        change_amount = coerce_quote_number(fields[31])
        pct_change = coerce_quote_number(fields[32])

        raw_datetime = str(fields[30] or "").strip()
        quote_date = inferred_trade_date
        if len(raw_datetime) >= 8 and raw_datetime[:8].isdigit():
            quote_date = f"{raw_datetime[:4]}-{raw_datetime[4:6]}-{raw_datetime[6:8]}"
        volume, amount = _tencent_volume_amount(fields)

        quotes[code_val] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price or last_close,
            "volume": volume,
            "amount": amount,
            "last_close": last_close,
            "change": change_amount,
            "pct": pct_change,
            "date": quote_date,
            "name": name_val,
            **_network_quote_metadata(
                "tencent", _iso_quote_time_from_parts(raw_datetime[:8], raw_datetime[8:14])
            ),
        }

    if not quotes:
        raise RuntimeError("tencent realtime quote returned empty result")
    return quotes
