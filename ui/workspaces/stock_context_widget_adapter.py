"""GUI-thread adapter that copies widget state into a Qt-free snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Mapping

from PyQt6.QtCore import QCoreApplication, QThread

from app.services.stock_context_model_service import (
    DEFAULT_SOURCE_ORDER,
    StockContextSnapshot,
    StockSignal,
    coerce_stock_signal,
)
from app.services.stock_context_query_service import GENERAL_STOCK_CONTEXT_SOURCE_KEYS
from ui.workspaces.tab_capabilities import (
    DataLineageCapability,
    ForeignKeywordCapability,
    StockSignalSourceCapability,
)

SOURCE_KEYS = tuple(
    source_key for source_key in DEFAULT_SOURCE_ORDER if source_key in GENERAL_STOCK_CONTEXT_SOURCE_KEYS
)


@dataclass(frozen=True)
class _CapturedSource:
    key: str
    rows: tuple[dict, ...]
    loading: bool
    direct: bool
    signals: tuple[StockSignal, ...]
    foreign_keywords: tuple[str, ...]


def _plain_copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        if isinstance(value, Mapping):
            return {str(key): _plain_copy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_plain_copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_plain_copy(item) for item in value)
        return value


def _normalized_scope(values: Sequence[str] | set[str] | frozenset[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip())


def _copy_rows(rows) -> list[dict]:
    return [_plain_copy(dict(row)) for row in (rows or []) if isinstance(row, Mapping)]


def _scan_rows(tab) -> list[dict]:
    reader = getattr(tab, "get_scan_results", None)
    return _copy_rows(reader()) if callable(reader) else []


def _public_rows(tab) -> list[dict]:
    radar_reader = getattr(tab, "get_watchlist_radar_rows", None)
    reader = radar_reader if callable(radar_reader) else getattr(tab, "get_row_data", None)
    return _copy_rows(reader()) if callable(reader) else []


def _foreign_keywords(key: str, tab) -> tuple[str, ...]:
    if key != "foreign_block" or not isinstance(tab, ForeignKeywordCapability):
        return ()
    return tuple(str(item) for item in (tab.get_foreign_keywords() or []) if str(item))


def _direct_signals(key: str, tab) -> tuple[StockSignal, ...]:
    if not isinstance(tab, StockSignalSourceCapability):
        return ()
    signals: list[StockSignal] = []
    for raw_signal in list(tab.iter_stock_signals() or []):
        signal = coerce_stock_signal(raw_signal)
        if signal is None:
            continue
        source_signal = signal if signal.source_tab else replace(signal, source_tab=key)
        signals.append(replace(source_signal, payload=_plain_copy(dict(source_signal.payload or {}))))
    return tuple(signals)


def _captured_source(
    key: str,
    tab,
    rows: list[dict],
    loading: bool,
) -> _CapturedSource | None:
    if tab is None:
        return None
    signals = _direct_signals(key, tab)
    return _CapturedSource(
        key=key,
        rows=tuple(rows),
        loading=loading,
        direct=isinstance(tab, StockSignalSourceCapability),
        signals=signals,
        foreign_keywords=_foreign_keywords(key, tab),
    )


def _spec_key(spec: Mapping[str, Any]) -> str:
    return str(spec.get("key") or "").strip()


def _cached_rows_snapshot(
    cached_source_rows,
    selected_sources: frozenset[str] | None = None,
) -> dict[str, tuple[dict, ...]]:
    return {
        str(key): tuple(_copy_rows(rows))
        for key, rows in (cached_source_rows or {}).items()
        if selected_sources is None or str(key) in selected_sources
    }


def _available_source_keys(
    specs,
    sources: list[_CapturedSource],
    selected_sources: frozenset[str] | None = None,
) -> frozenset[str]:
    keys = {
        key
        for spec in specs
        if (key := _spec_key(spec))
        and (selected_sources is None or key in selected_sources)
    }
    keys.discard("")
    keys.update(source.key for source in sources)
    return frozenset(keys)


def _tab_title_map(specs) -> dict[str, str]:
    titles: dict[str, str] = {}
    for spec in specs:
        key = _spec_key(spec)
        if key:
            titles[key] = str(spec.get("title") or "").strip()
    return titles


def _source_rows_map(sources: list[_CapturedSource]) -> dict[str, tuple[dict, ...]]:
    return {source.key: source.rows for source in sources}


def _loading_source_keys(sources: list[_CapturedSource]) -> frozenset[str]:
    return frozenset(source.key for source in sources if source.loading)


def _direct_source_keys(sources: list[_CapturedSource]) -> frozenset[str]:
    return frozenset(source.key for source in sources if source.direct)


def _direct_source_signals(sources: list[_CapturedSource]) -> tuple[StockSignal, ...]:
    return tuple(signal for source in sources for signal in source.signals)


def _first_foreign_keywords(sources: list[_CapturedSource]) -> tuple[str, ...]:
    return next((source.foreign_keywords for source in sources if source.foreign_keywords), ())


def _build_snapshot(
    specs,
    sources,
    cached_source_rows,
    rps_bundle,
    *,
    selected_sources: frozenset[str] | None = None,
) -> StockContextSnapshot:
    return StockContextSnapshot(
        source_rows=_source_rows_map(sources),
        cached_source_rows=_cached_rows_snapshot(
            cached_source_rows,
            selected_sources,
        ),
        available_sources=_available_source_keys(specs, sources, selected_sources),
        loading_sources=_loading_source_keys(sources),
        direct_source_keys=_direct_source_keys(sources),
        direct_signals=_direct_source_signals(sources),
        foreign_keywords=_first_foreign_keywords(sources),
        tab_titles=_tab_title_map(specs),
        rps_bundle=rps_bundle,
    )


class StockContextWidgetSnapshotAdapter:
    """Reads public tab capabilities only while running on the GUI thread."""

    def __init__(self, workspace) -> None:
        self._workspace = workspace

    @staticmethod
    def _assert_gui_thread() -> None:
        application = QCoreApplication.instance()
        if application is not None and QThread.currentThread() is not application.thread():
            raise RuntimeError("stock-context widget snapshots must be captured on the GUI thread")

    def _tab_specs(self) -> list[dict]:
        reader = getattr(self._workspace, "tab_specs", None)
        return [dict(spec) for spec in (reader() or []) if isinstance(spec, Mapping)] if callable(reader) else []

    def _loaded_tab(self, key: str):
        reader = getattr(self._workspace, "get_loaded_tab", None)
        return reader(key) if callable(reader) else None

    @staticmethod
    def _rows(tab, key: str) -> list[dict]:
        if tab is None:
            return []
        if key == "scan":
            rows = _scan_rows(tab)
            if rows:
                return rows
        return _public_rows(tab)

    @staticmethod
    def _is_loading(tab) -> bool:
        if not isinstance(tab, DataLineageCapability):
            return False
        try:
            lineage = tab.get_data_lineage() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        status = str(lineage.get("status") or "").strip().lower() if isinstance(lineage, Mapping) else ""
        return status in {"loading", "syncing"}

    def _rps_bundle(self):
        engine = getattr(self._workspace, "engine", None)
        reader = getattr(engine, "get_precomputed_rps", None)
        if not callable(reader):
            return None
        try:
            return _plain_copy(reader())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def capture(
        self,
        *,
        cached_source_rows: Mapping[str, list[dict] | tuple[dict, ...]] | None = None,
        include_rps_bundle: bool = True,
        sources: Sequence[str] | set[str] | frozenset[str] | None = None,
    ) -> StockContextSnapshot:
        self._assert_gui_thread()
        specs = self._tab_specs()
        selected_sources = _normalized_scope(sources)
        captured_sources: list[_CapturedSource] = []
        for key in SOURCE_KEYS:
            if selected_sources is not None and key not in selected_sources:
                continue
            tab = self._loaded_tab(key)
            source = _captured_source(
                key,
                tab,
                self._rows(tab, key),
                self._is_loading(tab),
            )
            if source is not None:
                captured_sources.append(source)
        for spec in specs:
            key = _spec_key(spec)
            if (
                not key
                or key in SOURCE_KEYS
                or (selected_sources is not None and key not in selected_sources)
            ):
                continue
            tab = self._loaded_tab(key)
            source = _captured_source(
                key,
                tab,
                [],
                self._is_loading(tab),
            )
            if source is not None and source.direct:
                captured_sources.append(source)
        rps_bundle = self._rps_bundle() if include_rps_bundle else None
        return _build_snapshot(
            specs,
            captured_sources,
            cached_source_rows,
            rps_bundle,
            selected_sources=selected_sources,
        )


def capture_workspace_stock_context(
    workspace,
    *,
    include_rps_bundle: bool = True,
    sources: Sequence[str] | set[str] | frozenset[str] | None = None,
) -> StockContextSnapshot | None:
    """Use the workspace compatibility facade without exposing its internals."""

    explicit_reader = getattr(workspace, "capture_stock_context_snapshot", None)
    if callable(explicit_reader):
        options = {}
        if not include_rps_bundle:
            options["include_rps_bundle"] = False
        if sources is not None:
            options["sources"] = sources
        snapshot = explicit_reader(**options)
        return snapshot if isinstance(snapshot, StockContextSnapshot) else None
    context_reader = getattr(workspace, "collect_stock_context", None)
    if not callable(context_reader):
        return None
    options = {"capture_snapshot": True}
    if not include_rps_bundle:
        options["include_rps_bundle"] = False
    if sources is not None:
        options["sources"] = sources
    try:
        snapshot = context_reader(**options)
    except TypeError:
        return None
    return snapshot if isinstance(snapshot, StockContextSnapshot) else None


__all__ = ["StockContextWidgetSnapshotAdapter", "capture_workspace_stock_context"]
