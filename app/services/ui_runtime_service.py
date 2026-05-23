# -*- coding: utf-8 -*-
"""Compatibility barrel for legacy UI runtime imports.

New UI code should import one of the narrower app.services.ui_* modules instead
of this broad migration barrel. This file remains so older call sites can move
in small, behavior-preserving slices.
"""

from __future__ import annotations

from app.services.ui_config_service import TableViewStateStore, app_config
from app.services.ui_earnings_calendar_service import (
    EarningsCalendarEvent,
    GlobalEarningsCalendarService,
    event_calendar_date,
    events_by_date,
    is_yfinance_date_conflict_event,
    is_yfinance_estimate_event,
    sorted_events,
)
from app.services.ui_earnings_service import EarningsScheduler
from app.services.ui_event_service import domain_events, ui_signal_hub, ui_signals
from app.services.ui_fund_holdings_service import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    fund_holdings_store,
    fund_holdings_sync_service,
)
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_navigation_service import ExternalTerminalNavigator
from app.services.ui_quote_service import (
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    get_missing_a_share_finance_codes,
    is_a_share_code,
    merge_quote_snapshot_inplace,
    publish_rt_quotes,
    resolve_quote_metrics,
)
from app.services.ui_task_service import (
    CENTRAL_QUOTES_POLL,
    NETWORK_FORCE_RECONNECT,
    NETWORK_GO_ONLINE,
    SHARED_MARKET_CAPS,
    WINDOW_F5_PRECOMPUTE,
    ProcessExecutionError,
    ProcessSubprocessError,
    ProcessTimeoutError,
    background_job_runner,
    build_domestic_process_env,
    run_process,
    task_id_of,
    task_registry,
    windows_no_window_creationflags,
    windows_no_window_kwargs,
)
from app.services.ui_watchlist_service import WatchlistViewModel, watchlist_vm

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
    "is_yfinance_date_conflict_event",
    "is_yfinance_estimate_event",
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
    "windows_no_window_kwargs",
    "windows_no_window_creationflags",
]
