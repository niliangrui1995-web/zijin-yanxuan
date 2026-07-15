# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ui_task_lifecycle_service import TaskCancelledError
from ui.tabs import lhb_tab as lhb


class _Token:
    def __init__(self, wait_result=False):
        self.calls = 0
        self.wait_result = wait_result

    def raise_if_cancelled(self):
        self.calls += 1

    def wait(self, seconds):
        return self.wait_result


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Button:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, value):
        self.enabled = bool(value)


class _State:
    def __init__(self):
        self.calls = []

    def show_table(self):
        self.calls.append(("table",))

    def show_empty(self, *args):
        self.calls.append(("empty", *args))

    def show_loading(self, *args):
        self.calls.append(("loading", *args))

    def show_error(self, *args, **kwargs):
        self.calls.append(("error", args, kwargs))


class _Manager:
    def __init__(self):
        self.added = []
        self.probes = []
        self.saved = 0

    def prune(self, dates):
        self.pruned = list(dates)

    def compute_pool(self, **kwargs):
        return [{"代码": "000001"}]

    def get_missing_dates(self, dates):
        return [dates[0]] if dates else []

    def get_dates_pending_validation(self, dates, ref):
        return [dates[-1]] if dates else []

    def get_cached_record_count(self, date):
        return 2

    def add_day(self, date, records, meta=None):
        self.added.append((date, records, meta))

    def mark_day_probe(self, date, **kwargs):
        self.probes.append((date, kwargs))

    def save(self):
        self.saved += 1

    def get_cached_dates(self):
        return ["20260714", "20260715"]


def test_finish_retry_and_pool_payload_branches(monkeypatch):
    state = _State()
    owner = SimpleNamespace(
        model=SimpleNamespace(row_data=[{}]),
        table_state=state,
        _manual_refresh=lambda: None,
    )
    lhb._finish_lhb_backfill_error(owner, "title", "message")
    assert state.calls[-1][0] == "table"
    owner.model.row_data = []
    lhb._finish_lhb_backfill_error(owner, "title", "message")
    assert state.calls[-1][0] == "error"

    calls = []
    owner._pending_backfill_request = (["d"], [], "ref")
    owner._start_backfill = lambda *args: calls.append(args)
    owner._load_and_display_pool = lambda: calls.append("load")
    lhb._retry_lhb_pool(owner)
    assert calls[-1][0] == ["d"]
    lhb._retry_lhb_pool(owner)
    assert calls[-1] == "load"

    token = _Token()
    owner._get_lhb_trade_dates = lambda: []
    assert lhb._load_lhb_pool_payload(owner, token) == {"status": "calendar_missing"}

    manager = _Manager()
    monkeypatch.setattr(lhb, "LhbPoolManager", lambda: manager)
    owner._get_lhb_trade_dates = lambda: ["20260714", "20260715"]
    owner.data_provider = object()
    owner._get_engine = lambda: object()
    owner._load_ai_chain_context_map = lambda: {"000001": "AI"}
    owner._build_pool_display_rows = lambda pool, context: [{"row": context["000001"]}]
    payload = lhb._load_lhb_pool_payload(owner, token)
    assert payload["status"] == "ok" and payload["row_data"] == [{"row": "AI"}]


def test_wait_as_payload_fetch_missing_success_error_and_cancel(monkeypatch):
    token = _Token(wait_result=True)
    lhb._wait_lhb_backfill_step(token, 1, 2)
    assert token.calls == 1
    lhb._wait_lhb_backfill_step(token, 2, 2)
    assert lhb._as_lhb_fetch_payload({"status": "empty"})["status"] == "empty"
    assert lhb._as_lhb_fetch_payload([1, 2])["count"] == 2

    from app.services import lhb_market_data_service

    responses = {
        "a": {"status": "ok", "records": [1], "count": 1},
        "b": {"status": "error", "records": []},
        "c": ValueError("bad"),
    }

    def _fetch(date, **kwargs):
        value = responses[date]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(lhb_market_data_service, "fetch_lhb_pool_for_date", _fetch)
    logs = []
    owner = SimpleNamespace(
        _build_backfill_progress_log=lambda step, total, date, payload: ("info", f"{date}:{payload.get('status')}")
    )
    fetched, step = lhb._fetch_missing_lhb_dates(owner, ["a", "b", "c"], 3, lambda *args: logs.append(args), _Token())
    assert list(fetched) == ["a"] and step == 3 and len(logs) == 3

    monkeypatch.setattr(
        lhb_market_data_service,
        "fetch_lhb_pool_for_date",
        lambda *args, **kwargs: (_ for _ in ()).throw(TaskCancelledError("stop")),
    )
    with pytest.raises(TaskCancelledError):
        lhb._fetch_missing_lhb_dates(owner, ["a"], 1, lambda *args: None, _Token())


