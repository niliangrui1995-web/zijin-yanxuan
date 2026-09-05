from __future__ import annotations

import json

from scripts.native_lhb_restore_profile import _acceptance, _log_evidence, _parse_args, _summarize_paints


def test_lhb_log_evidence_uses_structured_metric_once_and_exact_time_window(tmp_path):
    payload = {"event": "metric.recorded", "fields": {"metric": "lhb_table_paint_ms", "value": 400.3}}
    path = tmp_path / "vcp.log"
    path.write_text(
        "2026-09-05 03:08:25,999 [DEBUG] [structured] " + json.dumps(payload) + "\n"
        "2026-09-05 03:08:26,772 [WARNING] VCPTableView.paintEvent elapsed_ms=400.3\n"
        "2026-09-05 03:08:26,773 [DEBUG] [structured] " + json.dumps(payload) + "\n"
        "2026-09-05 03:08:30,000 [DEBUG] [structured] " + json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    result = _log_evidence(path, end="2026-09-05 03:08:30")

    assert len(result["metrics"]["lhb_table_paint_ms"]) == 1
    assert result["metrics"]["lhb_table_paint_ms"][0]["line"] == 3
    assert len(result["sha256"]) == 64


def test_lhb_native_summary_uses_actual_region_coverage_not_its_bounding_box():
    paints = [
        {"phase": "quote", "elapsed_ms": 12, "full_viewport": False, "rows": 42, "bounds": [0, 0, 1800, 900]},
        {"phase": "restore", "elapsed_ms": 100, "full_viewport": True, "rows": 42},
    ]

    result = _summarize_paints(paints)

    assert result["quote"]["full_viewport_count"] == 0
    assert result["quote"]["local_region_count"] == 1
    assert result["restore"]["full_viewport_count"] == 1


def _successful_native_report():
    return {
        "actual_paints": [{"visible": True, "full_viewport": True, "rows": 42}],
        "final_row_count": 42,
        "background_prewarm": {"finished": True, "loaded_count": 11, "failures": {}},
        "actions": [{"active_flash_cells": 126}],
        "metrics": {"lhb_table_paint_ms": [{"tags": {"reason": "flash_expiry"}}]},
        "final_sorted_prices": list(reversed(range(42))),
    }


def test_lhb_native_acceptance_preserves_first_frame_flash_sort_and_prewarm():
    assert _acceptance(_successful_native_report(), 42)["passed"]


def test_lhb_native_acceptance_rejects_suppressed_first_frame_despite_no_stalls():
    report = _successful_native_report()
    report["actual_paints"] = []

    assert "missing complete visible first frame" in _acceptance(report, 42)["violations"]


def test_lhb_native_acceptance_rejects_missing_flash_expiry_and_incomplete_prewarm():
    report = _successful_native_report()
    report["metrics"] = {}
    report["background_prewarm"]["loaded_count"] = 10

    result = _acceptance(report, 42)

    assert not result["passed"]
    assert "flash expiry paint not delivered" in result["violations"]
    assert "11-tab background prewarm incomplete" in result["violations"]


def test_lhb_native_cli_requires_explicit_output_and_defaults_to_42_real_rows(tmp_path):
    args = _parse_args(["--output-dir", str(tmp_path)])

    assert args.expected_rows == 42
    assert args.timeout_ms == 90000
    assert args.phase_ms == 1200
