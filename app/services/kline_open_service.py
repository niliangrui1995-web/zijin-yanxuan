# -*- coding: utf-8 -*-
from __future__ import annotations

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
SCAN_CODE_KEY = KEY_CODE


def _normalize_code_list(
    code_list: list | None,
    *,
    source_tab_index: int = -1,
    source_tab_key: str = "",
) -> list[dict]:
    normalized: list[dict] = []
    for item in code_list or []:
        enriched = dict(item) if isinstance(item, dict) else {}
        if source_tab_index >= 0:
            enriched.setdefault("__source_tab_index", source_tab_index)
        if source_tab_key:
            enriched.setdefault("__source_tab_key", source_tab_key)
        normalized.append(enriched)
    return normalized


def _find_scan_result(scan_results: list[dict], code: str) -> dict | None:
    code_text = str(code or "").strip()
    if not code_text:
        return None
    for row in scan_results or []:
        if not isinstance(row, dict):
            continue
        if str(row.get(SCAN_CODE_KEY, "")).strip() == code_text:
            return row
    return None


def build_kline_open_request(
    *,
    code: str,
    code_name_map: dict[str, str] | None,
    code_list: list | None,
    current_idx: int,
    workspace=None,
    source_tab_index: int = -1,
    source_tab_key: str = "",
) -> dict:
    code_text = str(code or "").strip()
    name = str((code_name_map or {}).get(code_text, code_text)).strip() or code_text
    normalized_code_list = _normalize_code_list(
        code_list,
        source_tab_index=source_tab_index,
        source_tab_key=source_tab_key,
    )

    vcp_data: dict = {
        KEY_CODE: code_text,
        KEY_NAME: name,
    }
    if normalized_code_list and 0 <= current_idx < len(normalized_code_list):
        item_data = normalized_code_list[current_idx]
        if isinstance(item_data, dict) and str(item_data.get(KEY_CODE, "")).strip() == code_text:
            vcp_data = dict(item_data)
            name = str(item_data.get(KEY_NAME, name) or name).strip() or name
            vcp_data.setdefault(KEY_CODE, code_text)
            vcp_data.setdefault(KEY_NAME, name)

    scan_results = []
    get_scan_results = getattr(workspace, "get_scan_results", None)
    if callable(get_scan_results):
        scan_results = list(get_scan_results() or [])

    scan_result = _find_scan_result(scan_results, code_text)
    if isinstance(scan_result, dict):
        for key, value in scan_result.items():
            if key not in vcp_data or not vcp_data.get(key):
                vcp_data[key] = value

    return {
        "code": code_text,
        "name": name,
        "vcp_data": vcp_data,
        "code_list": normalized_code_list,
        "current_idx": current_idx,
    }
