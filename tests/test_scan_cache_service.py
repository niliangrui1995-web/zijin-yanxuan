from __future__ import annotations

import app.services.scan_cache_service as scan_cache_service


class _FakeStore:
    def __init__(self, payload=None):
        self.payload = payload
        self.saved = []
        self.deleted = []

    def load_json(self, key, default=None):
        return self.payload if self.payload is not None else default

    def save_json(self, key, payload):
        self.saved.append((key, payload))

    def delete_key(self, key):
        self.deleted.append(key)


def test_save_scan_cache_uses_storage_boundary(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(scan_cache_service, "DataStore", lambda: store)

    assert scan_cache_service.save_scan_cache([], {}) == "cleared"
    assert store.deleted == ["scan_cache"]

    assert scan_cache_service.save_scan_cache([{"代码": "000001"}], {"rps": 90}) == "saved"
    assert store.saved[0][0] == "scan_cache"
    assert store.saved[0][1]["results"] == [{"代码": "000001"}]


def test_load_scan_cache_migrates_legacy_json(monkeypatch, tmp_path):
    legacy = tmp_path / "data" / "scan_cache.json"
    legacy.parent.mkdir()
    legacy.write_text('{"results": [{"代码": "000001"}]}', encoding="utf-8")
    store = _FakeStore(payload={})
    monkeypatch.setattr(scan_cache_service, "DataStore", lambda: store)
    monkeypatch.setattr(scan_cache_service, "PROJECT_ROOT", str(tmp_path))

    payload, migrated = scan_cache_service.load_scan_cache()

    assert migrated is True
    assert payload["results"] == [{"代码": "000001"}]
    assert store.saved == [("scan_cache", payload)]
    assert not legacy.exists()
    assert legacy.with_suffix(".json.migrated").exists()
