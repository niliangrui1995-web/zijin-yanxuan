# -*- coding: utf-8 -*-
"""Cache helpers for Asian K-line snapshots."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime


def _resolve_cache_output_dir(output_dir: str | None = None) -> str:
    """解析亚洲 K 线缓存输出目录。"""
    if output_dir:
        return output_dir
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "Cache")


def _latest_cache_path(output_dir: str | None = None) -> str:
    return os.path.join(_resolve_cache_output_dir(output_dir), "asian_klines_latest.json")


def _rows_to_map(rows: list[dict] | None) -> dict[str, dict]:
    """按 ticker 建立唯一索引，便于做缺票校验与旧缓存回填。"""
    row_map: dict[str, dict] = {}
    for row in rows or []:
        ticker = str((row or {}).get("ticker", "")).strip()
        if ticker and ticker not in row_map:
            row_map[ticker] = row
    return row_map


def _load_cached_row_map(output_dir: str | None = None) -> dict[str, dict]:
    cache_path = _latest_cache_path(output_dir)
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return _rows_to_map(raw.get("stocks", []))


def save_kline_data(data: list[dict], output_dir: str | None = None) -> str:
    """保存 K 线数据到 JSON 文件。"""
    output_dir = _resolve_cache_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    filepath = os.path.join(output_dir, f"asian_klines_{now_str}.json")

    # Why: 生成元数据摘要，前端可以读 meta 做标题/更新时间显示
    meta = {
        "generated_at": datetime.now().isoformat(),
        "total_stocks": len(data),
        "markets": {},
    }
    for item in data:
        market = item["market"]
        meta["markets"].setdefault(market, 0)
        meta["markets"][market] += 1

    output = {
        "meta": meta,
        "stocks": data,
    }

    tmp_filepath = f"{filepath}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp_filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_filepath, filepath)

    logging.info(f"💾 K 线数据已保存: {filepath} ({os.path.getsize(filepath) / 1024:.0f} KB)")

    # Why: 同时生成一份 latest.json 供看板前端固定路径读取
    latest_path = os.path.join(output_dir, "asian_klines_latest.json")
    tmp_latest_path = f"{latest_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp_latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_latest_path, latest_path)
    logging.info(f"💾 最新快照已更新: {latest_path}")

    return filepath


__all__ = [
    "_latest_cache_path",
    "_load_cached_row_map",
    "_resolve_cache_output_dir",
    "_rows_to_map",
    "save_kline_data",
]
