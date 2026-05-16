from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from domains.runtime.fault_tolerance import provider_fault_tolerance

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_RECENT_TIME = "\u6700\u8fd1\u65f6\u95f4"
KEY_TRADE_DATE = "\u4ea4\u6613\u65e5"
KEY_TRIGGER_DATE = "\u89e6\u53d1\u65e5\u671f"

DEFAULT_CACHE_REFS = (
    "workspace.collect_stock_context",
    "global_store.quotes",
    "DataStore.scan_cache",
    "fund_holdings_store",
)


def _utc_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _attr(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _signal_payload(signal: Any) -> Mapping[str, Any]:
    payload = _attr(signal, "payload", {})
    return payload if isinstance(payload, Mapping) else {}


def _signal_source_tab(signal: Any) -> str:
    return _text(_attr(signal, "source_tab", ""))


def _signal_trade_date(signal: Any) -> str:
    for value in (
        _attr(signal, "observed_at", ""),
        _attr(signal, "refreshed_at", ""),
    ):
        text = _text(value)
        if text:
            return text

    payload = _signal_payload(signal)
    for key in (
        "trade_date",
        "date",
        KEY_TRADE_DATE,
        KEY_TRIGGER_DATE,
        KEY_RECENT_TIME,
    ):
        text = _text(payload.get(key))
        if text:
            return text
    return ""


def _iter_signals(context: Mapping[str, Sequence[Any]] | None) -> list[Any]:
    signals: list[Any] = []
    if not isinstance(context, Mapping):
        return signals
    for values in context.values():
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            continue
        signals.extend(values)
    return signals


def _canonical_signal(signal: Any) -> dict[str, Any]:
    payload = _signal_payload(signal)
    return {
        "code": _text(_attr(signal, "code", "")),
        "source_tab": _signal_source_tab(signal),
        "signal_type": _text(_attr(signal, "signal_type", "")),
        "summary": _text(_attr(signal, "summary", "")),
        "observed_at": _text(_attr(signal, "observed_at", "")),
        "refreshed_at": _text(_attr(signal, "refreshed_at", "")),
        "payload_keys": sorted(str(key) for key in payload.keys()),
    }


def _canonical_row(row: Mapping[str, Any]) -> dict[str, Any]:
    visible = {
        str(key): value
        for key, value in row.items()
        if str(key) != "_signals"
    }
    signals = [_canonical_signal(signal) for signal in row.get("_signals", []) or []]
    return {
        "visible": visible,
        "signals": signals,
    }


def _stable_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = [_canonical_row(row) for row in rows if isinstance(row, Mapping)]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StockCandidatesDataLineage:
    key: str = "stock_candidates"
    view: str = "stock_candidates"
    source: str = "workspace_stock_context"
    provider: str = "workspace_stock_context"
    cache_refs: tuple[str, ...] = DEFAULT_CACHE_REFS
    trade_date: str = ""
    triggered_network: bool = False
    fallback_or_degraded: bool = False
    updated_at: str = ""
    row_count: int = 0
    signal_count: int = 0
    source_tabs: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provider_fault_tolerance: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "view": self.view,
            "source": self.source,
            "provider": self.provider,
            "cache_refs": list(self.cache_refs),
            "trade_date": self.trade_date,
            "triggered_network": self.triggered_network,
            "fallback_or_degraded": self.fallback_or_degraded,
            "updated_at": self.updated_at,
            "last_updated": self.updated_at,
            "row_count": self.row_count,
            "signal_count": self.signal_count,
            "source_tabs": list(self.source_tabs),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "provider_fault_tolerance": dict(self.provider_fault_tolerance or {}),
        }


@dataclass(frozen=True)
class StockCandidatesResult:
    rows: list[dict]
    lineage: StockCandidatesDataLineage
    signature: str

    def as_dict(self) -> dict[str, Any]:
        payload = self.lineage.as_dict()
        payload["rows"] = self.rows
        payload["signature"] = self.signature
        return payload


class StockCandidatesDataService:
    """Data boundary for the stock_candidates tab.

    The service reads a stock-code context, delegates row shaping to the tab's
    existing row builder, and returns a unified data + lineage envelope. It has
    no PyQt or UI imports, so the tab remains responsible only for scheduling
    and presentation.
    """

    def __init__(
        self,
        *,
        context_reader: Callable[[], Mapping[str, Sequence[Any]] | None],
        row_builder: Callable[[Mapping[str, Sequence[Any]]], list[dict]],
        provider_status_reader: Callable[[], Mapping[str, Any] | None] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._context_reader = context_reader
        self._row_builder = row_builder
        self._provider_status_reader = provider_status_reader
        self._clock = clock or _utc_now_iso

    def load(self) -> StockCandidatesResult:
        errors: list[str] = []
        warnings: list[str] = []
        context: Mapping[str, Sequence[Any]] = {}
        try:
            raw_context = self._context_reader() or {}
            if isinstance(raw_context, Mapping):
                context = raw_context
            else:
                warnings.append("context_reader_returned_non_mapping")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"context_reader_failed:{exc.__class__.__name__}")

        try:
            rows = list(self._row_builder(context) or [])
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            rows = []
            errors.append(f"row_builder_failed:{exc.__class__.__name__}")

        provider_status: Mapping[str, Any] = {}
        if self._provider_status_reader is not None:
            try:
                provider_status = self._provider_status_reader() or {}
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                errors.append(f"provider_status_failed:{exc.__class__.__name__}")

        signals = _iter_signals(context)
        source_tabs = sorted({tab for tab in (_signal_source_tab(signal) for signal in signals) if tab})
        trade_dates = sorted({date for date in (_signal_trade_date(signal) for signal in signals) if date})
        fault_tolerance = provider_fault_tolerance(provider_status)
        if not rows and context:
            warnings.append("no_rows_after_candidate_filter")

        lineage = StockCandidatesDataLineage(
            trade_date=trade_dates[-1] if trade_dates else "",
            triggered_network=bool(fault_tolerance.get("recent_triggered_network")),
            fallback_or_degraded=bool(fault_tolerance.get("fallback_or_degraded") or errors),
            updated_at=self._clock(),
            row_count=len(rows),
            signal_count=len(signals),
            source_tabs=tuple(source_tabs),
            errors=tuple(errors),
            warnings=tuple(warnings),
            provider_fault_tolerance=fault_tolerance,
        )
        return StockCandidatesResult(
            rows=rows,
            lineage=lineage,
            signature=_stable_signature(rows),
        )

    def empty_lineage(self, *, row_count: int = 0) -> StockCandidatesDataLineage:
        return StockCandidatesDataLineage(
            updated_at=self._clock(),
            row_count=max(0, int(row_count or 0)),
        )
