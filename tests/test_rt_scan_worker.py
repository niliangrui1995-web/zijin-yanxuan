# -*- coding: utf-8 -*-
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
    pass


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
