# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
import urllib.request
from contextlib import suppress

from core.logger import get_logger

log = get_logger(__name__)


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
    market = 1 if code.startswith(("6", "9")) else 0
    return f"{market}.{code}"


def to_sina_symbol(code: str) -> str:
    code = str(code).strip()
    if code.startswith(("5", "6", "9")):
        prefix = "sh"
    elif code.startswith(("4", "8")):
        prefix = "bj"
    else:
        prefix = "sz"
    return f"{prefix}{code}"


def coerce_quote_number(value) -> float:
    if value in (None, "", "-", "--"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def request_eastmoney_quote_batch(provider, codes, inferred_trade_date: str):
    normalized_codes = [
        str(code).strip()
        for code in dict.fromkeys(codes or [])
        if str(code or "").strip()
    ]
    if not normalized_codes:
        return {}

    fields = "f12,f13,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18"
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
            resp = urllib.request.urlopen(req, timeout=timeout_sec)
            try:
                payload = json.loads(resp.read().decode("utf-8"))
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
                    "source": "eastmoney",
                    "name": name_val,
                }

            if not quotes:
                raise RuntimeError("东方财富实时报价返回空结果")
            register_eastmoney_success(provider)
            return quotes
        except (AttributeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            last_error = exc
            log.debug(f"[实时报价] 东方财富主机 {host} 失败: {exc}")

    if last_error is not None:
        raise last_error
    raise RuntimeError("东方财富实时报价返回空结果")


def request_sina_quote_batch(provider, codes, inferred_trade_date: str):
    normalized_codes = [
        str(code).strip()
        for code in dict.fromkeys(codes or [])
        if str(code or "").strip()
    ]
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
    resp = urllib.request.urlopen(req, timeout=timeout_sec)
    try:
        text = resp.read().decode("gbk", errors="ignore")
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
            "source": "sina",
            "name": name_val,
        }

    if not quotes:
        raise RuntimeError("新浪实时报价返回空结果")
    return quotes
