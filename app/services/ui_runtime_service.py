# -*- coding: utf-8 -*-
"""Stable app-layer runtime entrypoints for UI modules.

UI code should depend on this module instead of importing domains/infra/core
runtime helpers directly. The goal is to keep the UI side on app-owned
contracts while the underlying implementations continue to migrate.
"""

from __future__ import annotations

from core.app_config import app_config
from core.background_job_runner import background_job_runner
from domains.earnings import EarningsScheduler
from domains.fund_holdings import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    fund_holdings_store,
    fund_holdings_sync_service,
)
from domains.global_earnings_calendar import (
    EarningsCalendarEvent,
    GlobalEarningsCalendarService,
    event_calendar_date,
    events_by_date,
    sorted_events,
)
from domains.market_calendar import MarketCalendar
from domains.quotes import (
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    get_missing_a_share_finance_codes,
    is_a_share_code,
    merge_quote_snapshot_inplace,
    publish_rt_quotes,
    resolve_quote_metrics,
)
from domains.runtime import domain_events
from domains.watchlist import WatchlistViewModel, watchlist_vm
from infra.events import ui_signal_hub
from infra.navigation import ExternalTerminalNavigator
from infra.settings import TableViewStateStore
from infra.tasks import (
    CENTRAL_QUOTES_POLL,
    NETWORK_FORCE_RECONNECT,
    NETWORK_GO_ONLINE,
    SHARED_MARKET_CAPS,
    WINDOW_F5_PRECOMPUTE,
    ProcessExecutionError,
    ProcessSubprocessError,
    ProcessTimeoutError,
    build_domestic_process_env,
    run_process,
    task_id_of,
    task_registry,
    windows_no_window_creationflags,
)

ui_signals = ui_signal_hub

__all__ = [
    "CENTRAL_QUOTES_POLL",
    "EarningsCalendarEvent",
    "EarningsScheduler",
    "ExternalTerminalNavigator",
    "GlobalEarningsCalendarService",
    "MarketCalendar",
    "NETWORK_FORCE_RECONNECT",
    "NETWORK_GO_ONLINE",
    "ProcessExecutionError",
    "ProcessSubprocessError",
    "ProcessTimeoutError",
    "QFII_CAPITAL_ATTRIBUTE_CLIENT",
    "QFII_CAPITAL_ATTRIBUTE_SELF_OWNED",
    "QFII_CAPITAL_ATTRIBUTE_UNMARKED",
    "SHARED_MARKET_CAPS",
    "SUBJECT_QFII",
    "SUBJECT_RUIYUAN",
    "TableViewStateStore",
    "WINDOW_F5_PRECOMPUTE",
    "WatchlistViewModel",
    "app_config",
    "background_job_runner",
    "build_domestic_process_env",
    "build_finance_quote_payload",
    "coerce_number",
    "domain_events",
    "event_calendar_date",
    "events_by_date",
    "enrich_quotes_with_finance",
    "fund_holdings_store",
    "fund_holdings_sync_service",
    "get_missing_a_share_finance_codes",
    "is_a_share_code",
    "merge_quote_snapshot_inplace",
    "publish_rt_quotes",
    "resolve_quote_metrics",
    "run_process",
    "sorted_events",
    "task_id_of",
    "task_registry",
    "ui_signal_hub",
    "ui_signals",
    "watchlist_vm",
    "windows_no_window_creationflags",
]
