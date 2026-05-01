# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.kline_open_service import build_kline_open_request


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


def test_build_kline_open_request_adds_scan_context_for_watchlist_without_overwriting():
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
    assert request["vcp_data"]["区间最高价"] == 91.0
    assert request["vcp_data"]["区间最低点"] == 65.6
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
