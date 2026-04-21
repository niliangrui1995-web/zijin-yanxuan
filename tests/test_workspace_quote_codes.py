# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

import ui.workspaces.classic_workspace as classic_workspace_module
from ui.workspaces.classic_workspace import ClassicWorkspace


def _make_workspace(*, tabs=None, engine=None):
    ordered_tabs = dict(tabs or {})
    workspace = SimpleNamespace(engine=engine)
    workspace.get_tab = lambda key: ordered_tabs.get(key)
    workspace.iter_tabs = lambda: [tab for tab in ordered_tabs.values() if tab is not None]
    return workspace


def _make_rows_tab(rows):
    return SimpleNamespace(get_row_data=lambda current_model=None: list(rows or []))


def _make_quote_tab(codes):
    return SimpleNamespace(get_realtime_quote_codes=lambda: set(codes or set()))


def test_workspace_collects_a_share_quote_codes_from_public_tab_apis():
    workspace = _make_workspace(
        tabs={
            "scan": _make_quote_tab({"000001"}),
            "rt_monitor": _make_quote_tab({"600000"}),
            "watchlist": _make_quote_tab({"000001", "300001"}),
            "foreign_block": _make_quote_tab({"600000", "688001"}),
            "na_daily": _make_quote_tab({"002415"}),
            "earnings": _make_quote_tab({"300001"}),
            "lhb": _make_quote_tab({"601318"}),
            "fund_holdings": _make_quote_tab({"002594", "00700"}),
        }
    )

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "600000", "300001", "688001", "002415", "601318"}


def test_workspace_primes_watchlist_with_public_startup_hook():
    called = []
    workspace = _make_workspace(
        tabs={
            "watchlist": SimpleNamespace(prime_startup_state=lambda: called.append("watchlist")),
        }
    )

    ClassicWorkspace.schedule_watchlist_special_quotes(workspace, task_manager=None)

    assert called == ["watchlist"]


def test_workspace_collects_structured_watchlist_radar_metrics():
    workspace = _make_workspace(
        engine=SimpleNamespace(get_precomputed_rps=lambda: {"cached": True}),
        tabs={
            "na_daily": _make_rows_tab([]),
            "foreign_block": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "交易详情": "买入",
                        "买方营业部": "机构专用",
                        "卖方营业部": "",
                        "成交金额(万元)": 2709,
                    }
                ]
            ),
            "earnings": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "环比%": 32.5,
                    }
                ]
            ),
            "lhb": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "_最近上榜_raw": "20260420",
                        "上榜净买额(万)": 1200,
                        "机构净买(万)": 800,
                        "外资净买(万)": -150,
                    }
                ]
            ),
        },
    )

    na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle = ClassicWorkspace.collect_watchlist_radar_data(
        workspace
    )

    assert na_data == {}
    assert na_subsector_data == {}
    assert rps_bundle == {"cached": True}
    assert block_data["300750"]["text"] == "机构专用买入2709万"
    assert block_data["300750"]["amount_wan"] == 2709
    assert earn_data["300750"]["text"] == "32.5%"
    assert earn_data["300750"]["qoq_pct"] == 32.5
    assert lhb_data["300750"]["text"] == "04-20 | 净买1200万 | 机构净买800万 | 外资净卖150万"
    assert lhb_data["300750"]["net_wan"] == 1200


def test_workspace_refreshes_all_tabs_after_f5():
    calls = []
    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "lhb": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb")),
        "na_daily": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("na_daily")),
        "asian_market": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("asian")),
        "rt_monitor": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("rt")),
        "foreign_block": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("foreign")),
        "fund_holdings": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("fund")),
        "earnings": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("earnings")),
        "scan": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("scan")),
        "system_log": SimpleNamespace(),
    }
    workspace = _make_workspace(tabs=tabs)
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


def test_workspace_defers_heavy_tab_autoload(monkeypatch):
    ctor_kwargs = {}

    def _make_tab(name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                ctor_kwargs[name] = dict(kwargs)

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "RtMonitorTab", _make_tab("rt_monitor"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        assert ctor_kwargs["lhb"]["autoload_pool"] is False
        assert ctor_kwargs["fund_holdings"]["autoload"] is False
        assert "autoload_pool" not in ctor_kwargs["watchlist"]
        assert "autoload" not in ctor_kwargs["watchlist"]
    finally:
        workspace.deleteLater()


def test_workspace_shutdown_continues_after_tab_failure():
    calls = []

    class _BrokenTab:
        def shutdown(self):
            calls.append("broken")
            raise RuntimeError("boom")

    workspace = _make_workspace(
        tabs={
            "broken": _BrokenTab(),
            "good": SimpleNamespace(shutdown=lambda: calls.append("good")),
        }
    )

    ClassicWorkspace.shutdown(workspace)

    assert calls == ["broken", "good"]
