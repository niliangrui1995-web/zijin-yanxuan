# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import replace

from app.services.stock_context_fund_snapshot_process_service import (
    load_stock_context_fund_snapshot_in_subprocess,
)
from app.services.stock_context_model_service import (
    StockContextReadPolicy,
    StockContextSignalIndex,
    StockContextSnapshot,
    StockSignal,
)
from app.services.stock_context_query_service import StockContextQueryService
from app.services.stock_context_snapshot_service import (
    lhb_pool_cache_signature,
    load_lhb_pool_rows,
    project_root,
)
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from app.services.ui_task_lifecycle_service import raise_if_cancelled as _checkpoint
from ui.workspaces.stock_context_widget_adapter import StockContextWidgetSnapshotAdapter

KEY_LAST_LISTED_RAW = "_\u6700\u8fd1\u4e0a\u699c_raw"
KEY_LAST_LISTED = "\u6700\u8fd1\u4e0a\u699c"
POST_F5_CONTEXT_SNAPSHOT_DEFER_SECONDS = 5.0
FUND_SNAPSHOT_TIMEOUT_SECONDS = 90.0
LHB_SNAPSHOT_TIMEOUT_SECONDS = 180.0


def _normalized_scope(
    values: Sequence[str] | set[str] | frozenset[str] | None,
) -> frozenset[str] | None:
    if values is None:
        return None
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip())


