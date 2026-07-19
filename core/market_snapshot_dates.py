# -*- coding: utf-8 -*-
"""Shared trade-date inference for staged market snapshots."""

from __future__ import annotations

from collections import Counter


def normalize_trade_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y%m%d")
        except (AttributeError, TypeError, ValueError):
            return ""
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _frame_latest_trade_date(frame) -> str:
    if frame is None:
        return ""
    try:
        if "datetime" in getattr(frame, "columns", ()):
            return normalize_trade_date(frame["datetime"].max())
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        pass
    try:
        return normalize_trade_date(frame.index.max())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def infer_effective_trade_date(cache_data) -> str:
    dates = (_frame_latest_trade_date(frame) for frame in (cache_data or {}).values())
    counts = Counter(date_text for date_text in dates if date_text)
    if not counts:
        return ""
    highest_count = max(counts.values())
    return max(date_text for date_text, count in counts.items() if count == highest_count)


__all__ = ["infer_effective_trade_date", "normalize_trade_date"]
