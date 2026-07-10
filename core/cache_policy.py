# core/cache_policy.py
# ================================================================================
# 紫金研选 统一缓存淘汰策略
#
# 为什么需要这个: 缓存文件散落在 data/Cache/ 和 data/parquet/ 下，
# 有的设了短期过期，有的长期保留(rps/parquet)。
# 时间一长磁盘会越来越大。这里统一定义过期规则和清理逻辑。
# ================================================================================
import glob
import os
import time

from core.logger import get_logger

log = get_logger(__name__)


# 缓存策略配置：每个条目定义 pattern(匹配规则) 和 max_age_days(过期天数)
CACHE_POLICIES = [
    {
        "name": "RPS 预计算缓存",
        "directory": "data/Cache",
        "pattern": "vcp_rps_precomputed.json",
        "max_age_days": 30,
    },
    {
        "name": "旧版 RPS 预计算缓存",
        "directory": "data/Cache",
        "pattern": "vcp_rps_precomputed.pkl",
        "max_age_days": 1,
    },
    {
        "name": "板块 RPS 缓存",
        "directory": "data/Cache",
        "pattern": "vcp_sector_rps_precomputed.json",
        "max_age_days": 30,
    },
    {
        "name": "旧版板块 RPS 缓存",
        "directory": "data/Cache",
        "pattern": "vcp_sector_rps_precomputed.pkl",
        "max_age_days": 1,
    },
    {
        "name": "gbbq 解析缓存",
        "directory": "data/Cache",
        "pattern": "gbbq_parsed.json",
        "max_age_days": 60,
    },
    {
        "name": "旧版 gbbq 解析缓存",
        "directory": "data/Cache",
        "pattern": "gbbq_parsed.pkl",
        "max_age_days": 1,
    },
    {
        "name": "亚洲市场 K 线历史快照",
        "directory": "data/Cache",
        "pattern": "asian_klines_*.json",
        "max_age_days": 3,
        "exclude_names": ["asian_klines_latest.json"],
    },
    {
        "name": "临时写入残片",
        "directory": "data/Cache",
        "pattern": "*.tmp",
        "max_age_days": 1,
    },
    {
        "name": "扫描结果缓存",
        "directory": "data",
        "pattern": "scan_cache.json",
        "max_age_days": 30,
    },
    {
        "name": "Parquet 数据缓存",
        "directory": "data/parquet",
        "pattern": "*.parquet",
        "max_age_days": 90,
    },
]


def cleanup_stale_caches(project_root: str, dry_run: bool = False) -> dict:
    """
    扫描并清理过期缓存文件

    参数:
        project_root: 项目根目录 (紫金研选/)
        dry_run: True 时仅统计不实际删除

    返回:
        {"cleaned": 已清理文件数, "freed_bytes": 释放字节数, "details": 明细列表}
    """
    now = time.time()
    result = {"cleaned": 0, "freed_bytes": 0, "details": []}

    for policy in CACHE_POLICIES:
        target_dir = os.path.join(project_root, policy["directory"])
        if not os.path.isdir(target_dir):
            continue

        pattern = os.path.join(target_dir, policy["pattern"])
        files = glob.glob(pattern)
        exclude_names = set(policy.get("exclude_names", []))

        for filepath in files:
            try:
                if os.path.basename(filepath) in exclude_names:
                    continue

                mtime = os.path.getmtime(filepath)
                age_days = (now - mtime) / 86400

                if age_days > policy["max_age_days"]:
                    size = os.path.getsize(filepath)
                    detail = {
                        "file": filepath,
                        "category": policy["name"],
                        "age_days": round(age_days, 1),
                        "size_bytes": size,
                    }
                    result["details"].append(detail)

                    if not dry_run:
                        os.remove(filepath)
                        log.info(
                            f"[缓存清理] 已删除过期文件: {os.path.basename(filepath)} "
                            f"({policy['name']}, {age_days:.0f}天前, {size / 1024:.1f}KB)"
                        )

                    result["cleaned"] += 1
                    result["freed_bytes"] += size

            except OSError as e:
                log.warning(f"[缓存清理] 无法处理文件 {filepath}: {e}")

    if result["cleaned"] > 0:
        freed_mb = result["freed_bytes"] / (1024 * 1024)
        log.info(f"[缓存清理] 共清理 {result['cleaned']} 个过期文件, 释放 {freed_mb:.1f} MB 磁盘空间")
    else:
        log.info("[缓存清理] 未发现需要清理的过期缓存")

    return result
