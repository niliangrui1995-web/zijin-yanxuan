# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.complexity_hotspot_audit import HOTSPOT_BUDGETS, build_report, scan_hotspots


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_complexity_hotspot_audit_accepts_functions_within_budget(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def run(self):
        return 1
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 5}}))

    assert report["status"] == "ok"
    assert report["findings"] == []


def test_complexity_hotspot_audit_rejects_functions_over_budget(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def run(self):
        value = 1
        value += 1
        return value
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 3}}))

    assert report["status"] == "fail"
    assert report["finding_count"] == 1
    assert report["findings"][0]["qualname"] == "Worker.run"
    assert report["findings"][0]["line_count"] == 4
    assert report["findings"][0]["budget"] == 3


def test_complexity_hotspot_audit_rejects_missing_hotspot_function(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def other(self):
        return 1
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 5}}))

    assert report["status"] == "fail"
    assert report["findings"][0]["qualname"] == "Worker.run"
    assert report["findings"][0]["line_count"] == 0


def test_default_hotspot_budgets_cover_known_refactor_targets():
    assert HOTSPOT_BUDGETS["ui/kline_chart_payload.py"]["build_kline_html"] == 35
    assert HOTSPOT_BUDGETS["ui/workers/rt_scan_worker.py"]["RtScanWorker._run_one_round"] == 30
    assert HOTSPOT_BUDGETS["ui/workers/scan_worker.py"]["ScanWorker.run"] == 70
