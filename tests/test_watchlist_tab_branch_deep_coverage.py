# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.stock_context_model_service import StockContextSnapshot
from core.buy_point import BUY_POINT_TEXT
from core.exceptions import CacheIOError
from ui.tabs import watchlist_tab as watch


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)

    def disconnect(self, slot):
        self.calls.append(("disconnect", slot))


class _Index:
    def __init__(self, row=0, valid=True):
        self._row = row
        self._valid = valid

    def isValid(self):
        return self._valid

    def row(self):
        return self._row


class _Timer:
    def __init__(self, active=False, fail=False):
        self.active = active
        self.fail = fail
        self.started = []
        self.stopped = 0

    def isActive(self):
        return self.active

    def stop(self):
        self.stopped += 1
        if self.fail:
            raise RuntimeError("deleted")

    def start(self, delay):
        self.started.append(delay)


def test_module_helpers_cancellation_generation_and_commit(monkeypatch):
    token = SimpleNamespace(cancelled=True)
    assert watch._task_cancelled(token)
    assert list(watch._active_items([1, 2], token)) == []
    assert list(watch._active_items([1, 2], None)) == [1, 2]

    calls = []
    owner = SimpleNamespace(
        _closing=False,
        _vcp_task_generation=2,
    )
    monkeypatch.setattr(
        watch,
        "build_watchlist_indicator_results",
        lambda values, **kwargs: calls.append((values, kwargs)) or {"ok": 1},
    )
    monkeypatch.setattr(
        watch,
        "persist_watchlist_metrics",
        lambda value, **kwargs: calls.append((value, kwargs)),
    )
    empty_radar = ({}, {}, {}, {}, {}, None)
    assert watch._run_vcp_refresh([(0, "x")], None, empty_radar, token) == {"ok": 1}
    watch._run_metrics_persist({"x": {}}, token)

    signal = _Signal()
    monkeypatch.setattr(watch, "event_bus", SimpleNamespace(sig_vcp_watchlist_ready=signal))
    watch._emit_vcp_if_current(owner, 1, {"x": {}})
    watch._emit_vcp_if_current(owner, 2, {})
    watch._emit_vcp_if_current(owner, 2, {"x": {}})
    assert signal.calls == [({"x": {}},)]
    watch._log_task_error_if_current(owner, 1, "x", "bad")
    watch._log_task_error_if_current(owner, 2, "x", "bad")

    patched = []
    monkeypatch.setattr(
        watch.watchlist_vm, "bulk_patch_entries", lambda payload, remove_keys: patched.append((payload, remove_keys))
    )
    watch._commit_watchlist_metrics({"x": {}}, token)
    watch._commit_watchlist_metrics({"x": {}}, None)
    assert patched == [({"x": {}}, ["催化剂", "美股日报", "热点板块"])]


def test_vcp_refresh_loads_rps_off_thread_when_snapshot_omits_bundle(monkeypatch):
    bundle = {"rps120": {"000001": 80}, "rps250": {"000001": 90}}
    load_calls = []
    monkeypatch.setattr(
        watch,
        "load_active_rps_payload",
        lambda: load_calls.append("rps") or bundle,
    )

    embedded_result = watch._run_vcp_refresh(
        [(0, "000001")],
        StockContextSnapshot(rps_bundle=bundle),
        None,
        None,
    )
    assert load_calls == []

    fallback_result = watch._run_vcp_refresh(
        [(0, "000001")],
        StockContextSnapshot(),
        None,
        None,
    )

    assert fallback_result == embedded_result
    assert fallback_result["000001"]["rps"] == "90/80"
    assert load_calls == ["rps"]


def test_vcp_refresh_degrades_when_active_rps_payload_is_not_an_object(monkeypatch):
    monkeypatch.setattr(
        watch,
        "load_active_rps_payload",
        lambda: (_ for _ in ()).throw(ValueError("active F5 RPS payload must be an object")),
    )

    result = watch._run_vcp_refresh(
        [(0, "000001")],
        StockContextSnapshot(),
        None,
        None,
    )

    assert result["000001"]["rps"] == "--"


