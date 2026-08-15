# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime

from app.services.earnings_cache_query_service import (
    load_cached_earnings_rows,
    prepare_cached_earnings_rows,
)


def _record(code: str, *, reveal_date: str, qoq: float = 50.0, yoy: float = 20.0) -> dict:
    return {
        "股票代码": code,
        "股票名称": f"股票{code}",
        "单季净利润_新增": 120_000_000.0,
        "单季净利润_上期": 80_000_000.0,
        "环比增速_百分比": qoq,
        "同比增速_百分比": yoy,
        "报告期": "2026-06-30",
        "数据类型": "预告",
        "公告日期": reveal_date,
    }


def test_prepare_cached_earnings_rows_matches_display_cache_rules_without_mutation():
    payload = {
        "records": [
            _record("1", reveal_date="2026-07-15", qoq=55.0),
            _record("000002", reveal_date="2026-07-14", qoq=80.0),
            _record("000003", reveal_date="2026-07-13", qoq=29.9),
            _record("000004", reveal_date="2026-06-01"),
            _record("000005", reveal_date="2026-07-15", yoy=0.0),
        ]
    }
    original = deepcopy(payload)

    rows = prepare_cached_earnings_rows(
        payload,
        updated_at="2026-07-15 16:00:38",
        stock_codes={"000001", "000002", "000003", "000004", "000005"},
        context_map={"000001": "AI PCB", "000002": "光模块"},
        now=datetime(2026, 7, 16, 9, 30),
    )

    assert [row["股票代码"] for row in rows] == ["1", "000002"]
    assert rows[0]["揭晓日"] == "2026-07-15"
    assert rows[0]["发现时间"] == "2026-07-15 16:00:38"
    assert rows[0]["所属行业与概念"] == "AI PCB"
    assert rows[1]["所属行业与概念"] == "光模块"
    assert payload == original


def test_load_cached_earnings_rows_uses_injected_read_only_sources():
    calls = []
    rows = load_cached_earnings_rows(
        state_loader=lambda: calls.append("state") or ({"records": [_record("000001", reveal_date="2026-07-15")]}, ""),
        stock_codes_loader=lambda: calls.append("codes") or {"000001"},
        context_map_loader=lambda: calls.append("context") or {"000001": "先进封装"},
        now=datetime(2026, 7, 16, 9, 30),
    )

    assert calls == ["state", "codes", "context"]
    assert len(rows) == 1
    assert rows[0]["所属行业与概念"] == "先进封装"


def test_lightweight_cache_query_keeps_scan_dataframe_stack_cold():
    script = r'''
import json
import sys
from datetime import datetime
from app.services.ui_earnings_service import EarningsRefreshService
from app.services.earnings_cache_query_service import load_cached_earnings_rows

record = {
    "股票代码": "000001",
    "股票名称": "平安银行",
    "单季净利润_新增": 100.0,
    "单季净利润_上期": 50.0,
    "环比增速_百分比": 50.0,
    "同比增速_百分比": 20.0,
    "报告期": "2026-06-30",
    "数据类型": "预告",
    "公告日期": "2026-07-15",
}
rows = load_cached_earnings_rows(
    state_loader=lambda: ({"records": [record]}, ""),
    stock_codes_loader=lambda: {"000001"},
    context_map_loader=lambda: {},
    now=datetime(2026, 7, 16, 9, 30),
)

class Runner:
    @staticmethod
    def is_active_task(_task_id):
        return False

    @staticmethod
    def run_in_background(fn, **kwargs):
        result = fn()
        on_success = kwargs.get("on_success")
        if on_success is not None:
            on_success(result)
        return str(kwargs.get("task_id") or "")

emitted = []
service = EarningsRefreshService(job_runner=Runner(), cache_rows_loader=lambda: rows)
service.sig_new_surprises_found.connect(lambda payload, mode: emitted.append((len(payload), mode)))
service.load_cached_records_async()
print(json.dumps({
    "emitted": emitted,
    "heavy": [name for name in ("domains.earnings.engine", "pandas", "akshare", "bs4") if name in sys.modules],
}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    json_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    payload = json.loads(json_lines[-1])

    assert payload == {"emitted": [[1, "warm_cache"]], "heavy": []}
