# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from core.json_cache import remove_cache_file

RT_QUOTE_CACHE_TTL_SEC = 180.0
RT_QUOTE_CACHE_MAX_ENTRIES = 4096


def prune_rt_quote_cache(provider, now: float | None = None) -> int:
    now = time.time() if now is None else now
    ttl = float(getattr(provider, "_rt_quote_cache_ttl_sec", RT_QUOTE_CACHE_TTL_SEC))
    max_entries = int(getattr(provider, "_rt_quote_cache_max_entries", RT_QUOTE_CACHE_MAX_ENTRIES))
    removed = 0

    with provider._rt_quote_lock:
        expired_codes = [
            code
            for code, cached_at in provider._rt_quote_time.items()
            if now - float(cached_at or 0) > ttl
        ]
        for code in expired_codes:
            provider._rt_quote_time.pop(code, None)
            provider._rt_quote_cache.pop(code, None)
        removed += len(expired_codes)

        overflow = len(provider._rt_quote_time) - max_entries
        if overflow > 0:
            oldest_codes = sorted(
                provider._rt_quote_time.items(),
                key=lambda item: item[1],
            )[:overflow]
            for code, _ in oldest_codes:
                provider._rt_quote_time.pop(code, None)
                provider._rt_quote_cache.pop(code, None)
            removed += len(oldest_codes)

    return removed


def compact_runtime_caches(provider, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    removed = prune_rt_quote_cache(provider, now=now)
    with provider._rt_quote_lock:
        rt_quote_cache_size = len(provider._rt_quote_cache)
    rt_runtime = provider.get_realtime_runtime_stats()
    return {
        "removed_rt_quotes": removed,
        "rt_quote_cache_size": rt_quote_cache_size,
        "history_symbol_count": len(provider.cache_data),
        "rt_runtime": rt_runtime,
    }


def downcast_memory(provider, *, logger):
    """将 cache_data 中所有 float64 列降为 float32，节省数值内存。"""
    if getattr(provider, "_downcast_done", False):
        return

    count = 0
    for index, (_code, df) in enumerate(list(provider.cache_data.items())):
        if df is None:
            continue
        changed = False
        for col in df.columns:
            if df[col].dtype == "float64":
                df[col] = df[col].astype("float32")
                changed = True
        if changed:
            count += 1
        if index % 50 == 0 and index > 0:
            time.sleep(0)

    provider._downcast_done = True
    if count > 0:
        logger.info(f"[缓存优化] 已压缩 {count} 只标的数据类型，节省内存")


def _get_market_data_warehouse(provider):
    warehouse = getattr(provider, "market_data_warehouse", None)
    if warehouse is not None:
        return warehouse
    try:
        from infra.market_data.market_data_warehouse import get_default_market_data_warehouse

        warehouse = get_default_market_data_warehouse()
        setattr(provider, "market_data_warehouse", warehouse)
        return warehouse
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _set_market_data_source_status(provider, status, *, active_layer: str | None = None) -> None:
    if hasattr(status, "to_dict"):
        payload = status.to_dict()
    else:
        payload = dict(status or {})
    if active_layer:
        payload["active_layer"] = active_layer
    try:
        provider._last_market_data_source_status = payload
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


def load_cache_from_disk(provider, *, logger) -> str:
    """Load disk cache into memory and return the cache date string."""
    warehouse = _get_market_data_warehouse(provider)
    if warehouse is not None:
        result = warehouse.read_full_market()
        _set_market_data_source_status(provider, result.status)
        if result.status.ok and result.data:
            with provider.cache_lock:
                provider.cache_data = result.data
            remove_cache_file(provider.legacy_cache_file)
            remove_cache_file(provider.legacy_cache_file + ".corrupted")
            remove_cache_file(provider.legacy_fallback_cache_file)
            logger.info(
                f"\n[data-warehouse] Parquet/SQLite loaded: {len(provider.cache_data)} symbols "
                f"(trade_date: {result.status.trade_date})"
            )
            return result.status.trade_date
        logger.info(
            f"[data-warehouse] unavailable, trying legacy parquet fallback: "
            f"{result.status.data_status} {result.status.error}"
        )

    try:
        from vcp.polars_engine import load_cache_parquet

        result = load_cache_parquet()
        if result is not None:
            loaded_data, last_date = result
            if loaded_data and isinstance(loaded_data, dict):
                with provider.cache_lock:
                    provider.cache_data = loaded_data
                if warehouse is not None:
                    status = warehouse.register_existing_parquet(
                        trade_date=last_date,
                        source="legacy_parquet",
                        source_version="vcp.polars_engine.load_cache_parquet:v3",
                    )
                    active_layer = "legacy_parquet_bootstrap" if status.ok else "legacy_parquet_without_manifest"
                    _set_market_data_source_status(provider, status, active_layer=active_layer)
                else:
                    _set_market_data_source_status(
                        provider,
                        {
                            "ok": True,
                            "trade_date": last_date,
                            "symbol_count": len(provider.cache_data),
                            "data_status": "ok",
                        },
                        active_layer="legacy_parquet_without_manifest",
                    )
                remove_cache_file(provider.legacy_cache_file)
                remove_cache_file(provider.legacy_cache_file + ".corrupted")
                remove_cache_file(provider.legacy_fallback_cache_file)
                logger.info(
                    f"\n[数据中台] Parquet 快速加载: {len(provider.cache_data)} 只标的 (缓存日期: {last_date})"
                )
                return last_date
    except ImportError:
        pass
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error(f"[数据中台] Parquet 加载失败: {exc}")

    if provider.legacy_cache_file or provider.legacy_fallback_cache_file:
        import os

        if os.path.exists(provider.legacy_cache_file) or os.path.exists(provider.legacy_fallback_cache_file):
            logger.info("[数据中台] 检测到旧版 pkl 行情缓存，已弃用并忽略")
            remove_cache_file(provider.legacy_cache_file)
            remove_cache_file(provider.legacy_cache_file + ".corrupted")
            remove_cache_file(provider.legacy_fallback_cache_file)

    return ""
