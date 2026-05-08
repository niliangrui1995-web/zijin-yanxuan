# -*- coding: utf-8 -*-
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QSignalSpy

from app.services.ui_runtime_service import domain_events as event_bus
from app.services.ui_runtime_service import watchlist_vm
from core.global_store import global_store
from ui.tabs.ai_industry_chain_tab import AIIndustryChainTab


class DummyProvider:
    def __init__(self):
        self.calls = []

    def get_data(self, code):
        if code == "002384":
            return pd.DataFrame({"close": list(range(1, 22))})
        return pd.DataFrame({"close": [10, 11, 12, 13, 14, 15]})

    def fetch_realtime_quotes_batch(self, codes):
        self.calls.append(list(codes))
        return {}


class NoFetchProvider(DummyProvider):
    def fetch_realtime_quotes_batch(self, codes):
        raise AssertionError(f"should reuse global quote snapshot, got {codes}")


def _write_workbook(path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AI产业链"
    ws.append(["细分板块", "代码", "公司名称", "备注"])
    ws.append(["200G EML / InP激光器", "002384", "东山精密", "高速光芯片/光模块链条"])
    ws.append(["光DSP / 200G SerDes", "暂无", "暂无合格A股纯标的", "不硬凑"])
    ws.append(["CW / UHP激光器", "688498", "源杰科技", "布局CW激光器"])
    wb.save(path)


def test_ai_industry_chain_loads_workbook_and_period_returns(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )

    try:
        tab._load_chain_data()

        assert tab.model.headers == [
            "序号", "代码", "名称", "现价", "涨幅", "市值", "细分板块",
            "5日涨幅", "10日涨幅", "20日涨幅", "备注",
        ]
        assert len(tab.model.row_data) == 2
        first = tab.model.row_data[0]
        assert first["代码"] == "002384"
        assert first["名称"] == "东山精密"
        assert first["细分板块"] == "200G EML / InP激光器"
        assert first["备注"] == "高速光芯片/光模块链条"
        assert first["5日涨幅"] == pytest.approx((21 / 16 - 1) * 100)
        assert first["10日涨幅"] == pytest.approx((21 / 11 - 1) * 100)
        assert first["20日涨幅"] == pytest.approx((21 / 1 - 1) * 100)
        assert refresh_calls == [{"quote_task_id": "ai_industry_chain_quotes"}]
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_uses_plain_pct_headers_for_quotes(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        tab._load_chain_data()
        tab.model.update_quotes({
            "002384": {"close": 12.6, "last_close": 12.0, "zongguben": 1_000_000_000},
        })

        row = tab.model.row_data[0]
        assert row["现价"] == "12.60"
        assert row["涨幅"] == pytest.approx(5.0)
        assert row["市值"] == "126亿"

        pct_col = tab.model.headers.index("涨幅")
        assert tab.model.data(tab.model.index(0, pct_col), Qt.ItemDataRole.DisplayRole) == "+5.00%"
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_reset_view_restores_source_order(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        tab._load_chain_data()
        code_col = tab.model.headers.index("代码")

        tab.table.sortByColumn(code_col, Qt.SortOrder.DescendingOrder)
        assert tab.proxy_model.sortColumn() == code_col

        tab._reset_view()

        assert tab.proxy_model.sortColumn() == -1
        visible_codes = []
        for row in range(tab.proxy_model.rowCount()):
            source_row = tab.proxy_model.mapToSource(tab.proxy_model.index(row, 0)).row()
            visible_codes.append(tab.model.row_data[source_row]["代码"])
        assert visible_codes == ["002384", "688498"]
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_refreshes_from_global_snapshot_without_fetch(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    global_store.reset_quotes()

    provider = DummyProvider()
    tab = AIIndustryChainTab(provider, workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        tab._load_chain_data()
        global_store.merge_quotes({
            "002384": {"close": 13.2, "last_close": 12.0, "zongguben": 1_000_000_000},
        })

        tab.refresh_table_from_latest_snapshot()

        row = tab.model.row_data[0]
        assert row["现价"] == "13.20"
        assert row["涨幅"] == pytest.approx(10.0)
        assert row["市值"] == "132亿"
        assert provider.calls == []
    finally:
        global_store.reset_quotes()
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_load_reuses_global_quote_snapshot(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    global_store.reset_quotes()
    global_store.merge_quotes({
        "002384": {"close": 13.2, "last_close": 12.0, "zongguben": 1_000_000_000},
        "688498": {"close": 98.5, "last_close": 102.0, "zongguben": 120_000_000},
    })

    tab = AIIndustryChainTab(NoFetchProvider(), workbook_path=workbook_path)

    try:
        tab._load_chain_data()

        row = tab.model.row_data[0]
        assert row["现价"] == "13.20"
        assert row["涨幅"] == pytest.approx(10.0)
        assert row["市值"] == "132亿"
    finally:
        global_store.reset_quotes()
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_watchlist_payload_maps_segment_and_source(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {}

    payload = AIIndustryChainTab._build_watchlist_payload({
        "代码": "002384",
        "名称": "东山精密",
        "现价": "13.20",
        "涨幅": 10.0,
        "市值": "132亿",
        "细分板块": "200G EML / InP激光器",
        "备注": "高速光芯片/光模块链条",
    })

    try:
        assert payload["细分板块"] == "200G EML / InP激光器"
        assert payload["AI产业链"] == "高速光芯片/光模块链条"
        assert payload["来源标签"] == ["AI产业链"]

        assert watchlist_vm.add_stock("002384", "东山精密", payload) is True
        entry = watchlist_vm._cache["002384"]
        assert entry["细分板块"] == "200G EML / InP激光器"
        assert entry["AI产业链"] == "高速光芯片/光模块链条"
        assert entry["来源标签"] == ["AI产业链"]
        assert "涨幅" not in entry
    finally:
        watchlist_vm._cache = original_cache


def test_ai_industry_chain_emits_updated_event_after_successful_load(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)
    spy = QSignalSpy(event_bus.sig_ai_industry_chain_updated)

    try:
        tab._load_chain_data()

        assert len(spy) == 1
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_prime_background_loads_workbook_immediately(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )

    try:
        tab.prime_background_load()

        assert tab._runtime_started is True
        assert len(tab.model.row_data) == 2
        assert tab._chain_codes == {"002384", "688498"}
        assert refresh_calls == [{"quote_task_id": "ai_industry_chain_quotes"}]
    finally:
        tab.close()
        tab.deleteLater()
