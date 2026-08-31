from types import SimpleNamespace

import pandas as pd
from PyQt6.QtGui import QHideEvent
from PyQt6.QtTest import QTest

import app.services.foreign_block_market_data_service as foreign_market_service
from app.services.foreign_block_market_data_service import (
    build_foreign_block_trade_rows,
)
from app.services.foreign_block_market_data_service import (
    normalize_trade_date_series as _normalize_trade_date_series,
)
from app.services.foreign_block_market_data_service import (
    normalize_trade_date_value as _normalize_trade_date_value,
)
from ui.models.table_models import StockTableModel
from ui.tabs import foreign_block_trade_tab as foreign_module
from ui.tabs.foreign_block_trade_tab import (
    BlockTradeFilterProxyModel,
    ForeignBlockTradeTab,
)


class _DeferredForeignBlockProvider:
    pass


def _wait_for_background_ui_construction(tab, qt_application) -> None:
    for _ in range(20):
        if tab.is_background_ui_construction_complete():
            return
        QTest.qWait(20)
        qt_application.processEvents()
    assert tab.is_background_ui_construction_complete()


def test_foreign_block_background_ui_construction_defers_autoload_until_all_phases_finish(
    qt_application,
    monkeypatch,
):
    scheduled = []
    monkeypatch.setattr(
        ForeignBlockTradeTab,
        "_schedule_initial_local_cache_load",
        lambda self: scheduled.append("initial_cache") or True,
    )

    tab = ForeignBlockTradeTab(
        _DeferredForeignBlockProvider(),
        defer_background_ui_build=True,
    )
    try:
        assert tab.is_background_ui_construction_complete() is False
        assert not hasattr(tab, "model")
        assert scheduled == []

        _wait_for_background_ui_construction(tab, qt_application)

        assert tab.model is not None
        assert tab.table_state is not None
        assert scheduled == ["initial_cache"]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_foreign_block_background_ui_construction_pauses_then_cancels_before_controls(
    qt_application,
):
    tab = ForeignBlockTradeTab(
        _DeferredForeignBlockProvider(),
        autoload=False,
        defer_background_ui_build=True,
    )
    try:
        assert tab.pause_background_preload() is True
        QTest.qWait(40)
        qt_application.processEvents()
        assert tab.is_background_ui_construction_complete() is False
        assert not hasattr(tab, "model")

        assert tab.resume_background_preload() is True
        assert tab.cancel_background_preload(reason="step_timeout").is_settled() is True
        QTest.qWait(40)
        qt_application.processEvents()
        assert tab.is_background_ui_construction_active() is False
        assert tab.background_ui_construction_error() == "background UI construction cancelled"
        assert not hasattr(tab, "model")

        # A workspace shutdown may race this early cancellation; it must not
        # touch controls that have not been constructed.
        tab.shutdown()
    finally:
        tab.deleteLater()


def test_foreign_block_background_ui_construction_keeps_autoload_false_for_prime(
    qt_application,
    monkeypatch,
):
    scheduled = []

    def _schedule_initial_cache(self):
        if self._initial_local_cache_load_started:
            return False
        self._initial_local_cache_load_started = True
        scheduled.append("initial_cache")
        return True

    monkeypatch.setattr(
        ForeignBlockTradeTab,
        "_schedule_initial_local_cache_load",
        _schedule_initial_cache,
    )
    tab = ForeignBlockTradeTab(
        _DeferredForeignBlockProvider(),
        autoload=False,
        defer_background_ui_build=True,
    )
    try:
        _wait_for_background_ui_construction(tab, qt_application)
        assert scheduled == []

        assert tab.prime_background_load() is True
        assert scheduled == ["initial_cache"]
        assert tab.prime_background_load() is False
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_normalize_trade_date_value_handles_epoch_ms():
    assert _normalize_trade_date_value("1775779200000") == "2026-04-10"


def test_normalize_trade_date_series_handles_iso_and_plain_text():
    series = pd.Series(["2026-04-10T00:00:00.000", "20260411", "2026-04-08"])
    result = _normalize_trade_date_series(series).tolist()
    assert result == ["2026-04-10", "2026-04-11", "2026-04-08"]


