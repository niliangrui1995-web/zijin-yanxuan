"""Narrow application boundary for the stock-context formatter used by UI orchestration."""

from domains.stock_context.signal_builders import format_fund_holding_store_rows

__all__ = ["format_fund_holding_store_rows"]
