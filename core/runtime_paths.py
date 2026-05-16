# -*- coding: utf-8 -*-
"""无导入副作用的运行时路径和常量入口。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])


def get_data_dir(sub_folder: str = "Cache") -> str:
    """返回 data 子目录路径；不创建目录。"""
    base = os.path.join(PROJECT_ROOT, "data")
    if not sub_folder:
        return base
    return os.path.join(base, sub_folder)


def ensure_data_dir(sub_folder: str = "Cache") -> str:
    """显式创建并返回 data 子目录路径。"""
    target_dir = get_data_dir(sub_folder)
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def ensure_cache_dir() -> str:
    """显式创建并返回缓存目录路径。"""
    return ensure_data_dir("Cache")


DATE_FMT = "%Y%m%d"

MARKET_OPEN_AM, MARKET_CLOSE_AM = (9, 25), (11, 30)
MARKET_OPEN_PM, MARKET_CLOSE_PM = (13, 0), (15, 0)

APP_VERSION = "8.0.0"

CACHE_DIR = get_data_dir("Cache")
RPS_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_rps_precomputed.json")
SECTOR_RPS_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_sector_rps_precomputed.json")
SHAREHOLDER_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_shareholder_cache.json")
FINANCE_CACHE_FILE = os.path.join(CACHE_DIR, "vcp_finance_cache.json")
SPECIAL_LATEST_DATA = os.path.join(PROJECT_ROOT, "data", "special_latest_data.json")

MIN_MARKET_CAP = 4e9

INSTITUTION_KEYWORDS = ("基金", "券商", "保险", "信托", "社保", "QFII")
INSTITUTION_NAME_KEYWORDS = ("香港中央结算", "中国证券金融", "中央汇金")

DEFAULT_AMP_THRESHOLD = 0.45

CACHE_VERSION = 3
MAX_HISTORY_BARS = 500
INCREMENTAL_BARS = 30
MARKET_SYNC_WORKERS = 15
RPS_BUFFER_DAYS = 500

LOOKBACK_DAYS = 130
GROUP_DAYS = 15
PEAKS_FROM_GROUPS = 5
PCT_BASELINE = 0.93
MERGE_WITHIN_DAYS = 15
EXCLUDE_DAYS_FOR_PEAKS = 3

MIN_PEAKS_COUNT = 3
MAX_PEAKS_COUNT = 4
FLEXIBLE_MIN_INTERVAL = 30
FLEXIBLE_MAX_INTERVAL = 150
MIN_DAYS_AFTER_LAST_PEAK = 2
MIN_DAYS_AFTER_LAST_PEAK_CONFIRM = 3
MAX_R2_BELOW_R1_PCT = 0.15
MIN_FIRST_TO_THIRD_DAYS = 50
MIN_R1_R2_DAYS = 50

MIN_SMA50_SLOPE = -0.0003