def test_probe_messages_and_validate_date_all_paths(monkeypatch):
    assert lhb._probe_validation_message("d", 1, 2, 3, {"status": "ok"})[0] == "info"
    assert lhb._probe_validation_message("d", 1, 2, 3, {"status": "empty"})[0] == "warn"
    assert "异常" in lhb._probe_validation_message("d", 1, 2, 3, {"status": "error"})[1]

    from app.services import lhb_market_data_service

    manager = _Manager()
    owner = SimpleNamespace(_should_refresh_after_probe=lambda cached, payload: False)
    monkeypatch.setattr(
        lhb_market_data_service,
        "probe_lhb_detail_count_for_date",
        lambda *args, **kwargs: {"status": "ok", "count": 2},
    )
    result = lhb._validate_lhb_date(owner, manager, "d", "ref", 1, 1, _Token())
    assert result[0] is None and result[1]["count"] == 2

    owner._should_refresh_after_probe = lambda cached, payload: True
    monkeypatch.setattr(
        lhb_market_data_service,
        "fetch_lhb_pool_for_date",
        lambda *args, **kwargs: {"status": "error"},
    )
    result = lhb._validate_lhb_date(owner, manager, "d", "ref", 1, 1, _Token())
    assert result[1]["status"] == "repair_failed"

    monkeypatch.setattr(
        lhb_market_data_service,
        "fetch_lhb_pool_for_date",
        lambda *args, **kwargs: {"status": "ok", "count": 4, "records": [1, 2]},
    )
    monkeypatch.setattr(
        lhb_market_data_service,
        "probe_lhb_detail_count_for_date",
        lambda *args, **kwargs: {"status": "ok", "count": 4},
    )
    result = lhb._validate_lhb_date(owner, manager, "d", "ref", 1, 1, _Token())
    assert result[0]["meta"]["source_count"] == 4 and result[1] is None


def test_validate_dates_merge_and_build_payload(monkeypatch):
    manager = _Manager()
    token = _Token()
    calls = []
    outcomes = {
        "repair": ({"records": [1]}, None, "warn", "fixed"),
        "probe": (None, {"count": 2, "status": "ok"}, "info", "valid"),
        "bad": ValueError("bad"),
    }

    def _validate(owner, manager, date, *args):
        value = outcomes[date]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(lhb, "_validate_lhb_date", _validate)
    fetched, validated = lhb._validate_lhb_dates(
        object(), manager, ["repair", "probe", "bad"], "ref", 0, 3, lambda *args: calls.append(args), token
    )
    assert list(fetched) == ["repair"] and list(validated) == ["probe"] and len(calls) == 3

    lhb._merge_lhb_backfill_state(
        manager,
        {"d": {"records": [1], "meta": {"x": 1}}},
        {"e": {"count": 3, "status": "empty"}},
        "ref",
        token,
    )
    assert manager.added and manager.probes

    monkeypatch.setattr(lhb, "LhbPoolManager", lambda: manager)
    monkeypatch.setattr(lhb, "_fetch_missing_lhb_dates", lambda *args: ({"d": {"records": [1]}}, 1))
    monkeypatch.setattr(lhb, "_validate_lhb_dates", lambda *args: ({"e": {"records": [2]}}, {"f": {"count": 1}}))
    owner = SimpleNamespace(
        data_provider=object(),
        _get_engine=lambda: object(),
        _load_ai_chain_context_map=lambda: {"x": "AI"},
        _build_pool_display_rows=lambda pool, context: [{"ok": 1}],
    )
    result = lhb._build_lhb_backfill_payload(owner, ["d"], ["f"], "ref", lambda *args: None, token)
    assert result["row_data"] == [{"ok": 1}] and manager.saved == 1