def test_build_foreign_block_trade_rows_groups_records_and_filters_ai_chain(monkeypatch):
    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: [row for row in rows if row.get("代码") == "600000"],
    )
    records = [
        {
            "交易日期": "20260410",
            "证券代码": "600000",
            "证券简称": "浦发银行",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "普通营业部",
            "收盘价": 10.0,
            "成交价": 9.5,
            "折溢率": -0.05,
            "成交量": 10000,
            "成交额": 95000,
        },
        {
            "交易日期": "2026-04-10",
            "证券代码": "600000",
            "证券简称": "浦发银行",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "普通营业部",
            "收盘价": 10.0,
            "成交价": 9.7,
            "折溢率": -0.03,
            "成交量": 20000,
            "成交额": 194000,
        },
        {
            "交易日期": "2026-04-11",
            "证券代码": "000001",
            "证券简称": "平安银行",
            "买方营业部": "普通营业部",
            "卖方营业部": "瑞银证券上海浦东新区营业部",
            "收盘价": 12.0,
            "成交价": 12.2,
            "折溢率": 0.02,
            "成交量": 10000,
            "成交额": 122000,
        },
    ]

    rows, grouped_count = build_foreign_block_trade_rows(records)

    assert grouped_count == 2
    assert rows == [
        {
            "代码": "600000",
            "名称": "浦发银行",
            "现价": "--",
            "涨幅%": "--",
            "市值": "--",
            "交易日期": "2026-04-10",
            "交易详情": "外资买入",
            "当日收盘价": "10.00",
            "成交价格": "9.60",
            "折/溢价率(%)": "-4.00%",
            "成交数量(万股)": "3.00",
            "成交金额(万元)": "28.90",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "普通营业部",
        }
    ]


def test_build_foreign_block_trade_rows_normalizes_numeric_bse_code_before_ai_pool_filter(monkeypatch):
    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **_kwargs: [row for row in rows if row.get("代码") == "920045"],
    )

    rows, grouped_count = build_foreign_block_trade_rows(
        [
            {
                "交易日期": "2026-08-05",
                "证券代码": 920045.0,
                "证券简称": "蘅东光",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 400.0,
                "成交价": 398.0,
                "折溢率": -0.005,
                "成交量": 10_000,
                "成交额": 3_980_000,
            }
        ]
    )

    assert grouped_count == 1
    assert [row["代码"] for row in rows] == ["920045"]


def test_foreign_block_cache_rows_normalize_numeric_bse_code_before_ai_pool_filter(monkeypatch):
    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **_kwargs: [row for row in rows if row.get("代码") == "920045"],
    )

    rows = foreign_market_service.build_foreign_block_cache_rows(
        [
            {
                "交易日期": "2026-08-05",
                "证券代码": 920045.0,
                "证券简称": "蘅东光",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 400.0,
                "成交价": 398.0,
                "折溢率": -0.005,
                "成交量": 10_000,
                "成交额": 3_980_000,
            }
        ]
    )

    assert [row["代码"] for row in rows] == ["920045"]


def test_should_include_row_only_matches_foreign_branches():
    assert ForeignBlockTradeTab._should_include_row(None, "高盛上海营业部", "普通营业部")
    assert ForeignBlockTradeTab._should_include_row(None, "普通营业部", "瑞银证券上海浦东新区营业部")
    assert not ForeignBlockTradeTab._should_include_row(None, "机构专用", "普通营业部")


def test_determine_direction_keeps_only_foreign_actions():
    assert ForeignBlockTradeTab._determine_direction(None, "高盛上海营业部", "普通营业部")[0] == "外资买入"
    assert ForeignBlockTradeTab._determine_direction(None, "普通营业部", "瑞银证券上海浦东新区营业部")[0] == "外资卖出"
    assert (
        ForeignBlockTradeTab._determine_direction(None, "高盛上海营业部", "瑞银证券上海浦东新区营业部")[0] == "外资对倒"
    )
    assert ForeignBlockTradeTab._determine_direction(None, "机构专用", "普通营业部")[0] == "--"


def test_block_trade_tab_does_not_join_realtime_quote_universe():
    assert ForeignBlockTradeTab.get_realtime_quote_codes(None) == set()


