# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from ui.tabs import fund_holdings_tab as fund_holdings_module
from ui.theme import theme_manager


class _DummyProvider:
    pass


class _FakeSettings:
    def __init__(self):
        self._values = {}
        self.synced = False

    def value(self, key, default=None, type=None):
        value = self._values.get(key, default)
        if value is None or type is None:
            return value
        if type is list:
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            return [value]
        try:
            return type(value)
        except (TypeError, ValueError):
            return default

    def setValue(self, key, value):
        self._values[key] = value

    def contains(self, key):
        return key in self._values

    def sync(self):
        self.synced = True
        return None


class _CountingFundHoldingsFilterProxy(fund_holdings_module.FundHoldingsFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.invalidate_count = 0

    def invalidateFilter(self):  # noqa: N802 - Qt API naming
        self.invalidate_count += 1
        return super().invalidateFilter()


def _build_change_row(
    *,
    subject_code: str,
    subject_name: str,
    quarter_key: str,
    compare_quarter_key: str,
    change_type: str,
    stock_code: str,
    stock_name: str,
    capital_attribute: str = "",
):
    return {
        "subject_code": subject_code,
        "subject_name": subject_name,
        "capital_attribute": capital_attribute,
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


def test_fund_holdings_filter_state_batches_invalidations():
    proxy = _CountingFundHoldingsFilterProxy()

    proxy.set_filter_state(
        subject_names={"A", ""},
        capital_attributes={"owned"},
        quarter_keys={"2025Q4"},
        change_types={"new"},
        latest_only=False,
        filter_text="  Ping An  ",
    )
    assert proxy.invalidate_count == 1
    assert proxy._subject_names == {"A"}
    assert proxy._filter_text == "ping an"

    proxy.set_filter_state(
        subject_names={"A"},
        capital_attributes={"owned"},
        quarter_keys={"2025Q4"},
        change_types={"new"},
        latest_only=False,
        filter_text="ping an",
    )
    assert proxy.invalidate_count == 1


def _setup_store(
    monkeypatch,
    rows,
    settings=None,
    concept_map=None,
    sync_map=None,
    *,
    ai_codes=None,
    patch_local_snapshot: bool = True,
):
    settings = settings or _FakeSettings()
    concept_map = concept_map or {}
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "get_latest_quarter_map",
        lambda: {"QFII": "2025Q4", "007119": "2025Q4"},
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "get_latest_sync_map",
        lambda: dict(sync_map or {}),
    )

    def _query_change_rows(*, quarter_keys=None):
        if quarter_keys is None:
            return rows
        selected = {
            str(quarter_key or "").strip() for quarter_key in (quarter_keys or []) if str(quarter_key or "").strip()
        }
        return [row for row in rows if str(row.get("quarter_key") or "").strip() in selected]

    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "query_change_rows",
        _query_change_rows,
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
    if ai_codes is None:
        ai_codes = {
            str(row.get("stock_code") or "").strip()
            for row in rows
            if str(row.get("stock_code") or "").strip()
        }
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_stock_universe_provider",
        staticmethod(lambda: set(ai_codes or set())),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_create_settings",
        staticmethod(lambda: settings),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_chain_context_provider",
        staticmethod(lambda: dict(concept_map)),
        raising=False,
    )
    if patch_local_snapshot:
        monkeypatch.setattr(
            fund_holdings_module.FundHoldingsTab,
            "_apply_quote_store_snapshot",
            lambda self, current_model=None: None,
            raising=False,
        )
        monkeypatch.setattr(
            fund_holdings_module.FundHoldingsTab,
            "refresh_table_from_latest_snapshot",
            lambda self, current_model=None, *, async_local=True: None,
            raising=False,
        )
    return settings


def _visible_codes(tab):
    codes = []
    for row_index in range(tab.proxy_model.rowCount()):
        proxy_index = tab.proxy_model.index(row_index, 0)
        source_index = tab.proxy_model.mapToSource(proxy_index)
        codes.append(str(tab.model.get_row_data(source_index.row()).get("代码") or "").strip())
    return codes


