# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QShowEvent
from PyQt6.QtWidgets import QHeaderView, QWidget

from app.services.stock_context_model_service import StockContextSnapshot, StockSignal
from app.services.ui_event_service import domain_events as event_bus
from infra.tasks.lifecycle import CancellationToken
from ui.tabs import stock_candidate_tab as stock_candidate_module
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.theme import theme_manager


def _run_candidate_refreshes_inline(monkeypatch, submitted=None):
    def _run_in_background(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        if submitted is not None:
            submitted.append(task_id)
        if on_success is not None:
            on_success(fn())
        return task_id

    monkeypatch.setattr(
        stock_candidate_module.task_manager,
        "run_in_background",
        _run_in_background,
        raising=False,
    )


def _close_and_delete(tab):
    tab.close()
    tab.deleteLater()


class _PrimingWorkspace(QWidget):
    def __init__(self, primes):
        super().__init__()
        self._primes = primes

    def collect_stock_context(self):
        return {}

    def prime_stock_context_snapshots(self):
        self._primes.append("prime")
        return True


class _KwargPrimingWorkspace(QWidget):
    def __init__(self, primes):
        super().__init__()
        self._primes = primes

    def collect_stock_context(self):
        return {}

    def prime_stock_context_snapshots(self, **kwargs):
        self._primes.append(dict(kwargs))
        return True


def test_stock_candidate_rows_keep_multi_source_names_and_rank_score():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "na_daily", "title": "北美战报"},
                    {"key": "earnings", "title": "业绩异动"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "300750": [
                StockSignal(
                    code="300750",
                    name="宁德时代",
                    source_tab="na_daily",
                    signal_type="catalyst",
                    summary="北美订单催化",
                    payload={"现价": "183.50", "涨幅%": 5.2, "市值": "1.18万亿"},
                ),
                StockSignal(
                    code="300750",
                    source_tab="earnings",
                    signal_type="earnings",
                    summary="32.5%",
                    observed_at="20260420",
                ),
            ],
            "000001": [
                StockSignal(
                    code="000001",
                    source_tab="na_daily",
                    signal_type="catalyst",
                    summary="单一信号",
                )
            ],
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["代码"] == "300750"
    assert row["名称"] == "宁德时代"
    assert row["市价"] == "183.50"
    assert row["涨幅%"] == "5.2"
    assert row["市值"] == "1.18万亿"
    assert row["来源数"] == 2
    assert row["信号数"] == 2
    assert row["来源"] == "北美战报｜业绩异动"
    assert row["最近时间"] == "20260420"
    assert "北美订单催化" in row["核心信号"]


def test_stock_candidate_rows_compact_vcp_scan_core_signal():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "ai_industry_chain", "title": "AI产业链"},
                    {"key": "scan", "title": "VCP扫描"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "688498": [
                StockSignal(
                    code="688498",
                    source_tab="ai_industry_chain",
                    signal_type="subsector",
                    summary="CPO",
                ),
                StockSignal(
                    code="688498",
                    source_tab="scan",
                    signal_type="vcp_scan",
                    summary="触发20260423 | 评分91 | RPS96 | 接近突破 | CPO",
                    payload={
                        "触发日期": "20260423",
                        "RPS强度": "96",
                    },
                ),
                StockSignal(
                    code="688498",
                    source_tab="scan",
                    signal_type="vcp_scan",
                    summary="触发20260423 | 评分91 | RPS96 | 接近突破 | CPO",
                    payload={
                        "触发日期": "20260423",
                        "RPS强度": "96",
                    },
                ),
            ],
        },
    )

    assert len(rows) == 1
    assert "触发日期 20260423 | RPS 96" in rows[0]["核心信号"]
    assert "评分91" not in rows[0]["核心信号"]


