# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from ui.workspaces import stock_context_service as context_module


def _service(workspace=None):
    return context_module.StockContextService(workspace or SimpleNamespace())


def test_stock_context_low_level_normalization_and_block_trade_branches():
    service = _service()
    assert service._safe_float("1,234.5") == 1234.5
    assert service._safe_float("", 7) == 7.0
    assert service._safe_float("bad", 8) == 8.0
    assert service._compact_block_trade_branch("", ["高盛"]) == ""
    assert service._compact_block_trade_branch("高盛上海", ["高盛"]) == "高盛"
    assert service._compact_block_trade_branch("机构专用席位", []) == "机构专用"
    assert service._compact_block_trade_branch("普通", []) == ""

    build = service._build_watchlist_block_trade_signal
    assert build("买入", "高盛", "", 0, ["高盛"]) == ("", 0.0)
    assert build("买入", "高盛", "", 100, ["高盛"])[0] == "高盛买入100万"
    assert build("买入", "", "高盛", 100, ["高盛"])[0] == "高盛卖出100万"
    assert build("卖出", "", "高盛", 100, ["高盛"])[0] == "高盛卖出100万"
    assert build("卖出", "高盛", "", 100, ["高盛"])[0] == "高盛买入100万"
    assert build("", "same", "same", 100, [])[0] == "大宗对倒 100万"
    assert build("", "a", "b", 100, []) == ("", 0.0)


def test_stock_context_workspace_tab_iteration_and_row_readers():
    first = SimpleNamespace(get_watchlist_radar_rows=lambda: [{"x": 1}])
    second = SimpleNamespace(get_row_data=lambda: [{"x": 2}])
    workspace = SimpleNamespace(
        get_loaded_tab=lambda key: {"first": first, "duplicate": first, "second": second}.get(key),
        tab_specs=lambda: [
            {"key": "first"},
            {"key": ""},
            {"key": "duplicate"},
            {"key": "second"},
        ],
        iter_tabs=lambda: [None, first, SimpleNamespace()],
    )
    service = _service(workspace)
    assert service._get_tab("first") is first
    assert service._has_tab_key("first", first)
    assert not service._has_tab_key("")
    assert service._has_tab_key("second")
    items = list(service._iter_tabs_with_keys())
    assert [(key, item) for key, item in items[:2]] == [("first", first), ("second", second)]
    assert items[-1][0] == ""
    assert service._get_rows(first) == [{"x": 1}]
    assert service._get_rows(second) == [{"x": 2}]
    assert service._get_rows(SimpleNamespace()) == []

    fallback_workspace = SimpleNamespace(get_tab=lambda key: key, tab_specs="bad")
    fallback = _service(fallback_workspace)
    assert fallback._get_tab("x") == "x"
    assert fallback._tab_specs() == []
    assert list(fallback._iter_tabs_with_keys()) == []
    assert _service(SimpleNamespace())._get_tab("x") is None

    assert service._coerce_cache_rows(None) == []
    assert service._coerce_cache_rows([{"x": 1}, "bad"]) == [{"x": 1}]
    assert service._normalize_target_codes(None) == set()
    assert service._normalize_target_codes([" 1 ", "", None]) == {"1"}


