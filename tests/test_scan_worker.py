# -*- coding: utf-8 -*-
from __future__ import annotations

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
