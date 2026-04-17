# -*- coding: utf-8 -*-
"""
core/data_store.py — 统一 SQLite 数据存储层（单例）

为什么要这个：
    之前各模块各自读写 JSON 文件，多线程同写一个文件会竞态覆盖（数据丢失），
    而且每次保存都要全量重写整个文件，IO 浪费严重。
    SQLite WAL 模式天然支持一写多读、事务原子性、崩溃自恢复。

设计原则：
    1. 单例模式 — 全局共享一个 connection，不存在多连接冲突
    2. WAL 模式 — 一写多读不阻塞
    3. 裸 SQL — 不引 ORM，项目体量用不上
    4. JSON 列存储 — 对结构松散的数据直接存 JSON 字符串
    5. 幂等建表 — IF NOT EXISTS，脚本可重跑
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)


class DataStore:
    """VCP Hunter 统一数据存储 — SQLite 单例"""

    _instance: Optional["DataStore"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = ""):
        # 防止重复初始化
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        if not db_path:
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(root_dir, "data", "vcp_hunter.db")

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path

        # check_same_thread=False: 因为我们用 _lock 自己管线程安全
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL 模式：一写多读不阻塞，崩溃自恢复
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row

        self._ensure_tables()
        self._clean_migrated_backups()
        log.info(f"[DataStore] SQLite 存储已就绪: {db_path}")

    def _ensure_tables(self):
        """幂等建表：IF NOT EXISTS，脚本可重跑"""
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self._conn.commit()

    def _clean_migrated_backups(self):
        """启动时自动清理超过 30 天的 .migrated 备份文件"""
        import time
        data_dir = os.path.dirname(self._db_path)
        cutoff = time.time() - (30 * 86400)
        try:
            for filename in os.listdir(data_dir):
                if not filename.endswith('.migrated'):
                    continue
                filepath = os.path.join(data_dir, filename)
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    log.info(f"[DataStore] 已清理过期备份: {filename}")
        except OSError as _e:
            log.debug(f"[DataStore] 迁移备份清理异常: {_e}")

    # ========== 通用 KV 操作 ==========

    def save_json(self, key: str, data) -> None:
        """将任意 Python 对象序列化为 JSON 存入 kv_store"""
        json_str = json.dumps(data, ensure_ascii=False, default=str)
        with self._lock:
            self._conn.execute(
                """INSERT INTO kv_store (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, json_str)
            )
            self._conn.commit()

    def load_json(self, key: str, default=None):
        """从 kv_store 读取并反序列化 JSON，不存在则返回 default"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            log.error(f"[DataStore] 反序列化失败: key={key}")
            return default

    def delete_key(self, key: str) -> None:
        """删除指定 key"""
        with self._lock:
            self._conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            self._conn.commit()

    # ========== 通用 SQL 助手 ==========

    @contextmanager
    def transaction(self):
        """提供一个带锁事务上下文，适合批量写入或多表更新。"""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def execute(self, sql: str, params=()):
        """执行单条 SQL 并自动提交。"""
        with self.transaction() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount

    def executemany(self, sql: str, seq_of_params):
        """执行批量 SQL 并自动提交。"""
        rows = list(seq_of_params or [])
        if not rows:
            return 0
        with self.transaction() as cursor:
            cursor.executemany(sql, rows)
            return cursor.rowcount

    def execute_script(self, sql_script: str) -> None:
        """执行多条建表/迁移脚本。"""
        with self._lock:
            self._conn.executescript(sql_script)
            self._conn.commit()

    def fetch_all(self, sql: str, params=()):
        """查询多行，统一返回 dict 列表。"""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def fetch_one(self, sql: str, params=(), default=None):
        """查询单行，统一返回 dict。"""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            row = cursor.fetchone()
        if row is None:
            return default
        return dict(row)

    # ========== 业绩异动专用方法 ==========

    def save_earnings_state(self, last_sync_date: str, seen: list, records: list) -> None:
        """持久化业绩异动引擎的全部状态"""
        self.save_json("earnings_state", {
            "last_sync_date": last_sync_date,
            "seen": seen,
            "records": records,
        })

    def load_earnings_state(self) -> dict:
        """读取业绩异动引擎状态，返回 dict 或空 dict"""
        return self.load_json("earnings_state", default={})

    # ========== 生命周期 ==========

    def close(self):
        """应用退出时调用，确保数据落盘"""
        try:
            self._conn.close()
            log.info("[DataStore] SQLite 连接已关闭")
        except sqlite3.Error as _e:
            log.debug(f"[DataStore] SQLite 关闭异常: {_e}")


# 全局单例
data_store = DataStore()
