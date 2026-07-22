# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from collections.abc import Mapping
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger
from core.observability import record_metric
from core.state.quote_snapshot import QuoteSnapshot
from domains.quotes.snapshot import get_missing_a_share_finance_codes, merge_quote_snapshot

log = get_logger(__name__)
_observed_unknown_quote_leaf_types: set[str] = set()
_unknown_quote_leaf_types_lock = threading.Lock()


def _observe_quote_snapshot(snapshot: QuoteSnapshot, *, operation: str) -> None:
    if snapshot.unknown_leaf_count <= 0:
        return
    record_metric(
        "quote_snapshot_mutable_leaf_detected",
        snapshot.unknown_leaf_count,
        unit="count",
        tags={
            "operation": operation,
            "types": "|".join(snapshot.unknown_leaf_types),
        },
    )
    with _unknown_quote_leaf_types_lock:
        new_types = tuple(
            type_name
            for type_name in snapshot.unknown_leaf_types
            if type_name not in _observed_unknown_quote_leaf_types
        )
        _observed_unknown_quote_leaf_types.update(new_types)
    for type_name in new_types:
        log.warning(f"[行情快照] 检测到未声明的叶子类型，已隔离对应行情字段: {type_name}")


class GlobalStore(QObject):
    """Process-wide lightweight snapshot store."""

    sig_state_changed = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self._state_lock = threading.RLock()
        self._quote_snapshot = QuoteSnapshot.empty()
        self._watchlist: list[object] = []

    @property
    def state(self) -> Mapping[str, object]:
        """Return an immutable compatibility view of process state."""
        with self._state_lock:
            return MappingProxyType(
                {
                    "quotes": self._quote_snapshot,
                    "watchlist": list(self._watchlist),
                }
            )

    def merge_quotes(self, data: Mapping | None) -> QuoteSnapshot:
        if not isinstance(data, Mapping):
            return self.get_latest_quotes()
        if not data:
            return self.get_latest_quotes()

        with self._state_lock:
            old_snapshot = self._quote_snapshot
            merged_quotes = merge_quote_snapshot(old_snapshot.quotes, data)
            observed_snapshot = QuoteSnapshot.create(
                version=old_snapshot.version + 1,
                quotes=merged_quotes,
            )
            if observed_snapshot.unknown_leaf_count > 0:
                safe_incoming = QuoteSnapshot.create(
                    version=0,
                    timestamp=0,
                    quotes=data,
                )
                new_snapshot = QuoteSnapshot.create(
                    version=old_snapshot.version + 1,
                    quotes=merge_quote_snapshot(old_snapshot.quotes, safe_incoming.quotes),
                )
            else:
                new_snapshot = observed_snapshot
            self._quote_snapshot = new_snapshot
        _observe_quote_snapshot(observed_snapshot, operation="merge")
        return new_snapshot

    def replace_quotes(self, quotes: Mapping[str, Mapping[str, object]]) -> QuoteSnapshot:
        """Atomically replace the current quotes with a new immutable snapshot."""
        if not isinstance(quotes, Mapping):
            raise TypeError("quotes must be a mapping")
        with self._state_lock:
            new_snapshot = QuoteSnapshot.create(
                version=self._quote_snapshot.version + 1,
                quotes=quotes,
            )
            self._quote_snapshot = new_snapshot
        _observe_quote_snapshot(new_snapshot, operation="replace")
        return new_snapshot

    def replace_quote_snapshot(self, snapshot: QuoteSnapshot) -> QuoteSnapshot:
        """Atomically install a caller-built snapshot when its version advances."""
        if not isinstance(snapshot, QuoteSnapshot):
            raise TypeError("snapshot must be a QuoteSnapshot")
        with self._state_lock:
            if snapshot.version <= self._quote_snapshot.version:
                raise ValueError("snapshot version must advance")
            self._quote_snapshot = snapshot
        _observe_quote_snapshot(snapshot, operation="install")
        return snapshot

    def get_latest_quotes(self) -> QuoteSnapshot:
        with self._state_lock:
            return self._quote_snapshot

    def get_missing_a_share_finance_codes(self, codes) -> list[str]:
        return get_missing_a_share_finance_codes(codes, self.get_latest_quotes().quotes)

    def reset_quotes(self) -> QuoteSnapshot:
        return self.replace_quotes({})

    def reset_runtime_state(self) -> None:
        self.reset_quotes()
        with self._state_lock:
            self._watchlist.clear()


global_store = GlobalStore()