def test_fund_holdings_tab_reload_uses_f5_quote_cache_only(monkeypatch):
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
        patch_local_snapshot=False,
    )
    from core.global_store import global_store

    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.5}},
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
        assert refresh_calls == []
        assert tab.model.get_row_data(0)["市价"] == "10.50"
    finally:
        tab.deleteLater()


def test_fund_holdings_data_lineage_reports_loaded_rows(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="increase",
                stock_code="000001",
                stock_name="Ping An Bank",
            )
        ],
        sync_map={"QFII": {"finished_at": "2026-04-30T20:30:00"}},
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        lineage = tab.get_data_lineage()

        assert lineage["key"] == "fund_holdings"
        assert lineage["source"] == "fund_holdings_store + local_quote_snapshot"
        assert lineage["status"] == "loaded"
        assert lineage["row_count"] == 1
        assert lineage["visible_row_count"] == 1
        assert lineage["updated_at"] == "2026-04-30T20:30:00"
        assert lineage["loaded_quarter_scope"] == "latest"
        assert lineage["latest_quarter"] == "2025Q4"
        assert lineage["triggered_network"] is False
        assert "data/vcp_hunter.db:fund holdings tables" in lineage["cache_refs"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_filters_rows_to_ai_industry_chain_pool(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="300308",
                stock_name="中际旭创",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="QFII",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="600000",
                stock_name="浦发银行",
            ),
        ],
        ai_codes={"300308"},
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert [row["代码"] for row in tab.model.row_data] == ["300308"]
        assert _visible_codes(tab) == ["300308"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_applies_latest_quotes_from_local_snapshot():
    model = type("Model", (), {"row_data": []})()
    calls = []

    class DummyTab:
        def __init__(self):
            self.model = model

        def _apply_quote_store_snapshot(self):
            calls.append("store")

    fund_holdings_module.FundHoldingsTab._apply_latest_quotes_from_store(DummyTab())

    assert calls == ["store"]


def test_fund_holdings_tab_local_snapshot_fills_market_fields_without_realtime(monkeypatch, qt_application):
    from core.global_store import global_store
    from ui.models.table_models import StockTableModel
    from ui.tabs.base_stock_tab import BaseStockTab

    code_key = "\u4ee3\u7801"
    name_key = "\u540d\u79f0"
    price_key = "\u5e02\u4ef7"
    pct_key = "\u6da8\u5e45%"
    cap_key = "\u5e02\u503c"

    class OfflineProvider:
        def __init__(self):
            self.offline_calls = []

        def _build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 10.5, "last_close": 10.0}}

        def fetch_realtime_quotes_batch(self, _codes):
            raise AssertionError("fund holdings should not fetch realtime quotes")

    class DummyFundTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = StockTableModel([code_key, name_key, price_key, pct_key, cap_key])

    provider = OfflineProvider()
    tab = DummyFundTab(provider)
    tab.model.update_data([{code_key: "000001", name_key: "平安银行", price_key: "--", pct_key: "--", cap_key: "--"}])
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        lambda codes: {"000001": {"zongguben": 1_000_000_000}} if codes == ["000001"] else {},
        raising=False,
    )

    global_store.reset_quotes()
    try:
        tab.refresh_table_from_latest_snapshot(async_local=False)

        row = tab.model.row_data[0]
        assert provider.offline_calls == [["000001"]]
        assert row[price_key] == "10.50"
        assert round(float(row[pct_key]), 2) == 5.0
        assert row[cap_key] == "105亿"
    finally:
        global_store.reset_quotes()
        tab.deleteLater()


def test_fund_holdings_tab_does_not_schedule_late_local_quote_after_delete(monkeypatch):
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

    class _OfflineQuoteProvider:
        def _build_offline_quotes(self, codes):
            raise AssertionError("local quote snapshot should not be primed")

    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_load_cached_finance_snapshot",
        staticmethod(
            lambda codes: (
                {
                    "000001": {
                        "zongguben": 1_000_000_000,
                        "market_cap": 10_000_000_000,
                        "price_base": 10.0,
                    }
                }
                if codes == ["000001"]
                else {}
            )
        ),
        raising=False,
    )
    captured_tasks = []

    def _capture_run_in_background(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        captured_tasks.append((fn, on_success, on_error))
        return task_id or "captured"

    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        _capture_run_in_background,
        raising=False,
    )

    spy = QSignalSpy(event_bus.sig_rt_quotes)
    tab = fund_holdings_module.FundHoldingsTab(_OfflineQuoteProvider())
    assert captured_tasks == []

    tab.deleteLater()
    assert len(spy) == 0