class _LhbDummy:
    def __init__(self):
        self._pool_bootstrap_not_before = 0.0
        self._post_f5_pool_defer_until = 0.0
        self._pool_load_in_progress = False
        self._pool_bootstrap_started = True
        self._calendar_retry_count = 0
        self._pending_pool_refresh = False
        self._handling_lhb_pool_update = False
        self._post_f5_pool_pending = False
        self._post_f5_pool_emit_event = False
        self._backfill_in_progress = False
        self._pending_backfill_request = None
        self.pool_manager = None
        self._ai_chain_context_map = None
        self.data_provider = object()
        self.model = SimpleNamespace(row_data=[])
        self.table_state = _State()
        self.btn_refresh = _Button()
        self.calls = []

    def _set_pool_status(self, *args, **kwargs):
        self.calls.append(("status", args, kwargs))

    def _schedule_post_f5_pool_load(self, emit_event=True):
        self.calls.append(("schedule_post", emit_event))
        return True

    def _schedule_pool_retry(self):
        self.calls.append(("retry",))

    def _display_pool(self, *args, **kwargs):
        self.calls.append(("display", args, kwargs))

    def _start_backfill(self, *args):
        self.calls.append(("backfill", args))

    def _schedule_pending_pool_refresh(self):
        self.calls.append(("pending",))

    def _load_and_display_pool(self, emit_event=True):
        self.calls.append(("load", emit_event))

    def _run_post_f5_pool_load(self):
        return lhb.LhbTab._run_post_f5_pool_load(self)

    def _ensure_pool_bootstrap_started(self):
        self.calls.append(("ensure",))

    def _get_pool_manager(self):
        if self.pool_manager is None:
            self.pool_manager = _Manager()
        return self.pool_manager


