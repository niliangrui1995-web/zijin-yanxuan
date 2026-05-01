# -*- coding: utf-8 -*-
from __future__ import annotations

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
SCAN_CODE_KEY = KEY_CODE
SCAN_SOURCE_KEY = "scan"


def _get_signal_value(signal, key: str, default=""):
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _extract_scan_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    for signal in item_data.get("_signals") or []:
        signal_code = str(_get_signal_value(signal, "code") or _get_signal_value(signal, "代码") or "").strip()
        if signal_code and signal_code != code:
            continue

        source_tab = str(_get_signal_value(signal, "source_tab") or "").strip()
        signal_type = str(_get_signal_value(signal, "signal_type") or "").strip()
        if source_tab != SCAN_SOURCE_KEY and signal_type != "vcp_scan":
            continue

        payload = _get_signal_value(signal, "payload", {}) or {}
        scan_payload = dict(payload) if isinstance(payload, dict) else {}
        scan_payload.setdefault(KEY_CODE, code)
        signal_name = str(_get_signal_value(signal, "name") or "").strip()
        if signal_name:
            scan_payload.setdefault(KEY_NAME, signal_name)
        observed_at = str(_get_signal_value(signal, "observed_at") or "").strip()
        if observed_at:
            scan_payload.setdefault("触发日期", observed_at)
        scan_payload["source_tab"] = source_tab or SCAN_SOURCE_KEY
        scan_payload["signal_type"] = signal_type or "vcp_scan"
        scan_payload["_vcp_overlay_allowed"] = True
        return scan_payload

    return {}


def _source_allows_workspace_scan_merge(vcp_data: dict, source_tab_key: str) -> bool:
    source_key = str(vcp_data.get("__source_tab_key") or source_tab_key or "").strip()
    source_tab = str(vcp_data.get("source_tab") or "").strip()
    signal_type = str(vcp_data.get("signal_type") or "").strip()
    return source_key == SCAN_SOURCE_KEY or source_tab == SCAN_SOURCE_KEY or signal_type == "vcp_scan"


def _merge_missing(base: dict, extra: dict) -> None:
    for key, value in (extra or {}).items():
        if value in (None, "", [], {}):
            continue
        if key not in base or not base.get(key):
            base[key] = value


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


def _extract_workspace_scan_signal_payload(workspace, code: str) -> dict:
    code_text = str(code or "").strip()
    if not code_text:
        return {}

    collect_stock_context = getattr(workspace, "collect_stock_context", None)
    if not callable(collect_stock_context):
        return {}

    try:
        stock_context = collect_stock_context() or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}

    if not isinstance(stock_context, dict):
        return {}

    signals = stock_context.get(code_text) or []
    if not isinstance(signals, (list, tuple)):
        signals = [signals]
    return _extract_scan_signal_payload({"_signals": list(signals)}, code_text)


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

    embedded_scan = _extract_scan_signal_payload(vcp_data, code_text)
    context_scan = {}
    if not embedded_scan:
        context_scan = _extract_workspace_scan_signal_payload(workspace, code_text)
    if embedded_scan:
        _merge_missing(vcp_data, embedded_scan)
    elif context_scan:
        _merge_missing(vcp_data, context_scan)
    elif _source_allows_workspace_scan_merge(vcp_data, source_tab_key):
        scan_result = _find_scan_result(scan_results, code_text)
        if isinstance(scan_result, dict):
            _merge_missing(vcp_data, scan_result)
            vcp_data["_vcp_overlay_allowed"] = True

    return {
        "code": code_text,
        "name": name,
        "vcp_data": vcp_data,
        "code_list": normalized_code_list,
        "current_idx": current_idx,
    }
