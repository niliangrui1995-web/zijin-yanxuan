# -*- coding: utf-8 -*-
"""
tests/test_data_store.py — DataStore SQLite 存储层测试

验证目标：
    1. 单例模式生效
    2. save_json / load_json 读写闭环
    3. earnings_state 专用方法正确性
    4. 不存在的 key 返回 default
"""

import os
import tempfile
from pathlib import Path


def test_pytest_session_default_database_is_outside_project_data():
    from core.data_store import data_store

    configured = Path(os.environ["VCP_HUNTER_DB_PATH"]).resolve()
    production = Path(__file__).resolve().parents[1] / "data" / "vcp_hunter.db"

    assert configured != production.resolve()
    assert Path(data_store._db_path).resolve() == configured


def test_default_data_store_path_honors_test_environment(monkeypatch, tmp_path):
    from core.data_store import DataStore

    db_path = tmp_path / "isolated" / "test.db"
    monkeypatch.setenv("VCP_HUNTER_DB_PATH", str(db_path))
    monkeypatch.setattr(DataStore, "_instance", None)

    store = DataStore()
    try:
        assert store._db_path == str(db_path.resolve())
    finally:
        store.close()
        DataStore._instance = None


class TestDataStore:
    """测试 DataStore 单例和 KV 读写"""

    def _make_store(self):
        """创建一个临时 db 的 DataStore 实例用于测试"""
        from core.data_store import DataStore

        # 绕过单例以便每次测试独立
        DataStore._instance = None
        if hasattr(DataStore, "_initialized"):
            del DataStore._initialized

        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "test_vcp.db")
        store = DataStore(db_path=db_path)

        # 重置单例，不影响全局
        DataStore._instance = None
        if hasattr(store, "_initialized"):
            del store._initialized

        return store, db_path

    def test_save_load_roundtrip(self):
        """写入→读取→验证一致性"""
        store, db_path = self._make_store()
        try:
            test_data = {"name": "测试", "value": 42, "list": [1, 2, 3]}
            store.save_json("test_key", test_data)
            result = store.load_json("test_key")
            assert result == test_data, f"数据不一致: {result}"
        finally:
            store.close()
            os.remove(db_path)

    def test_load_nonexistent_returns_default(self):
        """读取不存在的 key 应返回 default"""
        store, db_path = self._make_store()
        try:
            result = store.load_json("nonexistent_key", default={"fallback": True})
            assert result == {"fallback": True}
        finally:
            store.close()
            os.remove(db_path)

    def test_upsert_overwrites(self):
        """同一个 key 第二次写入应覆盖"""
        store, db_path = self._make_store()
        try:
            store.save_json("upsert_key", {"version": 1})
            store.save_json("upsert_key", {"version": 2})
            result = store.load_json("upsert_key")
            assert result["version"] == 2, "UPSERT 未生效，旧值未被覆盖"
        finally:
            store.close()
            os.remove(db_path)

    def test_earnings_state_methods(self):
        """验证 earnings_state 专用方法"""
        store, db_path = self._make_store()
        try:
            store.save_earnings_state(
                last_sync_date="2026-04-06",
                seen=["fp1", "fp2"],
                records=[{"代码": "000001", "名称": "平安银行"}],
            )
            result = store.load_earnings_state()
            assert result["last_sync_date"] == "2026-04-06"
            assert len(result["seen"]) == 2
            assert len(result["records"]) == 1
            assert result["records"][0]["代码"] == "000001"
        finally:
            store.close()
            os.remove(db_path)

    def test_delete_key(self):
        """删除 key 后读取返回 default"""
        store, db_path = self._make_store()
        try:
            store.save_json("delete_me", {"data": True})
            store.delete_key("delete_me")
            result = store.load_json("delete_me", default=None)
            assert result is None, "删除后仍能读到数据"
        finally:
            store.close()
            os.remove(db_path)