def test_load_pool_defer_guards_and_callbacks(monkeypatch):
    tab = _LhbDummy()
    monkeypatch.setattr(lhb.time, "monotonic", lambda: 10.0)
    tab._pool_bootstrap_not_before = 20.0
    lhb.LhbTab._load_and_display_pool(tab, emit_event=False)
    assert tab.calls[-1] == ("schedule_post", False)

    tab._pool_bootstrap_not_before = 0.0
    tab._pool_load_in_progress = True
    lhb.LhbTab._load_and_display_pool(tab)
    tab._pool_load_in_progress = False
    monkeypatch.setattr(lhb.task_manager, "is_active_task", lambda task_id: True)
    lhb.LhbTab._load_and_display_pool(tab)

    monkeypatch.setattr(lhb.task_manager, "is_active_task", lambda task_id: False)
    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(lhb, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    lhb.LhbTab._load_and_display_pool(tab, emit_event=False)
    assert tab._pool_load_in_progress and captured["on_success"] is not None

    for _ in range(4):
        captured["on_success"]({"status": "calendar_missing"})
    assert tab._calendar_retry_count == 4
    captured["on_success"](
        {
            "status": "ok",
            "pool_manager": _Manager(),
            "ai_chain_context_map": {"x": "AI"},
            "pool": [{"代码": "x"}],
            "row_data": [{"代码": "x"}],
            "missing": ["d"],
            "pending_validation": ["e"],
            "validation_ref_date": "ref",
        }
    )
    assert any(call[0] == "display" for call in tab.calls)
    assert any(call[0] == "backfill" for call in tab.calls)

    tab._pending_pool_refresh = True
    captured["on_success"]({"status": "ok", "pool": [], "missing": [], "pending_validation": []})
    assert any(call[0] == "pending" for call in tab.calls)
    captured["on_error"]("bad")
    assert not tab._pool_bootstrap_started and tab.table_state.calls[-1][0] == "error"


def test_post_f5_schedule_run_and_small_pool_helpers(monkeypatch):
    tab = _LhbDummy()
    singles = []
    monkeypatch.setattr(lhb.QTimer, "singleShot", lambda delay, callback: singles.append((delay, callback)))
    monkeypatch.setattr(lhb.time, "monotonic", lambda: 10.0)
    tab._post_f5_pool_defer_until = 11.0
    assert lhb.LhbTab._schedule_post_f5_pool_load(tab, emit_event=False)
    assert not lhb.LhbTab._schedule_post_f5_pool_load(tab, emit_event=True)
    assert singles[0][0] == 1000
    assert lhb.LhbTab._run_post_f5_pool_load(tab)
    assert tab.calls[-1] == ("schedule_post", True)
    tab._post_f5_pool_defer_until = 0.0
    assert lhb.LhbTab._run_post_f5_pool_load(tab)
    assert tab.calls[-1] == ("load", True)

    tab.pool_manager = _Manager()
    assert lhb.LhbTab._latest_cached_trade_date(tab) == "20260715"
    assert lhb.LhbTab._latest_loaded_cached_trade_date(tab) == "20260715"
    assert lhb.LhbTab._cached_pool_day_count(tab) == 2
    tab.pool_manager.get_cached_dates = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    assert lhb.LhbTab._latest_loaded_cached_trade_date(tab) == ""
    assert lhb.LhbTab._cached_pool_day_count(tab) == 0


def test_lineage_status_sort_quote_helpers_and_navigation(monkeypatch):
    tab = _LhbDummy()
    tab._pool_bootstrap_started = False
    assert lhb.LhbTab._lhb_lineage_status(tab, []) == "deferred"
    tab._pool_bootstrap_started = True
    assert lhb.LhbTab._lhb_lineage_status(tab, []) == "empty"
    tab._pool_load_in_progress = True
    assert lhb.LhbTab._lhb_lineage_status(tab, []) == "loading"
    tab._backfill_in_progress = True
    assert lhb.LhbTab._lhb_lineage_status(tab, []) == "syncing"
    tab._backfill_in_progress = False
    tab._pool_load_in_progress = False
    assert lhb.LhbTab._lhb_lineage_status(tab, [{}]) == "loaded"

    tab.proxy_model = SimpleNamespace(sortColumn=lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert lhb.LhbTab._is_default_lhb_sort_active(tab)
    tab._pending_quote_snapshot = {"a": {}, "b": {}}
    tab._is_opening_warmup_window = lambda: False
    assert lhb.LhbTab._quote_apply_chunk_size(tab) == 2
    tab._pending_quote_snapshot = {}
    tab._applying_pending_quote_snapshot = False
    tab._runtime_cleanup_done = False
    assert not lhb.LhbTab._should_defer_visible_quote_snapshot(tab, {})

    calls = []
    monkeypatch.setattr(lhb, "_show_kline_from_proxy_index", lambda *args: calls.append("kline"))
    monkeypatch.setattr(lhb, "_show_stock_context_menu_from_proxy_index", lambda *args: calls.append("menu"))
    lhb.LhbTab._on_double_click(tab, object())
    lhb.LhbTab._show_context_menu(tab, object())
    assert calls == ["kline", "menu"]


def test_bootstrap_update_cache_events_engine_and_context(monkeypatch):
    tab = _LhbDummy()
    tab._pool_bootstrap_started = False
    tab._initial_load_delay_ms = 10
    singles = []
    monkeypatch.setattr(lhb.QTimer, "singleShot", lambda delay, callback: singles.append((delay, callback)))
    monkeypatch.setattr(lhb.time, "monotonic", lambda: 100.0)
    lhb.LhbTab._ensure_pool_bootstrap_started(tab, delay_ms="bad")
    assert tab._pool_bootstrap_started and tab.calls[-1] == ("load", True)
    lhb.LhbTab._ensure_pool_bootstrap_started(tab, delay_ms=5)

    tab._pool_bootstrap_started = False
    lhb.LhbTab._ensure_pool_bootstrap_started(tab, delay_ms=25)
    assert singles[-1][0] == 25 and tab._pool_bootstrap_not_before > 100

    tab._handling_lhb_pool_update = True
    lhb.LhbTab._on_lhb_pool_updated(tab)
    tab._handling_lhb_pool_update = False
    tab._pool_bootstrap_started = False
    lhb.LhbTab._on_lhb_pool_updated(tab)
    tab._pool_bootstrap_started = True
    tab.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    tab._is_current_workspace_tab = lambda: True
    lhb.LhbTab._on_lhb_pool_updated(tab)
    assert tab._pending_pool_refresh
    tab.isVisible = lambda: True
    tab._pending_pool_refresh = False
    lhb.LhbTab._on_lhb_pool_updated(tab)
    assert tab.calls[-1] == ("pending",)

    tab._rps_injected_flag = True
    lhb.LhbTab._on_cache_bootstrap_ready(tab)
    tab._rps_injected_flag = False
    tab._pool_bootstrap_started = False
    lhb.LhbTab._on_cache_bootstrap_ready(tab)
    tab._pool_bootstrap_started = True
    lhb.LhbTab._on_cache_bootstrap_ready(tab)
    lhb.LhbTab._on_cache_reload_completed(tab)
    tab._pool_bootstrap_started = False
    assert not lhb.LhbTab.refresh_data_after_ai_industry_chain_update(tab)

    monkeypatch.setattr(lhb, "create_scan_engine", lambda: (_ for _ in ()).throw(OSError("bad")))
    assert lhb.LhbTab._get_engine() is None
    monkeypatch.setattr(lhb.LhbTab, "_chain_context_provider", lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert lhb.LhbTab._load_ai_chain_context_map() == {}
    monkeypatch.setattr(lhb, "normalize_ai_chain_code", lambda code: "")
    assert lhb.LhbTab._context_text_for_code("bad", {}) == lhb.LhbTab._DISPLAY_PLACEHOLDER


def test_pending_pool_refresh_timer_paths(monkeypatch):
    tab = _LhbDummy()
    tab._pool_load_in_progress = True
    assert not lhb.LhbTab._schedule_pending_pool_refresh(tab)
    tab._pool_load_in_progress = False
    tab._pool_update_refresh_timer = None
    tab._run_pending_pool_refresh = lambda: True
    assert lhb.LhbTab._schedule_pending_pool_refresh(tab)

    timer = SimpleNamespace(isActive=lambda: True, start=lambda delay: None)
    tab._pool_update_refresh_timer = timer
    assert not lhb.LhbTab._schedule_pending_pool_refresh(tab)
    started = []
    timer.isActive = lambda: False
    timer.start = lambda delay: started.append(delay)
    assert lhb.LhbTab._schedule_pending_pool_refresh(tab, delay_ms="bad")
    assert started[-1] == lhb.LHB_POOL_UPDATE_DEBOUNCE_MS

    tab._pending_pool_refresh = False
    assert not lhb.LhbTab._run_pending_pool_refresh(tab)
    tab._pending_pool_refresh = True
    tab.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    tab._is_current_workspace_tab = lambda: True
    assert not lhb.LhbTab._run_pending_pool_refresh(tab)
    tab.isVisible = lambda: True
    assert lhb.LhbTab._run_pending_pool_refresh(tab) is None
    assert tab.calls[-1] == ("load", False)


def test_reference_dates_manual_refresh_strategy_branches(monkeypatch):
    import datetime as dt

    from app.services import lhb_market_data_service

    now = dt.datetime(2026, 7, 15, 21)
    monkeypatch.setattr(lhb.MarketCalendar, "_get_market_now", classmethod(lambda cls, market: now))
    monkeypatch.setattr(
        lhb.MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market, ref_date=None: None)
    )
    assert lhb.LhbTab._get_lhb_reference_trade_date() is None
    assert lhb.LhbTab._get_lhb_trade_dates(SimpleNamespace(_get_lhb_reference_trade_date=lambda: None)) == []

    fallback = dt.date(2026, 7, 14)
    monkeypatch.setattr(lhb.LhbTab, "_get_lhb_reference_trade_date", staticmethod(lambda: fallback))
    monkeypatch.setattr(lhb.MarketCalendar, "is_trade_day", classmethod(lambda cls, date, market: True))
    monkeypatch.setattr(
        lhb.MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market, ref_date=None: fallback)
    )
    monkeypatch.setattr(
        lhb.MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n, ref_date=None: [ref_date.strftime("%Y%m%d")]),
    )

    monkeypatch.setattr(
        lhb_market_data_service,
        "probe_lhb_detail_count_for_date",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("bad")),
    )
    dates, message, level = lhb.LhbTab._get_manual_refresh_trade_dates()
    assert dates == ["20260714"] and level == "warn"

    monkeypatch.setattr(
        lhb_market_data_service,
        "probe_lhb_detail_count_for_date",
        lambda *args, **kwargs: {"status": "empty", "count": 0},
    )
    dates, message, level = lhb.LhbTab._get_manual_refresh_trade_dates()
    assert dates == ["20260714"] and level == "info"

    monkeypatch.setattr(
        lhb.MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market, ref_date=None: None)
    )
    dates, message, level = lhb.LhbTab._get_manual_refresh_trade_dates()
    assert dates == [] and level == "warn"

    monkeypatch.setattr(
        lhb.MarketCalendar, "get_latest_trade_date", classmethod(lambda cls, market, ref_date=None: fallback)
    )
    monkeypatch.setattr(
        lhb_market_data_service,
        "probe_lhb_detail_count_for_date",
        lambda *args, **kwargs: {"status": "error", "count": 0},
    )
    dates, message, level = lhb.LhbTab._get_manual_refresh_trade_dates()
    assert level == "warn"


