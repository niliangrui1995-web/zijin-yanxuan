from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "gbbq_single_max_rss_delta_mb": 8.0,
    "gbbq_single_max_elapsed_ms": 1500.0,
    "gbbq_full_max_rss_delta_mb": 130.0,
    "gbbq_full_max_elapsed_ms": 10000.0,
    "tab_cycle_max_rss_delta_mb": 24.0,
    "kline_max_rss_delta_mb": 140.0,
    "kline_max_final_webengine_children": 0,
    "soak_max_tail_range_mb": 24.0,
}


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _snapshot_webengine_children(snapshot: dict | None) -> int:
    return _as_int((snapshot or {}).get("webengine_child_count"), default=0)


def _last_snapshot(report: dict) -> dict:
    snapshots = report.get("snapshots") or report.get("samples") or []
    if not isinstance(snapshots, list):
        return {}
    return snapshots[-1] if snapshots else {}


def _fail(failures: list[dict], check: str, detail: str, **values) -> None:
    failures.append({"check": check, "detail": detail, **values})


def check_gbbq_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    samples = ((report.get("gbbq_profile") or {}).get("samples") or {})
    single = samples.get("single_code")
    full = samples.get("full")

    if single is None and full is None:
        _fail(failures, "gbbq.present", "gbbq_profile has no single_code or full sample")
        return failures

    if single is not None:
        result = single.get("result") or {}
        if result.get("full_loaded") is not False:
            _fail(failures, "gbbq.single.lazy", "single-code gbbq load materialized the full cache")
        if _as_int(result.get("codes")) > 1:
            _fail(failures, "gbbq.single.codes", "single-code gbbq load returned more than one code")
        if _as_float(single.get("rss_delta_mb")) > budget["gbbq_single_max_rss_delta_mb"]:
            _fail(
                failures,
                "gbbq.single.rss_delta",
                "single-code gbbq RSS delta exceeded budget",
                actual=single.get("rss_delta_mb"),
                budget=budget["gbbq_single_max_rss_delta_mb"],
            )
        if _as_float(single.get("elapsed_ms")) > budget["gbbq_single_max_elapsed_ms"]:
            _fail(
                failures,
                "gbbq.single.elapsed",
                "single-code gbbq elapsed time exceeded budget",
                actual=single.get("elapsed_ms"),
                budget=budget["gbbq_single_max_elapsed_ms"],
            )

    if full is not None:
        result = full.get("result") or {}
        if result.get("full_loaded") is not True:
            _fail(failures, "gbbq.full.loaded", "full gbbq run did not materialize the full cache")
        if _as_float(full.get("rss_delta_mb")) > budget["gbbq_full_max_rss_delta_mb"]:
            _fail(
                failures,
                "gbbq.full.rss_delta",
                "full gbbq RSS delta exceeded budget",
                actual=full.get("rss_delta_mb"),
                budget=budget["gbbq_full_max_rss_delta_mb"],
            )
        if _as_float(full.get("elapsed_ms")) > budget["gbbq_full_max_elapsed_ms"]:
            _fail(
                failures,
                "gbbq.full.elapsed",
                "full gbbq elapsed time exceeded budget",
                actual=full.get("elapsed_ms"),
                budget=budget["gbbq_full_max_elapsed_ms"],
            )

    return failures


def check_tab_cycle_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    sample = (report.get("samples") or {}).get("tab_cycles")
    if sample is None:
        _fail(failures, "tab_cycle.present", "tab cycle sample is missing")
        return failures

    if _as_float(sample.get("rss_delta_mb")) > budget["tab_cycle_max_rss_delta_mb"]:
        _fail(
            failures,
            "tab_cycle.rss_delta",
            "tab cycle RSS delta exceeded budget",
            actual=sample.get("rss_delta_mb"),
            budget=budget["tab_cycle_max_rss_delta_mb"],
        )
    return failures


def check_kline_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    sample = (report.get("samples") or {}).get("kline_cycles")
    if sample is None:
        _fail(failures, "kline.present", "K-line cycle sample is missing")
        return failures

    result = sample.get("result") or {}
    cycles = _as_int(result.get("cycles"))
    opened = _as_int(result.get("opened"))
    closed = _as_int(result.get("closed"))
    blocked = _as_int(result.get("blocked"))
    if cycles <= 0 or opened != cycles or closed != cycles or blocked != 0:
        _fail(
            failures,
            "kline.cycles",
            "K-line cycles did not open and close cleanly",
            cycles=cycles,
            opened=opened,
            closed=closed,
            blocked=blocked,
        )

    if _as_float(sample.get("rss_delta_mb")) > budget["kline_max_rss_delta_mb"]:
        _fail(
            failures,
            "kline.rss_delta",
            "K-line RSS delta exceeded budget",
            actual=sample.get("rss_delta_mb"),
            budget=budget["kline_max_rss_delta_mb"],
        )

    end_children = _snapshot_webengine_children(_last_snapshot(report))
    if end_children > budget["kline_max_final_webengine_children"]:
        _fail(
            failures,
            "kline.webengine_children.final",
            "QtWebEngine child processes remained after probe end",
            actual=end_children,
            budget=budget["kline_max_final_webengine_children"],
        )

    for cycle_sample in result.get("cycle_samples") or []:
        label = str(cycle_sample.get("label") or "")
        if label.endswith(":after_close"):
            children = _snapshot_webengine_children(cycle_sample)
            if children > budget["kline_max_final_webengine_children"]:
                _fail(
                    failures,
                    "kline.webengine_children.after_close",
                    "QtWebEngine child process remained after a close sample",
                    label=label,
                    actual=children,
                    budget=budget["kline_max_final_webengine_children"],
                )
    return failures


