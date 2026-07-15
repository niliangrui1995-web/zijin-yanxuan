# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QModelIndex

from core.exceptions import CacheIOError
from ui.models.table_models import StockTableModel
from ui.tabs import foreign_block_trade_tab as foreign


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Button:
    def __init__(self):
        self.enabled = True
        self.summary = []
        self.values = []
        self.labels = []

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def apply_summary(self, prefix, all_text=""):
        self.summary.append((prefix, all_text))

    def selected_values(self):
        return list(self.values)

    def selected_labels(self):
        return list(self.labels)

    def set_options(self, values, preserve_selection=True):
        self.values = list(values)
        self.preserve_selection = preserve_selection


class _TableState:
    def __init__(self):
        self.calls = []

    def show_table(self):
        self.calls.append(("table",))

    def show_loading(self, *args):
        self.calls.append(("loading", *args))

    def show_empty(self, *args):
        self.calls.append(("empty", *args))

    def show_error(self, *args, **kwargs):
        self.calls.append(("error", args, kwargs))


class _ForeignDummy:
    def __init__(self, rows=None):
        self._closing = False
        self._runtime_cleanup_done = False
        self._local_cache_generation = 0
        self._fetch_generation = 0
        self._local_cache_loading = False
        self._local_cache_pending_emit_event = None
        self._post_f5_local_cache_defer_until = 0.0
        self._post_f5_local_cache_pending = False
        self._post_f5_local_cache_emit_event = False
        self._initial_local_cache_load_started = False
        self._is_loading = False
        self._had_rows_before_refresh = False
        self._pending_f5_online_refresh = False
        self._last_success_at = None
        self._status_segments = ()
        self._status_primary = "wait"
        self._status_freshness = ""
        self._status_next_step = ""
        self.days_to_fetch = 30
        self.model = SimpleNamespace(row_data=list(rows or []), update_data=self._update_data)
        self.btn_refresh = _Button()
        self.table_state = _TableState()
        self.cmb_filter_date = _Button()
        self.cmb_filter_branch = _Button()
        self.cmb_filter_direction = _Button()
        self.search_box = SimpleNamespace(text=lambda: "")
        self.proxy_model = SimpleNamespace(
            rowCount=lambda: len(self.model.row_data),
            setFilterText=lambda value: None,
            setExactFilters=lambda key, value: None,
        )
        self.lbl_status = SimpleNamespace(setText=lambda text: setattr(self, "status_text", text))
        self.status_calls = []
        self.saved = []
        self.loads = []
        self.quote_calls = 0
        self.prime_calls = 0
        self.filter_calls = 0
        self.finished = 0

    def _update_data(self, rows):
        self.model.row_data = list(rows)

    def _set_fetch_status(self, *args, **kwargs):
        self.status_calls.append((args, kwargs))

    @staticmethod
    def _status_metric(prefix, value, suffix=""):
        return f"{prefix}{value}{suffix}"

    def _apply_row_data(self, rows, preserve_selection=True, already_filtered=False):
        self.model.row_data = list(rows or [])
        return ["20260715"] if rows else [], ["高盛"] if rows else []

    @staticmethod
    def _filter_rows_to_ai_chain(rows):
        return foreign.filter_foreign_block_rows_to_ai_chain(rows)

    @staticmethod
    def _extract_cache_filter_options(rows):
        return foreign.ForeignBlockTradeTab._extract_cache_filter_options(rows)

    def _save_local_cache(self, rows):
        self.saved.append(list(rows or []))
        return True

    def _filter_table_combo(self):
        self.filter_calls += 1

    def _apply_latest_quotes_from_store(self):
        self.quote_calls += 1

    def _prime_visible_local_quote_snapshot(self, model):
        self.prime_calls += 1

    def _latest_trade_date_text(self):
        return "20260715"

    def _finish_local_cache_load(self):
        self.finished += 1

    def _load_local_cache(self, emit_event=True):
        self.loads.append(bool(emit_event))

    def _schedule_post_f5_local_cache_load(self, emit_event=True):
        self.loads.append(("schedule", bool(emit_event)))
        return True

    def _run_post_f5_local_cache_load(self):
        return foreign.ForeignBlockTradeTab._run_post_f5_local_cache_load(self)

    def _load_block_trade_data(self):
        self.loads.append("online")

    def _on_local_cache_failed(self, error_message):
        return foreign.ForeignBlockTradeTab._on_local_cache_failed(self, error_message)

    def _filter_status_text(self, button, *, all_text):
        return foreign.ForeignBlockTradeTab._filter_status_text(self, button, all_text=all_text)

    @staticmethod
    def _should_save_cache(timeout_chunks, failed_chunks):
        return foreign.ForeignBlockTradeTab._should_save_cache(timeout_chunks, failed_chunks)


