import datetime
from types import SimpleNamespace

import pandas as pd

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

        tab.deleteLater()
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
        assert (foreign_module.LOCAL_CACHE_LOAD_DELAY_MS, tab._load_local_cache) in scheduled
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
        local_cache_jobs = [
            item for item in scheduled if item == (foreign_module.LOCAL_CACHE_LOAD_DELAY_MS, tab._load_local_cache)
        ]
        assert local_cache_jobs == []

        assert tab.prime_background_load() is True
        assert (foreign_module.LOCAL_CACHE_LOAD_DELAY_MS, tab._load_local_cache) in scheduled
        assert tab.prime_background_load() is False
        local_cache_jobs = [
            item for item in scheduled if item == (foreign_module.LOCAL_CACHE_LOAD_DELAY_MS, tab._load_local_cache)
        ]
        assert len(local_cache_jobs) == 1
    finally:
        tab.deleteLater()


def test_should_trigger_auto_refresh_only_after_20_on_trade_day():
    now = datetime.datetime(2026, 4, 20, 20, 5, 0)
    before_20 = datetime.datetime(2026, 4, 20, 19, 59, 0)
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        before_20,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 14, 30, 0),
        pending_auto_refresh_date="",
    )
    assert ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 14, 30, 0),
        pending_auto_refresh_date="",
    )


def test_should_trigger_auto_refresh_skips_when_today_after_20_already_saved_or_pending():
    now = datetime.datetime(2026, 4, 20, 20, 5, 0)
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 20, 1, 0),
        pending_auto_refresh_date="",
    )
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="20260420",
        last_success_at=None,
        pending_auto_refresh_date="",
    )
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=None,
        pending_auto_refresh_date="20260420",
    )


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
        "_task_lifecycle_for",
        lambda owner: background_calls.append(owner),
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
    tab = ForeignBlockTradeTab.__new__(ForeignBlockTradeTab)
    tab._pending_f5_online_refresh = False
    tab.run_post_online_refresh = lambda: calls.append("online") or True

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