def test_block_trade_latest_quote_store_applies_existing_snapshot_only(monkeypatch):
    from core.global_store import global_store
    from ui.tabs.base_stock_tab import BaseStockTab

    model = type("Model", (), {"row_data": [{"code": "600000"}]})()
    applied = []

    class DummyTab:
        def _resolve_active_quote_model(self):
            return model

        def _collect_table_codes(self, current_model=None):
            assert current_model is model
            return ["600000", "000001"]

        def _apply_quote_snapshot(self, quotes):
            applied.append(quotes)

        def refresh_table_from_latest_snapshot(self):
            raise AssertionError("local quote snapshot should not be primed")

    class DummyForeignTab:
        def __init__(self):
            self.model = model

        def _apply_quote_store_snapshot(self):
            applied.append("store")

    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {
            "600000": {"close": 12.3},
            "300001": {"close": 20.5},
        },
    )

    BaseStockTab._apply_quote_store_snapshot(DummyTab())

    assert applied == [{"600000": {"close": 12.3}}]

    applied.clear()
    ForeignBlockTradeTab._apply_latest_quotes_from_store(DummyForeignTab())
    assert applied == ["store"]


def test_block_trade_local_snapshot_fills_market_fields_without_realtime(monkeypatch, qt_application):
    from core.global_store import global_store
    from ui.tabs.base_stock_tab import BaseStockTab

    code_key = "\u4ee3\u7801"
    name_key = "\u540d\u79f0"
    price_key = "\u73b0\u4ef7"
    pct_key = "\u6da8\u5e45%"
    cap_key = "\u5e02\u503c"

    class OfflineProvider:
        def __init__(self):
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"600000": {"close": 12.3, "last_close": 12.0}}

        def fetch_realtime_quotes_batch(self, _codes):
            raise AssertionError("information source tabs should not fetch realtime quotes")

    class DummyForeignTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = StockTableModel([code_key, name_key, price_key, pct_key, cap_key])

    provider = OfflineProvider()
    tab = DummyForeignTab(provider)
    tab.model.update_data([{code_key: "600000", name_key: "浦发银行", price_key: "--", pct_key: "--", cap_key: "--"}])
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        lambda codes: {"600000": {"zongguben": 2_000_000_000}} if codes == ["600000"] else {},
        raising=False,
    )

    global_store.reset_quotes()
    try:
        tab.refresh_table_from_latest_snapshot(async_local=False)

        row = tab.model.row_data[0]
        assert provider.offline_calls == [["600000"]]
        assert row[price_key] == "12.30"
        assert round(float(row[pct_key]), 2) == 2.5
        assert row[cap_key] == "246亿"
    finally:
        global_store.reset_quotes()
        tab.deleteLater()


def test_block_trade_load_cache_primes_local_snapshot(monkeypatch):
    calls = []
    queued = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: queued.append((delay, callback)),
    )
    monkeypatch.setattr(
        foreign_module,
        "load_foreign_block_cache",
        lambda **_kwargs: {
            "latest_trade_date": "20260508",
            "saved_at": "2026-05-08T20:00:00",
            "rows": [{"代码": "600000", "名称": "浦发银行"}],
            "raw_count": 1,
            "days_to_fetch": 30,
        },
    )

    class FakeCancellationToken:
        @staticmethod
        def raise_if_cancelled():
            return None

    class FakeTaskLifecycle:
        def run_background(self, _name, fn, *, task_id=None, timeout_sec=None, on_success=None, on_error=None):
            try:
                result = fn(FakeCancellationToken())
            except Exception as exc:
                if on_error is not None:
                    on_error(str(exc))
            else:
                if on_success is not None:
                    on_success(result)
            return str(task_id)

    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: list(rows or []),
    )

    class DummyTab:
        model = object()
        table_state = SimpleNamespace(show_table=lambda: calls.append("show_table"))
        _local_cache_loading = False
        _local_cache_pending_emit_event = None
        _local_cache_generation = 0
        _closing = False
        _task_lifecycle = FakeTaskLifecycle()
        _apply_local_cache_payload = ForeignBlockTradeTab._apply_local_cache_payload
        _on_local_cache_failed = ForeignBlockTradeTab._on_local_cache_failed
        _finish_local_cache_load = ForeignBlockTradeTab._finish_local_cache_load

        def _apply_row_data(self, rows, preserve_selection=False, already_filtered=False):
            return calls.append(("rows", rows)) or (["20260508"], ["机构"])

        _status_metric = staticmethod(lambda label, value, suffix="": f"{label}{value}{suffix}")

        def _set_fetch_status(self, *args, **kwargs):
            return calls.append(("status", args, kwargs))

        def _latest_trade_date_text(self):
            return "20260508"

        def _apply_latest_quotes_from_store(self):
            return calls.append("store")

        def _prime_visible_local_quote_snapshot(self, current_model=None):
            return calls.append(("local", current_model)) or True

    ForeignBlockTradeTab._load_local_cache(DummyTab())

    assert ("local", DummyTab.model) not in calls
    assert queued[0][0] == 0

    queued[0][1]()

    assert ("local", DummyTab.model) in calls


