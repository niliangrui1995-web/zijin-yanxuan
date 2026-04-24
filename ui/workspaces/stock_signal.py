# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class StockSignal:
    """Normalized stock-centered signal emitted by workspace tabs."""

    code: str
    source_tab: str
    signal_type: str
    summary: str
    name: str = ""
    source_label: str = ""
    numeric_value: float | None = None
    observed_at: str = ""
    refreshed_at: str = ""
    freshness: str = ""
    row_ref: int | str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def normalized_code(self) -> str:
        return str(self.code or "").strip()


def coerce_stock_signal(value) -> StockSignal | None:
    if isinstance(value, StockSignal):
        return value if value.normalized_code() else None
    if not isinstance(value, Mapping):
        return None

    code = str(value.get("code") or value.get("代码") or "").strip()
    if not code:
        return None

    numeric_value = value.get("numeric_value")
    if numeric_value is not None:
        try:
            numeric_value = float(numeric_value)
        except (TypeError, ValueError):
            numeric_value = None

    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        metadata_keys = {
            "code",
            "代码",
            "name",
            "名称",
            "source_tab",
            "source_label",
            "signal_type",
            "summary",
            "numeric_value",
            "observed_at",
            "refreshed_at",
            "freshness",
            "row_ref",
            "payload",
        }
        payload = {
            key: val
            for key, val in value.items()
            if key not in metadata_keys
        }

    return StockSignal(
        code=code,
        name=str(value.get("name") or value.get("名称") or ""),
        source_tab=str(value.get("source_tab") or ""),
        source_label=str(value.get("source_label") or ""),
        signal_type=str(value.get("signal_type") or ""),
        summary=str(value.get("summary") or ""),
        numeric_value=numeric_value,
        observed_at=str(value.get("observed_at") or ""),
        refreshed_at=str(value.get("refreshed_at") or ""),
        freshness=str(value.get("freshness") or ""),
        row_ref=value.get("row_ref"),
        payload=dict(payload),
    )
