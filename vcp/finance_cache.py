# -*- coding: utf-8 -*-
"""基本面数据本地缓存 — 总股本/流通股等变化极慢的数据

TTL 默认 7 天，避免重复网络请求。
"""

import os
import time
import pickle
from core.config import app_config


class FinanceCache:
    """财务数据本地缓存（单例）"""

    _instance = None
    _CACHE_FILENAME = 'finance_info_cache.pkl'

    @classmethod
    def get_instance(cls) -> 'FinanceCache':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._cache: dict = {}
        self._cache_ts: float = 0.0
        self._cache_path = os.path.join(
            app_config.cache_dir, self._CACHE_FILENAME
        )
        self._ttl = app_config.finance_cache_ttl_days * 86400
        self._load()

    def _load(self):
        """从磁盘加载"""
        if not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path, 'rb') as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                self._cache = data.get('data', {})
                self._cache_ts = data.get('ts', 0.0)
                # TTL 过期则清空
                if time.time() - self._cache_ts > self._ttl:
                    print(f"[财务缓存] 缓存已过期 ({app_config.finance_cache_ttl_days}天)，清空")
                    self._cache = {}
                else:
                    print(f"[财务缓存] 加载 {len(self._cache)} 条记录")
        except Exception as e:
            print(f"[财务缓存] ⚠ 加载失败: {e}")

    def save(self):
        """持久化到磁盘"""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, 'wb') as f:
                pickle.dump({'data': self._cache, 'ts': time.time()}, f)
        except Exception as e:
            print(f"[财务缓存] ⚠ 保存失败: {e}")

    def get(self, code: str) -> dict | None:
        """获取单只股票的财务数据"""
        return self._cache.get(code)

    def get_batch(self, codes: list[str]) -> dict:
        """批量获取，返回命中的 {code: info_dict}"""
        return {c: self._cache[c] for c in codes if c in self._cache}

    def put(self, code: str, info: dict):
        """写入单条"""
        self._cache[code] = info

    def put_batch(self, data: dict):
        """批量写入"""
        self._cache.update(data)

    def missing(self, codes: list[str]) -> list[str]:
        """返回缓存中不存在的代码列表"""
        return [c for c in codes if c not in self._cache]

    @property
    def size(self) -> int:
        return len(self._cache)


finance_cache = FinanceCache.get_instance()
