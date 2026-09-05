"""GUI-thread adapter that copies widget state into a Qt-free snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from PyQt6.QtCore import QCoreApplication, QThread

from app.services.stock_context_model_service import (
    DEFAULT_SOURCE_ORDER,
    StockContextSnapshot,
    StockSignal,
    coerce_stock_signal,
)
from app.services.stock_context_model_service import freeze_stock_context_value as _freeze_plain
from app.services.stock_context_query_service import GENERAL_STOCK_CONTEXT_SOURCE_KEYS
from ui.workspaces.tab_capabilities import (
    DataLineageCapability,
    ForeignKeywordCapability,
    StockSignalSourceCapability,
)

SOURCE_KEYS = tuple(
    source_key for source_key in DEFAULT_SOURCE_ORDER if source_key in GENERAL_STOCK_CONTEXT_SOURCE_KEYS
)
SNAPSHOT_CAPTURE_ROW_CHUNK_SIZE = 32


@dataclass(frozen=True)
class _CapturedSource:
    key: str
    rows: tuple[Mapping[str, Any], ...]
    source_row_count: int
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


def _copy_rows(
    rows,
    target_codes: frozenset[str] | None = None,
) -> tuple[list[dict], int]:
    candidate_rows = [row for row in (rows or []) if isinstance(row, Mapping)]
    selected_rows = (
        candidate_rows
        if target_codes is None
        else [
            row
            for row in candidate_rows
            if str(row.get("代码") or "").strip() in target_codes
        ]
    )
    return ([_plain_copy(dict(row)) for row in selected_rows], len(candidate_rows))


def _scan_rows(tab, target_codes: frozenset[str] | None = None) -> tuple[list[dict], int]:
    reader = getattr(tab, "get_scan_results", None)
    return _copy_rows(reader(), target_codes) if callable(reader) else ([], 0)


def _public_rows(tab, target_codes: frozenset[str] | None = None) -> tuple[list[dict], int]:
    radar_reader = getattr(tab, "get_watchlist_radar_rows", None)
    reader = radar_reader if callable(radar_reader) else getattr(tab, "get_row_data", None)
    return _copy_rows(reader(), target_codes) if callable(reader) else ([], 0)


def _foreign_keywords(key: str, tab) -> tuple[str, ...]:
    if key != "foreign_block" or not isinstance(tab, ForeignKeywordCapability):
        return ()
    return tuple(str(item) for item in (tab.get_foreign_keywords() or []) if str(item))


def _direct_signals(
    key: str,
    tab,
    target_codes: frozenset[str] | None = None,
) -> tuple[StockSignal, ...]:
    if not isinstance(tab, StockSignalSourceCapability):
        return ()
    signals: list[StockSignal] = []
    for raw_signal in list(tab.iter_stock_signals() or []):
        signal = coerce_stock_signal(raw_signal)
        if signal is None:
            continue
        source_signal = signal if signal.source_tab else replace(signal, source_tab=key)
        if target_codes is not None and source_signal.normalized_code() not in target_codes:
            continue
        signals.append(replace(source_signal, payload=_plain_copy(dict(source_signal.payload or {}))))
    return tuple(signals)


def _captured_source(
    key: str,
    tab,
    rows: list[dict],
    source_row_count: int,
    loading: bool,
    target_codes: frozenset[str] | None = None,
) -> _CapturedSource | None:
    if tab is None:
        return None
    signals = _direct_signals(key, tab, target_codes)
    return _CapturedSource(
        key=key,
        rows=tuple(rows),
        source_row_count=source_row_count,
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
    target_codes: frozenset[str] | None = None,
) -> dict[str, tuple[dict, ...]]:
    selected_rows = {
        str(key): rows
        for key, rows in (cached_source_rows or {}).items()
        if selected_sources is None or str(key) in selected_sources
    }
    if target_codes is not None and not target_codes:
        return {key: () for key in selected_rows}
    return {
        key: tuple(_copy_rows(rows, target_codes)[0])
        for key, rows in selected_rows.items()
    }


def _cached_row_count_snapshot(
    cached_source_rows,
    cached_source_row_counts,
    selected_sources: frozenset[str] | None = None,
) -> dict[str, int]:
    selected_counts = {
        str(key): count
        for key, count in (cached_source_row_counts or {}).items()
        if selected_sources is None or str(key) in selected_sources
    }
    for key, rows in (cached_source_rows or {}).items():
        source = str(key)
        if selected_sources is not None and source not in selected_sources:
            continue
        if source not in selected_counts and isinstance(rows, (list, tuple)):
            selected_counts[source] = len(rows)
    return selected_counts


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


def _source_row_count_map(sources: list[_CapturedSource]) -> dict[str, int]:
    return {source.key: source.source_row_count for source in sources}


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
    cached_source_row_counts=None,
    prepared_cached_source_rows: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
    selected_sources: frozenset[str] | None = None,
    target_codes: frozenset[str] | None = None,
) -> StockContextSnapshot:
    return StockContextSnapshot._from_frozen_parts(
        source_rows=_source_rows_map(sources),
        cached_source_rows=dict(prepared_cached_source_rows or {}),
        available_sources=_available_source_keys(specs, sources, selected_sources),
        loading_sources=_loading_source_keys(sources),
        direct_source_keys=_direct_source_keys(sources),
        direct_signals=_direct_source_signals(sources),
        foreign_keywords=_first_foreign_keywords(sources),
        tab_titles=_freeze_plain(_tab_title_map(specs)),
        rps_bundle=rps_bundle,
        source_row_counts=_source_row_count_map(sources),
        cached_source_row_counts=_cached_row_count_snapshot(
            cached_source_rows,
            cached_source_row_counts,
            selected_sources,
        ),
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
    def _rows(
        tab,
        key: str,
        target_codes: frozenset[str] | None = None,
    ) -> tuple[list[dict], int]:
        if tab is None:
            return ([], 0)
        if key == "scan":
            rows, source_row_count = _scan_rows(tab, target_codes)
            if source_row_count:
                return (rows, source_row_count)
        return _public_rows(tab, target_codes)

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
        cached_source_row_counts: Mapping[str, int] | None = None,
        include_rps_bundle: bool = True,
        sources: Sequence[str] | set[str] | frozenset[str] | None = None,
        target_codes: Sequence[str] | set[str] | frozenset[str] | None = None,
        row_chunk_size: int = SNAPSHOT_CAPTURE_ROW_CHUNK_SIZE,
    ) -> StockContextSnapshot:
        session = self.begin_capture(
            cached_source_rows=cached_source_rows,
            cached_source_row_counts=cached_source_row_counts,
            include_rps_bundle=include_rps_bundle,
            sources=sources,
            target_codes=target_codes,
            row_chunk_size=row_chunk_size,
        )
        while not session.advance():
            pass
        return session.snapshot()

    def begin_capture(
        self,
        *,
        cached_source_rows: Mapping[str, list[dict] | tuple[dict, ...]] | None = None,
        cached_source_row_counts: Mapping[str, int] | None = None,
        include_rps_bundle: bool = True,
        sources: Sequence[str] | set[str] | frozenset[str] | None = None,
        target_codes: Sequence[str] | set[str] | frozenset[str] | None = None,
        row_chunk_size: int = SNAPSHOT_CAPTURE_ROW_CHUNK_SIZE,
    ) -> "StockContextWidgetSnapshotCaptureSession":
        return StockContextWidgetSnapshotCaptureSession(
            self,
            cached_source_rows=cached_source_rows,
            cached_source_row_counts=cached_source_row_counts,
            include_rps_bundle=include_rps_bundle,
            sources=sources,
            target_codes=target_codes,
            row_chunk_size=row_chunk_size,
        )


@dataclass
class _CachedRowsCapture:
    key: str
    rows_iterator: object
    frozen_rows: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass
class _SourceRowsCapture:
    key: str
    tab: object
    rows_iterator: object
    include_rows: bool
    loading: bool
    direct: bool
    fallback_reader: object | None = None
    source_row_count: int = 0
    frozen_rows: list[Mapping[str, Any]] = field(default_factory=list)
    signals_iterator: object | None = None
    frozen_signals: list[StockSignal] = field(default_factory=list)


class StockContextWidgetSnapshotCaptureSession:
    """Copy GUI-owned rows in fixed-size chunks before immutable assembly."""

    def __init__(
        self,
        adapter: StockContextWidgetSnapshotAdapter,
        *,
        cached_source_rows: Mapping[str, list[dict] | tuple[dict, ...]] | None = None,
        cached_source_row_counts: Mapping[str, int] | None = None,
        include_rps_bundle: bool = True,
        sources: Sequence[str] | set[str] | frozenset[str] | None = None,
        target_codes: Sequence[str] | set[str] | frozenset[str] | None = None,
        row_chunk_size: int = SNAPSHOT_CAPTURE_ROW_CHUNK_SIZE,
    ) -> None:
        adapter._assert_gui_thread()
        self._adapter = adapter
        self._specs = adapter._tab_specs()
        self._cached_source_rows = dict(cached_source_rows or {})
        self._cached_source_row_counts = dict(cached_source_row_counts or {})
        self._selected_sources = _normalized_scope(sources)
        self._target_codes = _normalized_scope(target_codes)
        self._row_chunk_size = max(1, int(row_chunk_size or 1))
        self._captured_sources: list[_CapturedSource] = []
        self._prepared_cached_source_rows: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self._pending_cached_keys = [
            str(key)
            for key in self._cached_source_rows
            if self._selected_sources is None or str(key) in self._selected_sources
        ]
        self._pending_source_keys = [
            key
            for key in SOURCE_KEYS
            if self._selected_sources is None or key in self._selected_sources
        ]
        self._pending_extra_specs = [
            spec
            for spec in self._specs
            if (key := _spec_key(spec))
            and key not in SOURCE_KEYS
            and (self._selected_sources is None or key in self._selected_sources)
        ]
        self._active_cached_capture: _CachedRowsCapture | None = None
        self._active_source_capture: _SourceRowsCapture | None = None
        self._rps_bundle = None
        self._rps_pending = bool(include_rps_bundle)
        self._snapshot: StockContextSnapshot | None = None

    @staticmethod
    def _values_iterator(value) -> object:
        return iter(value or ())

    @staticmethod
    def _public_rows_reader(tab):
        radar_reader = getattr(tab, "get_watchlist_radar_rows", None)
        return radar_reader if callable(radar_reader) else getattr(tab, "get_row_data", None)

    @classmethod
    def _stock_context_rows_reader(cls, tab):
        """Prefer the lazy public context-row capability when a tab has it."""
        iterator_reader = getattr(tab, "iter_stock_context_rows", None)
        return iterator_reader if callable(iterator_reader) else cls._public_rows_reader(tab)

    def _new_source_capture(self, key: str, *, include_rows: bool) -> _SourceRowsCapture | None:
        tab = self._adapter._loaded_tab(key)
        if tab is None:
            return None
        reader = None
        fallback_reader = None
        if include_rows:
            if key == "scan":
                scan_iterator_reader = getattr(tab, "iter_scan_results", None)
                if callable(scan_iterator_reader):
                    # ScanTab's iterator owns its legacy model fallback.  Do
                    # not also call the old eager getters after it completes.
                    reader = scan_iterator_reader
                else:
                    scan_reader = getattr(tab, "get_scan_results", None)
                    if callable(scan_reader):
                        reader = scan_reader
                        fallback_reader = self._stock_context_rows_reader(tab)
                    else:
                        reader = self._stock_context_rows_reader(tab)
            else:
                reader = self._stock_context_rows_reader(tab)
        raw_rows = reader() if callable(reader) else ()
        return _SourceRowsCapture(
            key=key,
            tab=tab,
            rows_iterator=self._values_iterator(raw_rows),
            include_rows=include_rows,
            loading=self._adapter._is_loading(tab),
            direct=isinstance(tab, StockSignalSourceCapability),
            fallback_reader=fallback_reader,
        )

    def advance(self) -> bool:
        """Copy at most ``row_chunk_size`` row/signal values in this turn."""
        self._adapter._assert_gui_thread()
        if self._snapshot is not None:
            return True
        if self._target_codes is not None and not self._target_codes:
            return self._advance_empty_target()
        if self._active_cached_capture is not None or self._pending_cached_keys:
            return self._advance_cached_capture()
        if self._active_source_capture is not None or self._pending_source_keys:
            return self._advance_source_capture(include_rows=True)
        if self._pending_extra_specs:
            return self._advance_source_capture(include_rows=False)
        if self._rps_pending:
            self._rps_bundle = _freeze_plain(self._adapter._rps_bundle())
            self._rps_pending = False
            return False
        self._snapshot = _build_snapshot(
            self._specs,
            self._captured_sources,
            self._cached_source_rows,
            self._rps_bundle,
            cached_source_row_counts=self._cached_source_row_counts,
            prepared_cached_source_rows=self._prepared_cached_source_rows,
            selected_sources=self._selected_sources,
            target_codes=self._target_codes,
        )
        return True

    def _advance_empty_target(self) -> bool:
        if self._pending_cached_keys:
            key = self._pending_cached_keys.pop(0)
            self._prepared_cached_source_rows[key] = ()
            return False
        if self._pending_source_keys:
            self._pending_source_keys.pop(0)
            return False
        if self._pending_extra_specs:
            self._pending_extra_specs.pop(0)
            return False
        if self._rps_pending:
            self._rps_bundle = None
            self._rps_pending = False
            return False
        self._snapshot = _build_snapshot(
            self._specs,
            self._captured_sources,
            self._cached_source_rows,
            self._rps_bundle,
            cached_source_row_counts=self._cached_source_row_counts,
            prepared_cached_source_rows=self._prepared_cached_source_rows,
            selected_sources=self._selected_sources,
            target_codes=self._target_codes,
        )
        return True

    def _advance_cached_capture(self) -> bool:
        capture = self._active_cached_capture
        if capture is None:
            key = self._pending_cached_keys.pop(0)
            capture = _CachedRowsCapture(
                key=key,
                rows_iterator=self._values_iterator(self._cached_source_rows.get(key)),
            )
            self._active_cached_capture = capture

        processed = 0
        while processed < self._row_chunk_size:
            try:
                row = next(capture.rows_iterator)
            except StopIteration:
                self._prepared_cached_source_rows[capture.key] = tuple(capture.frozen_rows)
                self._active_cached_capture = None
                return False
            processed += 1
            if not isinstance(row, Mapping):
                continue
            if self._target_codes is not None and str(row.get("代码") or "").strip() not in self._target_codes:
                continue
            capture.frozen_rows.append(_freeze_plain(_plain_copy(dict(row))))
        return False

    def _advance_source_capture(self, *, include_rows: bool) -> bool:
        capture = self._active_source_capture
        if capture is None:
            if include_rows:
                key = self._pending_source_keys.pop(0)
            else:
                key = _spec_key(self._pending_extra_specs.pop(0))
            capture = self._new_source_capture(key, include_rows=include_rows)
            if capture is None:
                return False
            self._active_source_capture = capture

        processed = 0
        if capture.include_rows:
            while processed < self._row_chunk_size:
                try:
                    row = next(capture.rows_iterator)
                except StopIteration:
                    if capture.fallback_reader is not None and capture.source_row_count == 0:
                        reader = capture.fallback_reader
                        capture.fallback_reader = None
                        capture.rows_iterator = self._values_iterator(reader() if callable(reader) else ())
                        continue
                    break
                processed += 1
                if not isinstance(row, Mapping):
                    continue
                capture.source_row_count += 1
                if self._target_codes is not None and str(row.get("代码") or "").strip() not in self._target_codes:
                    continue
                capture.frozen_rows.append(_freeze_plain(_plain_copy(dict(row))))
            if processed >= self._row_chunk_size:
                return False

        if capture.direct:
            if capture.signals_iterator is None:
                reader = getattr(capture.tab, "iter_stock_signals", None)
                capture.signals_iterator = self._values_iterator(reader() if callable(reader) else ())
            while processed < self._row_chunk_size:
                try:
                    raw_signal = next(capture.signals_iterator)
                except StopIteration:
                    break
                processed += 1
                signal = coerce_stock_signal(raw_signal)
                if signal is None:
                    continue
                source_signal = signal if signal.source_tab else replace(signal, source_tab=capture.key)
                if self._target_codes is not None and source_signal.normalized_code() not in self._target_codes:
                    continue
                capture.frozen_signals.append(
                    replace(
                        source_signal,
                        payload=_freeze_plain(_plain_copy(dict(source_signal.payload or {}))),
                    )
                )
            if processed >= self._row_chunk_size:
                return False

        self._finish_source_capture(capture)
        self._active_source_capture = None
        return False

    def _finish_source_capture(self, capture: _SourceRowsCapture) -> None:
        if not capture.include_rows and not capture.direct:
            return
        self._captured_sources.append(
            _CapturedSource(
                key=capture.key,
                rows=tuple(capture.frozen_rows),
                source_row_count=capture.source_row_count,
                loading=capture.loading,
                direct=capture.direct,
                signals=tuple(capture.frozen_signals),
                foreign_keywords=_foreign_keywords(capture.key, capture.tab),
            )
        )

    def next_phase_label(self) -> str:
        """Return the next bounded GUI phase for diagnostics and scheduling."""
        if self._snapshot is not None:
            return "complete"
        if self._active_cached_capture is not None:
            return f"cached_{self._active_cached_capture.key}_rows"
        if self._pending_cached_keys:
            return f"cached_{self._pending_cached_keys[0]}_rows"
        if self._active_source_capture is not None:
            return f"source_{self._active_source_capture.key}_rows"
        if self._pending_source_keys:
            return f"source_{self._pending_source_keys[0]}_rows"
        if self._pending_extra_specs:
            return f"source_{_spec_key(self._pending_extra_specs[0])}_signals"
        if self._rps_pending:
            return "rps_bundle"
        return "assemble_snapshot"

    def snapshot(self) -> StockContextSnapshot:
        if self._snapshot is None:
            raise RuntimeError("stock-context snapshot capture is not complete")
        return self._snapshot


def capture_workspace_stock_context(
    workspace,
    *,
    include_rps_bundle: bool = True,
    sources: Sequence[str] | set[str] | frozenset[str] | None = None,
    target_codes: Sequence[str] | set[str] | frozenset[str] | None = None,
) -> StockContextSnapshot | None:
    """Use the workspace compatibility facade without exposing its internals."""

    explicit_reader = getattr(workspace, "capture_stock_context_snapshot", None)
    if callable(explicit_reader):
        options: dict[str, Any] = {}
        if not include_rps_bundle:
            options["include_rps_bundle"] = False
        if sources is not None:
            options["sources"] = sources
        if target_codes is not None:
            options["target_codes"] = target_codes
        snapshot = explicit_reader(**options)
        return snapshot if isinstance(snapshot, StockContextSnapshot) else None
    context_reader = getattr(workspace, "collect_stock_context", None)
    if not callable(context_reader):
        return None
    context_options: dict[str, Any] = {"capture_snapshot": True}
    if not include_rps_bundle:
        context_options["include_rps_bundle"] = False
    if sources is not None:
        context_options["sources"] = sources
    if target_codes is not None:
        context_options["target_codes"] = target_codes
    try:
        snapshot = context_reader(**context_options)
    except TypeError:
        return None
    return snapshot if isinstance(snapshot, StockContextSnapshot) else None


__all__ = [
    "StockContextWidgetSnapshotAdapter",
    "StockContextWidgetSnapshotCaptureSession",
    "capture_workspace_stock_context",
]