def test_filter_proxy_exact_search_and_branch_filters(monkeypatch):
    model = StockTableModel(["代码", "名称", "交易日期", "交易详情", "买方营业部", "卖方营业部"])
    model.update_data(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "交易日期": "20260715",
                "交易详情": "外资买入",
                "买方营业部": "高盛上海",
                "卖方营业部": "普通席位",
            }
        ]
    )
    proxy = foreign.BlockTradeFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setExactFilter("交易日期", "20260715")
    assert proxy.filterAcceptsRow(0, QModelIndex())
    proxy.setExactFilters("交易日期", ["bad"])
    assert not proxy.filterAcceptsRow(0, QModelIndex())
    proxy.setExactFilters("交易日期", [])

    proxy.exact_filters = {"买方营业部": {""}}
    assert proxy.filterAcceptsRow(0, QModelIndex())
    proxy.exact_filters = {"买方营业部": {"摩根"}}
    assert not proxy.filterAcceptsRow(0, QModelIndex())
    proxy.exact_filters = {"_branch": {"高盛"}}
    assert proxy.filterAcceptsRow(0, QModelIndex())
    proxy.exact_filters = {"_branch": {"摩根"}}
    assert not proxy.filterAcceptsRow(0, QModelIndex())

    proxy.exact_filters = {}
    proxy._filter_text = "000001"
    monkeypatch.setattr(foreign.SearchFilter, "match_pinyin_or_text", lambda *args: True)
    assert proxy.filterAcceptsRow(0, QModelIndex())
    monkeypatch.setattr(foreign.SearchFilter, "match_pinyin_or_text", lambda *args: False)
    proxy._filter_text = "高盛"
    assert proxy.filterAcceptsRow(0, QModelIndex())
    proxy._filter_text = "不存在"
    assert not proxy.filterAcceptsRow(0, QModelIndex())


def test_module_cache_helpers_generation_guards_and_directions(monkeypatch):
    class _Broken:
        @property
        def value(self):
            raise RuntimeError("gone")

    assert foreign._owner_attr(_Broken(), "value", 3) == 3
    assert foreign._owner_attr(SimpleNamespace(value=4), "value", 3) == 4

    monkeypatch.setattr(foreign, "invoke_with_cancellation", lambda fn, token, **kwargs: {"rows": [1]})
    assert foreign._load_cache_payload(False, object()) == {"rows": [1], "emit_event": False}

    calls = []
    owner = SimpleNamespace(
        _closing=False,
        _local_cache_generation=2,
        _fetch_generation=3,
        _apply_local_cache_payload=lambda value: calls.append(("cache", value)),
        _on_local_cache_failed=lambda value: calls.append(("cache_error", value)),
        _on_data_fetched=lambda value: calls.append(("fetch", value)),
        _on_data_fetch_failed=lambda value: calls.append(("fetch_error", value)),
    )
    foreign._apply_cache_if_current(owner, 1, {})
    foreign._apply_cache_error_if_current(owner, 1, "bad")
    foreign._apply_fetch_if_current(owner, 2, {})
    foreign._apply_fetch_error_if_current(owner, 2, "bad")
    assert calls == []
    foreign._apply_cache_if_current(owner, 2, {"ok": 1})
    foreign._apply_cache_error_if_current(owner, 2, "cache")
    foreign._apply_fetch_if_current(owner, 3, {"ok": 2})
    foreign._apply_fetch_error_if_current(owner, 3, "fetch")
    assert [call[0] for call in calls] == ["cache", "cache_error", "fetch", "fetch_error"]
    owner._closing = True
    foreign._apply_cache_if_current(owner, 2, {})
    assert len(calls) == 4

    monkeypatch.setattr(foreign, "foreign_block_direction", lambda buyer, seller: "unknown")
    assert foreign.determine_foreign_block_direction("a", "b") == ("unknown", foreign.COLOR_FLAT)


