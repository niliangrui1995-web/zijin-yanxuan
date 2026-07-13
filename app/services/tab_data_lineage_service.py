from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from domains.runtime.fault_tolerance import provider_fault_tolerance


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _stable_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = [dict(row) for row in rows if isinstance(row, Mapping)]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TabDataLineage:
    key: str
    view: str
    source: str
    provider: str
    cache_refs: tuple[str, ...]
    trade_date: str = ""
    updated_at: str = ""
    row_count: int = 0
    triggered_network: bool = False
    fallback_or_degraded: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provider_fault_tolerance: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "view": self.view,
            "source": self.source,
            "provider": self.provider,
            "cache_refs": list(self.cache_refs),
            "trade_date": self.trade_date,
            "updated_at": self.updated_at,
            "last_updated": self.updated_at,
            "row_count": self.row_count,
            "triggered_network": self.triggered_network,
            "fallback_or_degraded": self.fallback_or_degraded,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "provider_fault_tolerance": dict(self.provider_fault_tolerance or {}),
        }
        payload.update(dict(self.extra or {}))
        return payload


@dataclass(frozen=True)
class TabDataResult:
    rows: list[dict]
    lineage: TabDataLineage
    signature: str

    def as_dict(self) -> dict[str, Any]:
        payload = self.lineage.as_dict()
        payload["rows"] = self.rows
        payload["signature"] = self.signature
        return payload


class TabDataLineageService:
    def __init__(
        self,
        *,
        key: str,
        source: str,
        provider: str,
        cache_refs: Sequence[str],
        provider_status_reader: Callable[[], Mapping[str, Any] | None] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._key = str(key)
        self._source = str(source)
        self._provider = str(provider)
        self._cache_refs = tuple(str(item) for item in cache_refs)
        self._provider_status_reader = provider_status_reader
        self._clock = clock or _now_iso

    def describe(
        self,
        rows: Sequence[Mapping[str, Any]] | None,
        *,
        trade_date: str = "",
        updated_at: str = "",
        triggered_network: bool | None = None,
        errors: Sequence[str] | None = None,
        warnings: Sequence[str] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> TabDataResult:
        row_list = [dict(row) for row in rows or [] if isinstance(row, Mapping)]
        error_list = [_text(item) for item in errors or [] if _text(item)]
        warning_list = [_text(item) for item in warnings or [] if _text(item)]

        provider_status: Mapping[str, Any] = {}
        if self._provider_status_reader is not None:
            try:
                provider_status = self._provider_status_reader() or {}
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                error_list.append(f"provider_status_failed:{exc.__class__.__name__}")

        fault = provider_fault_tolerance(provider_status)
        recent_triggered_network = bool(fault.get("recent_triggered_network"))
        effective_triggered_network = recent_triggered_network if triggered_network is None else bool(triggered_network)
        lineage = TabDataLineage(
            key=self._key,
            view=self._key,
            source=self._source,
            provider=self._provider,
            cache_refs=self._cache_refs,
            trade_date=_text(trade_date),
            updated_at=_text(updated_at) or self._clock(),
            row_count=len(row_list),
            triggered_network=effective_triggered_network,
            fallback_or_degraded=bool(fault.get("fallback_or_degraded") or error_list),
            errors=tuple(error_list),
            warnings=tuple(warning_list),
            provider_fault_tolerance=fault,
            extra=dict(extra or {}),
        )
        return TabDataResult(
            rows=row_list,
            lineage=lineage,
            signature=_stable_signature(row_list),
        )

    def empty_lineage(self, *, row_count: int = 0, warnings: Sequence[str] | None = None) -> TabDataLineage:
        result = self.describe([], warnings=warnings)
        return replace(
            result.lineage,
            row_count=max(0, int(row_count or 0)),
            triggered_network=False,
        )
