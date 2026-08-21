# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
import urllib.request
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from core.logger import get_logger
from domains.quotes.snapshot import coerce_number
from infra.http_safety import urlopen_https
from infra.tasks.lifecycle import (
    bounded_io_timeout,
    raise_if_cancelled,
    reraise_task_cancellation,
)
from vcp.realtime_quote_batch import normalize_error_text

log = get_logger(__name__)
_CN_TZ = timezone(timedelta(hours=8))


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


def to_eastmoney_secid(code: str) -> str:
    code = str(code).strip()
    if code.startswith("92"):
        market = 0
    else:
        market = 1 if code.startswith(("6", "9")) else 0
    return f"{market}.{code}"


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
    return volume, amount


def request_eastmoney_quote_batch(provider, codes, inferred_trade_date: str, *, cancellation_token=None):
    normalized_codes = [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]
    if not normalized_codes:
        return {}
    fields = "f12,f13,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f124"
    secids = ",".join(to_eastmoney_secid(code) for code in normalized_codes)
    hosts = list(
        dict.fromkeys(
            getattr(
                provider,
                "_rt_eastmoney_hosts",
                ["push2.eastmoney.com", "88.push2.eastmoney.com"],
            )
        )
    )
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
            resp = urlopen_https(req, timeout=bounded_io_timeout(timeout_sec, cancellation_token))
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
                    "volume": coerce_quote_number(row.get("f5")),
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
    resp = urlopen_https(req, timeout=bounded_io_timeout(timeout_sec, cancellation_token))
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
    resp = urlopen_https(req, timeout=bounded_io_timeout(timeout_sec, cancellation_token))
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