def test_stock_candidate_requires_ai_chain_or_na_daily_anchor_source():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "na_daily", "title": "北美战报"},
                    {"key": "ai_industry_chain", "title": "AI产业链"},
                    {"key": "scan", "title": "VCP扫描"},
                    {"key": "fund_holdings", "title": "基金持仓"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "688498": [
                StockSignal(code="688498", source_tab="scan", signal_type="vcp_scan", summary="VCP"),
                StockSignal(code="688498", source_tab="fund_holdings", signal_type="fund_holding", summary="新进"),
            ],
            "300750": [
                StockSignal(code="300750", source_tab="ai_industry_chain", signal_type="subsector", summary="CPO"),
                StockSignal(code="300750", source_tab="scan", signal_type="vcp_scan", summary="VCP"),
            ],
            "002156": [
                StockSignal(code="002156", source_tab="na_daily", signal_type="catalyst", summary="P7核心"),
                StockSignal(code="002156", source_tab="na_daily", signal_type="subsector", summary="先进封装"),
            ],
            "688629": [
                StockSignal(code="688629", source_tab="na_daily", signal_type="catalyst", summary="北美催化"),
                StockSignal(
                    code="688629", source_tab="ai_industry_chain", signal_type="subsector", summary="高速连接器"
                ),
            ],
        },
    )

    assert [row["代码"] for row in rows] == ["300750"]


def test_stock_candidate_counts_na_daily_and_ai_chain_as_one_source_group():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "na_daily", "title": "北美战报"},
                    {"key": "ai_industry_chain", "title": "AI产业链"},
                    {"key": "lhb", "title": "龙虎榜"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "688629": [
                StockSignal(code="688629", source_tab="na_daily", signal_type="catalyst", summary="北美催化"),
                StockSignal(
                    code="688629", source_tab="ai_industry_chain", signal_type="subsector", summary="高速连接器"
                ),
                StockSignal(code="688629", source_tab="lhb", signal_type="lhb", summary="机构净买"),
            ],
        },
    )

    assert len(rows) == 1
    assert rows[0]["来源"] == "北美战报｜AI产业链｜龙虎榜"
    assert rows[0]["来源数"] == 2
    assert rows[0]["信号数"] == 2
    assert rows[0]["共振分"] == 36


def test_stock_candidate_rows_keep_sector_for_kline_context():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "na_daily", "title": "北美战报"},
                    {"key": "ai_industry_chain", "title": "AI产业链"},
                    {"key": "lhb", "title": "龙虎榜"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "688629": [
                StockSignal(
                    code="688629",
                    source_tab="na_daily",
                    signal_type="subsector",
                    summary="北美旧分类",
                    payload={"细分板块": "北美旧分类"},
                ),
                StockSignal(
                    code="688629",
                    source_tab="ai_industry_chain",
                    signal_type="subsector",
                    summary="高速连接器",
                    payload={"细分板块": "高速连接器"},
                ),
                StockSignal(code="688629", source_tab="lhb", signal_type="lhb", summary="机构净买"),
            ],
            "002156": [
                StockSignal(
                    code="002156",
                    source_tab="na_daily",
                    signal_type="catalyst",
                    summary="北美催化",
                    payload={"细分板块": "先进封装"},
                ),
                StockSignal(code="002156", source_tab="scan", signal_type="vcp_scan", summary="VCP"),
            ],
        },
    )

    sectors = {row["代码"]: row["细分板块"] for row in rows}
    assert sectors["688629"] == "高速连接器"
    assert sectors["002156"] == "先进封装"


def test_stock_candidate_listens_to_global_quote_updates(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "市价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "共振分": 22,
                    "来源数": 2,
                    "信号数": 2,
                    "来源": "VCP扫描｜基金持仓",
                    "核心信号": "触发日期 20260423 | RPS 96",
                    "最近时间": "20260423",
                }
            ]
        )

        tab.show()
        event_bus.sig_rt_quotes.emit(
            {
                "300750": {
                    "close": 183.5,
                    "last_close": 175.0,
                    "zongguben": 6_400_000_000,
                }
            }
        )

        row = tab.model.row_data[0]
        assert row["市价"] == "183.50"
        assert round(float(row["涨幅%"]), 2) == 4.86
        assert row["市值"] == "11744亿"
        assert tab.get_realtime_quote_codes() == {"300750"}

        muted = QColor(theme_manager.get("TEXT_MUTED")).name()
        for header in ["共振分", "来源数", "信号数", "来源", "核心信号", "最近时间"]:
            idx = tab.model.index(0, tab.model.headers.index(header))
            assert tab.model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == muted

        price_idx = tab.model.index(0, tab.model.headers.index("市价"))
        assert tab.model.data(price_idx, Qt.ItemDataRole.ForegroundRole).name() == QColor(
            theme_manager.get("COLOR_RISE")
        ).name()
    finally:
        _close_and_delete(tab)


