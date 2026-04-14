# -*- coding: utf-8 -*-
"""External data/cache helpers for VCPEngine."""

from __future__ import annotations

import json
import os
import time as _time
import urllib.request
from datetime import datetime

from core.json_cache import load_json_file, remove_cache_file, save_json_file
from core.logger import get_logger
from vcp.constants import (
    FINANCE_CACHE_FILE,
    INSTITUTION_KEYWORDS,
    INSTITUTION_NAME_KEYWORDS,
    SHAREHOLDER_CACHE_FILE,
)

_log = get_logger(__name__)


def tdx_connect(tdx_servers):
    """连接通达信服务器，返回 api 对象，失败返回 None"""

    from pytdx.hq import TdxHq_API

    api = TdxHq_API(auto_retry=False, heartbeat=False)
    for host, port in tdx_servers:
        try:
            if api.connect(host, port, time_out=5):
                return api
        except Exception as exc:
            _log.debug(f"[pytdx] 连接服务器 {host}:{port} 失败: {exc}")
            continue
    return None


def batch_get_finance_info(codes, tdx_servers):
    """通过通达信批量获取财务信息（总股本、法人股等），带30天磁盘缓存"""

    cache = {}
    if os.path.exists(FINANCE_CACHE_FILE):
        try:
            cache = load_json_file(FINANCE_CACHE_FILE) or {}
        except Exception as exc:
            _log.debug(f"[pytdx] 财务缓存文件读取异常，将重建: {exc}")
            cache = {}

    results = {}
    need_query = []
    now = datetime.now()

    for code in codes:
        if code in cache:
            cached = cache[code]
            try:
                cache_date = datetime.strptime(cached.get('date', '2000-01-01'), '%Y-%m-%d')
                if (now - cache_date).days < 30:
                    results[code] = cached['info']
                    continue
            except (ValueError, KeyError) as exc:
                _log.debug(f"[pytdx] 缓存日期解析异常({code}): {exc}")
        need_query.append(code)

    if not need_query:
        return results

    api = tdx_connect(tdx_servers)
    if api is None:
        _log.warning("[pytdx] 无法连接通达信服务器，市值计算将使用本地旧缓存或暂无数据")
        for code in need_query:
            if code in cache:
                results[code] = cache[code]['info']
        return results

    try:
        for i, raw_code in enumerate(need_query):
            code = raw_code.replace("sh", "").replace("sz", "")
            market = 1 if code.startswith(('6', '5')) else 0
            try:
                info = api.get_finance_info(market, code)
                if info:
                    results[raw_code] = info
                    cache[raw_code] = {'info': info, 'date': now.strftime('%Y-%m-%d')}
            except Exception as exc:
                _log.debug(f"[pytdx] 获取 {raw_code} 财务信息失败: {exc}")

            if (i + 1) % 50 == 0:
                _time.sleep(0.3)

        try:
            save_json_file(FINANCE_CACHE_FILE, cache)
            remove_cache_file(FINANCE_CACHE_FILE.replace(".json", ".pkl"))
        except Exception as exc:
            _log.error(f"[pytdx] 财务缓存写入失败: {exc}")
    finally:
        try:
            api.disconnect()
        except Exception as exc:
            _log.debug(f"[pytdx] 断开服务器连接时异常（可忽略）: {exc}")

    return results


def batch_check_market_cap(codes: list[str], tdx_servers, close_prices: dict[str, float] | None = None) -> dict[str, float]:
    """批量计算总市值 = 总股本 × 收盘价"""

    finance_data = batch_get_finance_info(codes, tdx_servers)
    results = {}
    for code in codes:
        info = finance_data.get(code)
        if not info:
            continue
        zongguben = info.get('zongguben', 0)
        if zongguben and zongguben > 0:
            if close_prices and code in close_prices:
                results[code] = zongguben * close_prices[code]
            else:
                results[code] = zongguben
    return results


def is_institution(name, holder_type):
    for kw in INSTITUTION_KEYWORDS:
        if kw in (holder_type or ''):
            return True

    for kw in INSTITUTION_NAME_KEYWORDS:
        if kw in (name or ''):
            return True

    return False


def check_institutional_shareholders(code):
    """通过东方财富API查询单只股票的十大流通股东，判断是否有机构持仓"""

    if code.startswith(('6', '5')):
        prefix = 'SH'
    elif code.startswith(('0', '3')):
        prefix = 'SZ'
    elif code.startswith(('4', '8')):
        prefix = 'BJ'
    else:
        prefix = 'SZ'

    url = f'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={prefix}{code}'

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://emweb.securities.eastmoney.com/',
            },
        )
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read().decode('utf-8'))
        shareholders = data.get('sdltgd', [])
        if not shareholders:
            return False, "无股东数据"

        institutions = []
        for shareholder in shareholders:
            name = shareholder.get('HOLDER_NAME', '')
            holder_type = shareholder.get('HOLDER_TYPE', '')

            if is_institution(name, holder_type):
                short = name[:12] + '..' if len(name) > 12 else name
                institutions.append(short)

        if institutions:
            display = '/'.join(institutions[:3])
            if len(institutions) > 3:
                display += f' 等{len(institutions)}家'
            return True, display
        return False, "无机构"
    except Exception as exc:
        return False, f"查询失败:{str(exc)[:20]}"


def batch_check_institution(codes):
    """批量查询多只股票的机构股东情况（带 90 天磁盘缓存）"""

    cache = {}
    if os.path.exists(SHAREHOLDER_CACHE_FILE):
        try:
            cache = load_json_file(SHAREHOLDER_CACHE_FILE) or {}
        except Exception as exc:
            _log.debug(f"[机构股东] 缓存文件读取异常，将重建: {exc}")
            cache = {}

    results = {}
    need_query = []
    now = datetime.now()

    for code in codes:
        if code in cache:
            cached = cache[code]
            try:
                cache_date = datetime.strptime(cached['date'], '%Y-%m-%d')
                if (now - cache_date).days < 90:
                    results[code] = cached
                    continue
            except (ValueError, KeyError) as exc:
                _log.debug(f"[机构股东] 缓存日期解析异常({code}): {exc}")
        need_query.append(code)

    if need_query:
        _log.info(f"[机构股东] 东方财富查询 {len(need_query)} 只（缓存命中 {len(codes) - len(need_query)} 只）...")
        for i, code in enumerate(need_query):
            has_inst, detail = check_institutional_shareholders(code)
            entry = {
                'has_institution': has_inst,
                'detail': detail,
                'date': now.strftime('%Y-%m-%d'),
            }
            results[code] = entry
            cache[code] = entry
            if i < len(need_query) - 1:
                _time.sleep(0.3)

        try:
            save_json_file(SHAREHOLDER_CACHE_FILE, cache)
            remove_cache_file(SHAREHOLDER_CACHE_FILE.replace(".json", ".pkl"))
        except Exception as exc:
            _log.debug(f"[机构股东] 缓存保存失败: {exc}")

    return results
