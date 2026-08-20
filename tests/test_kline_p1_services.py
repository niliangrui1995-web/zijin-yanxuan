# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import os
from contextlib import nullcontext
from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from app.services import asian_market_cache_service as asian_cache_service
from app.services import kline_open_service
from app.services import kline_render_preparer as render_preparer_module
from app.services.kline_data_service import KlineDataService
from app.services.kline_open_context import KlineOpenContext
from app.services.kline_open_service import build_kline_open_context
from app.services.kline_render_preparer import KlineRenderPreparer
from infra.market_data.local_history_provider import LocalHistoryProvider
from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_preheated_shell_html,
    build_kline_shell_html,
)


class _RejectDeepCopy:
    def __deepcopy__(self, _memo):
        raise AssertionError("non-current navigation rows must not be deep-copied")


def _context() -> KlineOpenContext:
    return KlineOpenContext(
        code="000001",
        name="平安银行",
        vcp_data={"代码": "000001", "名称": "平安银行"},
        navigation=(),
        current_idx=0,
        source_tab_key="watchlist",
        source_tab_index=1,
    )


def _frame(rows: int = 260) -> pd.DataFrame:
    closes = [10.0 + index * 0.05 for index in range(rows)]
    return pd.DataFrame(
        {
            "open": [value - 0.1 for value in closes],
            "high": [value + 0.3 for value in closes],
            "low": [value - 0.3 for value in closes],
            "close": closes,
            "volume": [10000.0 + index for index in range(rows)],
        },
        index=pd.date_range("2025-01-01", periods=rows, freq="D"),
    )


def test_kline_open_context_is_frozen_and_navigation_is_compact(monkeypatch):
    signal_reads = []
    monkeypatch.setattr(
        kline_open_service,
        "_workspace_stock_signals",
        lambda workspace, code: signal_reads.append((workspace, code)) or [],
    )

    context = build_kline_open_context(
        code="000001",
        code_name_map={"000001": "平安银行", "000002": "万科A"},
        code_list=[
            {"代码": "000001", "名称": "平安银行", "来源": "关注池"},
            {"代码": "000002", "名称": "万科A", "heavy": _RejectDeepCopy()},
        ],
        current_idx=0,
        workspace="workspace",
        source_tab_index=1,
        source_tab_key="watchlist",
    )

    assert signal_reads == [("workspace", "000001")]
    assert [(item.code, item.name) for item in context.navigation] == [
        ("000001", "平安银行"),
        ("000002", "万科A"),
    ]
    assert not hasattr(context.navigation[1], "heavy")
    assert context.vcp_data["来源"] == "关注池"
    with pytest.raises(TypeError):
        context.vcp_data["来源"] = "changed"
    with pytest.raises(FrozenInstanceError):
        context.code = "000002"


def test_kline_render_preparer_builds_one_owned_json_without_scan_indicators(monkeypatch):
    from domains.scan.indicator_service import IndicatorService

    monkeypatch.setattr(
        IndicatorService,
        "calculate_indicators",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scan indicators must not run")),
    )
    real_dumps = json.dumps
    dump_calls = []

    def _counting_dumps(*args, **kwargs):
        dump_calls.append(True)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(render_preparer_module.json, "dumps", _counting_dumps)
    prepared_columns = []

    def _payload_builder(frame, **kwargs):
        prepared_columns.extend(frame.columns)
        return build_kline_echarts_payload(frame, **kwargs)

    source_frame = _frame()
    prepared = KlineRenderPreparer(_payload_builder).prepare(
        source_frame,
        context=_context(),
        owner_id="window-1",
        generation=7,
        snapshot_version=3,
    )

    payload = json.loads(prepared.payload_json)
    chart_data = payload["data"]
    assert payload["windowId"] == payload["window_id"] == "window-1"
    assert payload["generation"] == 7
    assert payload["snapshotVersion"] == payload["snapshot_version"] == 3
    assert payload["code"] == "000001"
    assert payload["points"] == 250
    assert prepared.identity == ("window-1", 7, "000001")
    assert prepared.snapshot_version == 3
    assert prepared.point_count == 250
    display_frame = prepared.display_frame
    history_frame = prepared.history_frame
    assert len(display_frame) == 250
    assert len(history_frame) == len(source_frame)
    assert display_frame.attrs == {
        "kline_window_id": "window-1",
        "kline_generation": 7,
        "kline_code": "000001",
        "kline_snapshot_version": 3,
    }
    assert history_frame.attrs == display_frame.attrs
    expected_macd = (
        source_frame["close"].ewm(span=12, adjust=False).mean()
        - source_frame["close"].ewm(span=26, adjust=False).mean()
    )
    assert display_frame.iloc[0]["MACD"] == pytest.approx(expected_macd.iloc[-250])
    display_frame.iloc[-1, display_frame.columns.get_loc("close")] = -1
    assert prepared.display_frame.iloc[-1]["close"] != -1
    assert len(chart_data["dates"]) == 250
    assert len(chart_data["macd"]) == 250
    assert len(chart_data["diff"]) == 250
    assert len(chart_data["dea"]) == 250
    assert len(chart_data["volMa20"]) == 250
    assert "ma5" not in chart_data
    assert "tradeMarkers" not in chart_data
    assert set(prepared_columns) == {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ma10",
        "ma20",
        "ma50",
        "ma150",
        "ma200",
        "volMa20",
        "MACD",
        "MACD_Signal",
        "MACD_Hist",
    }
    assert len(dump_calls) == 1
    with pytest.raises(FrozenInstanceError):
        prepared.generation = 8