def test_stock_context_earnings_normalizers_discovery_and_cache_rows(monkeypatch):
    service = _service()
    c = context_module
    assert service._normalize_earnings_code({c.RAW_STOCK_CODE: "1"}) == "000001"
    assert service._normalize_earnings_code({c.KEY_CODE: "ABC"}) == "ABC"
    assert service._earnings_report_type({c.RAW_DATA_TYPE: "quarter"}) == "quarter"
    assert service._earnings_report_type({c.KEY_REPORT_TYPE: "annual"}) == "annual"
    assert service._earnings_discovered_at({"discovered_at": "x"}, "f") == "x"
    assert service._earnings_discovered_at({}, "f") == "f"
    assert service._earnings_reveal_date({c.KEY_EARNINGS_MARK_DATE: "2026-01-01"}) == "2026-01-01"
    assert service._earnings_reveal_date({c.RAW_DISCLOSURE_DATE: "2026-01-02"}) == "2026-01-02"

    payload = {
        "records": [
            "bad",
            {c.RAW_STOCK_CODE: "", c.KEY_DISCOVERED_AT: "x"},
            {c.RAW_STOCK_CODE: "1", c.KEY_REPORT_PERIOD: "2026Q1", c.RAW_DATA_TYPE: "Q1", c.KEY_DISCOVERED_AT: "t1"},
            {c.RAW_STOCK_CODE: "2", c.KEY_REPORT_PERIOD: "", c.KEY_REPORT_TITLE: "", c.KEY_DISCOVERED_AT: ""},
        ]
    }
    monkeypatch.setattr(service, "_load_earnings_state_payload", lambda: (payload, "fallback"))
    lookup = service._earnings_discovery_lookup()
    assert lookup[("000001", "2026Q1", "Q1")] == "t1"
    assert lookup[("000001", "2026Q1", "")] == "t1"
    assert lookup[("000002", "", "")] == "fallback"
    assert service._earnings_lookup_discovery(lookup, code="000001", report_period="2026Q1", report_type="Q1") == "t1"
    assert (
        service._earnings_lookup_discovery(lookup, code="000001", report_period="2026Q1", report_type="other") == "t1"
    )
    assert service._earnings_lookup_discovery(lookup, code="none", report_period="", report_type="") == ""

    monkeypatch.setattr(service, "_load_earnings_state_payload", lambda: ({}, ""))
    assert service._load_earnings_cache_rows() == []

    raw_payload = {
        "records": [
            {
                c.KEY_CODE: "300308",
                c.KEY_DISCOVERED_AT: "2026-07-15T09:00:00",
                c.KEY_REVEAL_DATE: "2026-07-15",
            },
            {
                c.RAW_STOCK_CODE: "2",
                c.RAW_STOCK_NAME: "Name",
                c.RAW_QOQ_PCT: "12.3",
                c.RAW_DATA_TYPE: "半年报",
                c.RAW_DISCLOSURE_DATE: "2026-07-14",
            },
            {c.RAW_STOCK_CODE: ""},
        ]
    }
    monkeypatch.setattr(service, "_load_earnings_state_payload", lambda: (raw_payload, "state-time"))
    rows = service._load_earnings_cache_rows()
    assert len(rows) == 2
    assert rows[0][c.KEY_DISCOVERED_AT] == "2026-07-15T09:00:00"
    assert rows[1][c.KEY_CODE] == "000002"
    assert rows[1][c.KEY_NAME] == "Name"
    assert rows[1][c.KEY_REPORT_TYPE] == "半年报"
    assert rows[1][c.KEY_DISCOVERED_AT] == "state-time"


def test_stock_context_fund_formatters_and_store_row_filtering():
    from app.services.ui_fund_holdings_service import SUBJECT_QFII

    service = _service()
    assert service._format_fund_holding_pct("1.234") == "1.23%"
    assert service._format_fund_holding_pct("bad") == "--"
    assert service._format_fund_holding_amount(25000) == "+2.50"
    assert service._format_fund_holding_amount(-10000) == "-1.00"
    assert service._format_fund_holding_amount("bad") == "--"

    qfii = str(SUBJECT_QFII.get("subject_code") or "")
    latest = {qfii: "2026Q2", "fund": "2026Q1"}
    rows = service._format_fund_holding_store_rows(
        latest,
        [
            {"stock_code": "", "change_type": "新进"},
            {"stock_code": "1", "subject_code": "fund", "quarter_key": "2026Q1", "change_type": "减持"},
            {"stock_code": "2", "subject_code": "fund", "quarter_key": "old", "change_type": "增持"},
            {
                "stock_code": "3",
                "stock_name": "Three",
                "subject_code": qfii,
                "subject_name": "QFII",
                "quarter_key": "2026Q2",
                "change_type": "新进",
                "capital_attribute": "",
                "curr_ratio_pct": "1.2",
                "delta_hold_num_shares": 20000,
            },
            {
                "stock_code": "4",
                "stock_name": "Four",
                "subject_code": "fund",
                "subject_name": "Fund",
                "quarter_key": "2026Q1",
                "change_type": "增持",
                "capital_attribute": "long",
                "curr_ratio_pct": "bad",
                "delta_hold_num_shares": "bad",
            },
        ],
    )
    assert [row[context_module.KEY_CODE] for row in rows] == ["3", "4"]
    assert rows[0][context_module.KEY_CAPITAL_ATTRIBUTE] == ""
    assert rows[1][context_module.KEY_CAPITAL_ATTRIBUTE] == "long"
    assert rows[1][context_module.KEY_CURRENT_RATIO] == "--"


