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
from domains.quotes.snapshot import TOTAL_SHARES_KEY, get_total_shares
from domains.quotes.snapshot import coerce_number as _coerce_number
from infra.http_safety import urlopen_https
from vcp.constants import (
    FINANCE_CACHE_FILE,
    INSTITUTION_KEYWORDS,
    INSTITUTION_NAME_KEYWORDS,
    SHAREHOLDER_CACHE_FILE,
)
from vcp.data_provider_local import load_local_tdx_capital_snapshot
from vcp.utils import _load_tdx_local_config

_log = get_logger(__name__)
_LOCAL_TDX_FINANCE_READ_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_EASTMONEY_FINANCE_FETCH_ERRORS = (json.JSONDecodeError, KeyError, OSError, RuntimeError, TypeError, ValueError)
_EASTMONEY_FINANCE_ALLOWED_HOSTS = frozenset({"push2.eastmoney.com", "push2delay.eastmoney.com"})
_EASTMONEY_SHAREHOLDER_ALLOWED_HOSTS = frozenset({"emweb.securities.eastmoney.com"})


def _to_eastmoney_secid(code: str) -> str:
    code = str(code).strip()
    # 920xxx 为北交所；900xxx 仍为沪市 B 股。
    if code.startswith("92"):
        market = 0
    else:
        market = 1 if code.startswith(("5", "6", "9")) else 0
    return f"{market}.{code}"


def _normalize_stock_codes(codes) -> list[str]:
    normalized_codes = [
        str(code or "").strip().zfill(6) for code in dict.fromkeys(codes or []) if str(code or "").strip()
    ]
    return [code for code in normalized_codes if len(code) == 6 and code.isdigit()]


def _has_valid_share_capital(entry) -> bool:
    return get_total_shares(entry) > 0


def _canonicalize_finance_info(entry: dict | None) -> dict:
    normalized = dict(entry or {})
    total_shares = get_total_shares(normalized)
    if total_shares > 0:
        normalized[TOTAL_SHARES_KEY] = total_shares
    normalized.pop("zongguben", None)
    normalized.pop("_zongguben", None)
    return normalized


def _load_local_tdx_finance_info(codes) -> dict[str, dict]:
    tdx_vipdoc = _load_tdx_local_config()
    if not tdx_vipdoc:
        return {}
    try:
        return load_local_tdx_capital_snapshot(codes, tdx_vipdoc)
    except _LOCAL_TDX_FINANCE_READ_ERRORS as exc:
        _log.warning("[财务股本] 读取通达信本地总股本失败: %s", exc, exc_info=True)
        return {}


def _fetch_eastmoney_finance_info(codes):
    """批量获取总股本/总市值等财务基础数据。"""

    normalized_codes = _normalize_stock_codes(codes)
    if not normalized_codes:
        return {}

    results = {}
    batch_size = 80
    fields = "f12,f2,f18,f20,f21"

    for start in range(0, len(normalized_codes), batch_size):
        batch = normalized_codes[start : start + batch_size]
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

        resp = urlopen_https(
            req,
            timeout=8,
            allowed_hosts=_EASTMONEY_FINANCE_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
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
                TOTAL_SHARES_KEY: total_market_cap / price_base,
                "market_cap": total_market_cap,
                "float_market_cap": float_market_cap,
                "price_base": price_base,
                "source": "eastmoney",
            }

    return results


def _fresh_cached_finance_info(cache_entry: dict, now: datetime, code: str) -> dict | None:
    try:
        cache_date = datetime.strptime(cache_entry.get("date", "2000-01-01"), "%Y-%m-%d")
    except (KeyError, ValueError) as exc:
        _log.debug(f"[财务股本] 缓存日期解析异常({code}): {exc}")
        return None
    cached_info = cache_entry.get("info")
    if (now - cache_date).days < 30 and _has_valid_share_capital(cached_info):
        return _canonicalize_finance_info(cached_info)
    return None


def batch_get_finance_info(codes):
    """批量获取财务信息，带 30 天磁盘缓存。"""

    normalized_codes = _normalize_stock_codes(codes)
    if not normalized_codes:
        return {}

    cache = {}
    if os.path.exists(FINANCE_CACHE_FILE):
        try:
            cache = load_json_file(FINANCE_CACHE_FILE) or {}
        except (CacheIOError, DataFormatError) as exc:
            _log.debug(f"[财务股本] 缓存读取异常，将重建: {exc}")
            cache = {}

    local_results = _load_local_tdx_finance_info(normalized_codes)
    results = {
        code: _canonicalize_finance_info(info)
        for code, info in (local_results or {}).items()
        if _has_valid_share_capital(info)
    }

    need_query = []
    now = datetime.now()

    for code in normalized_codes:
        if code in results:
            continue
        if code in cache:
            cached_info = _fresh_cached_finance_info(cache[code], now, code)
            if cached_info is not None:
                results[code] = cached_info
                continue
        need_query.append(code)

    if not need_query:
        return results

    try:
        online_results = _fetch_eastmoney_finance_info(need_query)
    except _EASTMONEY_FINANCE_FETCH_ERRORS as exc:
        _log.warning("[eastmoney] 无法获取总股本，回退本地旧缓存: %s", exc, exc_info=True)
        for code in need_query:
            if code in cache and _has_valid_share_capital(cache[code].get("info")):
                results[code] = _canonicalize_finance_info(cache[code]["info"])
        return results

    if not online_results:
        for code in need_query:
            if code in cache and _has_valid_share_capital(cache[code].get("info")):
                results[code] = _canonicalize_finance_info(cache[code]["info"])
        return results

    for index, raw_code in enumerate(need_query):
        info = online_results.get(raw_code)
        if info:
            info = _canonicalize_finance_info(info)
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

        total_shares = get_total_shares(info)
        if total_shares > 0:
            if close_prices and code in close_prices:
                results[code] = total_shares * close_prices[code]
            else:
                results[code] = total_shares
    return results


def is_institution(name, holder_type):
    return any(kw in (holder_type or "") for kw in INSTITUTION_KEYWORDS) or any(
        kw in (name or "") for kw in INSTITUTION_NAME_KEYWORDS
    )


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

    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={prefix}{code}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://emweb.securities.eastmoney.com/",
            },
        )
        resp = urlopen_https(
            req,
            timeout=8,
            allowed_hosts=_EASTMONEY_SHAREHOLDER_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
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
        _log.info(f"[机构股东] 东方财富查询 {len(need_query)} 只（缓存命中 {len(codes) - len(need_query)} 只）...")
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