def test_small_status_filter_and_cache_methods(monkeypatch):
    tab = _ForeignDummy([None, {"交易日期": "20260714"}, {"交易日期": "20260715"}])
    foreign.ForeignBlockTradeTab._on_days_changed(tab, 99)
    assert tab.days_to_fetch == 10 and tab.loads == ["online"]
    assert foreign.ForeignBlockTradeTab._format_last_success_segment(tab) == ""
    tab._last_success_at = dt.datetime(2026, 7, 15, 9, 30)
    assert "09:30:00" in foreign.ForeignBlockTradeTab._format_last_success_segment(tab)
    assert foreign.ForeignBlockTradeTab._latest_trade_date_text(tab) == "20260715"

    rows = [None, {"交易日期": "20260715", "买方营业部": "高盛上海", "卖方营业部": ""}]
    dates, branches = foreign.ForeignBlockTradeTab._extract_cache_filter_options(rows)
    assert dates == ["20260715"] and branches == ["高盛上海"]

    monkeypatch.setattr(foreign, "filter_foreign_block_rows_to_ai_chain", lambda value: list(value)[::-1])
    tab._refresh_filter_button_text = lambda *args: None
    dates, branches = foreign.ForeignBlockTradeTab._apply_row_data(tab, rows, already_filtered=False)
    assert tab.model.row_data[0]["交易日期"] == "20260715"

    monkeypatch.setattr(
        foreign,
        "save_foreign_block_cache",
        lambda rows, days_to_fetch: {"latest_trade_date": "20260715"},
    )
    assert foreign.ForeignBlockTradeTab._save_local_cache(tab, rows)
    monkeypatch.setattr(
        foreign,
        "save_foreign_block_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(CacheIOError("disk")),
    )
    assert not foreign.ForeignBlockTradeTab._save_local_cache(tab, rows)


