# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "tmp" / "cold_import_budget.json"
PROBE_SAMPLE_COUNT = 3
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class ImportBudget:
    max_elapsed_ms: float
    max_rss_delta_mb: float
    requires_qapplication: bool = False


TARGET_BUDGETS = {
    "app.services": ImportBudget(max_elapsed_ms=500.0, max_rss_delta_mb=32.0),
    "infra.market_data": ImportBudget(max_elapsed_ms=500.0, max_rss_delta_mb=32.0),
    "ui.main_window_qt": ImportBudget(
        max_elapsed_ms=1000.0,
        max_rss_delta_mb=48.0,
        requires_qapplication=True,
    ),
}


def _rss_mb() -> float:
    return float(psutil.Process(os.getpid()).memory_info().rss) / 1024.0 / 1024.0


def _run_child_probe(target: str, *, requires_qapplication: bool) -> dict:
    app = None
    if requires_qapplication:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtCore import QCoreApplication, Qt
        from PyQt6.QtWidgets import QApplication

        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication.instance() or QApplication([])

    rss_before_mb = _rss_mb()
    started_at = time.perf_counter()
    importlib.import_module(target)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    rss_after_mb = _rss_mb()
    return {
        "target": target,
        "elapsed_ms": round(elapsed_ms, 3),
        "rss_before_mb": round(rss_before_mb, 3),
        "rss_after_mb": round(rss_after_mb, 3),
        "rss_delta_mb": round(max(0.0, rss_after_mb - rss_before_mb), 3),
        "qapplication_created": app is not None,
    }


def _isolated_env(temp_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "QT_QPA_PLATFORM": env.get("QT_QPA_PLATFORM") or "offscreen",
            "VCP_HUNTER_DB_PATH": str(temp_root / "vcp_hunter_probe.db"),
            "VCP_HUNTER_LOG_DIR": str(temp_root / "logs"),
            "VCP_HUNTER_TEST_QSETTINGS_DIR": str(temp_root / "settings"),
            "VCP_HUNTER_SETTINGS_ORGANIZATION": "VCPHunterColdImportProbe",
            "VCP_HUNTER_SETTINGS_APPLICATION": "ColdImportProbe",
        }
    )
    return env


def _parse_child_measurement(stdout: str) -> dict:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("target"):
            return payload
    raise ValueError("cold import child did not emit a measurement")


def probe_target(target: str, budget: ImportBudget) -> dict:
    samples = []
    for sample_number in range(1, PROBE_SAMPLE_COUNT + 1):
        with tempfile.TemporaryDirectory(prefix="vcp_hunter_cold_import_") as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--probe",
                    target,
                    "--requires-qapplication"
                    if budget.requires_qapplication
                    else "--no-requires-qapplication",
                ],
                cwd=REPO_ROOT,
                env=_isolated_env(Path(temp_dir)),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
            )
            if completed.returncode != 0:
                detail = str(completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(
                    f"cold import child failed for {target} sample {sample_number}/{PROBE_SAMPLE_COUNT}: {detail}"
                )
        sample = _parse_child_measurement(completed.stdout)
        sample["sample_number"] = sample_number
        samples.append(sample)

    return aggregate_samples(target, samples)


def aggregate_samples(target: str, samples: list[dict]) -> dict:
    return {
        "target": target,
        "sample_count": len(samples),
        "aggregation": "median",
        "elapsed_ms": round(float(median(float(sample["elapsed_ms"]) for sample in samples)), 3),
        "rss_delta_mb": round(float(median(float(sample["rss_delta_mb"]) for sample in samples)), 3),
        "qapplication_created": all(bool(sample.get("qapplication_created")) for sample in samples),
        "samples": samples,
    }


def evaluate_measurement(measurement: dict, budget: ImportBudget) -> list[dict]:
    failures = []
    elapsed_ms = float(measurement.get("elapsed_ms") or 0.0)
    rss_delta_mb = float(measurement.get("rss_delta_mb") or 0.0)
    if elapsed_ms > budget.max_elapsed_ms:
        failures.append(
            {
                "metric": "elapsed_ms",
                "actual": elapsed_ms,
                "budget": budget.max_elapsed_ms,
            }
        )
    if rss_delta_mb > budget.max_rss_delta_mb:
        failures.append(
            {
                "metric": "rss_delta_mb",
                "actual": rss_delta_mb,
                "budget": budget.max_rss_delta_mb,
            }
        )
    return failures


def build_report(targets: list[str]) -> dict:
    measurements = []
    failures = []
    for target in targets:
        budget = TARGET_BUDGETS[target]
        measurement = probe_target(target, budget)
        measurement["budget"] = asdict(budget)
        measurements.append(measurement)
        for failure in evaluate_measurement(measurement, budget):
            failures.append({"target": target, **failure})
    return {
        "status": "fail" if failures else "ok",
        "measurements": measurements,
        "failure_count": len(failures),
        "failures": failures,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure cold Python imports in isolated child processes.")
    parser.add_argument("--target", action="append", choices=tuple(TARGET_BUDGETS), dest="targets")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--probe", choices=tuple(TARGET_BUDGETS), default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--requires-qapplication",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.probe:
        print(
            json.dumps(
                _run_child_probe(args.probe, requires_qapplication=bool(args.requires_qapplication)),
                ensure_ascii=False,
            )
        )
        return 0

    report = build_report(list(args.targets or TARGET_BUDGETS))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
