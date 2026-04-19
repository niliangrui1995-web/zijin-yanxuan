# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui.workspaces.classic_workspace import ClassicWorkspace


def test_workspace_collects_a_share_quote_codes_from_all_tabs():
    workspace = SimpleNamespace(
        tab_scan=SimpleNamespace(source_model=SimpleNamespace(row_data=[{"代码": "000001"}, {"代码": "abc"}])),
        tab_rt=SimpleNamespace(source_model=SimpleNamespace(row_data=[{"代码": "600000"}])),
        tab_watchlist=SimpleNamespace(model=SimpleNamespace(row_data=[{"代码": "000001"}, {"代码": "300001"}])),
        tab_foreign_block=SimpleNamespace(model=None, _block_trade_codes=["600000", "688001", "bad"]),
        tab_fund_holdings=SimpleNamespace(model=SimpleNamespace(row_data=[{"代码": "002594"}, {"代码": "00700"}])),
        tab_na_daily=SimpleNamespace(model=SimpleNamespace(row_data=[{"代码": "002415"}])),
        tab_earnings=SimpleNamespace(model=SimpleNamespace(row_data=[{"代码": "300001"}])),
        tab_lhb=SimpleNamespace(model=SimpleNamespace(row_data=[{"代码": "601318"}])),
    )

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "600000", "300001", "688001", "002415", "601318"}


def test_workspace_primes_watchlist_with_public_startup_hook():
    called = []
    workspace = SimpleNamespace(
        tab_watchlist=SimpleNamespace(prime_startup_state=lambda: called.append("watchlist")),
    )

    ClassicWorkspace.schedule_watchlist_special_quotes(workspace, task_manager=None)

    assert called == ["watchlist"]


def test_workspace_refreshes_all_tabs_after_f5():
    calls = []
    workspace = SimpleNamespace(
        tab_watchlist=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        tab_lhb=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb")),
        tab_na_daily=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("na_daily")),
        tab_asian_market=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("asian")),
        tab_rt=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("rt")),
        tab_foreign_block=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("foreign")),
        tab_fund_holdings=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("fund")),
        tab_earnings=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("earnings")),
        tab_scan=SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("scan")),
        tab_log=SimpleNamespace(),
    )
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == [
        "watchlist",
        "lhb",
        "na_daily",
        "asian",
        "rt",
        "foreign",
        "fund",
        "earnings",
        "scan",
    ]
