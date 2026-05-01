# -*- coding: utf-8 -*-
from datetime import datetime

from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from ui.tabs import fund_holdings_tab as fund_holdings_module


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


def _setup_store(monkeypatch, rows, settings=None, concept_map=None):
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
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_create_settings",
        staticmethod(lambda: settings),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_get_concept_sector_text",
        lambda self, stock_code: concept_map.get(str(stock_code or "").strip(), "--"),
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


def test_fund_holdings_tab_reload_warms_local_snapshot_for_new_codes(monkeypatch):
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
            assert codes == ["000001"]
            return {
                "000001": {
                    "close": 10.5,
                    "last_close": 10.0,
                }
            }

    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_load_cached_finance_snapshot",
        staticmethod(
            lambda codes: {
                "000001": {
                    "zongguben": 1_000_000_000,
                    "market_cap": 10_000_000_000,
                    "price_base": 10.0,
                }
            }
            if codes == ["000001"]
            else {}
        ),
        raising=False,
    )
    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: on_success(fn()) if callable(on_success) else None,
        raising=False,
    )

    from core.global_store import global_store

    tab = fund_holdings_module.FundHoldingsTab(_OfflineQuoteProvider())
    try:
        latest = global_store.get_latest_quotes()
        assert latest["000001"]["close"] == 10.5
        assert latest["000001"]["zongguben"] == 1_000_000_000
        assert tab.model.get_row_data(0)["市价"] == "10.50"
        assert round(float(tab.model.get_row_data(0)["涨幅%"]), 2) == 5.0
        assert tab.model.get_row_data(0)["市值"] == "105亿"
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


def test_fund_holdings_tab_shows_concept_sector_column(monkeypatch):
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
        concept_map={"000001": "MSCI概念 | 互联金融"},
    )
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider())
    try:
        assert tab.model.get_row_data(0)["概念板块"] == "MSCI概念 | 互联金融"
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
        assert code_list[current_idx]["主体"] == "MORGAN STANLEY & CO.INTERNATIONAL PLC"
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_defers_initial_load_when_autoload_disabled(monkeypatch):
    _setup_store(monkeypatch, [])
    scheduled = []
    monkeypatch.setattr(
        fund_holdings_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: scheduled.append(task_id or "scheduled"),
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
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: scheduled.append(task_id or "scheduled"),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab.prime_background_load()
        tab.prime_background_load()

        assert len(scheduled) == 1
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_applies_latest_quotes_with_async_local(monkeypatch):
    _setup_store(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "refresh_table_from_latest_snapshot",
        lambda self, *args, **kwargs: calls.append(kwargs.get("async_local")),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        tab._apply_latest_quotes_from_store()

        assert calls == [True]
    finally:
        tab.deleteLater()


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


def test_fund_holdings_tab_daily_auto_sync_starts_once_after_2030(monkeypatch):
    settings = _setup_store(monkeypatch, [])
    monkeypatch.setattr(
        fund_holdings_module.MarketCalendar,
        "now",
        staticmethod(lambda _market="CN": datetime(2026, 4, 24, 20, 30)),
    )
    calls = []
    monkeypatch.setattr(
        fund_holdings_module.FundHoldingsTab,
        "_run_sync_action",
        lambda self, label, runner: calls.append((label, runner)),
        raising=False,
    )

    tab = fund_holdings_module.FundHoldingsTab(_DummyProvider(), autoload=False)
    try:
        assert tab._check_daily_auto_sync() is True
        assert calls == [("20:30自动更新", fund_holdings_module.fund_holdings_sync_service.sync_latest_all)]
        assert settings._values[fund_holdings_module.FundHoldingsTab._DAILY_AUTO_SYNC_DATE_KEY] == "20260424"
        assert settings.synced is True

        assert tab._check_daily_auto_sync() is False
        assert len(calls) == 1
    finally:
        tab.deleteLater()


def test_fund_holdings_tab_daily_auto_sync_skips_before_2030():
    now = datetime(2026, 4, 24, 20, 29)

    assert fund_holdings_module.FundHoldingsTab._should_trigger_daily_auto_sync(
        now,
        last_auto_sync_date="",
        pending_auto_sync_date="",
    ) is False


def test_fund_holdings_tab_daily_auto_sync_skips_existing_today():
    now = datetime(2026, 4, 24, 21, 0)

    assert fund_holdings_module.FundHoldingsTab._should_trigger_daily_auto_sync(
        now,
        last_auto_sync_date="20260424",
        pending_auto_sync_date="",
    ) is False
    assert fund_holdings_module.FundHoldingsTab._should_trigger_daily_auto_sync(
        now,
        last_auto_sync_date="",
        pending_auto_sync_date="20260424",
    ) is False


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
        assert tab.cmb_subject.option_values() == ["BARCLAYS BANK PLC", "UBS AG"]
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
        tab.cmb_subject.set_selected_values({"BARCLAYS BANK PLC", "睿远成长价值混合A"})
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
        assert tab.cmb_subject.option_values() == ["MORGAN STANLEY & CO.INTERNATIONAL PLC", "睿远成长价值混合A"]
        assert tab.cmb_capital_attribute.option_values() == ["自有资金", "未标注"]
        assert tab.cmb_capital_attribute.option_labels() == ["自有资金", "--"]
        assert tab.model.get_row_data(1)["资金属性"] == "--"
        tab._set_quarter_filter_state(all_quarters=True, apply=True)
        tab.cmb_capital_attribute.set_selected_values({"自有资金"})
        assert tab.proxy_model.rowCount() == 1
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
        assert restored.table.horizontalHeader().sortIndicatorOrder() == fund_holdings_module.Qt.SortOrder.DescendingOrder
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