def test_local_cache_load_defer_pending_task_and_finish(monkeypatch):
    tab = _ForeignDummy()
    monkeypatch.setattr(foreign.time, "monotonic", lambda: 10.0)
    tab._closing = True
    foreign.ForeignBlockTradeTab._load_local_cache(tab)
    assert tab.loads == []
    tab._closing = False
    tab._post_f5_local_cache_defer_until = 20.0
    foreign.ForeignBlockTradeTab._load_local_cache(tab, emit_event=False)
    assert tab.loads == [("schedule", False)]

    tab._post_f5_local_cache_defer_until = 0.0
    tab._local_cache_loading = True
    foreign.ForeignBlockTradeTab._load_local_cache(tab, emit_event=False)
    assert tab._local_cache_pending_emit_event is False

    tab._local_cache_loading = False
    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(foreign, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    foreign.ForeignBlockTradeTab._load_local_cache(tab, emit_event=True)
    assert tab._local_cache_loading and tab._local_cache_generation == 1
    assert captured["on_success"] is not None and captured["on_error"] is not None

    singles = []
    monkeypatch.setattr(foreign.QTimer, "singleShot", lambda delay, callback: singles.append(callback))
    tab._local_cache_pending_emit_event = False
    foreign.ForeignBlockTradeTab._finish_local_cache_load(tab)
    singles.pop()()
    assert tab.loads[-1] is False


def test_post_f5_schedule_and_apply_cache_payload_branches(monkeypatch):
    tab = _ForeignDummy()
    singles = []
    monkeypatch.setattr(foreign.QTimer, "singleShot", lambda delay, callback: singles.append((delay, callback)))
    monkeypatch.setattr(foreign.time, "monotonic", lambda: 10.0)
    tab._post_f5_local_cache_defer_until = 11.0
    assert foreign.ForeignBlockTradeTab._schedule_post_f5_local_cache_load(tab, emit_event=False)
    assert not foreign.ForeignBlockTradeTab._schedule_post_f5_local_cache_load(tab, emit_event=True)
    assert singles[0][0] == 1000
    assert foreign.ForeignBlockTradeTab._run_post_f5_local_cache_load(tab)
    assert tab.loads[-1] == ("schedule", True)

    tab._post_f5_local_cache_defer_until = 0.0
    assert foreign.ForeignBlockTradeTab._run_post_f5_local_cache_load(tab)
    assert tab.loads[-1] is True

    tab.finished = 0
    foreign.ForeignBlockTradeTab._apply_local_cache_payload(
        tab,
        {"rows": [{"代码": "000001"}], "raw_count": 1, "saved_at": "bad-date", "emit_event": False},
    )
    _delay, callback = singles[-1]
    callback()
    assert tab._last_success_at is None
    assert tab.quote_calls == 1 and tab.prime_calls == 1 and tab.finished == 1

    foreign.ForeignBlockTradeTab._apply_local_cache_payload(tab, {"raw_count": object()})
    assert tab.finished == 2


def test_finish_cache_payload_empty_cleanup_and_event(monkeypatch):
    signal = _Signal()
    monkeypatch.setattr(foreign, "event_bus", SimpleNamespace(sig_block_trade_updated=signal))
    tab = _ForeignDummy()
    foreign.ForeignBlockTradeTab._finish_apply_local_cache_payload(
        tab, rows=[], raw_count=0, latest_trade_date="", payload={"emit_event": True}
    )
    assert tab.table_state.calls[-1][0] == "empty"
    assert signal.calls == [()]
    assert tab.finished == 1

    tab._runtime_cleanup_done = True
    foreign.ForeignBlockTradeTab._finish_apply_local_cache_payload(
        tab, rows=[{}], raw_count=1, latest_trade_date="", payload={}
    )
    assert tab.finished == 2
    foreign.ForeignBlockTradeTab._on_local_cache_failed(tab, "bad")
    assert tab.finished == 3


def test_header_status_filter_summary_and_set_status(monkeypatch):
    tab = _ForeignDummy([{"交易日期": "20260715"}])
    tab.cmb_filter_date.labels = ["20260715"]
    tab.cmb_filter_branch.labels = ["高盛"]
    tab.cmb_filter_direction.labels = ["外资买入"]
    tab.search_box = SimpleNamespace(text=lambda: "  code ")
    monkeypatch.setattr(
        foreign,
        "format_multi_select_summary",
        lambda prefix, labels, all_text="": ((labels[0] if labels else all_text), False),
    )
    summary = foreign.ForeignBlockTradeTab._current_filter_summary(tab)
    assert "20260715" in summary and "code" in summary
    tab.cmb_filter_date.labels = []
    tab.cmb_filter_branch.labels = []
    tab.cmb_filter_direction.labels = []
    tab.search_box = SimpleNamespace(text=lambda: "")
    assert foreign.ForeignBlockTradeTab._current_filter_summary(tab) == "全部"

    tab._format_last_success_segment = lambda: "last"
    tab._current_filter_summary = lambda: "all"
    tab._latest_trade_date_text = lambda: "20260715"
    tab.format_workspace_status = lambda *args, **kwargs: str((args, kwargs))
    foreign.ForeignBlockTradeTab._refresh_header_status(tab)
    assert "last" in tab.status_text
    tab._refresh_header_status = lambda: setattr(tab, "refreshed", True)
    foreign.ForeignBlockTradeTab._set_fetch_status(tab, "ok", "", "count", freshness=" fresh ", next_step=" next ")
    assert tab._status_segments == ("count",) and tab.refreshed


def test_load_block_trade_guards_and_background_submission(monkeypatch):
    tab = _ForeignDummy([{}])
    tab._closing = True
    foreign.ForeignBlockTradeTab._load_block_trade_data(tab)
    assert not tab._is_loading
    tab._closing = False
    tab._is_loading = True
    foreign.ForeignBlockTradeTab._load_block_trade_data(tab)
    assert tab.status_calls[-1][0][0] == "大宗抓取中"

    tab._is_loading = False
    foreign._kline_cache["x"] = 1
    captured = {}
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: captured.update(args=args, **kwargs))
    monkeypatch.setattr(foreign, "task_lifecycle_for", lambda owner, runner=None: lifecycle)
    foreign.ForeignBlockTradeTab._load_block_trade_data(tab)
    assert tab._is_loading and not tab.btn_refresh.enabled
    assert foreign._kline_cache == {}
    assert captured["on_success"] is not None


