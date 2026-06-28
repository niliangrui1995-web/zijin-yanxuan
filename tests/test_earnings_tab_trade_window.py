# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QShowEvent

from ui.theme import theme_manager


def _earnings_module():
    import ui.tabs.earnings_tab as earnings_module

    return earnings_module


def _earnings_tab_class():
    return _earnings_module().EarningsTab


@pytest.fixture
def earnings_qt(qt_application):
    earnings_module = _earnings_module()
    from ui.models.table_models import RtSortFilterProxyModel, StockTableModel
    from ui.tabs.base_stock_tab import BaseStockTab

    return SimpleNamespace(
        module=earnings_module,
        EarningsTab=earnings_module.EarningsTab,
        RtSortFilterProxyModel=RtSortFilterProxyModel,
        StockTableModel=StockTableModel,
        BaseStockTab=BaseStockTab,
    )


def test_recent_trade_window_start_uses_oldest_trade_day(monkeypatch):
    EarningsTab = _earnings_tab_class()
    from core.market_calendar import MarketCalendar

    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    assert EarningsTab._recent_trade_window_start(3) == "2026-04-10"


def test_prune_rows_to_recent_trade_window_keeps_records_within_trade_span(monkeypatch):
    EarningsTab = _earnings_tab_class()
    from core.market_calendar import MarketCalendar

    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    rows = [
        {"代码": "000001", "揭晓日": "2026-04-14"},
        {"代码": "000002", "揭晓日": "2026-04-12"},
        {"代码": "000003", "揭晓日": "2026-04-09"},
        {"代码": "000004", "揭晓日": ""},
    ]

    pruned = EarningsTab._prune_rows_to_recent_trade_window(rows, trade_days=3)

    kept_codes = [row["代码"] for row in pruned]
    assert kept_codes == ["000001", "000002", "000004"]


def test_earnings_display_trade_days_is_30():
    assert _earnings_module().EARNINGS_DISPLAY_TRADE_DAYS == 30


def test_earnings_tab_does_not_join_realtime_quote_universe():
    EarningsTab = _earnings_tab_class()
    assert EarningsTab.get_realtime_quote_codes(None) == set()


def test_earnings_tab_defers_scheduler_creation_until_runtime(monkeypatch, earnings_qt):
    created = []

    class DummySignal:
        def connect(self, callback):
            self.callback = callback

    class DummyScheduler:
        sig_new_surprises_found = DummySignal()
        sig_fetch_failed = DummySignal()

        def load_cached_records_async(self):
            pass

        def stop_patrol(self):
            pass

    monkeypatch.setattr(
        earnings_qt.module,
        "EarningsScheduler",
        lambda parent=None: created.append(parent) or DummyScheduler(),
    )

    tab = earnings_qt.EarningsTab()
    try:
        assert created == []
        assert tab.scheduler is None

        assert tab._ensure_scheduler() is tab.scheduler
        assert created == [tab]
    finally:
        tab.deleteLater()


def test_earnings_tab_constructor_does_not_resolve_scheduler_service(monkeypatch, earnings_qt):
    monkeypatch.setattr(
        earnings_qt.module,
        "_resolve_earnings_refresh_service_class",
        lambda: (_ for _ in ()).throw(AssertionError("scheduler service should be resolved on runtime start")),
    )

    tab = earnings_qt.EarningsTab()
    try:
        assert tab.scheduler is None
    finally:
        tab.deleteLater()


def test_earnings_report_period_and_right_columns_use_muted_text(earnings_qt):
    tab = earnings_qt.EarningsTab()
    try:
        tab.model.update_data(
            [
                {
                    "代码": "300308",
                    "名称": "中际旭创",
                    "现价": "128.50",
                    "涨幅%": "2.30",
                    "市值": "1200亿",
                    "PE(TTM)": "38.2",
                    "环比%": "18.2",
                    "同比%": "35.6",
                    "当季利润": "18.2亿",
                    "上季利润": "15.4亿",
                    "报告期": "2026Q1",
                    "类型": "财报",
                    "揭晓日": "2026-04-24",
                    "基调": "高增",
                    "所属行业与概念": "CPO",
                }
            ]
        )

        muted = QColor(theme_manager.get("TEXT_MUTED")).name()
        for header in ["报告期", "类型", "揭晓日", "基调", "所属行业与概念"]:
            idx = tab.model.index(0, tab.model.headers.index(header))
            assert tab.model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == muted
    finally:
        tab.deleteLater()