def test_stock_candidates_visible_202_row_quote_batch_stays_partial_after_first_frame(
    qt_application, monkeypatch
):
    """195 行行情回写不能因 Qt 默认阈值升级为第二次整视口绘制。"""
    recorded = []
    monkeypatch.setattr(
        "core.observability.record_metric",
        lambda name, value, **kwargs: recorded.append((name, value, kwargs)),
    )

    tab = StockCandidateTab(data_provider=SimpleNamespace())
    monkeypatch.setattr(tab, "_should_start_runtime_on_show", lambda: False)
    # 隔离同一 QApplication 中其他候选页延迟绘制的观测指标；该 scope 不改变
    # stock_candidates 的非 Watchlist 绘制行为。
    tab.table.set_targeted_flash_repaint_enabled(False, metric_scope="stock_candidates_202_regression")
    rows = [
        {
            "代码": f"{index:06d}",
            "名称": f"综合候选{index}",
            "市价": "10.00",
            "涨幅%": 0.0,
            "市值": "10亿",
            "共振分": 22,
            "来源数": 2,
            "信号数": 2,
            "来源": "VCP扫描｜基金持仓",
            "核心信号": "触发日期 20260423 | RPS 96",
            "最近时间": "20260423",
        }
        for index in range(202)
    ]
    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        assert tab.model.rowCount() == 202
        assert tab.proxy_model.rowCount() == 202
        tab.resize(1280, 720)
        tab.show()
        for _ in range(5):
            qt_application.processEvents()
        tab.table_state.show_table()
        for _ in range(5):
            qt_application.processEvents()

        # 清除首次可见首帧，以下仅观察预热揭示后的本地行情批量回写。
        recorded.clear()
        quotes = {
            f"{index:06d}": {
                "close": 11.0,
                "last_close": 10.0,
                "zongguben": 1_000_000_000,
            }
            for index in range(195)
        }
        assert tab.model.update_quotes(quotes, record_flash=True) == 195
        assert tab.model._flash_records
        for _ in range(10):
            qt_application.processEvents()

        paints = [item for item in recorded if item[0] == "stock_candidates_202_regression_table_paint_ms"]
        assert len(paints) == 1
        tags = paints[0][2]["tags"]
        assert tags["reason"] == "quote_data_changed"
        assert tags["changed_rows"] == "195"
        assert tags["changed_indexes"] == "585"
        assert int(tags["update_threshold"]) > 195 * 3
        assert tags["threshold_exceeded"] == "false"
        assert tags["delivered_full_viewport"] == "false"
        assert tags["delivery_kind"] == "partial_region"
    finally:
        _close_and_delete(tab)


