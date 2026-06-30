# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QShowEvent
from PyQt6.QtWidgets import QHeaderView, QWidget

from app.services.ui_event_service import domain_events as event_bus
from ui.tabs import stock_candidate_tab as stock_candidate_module
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.theme import theme_manager
from ui.workspaces.stock_signal import StockSignal


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
        tab.close()


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
        tab.close()


def test_stock_candidate_earnings_update_primes_context_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    primes = []

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self):
            primes.append("prime")
            return True

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        event_bus.sig_earnings_updated.emit()

        assert primes == ["prime"]
        assert tab._auto_refresh_timer.isActive()
    finally:
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_hidden_context_update_defers_refresh_until_visible(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    primes = []
    current = {"value": False}

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self):
            primes.append("prime")
            return True

    workspace = _Workspace()
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
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_hidden_lhb_update_does_not_prime_lhb_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    primes = []

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self, **kwargs):
            primes.append(dict(kwargs))
            return True

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: False
    try:
        event_bus.sig_lhb_pool_updated.emit()

        assert primes == [{"force": False, "include_fund": False, "include_lhb": False}]
        assert tab._context_refresh_pending is True
        assert not tab._auto_refresh_timer.isActive()
    finally:
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_current_lhb_update_does_not_reprime_lhb_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    primes = []

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self, **kwargs):
            primes.append(dict(kwargs))
            return True

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab._is_current_workspace_tab = lambda: True
    try:
        event_bus.sig_lhb_pool_updated.emit()

        assert primes == [{"force": False, "include_fund": False, "include_lhb": False}]
        assert tab._context_refresh_pending is False
        assert tab._auto_refresh_timer.isActive()
    finally:
        tab.close()
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
        tab.close()


def test_stock_candidate_auto_refreshes_when_stock_context_snapshot_updates(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        event_bus.sig_stock_context_snapshot_updated.emit()

        assert tab._auto_refresh_timer.isActive()
        assert tab._status_primary == "等待综合候选自动刷新"
        assert tab._status_freshness == "数据源已更新"
    finally:
        tab.close()


def test_stock_candidate_auto_refresh_accepts_watchlist_signal_args(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        event_bus.sig_watchlist_changed.emit("add", "300750")

        assert tab._auto_refresh_timer.isActive()
        assert tab._status_primary == "等待综合候选自动刷新"
    finally:
        tab.close()


def test_stock_candidate_prime_background_load_primes_snapshot_and_refresh(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda delay, callback: scheduled.append(delay)
    )
    primes = []

    class _Workspace(QWidget):
        def collect_stock_context(self):
            return {}

        def prime_stock_context_snapshots(self):
            primes.append("prime")
            return True

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    try:
        tab.prime_background_load()
        tab.prime_background_load()

        assert primes == ["prime", "prime"]
        assert 350 not in scheduled
        assert tab._initial_refresh_started is False
    finally:
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_prime_background_load_primes_anchor_sources(monkeypatch):
    scheduled = []
    primed = []
    loads = []
    snapshots = []
    monkeypatch.setattr(
        "ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda delay, callback: scheduled.append(delay)
    )

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

        assert loads == [
            ("na_daily", "stock_candidates_anchor"),
            ("ai_industry_chain", "stock_candidates_anchor"),
        ]
        assert primed == ["na_daily", "ai_industry_chain"]
        assert snapshots == ["prime"]
        assert 350 not in scheduled
    finally:
        tab.close()
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
        tab.close()


def test_stock_candidate_refresh_defers_candidate_load_to_background(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    jobs = []

    class _Workspace(QWidget):
        def __init__(self):
            super().__init__()
            self.collect_calls = 0

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

        @staticmethod
        def tab_specs():
            return [
                {"key": "na_daily", "title": "na"},
                {"key": "scan", "title": "scan"},
            ]

    def _capture_run_in_background(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        jobs.append((fn, on_success, on_error, task_id))
        return task_id

    monkeypatch.setattr(
        stock_candidate_module.task_manager,
        "run_in_background",
        _capture_run_in_background,
        raising=False,
    )

    workspace = _Workspace()
    tab = StockCandidateTab(data_provider=SimpleNamespace(), parent=workspace)
    tab.refresh_table_from_latest_snapshot = lambda *_args, **_kwargs: None
    try:
        tab.refresh_candidates()

        assert len(jobs) == 1
        assert str(jobs[0][3]) == StockCandidateTab.REFRESH_TASK_ID
        assert workspace.collect_calls == 0
        assert tab.model.row_data == []

        result = jobs[0][0]()
        assert workspace.collect_calls == 1
        jobs[0][1](result)

        assert tab._candidate_refresh_running is False
        assert len(tab.model.row_data) == 1
    finally:
        tab.close()
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

    def _capture_run_in_background(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        jobs.append((fn, on_success, on_error, task_id))
        return task_id

    monkeypatch.setattr(
        stock_candidate_module.task_manager,
        "run_in_background",
        _capture_run_in_background,
        raising=False,
    )

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
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_refresh_exposes_service_lineage(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    _run_candidate_refreshes_inline(monkeypatch)

    class _Provider:
        _rt_eastmoney_cooldown_until = 0.0
        _rt_eastmoney_last_error = ""

        @staticmethod
        def get_quote_request_stats():
            return {
                "recent_triggered_network": False,
                "recent_cache_hit_count": 1,
                "recent_status": "runtime_cache_hit",
                "recent_source_layers": ["runtime_cache"],
            }

        @staticmethod
        def get_realtime_runtime_stats():
            return {"cooldown_until": 0.0, "last_error": ""}

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

        assert lineage["key"] == "stock_candidates"
        assert lineage["source"] == "workspace_stock_context"
        assert lineage["triggered_network"] is False
        assert lineage["trade_date"] == "2026-05-09"
        assert lineage["row_count"] == 1
        assert lineage["signal_count"] == 2
        assert lineage["source_tabs"] == ["na_daily", "scan"]
        assert lineage["provider_fault_tolerance"]["recent_cache_hit_count"] == 1
    finally:
        tab.close()
        workspace.deleteLater()


def test_stock_candidate_refresh_collects_context_without_lhb_compute(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
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

        assert calls == [{"allow_lhb_cache_compute": False}]
        assert tab.get_data_lineage()["row_count"] == 1
    finally:
        tab.close()
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
        tab.close()
        workspace.deleteLater()
