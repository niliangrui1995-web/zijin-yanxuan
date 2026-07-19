"""Qt-free stock-context query service over a captured widget snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.services.stock_context_snapshot_service import (
    load_ai_chain_cache_rows,
    load_earnings_state_payload,
    load_fund_holding_snapshot,
    load_lhb_pool_rows,
    load_named_cache_rows,
    load_scan_cache_rows,
    project_root,
)
from domains.stock_context.models import StockContextReadPolicy, StockContextSnapshot, StockSignal
from domains.stock_context.signal_builders import (
    DEFAULT_SOURCE_ORDER,
    KLINE_SOURCE_KEYS,
    RADAR_SOURCE_KEYS,
    build_signals_for_source,
    build_watchlist_radar_data,
    earnings_discovery_lookup,
    format_fund_holding_store_rows,
    index_signals_by_code,
    normalize_lhb_pool_rows,
    prepare_earnings_cache_rows,
)

GENERAL_STOCK_CONTEXT_SOURCE_KEYS = frozenset(DEFAULT_SOURCE_ORDER)


class StockContextQueryService:
    """Assembles signals without reading a QWidget or Qt item model."""

    def __init__(
        self,
        snapshot: StockContextSnapshot,
        *,
        root: Path | None = None,
        engine: Any = None,
    ) -> None:
        self._snapshot = snapshot
        self._root = root or project_root()
        self._engine = engine
        self._earnings_state: tuple[dict, str] | None = None

    def _read_earnings_state(self) -> tuple[dict, str]:
        if self._earnings_state is None:
            payload, updated_at = load_earnings_state_payload()
            self._earnings_state = (dict(payload or {}), str(updated_at or ""))
        return self._earnings_state

    def _fund_rows(self, policy: StockContextReadPolicy) -> list[dict]:
        cached_rows = self._snapshot.cached_rows_for("fund_holdings")
        if cached_rows:
            return self._filter_rows(cached_rows, policy.target_codes)
        if "fund_holdings" in self._snapshot.loading_sources:
            return []
        if not policy.allow_fund_store_query:
            return []
        latest_quarters, change_rows = load_fund_holding_snapshot(stock_codes=policy.target_codes)
        try:
            from app.services.ui_fund_holdings_service import QFII_CAPITAL_ATTRIBUTE_UNMARKED, SUBJECT_QFII

            qfii_code = str((SUBJECT_QFII or {}).get("subject_code") or "")
        except (ImportError, RuntimeError):
            QFII_CAPITAL_ATTRIBUTE_UNMARKED = ""
            qfii_code = ""
        return format_fund_holding_store_rows(
            latest_quarters,
            change_rows,
            qfii_subject_code=qfii_code,
            unmarked_capital_attribute=QFII_CAPITAL_ATTRIBUTE_UNMARKED,
        )

    def _lhb_rows(self, policy: StockContextReadPolicy) -> list[dict]:
        cached_rows = self._snapshot.cached_rows_for("lhb")
        if cached_rows or "lhb" in self._snapshot.loading_sources:
            return cached_rows
        if not policy.allow_lhb_cache_compute:
            return []
        return normalize_lhb_pool_rows(load_lhb_pool_rows(engine=self._engine))

    def _special_fallback_rows(self, source: str, policy: StockContextReadPolicy) -> list[dict] | None:
        if source == "scan":
            return load_scan_cache_rows(root=self._root) if policy.include_cache_fallback else []
        if source == "fund_holdings":
            return self._fund_rows(policy)
        if source == "lhb":
            return self._lhb_rows(policy) if policy.source_cache_fallback else []
        return None

    def _source_cache_rows(self, source: str) -> list[dict]:
        if source == "ai_industry_chain":
            return [dict(row) for row in load_ai_chain_cache_rows()]
        filenames = {
            "na_daily": "na_daily_latest.json",
            "foreign_block": "foreign_block_trade_latest.json",
        }
        if source in filenames:
            return load_named_cache_rows(filenames[source], root=self._root)
        if source == "earnings":
            payload, updated_at = self._read_earnings_state()
            return prepare_earnings_cache_rows(payload, updated_at)
        return []

    def _fallback_rows(self, source: str, policy: StockContextReadPolicy) -> list[dict]:
        if source not in self._snapshot.available_sources:
            return []
        special_rows = self._special_fallback_rows(source, policy)
        if special_rows is not None:
            return special_rows
        if not policy.source_cache_fallback:
            return []
        return self._source_cache_rows(source)

    def _source_rows(self, source: str, policy: StockContextReadPolicy) -> list[dict]:
        widget_rows = self._snapshot.rows_for(source)
        if widget_rows:
            return self._filter_rows(widget_rows, policy.target_codes)
        return self._filter_rows(self._fallback_rows(source, policy), policy.target_codes)

    @staticmethod
    def _filter_rows(rows: Sequence[Mapping[str, Any]], target_codes: frozenset[str] | None) -> list[dict]:
        copied = [dict(row) for row in rows]
        if target_codes is None:
            return copied
        return [row for row in copied if str(row.get("代码") or "").strip() in target_codes]

    def _source_signals(self, source: str, policy: StockContextReadPolicy) -> list[StockSignal]:
        rows = self._source_rows(source, policy)
        lookup = None
        if source == "earnings" and rows:
            payload, updated_at = self._read_earnings_state()
            lookup = earnings_discovery_lookup(payload, updated_at)
        return build_signals_for_source(
            source,
            rows,
            foreign_keywords=self._snapshot.foreign_keywords,
            discovery_lookup=lookup,
        )

    @staticmethod
    def _filter_signals(signals: Sequence[StockSignal], target_codes: frozenset[str] | None) -> list[StockSignal]:
        normalized = [signal for signal in signals if signal.normalized_code()]
        if target_codes is None:
            return normalized
        return [signal for signal in normalized if signal.normalized_code() in target_codes]

    def query_signals(self, policy: StockContextReadPolicy | None = None) -> list[StockSignal]:
        read_policy = policy or StockContextReadPolicy()
        if read_policy.target_codes is not None and not read_policy.target_codes:
            return []
        signals = [
            replace(signal, payload=dict(signal.payload or {}))
            for signal in self._snapshot.direct_signals
            if read_policy.includes_source(signal.source_tab)
        ]
        for source in DEFAULT_SOURCE_ORDER:
            if not read_policy.includes_source(source) or source in self._snapshot.direct_source_keys:
                continue
            signals.extend(self._source_signals(source, read_policy))
        return self._filter_signals(signals, read_policy.target_codes)

    def query_by_code(self, policy: StockContextReadPolicy | None = None) -> dict[str, list[StockSignal]]:
        return index_signals_by_code(self.query_signals(policy))

    def query_watchlist_radar(
        self,
        *,
        target_codes: Sequence[str] | set[str] | None = None,
        include_source_cache_fallback: bool = True,
        allow_lhb_cache_compute: bool = False,
    ) -> tuple[dict, dict, dict, dict, dict, Any]:
        policy = StockContextReadPolicy.build(
            include_cache_fallback=False,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            target_codes=target_codes,
            sources=set(RADAR_SOURCE_KEYS),
        )
        signals = self.query_signals(policy)
        return build_watchlist_radar_data(
            signals,
            rps_bundle=self._snapshot.rps_bundle,
            target_codes=policy.target_codes,
        )

    def query_kline_signals(self, code: str) -> list[StockSignal]:
        policy = StockContextReadPolicy.build(
            target_codes={str(code or "").strip()},
            sources=set(KLINE_SOURCE_KEYS),
        )
        return self.query_signals(policy)


__all__ = ["GENERAL_STOCK_CONTEXT_SOURCE_KEYS", "StockContextQueryService"]