def test_stock_candidate_show_runtime_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "perf_memory_probe"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not StockCandidateTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert StockCandidateTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_stock_candidate_auto_refreshes_when_source_tabs_update(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        assert not tab._auto_refresh_timer.isActive()

        event_bus.sig_ai_industry_chain_updated.emit()

        assert tab._auto_refresh_timer.isActive()
        assert tab._status_primary == "等待综合候选自动刷新"
        assert tab._status_freshness == "数据源已更新"
    finally:
        _close_and_delete(tab)


def test_stock_candidate_earnings_update_primes_context_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    primes = []
    workspace = _PrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        event_bus.sig_earnings_updated.emit()

        assert primes == ["prime"]
        assert tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_hidden_context_update_defers_refresh_until_visible(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    primes = []
    current = {"value": False}
    workspace = _PrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: current["value"]
    try:
        event_bus.sig_earnings_updated.emit()

        assert primes == ["prime"]
        assert tab._context_refresh_pending is True
        assert not tab._auto_refresh_timer.isActive()

        current["value"] = True
        tab.showEvent(QShowEvent())

        assert tab._context_refresh_pending is False
        assert tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_after_hours_hidden_update_skips_snapshot_prime(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: False)
    primes = []
    workspace = _KwargPrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: False
    try:
        event_bus.sig_earnings_updated.emit()

        assert primes == []
        assert tab._context_refresh_pending is True
        assert not tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_hidden_lhb_update_does_not_prime_lhb_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    primes = []
    workspace = _KwargPrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: False
    try:
        event_bus.sig_lhb_pool_updated.emit()

        assert primes == [{"force": False, "include_fund": False, "include_lhb": False}]
        assert tab._context_refresh_pending is True
        assert not tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_current_lhb_update_does_not_reprime_lhb_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    primes = []
    workspace = _KwargPrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: True
    try:
        event_bus.sig_lhb_pool_updated.emit()

        assert primes == [{"force": False, "include_fund": False, "include_lhb": False}]
        assert tab._context_refresh_pending is False
        assert tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_noninteractive_load_ignores_source_update(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        tab._workspace_noninteractive_loaded = True
        tab._is_current_workspace_tab = lambda: False
        event_bus.sig_ai_industry_chain_updated.emit()

        assert not tab._auto_refresh_timer.isActive()
    finally:
        _close_and_delete(tab)


def test_stock_candidate_auto_refreshes_when_stock_context_snapshot_updates(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        event_bus.sig_stock_context_snapshot_updated.emit()

        assert tab._auto_refresh_timer.isActive()
        assert tab._status_primary == "等待综合候选自动刷新"
        assert tab._status_freshness == "数据源已更新"
    finally:
        _close_and_delete(tab)


def test_stock_candidate_auto_refresh_accepts_watchlist_signal_args(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        event_bus.sig_watchlist_changed.emit("add", "300750")

        assert tab._auto_refresh_timer.isActive()
        assert tab._status_primary == "等待综合候选自动刷新"
    finally:
        _close_and_delete(tab)


def test_stock_candidate_prime_background_load_coalesces_snapshot_and_refresh(monkeypatch):
    scheduled = []
    jobs = []
    monkeypatch.setattr(
        "ui.tabs.stock_candidate_tab.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    primes = []
    workspace = _PrimingWorkspace(primes)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    try:
        assert tab.prime_background_load() is True
        assert tab.prime_background_load() is True

        assert primes == ["prime"]
        assert [job[0] for job in jobs] == ["candidate_refresh"]
        assert tab._initial_refresh_started is True
        assert tab._candidate_refresh_pending is False
        assert tab.is_background_preload_complete() is False

        first_result = jobs[0][1](CancellationToken())
        jobs[0][2]["on_success"](first_result)

        assert tab.is_background_preload_complete() is True
        assert tab.prime_background_load() is False
        assert len(jobs) == 1
        assert primes == ["prime"]
        assert tab._background_preload_done is True
        assert tab._background_preload_error == ""
        assert tab.is_background_preload_complete() is True

        followup_schedules_before = sum(
            callback == tab._run_candidate_refresh_followup for _delay, callback in scheduled
        )
        tab._ensure_runtime_started()
        assert (
            sum(callback == tab._run_candidate_refresh_followup for _delay, callback in scheduled)
            == followup_schedules_before
        )
        assert len(jobs) == 1
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_prime_background_load_warms_anchor_caches_without_loading_widgets(monkeypatch):
    primed = []
    loads = []
    snapshots = []
    jobs = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())

    class _AnchorTab:
        def __init__(self, key):
            self.key = key

        def prime_background_load(self):
            primed.append(self.key)

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self._anchors = {
                "na_daily": _AnchorTab("na_daily"),
                "ai_industry_chain": _AnchorTab("ai_industry_chain"),
            }

        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self):
            snapshots.append("prime")
            return True

        def get_loaded_tab(self, key):
            return None

        def ensure_tab_loaded(self, key, reason=""):
            loads.append((key, reason))
            return self._anchors.get(key)

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        tab.prime_background_load()

        assert loads == []
        assert primed == []
        assert snapshots == ["prime"]
        assert [job[0] for job in jobs] == ["candidate_refresh"]
        assert tab._initial_refresh_started is True
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_preload_waits_for_snapshots_then_builds_latest_context_once(monkeypatch):
    jobs = []
    capture_states = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self.snapshots_settled = False
            self.prime_calls = 0

        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self, **_kwargs):
            self.prime_calls += 1
            return True

        def stock_context_snapshots_settled(self):
            return self.snapshots_settled

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(
        stock_candidate_module,
        "capture_workspace_stock_context",
        lambda workspace: capture_states.append(workspace.snapshots_settled) or None,
    )
    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        assert tab.prime_background_load() is True
        assert tab.prime_background_load() is True
        assert workspace.prime_calls == 1
        assert jobs == []
        assert capture_states == []
        assert tab.is_background_preload_complete() is False

        workspace.snapshots_settled = True
        assert tab.is_background_preload_complete() is False
        assert [job[0] for job in jobs] == ["candidate_refresh"]
        assert capture_states == [True]

        result = jobs[0][1](CancellationToken())
        jobs[0][2]["on_success"](result)
        assert tab.is_background_preload_complete() is True
        assert len(jobs) == 1
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_preload_reuses_ready_upstream_snapshots_with_bounded_inner_deadline(
    monkeypatch,
):
    jobs = []
    captures = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self.ready_keys = set(StockCandidateTab.SNAPSHOT_SOURCE_TABS)
            self.prime_calls = 0

        @staticmethod
        def collect_stock_context():
            return {}

        def background_preload_status(self):
            return {"ready_keys": sorted(self.ready_keys)}

        @staticmethod
        def get_loaded_tab(_key):
            raise AssertionError("candidate tab must not inspect upstream widgets")

        def prime_stock_context_snapshots(self, **_kwargs):
            self.prime_calls += 1
            raise AssertionError("ready upstream snapshots must be reused")

        @staticmethod
        def stock_context_snapshots_settled():
            raise AssertionError("ready upstream widgets do not wait for unrelated snapshot tasks")

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(
        stock_candidate_module,
        "capture_workspace_stock_context",
        lambda workspace: captures.append(workspace) or StockContextSnapshot(),
    )
    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        assert tab.prime_background_load() is True
        assert workspace.prime_calls == 0
        assert tab._background_preload_reuses_ready_sources is True
        assert [job[0] for job in jobs] == ["candidate_refresh"]
        assert captures == [workspace]

        from ui.workspaces.classic_workspace import ClassicWorkspace

        timeout_seconds = jobs[0][2]["timeout_sec"]
        assert timeout_seconds == StockCandidateTab.BACKGROUND_REFRESH_TIMEOUT_SECONDS
        assert (
            timeout_seconds * 1000
            < ClassicWorkspace.BACKGROUND_PREWARM_STEP_TIMEOUT_MS
            - ClassicWorkspace.BACKGROUND_PREWARM_CANCEL_SETTLEMENT_TIMEOUT_MS
        )

        result = jobs[0][1](CancellationToken())
        jobs[0][2]["on_success"](result)
        assert tab.is_background_preload_complete() is True
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_background_capture_is_sliced_and_honors_hold_without_sync_capture(monkeypatch):
    jobs = []
    sync_capture_calls = []

    class _CaptureSession:
        def __init__(self):
            self.advance_calls = 0
            self.cancel_calls = 0

        def next_phase_label(self):
            return f"source_{self.advance_calls}"

        def advance(self):
            self.advance_calls += 1
            return self.advance_calls >= 2

        def snapshot(self):
            assert self.advance_calls >= 2
            return StockContextSnapshot()

        def cancel(self):
            self.cancel_calls += 1

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    class _Workspace(QWidget):
        def __init__(self, session):
            super().__init__()
            self.session = session
            self.capture_calls = []

        @staticmethod
        def collect_stock_context():
            return {}

        @staticmethod
        def background_preload_status():
            return {"ready_keys": list(StockCandidateTab.SNAPSHOT_SOURCE_TABS)}

        def begin_background_stock_context_snapshot_capture(self, **kwargs):
            self.capture_calls.append(dict(kwargs))
            return self.session

        @staticmethod
        def tab_specs():
            return []

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(
        stock_candidate_module,
        "capture_workspace_stock_context",
        lambda *_args, **_kwargs: sync_capture_calls.append("sync")
        or (_ for _ in ()).throw(AssertionError("background capture must not fall back to synchronous capture")),
    )
    session = _CaptureSession()
    workspace = _Workspace(session)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        assert tab.prime_background_load() is True
        assert workspace.capture_calls == [
            {"include_rps_bundle": False, "include_cached_sources": False}
        ]
        assert jobs == []

        assert tab.pause_background_preload() is True
        tab._advance_background_context_capture()
        assert session.advance_calls == 0
        assert jobs == []

        assert tab.resume_background_preload() is True
        tab._advance_background_context_capture()
        assert session.advance_calls == 1
        assert jobs == []

        tab._advance_background_context_capture()
        assert session.advance_calls == 2
        assert [name for name, _fn, _kwargs in jobs] == ["candidate_refresh"]
        assert sync_capture_calls == []
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_background_capture_cancel_blocks_late_callback(monkeypatch):
    jobs = []

    class _CaptureSession:
        def __init__(self):
            self.advance_calls = 0
            self.cancel_calls = 0

        @staticmethod
        def next_phase_label():
            return "source"

        def advance(self):
            self.advance_calls += 1
            return False

        @staticmethod
        def snapshot():
            return StockContextSnapshot()

        def cancel(self):
            self.cancel_calls += 1

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    class _Runner:
        @staticmethod
        def is_task_unsettled(_task_id):
            return False

        @staticmethod
        def cancel_task(_task_id, *, reason):
            del reason
            return True

    class _Workspace(QWidget):
        def __init__(self, session):
            super().__init__()
            self.session = session

        @staticmethod
        def collect_stock_context():
            return {}

        @staticmethod
        def background_preload_status():
            return {"ready_keys": list(StockCandidateTab.SNAPSHOT_SOURCE_TABS)}

        def begin_background_stock_context_snapshot_capture(self, **_kwargs):
            return self.session

        @staticmethod
        def tab_specs():
            return []

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(stock_candidate_module, "task_manager", _Runner())
    monkeypatch.setattr(
        stock_candidate_module,
        "capture_workspace_stock_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("late synchronous capture")),
    )
    session = _CaptureSession()
    workspace = _Workspace(session)
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        assert tab.prime_background_load() is True
        receipt = tab.cancel_background_preload(reason="step_timeout")
        assert receipt.is_settled() is True
        assert session.cancel_calls == 1

        tab._advance_background_context_capture()
        assert session.advance_calls == 0
        assert jobs == []
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_cancel_receipt_covers_candidate_and_snapshot_workers(monkeypatch):
    class _Runner:
        def __init__(self):
            self.active = {
                "stock_candidates_context_refresh",
                "stock_context_fund_rows_snapshot",
                "stock_context_lhb_rows_snapshot",
            }
            self.cancel_calls = []

        def is_active_task(self, task_id):
            return task_id in self.active

        def is_task_unsettled(self, task_id):
            return task_id in self.active

        def cancel_task(self, task_id, *, reason):
            self.cancel_calls.append((task_id, reason))
            return True

    class _Lifecycle:
        def __init__(self):
            self.calls = []

        def cancel(self, name, *, reason):
            self.calls.append((name, reason))
            return True

        @staticmethod
        def task_ids_for(_names):
            return ()

        @staticmethod
        def submissions_settled_for(_names):
            return True

        @staticmethod
        def shutdown(*, timeout_ms):
            del timeout_ms
            return True

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self.cancel_calls = []

        @staticmethod
        def collect_stock_context():
            return {}

        @staticmethod
        def stock_context_snapshots_settled():
            return True

        def cancel_stock_context_snapshots(self, *, reason):
            self.cancel_calls.append(reason)
            return True

    runner = _Runner()
    monkeypatch.setattr(stock_candidate_module, "task_manager", runner)
    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._task_lifecycle = _Lifecycle()
    try:
        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert workspace.cancel_calls == ["step_timeout"]
        assert tab._task_lifecycle.calls == [("candidate_refresh", "step_timeout")]
        assert [task_id for task_id, _reason in runner.cancel_calls] == sorted(runner.active)
        assert receipt.is_settled() is False

        runner.active.clear()
        assert receipt.is_settled() is True
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_background_preload_error_is_terminal(monkeypatch):
    jobs = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            jobs.append((name, fn, kwargs))

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    workspace = _PrimingWorkspace([])
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        assert tab.prime_background_load() is True
        assert tab.is_background_preload_complete() is False

        jobs[0][2]["on_error"]("snapshot build failed")

        assert tab._background_preload_done is True
        assert tab._background_preload_error == "snapshot build failed"
        assert tab.is_background_preload_complete() is True
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_table_uses_fresh_context_column_layout(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    captured = {}

    def fake_bind_header_persistence(self, table, settings_key="header_state"):
        captured["settings_key"] = settings_key
        return False

    monkeypatch.setattr(StockCandidateTab, "bind_header_persistence", fake_bind_header_persistence)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        headers = tab.model.headers
        source_col = headers.index(StockCandidateTab.COLUMNS[-3])
        core_col = headers.index(StockCandidateTab.COLUMNS[-2])
        time_col = headers.index(StockCandidateTab.COLUMNS[-1])

        assert captured["settings_key"] == StockCandidateTab.HEADER_STATE_KEY
        assert StockCandidateTab.HEADER_STATE_KEY.endswith("_v2")
        assert tab.table.horizontalHeader().sectionResizeMode(core_col) == QHeaderView.ResizeMode.Stretch

        row = {
            StockCandidateTab.COLUMNS[0]: "300750",
            StockCandidateTab.COLUMNS[1]: "宁德时代",
            StockCandidateTab.COLUMNS[2]: "--",
            StockCandidateTab.COLUMNS[3]: "--",
            StockCandidateTab.COLUMNS[4]: "--",
            StockCandidateTab.COLUMNS[5]: 22,
            StockCandidateTab.COLUMNS[6]: 2,
            StockCandidateTab.COLUMNS[7]: 2,
            StockCandidateTab.COLUMNS[-3]: "VCP扫描｜基金持仓",
            StockCandidateTab.COLUMNS[-2]: "触发日期 20260423 | RPS 96",
            StockCandidateTab.COLUMNS[-1]: "20260423",
        }
        tab.model.update_data([row])

        assert tab.model.data(tab.model.index(0, source_col), Qt.ItemDataRole.DisplayRole) == "VCP扫描｜基金持仓"
        assert tab.model.data(tab.model.index(0, core_col), Qt.ItemDataRole.DisplayRole) == "触发日期 20260423 | RPS 96"
        assert tab.model.data(tab.model.index(0, time_col), Qt.ItemDataRole.DisplayRole) == "20260423"
    finally:
        _close_and_delete(tab)


def test_stock_candidate_refresh_defers_candidate_load_to_background(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    jobs = []

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self.collect_calls = 0
            self.published_indexes = []

        def collect_stock_context(self, **kwargs):
            self.collect_calls += 1
            return {
                "300750": [
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="scan",
                        signal_type="vcp_scan",
                        summary="scan",
                    ),
                ]
            }

        def capture_stock_context_snapshot(self):
            return StockContextSnapshot(
                direct_source_keys=frozenset({"na_daily", "scan"}),
                direct_signals=(
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="scan",
                        signal_type="vcp_scan",
                        summary="scan",
                    ),
                ),
            )

        def publish_stock_context_signal_index(self, index):
            self.published_indexes.append(index)
            return len(self.published_indexes)

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "scan", "title": "scan"},
            ]

    class _Lifecycle:
        def run_background(self, _name, fn, *, on_success=None, on_error=None, task_id=None, **_kwargs):
            jobs.append((lambda: fn(CancellationToken()), on_success, on_error, task_id))

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    tab._build_candidate_rows = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("background worker must not call the QWidget owner")
    )
    try:
        tab.refresh_candidates()

        assert len(jobs) == 1
        assert str(jobs[0][3]) == StockCandidateTab.REFRESH_TASK_ID
        assert workspace.collect_calls == 0
        assert tab.model.row_data == []

        result = jobs[0][0]()
        assert workspace.collect_calls == 0
        jobs[0][1](result)

        assert tab._candidate_refresh_running is False
        assert len(tab.model.row_data) == 1
        assert len(workspace.published_indexes) == 1
        assert workspace.published_indexes[0].signals_for("300750")
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_refresh_queues_followup_while_background_running(monkeypatch):
    scheduled = []
    jobs = []
    monkeypatch.setattr(
        "ui.tabs.stock_candidate_tab.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    class _Workspace(QWidget):
        def collect_stock_context(self, **kwargs):
            return {}

        @staticmethod
        def tab_specs():
            return []

    class _Lifecycle:
        def run_background(self, _name, fn, *, on_success=None, on_error=None, task_id=None, **_kwargs):
            jobs.append((lambda: fn(CancellationToken()), on_success, on_error, task_id))

    monkeypatch.setattr(stock_candidate_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        tab.refresh_candidates()
        tab.refresh_candidates()

        assert len(jobs) == 1
        assert tab._candidate_refresh_pending is True

        jobs[0][1](jobs[0][0]())

        assert tab._candidate_refresh_pending is False
        assert scheduled[-1][0] == 0
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_refresh_exposes_service_lineage(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    _run_candidate_refreshes_inline(monkeypatch)

    class _Provider:
        @staticmethod
        def read_provider_health():
            from infra.market_data.provider_ports import ProviderHealthSnapshot

            return ProviderHealthSnapshot(
                request_stats={
                    "recent_triggered_network": False,
                    "recent_cache_hit_count": 1,
                    "recent_status": "runtime_cache_hit",
                    "recent_source_layers": ["runtime_cache"],
                },
                runtime_stats={"cooldown_until": 0.0, "last_error": ""},
            )

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {
                "300750": [
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                        observed_at="2026-05-08",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="scan",
                        signal_type="vcp_scan",
                        summary="scan",
                        observed_at="2026-05-09",
                    ),
                ]
            }

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "scan", "title": "scan"},
            ]

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=_Provider(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    try:
        tab.refresh_candidates()
        lineage = tab.get_data_lineage()

        assert {"key", "view", "source", "provider", "cache_refs", "network_capable"}.isdisjoint(lineage)
        assert lineage["triggered_network"] is False
        assert lineage["trade_date"] == "2026-05-09"
        assert lineage["row_count"] == 1
        assert lineage["signal_count"] == 2
        assert lineage["source_tabs"] == ["na_daily", "scan"]
        assert lineage["provider_fault_tolerance"]["recent_cache_hit_count"] == 1
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_refresh_collects_context_without_lhb_compute(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    _run_candidate_refreshes_inline(monkeypatch)
    calls = []

    class _Workspace(QWidget):
        def collect_stock_context(self, **kwargs):
            calls.append(dict(kwargs))
            return {
                "300750": [
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="lhb",
                        signal_type="lhb",
                        summary="lhb",
                    ),
                ]
            }

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "lhb", "title": "lhb"},
            ]

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    try:
        tab.refresh_candidates()

        assert calls == [
            {"capture_snapshot": True},
            {
                "allow_lhb_cache_compute": False,
                "allow_async_snapshot_refresh": True,
            }
        ]
        assert tab.get_data_lineage()["row_count"] == 1
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_after_hours_refresh_suppresses_snapshot_wakeup(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stock_candidate_module.MarketCalendar, "is_quote_refresh_time", lambda: False)
    _run_candidate_refreshes_inline(monkeypatch)
    calls = []

    class _Workspace(QWidget):
        def collect_stock_context(self, **kwargs):
            calls.append(dict(kwargs))
            return {
                "300750": [
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="scan",
                        signal_type="vcp_scan",
                        summary="scan",
                    ),
                ]
            }

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "scan", "title": "scan"},
            ]

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    try:
        tab.refresh_candidates()

        assert calls == [
            {"capture_snapshot": True},
            {
                "allow_lhb_cache_compute": False,
                "allow_async_snapshot_refresh": False,
            }
        ]
        assert tab.get_data_lineage()["row_count"] == 1
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_refresh_skips_model_update_when_rows_unchanged(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    _run_candidate_refreshes_inline(monkeypatch)

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {
                "300750": [
                    StockSignal(
                        code="300750",
                        source_tab="na_daily",
                        signal_type="catalyst",
                        summary="anchor",
                    ),
                    StockSignal(
                        code="300750",
                        source_tab="scan",
                        signal_type="vcp_scan",
                        summary="scan",
                    ),
                ]
            }

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "scan", "title": "scan"},
            ]

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    refresh_calls = []
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: refresh_calls.append(True)
    update_calls = []
    original_update_data = tab.model.update_data

    def _spy_update_data(rows, **kwargs):
        update_calls.append((len(rows), dict(kwargs)))
        original_update_data(rows, **kwargs)

    tab.model.update_data = _spy_update_data
    try:
        tab.refresh_candidates()
        tab.refresh_candidates()

        assert update_calls == [(1, {"hydrate_latest_quotes": False})]
        assert refresh_calls == [True]
    finally:
        _close_and_delete(tab)
        workspace.deleteLater()


def test_stock_candidate_realtime_quote_projection_only_falls_back_while_initially_pending(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        assert tab.get_realtime_quote_source_projection() == {
            "codes": (),
            "status": "pending",
            "reason": "stock_candidates_tab_deferred",
        }

        tab._realtime_quote_projection_state = "ready"
        tab._realtime_quote_projection_reason = ""
        assert tab.get_realtime_quote_source_projection() == {
            "codes": (),
            "status": "registered_empty",
            "reason": "",
        }

        tab._realtime_quote_projection_state = "error"
        tab._realtime_quote_projection_reason = "stock_candidates_tab_refresh_failed"
        assert tab.get_realtime_quote_source_projection() == {
            "codes": (),
            "status": "error",
            "reason": "stock_candidates_tab_refresh_failed",
        }
    finally:
        _close_and_delete(tab)