def test_fund_holdings_show_runtime_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "screenshot"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not fund_holdings_module.FundHoldingsTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert fund_holdings_module.FundHoldingsTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_fund_holdings_tab_ignores_cache_reload_after_delete(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    monkeypatch.setattr(
        tab,
        "_apply_latest_quotes_from_store",
        lambda: calls.append("quotes"),
        raising=False,
    )
    monkeypatch.setattr(
        tab,
        "_update_status_summary",
        lambda: calls.append("status"),
        raising=False,
    )

    tab.deleteLater()
    tab._on_cache_reload_completed()

    assert calls == []


def test_fund_holdings_tab_delete_later_stops_view_state_timer_without_auto_timer(monkeypatch):
    _setup_store(monkeypatch, [])
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab._schedule_view_state_save()
        assert not hasattr(tab, "_daily_auto_sync_timer")
        assert not hasattr(tab, "_daily_auto_sync_initial_check_timer")
        assert tab._view_state_save_timer.isActive() is True

        tab.deleteLater()

        assert tab._view_state_save_timer.isActive() is False
        tab = None
    finally:
        if tab is not None:
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
        assert "占比变化" not in tab.columns
        assert "上期占比" not in tab.columns
        assert "对比季度" not in tab.columns
        assert "占比口径" not in tab.columns
        assert "本期持股(万股)" not in tab.columns
        assert "上期持股(万股)" not in tab.columns
        assert "持股变化(万股)" not in tab.columns
        assert "本期持股" in tab.columns
        assert "上期持股" in tab.columns
        assert "持股变化" in tab.columns
        assert "持有家数" not in tab.columns
        assert "概念板块" in tab.columns
        assert tab.model.get_row_data(0)["市价"] == "--"
        assert tab.model.get_row_data(0)["本期持股"] == "150.00"
        assert tab.model.get_row_data(0)["上期持股"] == "100.00"
        assert tab.model.get_row_data(0)["持股变化"] == "+50.00"
        assert tab.model.get_row_data(0)["概念板块"] == "--"
        assert tab.model.get_row_data(0)["主体"] == "阿布达比投资局"
    finally:
        tab.deleteLater()


def test_fund_holdings_toolbar_exposes_accessible_filter_names(monkeypatch):
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
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.cmb_subject.accessibleName() == "主体筛选"
        assert tab.cmb_capital_attribute.accessibleName() == "资金属性筛选"
        assert tab.btn_quarter.accessibleName() == "季度筛选"
        assert tab.btn_change.accessibleName() == "变动类型筛选"
        assert tab.search_box.accessibleName() == "基金持仓筛选"
        assert tab.btn_update.accessibleName() == "更新基金持仓数据库"
    finally:
        tab.deleteLater()


def test_fund_holdings_detail_columns_are_muted_and_new_add_types_use_watchlist_accent():
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab.model.update_data(
            [
                {
                    "代码": "300308",
                    "名称": "中际旭创",
                    "市价": "128.50",
                    "涨幅%": "2.30",
                    "市值": "1200亿",
                    "主体": "睿远成长价值混合A",
                    "资金属性": "自有资金",
                    "季度": "2026Q1",
                    "变化类型": "新进",
                    "本期占比": "1.20%",
                    "本期持股": "150.00万",
                    "上期持股": "--",
                    "持股变化": "+150.00万",
                    "概念板块": "CPO",
                },
                {
                    "代码": "688498",
                    "名称": "源杰科技",
                    "市价": "86.00",
                    "涨幅%": "-1.20",
                    "市值": "320亿",
                    "主体": "QFII",
                    "资金属性": "客户资金",
                    "季度": "2026Q1",
                    "变化类型": "增持",
                    "本期占比": "0.80%",
                    "本期持股": "80.00万",
                    "上期持股": "50.00万",
                    "持股变化": "+30.00万",
                    "概念板块": "光模块",
                },
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "市价": "10.00",
                    "涨幅%": "0.00",
                    "市值": "1900亿",
                    "主体": "QFII",
                    "资金属性": "--",
                    "季度": "2026Q1",
                    "变化类型": "减持",
                    "本期占比": "0.50%",
                    "本期持股": "30.00万",
                    "上期持股": "60.00万",
                    "持股变化": "-30.00万",
                    "概念板块": "银行",
                },
            ]
        )

        muted = QColor(theme_manager.get("TEXT_MUTED")).name()
        for header in ["主体", "资金属性", "季度", "本期占比", "本期持股", "上期持股", "持股变化", "概念板块"]:
            idx = tab.model.index(0, tab.model.headers.index(header))
            assert tab.model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == muted

        accent = QColor(theme_manager.get("BRAND_HOVER")).name()
        change_col = tab.model.headers.index("变化类型")
        assert tab.model.data(tab.model.index(0, change_col), Qt.ItemDataRole.ForegroundRole).name() == accent
        assert tab.model.data(tab.model.index(1, change_col), Qt.ItemDataRole.ForegroundRole).name() == accent
        assert tab.model.data(tab.model.index(2, change_col), Qt.ItemDataRole.ForegroundRole).name() != muted
        assert tab.model.data(tab.model.index(2, change_col), Qt.ItemDataRole.ForegroundRole).name() != accent
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_shows_ai_chain_context_column(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="阿布达比投资局",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="300308",
                stock_name="中际旭创",
            )
        ],
        concept_map={"300308": "光模块 | 800G"},
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.model.get_row_data(0)["概念板块"] == "光模块 | 800G"
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_loads_latest_quarter_before_all_quarters_on_demand(monkeypatch):
    rows = [
        _build_change_row(
            subject_code="QFII",
            subject_name="QFII",
            quarter_key="2025Q3",
            compare_quarter_key="2025Q2",
            change_type="增持",
            stock_code="000001",
            stock_name="平安银行",
        ),
        _build_change_row(
            subject_code="QFII",
            subject_name="QFII",
            quarter_key="2025Q4",
            compare_quarter_key="2025Q3",
            change_type="增持",
            stock_code="000002",
            stock_name="万科A",
        ),
    ]
    _setup_store(monkeypatch, rows)

    query_calls = []

    def _query_change_rows(*, quarter_keys=None):
        query_calls.append(None if quarter_keys is None else set(quarter_keys))
        if quarter_keys is None:
            return sorted(rows, key=lambda row: str(row.get("quarter_key") or ""), reverse=True)
        return sorted(
            [row for row in rows if str(row.get("quarter_key") or "").strip() in set(quarter_keys)],
            key=lambda row: str(row.get("quarter_key") or ""),
            reverse=True,
        )

    monkeypatch.setattr(fund_holdings_module.fund_holdings_store, "query_change_rows", _query_change_rows)
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: on_success(fn()),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert query_calls[0] == {"2025Q4"}
        assert _visible_codes(tab) == ["000002"]

        tab._set_quarter_filter_state(all_quarters=True, apply=True)

        assert query_calls[-1] is None
        assert _visible_codes(tab) == ["000002", "000001"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_centers_header_alignment(monkeypatch):
    _setup_store(monkeypatch, [])
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        alignment = tab.table.horizontalHeader().defaultAlignment()
        assert alignment & fund_holdings_module.Qt.AlignmentFlag.AlignHCenter
        assert alignment & fund_holdings_module.Qt.AlignmentFlag.AlignVCenter
    finally:
        tab.deleteLater()


def test_fund_holdings_kline_uses_clicked_duplicate_row(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2026Q1",
                compare_quarter_key="2025Q4",
                change_type="增持",
                stock_code="300308",
                stock_name="中际旭创",
            ),
            _build_change_row(
                subject_code="MS",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                quarter_key="2026Q1",
                compare_quarter_key="2025Q4",
                change_type="增持",
                stock_code="300308",
                stock_name="中际旭创",
            ),
        ],
    )
    monkeypatch.setattr(
        fund_holdings_module.fund_holdings_store,
        "get_latest_quarter_map",
        lambda: {"007119": "2026Q1", "MS": "2026Q1"},
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    spy = QSignalSpy(event_bus.sig_show_kline_with_list)
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        proxy_index = tab.proxy_model.index(1, 0)
        assert proxy_index.isValid()

        tab._on_double_click(proxy_index)

        assert len(spy) == 1
        code, code_list, current_idx = spy[0]
        assert code == "300308"
        assert current_idx == 1
        assert code_list[current_idx]["主体"] == "MORGAN STANLEY"
        assert code_list[current_idx]["主体原名"] == "MORGAN STANLEY & CO.INTERNATIONAL PLC"
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_defers_initial_load_when_autoload_disabled(monkeypatch):
    _setup_store(monkeypatch, [])
    scheduled = []
    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: scheduled.append(
            task_id or "scheduled"
        ),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        assert scheduled == []
        tab._ensure_initial_load_started()
        assert len(scheduled) == 1
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_prime_background_load_starts_deferred_load(monkeypatch):
    _setup_store(monkeypatch, [])
    scheduled = []
    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: scheduled.append(
            task_id or "scheduled"
        ),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab.prime_background_load()
        tab.prime_background_load()

        assert len(scheduled) == 1
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_applies_latest_quotes_without_realtime(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_apply_quote_store_snapshot",
        lambda self, current_model=None: calls.append("store"),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab._apply_latest_quotes_from_store()

        assert calls == ["store"]
    finally:
        tab.deleteLater()


def test_fund_holdings_apply_view_payload_primes_local_snapshot():
    calls = []

    class Model:
        row_data = []

        def update_data(self, rows, **_kwargs):
            self.row_data = list(rows)
            calls.append(("update", list(rows)))

    class DummyTab:
        model = Model()
        proxy_model = SimpleNamespace(rowCount=lambda: 1)
        table_state = SimpleNamespace(show_empty=lambda *_args: calls.append("empty"))

        def _refresh_filter_options(self):
            calls.append("filters")

        def _restore_view_state(self):
            calls.append("restore")

        def _apply_filters(self):
            calls.append("apply_filters")

        def _apply_latest_quotes_from_store(self):
            calls.append("store")

        def _prime_visible_local_quote_snapshot(self, current_model=None):
            calls.append(("local", current_model))
            return True

        def _update_status_summary(self):
            calls.append("status")

    tab = DummyTab()
    fund_holdings_module.FundHoldingsTab._apply_view_payload(
        tab,
        {
            "latest_quarter_map": {},
            "latest_sync_map": {},
            "concept_sector_cache": {},
            "view_rows": [{"代码": "000001"}],
        },
    )

    assert ("local", tab.model) in calls


def test_fund_holdings_tab_update_button_runs_sync_all_directly(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_run_sync_action",
        lambda self, label, runner: calls.append((label, runner)),
        raising=False,
    )
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.btn_update.menu() is None
        tab.btn_update.click()
        assert len(calls) == 1
        assert calls[0][0] == "全部更新"
        assert calls[0][1] == fund_holdings_module.fund_holdings_sync_service.sync_latest_all
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_runs_auto_sync_after_f5(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_run_sync_action",
        lambda self, label, runner: calls.append((label, runner)),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.run_auto_sync_after_f5() is True
        assert len(calls) == 1
        assert calls[0][0] == "F5后自动更新"
        assert calls[0][1] == fund_holdings_module.fund_holdings_sync_service.sync_latest_all
    finally:
        tab.deleteLater()


def test_fund_holdings_refresh_after_f5_schedules_auto_sync(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    scheduled = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_from_latest_snapshot",
        lambda self, current_model=None, *, async_local=True: calls.append(("snapshot", current_model, async_local)),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_run_sync_action",
        lambda self, label, runner: calls.append(("sync", label, runner)),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        assert tab.refresh_data_after_f5() is True
        assert calls == [("snapshot", tab.model, True)]
        assert scheduled[0][0] == tab._F5_AUTO_SYNC_DELAY_MS

        assert scheduled[0][1]() is True
        assert calls == [
            ("snapshot", tab.model, True),
            ("sync", "F5后自动更新", fund_holdings_module.fund_holdings_sync_service.sync_latest_all),
        ]
    finally:
        tab.deleteLater()


def test_fund_holdings_refresh_after_ai_chain_update_reloads_loaded_view(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_reload_from_db",
        lambda self: calls.append("reload"),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab._initial_load_started = True
        tab._concept_sector_cache["300750"] = "old"
        tab._ai_chain_context_map = {"300750": "old"}

        assert tab.refresh_data_after_ai_industry_chain_update() is True

        assert tab._concept_sector_cache == {}
        assert tab._ai_chain_context_map is None
        assert calls == ["reload"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_emits_updated_after_sync_success(monkeypatch):
    _setup_store(monkeypatch, [])

    def _run_in_background(sync_callable, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        if callable(on_success):
            on_success({"message": "同步完成"})

    monkeypatch.setattr(fund_holdings_module.task_manager, "run_in_background", _run_in_background, raising=False)
    spy = QSignalSpy(event_bus.sig_fund_holdings_updated)

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab._run_sync_action("测试同步", fund_holdings_module.fund_holdings_sync_service.sync_latest_all)

        assert len(spy) == 1
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_has_no_daily_auto_timer(monkeypatch):
    _setup_store(monkeypatch, [])
    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        assert not hasattr(tab, "_daily_auto_sync_timer")
        assert not hasattr(tab, "_daily_auto_sync_initial_check_timer")
        assert not hasattr(tab, "_check_daily_auto_sync")
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_filters_ai_related_concepts_for_display():
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("DeepSeek") is True
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("CPO概念") is True
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("数据中心") is True
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("白酒概念") is False
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("海南自贸") is False
    assert fund_holdings_module.FundHoldingsTab._is_ai_related_concept("AI营销") is False
    assert fund_holdings_module.FundHoldingsTab._filter_ai_related_concepts(
        ["白酒概念", "DeepSeek", "CPO概念", "DeepSeek", "海南自贸"]
    ) == ["DeepSeek", "CPO"]
    assert fund_holdings_module.FundHoldingsTab._filter_ai_related_concepts(
        ["液冷服务", "CPO概念", "DeepSeek", "液冷服务"]
    ) == ["液冷", "CPO", "DeepSeek"]


def test_fund_holdings_tab_subject_filter_uses_holder_name(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="BARCLAYS BANK PLC",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="UBS AG",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000002",
                stock_name="万科A",
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
        assert tab.cmb_subject.option_values() == ["BARCLAYS", "UBS"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_subject_names_are_shortened_for_display_and_filter(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY&CO.INT ERNATI ONAL PLC",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="J.P.Morgan Secur ities PLC",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="CITIGROUP GLOBAL MARKETS LIMITED",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="持平",
                stock_code="000003",
                stock_name="中国宝安",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="GOLDMAN SACHS INTERNATIONAL",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="减持",
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
        assert tab.cmb_subject.option_values() == ["MORGAN STANLEY", "J.P.Morgan", "CITI", "GOLDMAN SACHS"]
        assert [tab.model.get_row_data(index)["主体"] for index in range(4)] == [
            "MORGAN STANLEY",
            "J.P.Morgan",
            "CITI",
            "GOLDMAN SACHS",
        ]
        assert tab.model.get_row_data(0)["主体原名"] == "MORGAN STANLEY&CO.INT ERNATI ONAL PLC"
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_subject_filter_supports_multi_select(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="BARCLAYS BANK PLC",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="UBS AG",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="持平",
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
        tab.cmb_subject.set_selected_values({"BARCLAYS", "睿远成长价值混合A"})
        assert tab.proxy_model.rowCount() == 2
        assert _visible_codes(tab) == ["000001", "000004"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_capital_attribute_filter_supports_multi_select(monkeypatch):
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                capital_attribute="自有资金",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                capital_attribute="未标注",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="持平",
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
        assert tab.cmb_subject.option_values() == ["MORGAN STANLEY", "睿远成长价值混合A"]
        assert tab.cmb_capital_attribute.option_values() == ["自有资金", "未标注"]
        assert tab.cmb_capital_attribute.option_labels() == ["自有资金", "--"]
        assert tab.model.get_row_data(1)["资金属性"] == "--"
        tab._set_quarter_filter_state(all_quarters=True, apply=True)
        tab.cmb_capital_attribute.set_selected_values({"自有资金"})
        assert tab.proxy_model.rowCount() == 1
        assert _visible_codes(tab) == ["000001"]
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_restores_legacy_subject_name_as_shortened_value(monkeypatch):
    settings = _FakeSettings()
    settings.setValue(
        "fund_holdings_view_state_v2/subject_names",
        ["MORGAN STANLEY & CO.INTERNATIONAL PLC"],
    )
    _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000004",
                stock_name="国华网安",
            ),
        ],
        settings=settings,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.cmb_subject.selected_values() == {"MORGAN STANLEY"}
        assert _visible_codes(tab) == ["000001"]
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


def test_fund_holdings_tab_restores_saved_view_state(monkeypatch):
    settings = _setup_store(
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
            ),
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2025Q3",
                compare_quarter_key="2025Q2",
                change_type="持平",
                stock_code="000002",
                stock_name="万科A",
            ),
            _build_change_row(
                subject_code="007119",
                subject_name="睿远成长价值混合A",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000004",
                stock_name="国华网安",
            ),
        ],
        settings=_FakeSettings(),
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        tab.cmb_subject.set_selected_values({"睿远成长价值混合A"})
        tab._set_quarter_filter_state(selected_quarters={"2025Q3", "2025Q4"}, apply=True)
        tab._set_change_filter_values({"新进", "持平"}, apply=True)
        tab.search_box.setText("0")
        code_col = tab.model.headers.index("代码")
        tab.table.sortByColumn(code_col, fund_holdings_module.Qt.SortOrder.DescendingOrder)
        tab._save_view_state()
        assert settings.value(tab._view_state_key("subject_name")) == "睿远成长价值混合A"
        assert settings.value(tab._view_state_key("subject_names")) == ["睿远成长价值混合A"]
    finally:
        tab.deleteLater()

    restored = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert restored.cmb_subject.selected_values() == {"睿远成长价值混合A"}
        assert restored.search_box.text() == "0"
        assert restored._selected_change_types() == {"新进", "持平"}
        assert restored._quarter_filter_state() == (False, {"2025Q3", "2025Q4"})
        assert restored.table.sorted_column() == code_col
        assert (
            restored.table.horizontalHeader().sortIndicatorOrder() == fund_holdings_module.Qt.SortOrder.DescendingOrder
        )
        assert _visible_codes(restored) == ["000004", "000002"]
        assert settings.value(restored._view_state_key("subject_name")) == "睿远成长价值混合A"
    finally:
        restored.deleteLater()


def test_fund_holdings_tab_restores_saved_capital_attribute_state(monkeypatch):
    settings = _setup_store(
        monkeypatch,
        [
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                capital_attribute="自有资金",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="增持",
                stock_code="000001",
                stock_name="平安银行",
            ),
            _build_change_row(
                subject_code="QFII",
                subject_name="MORGAN STANLEY & CO.INTERNATIONAL PLC",
                capital_attribute="未标注",
                quarter_key="2025Q4",
                compare_quarter_key="2025Q3",
                change_type="新进",
                stock_code="000002",
                stock_name="万科A",
            ),
        ],
        settings=_FakeSettings(),
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
        tab.cmb_capital_attribute.set_selected_values({"未标注"})
        tab._save_view_state()
        assert settings.value(tab._view_state_key("capital_attributes")) == ["未标注"]
    finally:
        tab.deleteLater()

    restored = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert restored.cmb_capital_attribute.selected_values() == {"未标注"}
        assert _visible_codes(restored) == ["000002"]
    finally:
        restored.deleteLater()
