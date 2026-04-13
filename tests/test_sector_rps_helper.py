import json

from core import sector_rps_helper as helper


class FakeSectorManager:
    def __init__(self, build_result=None, info_str="光模块(20d=95)"):
        self.build_result = build_result or {"光模块": {20: 95.0}}
        self.info_str = info_str
        self.build_calls = []

    def build_sector_rps(self, all_data, target_date):
        self.build_calls.append((all_data, target_date))
        return self.build_result

    def check_sector_rps(self, code, sector_rps_dict, threshold=0):
        return True, self.info_str, 95.0


def test_load_sector_rps_snapshot_uses_matching_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "sector_cache.json"
    with open(cache_path, "w", encoding="utf-8") as file_obj:
        json.dump({"date": "20260410", "sector_rps": {"缓存板块": {20: 88.0}}}, file_obj, ensure_ascii=False)

    manager = FakeSectorManager()
    monkeypatch.setattr(helper, "SECTOR_RPS_CACHE_FILE", str(cache_path))
    monkeypatch.setattr(helper, "_get_sector_manager", lambda _provider: manager)

    sector_manager, sector_rps, trade_date, source = helper.load_sector_rps_snapshot(
        object(),
        {"000001": object()},
        target_date="2026-04-10",
    )

    assert sector_manager is manager
    assert trade_date == "20260410"
    assert source == "cache"
    assert sector_rps == {"缓存板块": {20: 88.0}}
    assert manager.build_calls == []


def test_load_sector_rps_snapshot_rebuilds_stale_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "sector_cache.json"
    with open(cache_path, "w", encoding="utf-8") as file_obj:
        json.dump({"date": "20260409", "sector_rps": {"旧板块": {20: 70.0}}}, file_obj, ensure_ascii=False)

    all_data = {"000001": ["dummy"]}
    manager = FakeSectorManager(build_result={"新板块": {20: 93.0}})
    monkeypatch.setattr(helper, "SECTOR_RPS_CACHE_FILE", str(cache_path))
    monkeypatch.setattr(helper, "_get_sector_manager", lambda _provider: manager)

    sector_manager, sector_rps, trade_date, source = helper.load_sector_rps_snapshot(
        object(),
        all_data,
        target_date="2026-04-10",
    )

    assert sector_manager is manager
    assert trade_date == "20260410"
    assert source == "rebuild"
    assert sector_rps == {"新板块": {20: 93.0}}
    assert manager.build_calls == [(all_data, "20260410")]

    with open(cache_path, "r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    assert payload["date"] == "20260410"
    assert payload["sector_rps"] == {"新板块": {"20": 93.0}}


def test_resolve_hot_sector_prefers_existing_text():
    manager = FakeSectorManager(info_str="不会被调用")
    assert helper.resolve_hot_sector("000001", "CPO | 光模块", manager, {"任意": {}}, logger=None) == "CPO | 光模块"


def test_enrich_hot_sector_rows_falls_back_to_placeholder(monkeypatch):
    class BrokenSectorManager:
        def check_sector_rps(self, code, sector_rps_dict, threshold=0):
            raise RuntimeError("boom")

    rows = [{"代码": "300308", "热点板块": ""}]
    helper.enrich_hot_sector_rows(rows, BrokenSectorManager(), {"任意": {}}, logger=None)
    assert rows[0]["热点板块"] == "--"
