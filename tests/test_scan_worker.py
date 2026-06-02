# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

import pandas as pd

from ui.workers.scan_worker import ScanWorker


class DummyProvider:
    def __init__(self):
        self.cache_lock = threading.Lock()
        self._local_gbbq = {"loaded": True}
        self.cache_data = {
            "300093": pd.DataFrame(
                {
                    "close": [20.5, 21.0],
                    "entangle": [0.1, 0.1],
                },
                index=pd.to_datetime(["2026-04-16", "2026-04-17"]),
            )
        }
        self.code2name = {"300093": "300093"}
        self._offline = False
        self.repair_requests = []

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        normalized_codes = tuple(sorted(str(code) for code in (codes or ())))
        self.repair_requests.append((normalized_codes, refresh_missing))
        if refresh_missing and "300093" in normalized_codes:
            self.code2name["300093"] = "*ST金刚"
        return dict(self.code2name)

    def get_all_valid_data(self):
        return dict(self.cache_data)

    def get_data(self, code):
        return self.cache_data.get(code)


class DummyEngine:
    def __init__(self):
        self.evaluate_calls = 0

    def build_rps_matrix(self, _market_cache, _sd, _ed):
        return {
            "2026-04-17": {
                "rps250": {"300093": 92},
                "rps120": {"300093": 88},
            }
        }

    def evaluate_conditions(self, *_args, **_kwargs):
        self.evaluate_calls += 1
        return (
            True,
            "",
            {
                "收盘": 21.0,
                "评分": 96.3,
                "RPS强度": "89/93",
                "距突破": "-3.6%",
                "突破状态": "放量突破",
                "区间振幅": "29.3%",
            },
        )


class RejectingEngine(DummyEngine):
    def evaluate_conditions(self, *_args, **_kwargs):
        self.evaluate_calls += 1
        return False, "量能不足", {}


def test_scan_worker_repairs_placeholder_names_before_st_filter():
    provider = DummyProvider()
    engine = DummyEngine()
    worker = ScanWorker(
        provider,
        engine,
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )

    captured_results = []
    finished_states = []
    worker.result_ready.connect(lambda rows: captured_results.append(rows))
    worker.finished_scan.connect(lambda success, msg: finished_states.append((success, msg)))

    worker.run()

    assert provider.code2name["300093"] == "*ST金刚"
    assert any(refresh for _codes, refresh in provider.repair_requests)
    assert engine.evaluate_calls == 0
    assert captured_results == [[]]
    assert finished_states and finished_states[-1][0] is True


def test_scan_worker_enrich_hot_sectors_delegates_to_shared_helper(monkeypatch):
    provider = DummyProvider()
    worker = ScanWorker(
        provider,
        DummyEngine(),
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )
    rows = [
        {"代码": "300308", "触发日期": "2026-04-16", "热点板块": ""},
        {"代码": "688498", "触发日期": "2026-04-17", "热点板块": ""},
    ]
    calls = {}

    def fake_load_sector_rps_snapshot(data_provider, all_data, *, target_date, logger):
        calls["load"] = {
            "data_provider": data_provider,
            "all_data_keys": set(all_data),
            "target_date": target_date,
            "logger": logger,
        }
        return "manager", {"sector": {20: 95.0}}, "20260417", "cache"

    def fake_enrich_hot_sector_rows(input_rows, sector_manager, sector_rps, logger):
        calls["enrich"] = {
            "rows": input_rows,
            "sector_manager": sector_manager,
            "sector_rps": sector_rps,
            "logger": logger,
        }
        input_rows[0]["热点板块"] = "CPO"

    monkeypatch.setattr("ui.workers.scan_worker.load_sector_rps_snapshot", fake_load_sector_rps_snapshot)
    monkeypatch.setattr("ui.workers.scan_worker.enrich_hot_sector_rows", fake_enrich_hot_sector_rows)

    worker._enrich_hot_sectors(rows)

    assert calls["load"]["data_provider"] is provider
    assert calls["load"]["all_data_keys"] == set(provider.cache_data)
    assert calls["load"]["target_date"] == "2026-04-17"
    assert calls["enrich"]["rows"] is rows
    assert calls["enrich"]["sector_manager"] == "manager"
    assert rows[0]["热点板块"] == "CPO"


def test_scan_worker_enrich_market_caps_batches_close_prices(monkeypatch):
    provider = DummyProvider()
    worker = ScanWorker(
        provider,
        DummyEngine(),
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )
    rows = [{"代码": "300093"}, {"代码": "688498"}]
    calls = {}

    def fake_batch_check_market_cap(codes, *, close_prices):
        calls["codes"] = codes
        calls["close_prices"] = close_prices
        return {"300093": 12_300_000_000}

    monkeypatch.setattr("ui.workers.scan_worker.batch_check_market_cap", fake_batch_check_market_cap)

    worker._enrich_market_caps(rows)

    assert calls["codes"] == ["300093", "688498"]
    assert calls["close_prices"] == {"300093": 21.0}
    assert rows[0]["市值"].startswith("123")
    assert rows[0]["_cap_raw"] == 12_300_000_000
    assert rows[1]["市值"] == "--"
    assert rows[1]["_cap_raw"] == 0


def test_scan_worker_scan_candidate_for_day_returns_enriched_hit():
    provider = DummyProvider()
    engine = DummyEngine()
    worker = ScanWorker(
        provider,
        engine,
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )

    result = worker._scan_candidate_for_day(
        "300093",
        "2026-04-17",
        {"rps250": {"300093": 92}, "rps120": {"300093": 88}},
        {},
    )

    assert result["代码"] == "300093"
    assert result["名称"] == "300093"
    assert result["触发日期"] == "2026-04-17"
    assert result["热点板块"] == "-"
    assert engine.evaluate_calls == 1


def test_scan_worker_scan_candidate_for_day_tracks_rejection_reason():
    provider = DummyProvider()
    engine = RejectingEngine()
    worker = ScanWorker(
        provider,
        engine,
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )
    reason_stats = {}

    result = worker._scan_candidate_for_day(
        "300093",
        "2026-04-17",
        {"rps250": {"300093": 92}, "rps120": {"300093": 88}},
        reason_stats,
    )

    assert result is None
    assert reason_stats == {"量能不足": 1}
    assert engine.evaluate_calls == 1


def test_scan_worker_scan_matrix_candidates_collects_hits():
    provider = DummyProvider()
    engine = DummyEngine()
    worker = ScanWorker(
        provider,
        engine,
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
    )
    matrix = engine.build_rps_matrix(provider.get_all_valid_data(), "2026-04-17", "2026-04-17")

    rows = worker._scan_matrix_candidates(matrix)

    assert len(rows) == 1
    assert rows[0]["代码"] == "300093"
    assert rows[0]["触发日期"] == "2026-04-17"
    assert engine.evaluate_calls == 1


def test_scan_worker_run_stays_under_hotspot_budget():
    assert len(inspect.getsource(ScanWorker.run).splitlines()) <= 70
