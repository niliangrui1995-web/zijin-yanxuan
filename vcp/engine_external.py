# -*- coding: utf-8 -*-
"""External data/cache helpers for VCPEngine."""

from __future__ import annotations

import json
import os
import time as _time
import urllib.request
from contextlib import suppress
from datetime import datetime

from core.exceptions import CacheIOError, DataFormatError
from core.json_cache import load_json_file, remove_cache_file, save_json_file
from core.logger import get_logger
from infra.http_safety import urlopen_https
from vcp.constants import (
    FINANCE_CACHE_FILE,
    INSTITUTION_KEYWORDS,
    INSTITUTION_NAME_KEYWORDS,
    SHAREHOLDER_CACHE_FILE,
)

_log = get_logger(__name__)


def _coerce_number(value) -> float:
    if value in (None, "", "-", "--"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_eastmoney_secid(code: str) -> str:
    code = str(code).strip()
    market = 1 if code.startswith(("5", "6", "9")) else 0
    return f"{market}.{code}"


def _fetch_eastmoney_finance_info(codes):
    """批量获取总股本/总市值等财务基础数据。"""

    normalized_codes = [
        str(code).strip()
        for code in dict.fromkeys(codes or [])
        if str(code or "").strip()
    ]
    if not normalized_codes:
        return {}

    results = {}
    batch_size = 80
    fields = "f12,f2,f18,f20,f21"

    for start in range(0, len(normalized_codes), batch_size):
        batch = normalized_codes[start:start + batch_size]
        secids = ",".join(_to_eastmoney_secid(code) for code in batch)
        url = (
            "https://push2.eastmoney.com/api/qt/ulist/get"
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

        resp = urlopen_https(req, timeout=8)
        try:
            payload = json.loads(resp.read().decode("utf-8"))
        finally:
            with suppress(AttributeError, OSError, RuntimeError, TypeError):
                resp.close()

        if int(payload.get("rc", 0) or 0) != 0:
            raise RuntimeError(f"eastmoney finance rc={payload.get('rc')}")

        diff = (payload.get("data") or {}).get("diff") or []
        for row in diff:
            code_val = str(row.get("f12") or "").strip()
            if not code_val:
                continue

            total_market_cap = _coerce_number(row.get("f20"))
            float_market_cap = _coerce_number(row.get("f21"))
            latest_price = _coerce_number(row.get("f2"))
            last_close = _coerce_number(row.get("f18"))
            price_base = latest_price or last_close

            if total_market_cap <= 0 or price_base <= 0:
                continue

            results[code_val] = {
                "zongguben": total_market_cap / price_base,
                "market_cap": total_market_cap,
                "float_market_cap": float_market_cap,
                "price_base": price_base,
                "source": "eastmoney",
            }

    return results


def batch_get_finance_info(codes):
    """批量获取财务信息，带 30 天磁盘缓存。"""

    cache = {}
    if os.path.exists(FINANCE_CACHE_FILE):
        try:
            cache = load_json_file(FINANCE_CACHE_FILE) or {}
        except (CacheIOError, DataFormatError) as exc:
            _log.debug(f"[财务股本] 缓存读取异常，将重建: {exc}")
            cache = {}

    results = {}
    need_query = []
    now = datetime.now()

    for code in codes:
        if code in cache:
            cached = cache[code]
            try:
                cache_date = datetime.strptime(cached.get("date", "2000-01-01"), "%Y-%m-%d")
                if (now - cache_date).days < 30:
                    results[code] = cached["info"]
                    continue
            except (KeyError, ValueError) as exc:
                _log.debug(f"[财务股本] 缓存日期解析异常({code}): {exc}")
        need_query.append(code)

    if not need_query:
        return results

    try:
        online_results = _fetch_eastmoney_finance_info(need_query)
    except (json.JSONDecodeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _log.warning(f"[eastmoney] 无法获取总股本，回退本地旧缓存: {exc}")
        for code in need_query:
            if code in cache:
                results[code] = cache[code]["info"]
        return results

    if not online_results:
        for code in need_query:
            if code in cache:
                results[code] = cache[code]["info"]
        return results

    for index, raw_code in enumerate(need_query):
        info = online_results.get(raw_code)
        if info:
            results[raw_code] = info
            cache[raw_code] = {
                "info": info,
                "date": now.strftime("%Y-%m-%d"),
            }
        if (index + 1) % 80 == 0:
            _time.sleep(0.1)

    try:
        save_json_file(FINANCE_CACHE_FILE, cache)
        remove_cache_file(FINANCE_CACHE_FILE.replace(".json", ".pkl"))
    except CacheIOError as exc:
        _log.error(f"[eastmoney] 财务缓存写入失败: {exc}")

    return results


def batch_check_market_cap(codes: list[str], close_prices: dict[str, float] | None = None) -> dict[str, float]:
    """批量计算总市值。"""

    finance_data = batch_get_finance_info(codes)
    results = {}
    for code in codes:
        info = finance_data.get(code)
        if not info:
            continue

        market_cap = float(info.get("market_cap", 0) or 0)
        price_base = float(info.get("price_base", 0) or 0)
        if market_cap > 0:
            if close_prices and code in close_prices:
                close_price = float(close_prices.get(code, 0) or 0)
                if close_price > 0:
                    results[code] = market_cap * (close_price / price_base) if price_base > 0 else market_cap
                else:
                    results[code] = market_cap
            else:
                results[code] = market_cap
            continue

        zongguben = info.get("zongguben", 0)
        if zongguben and zongguben > 0:
            if close_prices and code in close_prices:
                results[code] = zongguben * close_prices[code]
            else:
                results[code] = zongguben
    return results


def is_institution(name, holder_type):
    for kw in INSTITUTION_KEYWORDS:
        if kw in (holder_type or ""):
            return True

    for kw in INSTITUTION_NAME_KEYWORDS:
        if kw in (name or ""):
            return True

    return False


def check_institutional_shareholders(code):
    """查询单只股票的十大流通股东是否包含机构。"""

    if code.startswith(("6", "5")):
        prefix = "SH"
    elif code.startswith(("0", "3")):
        prefix = "SZ"
    elif code.startswith(("4", "8")):
        prefix = "BJ"
    else:
        prefix = "SZ"

    url = (
        "https://emweb.securities.eastmoney.com/PC_HSF10/"
        f"ShareholderResearch/PageAjax?code={prefix}{code}"
    )

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://emweb.securities.eastmoney.com/",
            },
        )
        resp = urlopen_https(req, timeout=8)
        try:
            data = json.loads(resp.read().decode("utf-8"))
        finally:
            with suppress(AttributeError, OSError, RuntimeError, TypeError):
                resp.close()

        shareholders = data.get("sdltgd", [])
        if not shareholders:
            return False, "无股东数据"

        institutions = []
        for shareholder in shareholders:
            name = shareholder.get("HOLDER_NAME", "")
            holder_type = shareholder.get("HOLDER_TYPE", "")
            if is_institution(name, holder_type):
                short = name[:12] + ".." if len(name) > 12 else name
                institutions.append(short)

        if institutions:
            display = "/".join(institutions[:3])
            if len(institutions) > 3:
                display += f" 等{len(institutions)}家"
            return True, display
        return False, "无机构"
    except (json.JSONDecodeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"查询失败:{str(exc)[:20]}"


def batch_check_institution(codes):
    """批量查询多只股票的机构股东情况，带 90 天磁盘缓存。"""

    cache = {}
    if os.path.exists(SHAREHOLDER_CACHE_FILE):
        try:
            cache = load_json_file(SHAREHOLDER_CACHE_FILE) or {}
        except (CacheIOError, DataFormatError) as exc:
            _log.debug(f"[机构股东] 缓存读取异常，将重建: {exc}")
            cache = {}

    results = {}
    need_query = []
    now = datetime.now()

    for code in codes:
        if code in cache:
            cached = cache[code]
            try:
                cache_date = datetime.strptime(cached["date"], "%Y-%m-%d")
                if (now - cache_date).days < 90:
                    results[code] = cached
                    continue
            except (KeyError, ValueError) as exc:
                _log.debug(f"[机构股东] 缓存日期解析异常({code}): {exc}")
        need_query.append(code)

    if need_query:
        _log.info(
            f"[机构股东] 东方财富查询 {len(need_query)} 只"
            f"（缓存命中 {len(codes) - len(need_query)} 只）..."
        )
        for index, code in enumerate(need_query):
            has_inst, detail = check_institutional_shareholders(code)
            entry = {
                "has_institution": has_inst,
                "detail": detail,
                "date": now.strftime("%Y-%m-%d"),
            }
            results[code] = entry
            cache[code] = entry
            if index < len(need_query) - 1:
                _time.sleep(0.3)

        try:
            save_json_file(SHAREHOLDER_CACHE_FILE, cache)
            remove_cache_file(SHAREHOLDER_CACHE_FILE.replace(".json", ".pkl"))
        except CacheIOError as exc:
            _log.debug(f"[机构股东] 缓存保存失败: {exc}")

    return results