def test_earnings_delete_later_stops_runtime_timers_and_scheduler(monkeypatch, earnings_qt):
    created = []

    class DummySignal:
        def connect(self, callback):
            self.callback = callback

    class DummyScheduler:
        def __init__(self, parent=None):
            self._parent = parent
            self.sig_new_surprises_found = DummySignal()
            self.sig_fetch_failed = DummySignal()
            self.shutdown_calls = 0

        def parent(self):
            return self._parent

        def load_cached_records_async(self):
            pass

        def shutdown(self):
            self.shutdown_calls += 1

    monkeypatch.setattr(
        earnings_qt.module,
        "EarningsScheduler",
        lambda parent=None: created.append(parent) or DummyScheduler(parent),
    )

    tab = earnings_qt.EarningsTab()
    try:
        tab._ensure_runtime_started()
        tab._recalc_pe_timer.start(0)
        scheduler = tab.scheduler

        assert not hasattr(tab, "_runtime_start_timer")
        assert tab._recalc_pe_timer.isActive() is True

        tab.deleteLater()

        assert tab._recalc_pe_timer.isActive() is False
        assert scheduler.shutdown_calls == 1
        tab = None
    finally:
        if tab is not None:
            tab.deleteLater()


def test_earnings_runtime_start_is_gated_to_current_workspace_tab():
    EarningsTab = _earnings_tab_class()

    class DummyTabs:
        current = None

        def currentWidget(self):
            return self.current

    class DummyParent:
        tabs = DummyTabs()

    class DummyTab:
        def parent(self):
            return DummyParent()

    tab = DummyTab()
    DummyParent.tabs.current = object()
    assert not EarningsTab._is_current_workspace_tab(tab)

    DummyParent.tabs.current = tab
    assert EarningsTab._is_current_workspace_tab(tab)


def test_earnings_runtime_start_is_queued_once(monkeypatch, earnings_qt):
    EarningsTab = earnings_qt.EarningsTab
    queued = []
    calls = []
    monkeypatch.setattr(earnings_qt.module.QTimer, "singleShot", lambda delay, callback: queued.append((delay, callback)))

    class DummyTab:
        _patrol_started = False
        _runtime_start_queued = False
        _runtime_cleanup_done = False

        def _is_current_workspace_tab(self):
            return True

        def _ensure_runtime_started(self):
            calls.append("start")
            self._patrol_started = True

    tab = DummyTab()
    tab._start_queued_runtime = lambda: EarningsTab._start_queued_runtime(tab)

    EarningsTab._queue_runtime_start(tab)
    EarningsTab._queue_runtime_start(tab)

    assert queued[0][0] == 0
    assert len(queued) == 1
    assert tab._runtime_start_queued is True

    queued[0][1]()

    assert calls == ["start"]
    assert tab._runtime_start_queued is False