def test_stock_context_fund_row_sources_latest_quarters_and_signals(monkeypatch):
    c = context_module
    tab = SimpleNamespace(get_row_data=lambda: [{c.KEY_CODE: "tab"}])
    workspace = SimpleNamespace(
        get_loaded_tab=lambda key: tab if key == "fund_holdings" else None,
        tab_specs=lambda: [{"key": "fund_holdings"}],
    )
    service = _service(workspace)
    assert service._has_fund_holdings_tab()
    assert service._fund_holding_rows(target_codes=[]) == []

    service._fund_rows_loaded = True
    service._fund_rows_snapshot = [
        {c.KEY_CODE: "1", c.KEY_QUARTER: "2026Q1", c.KEY_SUBJECT_CODE: "a"},
        {c.KEY_CODE: "2", c.KEY_QUARTER: "2026Q2", c.KEY_SUBJECT_CODE: "a"},
    ]
    assert [row[c.KEY_CODE] for row in service._fund_holding_rows(target_codes=["2"])] == ["2"]
    assert len(service._cached_fund_holding_rows()) == 2

    service._fund_rows_loaded = False
    loaded = []
    monkeypatch.setattr(
        service,
        "_load_fund_holding_rows_snapshot",
        lambda **kwargs: loaded.append(kwargs) or [{c.KEY_CODE: "3"}],
    )
    assert service._fund_holding_rows(target_codes=["3"]) == [{c.KEY_CODE: "3"}]
    assert loaded == [{"stock_codes": {"3"}}]
    monkeypatch.setattr(service, "refresh_async_snapshots", lambda: loaded.append("refresh") or False)
    assert service._cached_fund_holding_rows(allow_async_refresh=True) == []
    assert loaded[-1] == "refresh"
    assert service._fund_holding_rows(allow_async_snapshot_refresh=False) == [{c.KEY_CODE: "tab"}]

    latest = service._latest_fund_holding_quarters(
        [
            {c.KEY_QUARTER: "", c.KEY_SUBJECT: "x"},
            {c.KEY_QUARTER: "2026Q1", c.KEY_SUBJECT: "x"},
            {c.KEY_QUARTER: "2026Q2", c.KEY_SUBJECT: "x"},
            {c.KEY_QUARTER: "2025Q4"},
        ]
    )
    assert latest == {"x": "2026Q2", "__all__": "2025Q4"}
    assert service._is_latest_fund_holding_row({"_is_latest_subject_quarter": True}, latest)
    assert not service._is_latest_fund_holding_row({c.KEY_QUARTER: ""}, latest)
    assert service._is_latest_fund_holding_row({c.KEY_QUARTER: "2026Q2", c.KEY_SUBJECT: "x"}, latest)

    monkeypatch.setattr(
        service,
        "_fund_holding_rows",
        lambda **_kwargs: [
            {},
            {c.KEY_CODE: "old", c.KEY_CHANGE_TYPE: "减持", c.KEY_QUARTER: "2026Q2"},
            {c.KEY_CODE: "stale", c.KEY_CHANGE_TYPE: "增持", c.KEY_QUARTER: "2025Q1", c.KEY_SUBJECT: "Fund"},
            {
                c.KEY_CODE: "ok",
                c.KEY_NAME: "Name",
                c.KEY_SUBJECT: "Fund",
                c.KEY_CAPITAL_ATTRIBUTE: "Long",
                c.KEY_QUARTER: "2026Q2",
                c.KEY_CHANGE_TYPE: "增持",
                c.KEY_CURRENT_RATIO: "1.2%",
                c.KEY_HOLDING_DELTA: "+2.0",
            },
        ],
    )
    signals = service._iter_fund_holdings_signals()
    assert [signal.code for signal in signals] == ["ok"]
    assert signals[0].summary == "Fund | Long | 增持 | 2026Q2 | 占比1.2% | 变化+2.0"


