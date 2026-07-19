# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.services.kline_open_context import KlineOpenContext, compact_kline_navigation
from app.services.stock_context_model_service import StockContextSnapshot
from app.services.stock_context_query_service import StockContextQueryService

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_TRIGGER_DATE = "\u89e6\u53d1\u65e5\u671f"
KEY_REVEAL_DATE = "\u63ed\u6653\u65e5"
KEY_DISCOVERED_AT = "\u53d1\u73b0\u65f6\u95f4"
KEY_EARNINGS_MARK_DATE = "\u4e1a\u7ee9\u65e5"
KEY_EARNINGS_TEXT = "\u4e1a\u7ee9\u5f02\u52a8"
SCAN_CODE_KEY = KEY_CODE
SCAN_SOURCE_KEY = "scan"
EARNINGS_SOURCE_KEY = "earnings"
WATCHLIST_SOURCE_KEY = "watchlist"


def _get_signal_value(signal, key: str, default: object = ""):
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _signal_matches_code(signal, code: str) -> bool:
    signal_code = str(_get_signal_value(signal, "code") or _get_signal_value(signal, KEY_CODE) or "").strip()
    return not signal_code or signal_code == code


def _signal_scan_identity(signal) -> tuple[str, str]:
    source_tab = str(_get_signal_value(signal, "source_tab") or "").strip()
    signal_type = str(_get_signal_value(signal, "signal_type") or "").strip()
    return source_tab, signal_type


def _is_scan_signal(source_tab: str, signal_type: str) -> bool:
    return source_tab == SCAN_SOURCE_KEY or signal_type == "vcp_scan"


def _is_earnings_signal(source_tab: str, signal_type: str) -> bool:
    return source_tab == EARNINGS_SOURCE_KEY or signal_type == EARNINGS_SOURCE_KEY


def _build_scan_signal_payload(signal, code: str, source_tab: str, signal_type: str) -> dict:
    payload = _get_signal_value(signal, "payload", {}) or {}
    scan_payload = dict(payload) if isinstance(payload, dict) else {}
    scan_payload.setdefault(KEY_CODE, code)

    signal_name = str(_get_signal_value(signal, "name") or "").strip()
    if signal_name:
        scan_payload.setdefault(KEY_NAME, signal_name)

    observed_at = str(_get_signal_value(signal, "observed_at") or "").strip()
    if observed_at:
        scan_payload.setdefault(KEY_TRIGGER_DATE, observed_at)

    scan_payload["source_tab"] = source_tab or SCAN_SOURCE_KEY
    scan_payload["signal_type"] = signal_type or "vcp_scan"
    scan_payload["_vcp_overlay_allowed"] = True
    return scan_payload


def _build_earnings_signal_payload(signal, code: str, source_tab: str, signal_type: str) -> dict:
    raw_payload = _get_signal_value(signal, "payload", {}) or {}
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    payload.setdefault(KEY_CODE, code)

    signal_name = str(_get_signal_value(signal, "name") or "").strip()
    if signal_name:
        payload.setdefault(KEY_NAME, signal_name)

    discovered_at = str(
        payload.get(KEY_DISCOVERED_AT)
        or payload.get("discovered_at")
        or _get_signal_value(signal, "observed_at")
        or ""
    ).strip()
    mark_date = str(
        payload.get(KEY_EARNINGS_MARK_DATE)
        or payload.get(KEY_REVEAL_DATE)
        or payload.get("\u516c\u544a\u65e5\u671f")
        or payload.get(KEY_TRIGGER_DATE)
        or payload.get("\u6e90\u516c\u544a\u65e5\u671f")
        or discovered_at
        or ""
    ).strip()
    if mark_date:
        payload[KEY_REVEAL_DATE] = mark_date
        payload[KEY_EARNINGS_MARK_DATE] = mark_date
    if discovered_at:
        payload[KEY_DISCOVERED_AT] = discovered_at

    summary = str(_get_signal_value(signal, "summary") or "").strip()
    if summary:
        payload.setdefault(KEY_EARNINGS_TEXT, summary)

    payload.setdefault("source_tab", source_tab or EARNINGS_SOURCE_KEY)
    payload.setdefault("signal_type", signal_type or EARNINGS_SOURCE_KEY)
    return payload


