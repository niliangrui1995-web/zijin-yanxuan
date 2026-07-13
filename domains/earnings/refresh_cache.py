# -*- coding: utf-8 -*-
"""Process-isolated earnings refresh entrypoint."""

from __future__ import annotations

import argparse
import json

from domains.earnings.engine import EarningsEngine
from domains.market_calendar import MarketCalendar
from domains.market_calendar.calendar_service import normalize_trade_dates as _normalize_trade_dates
from infra.storage.industry_chain_repository import IndustryChainRepository

STARTUP_BACKFILL_TRADE_DAYS = 10


def _create_engine() -> EarningsEngine:
    repository = IndustryChainRepository()
    return EarningsEngine(
        stock_universe_provider=repository.load_cached_stock_codes,
        stock_context_provider=repository.load_cached_context_map,
    )


def _startup_scan_dates(engine: EarningsEngine) -> list[str]:
    recent_trade_dates = _normalize_trade_dates(MarketCalendar.get_recent_trade_dates(STARTUP_BACKFILL_TRADE_DAYS))
    if not recent_trade_dates:
        return []
    if not engine.local_records or not engine.last_sync_date:
        return recent_trade_dates
    return [trade_date for trade_date in recent_trade_dates if trade_date > engine.last_sync_date]


def _degraded_error(engine: EarningsEngine) -> str:
    scan_result = getattr(engine, "last_scan_result", {}) or {}
    if str(scan_result.get("status") or "").strip() != "degraded":
        return ""
    return str(scan_result.get("error") or "earnings scan degraded").strip()


def run_startup_gap_fill(engine: EarningsEngine) -> dict[str, object]:
    cached = len(engine.get_cached_record_rows())
    missing_dates = _startup_scan_dates(engine)
    gap = 0
    errors: list[str] = []
    for target_date in missing_dates:
        frame = engine.fetch_daily_surprises(target_publish_date=target_date)
        gap += len(frame) if frame is not None else 0
        error = _degraded_error(engine)
        if error and error not in errors:
            errors.append(error)

    summary: dict[str, object] = {
        "status": "degraded" if errors else "success",
        "job_key": "earnings_startup_gap_fill",
        "records": int(cached + gap),
        "cached": int(cached),
        "gap": int(gap),
        "missing_dates": missing_dates,
    }
    if errors:
        summary["error"] = "; ".join(errors)
    return summary


def run_routine(engine: EarningsEngine, routine_time: str = "") -> dict[str, object]:
    frame = engine.fetch_daily_surprises()
    error = _degraded_error(engine)
    summary: dict[str, object] = {
        "status": "degraded" if error else "success",
        "job_key": "earnings_routine",
        "records": int(len(frame) if frame is not None else 0),
        "routine_time": str(routine_time or MarketCalendar.now("CN").isoformat(timespec="seconds")),
    }
    if error:
        summary["error"] = error
    return summary


def _normalize_error(error: object, limit: int = 500) -> str:
    text = str(error or "").strip() or error.__class__.__name__
    text = " | ".join(part.strip() for part in text.splitlines() if part.strip()) or text
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh A-share earnings cache in an isolated process.")
    parser.add_argument("mode", choices=("startup-gap-fill", "routine"))
    parser.add_argument("--routine-time", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    job_key = "earnings_startup_gap_fill" if args.mode == "startup-gap-fill" else "earnings_routine"
    try:
        engine = _create_engine()
        if args.mode == "startup-gap-fill":
            summary = run_startup_gap_fill(engine)
        else:
            summary = run_routine(engine, args.routine_time)
        return_code = 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary must always emit a parseable final line.
        summary = {
            "status": "failed",
            "job_key": job_key,
            "records": 0,
            "error": _normalize_error(exc),
        }
        if args.mode == "startup-gap-fill":
            summary.update({"cached": 0, "gap": 0, "missing_dates": []})
        else:
            summary["routine_time"] = str(args.routine_time or "")
        return_code = 1
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