def test_signature_delay_touch_and_trade_date_error(monkeypatch):
    assert watch.WatchlistTab._coerce_delay_ms("bad", 5) == 5
    assert watch.WatchlistTab._coerce_delay_ms(-1, 5) == 0
    assert watch.WatchlistTab._vcp_payload_signature(None) == ()
    signature = watch.WatchlistTab._vcp_payload_signature({"": {}, "000001": {"nested": {"a": [1, 2]}}, "000002": "x"})
    assert [item[0] for item in signature] == ["000001", "000002"]

    tab = SimpleNamespace(_watchlist_last_update="", _now_hhmm=lambda: "09:30")
    assert watch.WatchlistTab._touch_watchlist_update(tab)
    assert not watch.WatchlistTab._touch_watchlist_update(tab, "09:30")

    monkeypatch.setattr(
        watch.MarketCalendar,
        "get_latest_trade_date",
        classmethod(
            lambda cls, market, *, allow_refresh=True: (
                None
                if allow_refresh is False
                else (_ for _ in ()).throw(AssertionError("lineage date must stay cache-only"))
            )
        ),
    )
    monkeypatch.setattr(
        watch.MarketCalendar,
        "today",
        classmethod(lambda cls, market: (_ for _ in ()).throw(ValueError("bad"))),
    )
    assert watch.WatchlistTab._latest_trade_date_text(tab) == ""


def test_live_quote_checks_async_fallback_and_status_paths(monkeypatch):
    tab = SimpleNamespace(data_provider=None)
    assert not watch.WatchlistTab._can_fetch_live_quotes_now(tab)
    tab.data_provider = SimpleNamespace(is_online=lambda: False)
    assert not watch.WatchlistTab._can_fetch_live_quotes_now(tab)
    tab.data_provider = SimpleNamespace(is_online=lambda: True)
    monkeypatch.setattr(
        watch.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("bad"))),
    )
    assert not watch.WatchlistTab._can_fetch_live_quotes_now(tab)

    calls = []
    tab.refresh_table_quotes_and_market_caps = lambda **kwargs: calls.append(kwargs)
    watch.WatchlistTab._run_async_local_quote_refresh(tab, "task")
    assert calls[-1]["async_local"] is True

    def _legacy(**kwargs):
        if "async_local" in kwargs:
            raise TypeError("unexpected async_local")
        calls.append(kwargs)

    tab.refresh_table_quotes_and_market_caps = _legacy
    watch.WatchlistTab._run_async_local_quote_refresh(tab, "legacy")
    assert calls[-1] == {"quote_task_id": "legacy"}
    tab.refresh_table_quotes_and_market_caps = lambda **kwargs: (_ for _ in ()).throw(TypeError("other"))
    with pytest.raises(TypeError):
        watch.WatchlistTab._run_async_local_quote_refresh(tab, "bad")

    state = []
    tab = SimpleNamespace(
        model=SimpleNamespace(row_data=[]),
        proxy_model=SimpleNamespace(rowCount=lambda: 0),
        sp_search=SimpleNamespace(text=lambda: ""),
        lbl_sp_status=SimpleNamespace(setText=lambda text: state.append(text)),
        format_workspace_status=lambda *args, **kwargs: kwargs,
        _watchlist_last_update="",
        table_state=SimpleNamespace(
            show_empty=lambda *args: state.append("empty"),
            show_table=lambda: state.append("table"),
        ),
    )
    watch.WatchlistTab._update_status_summary(tab)
    assert "empty" in state
    tab.model.row_data = [{"来源标签": ["手动"]}, {"来源": "产业链"}]
    tab.proxy_model.rowCount = lambda: 2
    monkeypatch.setattr(
        watch.watchlist_vm, "normalize_source_tags", lambda value: list(value) if isinstance(value, list) else [value]
    )
    monkeypatch.setattr(watch.watchlist_vm, "format_source_tags", lambda values: "/".join(values))
    watch.WatchlistTab._update_status_summary(tab)
    assert state[-1] == "table"


