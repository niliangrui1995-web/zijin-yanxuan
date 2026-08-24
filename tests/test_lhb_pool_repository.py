# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import threading
from collections import OrderedDict

import pytest

from infra.storage.lhb_pool_repository import LhbPoolRepository


@pytest.fixture
def isolated_payload_cache(monkeypatch):
    with LhbPoolRepository._loaded_payload_lock:
        previous_cache = OrderedDict(LhbPoolRepository._loaded_payload_cache)
        LhbPoolRepository._loaded_payload_cache.clear()
    monkeypatch.setattr(LhbPoolRepository, "_loaded_payload_cache_max_entries", 8)
    try:
        yield
    finally:
        with LhbPoolRepository._loaded_payload_lock:
            LhbPoolRepository._loaded_payload_cache.clear()
            LhbPoolRepository._loaded_payload_cache.update(previous_cache)


def _write_payload(path, code: str) -> None:
    path.write_text(
        json.dumps(
            {
                "daily_data": {"20260825": [{"code": code, "nested": {"source": "cache"}}]},
                "day_meta": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_loaded_payload_cache_is_lru_and_keeps_hit_payloads_isolated(
    monkeypatch,
    tmp_path,
    isolated_payload_cache,
):
    paths = [tmp_path / f"pool-{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        _write_payload(path, f"00000{index}")
    monkeypatch.setattr(LhbPoolRepository, "_loaded_payload_cache_max_entries", 2)

    first = LhbPoolRepository.load_json_payload(str(paths[0]))
    first["daily_data"]["20260825"][0]["nested"]["source"] = "caller"
    assert LhbPoolRepository.load_json_payload(str(paths[1]))["daily_data"]
    restored = LhbPoolRepository.load_json_payload(str(paths[0]))
    assert restored["daily_data"]["20260825"][0]["nested"]["source"] == "cache"
    assert LhbPoolRepository.load_json_payload(str(paths[2]))["daily_data"]

    with LhbPoolRepository._loaded_payload_lock:
        cached_keys = tuple(LhbPoolRepository._loaded_payload_cache)
    assert LhbPoolRepository._payload_cache_key(str(paths[0])) in cached_keys
    assert LhbPoolRepository._payload_cache_key(str(paths[1])) not in cached_keys
    assert LhbPoolRepository._payload_cache_key(str(paths[2])) in cached_keys


def test_loaded_payload_cache_concurrent_hits_return_independent_snapshots(tmp_path, isolated_payload_cache):
    cache_path = tmp_path / "pool.json"
    _write_payload(cache_path, "000001")
    LhbPoolRepository.load_json_payload(str(cache_path))
    start = threading.Barrier(4)
    failures: list[BaseException] = []

    def read_and_mutate(worker_index: int) -> None:
        try:
            start.wait(timeout=2)
            for _ in range(30):
                payload = LhbPoolRepository.load_json_payload(str(cache_path))
                row = payload["daily_data"]["20260825"][0]
                row["code"] = f"mutated-{worker_index}"
                row["nested"]["source"] = f"worker-{worker_index}"
        except BaseException as exc:  # noqa: BLE001 - worker failures are asserted below.
            failures.append(exc)

    workers = [threading.Thread(target=read_and_mutate, args=(index,)) for index in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    restored = LhbPoolRepository.load_json_payload(str(cache_path))
    row = restored["daily_data"]["20260825"][0]
    assert row == {"code": "000001", "nested": {"source": "cache"}}