def test_sort_opening_quote_and_display_empty_branches(monkeypatch):
    tab = _LhbDummy()
    tab.proxy_model = SimpleNamespace(sortColumn=lambda: 1)
    tab._is_default_lhb_sort_active = lambda: lhb.LhbTab._is_default_lhb_sort_active(tab)
    assert lhb.LhbTab._sort_model_for_default_lhb_order(tab) is None
    tab.proxy_model.sortColumn = lambda: -1
    tab.model = SimpleNamespace(row_data=[])
    lhb.LhbTab._sort_model_for_default_lhb_order(tab)

    monkeypatch.setattr(
        lhb.MarketCalendar,
        "get_market_status",
        classmethod(lambda cls, market: (_ for _ in ()).throw(OSError("bad"))),
    )
    assert not lhb.LhbTab._is_opening_warmup_window(tab)
    tab._pending_quote_snapshot = {"x": {}}
    tab._is_opening_warmup_window = lambda: True
    tab.OPENING_WARMUP_QUOTE_APPLY_CHUNK_SIZE = "bad"
    assert lhb.LhbTab._quote_apply_chunk_size(tab) == 20
    tab._pending_quote_snapshot = {}
    lhb.LhbTab._flush_pending_quote_snapshot(tab)
    assert tab._pending_quote_snapshot == {}

    tab._is_default_lhb_sort_active = lambda: False
    lhb.LhbTab._schedule_default_lhb_quote_sort(tab)
    tab._is_default_lhb_sort_active = lambda: True
    tab.model = SimpleNamespace(row_data=[])
    lhb.LhbTab._schedule_default_lhb_quote_sort(tab)

    signal = _Signal()
    monkeypatch.setattr(lhb, "event_bus", SimpleNamespace(sig_lhb_pool_updated=signal))
    tab._build_pool_display_rows = lambda pool, context: []
    tab._get_ai_chain_context_map = lambda: {}
    tab._describe_lhb_rows = lambda rows: SimpleNamespace(signature="same")
    tab._last_lhb_signature = "same"
    tab._get_pool_manager = lambda: _Manager()
    tab._status_metric = lambda prefix, value, suffix="": f"{prefix}{value}{suffix}"
    tab._latest_cached_trade_date = lambda: ""
    tab._refresh_lhb_lineage = lambda rows: None
    lhb.LhbTab._display_pool(tab, [], row_data=None)
    assert tab.table_state.calls[-1][0] == "empty"