def test_kline_render_preparer_computes_visible_indicators_from_full_history():
    source_frame = _frame(500)
    prepared = KlineRenderPreparer(build_kline_echarts_payload).prepare(
        source_frame,
        context=_context(),
        owner_id="window-full-history",
        generation=1,
    )

    chart_data = json.loads(prepared.payload_json)["data"]
    visible_start = len(source_frame) - 250
    expected = {
        "ma10": source_frame["close"].rolling(10).mean(),
        "ma20": source_frame["close"].rolling(20).mean(),
        "ma50": source_frame["close"].rolling(50).mean(),
        "ma150": source_frame["close"].rolling(150).mean(),
        "ma200": source_frame["close"].rolling(200).mean(),
        "volMa20": source_frame["volume"].rolling(20).mean(),
    }

    for key, series in expected.items():
        digits = 0 if key == "volMa20" else 2
        assert chart_data[key][0] == pytest.approx(round(float(series.iloc[visible_start]), digits))
        assert chart_data[key][-1] == pytest.approx(round(float(series.iloc[-1]), digits))


def test_kline_shell_html_is_static_and_contains_no_market_rows(tmp_path):
    echarts_path = tmp_path / "echarts.min.js"
    echarts_path.write_text("window.echarts = {};", encoding="utf-8")
    colors = {
        "bg_canvas": "#000000",
        "bg_toolbar": "#000000",
        "border": "#222222",
        "text_primary": "#ffffff",
        "text_secondary": "#cccccc",
        "text_muted": "#999999",
        "depth_line": "#222222",
        "ma10": "#111111",
        "ma20": "#222222",
        "ma50": "#333333",
        "ma150": "#444444",
        "ma200": "#555555",
        "scrollbar_handle": "#666666",
        "scrollbar_handle_hover": "#777777",
        "scrollbar_handle_pressed": "#888888",
        "font_family": "sans-serif",
        "mono_font_family": "monospace",
        "up_color": "#ff0000",
        "down_color": "#00ff00",
    }

    html = build_kline_shell_html("K线", str(echarts_path), colors)

    assert "window.applySnapshot" in html
    assert "window.replaceKlineData" not in html
    assert "window.updateLastBar" not in html
    assert '"dates": []' in html
    assert '"klines": []' in html
    assert "000001" not in html
    assert "平安银行" not in html

    preheated_html = build_kline_preheated_shell_html("K线", str(echarts_path), colors)
    assert "preheat-000" in preheated_html and "preheat-249" in preheated_html
    assert all(f'"ma{period}"' in preheated_html for period in (10, 20, 50, 150, 200))
    assert '"ma5"' not in preheated_html


