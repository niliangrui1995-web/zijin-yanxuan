# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.ui_task_lifecycle_service import TaskLifecycleGroup
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
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
        self.adjustment_metadata_calls = []

    def ensure_adjustment_metadata(self, *, force=False):
        self.adjustment_metadata_calls.append(bool(force))
        return {"loaded": True}

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


def test_scan_worker_stops_inside_candidate_loop_when_token_is_cancelled(monkeypatch):
    token = CancellationToken()
    worker = ScanWorker(
        DummyProvider(),
        DummyEngine(),
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
        cancellation_token=token,
    )
    visited = []

    def _scan(code, *_args):
        visited.append(code)
        token.cancel("test_cancel")
        return None

    monkeypatch.setattr(worker, "_scan_candidate_for_day", _scan)
    matrix = {
        "2026-04-17": {
            "rps250": {"000001": 90, "000002": 91, "000003": 92},
            "rps120": {},
        }
    }

    with pytest.raises(TaskCancelledError, match="test_cancel"):
        worker._scan_matrix_candidates(matrix)

    assert visited == ["000001"]


def test_scan_worker_checks_deadline_before_rps_provider_stage():
    token = CancellationToken.with_timeout(0)
    engine = DummyEngine()
    worker = ScanWorker(
        DummyProvider(),
        engine,
        "2026-04-17",
        "2026-04-17",
        SimpleNamespace(rps_threshold=80),
        cancellation_token=token,
    )

    with pytest.raises(TimeoutError, match="截止时间"):
        worker._build_scan_matrix()

    assert engine.evaluate_calls == 0


def test_scan_tab_owns_and_passes_scan_lifecycle_token(monkeypatch):
    from ui.tabs import scan_tab as scan_tab_module

    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class _Worker:
        def __init__(self, *_args, **kwargs):
            self.kwargs = kwargs
            self.progress = _Signal()
            self.result_ready = _Signal()
            self.finished_scan = _Signal()
            self.finished = _Signal()
            self.started = False
            self.cancelled = False

        def start(self):
            self.started = True

        def isRunning(self):
            return self.started

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(scan_tab_module, "ScanWorker", _Worker)
    spin = SimpleNamespace(value=lambda: 1)
    tab = SimpleNamespace(
        worker=None,
        _task_lifecycle=TaskLifecycleGroup(),
        _scan_cancel_requested=False,
        _set_scan_action_state=lambda _state: None,
        _save_scan_params=lambda: None,
        _on_scan_results=lambda _rows: None,
        _on_scan_finished=lambda _success, _message: None,
        _on_worker_thread_finished=lambda: None,
        spn_scan_rps=spin,
        spn_scan_amp=spin,
        spn_scan_ma_bind=spin,
        spn_scan_high250=spin,
        spn_scan_amount=spin,
        data_provider=object(),
        engine=object(),
    )

    assert scan_tab_module.ScanTab.start_scan(tab, "20260417", "20260417") is True

    token = tab._scan_token
    assert tab.worker.kwargs["cancellation_token"] is token
    assert tab.worker.kwargs["timeout_sec"] == scan_tab_module.ScanTab.SCAN_TIMEOUT_SEC
    assert tab.worker.started is True

    assert scan_tab_module.ScanTab.cancel_scan(tab) is True
    assert token.cancelled is True
    assert token.reason == "user_cancelled"
    assert tab.worker.cancelled is True


def test_scan_worker_run_stays_under_hotspot_budget():
    assert len(inspect.getsource(ScanWorker.run).splitlines()) <= 70


def test_scan_worker_does_not_force_full_gc_from_ui_package():
    import ui.workers.scan_worker as scan_worker_module

    source = inspect.getsource(scan_worker_module)
    assert "gc.collect(" not in source
