# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.kline_open_service import build_kline_open_request


def test_build_kline_open_request_merges_scan_context_into_current_row():
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
    assert request["vcp_data"]["突破价"] == 251.2
    assert request["vcp_data"]["箱体上沿"] == 248.5


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