def _copy_cached_rows(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _cancellable_items(values, cancellation_token=None):
    for value in values:
        _checkpoint(cancellation_token)
        yield value


def _start_fund_snapshot(service, force: bool, include_fund: bool) -> tuple[bool, bool]:
    with service._fund_rows_lock:
        already_running = service._fund_rows_loading
        start = include_fund and (force or (not service._fund_rows_loading and not service._fund_rows_loaded))
        if start:
            service._fund_rows_loading = True
    return start, already_running


def _schedule_fund_snapshot(service, domain_events, task_registry) -> None:
    def _on_success(rows):
        if service._shutdown:
            return
        with service._fund_rows_lock:
            service._fund_rows_snapshot = [dict(row) for row in (rows or [])]
            service._fund_rows_loaded = True
            service._fund_rows_loading = False
        domain_events.sig_stock_context_snapshot_updated.emit()

    def _on_error(_message: str):
        with service._fund_rows_lock:
            service._fund_rows_loading = False

    service._task_lifecycle.run_background(
        "fund-snapshot",
        lambda token: invoke_with_cancellation(service._load_fund_holding_rows_snapshot, token),
        on_success=_on_success,
        on_error=_on_error,
        task_id=task_registry.workspace("stock_context_fund_rows_snapshot"),
        timeout_sec=FUND_SNAPSHOT_TIMEOUT_SECONDS,
    )


def _schedule_lhb_snapshot(service, domain_events, task_registry, signature) -> None:
    def _on_success(rows):
        if service._shutdown:
            return
        with service._lhb_rows_lock:
            service._lhb_rows_snapshot = [dict(row) for row in (rows or [])]
            service._lhb_rows_signature = signature
            service._lhb_rows_loading = False
        domain_events.sig_stock_context_snapshot_updated.emit()

    def _on_error(_message: str):
        with service._lhb_rows_lock:
            service._lhb_rows_loading = False

    service._task_lifecycle.run_background(
        "lhb-snapshot",
        lambda token: invoke_with_cancellation(service._load_lhb_pool_rows, token),
        on_success=_on_success,
        on_error=_on_error,
        task_id=task_registry.workspace("stock_context_lhb_rows_snapshot"),
        timeout_sec=LHB_SNAPSHOT_TIMEOUT_SECONDS,
    )

class StockContextService:
    """Owns asynchronously refreshed plain-data snapshots for StockContext queries."""

    def __init__(self, workspace):
        self._workspace = workspace
        self._fund_rows_lock = threading.RLock()
        self._fund_rows_snapshot: list[dict] = []
        self._fund_rows_loaded = False
        self._fund_rows_loading = False
        self._lhb_rows_lock = threading.RLock()
        self._lhb_rows_snapshot: list[dict] = []
        self._lhb_rows_signature: tuple[str, int, int] | None = None
        self._lhb_rows_loading = False
        self._post_f5_snapshot_defer_until = 0.0
        self._published_kline_index_lock = threading.RLock()
        self._published_kline_index: StockContextSignalIndex | None = None
        self._published_kline_index_version = 0
        self._task_lifecycle = TaskLifecycleGroup()
        self._shutdown = False

    @staticmethod
    def _project_root():
        return project_root()

    @staticmethod
    def _coerce_cache_rows(value) -> list[dict]:
        if not isinstance(value, (list, tuple)):
            return []
        return [dict(row) for row in value if isinstance(row, dict)]

    def _lhb_pool_cache_signature(self) -> tuple[str, int, int] | None:
        return lhb_pool_cache_signature(root=self._project_root())

    def _load_lhb_pool_rows(self, *, cancellation_token=None) -> list[dict]:
        _checkpoint(cancellation_token)
        signature = self._lhb_pool_cache_signature()
        if signature is not None:
            with self._lhb_rows_lock:
                if signature == self._lhb_rows_signature:
                    return [dict(row) for row in self._lhb_rows_snapshot]

        pool = invoke_with_cancellation(
            load_lhb_pool_rows,
            cancellation_token,
            engine=getattr(self._workspace, "engine", None),
        )

        rows: list[dict] = []
        for row in _cancellable_items(self._coerce_cache_rows(pool), cancellation_token):
            raw_date = str(row.get(KEY_LAST_LISTED, "") or "").strip()
            if len(raw_date) == 8:
                row[KEY_LAST_LISTED_RAW] = raw_date
                row[KEY_LAST_LISTED] = f"{raw_date[4:6]}-{raw_date[6:8]}"
            rows.append(row)
        if signature is not None:
            _checkpoint(cancellation_token)
            with self._lhb_rows_lock:
                self._lhb_rows_snapshot = [dict(row) for row in rows]
                self._lhb_rows_signature = signature
        return rows

    def prepare_post_f5_refresh(self) -> None:
        self._post_f5_snapshot_defer_until = max(
            float(getattr(self, "_post_f5_snapshot_defer_until", 0.0) or 0.0),
            time.monotonic() + POST_F5_CONTEXT_SNAPSHOT_DEFER_SECONDS,
        )

    def _should_defer_async_snapshots(self) -> bool:
        try:
            return time.monotonic() < float(getattr(self, "_post_f5_snapshot_defer_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False

    def publish_kline_signal_index(self, index: StockContextSignalIndex) -> int:
        if not isinstance(index, StockContextSignalIndex):
            raise TypeError("K-line signal index must be immutable")
        with self._published_kline_index_lock:
            self._published_kline_index = index
            self._published_kline_index_version += 1
            return self._published_kline_index_version

    def published_kline_signals(self, code: str) -> tuple[StockSignal, ...] | None:
        with self._published_kline_index_lock:
            index = self._published_kline_index
        return None if index is None else index.signals_for(code)

    def _load_fund_holding_rows_snapshot(self, *, stock_codes=None, cancellation_token=None) -> list[dict]:
        rows = load_stock_context_fund_snapshot_in_subprocess(
            project_root=self._project_root(),
            stock_codes=stock_codes,
            cancellation_token=cancellation_token,
            timeout_seconds=FUND_SNAPSHOT_TIMEOUT_SECONDS,
        )
        _checkpoint(cancellation_token)
        return rows

    def refresh_async_snapshots(
        self,
        *,
        force: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> bool:
        if self._shutdown or self._should_defer_async_snapshots():
            return False
        try:
            from app.services.ui_event_service import domain_events
            from app.services.ui_task_service import task_registry
        except (ImportError, RuntimeError):
            return False

        scheduled = False
        start_fund, already_running = _start_fund_snapshot(self, force, include_fund)
        if start_fund:
            scheduled = True
            _schedule_fund_snapshot(self, domain_events, task_registry)

        lhb_signature = self._lhb_pool_cache_signature() if include_lhb else None
        with self._lhb_rows_lock:
            already_running = already_running or self._lhb_rows_loading
            start_lhb = (
                lhb_signature is not None
                and (force or (not self._lhb_rows_loading and lhb_signature != self._lhb_rows_signature))
            )
            if start_lhb:
                self._lhb_rows_loading = True

        if start_lhb:
            scheduled = True
            _schedule_lhb_snapshot(self, domain_events, task_registry, lhb_signature)

        return scheduled or already_running

    def async_snapshots_settled(self) -> bool:
        with self._fund_rows_lock:
            fund_loading = self._fund_rows_loading
        with self._lhb_rows_lock:
            lhb_loading = self._lhb_rows_loading
        return not fund_loading and not lhb_loading

    def cancel_async_snapshots(self, *, reason: str = "cancelled") -> bool:
        cancelled = False
        for name in ("fund-snapshot", "lhb-snapshot"):
            cancelled = self._task_lifecycle.cancel(name, reason=reason) or cancelled
        with self._fund_rows_lock:
            self._fund_rows_loading = False
        with self._lhb_rows_lock:
            self._lhb_rows_loading = False
        return cancelled

    def shutdown(self, *, timeout_ms: int = 750) -> bool:
        self._shutdown = True
        completed = self._task_lifecycle.shutdown(timeout_ms=timeout_ms)
        with self._fund_rows_lock:
            self._fund_rows_loading = False
        with self._lhb_rows_lock:
            self._lhb_rows_loading = False
        with self._published_kline_index_lock:
            self._published_kline_index = None
        return completed

def capture_stock_context_snapshot(
    service: StockContextService,
    *,
    include_rps_bundle: bool = True,
    sources: Sequence[str] | set[str] | frozenset[str] | None = None,
) -> StockContextSnapshot:
    """Capture loaded-widget rows plus already-published async snapshots."""

    selected_sources = _normalized_scope(sources)
    include_fund = selected_sources is None or "fund_holdings" in selected_sources
    include_lhb = selected_sources is None or "lhb" in selected_sources
    cached_rows: dict[str, list[dict]] = {}
    loading_sources: set[str] = set()
    if include_fund:
        with service._fund_rows_lock:
            if service._fund_rows_loaded:
                cached_rows["fund_holdings"] = _copy_cached_rows(
                    service._fund_rows_snapshot,
                )
            if service._fund_rows_loading:
                loading_sources.add("fund_holdings")

    if include_lhb:
        signature = service._lhb_pool_cache_signature()
        with service._lhb_rows_lock:
            if signature is not None and signature == service._lhb_rows_signature:
                cached_rows["lhb"] = _copy_cached_rows(
                    service._lhb_rows_snapshot,
                )
            if service._lhb_rows_loading:
                loading_sources.add("lhb")

    adapter = StockContextWidgetSnapshotAdapter(service._workspace)
    if selected_sources is None:
        snapshot = (
            adapter.capture(cached_source_rows=cached_rows)
            if include_rps_bundle
            else adapter.capture(
                cached_source_rows=cached_rows,
                include_rps_bundle=False,
            )
        )
    else:
        snapshot = adapter.capture(
            cached_source_rows=cached_rows,
            include_rps_bundle=include_rps_bundle,
            sources=selected_sources,
        )
    return replace(snapshot, loading_sources=snapshot.loading_sources | frozenset(loading_sources))


def collect_stock_context_snapshot(
    snapshot: StockContextSnapshot,
    *,
    include_cache_fallback: bool = True,
    include_source_cache_fallback: bool | None = None,
    allow_lhb_cache_compute: bool = False,
    target_codes=None,
    sources=None,
) -> dict[str, list[StockSignal]]:
    policy = StockContextReadPolicy.build(
        include_cache_fallback=include_cache_fallback,
        include_source_cache_fallback=include_source_cache_fallback,
        allow_lhb_cache_compute=allow_lhb_cache_compute,
        target_codes=target_codes,
        sources=sources,
    )
    return StockContextQueryService(snapshot).query_by_code(policy)


def collect_watchlist_radar_snapshot(
    snapshot: StockContextSnapshot,
    *,
    include_source_cache_fallback: bool = True,
    target_codes=None,
    allow_lhb_cache_compute: bool = False,
) -> tuple[dict, dict, dict, dict, dict, object]:
    return StockContextQueryService(snapshot).query_watchlist_radar(
        target_codes=target_codes,
        include_source_cache_fallback=include_source_cache_fallback,
        allow_lhb_cache_compute=allow_lhb_cache_compute,
    )