@pytest.mark.parametrize(
    ("had_rows", "timeout", "failed", "expected_state"),
    [
        (False, [], [], "empty"),
        (True, ["chunk"], [], "table"),
        (False, [], ["chunk"], "error"),
    ],
)
def test_on_data_fetched_empty_result_branches(monkeypatch, had_rows, timeout, failed, expected_state):
    signal = _Signal()
    monkeypatch.setattr(foreign, "event_bus", SimpleNamespace(sig_block_trade_updated=signal))
    monkeypatch.setattr(foreign, "_format_incomplete_message", lambda timeout, failed: ";incomplete")
    tab = _ForeignDummy([{}] if had_rows else [])
    tab._is_loading = True
    tab._had_rows_before_refresh = had_rows
    foreign.ForeignBlockTradeTab._on_data_fetched(
        tab,
        {"records": [], "timeout_chunks": timeout, "failed_chunks": failed},
    )
    assert not tab._is_loading and tab.btn_refresh.enabled
    assert tab.table_state.calls[-1][0] == expected_state
    assert signal.calls == [()]
    if not timeout and not failed:
        assert tab.saved == [[]]


def test_on_data_fetched_rows_build_cache_and_incomplete(monkeypatch):
    signal = _Signal()
    monkeypatch.setattr(foreign, "event_bus", SimpleNamespace(sig_block_trade_updated=signal))
    monkeypatch.setattr(
        foreign, "_format_incomplete_message", lambda timeout, failed: ";partial" if timeout or failed else ""
    )
    monkeypatch.setattr(
        foreign,
        "build_foreign_block_trade_rows",
        lambda records: ([{"代码": "000001"}], 2),
    )
    tab = _ForeignDummy()
    foreign.ForeignBlockTradeTab._on_data_fetched(tab, [{"raw": 1}])
    assert tab.saved == [[{"代码": "000001"}]]
    assert tab.filter_calls == 1 and tab.quote_calls == 1 and tab.prime_calls == 1

    foreign.ForeignBlockTradeTab._on_data_fetched(
        tab,
        {
            "records": [{"raw": 2}],
            "row_data": [{"代码": "000002"}],
            "grouped_count": 0,
            "timeout_chunks": ["chunk"],
            "failed_chunks": [],
        },
    )
    assert len(tab.saved) == 1
    assert len(signal.calls) == 2


@pytest.mark.parametrize(
    ("rows", "message", "expected_state"),
    [
        ([{}], "", "table"),
        ([], "network", "error"),
        ([], "抓取超时：wait", "error"),
    ],
)
def test_on_data_fetch_failed_message_and_state(rows, message, expected_state):
    tab = _ForeignDummy(rows)
    tab._is_loading = True
    foreign.ForeignBlockTradeTab._on_data_fetch_failed(tab, message)
    assert not tab._is_loading and tab.btn_refresh.enabled
    assert tab.table_state.calls[-1][0] == expected_state
    status_text = str(tab.status_calls[-1])
    if message == "network":
        assert "大宗交易抓取失败" in status_text


