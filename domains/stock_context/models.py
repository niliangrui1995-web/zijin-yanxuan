"""Qt-free value objects for stock-centred context queries."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

SIGNAL_METADATA_KEYS = frozenset(
    {
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
)


def _normalized_text_set(values: Sequence[str] | set[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip())


def _numeric_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value if value is not None else "")


def _first_mapping_text(value: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        text = _text(value.get(key)).strip()
        if text:
            return text
    return ""


def _signal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = value.get("payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    return {key: item for key, item in value.items() if key not in SIGNAL_METADATA_KEYS}


def _freeze_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_plain(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_plain(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_plain(item) for item in value)
    return value


def _thaw_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_plain(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw_plain(item) for item in value}
    return value


def _freeze_source_rows(value: Mapping[str, Sequence[Mapping[str, Any]]]) -> Mapping[str, tuple[Mapping, ...]]:
    return MappingProxyType(
        {
            str(source): tuple(_freeze_plain(dict(row)) for row in rows)
            for source, rows in value.items()
        }
    )


def _freeze_signal(signal: StockSignal) -> StockSignal:
    return replace(signal, payload=_freeze_plain(dict(signal.payload or {})))


@dataclass(frozen=True)
class StockSignal:
    """Normalized signal emitted by one stock-context source."""

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


def _indexed_signal(raw_signal: Any, code: str) -> StockSignal | None:
    signal = coerce_stock_signal(raw_signal)
    if signal is None or signal.normalized_code() != code:
        return None
    return replace(
        signal,
        code=code,
        payload=_freeze_plain(dict(signal.payload or {})),
    )


def _indexed_signal_entry(raw_code: Any, raw_signals: Sequence[Any]) -> tuple[str, tuple[StockSignal, ...]] | None:
    code = str(raw_code or "").strip()
    if not code:
        return None
    signals: list[StockSignal] = []
    for raw_signal in raw_signals or ():
        signal = _indexed_signal(raw_signal, code)
        if signal is not None:
            signals.append(signal)
    return (code, tuple(signals)) if signals else None


@dataclass(frozen=True)
class StockContextSignalIndex:
    """Atomically publishable, immutable O(1) stock-signal lookup."""

    by_code: Mapping[str, tuple[StockSignal, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        frozen: dict[str, tuple[StockSignal, ...]] = {}
        for raw_code, raw_signals in (self.by_code or {}).items():
            entry = _indexed_signal_entry(raw_code, raw_signals)
            if entry is not None:
                code, signals = entry
                frozen[code] = signals
        object.__setattr__(self, "by_code", MappingProxyType(dict(sorted(frozen.items()))))

    @classmethod
    def from_context(cls, context: Mapping[str, Sequence[Any]] | None) -> StockContextSignalIndex:
        return cls(
            {
                str(code or "").strip(): tuple(signals or ())
                for code, signals in (context or {}).items()
                if str(code or "").strip()
                and isinstance(signals, Sequence)
                and not isinstance(signals, (str, bytes, bytearray))
            }
        )

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(self.by_code)

    @property
    def signal_count(self) -> int:
        return sum(len(signals) for signals in self.by_code.values())

    def signals_for(self, code: str) -> tuple[StockSignal, ...]:
        return self.by_code.get(str(code or "").strip(), ())


@dataclass(frozen=True)
class StockContextSnapshot:
    """Plain-data snapshot captured before work leaves the GUI thread.

    ``source_rows`` only contains rows copied from loaded widgets.  Cached
    background snapshots (currently fund holdings and LHB) live separately so
    the query layer can preserve the rule that a non-empty loaded source wins
    over its whole cache source.
    """

    source_rows: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    cached_source_rows: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    available_sources: frozenset[str] = field(default_factory=frozenset)
    loading_sources: frozenset[str] = field(default_factory=frozenset)
    direct_source_keys: frozenset[str] = field(default_factory=frozenset)
    direct_signals: tuple[StockSignal, ...] = ()
    foreign_keywords: tuple[str, ...] = ()
    tab_titles: Mapping[str, str] = field(default_factory=dict)
    rps_bundle: Any = None
    source_row_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        frozen_source_rows = _freeze_source_rows(self.source_rows)
        source_row_counts: dict[str, int] = {}
        for raw_source, raw_count in (self.source_row_counts or {}).items():
            source = str(raw_source or "").strip()
            if not source:
                continue
            try:
                source_row_counts[source] = max(0, int(raw_count))
            except (TypeError, ValueError):
                continue
        for source, rows in frozen_source_rows.items():
            source_row_counts.setdefault(source, len(rows))
        object.__setattr__(self, "source_rows", frozen_source_rows)
        object.__setattr__(self, "cached_source_rows", _freeze_source_rows(self.cached_source_rows))
        object.__setattr__(self, "available_sources", frozenset(self.available_sources))
        object.__setattr__(self, "loading_sources", frozenset(self.loading_sources))
        object.__setattr__(self, "direct_source_keys", frozenset(self.direct_source_keys))
        object.__setattr__(self, "direct_signals", tuple(_freeze_signal(signal) for signal in self.direct_signals))
        object.__setattr__(self, "foreign_keywords", tuple(self.foreign_keywords))
        object.__setattr__(self, "tab_titles", _freeze_plain(dict(self.tab_titles)))
        object.__setattr__(self, "rps_bundle", _freeze_plain(self.rps_bundle))
        object.__setattr__(self, "source_row_counts", MappingProxyType(source_row_counts))

    def rows_for(self, source: str) -> list[dict[str, Any]]:
        return [_thaw_plain(row) for row in self.source_rows.get(str(source or ""), ())]

    def cached_rows_for(self, source: str) -> list[dict[str, Any]]:
        return [_thaw_plain(row) for row in self.cached_source_rows.get(str(source or ""), ())]


@dataclass(frozen=True)
class StockContextReadPolicy:
    """Explicit query scope; ``None`` sources means the complete general set."""

    include_cache_fallback: bool = True
    include_source_cache_fallback: bool | None = None
    allow_lhb_cache_compute: bool = False
    allow_fund_store_query: bool = True
    target_codes: frozenset[str] | None = None
    sources: frozenset[str] | None = None

    @property
    def source_cache_fallback(self) -> bool:
        if self.include_source_cache_fallback is None:
            return bool(self.include_cache_fallback)
        return bool(self.include_source_cache_fallback)

    def includes_source(self, source: str) -> bool:
        return self.sources is None or source in self.sources

    @classmethod
    def build(
        cls,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_fund_store_query: bool = True,
        target_codes: Sequence[str] | set[str] | None = None,
        sources: Sequence[str] | set[str] | None = None,
    ) -> StockContextReadPolicy:
        return cls(
            include_cache_fallback=bool(include_cache_fallback),
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=bool(allow_lhb_cache_compute),
            allow_fund_store_query=bool(allow_fund_store_query),
            target_codes=_normalized_text_set(target_codes),
            sources=_normalized_text_set(sources),
        )


def coerce_stock_signal(value: Any) -> StockSignal | None:
    if isinstance(value, StockSignal):
        return value if value.normalized_code() else None
    if not isinstance(value, Mapping):
        return None

    code = _first_mapping_text(value, ("code", "代码"))
    if not code:
        return None

    return StockSignal(
        code=code,
        name=_first_mapping_text(value, ("name", "名称")),
        source_tab=_text(value.get("source_tab")),
        source_label=_text(value.get("source_label")),
        signal_type=_text(value.get("signal_type")),
        summary=_text(value.get("summary")),
        numeric_value=_numeric_or_none(value.get("numeric_value")),
        observed_at=_text(value.get("observed_at")),
        refreshed_at=_text(value.get("refreshed_at")),
        freshness=_text(value.get("freshness")),
        row_ref=value.get("row_ref"),
        payload=_signal_payload(value),
    )
