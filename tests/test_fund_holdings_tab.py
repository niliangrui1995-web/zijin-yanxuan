# -*- coding: utf-8 -*-
from ui.tabs import fund_holdings_tab as fund_holdings_module


class _DummyProvider:
    pass


def _build_change_row(
    *,
    subject_code: str,
    subject_name: str,
    quarter_key: str,
    compare_quarter_key: str,
    change_type: str,
    stock_code: str,
    stock_name: str,
):
    return {
        "subject_code": subject_code,
        "subject_name": subject_name,
        "quarter_key": quarter_key,
        "compare_quarter_key": compare_quarter_key,
        "change_type": change_type,
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
        "stock_code": stock_code,
        "stock_name": stock_name,
    }


def _setup_store(monkeypatch, rows):
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
        lambda: rows,
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "list_subjects",
        lambda: [
            {"subject_code": "QFII", "subject_name": "QFII"},
            {"subject_code": "007119", "subject_name": "睿远成长价值混合A"},
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "list_quarters",
        lambda: sorted({str(row.get("quarter_key") or "").strip() for row in rows}, reverse=True),
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )


def _visible_codes(tab):
    codes = []
    for row_index in range(tab.proxy_model.rowCount()):
        proxy_index = tab.proxy_model.index(row_index, 0)
        source_index = tab.proxy_model.mapToSource(proxy_index)
        codes.append(str(tab.model.get_row_data(source_index.row()).get("代码") or "").strip())
    return codes


def test_fund_holdings_tab_reload_triggers_quote_refresh(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            )
        ],
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


def test_fund_holdings_tab_hides_market_value_delta_columns(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="阿布达比投资局",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            )
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert "本期持仓(万元)" not in tab.columns
        assert "上期持仓(万元)" not in tab.columns
        assert "持仓变化(万元)" not in tab.columns
        assert tab.model.get_row_data(0)["主体"] == "阿布达比投资局"
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_change_filter_supports_multi_select(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="持平",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000004",
                stock_name="国华网安",
            ),
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        tab._set_quarter_filter_state(all_quarters=True, apply=True)
        tab._set_change_filter_values({"新进", "持平"}, apply=True)

        assert tab.proxy_model.rowCount() == 2
        assert _visible_codes(tab) == ["000001", "000002"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_quarter_filter_supports_multi_select(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q2",
                compare_quarter_key="2025Q1",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q3",
                compare_quarter_key="2025Q2",
                change_type="增持",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000004",
                stock_name="国华网安",
            ),
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        tab._set_quarter_filter_state(selected_quarters={"2025Q3", "2025Q4"}, apply=True)

        assert tab.proxy_model.rowCount() == 2
        assert _visible_codes(tab) == ["000002", "000004"]
    finally:
        tab.deleteLater()