def test_stock_context_earnings_labels_and_lhb_signal_variants(monkeypatch):
    c = context_module
    labels = [
        ({c.KEY_REPORT_NAME: "custom"}, "custom"),
        ({c.KEY_REPORT_PERIOD: "2026Q1"}, "一季度"),
        ({c.KEY_REPORT_PERIOD: "2026-06-30"}, "半年报"),
        ({c.KEY_REPORT_PERIOD: "2026.09.30"}, "三季度"),
        ({c.KEY_REPORT_PERIOD: "2026/12/31"}, "年报"),
        ({c.KEY_REPORT_PERIOD: "special"}, "special"),
        ({}, ""),
    ]
    for row, expected in labels:
        assert context_module.StockContextService._earnings_report_label(row) == expected

    service = _service()
    monkeypatch.setattr(service, "_get_tab", lambda key: object() if key == "lhb" else None)
    monkeypatch.setattr(service, "_has_tab_key", lambda *_args: True)
    monkeypatch.setattr(
        service,
        "_get_rows",
        lambda _tab: [
            {},
            {
                c.KEY_CODE: "1",
                c.KEY_LAST_LISTED: "20260715",
                c.KEY_NET_WAN: -10,
                c.KEY_INST_WAN: -20,
                c.KEY_FOREIGN_WAN: -30,
            },
            {c.KEY_CODE: "1", c.KEY_LAST_LISTED: "duplicate"},
            {
                c.KEY_CODE: "2",
                c.KEY_LAST_LISTED: "2026-07-14",
                c.KEY_NET_WAN: 10,
                c.KEY_INST_WAN: 20,
                c.KEY_FOREIGN_WAN: 30,
            },
            {c.KEY_CODE: "3", c.KEY_LAST_LISTED: "raw"},
        ],
    )
    signals = service._iter_lhb_signals(include_cache_fallback=False)
    assert [signal.code for signal in signals] == ["1", "2", "3"]
    assert "净卖10万" in signals[0].summary
    assert "07-14" in signals[1].summary
    assert signals[2].summary.startswith("raw")


def test_stock_context_post_f5_defer_refresh_names_and_shutdown(monkeypatch):
    calls = []
    watchlist = SimpleNamespace(
        refresh_watchlist_names=lambda mapping: calls.append(("names", mapping)) or True,
        prime_startup_state=lambda: calls.append(("prime",)),
    )
    workspace = SimpleNamespace(get_loaded_tab=lambda key: watchlist if key == "watchlist" else None)
    service = _service(workspace)
    monkeypatch.setattr(context_module.time, "monotonic", lambda: 100.0)
    service.prepare_post_f5_refresh()
    assert service._should_defer_async_snapshots()
    monkeypatch.setattr(context_module.time, "monotonic", lambda: 200.0)
    assert not service._should_defer_async_snapshots()
    service._post_f5_snapshot_defer_until = "bad"
    assert not service._should_defer_async_snapshots()
    assert service.refresh_watchlist_names({"1": "A"})
    service.prime_watchlist_state()
    assert calls == [("names", {"1": "A"}), ("prime",)]

    service._fund_rows_loading = True
    service._lhb_rows_loading = True
    monkeypatch.setattr(service._task_lifecycle, "shutdown", lambda **kwargs: kwargs["timeout_ms"] == 123)
    assert service.shutdown(timeout_ms=123)
    assert service._shutdown
    assert not service._fund_rows_loading and not service._lhb_rows_loading
    assert not service.refresh_async_snapshots()
