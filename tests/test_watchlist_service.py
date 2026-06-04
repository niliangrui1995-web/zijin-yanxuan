# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import core.data_store as data_store_module
import domains.watchlist.service as watchlist_module
from domains.watchlist.service import WatchlistViewModel, watchlist_vm


class _Signal:
    def __init__(self, calls):
        self.calls = calls

    def emit(self, *args):
        self.calls.append(args)


class _ItemValue:
    def item(self):
        return 7


class _StringOnly:
    def __str__(self):
        return "string-only"


def test_watchlist_service_tags_entries_and_mutations_stay_in_memory(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    saves = []
    events = []
    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: saves.append(deepcopy(watchlist_vm._cache)))
    monkeypatch.setattr(
        watchlist_module,
        "event_bus",
        SimpleNamespace(sig_system_log=_Signal(events), sig_watchlist_changed=_Signal(events)),
    )

    try:
        assert WatchlistViewModel._normalize_entry_value(_ItemValue()) == 7
        assert WatchlistViewModel._normalize_entry_value(_StringOnly()) == "string-only"
        assert WatchlistViewModel.normalize_source_tags("手动|扫描｜手动") == ["扫描", "手动"]
        assert WatchlistViewModel.normalize_source_tags(123) == []
        assert WatchlistViewModel.format_source_tags(["手动", "扫描"]) == "扫描｜手动"
        assert WatchlistViewModel.derive_source_tags(
            {
                "催化剂": "AI",
                "龙虎榜": "yes",
                "业绩异动": "yes",
                "大宗交易": "yes",
                "触发日期": "2026-04-20",
            },
            existing_tags=["手动"],
        ) == ["战报", "龙虎", "业绩", "大宗", "扫描", "手动"]

        entry = WatchlistViewModel._build_watchlist_entry(
            "Alpha",
            {"现价": 12.3, "自定义": _ItemValue(), "对象": _StringOnly(), "业绩异动": "yes"},
            source_tags=["手动"],
        )
        assert entry["现价"] == "--"
        assert entry["自定义"] == 7
        assert entry["对象"] == "string-only"
        assert "业绩" in entry["来源标签"]

        watchlist_vm._cache = {
            "000001": {"名称": "Alpha", "来源标签": ["手动"], "旧字段": "x"},
            "000002": {"名称": "Beta", "来源标签": ["扫描"]},
        }

        assert watchlist_vm.is_in_watchlist("000001") is True
        assert watchlist_vm.get_all_codes() == ["000001", "000002"]
        assert watchlist_vm.patch_entry("", {"备注": "x"}) is False
        assert watchlist_vm.patch_entry("missing", {"备注": "x"}) is False
        assert watchlist_vm.patch_entry("000001", {"名称": "Alpha"}) is False
        assert watchlist_vm.patch_entry("000001", {"备注": "core"}, remove_keys=["旧字段"]) is True
        assert watchlist_vm._cache["000001"]["备注"] == "core"
        assert "旧字段" not in watchlist_vm._cache["000001"]

        assert watchlist_vm.bulk_patch_entries(None) is False
        assert watchlist_vm.bulk_patch_entries({"": {"备注": "x"}, "missing": {"备注": "x"}}) is False
        assert watchlist_vm.bulk_patch_entries({"000002": {"备注": "watch"}}, remove_keys=["none"]) is True
        assert watchlist_vm._cache["000002"]["备注"] == "watch"

        assert watchlist_vm.replace_watchlist_data(None) is False
        assert watchlist_vm.replace_watchlist_data({"": {}, "bad": "entry"}) is False
        same = deepcopy(watchlist_vm._cache)
        assert watchlist_vm.replace_watchlist_data(same) is False
        assert watchlist_vm.replace_watchlist_data({"000003": {"名称": "Gamma", "触发日期": "2026-04-20"}}) is True
        assert list(watchlist_vm._cache) == ["000003"]

        assert watchlist_vm.add_stock("", "") is False
        assert watchlist_vm.add_stock("000003", "Gamma") is False
        assert watchlist_vm.add_stock("000004", "Delta", {"大宗交易": "yes"}) is True
        assert "000004" in watchlist_vm._cache

        watchlist_vm.toggle_stock("000004", "Delta")
        assert "000004" not in watchlist_vm._cache
        watchlist_vm.toggle_stock("000004", "Delta")
        assert "000004" in watchlist_vm._cache

        watchlist_vm.pin_to_top("000004")
        assert next(iter(watchlist_vm._cache)) == "000004"
        watchlist_vm.move_to_bottom("000004")
        assert list(watchlist_vm._cache)[-1] == "000004"
        watchlist_vm.reorder(["000004"])
        assert next(iter(watchlist_vm._cache)) == "000004"
        assert saves
        assert events
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_service_loads_legacy_json_and_handles_bad_loads(monkeypatch, tmp_path):
    original_cache = deepcopy(watchlist_vm._cache)
    saved_payloads = []

    class FakeDataStore:
        def load_json(self, key):
            assert key == "watchlist_special"
            return None

        def save_json(self, key, payload):
            saved_payloads.append((key, deepcopy(payload)))

    path = tmp_path / "special.json"
    path.write_text(
        json.dumps({"000001": {"名称": "Alpha", "现价": 12.3, "涨幅%": 1.2, "市值": 100, "触发日期": "2026-04-20"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(data_store_module, "DataStore", FakeDataStore)
    monkeypatch.setattr(watchlist_module, "SPECIAL_LATEST_DATA", str(path))

    try:
        watchlist_vm._load_data()
        assert saved_payloads
        assert not path.exists()
        assert (tmp_path / "special.json.migrated").exists()
        assert watchlist_vm._cache["000001"]["现价"] == "--"
        assert "扫描" in watchlist_vm._cache["000001"]["来源标签"]

        class ListDataStore:
            def load_json(self, key):
                return []

        monkeypatch.setattr(data_store_module, "DataStore", ListDataStore)
        watchlist_vm._load_data()
        assert watchlist_vm._cache == {}

        class FailingDataStore:
            def load_json(self, key):
                raise RuntimeError("load failed")

        monkeypatch.setattr(data_store_module, "DataStore", FailingDataStore)
        watchlist_vm._load_data()
        assert watchlist_vm._cache == {}
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_service_save_data_success_and_failure(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    saved_payloads = []

    class SavingDataStore:
        def save_json(self, key, payload):
            saved_payloads.append((key, deepcopy(payload)))

    try:
        watchlist_vm._cache = {"000001": {"名称": "Alpha"}}
        monkeypatch.setattr(data_store_module, "DataStore", SavingDataStore)
        watchlist_vm._save_data()
        assert saved_payloads == [("watchlist_special", {"000001": {"名称": "Alpha"}})]

        class FailingDataStore:
            def save_json(self, key, payload):
                raise RuntimeError("save failed")

        monkeypatch.setattr(data_store_module, "DataStore", FailingDataStore)
        watchlist_vm._save_data()
    finally:
        watchlist_vm._cache = original_cache