def check_soak_budget(report: dict, thresholds: dict | None = None) -> list[dict]:
    budget = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict] = []
    trend = report.get("trend") or {}
    if trend.get("growth_basis") != "stable_close_samples":
        _fail(
            failures,
            "soak.growth_basis",
            "soak trend is not based on stable close samples",
            actual=trend.get("growth_basis"),
        )

    for key in ("rss", "private"):
        item = trend.get(key) or {}
        if item.get("status") != "ok":
            _fail(failures, f"soak.{key}.status", f"soak {key} trend is not ok", actual=item.get("status"))
        if _as_float(item.get("tail_range")) > budget["soak_max_tail_range_mb"]:
            _fail(
                failures,
                f"soak.{key}.tail_range",
                f"soak {key} tail range exceeded budget",
                actual=item.get("tail_range"),
                budget=budget["soak_max_tail_range_mb"],
            )

    end_children = _snapshot_webengine_children(_last_snapshot(report))
    if end_children > budget["kline_max_final_webengine_children"]:
        _fail(
            failures,
            "soak.webengine_children.final",
            "QtWebEngine child processes remained after soak close sample",
            actual=end_children,
            budget=budget["kline_max_final_webengine_children"],
        )
    return failures


def run_budget_checks(args: argparse.Namespace) -> dict:
    thresholds = {
        "gbbq_single_max_rss_delta_mb": args.gbbq_single_max_rss_delta_mb,
        "gbbq_single_max_elapsed_ms": args.gbbq_single_max_elapsed_ms,
        "gbbq_full_max_rss_delta_mb": args.gbbq_full_max_rss_delta_mb,
        "gbbq_full_max_elapsed_ms": args.gbbq_full_max_elapsed_ms,
        "tab_cycle_max_rss_delta_mb": args.tab_cycle_max_rss_delta_mb,
        "kline_max_rss_delta_mb": args.kline_max_rss_delta_mb,
        "kline_max_final_webengine_children": args.kline_max_final_webengine_children,
        "soak_max_tail_range_mb": args.soak_max_tail_range_mb,
    }
    checks: list[dict] = []

    for label, path, checker in (
        ("gbbq", args.gbbq_report, check_gbbq_budget),
        ("tab_cycle", args.tab_report, check_tab_cycle_budget),
        ("kline", args.kline_report, check_kline_budget),
        ("soak", args.soak_report, check_soak_budget),
    ):
        if not path:
            continue
        failures = checker(_read_json(path), thresholds)
        checks.append({
            "label": label,
            "path": str(path),
            "status": "fail" if failures else "ok",
            "failures": failures,
        })

    status = "fail" if any(check["failures"] for check in checks) else "ok"
    return {
        "status": status,
        "thresholds": thresholds,
        "checks": checks,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate repeatable performance probe reports against budgets.")
    parser.add_argument("--gbbq-report", type=Path, default=None)
    parser.add_argument("--tab-report", type=Path, default=None)
    parser.add_argument("--kline-report", type=Path, default=None)
    parser.add_argument("--soak-report", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--gbbq-single-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["gbbq_single_max_rss_delta_mb"])
    parser.add_argument("--gbbq-single-max-elapsed-ms", type=float, default=DEFAULT_THRESHOLDS["gbbq_single_max_elapsed_ms"])
    parser.add_argument("--gbbq-full-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["gbbq_full_max_rss_delta_mb"])
    parser.add_argument("--gbbq-full-max-elapsed-ms", type=float, default=DEFAULT_THRESHOLDS["gbbq_full_max_elapsed_ms"])
    parser.add_argument("--tab-cycle-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["tab_cycle_max_rss_delta_mb"])
    parser.add_argument("--kline-max-rss-delta-mb", type=float, default=DEFAULT_THRESHOLDS["kline_max_rss_delta_mb"])
    parser.add_argument(
        "--kline-max-final-webengine-children",
        type=int,
        default=DEFAULT_THRESHOLDS["kline_max_final_webengine_children"],
    )
    parser.add_argument("--soak-max-tail-range-mb", type=float, default=DEFAULT_THRESHOLDS["soak_max_tail_range_mb"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_budget_checks(args)
    if report.get("checks"):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("No performance reports were provided.")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
