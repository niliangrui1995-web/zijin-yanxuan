# -*- coding: utf-8 -*-
"""Explicit workspace/tab capability protocols.

The workspace layer should orchestrate tabs through public capabilities instead
of reaching into concrete widget internals such as ``table_scan`` or
``model.row_data``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ui.workspaces.stock_signal import StockSignal


@runtime_checkable
class TableCollectionCapability(Protocol):
    def iter_tables(self) -> list: ...


@runtime_checkable
class SnapshotRefreshCapability(Protocol):
    def refresh_table_from_latest_snapshot(self, current_model=None, *, async_local: bool = True) -> None: ...


@runtime_checkable
class PostF5DataRefreshCapability(Protocol):
    def refresh_data_after_f5(self) -> bool: ...


@runtime_checkable
class AIIndustryChainUpdateCapability(Protocol):
    def refresh_data_after_ai_industry_chain_update(self) -> bool: ...


@runtime_checkable
class PrimaryRowSelectionCapability(Protocol):
    def select_primary_row(self, index: int) -> bool: ...


@runtime_checkable
class CodeRowSelectionCapability(Protocol):
    def select_code_row(self, code: str) -> bool: ...


@runtime_checkable
class QuoteUniverseCapability(Protocol):
    def get_realtime_quote_codes(self) -> set[str]: ...


@runtime_checkable
class StockSignalSourceCapability(Protocol):
    def iter_stock_signals(self) -> list[StockSignal]: ...


@runtime_checkable
class ScanResultsCapability(Protocol):
    def get_scan_results(self) -> list[dict]: ...


@runtime_checkable
class ForeignKeywordCapability(Protocol):
    def get_foreign_keywords(self) -> list[str]: ...