def test_filter_combo_and_navigation_wrappers(monkeypatch):
    tab = _ForeignDummy()
    exact = []
    tab.search_box = SimpleNamespace(text=lambda: " AbC ")
    tab.proxy_model = SimpleNamespace(
        setFilterText=lambda value: exact.append(("text", value)),
        setExactFilters=lambda key, value: exact.append((key, value)),
    )
    tab._refresh_filter_button_text = lambda *args: exact.append(("summary", args[1]))
    tab._refresh_header_status = lambda: exact.append(("header",))
    foreign.ForeignBlockTradeTab._filter_table_combo(tab)
    assert ("text", "abc") in exact and ("header",) in exact

    calls = []
    monkeypatch.setattr(foreign, "_show_kline_from_proxy_index", lambda *args: calls.append("kline"))
    monkeypatch.setattr(foreign, "_show_stock_context_menu_from_proxy_index", lambda *args: calls.append("menu"))
    foreign.ForeignBlockTradeTab._on_double_click(tab, object())
    foreign.ForeignBlockTradeTab._show_context_menu(tab, object())
    assert calls == ["kline", "menu"]


def test_remaining_small_wrappers_shutdown_and_static_branches(monkeypatch):
    now = dt.datetime(2026, 7, 15, 21)
    assert not foreign.ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=False,
        last_auto_refresh_date="",
    )
    assert foreign.ForeignBlockTradeTab._ensure_log_line("done") == "done\n"
    assert foreign.ForeignBlockTradeTab._ensure_log_line("done\n") == "done\n"
    assert foreign.ForeignBlockTradeTab.get_foreign_keywords() == list(foreign.FOREIGN_KEYWORDS)

    tab = _ForeignDummy()
    foreign.ForeignBlockTradeTab._on_block_trade_updated(tab)
    foreign.ForeignBlockTradeTab._on_cache_reload_completed(tab)
    assert tab.loads[-1] is False and tab.quote_calls == 1
    assert foreign.ForeignBlockTradeTab.run_post_online_refresh(tab)
    assert tab.loads[-1] == "online"

    cleanup = []
    lifecycle = SimpleNamespace(shutdown=lambda timeout_ms: cleanup.append(timeout_ms))
    tab._task_lifecycle = lifecycle
    tab._cleanup_runtime_state = lambda: cleanup.append("cleanup")
    foreign.ForeignBlockTradeTab.shutdown(tab)
    assert tab._closing and cleanup == [1000, "cleanup"]


def test_constructor_invalid_delay_and_show_event_wrapper(monkeypatch):
    fake_signal = SimpleNamespace(connect=lambda callback: None)
    monkeypatch.setattr(
        foreign,
        "event_bus",
        SimpleNamespace(sig_cache_reload_completed=fake_signal, sig_block_trade_updated=fake_signal),
    )
    monkeypatch.setattr(foreign.BaseStockTab, "__init__", lambda self, data_provider=None, parent=None: None)
    monkeypatch.setattr(foreign.ForeignBlockTradeTab, "_init_ui", lambda self: None)
    tab = foreign.ForeignBlockTradeTab(object(), autoload=False, initial_cache_load_delay_ms="bad")
    assert tab._initial_cache_load_delay_ms == foreign.LOCAL_CACHE_LOAD_DELAY_MS

    calls = []
    monkeypatch.setattr(foreign.BaseStockTab, "showEvent", lambda self, event: calls.append("super"))
    tab._should_start_runtime_on_show = lambda: True
    tab._schedule_initial_local_cache_load = lambda: calls.append("schedule")
    foreign.ForeignBlockTradeTab.showEvent(tab, object())
    assert calls == ["super", "schedule"]
