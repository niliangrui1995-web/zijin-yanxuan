# -*- coding: utf-8 -*-
"""板块 RPS 缓存与热点板块补全的共享工具。

统一解决两个问题：
1. 扫描与盘中监控以前各自维护一套板块 RPS 缓存读写逻辑，容易出现口径漂移。
2. 缓存日期过期时旧逻辑仍可能继续使用，导致“热点板块”整列空白或不刷新。
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Iterable

from core.json_cache import load_json_file, save_json_file
from core.logger import get_logger
from core.market_calendar import MarketCalendar
from vcp.constants import SECTOR_RPS_CACHE_FILE
from vcp.sector import SectorManager

log = get_logger(__name__)


def normalize_trade_date(target_date=None) -> str:
    """规范化为 yyyyMMdd 交易日字符串。"""
    if isinstance(target_date, _dt.datetime):
        return target_date.strftime("%Y%m%d")
    if isinstance(target_date, _dt.date):
        return target_date.strftime("%Y%m%d")

    text = str(target_date or "").strip()
    if len(text) == 8 and text.isdigit():
        return text
    if len(text) == 10 and text.count("-") == 2:
        return text.replace("-", "")
    if len(text) == 10 and text.count("/") == 2:
        return text.replace("/", "")

    trade_dt = MarketCalendar.get_latest_trade_date()
    return trade_dt.strftime("%Y%m%d")


def _normalize_sector_rps(sector_rps: dict) -> dict:
    """JSON 反序列化后把周期 key 从字符串恢复为 int。"""
    normalized = {}
    for sector_name, period_map in (sector_rps or {}).items():
        if not isinstance(period_map, dict):
            continue
        normalized[sector_name] = {}
        for period, value in period_map.items():
            try:
                key = int(period)
            except (TypeError, ValueError):
                key = period
            normalized[sector_name][key] = value
    return normalized


def _get_sector_manager(data_provider) -> SectorManager:
    tdx_vipdoc = getattr(data_provider, "tdx_vipdoc", "")
    tdx_root = os.path.dirname(tdx_vipdoc) if tdx_vipdoc else r"D:\HT"
    return SectorManager.get_instance(tdx_root)


def load_sector_rps_snapshot(data_provider, all_data, target_date=None, logger=None):
    """返回 (sector_manager, sector_rps, normalized_date, source)。

    source 取值：
    - "cache": 命中同交易日缓存
    - "rebuild": 重新计算
    - "error": 构建失败
    """
    logger = logger or log
    normalized_date = normalize_trade_date(target_date)

    try:
        sector_manager = _get_sector_manager(data_provider)
    except Exception as exc:
        logger.error(f"[板块RPS] 初始化 SectorManager 失败: {exc}")
        return False, {}, normalized_date, "error"

    sector_rps = {}
    cache_hit = False
    cached_date = ""

    if os.path.exists(SECTOR_RPS_CACHE_FILE):
        try:
            payload = load_json_file(SECTOR_RPS_CACHE_FILE)
            sector_rps = _normalize_sector_rps(payload.get("sector_rps", {}) or {})
            cached_date = normalize_trade_date(payload.get("date"))
            cache_hit = bool(sector_rps) and cached_date == normalized_date
            if cache_hit:
                logger.info(f"[板块RPS] 命中缓存 ({cached_date}, {len(sector_rps)} 个板块)")
            else:
                logger.info(f"[板块RPS] 缓存过期或为空，重建 ({cached_date or '-'} -> {normalized_date})")
        except Exception as exc:
            logger.warning(f"[板块RPS] 缓存读取失败，改为现算: {exc}")
            sector_rps = {}

    if cache_hit:
        return sector_manager, sector_rps, normalized_date, "cache"

    try:
        sector_rps = sector_manager.build_sector_rps(all_data or {}, normalized_date) or {}
        logger.info(f"[板块RPS] 现算完成 ({normalized_date}, {len(sector_rps)} 个板块)")
        if sector_rps:
            save_json_file(
                SECTOR_RPS_CACHE_FILE,
                {"date": normalized_date, "sector_rps": sector_rps},
            )
        return sector_manager, sector_rps, normalized_date, "rebuild"
    except Exception as exc:
        logger.error(f"[板块RPS] 现算失败: {exc}")
        return False, {}, normalized_date, "error"


def resolve_hot_sector(code: str, preferred_text: str, sector_manager, sector_rps, logger=None) -> str:
    """优先使用已有板块文本，缺失时再回查板块 RPS。"""
    logger = logger or log
    preferred = str(preferred_text or "").strip()
    if preferred and preferred not in {"-", "--"}:
        return preferred

    if not code or not sector_manager or not sector_rps:
        return "--"

    try:
        _, info_str, _ = sector_manager.check_sector_rps(code, sector_rps, threshold=0)
        return info_str if info_str else "--"
    except Exception as exc:
        logger.debug(f"[板块RPS] {code} 热点板块查询失败: {exc}")
        return "--"


def enrich_hot_sector_rows(
    rows: Iterable[dict],
    sector_manager,
    sector_rps,
    code_key: str = "代码",
    sector_key: str = "热点板块",
    logger=None,
):
    """原地补全热点板块列。"""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row[sector_key] = resolve_hot_sector(
            str(row.get(code_key, "")).strip(),
            row.get(sector_key, ""),
            sector_manager,
            sector_rps,
            logger=logger,
        )