def test_earnings_initial_visible_work_can_be_delayed(monkeypatch, earnings_qt):
    queued = []
    prime_calls = []
    monkeypatch.setattr(
        earnings_qt.BaseStockTab,
        "_prime_visible_local_quote_snapshot",
        lambda self, current_model=None: prime_calls.append(current_model) or True,
    )

    tab = earnings_qt.EarningsTab(runtime_start_delay_ms=1800)
    monkeypatch.setattr(earnings_qt.module.QTimer, "singleShot", lambda delay, callback: queued.append((delay, callback)))
    tab._is_current_workspace_tab = lambda: True
    tab.isVisible = lambda: True
    tab.row_data = [{"代码": "300750", "市值": "100亿", "_raw_profit": 1_000_000}]
    try:
        tab.showEvent(QShowEvent())

        assert prime_calls == []
        assert len(queued) == 1
        assert queued[0][0] == 1800
        assert tab.scheduler is None
        assert tab._runtime_start_queued is False
        assert tab._recalc_pe_timer.isActive() is False

        queued.pop(0)[1]()

        assert prime_calls == [tab.model]
        assert len(queued) == 1
        assert queued[0][0] == 0
        assert tab._runtime_start_queued is True
        assert tab._recalc_pe_timer.isActive() is True
    finally:
        tab.deleteLater()


def test_earnings_queued_runtime_start_skips_stale_tab(earnings_qt):
    EarningsTab = earnings_qt.EarningsTab
    calls = []

    class DummyTab:
        _patrol_started = False
        _runtime_start_queued = True
        _runtime_cleanup_done = False

        def _is_current_workspace_tab(self):
            return False

        def _ensure_runtime_started(self):
            calls.append("start")

    tab = DummyTab()

    EarningsTab._start_queued_runtime(tab)

    assert calls == []
    assert tab._runtime_start_queued is False


def test_earnings_runtime_show_skips_non_interactive_load_reason():
    EarningsTab = _earnings_tab_class()

    class DummyTab:
        _workspace_load_reason = "perf_memory_probe"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not EarningsTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert EarningsTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_apply_latest_quotes_uses_local_snapshot_and_recalculates_pe(earnings_qt):
    EarningsTab = earnings_qt.EarningsTab
    StockTableModel = earnings_qt.StockTableModel

    class DummyTab:
        model = StockTableModel(["代码", "名称", "现价", "涨幅%", "市值", "PE(TTM)"])

    tab = DummyTab()
    tab.model.update_data(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "PE(TTM)": "--",
                "_raw_profit": 250_000_000,
            }
        ]
    )
    calls = []

    def _apply_store_snapshot():
        calls.append("store")
        tab.model.set_cell_value(0, "市值", "100亿", emit_signal=False)

    tab._apply_quote_store_snapshot = _apply_store_snapshot
    tab.async_update_market_caps = lambda: calls.append("unexpected_market_caps")
    tab._recalc_pe_ttm = lambda: EarningsTab._recalc_pe_ttm(tab)

    EarningsTab._apply_latest_quotes_from_store(tab)

    assert calls == ["store"]
    assert tab.model.row_data[0]["PE(TTM)"] == "10.0"


def test_earnings_local_snapshot_fills_market_fields_and_pe_without_realtime(monkeypatch, earnings_qt):
    EarningsTab = earnings_qt.EarningsTab
    BaseStockTab = earnings_qt.BaseStockTab
    StockTableModel = earnings_qt.StockTableModel

    from core.global_store import global_store

    code_key = "\u4ee3\u7801"
    name_key = "\u540d\u79f0"
    price_key = "\u73b0\u4ef7"
    pct_key = "\u6da8\u5e45%"
    cap_key = "\u5e02\u503c"

    class OfflineProvider:
        def __init__(self):
            self.offline_calls = []

        def _build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 10.5, "last_close": 10.0}}

        def fetch_realtime_quotes_batch(self, _codes):
            raise AssertionError("earnings tab should not fetch realtime quotes")

    class DummyEarningsTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = StockTableModel([code_key, name_key, price_key, pct_key, cap_key, "PE(TTM)"])

        def _recalc_pe_ttm(self):
            return EarningsTab._recalc_pe_ttm(self)

    provider = OfflineProvider()
    tab = DummyEarningsTab(provider)
    tab.model.update_data(
        [
            {
                code_key: "000001",
                name_key: "平安银行",
                price_key: "--",
                pct_key: "--",
                cap_key: "--",
                "PE(TTM)": "--",
                "_raw_profit": 250_000_000,
            }
        ]
    )
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        lambda codes: {"000001": {"zongguben": 1_000_000_000}} if codes == ["000001"] else {},
        raising=False,
    )

    global_store.reset_quotes()
    try:
        tab.refresh_table_from_latest_snapshot(async_local=False)
        EarningsTab._recalc_pe_ttm(tab)

        row = tab.model.row_data[0]
        assert provider.offline_calls == [["000001"]]
        assert row[price_key] == "10.50"
        assert round(float(row[pct_key]), 2) == 5.0
        assert row[cap_key] == "105亿"
        assert row["PE(TTM)"] == "10.5"
    finally:
        global_store.reset_quotes()
        tab.deleteLater()