def test_start_backfill_validation_callback_error_and_zero(monkeypatch):
    tab = _LhbDummy()
    tab._status_metric = lambda prefix, value, suffix="": f"{prefix}{value}{suffix}"
    tab._ensure_log_line = lhb.LhbTab._ensure_log_line
    tab.window = lambda: None
    tab._manual_refresh = lambda: None
    tab._display_pool = lambda *args, **kwargs: tab.calls.append(("display_done", args, kwargs))
    tab._set_pool_status = lambda *args, **kwargs: tab.calls.append(("status", args, kwargs))
    monkeypatch.setattr(lhb.task_manager, "is_active_task", lambda task_id: False)
    signal = _Signal()
    monkeypatch.setattr(lhb, "event_bus", SimpleNamespace(sig_system_log=signal))
    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(lhb, "task_lifecycle_for", lambda owner, runner=None: lifecycle)

    lhb.LhbTab._start_backfill(tab, [], ["d"], "ref")
    assert captured["on_success"] is not None and not tab.btn_refresh.enabled
    captured["on_success"]({"fetched": {}, "validated": {"d": {"count": 1}}, "pool": [], "row_data": []})
    assert tab.btn_refresh.enabled and any(call[0] == "display_done" for call in tab.calls)

    tab._backfill_in_progress = False
    lhb.LhbTab._start_backfill(tab, [], [], "ref")
    assert not tab._backfill_in_progress and tab.btn_refresh.enabled

    tab._backfill_in_progress = False
    lhb.LhbTab._start_backfill(tab, ["d"], [], "ref")
    captured["on_success"]({})
    assert signal.calls[-1][0] == "error"
    tab._backfill_in_progress = False
    lhb.LhbTab._start_backfill(tab, ["d"], [], "ref")
    captured["on_error"]("boom")
    assert tab.btn_refresh.enabled and signal.calls[-1][0] == "error"