def test_reorder_double_click_and_context_menu(monkeypatch):
    from ui.components import toast_widget

    calls = []
    tab = SimpleNamespace(
        proxy_model=SimpleNamespace(sortColumn=lambda: 1),
        _load_special_data=lambda: calls.append("load"),
    )
    monkeypatch.setattr(watch, "show_toast", lambda *args, **kwargs: calls.append("toast"))
    monkeypatch.setattr(toast_widget, "show_toast", lambda *args, **kwargs: calls.append("toast"))
    watch.WatchlistTab._on_rows_reordered(tab, ["a"])
    assert calls == ["toast", "load"]
    tab.proxy_model.sortColumn = lambda: -1
    monkeypatch.setattr(watch.watchlist_vm, "reorder", lambda codes: calls.append(tuple(codes)))
    watch.WatchlistTab._on_rows_reordered(tab, ["a", "b"])
    assert calls[-2:] == [("a", "b"), "load"]

    signal = _Signal()
    monkeypatch.setattr(watch, "ui_signals", SimpleNamespace(sig_show_kline_with_list=signal))
    monkeypatch.setattr(
        watch.watchlist_vm,
        "get_watchlist_data",
        lambda: {"000001": {"备注": "persisted", "empty": ""}, "000002": {}},
    )
    rows = [{"代码": "000001", "名称": "A"}, {"代码": "000002", "名称": "B"}]
    proxy = SimpleNamespace(
        mapToSource=lambda index: _Index(index.row()),
        rowCount=lambda: 2,
        index=lambda row, col: _Index(row),
    )
    tab = SimpleNamespace(proxy_model=proxy, model=SimpleNamespace(row_data=rows))
    watch.WatchlistTab._on_double_click(tab, _Index(1))
    assert signal.calls[0][0] == "000002" and signal.calls[0][2] == 1
    watch.WatchlistTab._on_double_click(tab, _Index(valid=False))
    proxy.mapToSource = lambda index: _Index(5)
    watch.WatchlistTab._on_double_click(tab, _Index())

    menu_calls = []
    from ui.components import stock_context_menu

    monkeypatch.setattr(stock_context_menu, "build_stock_context_menu", lambda *args: menu_calls.append(args))
    tab.table_sp = SimpleNamespace(indexAt=lambda pos: _Index())
    proxy.mapToSource = lambda index: _Index()
    watch.WatchlistTab._show_context_menu(tab, object())
    assert menu_calls[-1][1:] == ("000001", "A")
    tab.table_sp.indexAt = lambda pos: _Index(valid=False)
    watch.WatchlistTab._show_context_menu(tab, object())


def test_name_map_and_add_custom_stock_all_outcomes(monkeypatch):
    provider = SimpleNamespace(code2name={}, get_all_codes=lambda: {"1": "One", "bad": "Bad"})
    tab = SimpleNamespace(
        data_provider=provider,
        _normalize_quote_code=lambda code: str(code or "").strip(),
    )
    result = watch.WatchlistTab._get_a_share_name_map(tab)
    assert result == {"000001": "One"}
    assert watch.WatchlistTab._get_a_share_name_map(tab) is result

    actions = []
    input_box = SimpleNamespace(
        text=lambda: "bad",
        setFocus=lambda: actions.append("focus"),
        selectAll=lambda: actions.append("select"),
        clear=lambda: actions.append("clear"),
    )
    scheduled_codes = []
    tab = SimpleNamespace(
        add_stock_input=input_box,
        _normalize_quote_code=lambda code: str(code or ""),
        _get_a_share_name_map=lambda: {"000001": "One"},
        _resolve_missing_a_share_name=lambda code: "",
        _schedule_missing_a_share_name_resolution=lambda code: scheduled_codes.append(code),
        lbl_sp_status=SimpleNamespace(setText=lambda text: actions.append(text)),
        format_workspace_status=lambda *args, **kwargs: kwargs,
        model=SimpleNamespace(row_data=[]),
        _watchlist_last_update="",
        _now_hhmm=lambda: "09:30",
        sp_search=SimpleNamespace(text=lambda: ""),
    )
    monkeypatch.setattr(watch, "show_toast", lambda *args, **kwargs: actions.append(args[0]))
    watch.WatchlistTab._add_custom_stock(tab)
    assert "focus" in actions and "select" in actions

    input_box.text = lambda: "000002"
    watch.WatchlistTab._add_custom_stock(tab)
    assert scheduled_codes == ["000002"]
    assert any("正在后台核验" in str(item) for item in actions)

    input_box.text = lambda: "000001"
    monkeypatch.setattr(watch.watchlist_vm, "is_in_watchlist", lambda code: True)
    watch.WatchlistTab._add_custom_stock(tab)
    assert "clear" in actions

    monkeypatch.setattr(watch.watchlist_vm, "is_in_watchlist", lambda code: False)
    monkeypatch.setattr(watch.watchlist_vm, "add_stock", lambda *args, **kwargs: True)
    watch.WatchlistTab._add_custom_stock(tab)
    assert any("已加入" in str(item) for item in actions)
    monkeypatch.setattr(watch.watchlist_vm, "add_stock", lambda *args, **kwargs: False)
    watch.WatchlistTab._add_custom_stock(tab)


