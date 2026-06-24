# -*- coding: utf-8 -*-
import datetime as dt

from ui.workers.rt_scan_worker import RtScanWorker


class _DummyEngine:
    def __init__(self):
        self.bundle = None

    def set_precomputed_rps(self, cache_date, rps120, rps250):
        self.bundle = {
            "date": cache_date,
            "rps120": rps120,
            "rps250": rps250,
        }


class _DummyProvider:
    code2name = {}


class _FakeTime:
    def __init__(self, now):
        self._now = now

    def time(self):
        return self._now


class _QuotePressureProvider:
    code2name = {}
    cache_data = {}

    def __init__(self, stats):
        self.stats = stats

    def get_quote_request_stats(self):
        return self.stats


def test_persist_rps_snapshot_uses_resolved_trade_date(monkeypatch):
    engine = _DummyEngine()
    worker = RtScanWorker(_DummyProvider(), engine)
    worker._rps120 = {"000001": 88.0}
    worker._rps250 = {"000001": 92.0}
    saved = []
    removed = []

    monkeypatch.setattr(
        "ui.workers.rt_scan_worker.save_json_file",
        lambda path, payload: saved.append((path, payload)),
    )
    monkeypatch.setattr(
        "ui.workers.rt_scan_worker.remove_cache_file",
        lambda path: removed.append(path),
    )

    assert worker._persist_rps_snapshot("20260417", 5113) is True
    assert saved and saved[0][1]["date"] == "20260417"
    assert engine.bundle is not None
    assert engine.bundle["date"] == "20260417"
    assert removed


def test_persist_rps_snapshot_skips_small_partial_cache(monkeypatch):
    engine = _DummyEngine()
    worker = RtScanWorker(_DummyProvider(), engine)
    worker._rps120 = {"000001": 88.0}
    worker._rps250 = {"000001": 92.0}
    saved = []

    monkeypatch.setattr(
        "ui.workers.rt_scan_worker.save_json_file",
        lambda path, payload: saved.append((path, payload)),
    )

    assert worker._persist_rps_snapshot("20260420", 305) is False
    assert saved == []
    assert engine.bundle is None


def test_ready_pool_rebuild_defers_after_heavy_fallback_quote_refresh(monkeypatch):
    now = dt.datetime(2026, 6, 24, 14, 37, 55).timestamp()
    ended_at = dt.datetime.fromtimestamp(now - 10).strftime("%Y-%m-%dT%H:%M:%S")
    stats = {
        "recent_requested_count": 180,
        "recent_pending_count": 180,
        "recent_cache_hit_count": 0,
        "recent_elapsed_ms": 42558.0,
        "recent_source_layers": ["sina"],
        "recent_status": "network_ok",
        "recent_ended_at": ended_at,
    }
    provider = _QuotePressureProvider(stats)
    worker = RtScanWorker(provider, _DummyEngine())
    worker._ready_pool = {"000001": {"box_high": 10}}
    worker._all_data = {}
    worker._rps120 = {}
    worker._rps250 = {}
    worker._scan_count = 141
    called = []

    monkeypatch.setattr(
        "ui.workers.rt_scan_worker.precompute_ready_pool",
        lambda *args, **kwargs: called.append((args, kwargs)) or {"000002": {}},
    )

    worker._refresh_ready_pool_if_needed(_FakeTime(now))

    assert called == []
    assert worker._pool_rebuild_pending is True
    assert worker._ready_pool == {"000001": {"box_high": 10}}

    provider.stats = {
        **stats,
        "recent_ended_at": dt.datetime.fromtimestamp(now - 120).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    worker._refresh_ready_pool_if_needed(_FakeTime(now))

    assert len(called) == 1
    assert worker._pool_rebuild_pending is False
    assert worker._ready_pool == {"000002": {}}
