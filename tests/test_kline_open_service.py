# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.kline_open_service import build_kline_open_request
from ui.workspaces.stock_signal import StockSignal

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_SOURCE_LABEL = "\u6765\u6e90\u6807\u7b7e"
KEY_RPS_STRENGTH = "RPS\u5f3a\u5ea6"
KEY_TRIGGER_DATE = "\u89e6\u53d1\u65e5\u671f"
KEY_RANGE_HIGH = "\u533a\u95f4\u6700\u9ad8\u4ef7"
KEY_RANGE_LOW = "\u533a\u95f4\u6700\u4f4e\u70b9"


def test_build_kline_open_request_does_not_merge_scan_context_into_non_scan_row():
    workspace = SimpleNamespace(
        get_scan_results=lambda: [
            {
                "代码": "300750",
                "名称": "宁德时代",
                "突破价": 251.2,
                "箱体上沿": 248.5,
            }
        ]
    )

    request = build_kline_open_request(
        code="300750",
        code_name_map={"300750": "宁德时代"},
        code_list=[
            {
                "代码": "300750",
                "名称": "宁德时代",
                "来源": "龙虎榜",
            }
        ],
        current_idx=0,
        workspace=workspace,
        source_tab_index=3,
        source_tab_key="lhb",
    )

    assert request["code"] == "300750"
    assert request["name"] == "宁德时代"
    assert request["current_idx"] == 0
    assert request["code_list"][0]["__source_tab_index"] == 3
    assert request["code_list"][0]["__source_tab_key"] == "lhb"
    assert request["vcp_data"]["来源"] == "龙虎榜"
    assert "突破价" not in request["vcp_data"]
    assert "箱体上沿" not in request["vcp_data"]


def test_build_kline_open_request_merges_scan_context_for_scan_source():
    workspace = SimpleNamespace(
        get_scan_results=lambda: [
            {
                "代码": "300750",
                "名称": "宁德时代",
                "突破价": 251.2,
                "箱体上沿": 248.5,
            }
        ]
    )

    request = build_kline_open_request(
        code="300750",
        code_name_map={"300750": "宁德时代"},
        code_list=[
            {
                "代码": "300750",
                "名称": "宁德时代",
            }
        ],
        current_idx=0,
        workspace=workspace,
        source_tab_index=4,
        source_tab_key="scan",
    )

    assert request["vcp_data"]["突破价"] == 251.2
    assert request["vcp_data"]["箱体上沿"] == 248.5
    assert request["vcp_data"]["_vcp_overlay_allowed"] is True


def test_build_kline_open_request_does_not_add_scan_context_for_plain_watchlist():
    workspace = SimpleNamespace(
        get_scan_results=lambda: [
            {
                "代码": "002975",
                "名称": "博杰股份",
                "RPS强度": "93/95",
                "区间最高价": 91.0,
                "区间最低点": 65.6,
            }
        ]
    )

    request = build_kline_open_request(
        code="002975",
        code_name_map={"002975": "博杰股份"},
        code_list=[
            {
                "代码": "002975",
                "名称": "博杰股份",
                "来源标签": ["业绩", "AI产业链"],
                "RPS强度": "97/90",
            }
        ],
        current_idx=0,
        workspace=workspace,
        source_tab_index=1,
        source_tab_key="watchlist",
    )

    assert request["vcp_data"]["RPS强度"] == "97/90"
    assert request["vcp_data"]["来源标签"] == ["业绩", "AI产业链"]
    assert "区间最高价" not in request["vcp_data"]
    assert "区间最低点" not in request["vcp_data"]
    assert "_vcp_overlay_allowed" not in request["vcp_data"]


def test_build_kline_open_request_uses_workspace_stock_context_scan_signal_for_watchlist():
    scan_signal = StockSignal(
        code="002975",
        name="BoJie",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="VCP",
        observed_at="20260416",
        payload={
            KEY_CODE: "002975",
            KEY_NAME: "BoJie",
            KEY_RPS_STRENGTH: "93/95",
            KEY_TRIGGER_DATE: "20260416",
            KEY_RANGE_HIGH: 91.0,
            KEY_RANGE_LOW: 65.6,
            "_peak_dates": ["20251127", "20260123", "20260226", "20260410"],
        },
    )
    workspace = SimpleNamespace(
        get_scan_results=lambda: [],
        collect_stock_context=lambda: {"002975": [scan_signal]},
    )

    request = build_kline_open_request(
        code="002975",
        code_name_map={"002975": "BoJie"},
        code_list=[
            {
                KEY_CODE: "002975",
                KEY_NAME: "BoJie",
                KEY_SOURCE_LABEL: ["earnings", "AI-chain"],
                KEY_RPS_STRENGTH: "97/90",
            }
        ],
        current_idx=0,
        workspace=workspace,
        source_tab_index=1,
        source_tab_key="watchlist",
    )

    assert request["vcp_data"][KEY_RPS_STRENGTH] == "97/90"
    assert request["vcp_data"][KEY_SOURCE_LABEL] == ["earnings", "AI-chain"]
    assert request["vcp_data"][KEY_TRIGGER_DATE] == "20260416"
    assert request["vcp_data"][KEY_RANGE_HIGH] == 91.0
    assert request["vcp_data"][KEY_RANGE_LOW] == 65.6
    assert request["vcp_data"]["_peak_dates"] == ["20251127", "20260123", "20260226", "20260410"]
    assert request["vcp_data"]["_vcp_overlay_allowed"] is True


def test_build_kline_open_request_uses_embedded_scan_signal_from_candidate_row():
    request = build_kline_open_request(
        code="300750",
        code_name_map={"300750": "宁德时代"},
        code_list=[
            {
                "代码": "300750",
                "名称": "宁德时代",
                "来源": "业绩｜VCP扫描",
                "_signals": [
                    {
                        "code": "300750",
                        "name": "宁德时代",
                        "source_tab": "scan",
                        "signal_type": "vcp_scan",
                        "observed_at": "2026-04-09",
                        "payload": {
                            "区间最高价": 251.2,
                            "区间最低点": 218.5,
                            "_peak_dates": ["2026-03-20", "2026-04-02"],
                        },
                    }
                ],
            }
        ],
        current_idx=0,
        workspace=SimpleNamespace(get_scan_results=lambda: []),
        source_tab_index=7,
        source_tab_key="stock_candidate",
    )

    assert request["vcp_data"]["区间最高价"] == 251.2
    assert request["vcp_data"]["区间最低点"] == 218.5
    assert request["vcp_data"]["触发日期"] == "2026-04-09"
    assert request["vcp_data"]["_vcp_overlay_allowed"] is True


def test_build_kline_open_request_falls_back_to_name_map_when_row_context_missing():
    request = build_kline_open_request(
        code="000001",
        code_name_map={"000001": "平安银行"},
        code_list=[],
        current_idx=0,
        workspace=SimpleNamespace(get_scan_results=lambda: []),
        source_tab_index=1,
        source_tab_key="watchlist",
    )

    assert request["code"] == "000001"
    assert request["name"] == "平安银行"
    assert request["vcp_data"]["代码"] == "000001"
    assert request["vcp_data"]["名称"] == "平安银行"
    assert request["code_list"] == []
