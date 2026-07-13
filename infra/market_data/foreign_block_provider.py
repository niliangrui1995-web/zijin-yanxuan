# -*- coding: utf-8 -*-
"""AkShare subprocess boundary for domestic foreign block-trade data."""

from __future__ import annotations

import json
import sys

from infra.tasks import (
    build_domestic_process_env,
    run_process,
    windows_no_window_kwargs,
)
from infra.tasks.lifecycle import raise_if_cancelled as _raise_if_cancelled

_AKSHARE_FETCH_SNIPPET = r"""
import json
import sys
import pandas as pd
import akshare as ak

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

mode = sys.argv[1]
if mode == "calendar":
    df = ak.tool_trade_date_hist_sina()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    print(json.dumps(df["trade_date"].tolist(), ensure_ascii=False))
elif mode == "block_trade":
    start_date = sys.argv[2]
    end_date = sys.argv[3]
    df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        print("[]")
    else:
        print(df.to_json(orient="records", force_ascii=False, date_format="iso"))
"""


def _bounded_timeout(timeout: float, cancellation_token=None) -> float:
    _raise_if_cancelled(cancellation_token)
    normalized = max(0.1, float(timeout))
    if cancellation_token is None:
        return normalized
    remaining = cancellation_token.remaining_seconds()
    if remaining is None:
        return normalized
    if remaining <= 0:
        cancellation_token.raise_if_cancelled()
    return max(0.1, min(normalized, remaining))


def _run_akshare_process(
    mode: str,
    *args: str,
    timeout: float,
    cancellation_token=None,
) -> list:
    process_timeout = _bounded_timeout(timeout, cancellation_token)
    env = build_domestic_process_env(extra={"PYTHONIOENCODING": "utf-8"})
    command = [sys.executable, "-c", _AKSHARE_FETCH_SNIPPET, mode, *args]
    completed = run_process(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=process_timeout,
        env=env,
        **windows_no_window_kwargs(),
        check=True,
    )
    _raise_if_cancelled(cancellation_token)
    payload = (completed.stdout or "").strip()
    return list(json.loads(payload)) if payload else []


def fetch_trade_calendar(*, timeout: float, cancellation_token=None) -> list[str]:
    return [
        str(value)
        for value in _run_akshare_process(
            "calendar",
            timeout=timeout,
            cancellation_token=cancellation_token,
        )
    ]


def fetch_block_trades(
    start_date: str,
    end_date: str,
    *,
    timeout: float,
    cancellation_token=None,
) -> list[dict]:
    records = _run_akshare_process(
        "block_trade",
        str(start_date),
        str(end_date),
        timeout=timeout,
        cancellation_token=cancellation_token,
    )
    return [dict(record) for record in records if isinstance(record, dict)]


__all__ = ["fetch_block_trades", "fetch_trade_calendar"]