def test_gather_radar_coalesced_and_vcp_refresh_variants(monkeypatch):
    tab = SimpleNamespace(window=lambda: SimpleNamespace(_workspace=None))
    assert watch.WatchlistTab._gather_radar_data(tab) == ({}, {}, {}, {}, {}, None)
    workspace = SimpleNamespace(collect_watchlist_radar_data=lambda **kwargs: (1, 2, 3, 4, 5, 6))
    tab.window = lambda: SimpleNamespace(_workspace=workspace)
    assert watch.WatchlistTab._gather_radar_data(tab, ["x"])[5] == 6
    workspace.collect_watchlist_radar_data = lambda **kwargs: (_ for _ in ()).throw(ValueError("bad"))
    assert watch.WatchlistTab._gather_radar_data(tab) == ({}, {}, {}, {}, {}, None)

    toggles = []
    proxy = SimpleNamespace(
        dynamicSortFilter=lambda: True,
        setDynamicSortFilter=lambda value: toggles.append(value),
    )
    tab.proxy_model = proxy
    assert watch.WatchlistTab._run_coalesced_model_update(tab, lambda: 7) == 7
    assert toggles == [False, True]
    with pytest.raises(RuntimeError):
        watch.WatchlistTab._run_coalesced_model_update(tab, lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert toggles[-1] is True

    idle_toggles = []
    tab.proxy_model = SimpleNamespace(
        dynamicSortFilter=lambda: True,
        sortColumn=lambda: -1,
        _filter_text="",
        _exact_column_filters={},
        setDynamicSortFilter=lambda value: idle_toggles.append(value),
    )
    assert watch.WatchlistTab._run_coalesced_model_update(tab, lambda: 8) == 8
    assert idle_toggles == []

    radar = (
        {"000001": "remark"},
        {"000001": "sector"},
        {"000001": {"text": "block", "amount_wan": 3}},
        {"000001": {"text": "earn", "qoq_pct": 4}},
        {"000001": {"date": "today"}},
        {"rps120": {"000001": 80}, "rps250": {"000001": 90}},
    )
    tab._gather_radar_data = lambda codes: radar
    results = watch.WatchlistTab._refresh_vcp_indicators(tab, [(0, "000001")])
    assert results["000001"]["rps"] == "90/80"

    monkeypatch.setattr(
        watch,
        "load_active_rps_payload",
        lambda: (_ for _ in ()).throw(CacheIOError("bad")),
    )
    tab._gather_radar_data = lambda codes: ({}, {}, {}, {}, {}, None)
    assert watch.WatchlistTab._refresh_vcp_indicators(tab, [(0, "000002")])["000002"]["rps"] == "--"


def test_apply_and_persist_metrics_branches(monkeypatch):
    updated = []
    model = SimpleNamespace(
        row_data=[{"代码": "000001", "名称": "A"}],
        update_data=lambda rows, **kwargs: updated.append(rows),
    )
    scheduled = []
    tab = SimpleNamespace(
        model=model,
        _last_vcp_payload_signature=(),
        _vcp_payload_signature=watch.WatchlistTab._vcp_payload_signature,
        _run_coalesced_model_update=lambda callback, **_kwargs: callback(),
        _format_watchlist_note=watch.WatchlistTab._format_watchlist_note,
        _schedule_watchlist_metrics_persist=lambda results: scheduled.append(results),
        _touch_watchlist_update=lambda: True,
        _update_status_summary=lambda: None,
    )
    monkeypatch.setattr(watch.watchlist_vm, "derive_source_tags", lambda *args, **kwargs: ["手动"])
    monkeypatch.setattr(watch.watchlist_vm, "format_source_tags", lambda tags: "/".join(tags))
    payload = {
        "missing": {"rps": "1"},
        "000001": {
            "rps": "90",
            "subsector": "AI",
            "remark": "r",
            "block_trade": "b",
            "earnings": "e",
            "lhb": {"date": "20260715", "net_wan": None, "buy_point": "yes"},
        },
    }
    watch.WatchlistTab._apply_vcp_indicators_ui(tab, payload)
    assert updated[-1][0]["龙虎榜"] == BUY_POINT_TEXT
    assert scheduled == [payload]
    watch.WatchlistTab._apply_vcp_indicators_ui(tab, payload)

    commits = []
    monkeypatch.setattr(watch, "_commit_watchlist_metrics", lambda payload, token: commits.append(payload))
    watch.WatchlistTab._persist_watchlist_metrics(
        tab,
        {
            "000001": {"rps": 90, "subsector": "AI", "lhb": {"date": "d", "net_wan": 1, "buy_point": "x"}},
            "000002": {"rps": "", "lhb": "legacy"},
        },
    )
    assert commits[-1]["000001"]["龙虎榜"] == BUY_POINT_TEXT
    assert "龙榜" not in commits[-1]["000001"]
    assert commits[-1]["000002"]["龙虎榜"] == "legacy"


def test_shutdown_disconnect_save_cache_and_event_wrappers(monkeypatch):
    timers = [_Timer(), None, _Timer(fail=True), _Timer()]
    disconnected = []
    lifecycle = SimpleNamespace(shutdown=lambda timeout_ms: disconnected.append(timeout_ms))
    tab = SimpleNamespace(
        _closing=False,
        _vcp_task_generation=0,
        _pending_vcp_calc=True,
        _pending_vcp_apply_payload={"x": {}},
        _pending_vcp_apply_signature=(1,),
        _delayed_special_timer=timers[0],
        _vcp_calc_timer=timers[1],
        _vcp_apply_timer=timers[2],
        _debounce_timer=timers[3],
        _task_lifecycle=lifecycle,
        _disconnect_runtime_signals=lambda: disconnected.append("signals"),
    )
    watch.WatchlistTab.shutdown(tab)
    assert tab._closing and disconnected == [750, "signals"]

    fake_event = SimpleNamespace(
        **{
            name: _Signal()
            for name in (
                "sig_watchlist_changed",
                "sig_app_closing",
                "sig_cache_bootstrap_ready",
                "sig_cache_reload_completed",
                "sig_earnings_updated",
                "sig_na_daily_updated",
                "sig_ai_industry_chain_updated",
                "sig_block_trade_updated",
                "sig_lhb_pool_updated",
                "sig_fund_holdings_updated",
                "sig_stock_context_snapshot_updated",
                "sig_vcp_watchlist_ready",
            )
        }
    )
    monkeypatch.setattr(watch, "event_bus", fake_event)
    for name in (
        "_on_watchlist_changed",
        "_on_app_closing",
        "_on_cache_or_earnings_updated",
        "_on_na_daily_updated",
        "_on_ai_industry_chain_updated",
        "_on_block_trade_updated",
        "_on_vcp_watchlist_ready",
    ):
        setattr(tab, name, lambda *args: None)
    watch.WatchlistTab._disconnect_runtime_signals(tab)

    current = {"000001": {"催化剂": "x"}, "000002": {"keep": 1}}
    replaced = []
    monkeypatch.setattr(watch.watchlist_vm, "get_watchlist_data", lambda: current)
    monkeypatch.setattr(watch.watchlist_vm, "derive_source_tags", lambda *args, **kwargs: ["手动"])
    monkeypatch.setattr(watch.watchlist_vm, "replace_watchlist_data", lambda data: replaced.append(data))
    idx = _Index(0)
    tab.proxy_model = SimpleNamespace(
        rowCount=lambda: 1,
        index=lambda row, col: idx,
        mapToSource=lambda index: idx,
    )
    tab.model = SimpleNamespace(row_data=[{"代码": "000001", "RPS强度": "90", "摘要": "note"}])
    watch.WatchlistTab._save_special_cache_from_table(tab)
    assert list(replaced[-1]) == ["000001", "000002"]
    assert "催化剂" not in replaced[-1]["000001"]


def test_vcp_defer_schedule_flush_request_and_do_calc(monkeypatch):
    tab = SimpleNamespace(
        _closing=False,
        _deferred_vcp_signature=(),
        _deferred_vcp_payload=None,
        _last_vcp_payload_signature=(),
        _pending_vcp_apply_signature=(),
        _pending_vcp_apply_payload=None,
        _vcp_payload_signature=watch.WatchlistTab._vcp_payload_signature,
        _is_active_workspace_tab_for_vcp=lambda: False,
    )
    payload = {"000001": {"rps": 90}}
    watch.WatchlistTab._defer_vcp_payload(tab, payload)
    watch.WatchlistTab._defer_vcp_payload(tab, payload)
    assert tab._deferred_vcp_payload == payload

    tab._defer_vcp_payload = lambda value, signature=None: setattr(tab, "deferred", value)
    watch.WatchlistTab._schedule_vcp_payload_apply(tab, payload)
    assert tab.deferred == payload

    applied = []
    tab._pending_vcp_apply_payload = payload
    tab._pending_vcp_apply_signature = (1,)
    tab._is_active_workspace_tab_for_vcp = lambda: True
    tab._apply_vcp_indicators_ui = lambda value: applied.append(value)
    watch.WatchlistTab._flush_pending_vcp_apply(tab)
    assert applied == [payload]

    tab = SimpleNamespace(
        _closing=False,
        _startup_indicator_refresh_enabled=False,
        _workspace_noninteractive_loaded=True,
        _pending_vcp_calc=False,
        _is_background_prewarm_indicator_blocked=lambda: True,
        _is_active_workspace_tab_for_vcp=lambda: True,
    )
    watch.WatchlistTab._request_vcp_calc(tab)
    assert tab._pending_vcp_calc

    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(watch, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    tab = SimpleNamespace(
        _closing=False,
        _vcp_calc_allow_noninteractive=True,
        _is_active_workspace_tab_for_vcp=lambda: False,
        model=SimpleNamespace(row_data=[{"代码": "000001"}, {}]),
        _last_vcp_calc_started_at=0.0,
        _vcp_task_generation=0,
    )
    watch.WatchlistTab._do_vcp_calc(tab)
    assert captured["on_success"] is not None and tab._vcp_task_generation == 1


def test_show_hide_deferred_payload_and_pending_calc(monkeypatch):
    fake_event = SimpleNamespace(
        **{
            name: _Signal()
            for name in (
                "sig_watchlist_changed",
                "sig_app_closing",
                "sig_cache_bootstrap_ready",
                "sig_cache_reload_completed",
                "sig_earnings_updated",
                "sig_na_daily_updated",
                "sig_ai_industry_chain_updated",
                "sig_block_trade_updated",
                "sig_lhb_pool_updated",
                "sig_fund_holdings_updated",
                "sig_stock_context_snapshot_updated",
                "sig_vcp_watchlist_ready",
            )
        }
    )
    for signal in vars(fake_event).values():
        signal.connect = lambda callback: None
    monkeypatch.setattr(watch, "event_bus", fake_event)
    monkeypatch.setattr(watch.BaseStockTab, "__init__", lambda self, data_provider=None, parent=None: None)
    monkeypatch.setattr(watch.WatchlistTab, "_init_ui", lambda self: None)
    monkeypatch.setattr(watch.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    tab = watch.WatchlistTab(object(), startup_tasks_enabled=False)

    calls = []
    monkeypatch.setattr(watch.BaseStockTab, "showEvent", lambda self, event: calls.append("show"))
    monkeypatch.setattr(watch.BaseStockTab, "hideEvent", lambda self, event: calls.append("hide"))
    tab._deferred_vcp_payload = {"x": {}}
    tab._deferred_vcp_signature = (1,)
    tab._is_active_workspace_tab_for_vcp = lambda: True
    tab._schedule_vcp_payload_apply = lambda payload, delay_ms=None: calls.append((payload, delay_ms))
    tab._vcp_apply_delay_ms = lambda: 10
    tab._pending_vcp_calc = True
    tab._should_start_interactive_runtime_on_show = lambda: True
    tab._request_vcp_calc = lambda **kwargs: calls.append(kwargs)
    watch.WatchlistTab.showEvent(tab, object())
    assert calls[0] == "show" and tab._deferred_vcp_payload is None
    assert any(isinstance(call, dict) for call in calls)

    tab._vcp_calc_timer = _Timer(active=True)
    tab._vcp_apply_timer = _Timer(active=True)
    tab._pending_vcp_apply_payload = {"y": {}}
    tab._pending_vcp_apply_signature = (2,)
    tab._defer_vcp_payload = lambda payload, signature: calls.append(("defer", payload, signature))
    watch.WatchlistTab.hideEvent(tab, object())
    assert tab._pending_vcp_calc and tab._pending_vcp_apply_payload is None


def test_lineage_load_guard_reset_apply_quote_and_debounce(monkeypatch):
    result = SimpleNamespace(
        signature="sig",
        lineage=SimpleNamespace(as_dynamic_dict=lambda: {"ok": 1}),
        rows=[],
    )
    tab = SimpleNamespace(
        _last_watchlist_result=None,
        _last_watchlist_signature="",
        model=SimpleNamespace(row_data=[]),
        _describe_watchlist_rows=lambda rows: result,
    )
    assert watch.WatchlistTab.get_data_lineage(tab) == {"ok": 1}
    assert tab._last_watchlist_signature == "sig"

    tab = SimpleNamespace(_closing=True)
    watch.WatchlistTab._load_special_data(tab)

    calls = []
    tab = SimpleNamespace(
        table_sp=SimpleNamespace(sortByColumn=lambda *args: calls.append(args)),
        window=lambda: object(),
    )
    monkeypatch.setattr(watch, "show_toast", lambda *args, **kwargs: calls.append("toast"))
    watch.WatchlistTab._reset_view(tab)
    assert calls[0][0] == -1 and calls[-1] == "toast"

    actual = watch.WatchlistTab.__new__(watch.WatchlistTab)
    actual.proxy_model = SimpleNamespace(dynamicSortFilter=lambda: False)
    monkeypatch.setattr(watch.BaseStockTab, "_apply_quote_snapshot", lambda self, quotes: calls.append(quotes) or 3)
    assert watch.WatchlistTab._apply_quote_snapshot(actual, {"x": {}}) == 3

    class _FakeQTimer:
        def __init__(self, parent=None):
            self.timeout = SimpleNamespace(connect=lambda callback: calls.append(callback))
            self.started = []

        def setSingleShot(self, value):
            calls.append(value)

        def start(self, value):
            self.started.append(value)

    monkeypatch.setattr(watch, "QTimer", _FakeQTimer)
    tab = SimpleNamespace(_do_watchlist_reload=lambda: calls.append("reload"))
    watch.WatchlistTab._on_watchlist_changed(tab, "add", "x")
    assert tab._debounce_timer.started[-1] == 80
    watch.WatchlistTab._on_watchlist_changed(tab, "delete", "x")
    assert tab._debounce_timer.started[-1] == 300
    tab._load_special_data = lambda: calls.append("load")
    watch.WatchlistTab._do_watchlist_reload(tab)


def test_vcp_refresh_non_dict_sources_per_code_and_outer_error(monkeypatch):
    tab = SimpleNamespace(_gather_radar_data=lambda codes: None)
    monkeypatch.setattr(
        watch,
        "load_active_rps_payload",
        lambda: {"rps120": {"000001": 70}, "rps250": {"000001": 80}},
    )
    radar = ({}, {}, {"000001": "block"}, {"000001": "earn"}, {}, None)
    result = watch.WatchlistTab._refresh_vcp_indicators(tab, [(0, "000001")], radar_data_tuple=radar)
    assert result["000001"]["block_trade"] == "block"
    assert result["000001"]["earnings"] == "earn"

    bad_bundle = {"rps120": {"bad": object()}, "rps250": {"bad": 1}}
    radar = ({}, {}, {}, {}, {}, bad_bundle)
    assert watch.WatchlistTab._refresh_vcp_indicators(tab, [(0, "bad")], radar_data_tuple=radar) == {}
    assert watch.WatchlistTab._refresh_vcp_indicators(tab, None) is None


def test_schedule_metrics_app_closing_save_empty_and_error(monkeypatch):
    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(watch, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    tab = SimpleNamespace(_closing=True)
    watch.WatchlistTab._schedule_watchlist_metrics_persist(tab, {"x": {}})
    tab._closing = False
    watch.WatchlistTab._schedule_watchlist_metrics_persist(tab, {"": {}})
    watch.WatchlistTab._schedule_watchlist_metrics_persist(tab, {"x": {"rps": 1}})
    assert captured["on_error"] is not None

    calls = []
    tab = SimpleNamespace(
        shutdown=lambda: calls.append("shutdown"),
        model=SimpleNamespace(row_data=[{}]),
        _save_special_cache_from_table=lambda: calls.append("save"),
    )
    watch.WatchlistTab._on_app_closing(tab)
    assert calls == ["shutdown", "save"]

    monkeypatch.setattr(watch.watchlist_vm, "get_watchlist_data", lambda: {})
    watch.WatchlistTab._save_special_cache_from_table(SimpleNamespace())
    monkeypatch.setattr(
        watch.watchlist_vm,
        "get_watchlist_data",
        lambda: (_ for _ in ()).throw(ValueError("bad")),
    )
    watch.WatchlistTab._save_special_cache_from_table(SimpleNamespace())


def test_event_ready_delay_timer_active_guard_and_name_refresh(monkeypatch):
    scheduled = []
    tab = SimpleNamespace(
        _closing=True,
        _last_vcp_payload_signature=(),
        _deferred_vcp_signature=(),
        _is_active_workspace_tab_for_vcp=lambda: True,
        _schedule_vcp_payload_apply=lambda payload: scheduled.append(payload),
        _vcp_payload_signature=watch.WatchlistTab._vcp_payload_signature,
    )
    watch.WatchlistTab._on_vcp_watchlist_ready(tab, {"x": {}})
    tab._closing = False
    watch.WatchlistTab._on_vcp_watchlist_ready(tab, None)
    tab._last_vcp_payload_signature = watch.WatchlistTab._vcp_payload_signature({"x": {}})
    watch.WatchlistTab._on_vcp_watchlist_ready(tab, {"x": {}})
    tab._last_vcp_payload_signature = ()
    watch.WatchlistTab._on_vcp_watchlist_ready(tab, {"x": {}})
    assert scheduled == [{"x": {}}]

    monkeypatch.setattr(watch.time, "monotonic", lambda: 11.0)
    tab = SimpleNamespace(
        FOREGROUND_VCP_APPLY_DELAY_MS=150, POST_SHOW_VCP_APPLY_SETTLE_MS=2500, _last_vcp_tab_shown_at=10.0
    )
    assert watch.WatchlistTab._vcp_apply_delay_ms(tab) == 1500

    class _FakeQTimer:
        def __init__(self, parent=None):
            self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, "callback", callback))

        def setSingleShot(self, value):
            self.single = value

    monkeypatch.setattr(watch, "QTimer", _FakeQTimer)
    tab = SimpleNamespace(_vcp_apply_timer=None, _flush_pending_vcp_apply=lambda: None)
    timer = watch.WatchlistTab._ensure_vcp_apply_timer(tab)
    assert timer.single and watch.WatchlistTab._ensure_vcp_apply_timer(tab) is timer

    tab = SimpleNamespace(_is_current_workspace_tab=lambda: False)
    assert not watch.WatchlistTab._is_active_workspace_tab_for_vcp(tab)
    tab._is_current_workspace_tab = lambda: True
    tab.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    assert not watch.WatchlistTab._is_active_workspace_tab_for_vcp(tab)

    updated = []
    model = SimpleNamespace(
        row_data=[{"代码": "000001", "名称": "000001"}, {"代码": "000002", "名称": "Named"}],
        set_cell_value=lambda row, header, value, **kwargs: updated.append((row, header, value, kwargs)),
    )
    tab = SimpleNamespace(model=model, _run_coalesced_model_update=lambda callback, **_kwargs: callback())
    assert watch.WatchlistTab.refresh_watchlist_names(tab, {"000001": "One"})
    assert updated == [(0, "名称", "One", {"record_flash": False})]
    assert not watch.WatchlistTab.refresh_watchlist_names(SimpleNamespace(model=None), {})


def test_prime_do_calc_guards_filter_and_rt_quote_wrapper(monkeypatch):
    tab = SimpleNamespace(_closing=True)
    watch.WatchlistTab.prime_startup_state(tab)
    tab._closing = False
    tab.model = SimpleNamespace(row_data=[])
    watch.WatchlistTab.prime_startup_state(tab)

    tab = SimpleNamespace(_closing=True)
    watch.WatchlistTab._do_vcp_calc(tab)
    tab._closing = False
    tab._vcp_calc_allow_noninteractive = False
    tab._is_active_workspace_tab_for_vcp = lambda: False
    tab._pending_vcp_calc = False
    watch.WatchlistTab._do_vcp_calc(tab)
    assert tab._pending_vcp_calc

    calls = []
    tab = SimpleNamespace(
        proxy_model=object(),
        set_proxy_filter_text=lambda proxy, text: calls.append(text),
        _update_status_summary=lambda: calls.append("status"),
    )
    watch.WatchlistTab._filter_table(tab, "abc")
    assert calls == ["abc", "status"]

    actual = watch.WatchlistTab.__new__(watch.WatchlistTab)
    monkeypatch.setattr(watch.BaseStockTab, "_on_rt_quotes_direct", lambda self, quotes: calls.append(quotes))
    actual.isVisible = lambda: True
    actual._touch_watchlist_update = lambda: True
    actual._update_status_summary = lambda: calls.append("updated")
    watch.WatchlistTab._on_rt_quotes_direct(actual, {"x": {}})
    assert calls[-1] == "updated"