def test_earnings_refresh_after_f5_triggers_routine_scan():
    EarningsTab = _earnings_tab_class()

    class DummyTab:
        model = object()

    tab = DummyTab()
    calls = []
    tab._apply_latest_quotes_from_store = lambda: calls.append("quotes")
    tab._apply_display_trade_window = lambda force_refresh=False: calls.append(("window", force_refresh))
    tab.refresh_table_from_latest_snapshot = lambda current_model=None, *, async_local=True: calls.append(
        ("snapshot", current_model, async_local)
    )

    class Scheduler:
        def trigger_routine_scan(self, reason="manual"):
            calls.append(("routine", reason))
            return True

    tab._ensure_scheduler = lambda: Scheduler()

    assert EarningsTab.refresh_data_after_f5(tab) is True
    assert calls == ["quotes", ("window", True), ("snapshot", tab.model, True), ("routine", "f5")]


def test_earnings_refresh_after_ai_chain_update_replays_filtered_cache():
    EarningsTab = _earnings_tab_class()
    cached_frame = pd.DataFrame([{"stock": "300750"}])
    calls = []

    class Model:
        def update_data(self, rows, **kwargs):
            calls.append(("update", list(rows), kwargs))

    class Engine:
        def get_cached_records(self):
            calls.append("cached")
            return cached_frame

    tab = SimpleNamespace(
        row_data=[{"old": "row"}],
        model=Model(),
        _ensure_scheduler=lambda: SimpleNamespace(engine=Engine()),
        _on_new_data_found=lambda df, mode="routine": calls.append(("new_data", df, mode)),
        refresh_table_from_latest_snapshot=(
            lambda current_model=None, *, async_local=True: calls.append(("snapshot", current_model, async_local))
        ),
    )

    assert EarningsTab.refresh_data_after_ai_industry_chain_update(tab) is True
    assert tab.row_data == []
    assert calls[0] == "cached"
    assert calls[1] == ("update", [], {"hydrate_latest_quotes": False})
    assert calls[2][0] == "new_data"
    assert calls[2][1] is cached_frame
    assert calls[2][2] == "warm_cache"
    assert calls[3] == ("snapshot", tab.model, True)


def test_earnings_tab_preserves_discovery_time_from_engine_frame(monkeypatch, earnings_qt):
    tab = earnings_qt.EarningsTab()
    try:
        monkeypatch.setattr(tab, "_apply_display_trade_window", lambda force_refresh=False: True)
        monkeypatch.setattr(tab, "_set_window_status", lambda *args, **kwargs: None)
        monkeypatch.setattr(tab, "_status_metric", lambda *args, **kwargs: "")
        df = pd.DataFrame(
            [
                {
                    "股票代码": "300604",
                    "股票名称": "长川科技",
                    "环比增速_百分比": 57.69,
                    "同比增速_百分比": 88.8,
                    "单季净利润_新增": 120000000.0,
                    "单季净利润_上期": 76000000.0,
                    "报告期": "2026-03-31",
                    "数据类型": "财报",
                    "公告日期": "2026-04-20",
                    "发现时间": "2026-06-27T08:31:02",
                    "基调": "高增",
                    "所属行业与概念": "半导体设备",
                }
            ]
        )

        tab._on_new_data_found(df, "warm_cache")

        assert tab.row_data[0]["代码"] == "300604"
        assert tab.row_data[0]["揭晓日"] == "2026-04-20"
        assert tab.row_data[0]["发现时间"] == "2026-06-27T08:31:02"
    finally:
        tab.deleteLater()


