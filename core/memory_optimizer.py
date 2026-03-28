# -*- coding: utf-8 -*-
"""core/memory_optimizer.py — 内存使用监控与优化工具

提供：
- 运行时内存使用量查询
- DataFrame 降精度
- 对象引用分析
"""

import gc
import sys
from typing import Optional


def get_memory_usage_mb() -> float:
    """获取当前进程内存使用量（MB）"""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def downcast_dataframe(df, float_cols: Optional[list] = None):
    """将 DataFrame 中的 float64 列降精度为 float32 以节省约 50% 内存

    Args:
        df: pandas DataFrame
        float_cols: 指定需要降精度的列名列表，None 则自动处理所有 float64 列
    Returns:
        降精度后的 DataFrame（原地修改）
    """
    import numpy as np
    if df is None or len(df) == 0:
        return df

    if float_cols is None:
        float_cols = df.select_dtypes(include=['float64']).columns.tolist()

    for col in float_cols:
        if col in df.columns:
            df[col] = df[col].astype(np.float32)

    return df


def force_gc(generation: int = 2) -> int:
    """强制垃圾回收并返回回收对象数"""
    collected = gc.collect(generation)
    return collected


def get_top_memory_objects(top_n: int = 10) -> list:
    """获取内存占用最大的 top_n 类型统计（调试用）"""
    import collections
    type_counts: dict = collections.defaultdict(lambda: [0, 0])
    for obj in gc.get_objects():
        obj_type = type(obj).__name__
        try:
            size = sys.getsizeof(obj)
        except (TypeError, ReferenceError):
            size = 0
        type_counts[obj_type][0] += 1
        type_counts[obj_type][1] += size

    sorted_types = sorted(type_counts.items(), key=lambda x: x[1][1], reverse=True)
    return [(name, count, size_bytes) for name, (count, size_bytes) in sorted_types[:top_n]]
