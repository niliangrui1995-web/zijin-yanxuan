# -*- coding: utf-8 -*-
from ui.tabs import fund_holdings_tab as fund_holdings_module


class _DummyProvider:
    pass


def test_fund_holdings_tab_reload_triggers_quote_refresh(monkeypatch):
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "get_latest_quarter_map",
        lambda: {"QFII": "2025Q4", "007119": "2025Q4"},
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "get_latest_sync_map",
        lambda: {},
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "query_change_rows",
        lambda: [
            {
                "subject_code": "QFII",
                "subject_name": "QFII",
                "quarter_key": "2025Q4",
                "compare_quarter_key": "2025Q3",
                "change_type": "增持",
                "ratio_label": "持股比例",
                "curr_ratio_pct": 1.2,
                "prev_ratio_pct": 1.0,
                "delta_ratio_pct": 0.2,
                "curr_hold_num_shares": 1_500_000,
                "prev_hold_num_shares": 1_000_000,
                "delta_hold_num_shares": 500_000,
                "curr_hold_market_value_cny": 15_000_000,
                "prev_hold_market_value_cny": 10_000_000,
                "delta_hold_market_value_cny": 5_000_000,
                "holders_count": 1,
                "stock_code": "000001",
                "stock_name": "平安银行",
            }
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "list_subjects",
        lambda: [{"subject_code": "QFII", "subject_name": "QFII"}],
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "list_quarters",
        lambda: ["2025Q4"],
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    refresh_calls = []

    def _fake_refresh(self, current_model=None, force_quotes=False, quote_task_id=None):
        refresh_calls.append((current_model, force_quotes, quote_task_id))

    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        _fake_refresh,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert len(refresh_calls) == 1
        assert refresh_calls[0][0] is tab.model
        assert refresh_calls[0][1] is False
        assert refresh_calls[0][2] == "fund_holdings_quotes"
    finally:
        tab.deleteLater()