def test_earnings_display_window_primes_local_snapshot_after_cache_render():
    EarningsTab = _earnings_tab_class()
    calls = []

    class Model:
        def update_data(self, rows, **_kwargs):
            calls.append(("update", list(rows)))

    class DummyTab:
        row_data = [
            {
                "代码": "000001",
                "名称": "平安银行",
                "揭晓日": "2099-01-01",
            }
        ]
        model = Model()
        _prune_rows_to_recent_trade_window = staticmethod(EarningsTab._prune_rows_to_recent_trade_window)

        def _apply_latest_quotes_from_store(self):
            return calls.append("store")

        def _prime_visible_local_quote_snapshot(self, current_model=None):
            return calls.append(("local", current_model)) or True

    tab = DummyTab()

    assert EarningsTab._apply_display_trade_window(tab, force_refresh=True) is True
    assert calls == [("update", tab.row_data), "store", ("local", tab.model)]


def test_filter_out_st_dataframe_removes_st_rows():
    EarningsTab = _earnings_tab_class()

    df = pd.DataFrame(
        [
            {"股票代码": "000001", "股票名称": "平安银行"},
            {"股票代码": "000002", "股票名称": "ST晨鸣"},
            {"股票代码": "000003", "股票名称": "*ST同洲"},
        ]
    )

    filtered = EarningsTab._filter_out_st_dataframe(df)

    assert filtered["股票代码"].tolist() == ["000001"]


def test_prune_rows_to_recent_trade_window_drops_st_rows(monkeypatch):
    EarningsTab = _earnings_tab_class()
    from core.market_calendar import MarketCalendar

    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    rows = [
        {"代码": "000001", "名称": "平安银行", "揭晓日": "2026-04-14"},
        {"代码": "000002", "名称": "ST晨鸣", "揭晓日": "2026-04-14"},
        {"代码": "000003", "名称": "*ST同洲", "揭晓日": ""},
        {"代码": "000004", "名称": "万科A", "揭晓日": "2026-04-09"},
    ]

    pruned = EarningsTab._prune_rows_to_recent_trade_window(rows, trade_days=3)

    kept_codes = [row["代码"] for row in pruned]
    assert kept_codes == ["000001"]


def test_rt_sort_filter_proxy_supports_multi_select_column_filters(earnings_qt):
    RtSortFilterProxyModel = earnings_qt.RtSortFilterProxyModel
    StockTableModel = earnings_qt.StockTableModel

    model = StockTableModel(["代码", "名称", "类型"])
    model.update_data(
        [
            {"代码": "000001", "名称": "平安银行", "类型": "预告"},
            {"代码": "000002", "名称": "万科A", "类型": "快报"},
            {"代码": "000004", "名称": "国华网安", "类型": "财报"},
        ]
    )

    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setColumnFilters("类型", {"预告", "财报"})
    assert proxy.rowCount() == 2

    codes = []
    for row in range(proxy.rowCount()):
        source_index = proxy.mapToSource(proxy.index(row, 0))
        codes.append(model.get_row_data(source_index.row())["代码"])
    assert codes == ["000001", "000004"]


def test_earnings_fetch_failure_keeps_existing_rows_visible():
    EarningsTab = _earnings_tab_class()
    calls = []

    class TableState:
        def show_table(self):
            calls.append("show_table")

        def show_error(self, title, subtitle=""):
            calls.append(("show_error", title, subtitle))

    class DummyTab:
        row_data = [{"代码": "000001"}]
        table_state = TableState()

        def _set_window_status(self, *segments):
            calls.append(("status", segments))

    EarningsTab._on_fetch_failed(DummyTab(), "routine", "provider unavailable")

    assert calls == [
        "show_table",
        ("status", ("业绩抓取失败", "定时扫描", "provider unavailable")),
    ]