def test_block_trade_filters_rows_to_ai_industry_chain_pool(monkeypatch):
    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: [row for row in rows if row.get("代码") == "300308"],
    )

    rows = ForeignBlockTradeTab._filter_rows_to_ai_chain(
        [
            {"代码": "300308", "名称": "中际旭创"},
            {"代码": "600000", "名称": "浦发银行"},
        ]
    )

    assert rows == [{"代码": "300308", "名称": "中际旭创"}]


def test_block_trade_search_only_matches_code_name_and_foreign_branch():
    model = StockTableModel(["代码", "名称", "交易详情", "买方营业部", "卖方营业部"])
    model.update_data(
        [
            {
                "代码": "600000",
                "名称": "浦发银行",
                "交易详情": "外资买入",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
            }
        ]
    )

    proxy = BlockTradeFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setFilterText("高盛")
    assert proxy.rowCount() == 1

    proxy.setFilterText("浦发")
    assert proxy.rowCount() == 1

    proxy.setFilterText("买入")
    assert proxy.rowCount() == 0


def test_block_trade_exact_filters_support_multi_select():
    model = StockTableModel(["代码", "名称", "交易日期", "交易详情", "买方营业部", "卖方营业部"])
    model.update_data(
        [
            {
                "代码": "600000",
                "名称": "浦发银行",
                "交易日期": "2026-04-10",
                "交易详情": "外资买入",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
            },
            {
                "代码": "000001",
                "名称": "平安银行",
                "交易日期": "2026-04-11",
                "交易详情": "外资卖出",
                "买方营业部": "普通营业部",
                "卖方营业部": "瑞银证券上海浦东新区营业部",
            },
            {
                "代码": "000002",
                "名称": "万科A",
                "交易日期": "2026-04-12",
                "交易详情": "外资对倒",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "瑞银证券上海浦东新区营业部",
            },
        ]
    )

    proxy = BlockTradeFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setExactFilters("交易日期", {"2026-04-10", "2026-04-12"})
    assert proxy.rowCount() == 2

    proxy.setExactFilters("交易详情", {"外资买入", "外资对倒"})
    assert proxy.rowCount() == 2

    proxy.setExactFilters("_branch", {"高盛", "瑞银"})
    assert proxy.rowCount() == 2


def test_block_trade_delete_later_has_no_auto_timers(monkeypatch):
    monkeypatch.setattr(ForeignBlockTradeTab, "_load_local_cache", lambda self: None, raising=False)

    tab = ForeignBlockTradeTab(object())
    try:
        assert not hasattr(tab, "_auto_timer")
        assert not hasattr(tab, "_auto_initial_check_timer")
        tab._post_f5_online_timer.start(60_000)
        tab._visible_online_timer.start(60_000)

        tab.deleteLater()
        assert tab._closing is True
        assert tab._foreign_runtime_cleanup_done is True
        assert tab._post_f5_online_timer.isActive() is False
        assert tab._visible_online_timer.isActive() is False
        tab = None
    finally:
        if tab is not None:
            tab.deleteLater()


