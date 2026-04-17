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