def _extract_scan_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    for signal in item_data.get("_signals") or []:
        if not _signal_matches_code(signal, code):
            continue

        source_tab, signal_type = _signal_scan_identity(signal)
        if not _is_scan_signal(source_tab, signal_type):
            continue

        return _build_scan_signal_payload(signal, code, source_tab, signal_type)

    return {}


def _date_rank(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    head = text[:10].replace("/", "-").replace(".", "-")
    compact = head.replace("-", "")
    if len(compact) >= 8 and compact[:8].isdigit():
        return compact[:8]
    fallback = text[:8]
    if len(fallback) == 8 and fallback.isdigit():
        return fallback
    return text


def _earnings_signal_rank(signal) -> str:
    payload = _get_signal_value(signal, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    return _date_rank(
        payload.get(KEY_EARNINGS_MARK_DATE)
        or payload.get(KEY_REVEAL_DATE)
        or payload.get("\u516c\u544a\u65e5\u671f")
        or payload.get(KEY_TRIGGER_DATE)
        or payload.get("\u6e90\u516c\u544a\u65e5\u671f")
        or payload.get(KEY_DISCOVERED_AT)
        or payload.get("discovered_at")
        or _get_signal_value(signal, "observed_at")
    )


def _extract_earnings_signal_payload(item_data: dict | None, code: str) -> dict:
    if not isinstance(item_data, dict):
        return {}

    matches = []
    for signal in item_data.get("_signals") or []:
        if not _signal_matches_code(signal, code):
            continue

        source_tab, signal_type = _signal_scan_identity(signal)
        if not _is_earnings_signal(source_tab, signal_type):
            continue
        matches.append(signal)

    if not matches:
        return {}

    signal = max(matches, key=_earnings_signal_rank)
    source_tab, signal_type = _signal_scan_identity(signal)
    return _build_earnings_signal_payload(signal, code, source_tab, signal_type)


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
    source_key = str(source_tab_key or "").strip()
    for item in code_list or []:
        enriched = dict(item) if isinstance(item, dict) else {}
        if source_key == WATCHLIST_SOURCE_KEY:
            enriched["__source_tab_key"] = WATCHLIST_SOURCE_KEY
            if source_tab_index >= 0:
                enriched["__source_tab_index"] = source_tab_index
            else:
                enriched.pop("__source_tab_index", None)
        else:
            if source_tab_index >= 0:
                enriched.setdefault("__source_tab_index", source_tab_index)
            if source_key:
                enriched.setdefault("__source_tab_key", source_key)
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


def _current_vcp_data(
    normalized_code_list: list[dict], current_idx: int, code_text: str, name: str
) -> tuple[dict, str]:
    vcp_data: dict = {
        KEY_CODE: code_text,
        KEY_NAME: name,
    }
    if not normalized_code_list or not (0 <= current_idx < len(normalized_code_list)):
        return vcp_data, name

    item_data = normalized_code_list[current_idx]
    if not isinstance(item_data, dict) or str(item_data.get(KEY_CODE, "")).strip() != code_text:
        return vcp_data, name

    vcp_data = dict(item_data)
    name = str(item_data.get(KEY_NAME, name) or name).strip() or name
    vcp_data.setdefault(KEY_CODE, code_text)
    vcp_data.setdefault(KEY_NAME, name)
    return vcp_data, name


def _workspace_scan_results(workspace) -> list[dict]:
    get_scan_results = getattr(workspace, "get_scan_results", None)
    if callable(get_scan_results):
        scan_results = get_scan_results() or []
        return list(scan_results) if isinstance(scan_results, (list, tuple)) else []
    return []


def _workspace_context_snapshot(workspace) -> StockContextSnapshot | None:
    explicit_reader = getattr(workspace, "capture_stock_context_snapshot", None)
    if callable(explicit_reader):
        snapshot = explicit_reader()
        return snapshot if isinstance(snapshot, StockContextSnapshot) else None
    context_reader = getattr(workspace, "collect_stock_context", None)
    if not callable(context_reader):
        return None
    try:
        snapshot = context_reader(capture_snapshot=True)
    except TypeError:
        return None
    return snapshot if isinstance(snapshot, StockContextSnapshot) else None


def _snapshot_stock_signals(workspace, code: str) -> list | None:
    try:
        snapshot = _workspace_context_snapshot(workspace)
        return StockContextQueryService(snapshot).query_kline_signals(code) if snapshot is not None else None
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _published_stock_signals(workspace, code_text: str) -> tuple[bool, list]:
    published_reader = getattr(workspace, "get_published_stock_context_signals", None)
    if not callable(published_reader):
        return False, []
    try:
        published = published_reader(code_text)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return True, []
    if published is None:
        return True, []
    return True, list(published) if isinstance(published, (list, tuple)) else []


def _legacy_workspace_stock_signals(workspace, code_text: str) -> list:
    snapshot_signals = _snapshot_stock_signals(workspace, code_text)
    if snapshot_signals is not None:
        return snapshot_signals

    collect_stock_context = getattr(workspace, "collect_stock_context", None)
    if not callable(collect_stock_context):
        return []

    try:
        stock_context = collect_stock_context() or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return []

    if not isinstance(stock_context, dict):
        return []

    signals = stock_context.get(code_text) or []
    if not isinstance(signals, (list, tuple)):
        signals = [signals]
    return list(signals)


def _workspace_stock_signals(workspace, code: str) -> list:
    code_text = str(code or "").strip()
    if not code_text:
        return []
    published, signals = _published_stock_signals(workspace, code_text)
    return signals if published else _legacy_workspace_stock_signals(workspace, code_text)


def _extract_workspace_scan_signal_payload(workspace, code: str, signals=None) -> dict:
    code_text = str(code or "").strip()
    workspace_signals = _workspace_stock_signals(workspace, code_text) if signals is None else signals
    return _extract_scan_signal_payload({"_signals": workspace_signals}, code_text)


def _extract_workspace_earnings_signal_payload(workspace, code: str, signals=None) -> dict:
    code_text = str(code or "").strip()
    workspace_signals = _workspace_stock_signals(workspace, code_text) if signals is None else signals
    return _extract_earnings_signal_payload({"_signals": workspace_signals}, code_text)


def _merge_scan_context(
    *,
    vcp_data: dict,
    code_text: str,
    workspace,
    source_tab_key: str,
    scan_results: list[dict],
    workspace_signals=None,
) -> None:
    embedded_scan = _extract_scan_signal_payload(vcp_data, code_text)
    if embedded_scan:
        _merge_missing(vcp_data, embedded_scan)
        return

    context_scan = _extract_workspace_scan_signal_payload(workspace, code_text, workspace_signals)
    if context_scan:
        _merge_missing(vcp_data, context_scan)
        return

    if not _source_allows_workspace_scan_merge(vcp_data, source_tab_key):
        return

    scan_result = _find_scan_result(scan_results, code_text)
    if isinstance(scan_result, dict):
        _merge_missing(vcp_data, scan_result)
        vcp_data["_vcp_overlay_allowed"] = True


def merge_workspace_earnings_context(*, vcp_data: dict, code_text: str, workspace, workspace_signals=None) -> None:
    earnings_payload = _extract_workspace_earnings_signal_payload(workspace, code_text, workspace_signals)
    if earnings_payload:
        _merge_missing(vcp_data, earnings_payload)


def _scan_rows_needed(
    *,
    vcp_data: dict,
    code_text: str,
    source_tab_key: str,
    workspace,
    workspace_signals,
) -> list[dict]:
    if _extract_scan_signal_payload(vcp_data, code_text):
        return []
    if _extract_workspace_scan_signal_payload(workspace, code_text, workspace_signals):
        return []
    if not _source_allows_workspace_scan_merge(vcp_data, source_tab_key):
        return []
    return _workspace_scan_results(workspace)


def merge_workspace_kline_context(
    *,
    vcp_data: dict,
    code_text: str,
    workspace,
    source_tab_key: str = "",
) -> None:
    """Merge one code from the published index; never capture widgets on production click paths."""

    workspace_signals = _workspace_stock_signals(workspace, code_text)
    _merge_scan_context(
        vcp_data=vcp_data,
        code_text=code_text,
        workspace=workspace,
        source_tab_key=source_tab_key,
        scan_results=_scan_rows_needed(
            vcp_data=vcp_data,
            code_text=code_text,
            source_tab_key=source_tab_key,
            workspace=workspace,
            workspace_signals=workspace_signals,
        ),
        workspace_signals=workspace_signals,
    )
    merge_workspace_earnings_context(
        vcp_data=vcp_data,
        code_text=code_text,
        workspace=workspace,
        workspace_signals=workspace_signals,
    )


def _current_context_data(
    code_list: Sequence[Mapping] | None,
    current_idx: int,
    code_text: str,
    name: str,
    *,
    source_tab_index: int,
    source_tab_key: str,
) -> tuple[dict, str]:
    if not code_list or not (0 <= current_idx < len(code_list)):
        return {KEY_CODE: code_text, KEY_NAME: name}, name
    current = code_list[current_idx]
    if not isinstance(current, Mapping):
        return {KEY_CODE: code_text, KEY_NAME: name}, name
    normalized = _normalize_code_list(
        [current],
        source_tab_index=source_tab_index,
        source_tab_key=source_tab_key,
    )[0]
    return _current_vcp_data([normalized], 0, code_text, name)


def _resolved_open_context(
    *,
    code: str,
    name: str,
    vcp_data: dict,
    navigation,
    current_idx: int,
    source_tab_key: str,
    source_tab_index: int,
) -> KlineOpenContext:
    current_item = navigation[current_idx] if 0 <= current_idx < len(navigation) else None
    return KlineOpenContext(
        code=code,
        name=name,
        vcp_data=vcp_data,
        navigation=navigation,
        current_idx=current_idx,
        source_tab_key=current_item.source_tab_key if current_item is not None else source_tab_key,
        source_tab_index=current_item.source_tab_index if current_item is not None else source_tab_index,
    )


def build_kline_open_context(
    *,
    code: str,
    code_name_map: dict[str, str] | None,
    code_list: Sequence[Mapping] | None,
    current_idx: int,
    workspace=None,
    source_tab_index: int = -1,
    source_tab_key: str = "",
) -> KlineOpenContext:
    """Resolve the current stock once while retaining only compact navigation rows."""
    code_text = str(code or "").strip()
    name = str((code_name_map or {}).get(code_text, code_text)).strip() or code_text
    vcp_data, name = _current_context_data(
        code_list,
        current_idx,
        code_text,
        name,
        source_tab_index=source_tab_index,
        source_tab_key=source_tab_key,
    )
    merge_workspace_kline_context(
        vcp_data=vcp_data,
        code_text=code_text,
        workspace=workspace,
        source_tab_key=source_tab_key,
    )
    navigation = compact_kline_navigation(
        code_list,
        source_tab_key=source_tab_key,
        source_tab_index=source_tab_index,
    )
    return _resolved_open_context(
        code=code_text,
        name=name,
        vcp_data=vcp_data,
        navigation=navigation,
        current_idx=current_idx,
        source_tab_key=source_tab_key,
        source_tab_index=source_tab_index,
    )


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
    context = build_kline_open_context(
        code=code,
        code_name_map=code_name_map,
        code_list=code_list,
        current_idx=current_idx,
        workspace=workspace,
        source_tab_index=source_tab_index,
        source_tab_key=source_tab_key,
    )
    normalized_code_list = _normalize_code_list(
        code_list,
        source_tab_index=source_tab_index,
        source_tab_key=source_tab_key,
    )
    return {
        "code": context.code,
        "name": context.name,
        "vcp_data": context.mutable_vcp_data(),
        "code_list": normalized_code_list,
        "current_idx": context.current_idx,
    }


__all__ = [
    "build_kline_open_context",
    "build_kline_open_request",
    "merge_workspace_earnings_context",
    "merge_workspace_kline_context",
]