def test_asian_ticker_index_reuses_signature_invalidates_and_returns_safe_copy(tmp_path, monkeypatch):
    cache_path = tmp_path / "asian_klines_latest.json"
    cache_path.write_text(
        json.dumps(
            {
                "stocks": [
                    {"ticker": "2330.TW", "klines": [{"date": "2026-07-15", "close": 100}]},
                    {"ticker": "005930.KS", "klines": [{"date": "2026-07-15", "close": 200}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    asian_cache_service.clear_asian_ticker_index_cache()
    real_reader = asian_cache_service.read_json_cache
    reads = []

    def _counting_reader(path, *, default=None):
        reads.append(path)
        return real_reader(path, default=default)

    monkeypatch.setattr(asian_cache_service, "read_json_cache", _counting_reader)
    first = asian_cache_service.load_cached_asian_stock(str(cache_path), "2330.TW")
    second = asian_cache_service.load_cached_asian_stock(str(cache_path), "005930.KS")
    first["klines"][0]["close"] = -1

    assert second["ticker"] == "005930.KS"
    assert asian_cache_service.load_cached_asian_stock(str(cache_path), "2330.TW")["klines"][0]["close"] == 100
    assert len(reads) == 1

    previous_mtime_ns = cache_path.stat().st_mtime_ns
    cache_path.write_text(
        json.dumps({"stocks": [{"ticker": "2330.TW", "klines": [{"date": "2026-07-16", "close": 101}]}]}),
        encoding="utf-8",
    )
    os.utime(cache_path, ns=(previous_mtime_ns + 1_000_000, previous_mtime_ns + 1_000_000))

    refreshed = asian_cache_service.load_cached_asian_stock(str(cache_path), "2330.TW")
    assert refreshed["klines"][0]["close"] == 101
    assert asian_cache_service.load_cached_asian_stock(str(cache_path), "005930.KS") is None
    assert len(reads) == 2


class _CountingToken:
    def __init__(self):
        self.checkpoints = 0

    def raise_if_cancelled(self):
        self.checkpoints += 1


def test_kline_data_service_refreshes_stale_cn_data_and_preserves_source_metadata():
    initial = _frame(250)
    refreshed = _frame(251)
    token = _CountingToken()

    class Provider:
        def __init__(self):
            self.fresh_calls = []

        def get_data(self, code, *, cancellation_token=None):
            assert cancellation_token is token
            assert code == "000001"
            return initial

        def get_data_fresh_for_chart(self, code, force_sync=False, *, cancellation_token=None):
            assert cancellation_token is token
            self.fresh_calls.append((code, force_sync))
            return refreshed

        @staticmethod
        def get_market_data_source_status():
            return {"active_layer": "vipdoc_fallback"}

    provider = Provider()
    result = KlineDataService(provider).load(
        _context(),
        target_trade_date=refreshed.index.max().date(),
        cancellation_token=token,
    )

    assert provider.fresh_calls == [("000001", True)]
    assert result.source == "vipdoc_fallback"
    assert result.degraded is False
    assert result.latest_trade_date == refreshed.index.max().date()
    assert len(result.data) == 251
    assert result.data is not refreshed
    assert token.checkpoints >= 4


def test_kline_data_service_loads_asian_cache_with_degradation_metadata(tmp_path):
    token = _CountingToken()
    loader_calls = []

    def _loader(path, code, *, cancellation_token=None):
        loader_calls.append((path, code, cancellation_token))
        return {
            "ticker": code,
            "klines": [
                {"date": "2026-07-15", "open": 99, "high": 102, "low": 98, "close": 101, "volume": 80}
            ],
        }

    context = KlineOpenContext(code="2330.TW", name="台积电")
    service = KlineDataService(None, asian_stock_loader=_loader)
    result = service.load(
        context,
        asian_cache_path=str(tmp_path / "asian.json"),
        cancellation_token=token,
    )

    assert loader_calls == [(str(tmp_path / "asian.json"), "2330.TW", token)]
    assert result.source == "asian_json_cache"
    assert result.degraded is False
    assert result.latest_trade_date == pd.Timestamp("2026-07-15").date()
    assert result.data.iloc[-1]["close"] == 101

    missing = KlineDataService(None, asian_stock_loader=lambda *_args, **_kwargs: None).load(
        context,
        asian_cache_path=str(tmp_path / "missing.json"),
    )
    assert missing.data is None
    assert missing.degraded is True
    assert missing.degradation_reason == "asian_history_unavailable"


def test_kline_data_service_marks_nonempty_asian_cache_stale(tmp_path):
    def _loader(_path, _code, *, cancellation_token=None):
        return {
            "ticker": "2330.TW",
            "klines": [
                {"date": "2026-08-14", "open": 99, "high": 102, "low": 98, "close": 101, "volume": 80}
            ],
        }

    result = KlineDataService(None, asian_stock_loader=_loader).load(
        KlineOpenContext(code="2330.TW", name="台积电"),
        asian_cache_path=str(tmp_path / "asian.json"),
        target_trade_date=dt.date(2026, 8, 19),
    )

    assert result.data is not None
    assert result.latest_trade_date == dt.date(2026, 8, 14)
    assert result.degraded is True
    assert result.degradation_reason == "asian_history_stale"


def test_chart_history_refresh_propagates_token_and_skips_full_scan_indicators(monkeypatch):
    from domains.scan.indicator_service import IndicatorService

    existing = _frame(250)
    new = _frame(251).iloc[-1:]
    token = _CountingToken()

    class Provider:
        cache_data = {}
        cache_lock = nullcontext()
        server_pool = True

        @staticmethod
        def get_data(code, *, cancellation_token=None):
            assert code == "000001"
            assert cancellation_token is token
            return existing

        @staticmethod
        def _is_before_930_today():
            return False

        @staticmethod
        def _is_after_1500_today():
            return False

        @staticmethod
        def _get_thread_api():
            return "api"

    monkeypatch.setattr(
        IndicatorService,
        "calculate_indicators",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full scan must not run")),
    )
    service = LocalHistoryProvider(Provider())

    def _fetch(api, code, count, *, cancellation_token=None):
        assert (api, code) == ("api", "000001")
        assert cancellation_token is token
        return new

    monkeypatch.setattr(service, "fetch_standard_data", _fetch)
    result = service.get_data_fresh_for_chart("000001", cancellation_token=token)

    assert len(result) == 251
    assert "entangle" not in result.columns
    assert token.checkpoints >= 4