def test_block_trade_delays_initial_local_cache_load(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = ForeignBlockTradeTab(object())
    try:
        assert scheduled
        assert scheduled[-1][0] == foreign_module.LOCAL_CACHE_LOAD_DELAY_MS
        assert callable(scheduled[-1][1])
    finally:
        tab.deleteLater()


def test_block_trade_can_defer_initial_local_cache_load(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = ForeignBlockTradeTab(object(), autoload=False)
    try:
        local_cache_jobs = [item for item in scheduled if item[0] == foreign_module.LOCAL_CACHE_LOAD_DELAY_MS]
        assert local_cache_jobs == []

        assert tab.prime_background_load() is True
        assert tab.is_background_preload_complete() is False
        assert scheduled[-1][0] == foreign_module.LOCAL_CACHE_LOAD_DELAY_MS
        assert callable(scheduled[-1][1])
        assert tab.prime_background_load() is False
        local_cache_jobs = [item for item in scheduled if item[0] == foreign_module.LOCAL_CACHE_LOAD_DELAY_MS]
        assert len(local_cache_jobs) == 1

        tab._local_cache_loading = True
        tab._finish_local_cache_load()
        assert tab.is_background_preload_complete() is True
    finally:
        tab.deleteLater()


def test_block_trade_cancel_invalidates_queued_preload_and_activation_retries(monkeypatch):
    scheduled = []
    loads = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(
        foreign_module,
        "_schedule_pending_visible_online_refresh",
        lambda owner: loads.append(("visible", owner)) or True,
    )
    tab = ForeignBlockTradeTab(object(), autoload=False)
    tab._load_local_cache = lambda: loads.append("cache")
    try:
        assert tab.prime_background_load() is True
        stale_callback = scheduled[-1][1]
        receipt = tab.cancel_background_preload(reason="step_timeout")
        assert receipt.is_settled() is True

        stale_callback()
        assert loads == []

        tab.on_workspace_tab_activated()
        retry_callback = scheduled[-1][1]
        retry_callback()
        assert loads == [("visible", tab), "cache"]
    finally:
        tab.deleteLater()


def test_block_trade_cancel_invalidates_post_f5_cache_callback(monkeypatch):
    scheduled = []
    loads = []
    monkeypatch.setattr(foreign_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = ForeignBlockTradeTab(object(), autoload=False)
    tab._load_local_cache = lambda **kwargs: loads.append(kwargs)
    try:
        tab._post_f5_local_cache_defer_until = 15.0
        assert tab._schedule_post_f5_local_cache_load(emit_event=False) is True
        stale_callback = scheduled[-1][1]

        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert receipt.is_settled() is True
        assert tab._post_f5_local_cache_pending is False
        assert stale_callback() is False
        assert loads == []
    finally:
        tab.deleteLater()


def test_block_trade_cancelled_generation_drops_queued_cache_commit(monkeypatch):
    queued = []
    calls = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda _delay, callback: queued.append(callback),
    )

    class DummyTab:
        _local_cache_generation = 5
        _runtime_cleanup_done = False

        @staticmethod
        def _finish_local_cache_load():
            calls.append("finish")

    tab = DummyTab()
    foreign_module.ForeignBlockTradeTab._apply_local_cache_payload(
        tab,
        {"rows": [], "raw_count": 0, "emit_event": False},
    )
    assert len(queued) == 1

    tab._local_cache_generation += 1
    queued[0]()

    assert calls == []


def test_extract_cache_filter_options_and_save_gate():
    dates, branches = ForeignBlockTradeTab._extract_cache_filter_options(
        [
            {"交易日期": "2026-04-18", "买方营业部": "高盛上海营业部", "卖方营业部": "普通营业部"},
            {"交易日期": "2026-04-20", "买方营业部": "普通营业部", "卖方营业部": "瑞银证券上海浦东新区营业部"},
            {"交易日期": "2026-04-19", "买方营业部": "机构专用", "卖方营业部": "普通营业部"},
        ]
    )
    assert dates == ["2026-04-20", "2026-04-19", "2026-04-18"]
    assert branches == ["瑞银证券上海浦东新区营业部", "高盛上海营业部"]
    assert ForeignBlockTradeTab._should_save_cache([], [])
    assert not ForeignBlockTradeTab._should_save_cache(["20260420-20260420"], [])


def test_foreign_block_trade_refresh_after_f5_defers_cache_and_schedules_online_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(foreign_module.time, "monotonic", lambda: 100.0)
    tab = SimpleNamespace(
        _schedule_post_f5_local_cache_load=lambda: calls.append("deferred_cache") or True,
        model=object(),
        refresh_table_from_latest_snapshot=(
            lambda current_model=None, *, async_local=True: calls.append(("snapshot", current_model, async_local))
        ),
        schedule_post_online_refresh_after_f5=lambda: calls.append("scheduled") or True,
    )
    tab.prepare_post_f5_refresh = lambda: ForeignBlockTradeTab.prepare_post_f5_refresh(tab)

    assert ForeignBlockTradeTab.refresh_data_after_f5(tab) is True
    assert calls == ["deferred_cache", ("snapshot", tab.model, True), "scheduled"]
    assert tab._post_f5_local_cache_defer_until == 105.0


def test_foreign_block_trade_local_cache_waits_during_post_f5_defer(monkeypatch):
    scheduled = []
    background_calls = []
    monkeypatch.setattr(foreign_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(
        foreign_module,
        "task_lifecycle_for",
        lambda owner, **_kwargs: background_calls.append(owner),
    )
    tab = SimpleNamespace(
        _closing=False,
        _post_f5_local_cache_defer_until=15.0,
        _post_f5_local_cache_pending=False,
        _post_f5_local_cache_emit_event=False,
        _local_cache_loading=False,
        _local_cache_pending_emit_event=None,
        _initial_local_cache_load_started=False,
    )
    tab._schedule_post_f5_local_cache_load = (
        lambda **kwargs: ForeignBlockTradeTab._schedule_post_f5_local_cache_load(tab, **kwargs)
    )
    tab._run_post_f5_local_cache_load = lambda: None

    ForeignBlockTradeTab._load_local_cache(tab, emit_event=False)

    assert background_calls == []
    assert scheduled[0][0] == 5000
    assert tab._post_f5_local_cache_pending is True
    assert tab._post_f5_local_cache_emit_event is False


def test_foreign_block_trade_f5_online_refresh_runs_after_delay(monkeypatch):
    calls = []
    scheduled = []
    monkeypatch.setattr(
        foreign_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    tab = SimpleNamespace(
        _pending_f5_online_refresh=False,
        _pending_visible_online_refresh=False,
        isVisible=lambda: True,
        _is_current_workspace_tab=lambda: True,
        run_post_online_refresh=lambda: calls.append("online") or True,
    )

    assert ForeignBlockTradeTab.schedule_post_online_refresh_after_f5(tab) is True
    assert tab._pending_f5_online_refresh is True
    assert scheduled[0][0] == foreign_module.F5_AUTO_ONLINE_REFRESH_DELAY_MS

    assert scheduled[0][1]() is True
    assert tab._pending_f5_online_refresh is False
    assert calls == ["online"]


def test_foreign_block_trade_refresh_after_ai_chain_update_reloads_without_reemitting():
    calls = []
    tab = SimpleNamespace(
        _load_local_cache=lambda **kwargs: calls.append(("local_cache", kwargs)),
        model=object(),
        refresh_table_from_latest_snapshot=(
            lambda current_model=None, *, async_local=True: calls.append(("snapshot", current_model, async_local))
        ),
        run_post_online_refresh=lambda: calls.append("online") or True,
    )

    assert ForeignBlockTradeTab.refresh_data_after_ai_industry_chain_update(tab) is True
    assert calls == [("local_cache", {"emit_event": False}), ("snapshot", tab.model, True), "online"]


def test_foreign_block_trade_hidden_ai_chain_update_defers_online_fetch():
    calls = []
    tab = SimpleNamespace(
        _pending_visible_online_refresh=False,
        isVisible=lambda: False,
        _load_local_cache=lambda **kwargs: calls.append(("local_cache", kwargs)),
        model=object(),
        refresh_table_from_latest_snapshot=(
            lambda current_model=None, *, async_local=True: calls.append(("snapshot", current_model, async_local))
        ),
        run_post_online_refresh=lambda: calls.append("online") or True,
    )

    assert ForeignBlockTradeTab.refresh_data_after_ai_industry_chain_update(tab) is True
    assert calls == [("local_cache", {"emit_event": False}), ("snapshot", tab.model, True)]
    assert tab._pending_visible_online_refresh is True


def test_foreign_block_trade_hide_cancels_active_fetch_and_keeps_resume_intent():
    calls = []
    tab = SimpleNamespace(
        _closing=False,
        _is_loading=True,
        _fetch_generation=4,
        _task_lifecycle=SimpleNamespace(cancel=lambda name, **kwargs: calls.append((name, kwargs)) or True),
        btn_refresh=SimpleNamespace(setEnabled=lambda enabled: calls.append(("enabled", enabled))),
        _pending_visible_online_refresh=False,
    )

    assert foreign_module._cancel_online_fetch(tab, reason="owner_hidden", resume_when_visible=True) is True
    assert tab._is_loading is False
    assert tab._fetch_generation == 5
    assert tab._pending_visible_online_refresh is True
    assert calls == [("fetch", {"reason": "owner_hidden"}), ("enabled", True)]


def test_foreign_block_trade_hide_event_cancels_active_fetch():
    calls = []
    tab = ForeignBlockTradeTab(object(), autoload=False)
    try:
        tab._is_loading = True
        tab._fetch_generation = 1
        tab._task_lifecycle = SimpleNamespace(
            cancel=lambda name, **kwargs: calls.append((name, kwargs)) or True,
            shutdown=lambda **_kwargs: True,
        )
        tab.hideEvent(QHideEvent())
        assert tab._is_loading is False
        assert tab._pending_visible_online_refresh is True
        assert calls == [("fetch", {"reason": "owner_hidden"})]
    finally:
        tab.deleteLater()
