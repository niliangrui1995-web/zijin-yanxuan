# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from app.services import kline_open_service as service_module
from app.services.kline_open_service import build_kline_open_request

KEY_CODE = service_module.KEY_CODE
KEY_NAME = service_module.KEY_NAME


def test_kline_open_service_ignores_invalid_or_unrelated_scan_signals():
    assert service_module._extract_scan_signal_payload(None, "000001") == {}
    assert service_module._extract_scan_signal_payload(
        {"_signals": [{"code": "999999", "source_tab": "scan", "signal_type": "vcp_scan"}]},
        "000001",
    ) == {}
    assert service_module._extract_scan_signal_payload(
        {"_signals": [{"code": "000001", "source_tab": "watchlist", "signal_type": "manual"}]},
        "000001",
    ) == {}

    base = {"kept": "old"}
    service_module._merge_missing(base, {"empty": "", "list": [], "none": None, "kept": "new", "fresh": "ok"})

    assert base == {"kept": "old", "fresh": "ok"}


def test_kline_open_service_covers_missing_workspace_and_scan_result_branches():
    assert service_module._find_scan_result([], "") is None
    assert service_module._find_scan_result([None, {KEY_CODE: "000002"}], "000001") is None
    assert service_module._workspace_scan_results(SimpleNamespace()) == []
    assert service_module._extract_workspace_scan_signal_payload(SimpleNamespace(), "") == {}

    assert (
        service_module._extract_workspace_scan_signal_payload(
            SimpleNamespace(collect_stock_context=lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
            "000001",
        )
        == {}
    )
    assert (
        service_module._extract_workspace_scan_signal_payload(
            SimpleNamespace(collect_stock_context=lambda: ["bad"]),
            "000001",
        )
        == {}
    )


def test_kline_open_request_normalizes_watchlist_and_handles_mismatched_current_row():
    request = build_kline_open_request(
        code="000001",
        code_name_map={"000001": "PingAn"},
        code_list=[
            {
                KEY_CODE: "000002",
                KEY_NAME: "Other",
                "__source_tab_index": 7,
            }
        ],
        current_idx=0,
        workspace=SimpleNamespace(),
        source_tab_index=-1,
        source_tab_key="watchlist",
    )

    assert request["name"] == "PingAn"
    assert request["vcp_data"] == {KEY_CODE: "000001", KEY_NAME: "PingAn"}
    assert request["code_list"][0]["__source_tab_key"] == "watchlist"
    assert "__source_tab_index" not in request["code_list"][0]


def test_kline_open_service_wraps_single_workspace_signal():
    signal = {
        "code": "000001",
        "source_tab": "scan",
        "signal_type": "vcp_scan",
        "payload": {"fresh": "scan"},
    }
    payload = service_module._extract_workspace_scan_signal_payload(
        SimpleNamespace(collect_stock_context=lambda: {"000001": signal}),
        "000001",
    )

    assert payload["fresh"] == "scan"
    assert payload["_vcp_overlay_allowed"] is True