def test_manual_refresh_shutdown_filter_and_close_wrappers(monkeypatch):
    from ui.components import toast_widget

    calls = []
    monkeypatch.setattr(toast_widget, "show_toast", lambda *args, **kwargs: calls.append("toast"))
    tab = _LhbDummy()
    tab._backfill_in_progress = True
    lhb.LhbTab._manual_refresh(tab)
    assert calls == ["toast"]
    tab._backfill_in_progress = False
    tab._get_manual_refresh_trade_dates = lambda: ([], "", "warn")
    lhb.LhbTab._manual_refresh(tab)
    assert calls == ["toast", "toast"]
    tab._get_manual_refresh_trade_dates = lambda: (["d"], "strategy", "info")
    signal = _Signal()
    monkeypatch.setattr(lhb, "event_bus", SimpleNamespace(sig_system_log=signal))
    tab._ensure_log_line = lhb.LhbTab._ensure_log_line
    lhb.LhbTab._manual_refresh(tab)
    assert signal.calls and tab.calls[-1][0] == "backfill"
    tab._manual_refresh = lambda: calls.append("manual")
    assert lhb.LhbTab.refresh_history(tab)

    timers = [SimpleNamespace(stop=lambda: calls.append("stop")) for _ in range(4)]
    tab._pool_retry_timer, tab._quote_apply_timer, tab._quote_sort_timer, tab._pool_update_refresh_timer = timers
    for name in ("_on_cache_bootstrap_ready", "_on_cache_reload_completed", "_on_lhb_pool_updated"):
        setattr(tab, name, lambda: None)
    disconnected = SimpleNamespace(disconnect=lambda slot: None)
    monkeypatch.setattr(
        lhb,
        "event_bus",
        SimpleNamespace(
            sig_cache_bootstrap_ready=disconnected,
            sig_cache_reload_completed=disconnected,
            sig_lhb_pool_updated=disconnected,
        ),
    )
    lifecycle = SimpleNamespace(shutdown=lambda timeout_ms: calls.append(timeout_ms))
    monkeypatch.setattr(lhb, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    lhb.LhbTab.shutdown(tab)
    assert lhb.LHB_TASK_SHUTDOWN_WAIT_TIMEOUT_MS in calls

    tab.search_box = SimpleNamespace(text=lambda: " AbC ")
    tab.proxy_model = object()
    tab.set_proxy_filter_text = lambda proxy, text: calls.append(text)
    tab._refresh_pool_status = lambda: calls.append("status")
    lhb.LhbTab._filter_table(tab)
    assert calls[-2:] == ["abc", "status"]
