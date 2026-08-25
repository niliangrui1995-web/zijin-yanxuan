# -*- coding: utf-8 -*-
# ui/tabs/watchlist_tab.py
# 关注池独立组件 — 从 WatchlistMixin 解耦重构为完全自治的 QWidget
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial

_WATCHLIST_MODULE_IMPORT_STARTED_AT = time.perf_counter()
_WATCHLIST_MODULE_IMPORT_MS = 0.0

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.ui_diagnostics_service import ui_stall_span
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_quote_service import get_total_shares, resolve_quote_metrics
from app.services.ui_task_lifecycle_service import invoke_with_cancellation, task_lifecycle_for
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from app.services.watchlist_indicator_service import (
    build_watchlist_indicator_results,
    build_watchlist_metric_patch,
    persist_watchlist_metrics,
)
from core.buy_point import BUY_POINT_TEXT
from core.logger import get_logger
from core.observability import record_metric
from core.state.quote_snapshot import snapshot_to_mutable_dict
from ui.components.table_controls import VCPTableView
from ui.components.toast_widget import show_toast
from ui.models.stock_table_model import BUY_POINT_TRIGGER_ICON
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab
from ui.tabs.watchlist_table_state import LazyWatchlistTableStateWrapper as TableStateWrapper
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks
from ui.workspaces.tab_registry import create_tab_lineage_service

log = get_logger(__name__)


class _LazyWatchlistViewModelProxy:
    """Keep the SQLite-backed watchlist VM out of the tab's import path."""

    @staticmethod
    def _resolve():
        from app.services.ui_watchlist_service import watchlist_vm as resolved_watchlist_vm

        return resolved_watchlist_vm

    def __getattr__(self, name):
        return getattr(self._resolve(), name)

    def __setattr__(self, name, value):
        setattr(self._resolve(), name, value)

    def __delattr__(self, name):
        delattr(self._resolve(), name)


watchlist_vm = _LazyWatchlistViewModelProxy()


def load_active_rps_payload():
    from app.services.f5_snapshot_service import load_active_rps_payload as load_payload

    return load_payload()


def capture_workspace_stock_context(
    workspace,
    *,
    include_rps_bundle: bool = True,
    sources=None,
    target_codes=None,
):
    from ui.workspaces.stock_context_widget_adapter import capture_workspace_stock_context as capture_context

    options = {}
    if not include_rps_bundle:
        options["include_rps_bundle"] = False
    if sources is not None:
        options["sources"] = sources
    if target_codes is not None:
        options["target_codes"] = target_codes
    return capture_context(workspace, **options)


def _task_cancelled(cancellation_token) -> bool:
    return cancellation_token is not None and cancellation_token.cancelled


def _active_items(values, cancellation_token=None):
    for value in values:
        if _task_cancelled(cancellation_token):
            return
        yield value


def _resolve_a_share_name_in_background(provider, code: str, cancellation_token=None) -> dict:
    ensure_name_map = getattr(provider, "ensure_code_name_map", None)
    if not callable(ensure_name_map):
        return {}
    return invoke_with_cancellation(
        ensure_name_map,
        cancellation_token,
        [code],
        refresh_missing=True,
    ) or {}


def _format_watchlist_note(earnings: object = "", block_trade: object = "", lhb: object = "") -> str:
    parts = []
    for raw_value in (earnings, block_trade):
        text = str(raw_value or "").strip()
        if text and text != "--":
            parts.append(text)

    lhb_text = str(lhb or "").strip()
    if lhb_text and lhb_text != "--":
        parts.append(BUY_POINT_TRIGGER_ICON if lhb_text == BUY_POINT_TEXT else lhb_text)

    return " / ".join(parts)


def _watchlist_identity_fields(code, info_new, info_old, code_name_map):
    source_context = {**info_old, **info_new}
    source_context.pop("催化剂", None)
    source_context.pop("美股日报", None)
    source_tags = watchlist_vm.derive_source_tags(
        source_context,
        existing_tags=source_context.get("来源标签"),
    )
    name = info_new.get("名称") or info_old.get("名称")
    if not name or name == str(code):
        name = code_name_map.get(code, code)
    return {
        "代码": code,
        "名称": name,
        "来源": watchlist_vm.format_source_tags(source_tags),
        "来源标签": source_tags,
    }


def _prefer_live_value(live_entry, info_new, key):
    live_value = live_entry.get(key, "--")
    return live_value if live_value != "--" else info_new.get(key, "--")


def _watchlist_display_fields(info_new, info_old, live_entry):
    cap = _prefer_live_value(live_entry, info_new, "市值")
    subsector = info_new.get("细分板块") or info_old.get("细分板块") or info_new.get("subsector", "")
    total_shares = get_total_shares(live_entry)
    return {
        "现价": str(_prefer_live_value(live_entry, info_new, "现价")),
        "涨幅%": str(_prefer_live_value(live_entry, info_new, "涨幅%")),
        "市值": str(cap if cap and cap != "--" else ""),
        "RPS强度": str(info_new.get("RPS强度", "--")),
        "细分板块": str(subsector or ""),
        "摘要": str(info_new.get("备注") or info_old.get("备注", "") or ""),
        "total_shares": total_shares,
        "_zongguben": total_shares,
    }


def _watchlist_detail_fields(info_new, info_old):
    block_trade = info_new.get("大宗交易", "")
    earnings = info_new.get("业绩异动", "")
    lhb = info_new.get("龙虎榜", "")
    return {
        "备注": _format_watchlist_note(earnings, block_trade, lhb),
        "大宗交易": block_trade,
        "大宗交易金额(万)": info_new.get("大宗交易金额(万)", info_old.get("大宗交易金额(万)", "")),
        "业绩异动": earnings,
        "业绩环比%": info_new.get("业绩环比%", info_old.get("业绩环比%", "")),
        "龙虎榜": lhb,
        "龙虎榜日期": info_new.get("龙虎榜日期", ""),
        "龙虎榜净额(万)": info_new.get("龙虎榜净额(万)", info_old.get("龙虎榜净额(万)", "")),
    }


def _shape_watchlist_rows(all_codes, data_dict, old_pool, code_name_map, live_data_map, cancellation_token=None):
    rows = []
    for code in _active_items(all_codes, cancellation_token):
        info_new = data_dict.get(code, {})
        info_old = old_pool.get(code, {})
        row = _watchlist_identity_fields(code, info_new, info_old, code_name_map)
        row.update(_watchlist_display_fields(info_new, info_old, live_data_map.get(code, {})))
        row.update(_watchlist_detail_fields(info_new, info_old))
        rows.append(row)
    return rows


def _merge_watchlist_quote_row(row: dict, quote: Mapping) -> None:
    metrics = resolve_quote_metrics(row, quote)
    updates = {
        "现价": metrics.get("price_text"),
        "涨幅%": metrics.get("pct"),
        "市值": metrics.get("market_cap_text"),
    }
    row.update({key: value for key, value in updates.items() if value is not None and value != ""})
    total_shares = float(
        metrics.get("total_shares")
        or metrics.get("zongguben")
        or metrics.get("_zongguben")
        or 0
    )
    if total_shares > 0:
        row["total_shares"] = total_shares
        row["_zongguben"] = total_shares


def _merge_watchlist_quote_snapshot(rows, quote_snapshot) -> list:
    merged_rows = list(rows or [])
    snapshot = dict(quote_snapshot or {})
    for row in merged_rows:
        quote = snapshot.get(str(row.get("代码", "") or "").strip())
        if isinstance(quote, Mapping):
            _merge_watchlist_quote_row(row, quote)
    return merged_rows


def _copy_quote_snapshot(snapshot, codes=None) -> dict:
    return snapshot_to_mutable_dict(snapshot, codes)


def _capture_latest_quote_snapshot(codes=None) -> dict:
    try:
        from core.global_store import global_store

        snapshot = global_store.get_latest_quotes() or {}
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return {}
    return _copy_quote_snapshot(snapshot, codes)


def _load_watchlist_initial_payload(code_name_map, live_data_map, cancellation_token):
    started_at = time.perf_counter()
    data_dict = watchlist_vm.get_watchlist_data()
    rows = _shape_watchlist_rows(
        tuple(data_dict),
        data_dict,
        {},
        code_name_map,
        live_data_map,
        cancellation_token,
    )
    return {
        "rows": rows,
        "elapsed_ms": (time.perf_counter() - started_at) * 1000.0,
        "row_count": len(rows),
    }


def _build_watchlist_initial_job(code_name_map, live_data_map):
    return partial(
        _load_watchlist_initial_payload,
        dict(code_name_map),
        {code: dict(values) for code, values in live_data_map.items()},
    )


def _capture_watchlist_live_data(model) -> dict:
    live_data_map = {}
    for row in list(getattr(model, "row_data", None) or []):
        code = row.get("代码")
        if code:
            total_shares = get_total_shares(row)
            live_data_map[code] = {
                "现价": row.get("现价", "--"),
                "涨幅%": row.get("涨幅%", "--"),
                "市值": row.get("市值", "--"),
                "total_shares": total_shares,
                "_zongguben": total_shares,
            }
    return live_data_map


def _watchlist_rows_light_signature(rows) -> tuple:
    return tuple(
        tuple(sorted((str(key), str(value)) for key, value in dict(row).items()))
        for row in rows
        if isinstance(row, dict)
    )


def _finalize_watchlist_rows_lineage(owner, rows, generation: int | None = None) -> None:
    try:
        closing = bool(owner._closing)
    except RuntimeError:
        return
    if closing or (
        generation is not None
        and generation != getattr(owner, "_watchlist_lineage_generation", 0)
    ):
        return
    result = owner._describe_watchlist_rows(list(rows or []))
    owner._last_watchlist_result = result
    owner._last_watchlist_signature = result.signature
    owner._update_status_summary()


def _can_refresh_watchlist_live(owner) -> bool:
    return not bool(getattr(owner, "_workspace_noninteractive_loaded", False)) and owner._can_fetch_live_quotes_now()


def _apply_watchlist_rows(
    owner,
    rows,
    *,
    refresh_quote_store: bool = True,
    describe_rows: bool = True,
    update_status: bool = True,
    update_source: str = "watchlist_rows",
) -> None:
    row_payload = list(rows or [])
    with ui_stall_span(
        "WatchlistTab._apply_watchlist_rows",
        tab="watchlist",
        signal=str(len(row_payload)),
    ):
        owner._touch_watchlist_update()
        if describe_rows:
            with ui_stall_span(
                "WatchlistTab._describe_watchlist_rows",
                tab="watchlist",
                signal=str(len(row_payload)),
            ):
                result = owner._describe_watchlist_rows(row_payload)
            signature = result.signature
            owner._last_watchlist_result = result
        else:
            signature = _watchlist_rows_light_signature(row_payload)
            owner._last_watchlist_result = None
        rows_changed = signature != owner._last_watchlist_signature
        if rows_changed:
            with ui_stall_span(
                "WatchlistTab.model.update_data",
                tab="watchlist",
                signal=str(len(row_payload)),
            ):
                owner.model.update_data(
                    row_payload,
                    hydrate_latest_quotes=False,
                    allow_single_row_membership_delta=True,
                    membership_reconcile_source=update_source,
                )
            owner._last_watchlist_signature = signature
            quote_task_id = task_registry.quote_refresh("watchlist").task_id
            if refresh_quote_store:
                with ui_stall_span(
                    "WatchlistTab._refresh_quotes_from_store_or_live",
                    tab="watchlist",
                    signal=str(len(row_payload)),
                ):
                    owner._refresh_quotes_from_store_or_live(quote_task_id=quote_task_id)
            elif _can_refresh_watchlist_live(owner):
                owner._refresh_quotes_async_local(quote_task_id=quote_task_id)
        if update_status:
            owner._update_status_summary()


def _apply_special_data_payload(owner, payload, *, refresh_indicators, indicator_delay_ms) -> None:
    if owner._closing:
        return
    data = payload if isinstance(payload, dict) else {}
    rows = list(data.get("rows") or [])
    codes = [row.get("代码") for row in rows if row.get("代码")]
    rows = _merge_watchlist_quote_snapshot(rows, _capture_latest_quote_snapshot(codes))
    record_metric(
        "watchlist_tab_initial_data_ms",
        float(data.get("elapsed_ms") or 0.0),
        unit="ms",
        tags={"rows": str(data.get("row_count", len(rows)))},
    )
    _apply_watchlist_rows(
        owner,
        rows,
        refresh_quote_store=False,
        describe_rows=False,
        update_status=False,
        update_source="initial_data",
    )
    owner._finish_initial_data_loading()
    owner._watchlist_initial_data_finished = True
    if refresh_indicators and indicator_delay_ms is None:
        owner._request_vcp_calc()
    elif refresh_indicators:
        owner._request_vcp_calc(delay_ms=indicator_delay_ms)
    owner._resume_background_preload_after_rows()
    owner._watchlist_lineage_generation = int(
        getattr(owner, "_watchlist_lineage_generation", 0)
    ) + 1
    lineage_generation = owner._watchlist_lineage_generation
    QTimer.singleShot(
        250,
        lambda: _finalize_watchlist_rows_lineage(owner, rows, lineage_generation),
    )


def _on_special_data_error(owner, error_message) -> None:
    if owner._closing:
        return
    owner._finish_initial_data_loading()
    owner._watchlist_initial_data_finished = True
    log.error(f"[watchlist] initial data load failed: {error_message}")
    if not getattr(owner.model, "row_data", None) and hasattr(owner, "table_state"):
        owner.table_state.show_error("关注池加载失败", str(error_message or "请稍后重试"))
    owner._resume_background_preload_after_rows()


def _schedule_initial_loading_overlay(owner) -> None:
    owner._initial_data_loading = True
    if owner._initial_loading_timer is None:
        owner._initial_loading_timer = QTimer(owner)
        owner._initial_loading_timer.setSingleShot(True)
        owner._initial_loading_timer.timeout.connect(owner._show_initial_loading_if_pending)
    owner._initial_loading_timer.start(owner.INITIAL_LOADING_REVEAL_DELAY_MS)


def _show_initial_loading_if_pending(owner) -> None:
    if owner._closing or not owner._initial_data_loading:
        return
    if getattr(owner.model, "row_data", None) or not hasattr(owner, "table_state"):
        return
    owner.table_state.show_loading("正在加载关注池...", "先显示页面，数据将在后台回填")


def _finish_initial_data_loading(owner) -> None:
    owner._initial_data_loading = False
    if owner._initial_loading_timer is not None:
        owner._initial_loading_timer.stop()


def _run_pending_async_local_quote_refresh(owner) -> None:
    quote_task_id = owner._pending_quote_task_id
    owner._pending_quote_task_id = None
    if not owner._closing and quote_task_id:
        owner._run_async_local_quote_refresh(quote_task_id)


def _proxy_has_active_sort_or_filter(proxy) -> bool:
    if proxy is None or not proxy.dynamicSortFilter():
        return False
    sort_column = getattr(proxy, "sortColumn", None)
    has_filter_state = hasattr(proxy, "_filter_text") or hasattr(proxy, "_exact_column_filters")
    if not callable(sort_column) and not has_filter_state:
        return True
    active_sort = callable(sort_column) and sort_column() >= 0
    active_filter = bool(getattr(proxy, "_filter_text", "") or getattr(proxy, "_exact_column_filters", {}))
    return active_sort or active_filter


def _proxy_filter_depends_on_changed_headers(proxy, changed_headers) -> bool:
    if proxy is None:
        return False

    global_filter_active = bool(getattr(proxy, "_filter_text", ""))
    exact_filters = getattr(proxy, "_exact_column_filters", {})
    exact_filter_active = bool(exact_filters)
    if changed_headers is None:
        return global_filter_active or exact_filter_active

    normalized_headers = {
        str(header or "").strip()
        for header in changed_headers
        if str(header or "").strip()
    }
    if not normalized_headers:
        return False

    # RtSortFilterProxyModel's global search can inspect every visible column.
    if global_filter_active:
        return True
    if isinstance(exact_filters, dict):
        return bool(normalized_headers.intersection(str(header) for header in exact_filters))
    return exact_filter_active


def _proxy_update_requires_coalescing(proxy, changed_headers) -> bool:
    if not _proxy_has_active_sort_or_filter(proxy):
        return False
    # Keep dynamic filtering enabled when an updated field can change row
    # membership. Re-enabling dynamicSortFilter does not invalidate a filter.
    if _proxy_filter_depends_on_changed_headers(proxy, changed_headers):
        return False
    if changed_headers is None:
        return True

    normalized_headers = {
        str(header or "").strip()
        for header in changed_headers
        if str(header or "").strip()
    }
    if not normalized_headers:
        return False

    sort_column = getattr(proxy, "sortColumn", None)
    if not callable(sort_column):
        return True
    try:
        column = int(sort_column())
    except (RuntimeError, TypeError, ValueError):
        return True
    if column < 0:
        return False

    source_model = getattr(proxy, "sourceModel", None)
    if not callable(source_model):
        return True
    try:
        source = source_model()
        sort_header = source.headerData(
            column,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.DisplayRole,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return True
    return str(sort_header or "").strip() in normalized_headers


def _run_coalesced_model_update(owner, callback, *, changed_headers=None, reason: str = "model_update"):
    proxy = getattr(owner, "proxy_model", None)
    normalized_headers = (
        None
        if changed_headers is None
        else {
            str(header or "").strip()
            for header in changed_headers
            if str(header or "").strip()
        }
    )
    coalesced = _proxy_update_requires_coalescing(proxy, normalized_headers)
    started_at = time.perf_counter()
    if coalesced:
        proxy.setDynamicSortFilter(False)
    try:
        return callback()
    finally:
        if coalesced:
            proxy.setDynamicSortFilter(True)
        record_metric(
            "watchlist_model_update_ms",
            (time.perf_counter() - started_at) * 1000.0,
            unit="ms",
            tags={
                "changed_headers": "*" if normalized_headers is None else ",".join(sorted(normalized_headers)),
                "mode": "coalesced" if coalesced else "direct",
                "reason": str(reason or "model_update"),
            },
        )


def _run_vcp_refresh(codes_with_rows, context_snapshot, fallback_radar_data, cancellation_token):
    radar_data = fallback_radar_data
    if context_snapshot is not None:
        from app.services.stock_context_query_service import StockContextQueryService

        radar_data = StockContextQueryService(context_snapshot).query_watchlist_radar(
            target_codes=[code for _, code in codes_with_rows],
            include_source_cache_fallback=True,
            allow_lhb_cache_compute=False,
        )
    return build_watchlist_indicator_results(
        codes_with_rows,
        radar_data=radar_data,
        rps_loader=load_active_rps_payload,
        cancellation_token=cancellation_token,
    )


def _build_vcp_refresh_job(owner, codes_with_rows):
    from app.services.stock_context_query_service import RADAR_SOURCE_KEYS

    window_reader = getattr(owner, "window", None)
    window = window_reader() if callable(window_reader) else None
    target_codes = tuple(
        dict.fromkeys(
            str(code or "").strip()
            for _, code in codes_with_rows
            if str(code or "").strip()
        )
    )
    capture_started_at = time.perf_counter()
    with ui_stall_span(
        "WatchlistTab._capture_stock_context_snapshot",
        tab="watchlist",
        signal=str(len(target_codes)),
    ):
        context_snapshot = capture_workspace_stock_context(
            getattr(window, "_workspace", None),
            include_rps_bundle=False,
            sources=RADAR_SOURCE_KEYS,
            target_codes=target_codes,
        )
    capture_elapsed_ms = (time.perf_counter() - capture_started_at) * 1000.0
    captured_source_rows = (
        sum(len(rows) for rows in context_snapshot.source_rows.values())
        if context_snapshot is not None
        else 0
    )
    candidate_source_rows = (
        sum(context_snapshot.source_row_counts.values())
        if context_snapshot is not None
        else 0
    )
    cached_rows = (
        sum(len(rows) for rows in context_snapshot.cached_source_rows.values())
        if context_snapshot is not None
        else 0
    )
    record_metric(
        "watchlist_context_snapshot_capture_ms",
        capture_elapsed_ms,
        unit="ms",
        tags={
            "cached_rows": cached_rows,
            "candidate_source_rows": candidate_source_rows,
            "captured_source_rows": captured_source_rows,
            "selected_sources": ",".join(sorted(RADAR_SOURCE_KEYS)),
            "source_rows": candidate_source_rows,
            "target_count": len(target_codes),
            "target_filter": True,
        },
        level="info",
    )
    fallback_radar_data = None
    if context_snapshot is None:
        gather_radar = getattr(owner, "_gather_radar_data", None)
        fallback_radar_data = (
            gather_radar([code for _, code in codes_with_rows])
            if callable(gather_radar)
            else ({}, {}, {}, {}, {}, None)
        )
    return partial(_run_vcp_refresh, tuple(codes_with_rows), context_snapshot, fallback_radar_data)


def _emit_vcp_if_current(owner, generation: int, results) -> None:
    complete_preload = getattr(owner, "_complete_background_preload_vcp", None)
    if owner._closing or generation != owner._vcp_task_generation or not results:
        if callable(complete_preload):
            complete_preload(generation)
        return
    event_bus.sig_vcp_watchlist_ready.emit(results)


def _log_task_error_if_current(owner, generation: int, label: str, error_message) -> None:
    if owner._closing or generation != owner._vcp_task_generation:
        return
    complete_preload = getattr(owner, "_complete_background_preload_vcp", None)
    if callable(complete_preload):
        complete_preload(generation, committed=False)
    log.error(f"[{label}] {error_message}")


def _run_metrics_persist(payload, cancellation_token):
    return persist_watchlist_metrics(payload, cancellation_token=cancellation_token)


def _commit_watchlist_metrics(patch_payload, cancellation_token) -> None:
    if _task_cancelled(cancellation_token):
        return
    watchlist_vm.bulk_patch_entries(patch_payload, remove_keys=["催化剂", "美股日报", "热点板块"])


@dataclass
class _WatchlistBackgroundPreloadState:
    requested: bool = False
    complete: bool = False
    signature: tuple[str, ...] = ()
    vcp_pending: bool = False
    vcp_generation: int = 0
    vcp_committed: bool = False


def _initialize_watchlist_vcp_state(owner) -> None:
    owner._pending_vcp_calc = False
    owner._deferred_vcp_payload = None
    owner._deferred_vcp_signature = ()
    owner._pending_vcp_apply_payload = None
    owner._pending_vcp_apply_signature = ()
    owner._pending_vcp_apply_noninteractive = False
    owner._pending_vcp_apply_generation = 0
    owner._vcp_apply_timer = None
    owner._last_vcp_tab_shown_at = 0.0
    owner._last_vcp_calc_started_at = 0.0
    owner._last_vcp_payload_signature = ()
    owner._vcp_calc_allow_noninteractive = False
    owner._background_preload = _WatchlistBackgroundPreloadState()


def _watchlist_initial_data_is_loading(owner) -> bool:
    lifecycle = getattr(owner, "_task_lifecycle", None)
    return bool(lifecycle is not None and "initial_data" in getattr(lifecycle, "active_names", ()))


def _resume_watchlist_preload_without_rows(owner, preload) -> bool:
    if _watchlist_initial_data_is_loading(owner):
        return False
    if owner._watchlist_initial_data_finished:
        preload.vcp_committed = True
        preload.complete = True
        return True
    owner._load_special_data(refresh_indicators=False)
    return False


def _clear_watchlist_vcp_apply_queue(owner) -> None:
    apply_timer = getattr(owner, "_vcp_apply_timer", None)
    if apply_timer is not None:
        apply_timer.stop()
    owner._deferred_vcp_payload = None
    owner._deferred_vcp_signature = ()
    owner._pending_vcp_apply_payload = None
    owner._pending_vcp_apply_signature = ()
    owner._pending_vcp_apply_noninteractive = False
    owner._pending_vcp_apply_generation = 0


def _watchlist_timer_is_active(owner, name: str) -> bool:
    timer = getattr(owner, name, None)
    return bool(timer is not None and timer.isActive())


def _watchlist_preload_state_is_ready(preload) -> bool:
    return bool(
        preload.requested
        and preload.complete
        and preload.vcp_committed
        and not preload.vcp_pending
    )


def _watchlist_vcp_queue_is_idle(owner) -> bool:
    return bool(
        not owner._pending_vcp_calc
        and owner._deferred_vcp_payload is None
        and owner._pending_vcp_apply_payload is None
        and not _watchlist_timer_is_active(owner, "_vcp_calc_timer")
        and not _watchlist_timer_is_active(owner, "_vcp_apply_timer")
    )


def _vcp_payload_dict(payload: object) -> dict:
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _pending_background_vcp_preload(owner):
    preload = getattr(owner, "_background_preload", None)
    if preload is not None and preload.requested and preload.vcp_pending:
        return preload
    return None


def _vcp_payload_matches_last(owner, signature: tuple, *, force: bool) -> bool:
    return bool(not force and signature and signature == owner._last_vcp_payload_signature)


def _vcp_payload_matches_pending(owner, signature: tuple) -> bool:
    return bool(signature and signature == owner._pending_vcp_apply_signature)


def _watchlist_vcp_rows(owner) -> list[tuple[int, str]]:
    model = getattr(owner, "model", None)
    rows = getattr(model, "row_data", None) if model is not None else None
    return [(idx, str(row.get("代码"))) for idx, row in enumerate(rows or ()) if row.get("代码")]


def _start_watchlist_vcp_refresh(owner, *, allow_noninteractive: bool) -> bool:
    codes_with_rows = _watchlist_vcp_rows(owner)
    if not codes_with_rows:
        return False

    refresh_job = _build_vcp_refresh_job(owner, codes_with_rows)
    owner._last_vcp_calc_started_at = time.monotonic()
    owner._vcp_task_generation += 1
    generation = owner._vcp_task_generation
    preload = getattr(owner, "_background_preload", None)
    if allow_noninteractive and preload is not None and preload.vcp_pending:
        preload.vcp_generation = generation
    task_lifecycle_for(owner, runner=task_manager).run_background(
        "vcp_refresh",
        refresh_job,
        on_success=partial(_emit_vcp_if_current, owner, generation),
        on_error=partial(_log_task_error_if_current, owner, generation, "关注池"),
        task_id=task_registry.workspace("watchlist_vcp_refresh").task_id,
        timeout_sec=120,
    )
    return True


class _WatchlistBackgroundPreloadMixin:
    """关注池缓存预载 capability；不改变前台 Tab 的交互刷新语义。"""

    def prime_background_load(self) -> bool:
        """仅使用已有缓存预加载关注池，并在基础行后到时自动继续。"""
        if self._closing:
            return False
        self._background_preload.requested = True
        return self._resume_background_preload_after_rows()

    def _resume_background_preload_after_rows(self) -> bool:
        preload = self._background_preload
        if self._closing or not preload.requested:
            return False

        rows = list(getattr(self.model, "row_data", None) or []) if self.model is not None else []
        signature = self._background_preload_row_signature(rows)
        if not signature:
            return _resume_watchlist_preload_without_rows(self, preload)

        if signature == preload.signature:
            return preload.complete

        preload.signature = signature
        preload.complete = False
        preload.vcp_pending = True
        preload.vcp_generation = 0
        preload.vcp_committed = False
        _clear_watchlist_vcp_apply_queue(self)
        # 后台预加载只消费内存行情快照与本地指标缓存，不发起实时行情请求。
        self._apply_quote_store_snapshot()
        self._request_vcp_calc(
            delay_ms=self._startup_indicator_refresh_delay_ms,
            allow_noninteractive=True,
        )
        return True

    def _complete_background_preload_vcp(self, generation: int, *, committed: bool = True) -> None:
        preload = self._background_preload
        if self._closing or not preload.vcp_pending or generation != preload.vcp_generation:
            return
        preload.vcp_pending = False
        preload.vcp_committed = bool(committed)
        preload.complete = bool(committed)

    def is_background_preload_complete(self) -> bool:
        preload = self._background_preload
        return _watchlist_preload_state_is_ready(preload) and _watchlist_vcp_queue_is_idle(self)

    def prepare_workspace_preload_reveal(self) -> None:
        table = getattr(self, "table_sp", None)
        prepare = getattr(table, "prepare_background_preload_reveal", None)
        if callable(prepare):
            prepare()

    def sync_workspace_viewport_background(self) -> None:
        table = getattr(self, "table_sp", None)
        sync = getattr(table, "sync_viewport_base_background", None)
        if callable(sync):
            sync()

    def prepare_shell_nav_repaint_guard(self) -> None:
        table = getattr(self, "table_sp", None)
        prepare = getattr(table, "prepare_shell_nav_repaint_guard", None)
        if callable(prepare):
            prepare()

    def cancel_background_preload(self, *, reason: str):
        def _reset() -> None:
            self._vcp_task_generation += 1
            self._watchlist_lineage_generation = int(
                getattr(self, "_watchlist_lineage_generation", 0)
            ) + 1
            self._pending_vcp_calc = False
            self._deferred_vcp_payload = None
            self._deferred_vcp_signature = ()
            self._pending_vcp_apply_payload = None
            self._pending_vcp_apply_signature = ()
            self._pending_vcp_apply_noninteractive = False
            self._pending_vcp_apply_generation = 0
            for timer_name in ("_vcp_calc_timer", "_vcp_apply_timer"):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            self._finish_initial_data_loading()
            preload = self._background_preload
            preload.requested = False
            preload.complete = False
            preload.vcp_pending = False
            preload.vcp_committed = False
            preload.signature = ()
            self._watchlist_initial_data_finished = False
            self._background_preload_retry_pending = True

        return cancel_background_preload_tasks(
            self,
            lifecycle_names=("initial_data", "vcp_refresh"),
            task_ids=(
                task_registry.workspace("watchlist_initial_data"),
                task_registry.workspace("watchlist_vcp_refresh"),
            ),
            reason=reason,
            reset_state=_reset,
            local_settled=lambda: not self._background_preload.vcp_pending
            and not self._initial_data_loading,
            runner=task_manager,
        )

    def on_workspace_tab_activated(self) -> None:
        if not getattr(self, "_background_preload_retry_pending", False):
            return
        self._background_preload_retry_pending = False
        rows = list(getattr(self.model, "row_data", None) or []) if self.model is not None else []
        if rows:
            self._request_vcp_calc(allow_noninteractive=True)
        else:
            self._load_special_data()

    def _cleanup_runtime_state(self):
        self.shutdown()
        super()._cleanup_runtime_state()


class WatchlistTab(_WatchlistBackgroundPreloadMixin, BaseStockTab):
    CONTEXT_REFRESH_MIN_INTERVAL_MS = 60_000
    INITIAL_LOADING_REVEAL_DELAY_MS = 120
    POST_SHOW_VCP_CALC_DELAY_MS = 2_000
    POST_SHOW_VCP_APPLY_SETTLE_MS = 2_500
    FOREGROUND_VCP_APPLY_DELAY_MS = 150
    _schedule_initial_loading_overlay = _schedule_initial_loading_overlay
    _show_initial_loading_if_pending = _show_initial_loading_if_pending
    _finish_initial_data_loading = _finish_initial_data_loading
    _run_pending_async_local_quote_refresh = _run_pending_async_local_quote_refresh
    _run_coalesced_model_update = _run_coalesced_model_update

    """
    关注池 独立 Tab 组件 (Controller + View)
    全权负责关注池的增删查改、实时报价、AI诊断结果展示。
    通过 EventBus 与外部通信，不直接依赖 MainWindowQT。
    """

    def __init__(
        self,
        data_provider,
        parent=None,
        *,
        startup_tasks_enabled: bool = True,
        startup_indicator_refresh_enabled: bool = True,
        startup_indicator_refresh_delay_ms: int = 500,
        startup_followup_refresh_enabled: bool = True,
    ):
        ui_construct_started_at = time.perf_counter()
        super().__init__(data_provider=data_provider, parent=parent)
        self._watchlist_last_update = ""
        self._closing = False
        self._vcp_task_generation = 0
        self._startup_tasks_enabled = bool(startup_tasks_enabled)
        self._startup_indicator_refresh_enabled = bool(startup_indicator_refresh_enabled)
        self._startup_indicator_refresh_delay_ms = self._coerce_delay_ms(startup_indicator_refresh_delay_ms, 500)
        self._startup_followup_refresh_enabled = bool(startup_followup_refresh_enabled)
        self._delayed_special_timer = self._initial_loading_timer = self._quote_refresh_timer = None
        self._initial_data_loading = False
        self._pending_quote_task_id = None
        _initialize_watchlist_vcp_state(self)
        self._watchlist_initial_data_finished = False
        self._watchlist_lineage_service = None
        self._last_watchlist_result = None
        self._last_watchlist_signature = ""
        self._init_ui()
        record_metric("watchlist_tab_import_ms", _WATCHLIST_MODULE_IMPORT_MS, unit="ms")
        record_metric(
            "watchlist_tab_ui_construct_ms",
            (time.perf_counter() - ui_construct_started_at) * 1000.0,
            unit="ms",
        )
        # 订阅全局报价与大一统市值更新机制
        self.subscribe_global_quotes()

        # 挂载全局事件总线
        event_bus.sig_watchlist_changed.connect(self._on_watchlist_changed)
        event_bus.sig_app_closing.connect(self._on_app_closing)

        # v4: 使用精准专用信道
        event_bus.sig_cache_bootstrap_ready.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_cache_reload_completed.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_earnings_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_na_daily_updated.connect(self._on_na_daily_updated)
        event_bus.sig_ai_industry_chain_updated.connect(self._on_ai_industry_chain_updated)
        event_bus.sig_block_trade_updated.connect(self._on_block_trade_updated)
        event_bus.sig_lhb_pool_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_fund_holdings_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_stock_context_snapshot_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_vcp_watchlist_ready.connect(self._on_vcp_watchlist_ready)

        # 先立即回填一次，避免启动期 UI 忙时定时器延后导致“关注池长期空白”。
        if self._startup_tasks_enabled:
            if self._startup_indicator_refresh_enabled:
                if self._startup_indicator_refresh_delay_ms == 500:
                    self._load_special_data()
                else:
                    self._load_special_data(indicator_delay_ms=self._startup_indicator_refresh_delay_ms)
            else:
                self._load_special_data(refresh_indicators=False)
            # 再做一次延迟回填，兜住启动后缓存/名称映射后到位的场景。
            if self._startup_indicator_refresh_enabled and self._startup_followup_refresh_enabled:
                self._delayed_special_timer = QTimer(self)
                self._delayed_special_timer.setSingleShot(True)
                self._delayed_special_timer.timeout.connect(self._load_special_data)
                self._delayed_special_timer.start(3500)

    # ================================================================
    # UI 构建
    # ================================================================
    @staticmethod
    def _coerce_delay_ms(value, default: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return max(0, int(default))

    @classmethod
    def _vcp_payload_signature(cls, payload: object) -> tuple:
        if not isinstance(payload, dict) or not payload:
            return ()

        items = []
        for raw_code, raw_data in payload.items():
            code = str(raw_code or "").strip()
            if not code:
                continue
            data = raw_data if isinstance(raw_data, dict) else {"value": raw_data}
            values = tuple(
                sorted((str(key), cls._stable_metric_value(value)) for key, value in dict(data or {}).items())
            )
            items.append((code, values))
        return tuple(sorted(items))

    @classmethod
    def _stable_metric_value(cls, value: object):
        if isinstance(value, dict):
            return tuple(sorted((str(key), cls._stable_metric_value(item)) for key, item in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(cls._stable_metric_value(item) for item in value)
        return str(value)

    def showEvent(self, event):  # noqa: N802 - Qt API naming
        super().showEvent(event)
        self._last_vcp_tab_shown_at = time.monotonic()
        if self._deferred_vcp_payload and self._is_active_workspace_tab_for_vcp():
            payload = self._deferred_vcp_payload
            self._deferred_vcp_payload = None
            self._deferred_vcp_signature = ()
            self._schedule_vcp_payload_apply(payload, delay_ms=self._vcp_apply_delay_ms())
        if self._pending_vcp_calc and self._should_start_interactive_runtime_on_show():
            self._pending_vcp_calc = False
            self._request_vcp_calc(
                delay_ms=max(self._startup_indicator_refresh_delay_ms, self.POST_SHOW_VCP_CALC_DELAY_MS),
                allow_noninteractive=True,
            )

    def hideEvent(self, event):  # noqa: N802 - Qt API naming
        super().hideEvent(event)
        timer = getattr(self, "_vcp_calc_timer", None)
        if timer is not None and timer.isActive():
            if not self._vcp_calc_allow_noninteractive:
                timer.stop()
                self._pending_vcp_calc = True
        apply_timer = getattr(self, "_vcp_apply_timer", None)
        if apply_timer is not None and apply_timer.isActive():
            if self._pending_vcp_apply_noninteractive:
                return
            apply_timer.stop()
            if self._pending_vcp_apply_payload:
                self._defer_vcp_payload(
                    self._pending_vcp_apply_payload,
                    self._pending_vcp_apply_signature,
                )
            self._pending_vcp_apply_payload = None
            self._pending_vcp_apply_signature = ()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_sp_status = QLabel("")

        self.sp_search = QLineEdit()
        self.sp_search.setPlaceholderText("筛选关注池...")
        self.sp_search.setAccessibleName("关注池筛选")
        self.sp_search.setAccessibleDescription("按代码或名称筛选当前关注池股票")
        self.sp_search.setMinimumWidth(150)
        self.sp_search.setMaximumWidth(240)
        self.sp_search.textChanged.connect(self._filter_table)

        filter_widgets = [self.sp_search]

        self.add_stock_input = QLineEdit()
        self.add_stock_input.setPlaceholderText("输入A股代码，如 600519")
        self.add_stock_input.setAccessibleName("添加自选股输入框")
        self.add_stock_input.setAccessibleDescription("输入六位 A 股代码后可加入关注池")
        self.add_stock_input.setClearButtonEnabled(True)
        self.add_stock_input.setMinimumWidth(160)
        self.add_stock_input.setMaximumWidth(260)
        self.add_stock_input.returnPressed.connect(self._add_custom_stock)

        btn_add_stock = QPushButton("添加自选股")
        btn_add_stock.setObjectName("primaryButton")
        btn_add_stock.clicked.connect(self._add_custom_stock)

        btn_reset = QPushButton("解除列表排序")
        btn_reset.setProperty("toolbarOverflow", True)
        btn_reset.clicked.connect(self._reset_view)

        action_widgets = [self.add_stock_input, btn_add_stock, btn_reset]
        toolbar = self.build_tab_toolbar("关注池", self.lbl_sp_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格控件
        self.table_sp = VCPTableView(default_row_height=30)

        # 拖拽排序设置 (只有在默认排序状态下才可用)
        self.table_sp.setDragEnabled(True)
        self.table_sp.setAcceptDrops(True)
        self.table_sp.setDropIndicatorShown(True)
        self.table_sp.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table_sp.setDragDropOverwriteMode(False)

        # 绑定 Model 与 Delegate
        headers = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "RPS强度",
            "细分板块",
            "摘要",
            "备注",
        ]
        self.model = StockTableModel(headers)
        self.model.set_sparse_update_coalescing(True)
        self.model.set_presentation_cache_enabled(True)
        # 仅行情保留真实连续区间，避免首末行大矩形失效；VCP/context 批量更新仍按原策略合并。
        self.model.set_sparse_quote_update_coalescing(False)
        self.model.set_muted_text_headers(["RPS强度", "细分板块", "摘要", "备注"])
        self.proxy_model = RtSortFilterProxyModel(self.table_sp)
        self.proxy_model.setSourceModel(self.model)
        self.table_sp.setModel(self.proxy_model)
        self.table_sp.set_coalesced_flash_repaint_enabled(True)
        self.table_sp.set_targeted_flash_repaint_enabled(True, metric_scope="watchlist")
        # Keep the non-opaque contract while the viewport owns its palette background.
        self.table_sp.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.table_sp.set_viewport_base_background_enabled(True)

        self.delegate = StockItemDelegate(self.table_sp)
        self.table_sp.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table_sp, empty_title="暂无关注池数据", loading_title="加载中...")

        # 接收模型发出的手动排序完成信号
        self.model.sig_rows_reordered.connect(self._on_rows_reordered)

        # 自适应列宽
        header = self.table_sp.horizontalHeader()
        self.apply_table_column_preset(
            self.table_sp,
            [64, 76, 70, 70, 88, 84, 112, 150, 220],
            stretch_last=True,
        )
        # 绑定防抖自动保存与恢复配置（列结构变更后沿用新 key，避免旧状态错位）
        restored_sort = self.bind_header_persistence(self.table_sp, "header_state_watchlist_v11")
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        if not restored_sort:
            self.table_sp.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        # 双击 → 查看K线图（通过 EventBus 广播）
        self.table_sp.doubleClicked.connect(self._on_double_click)

        # 右键菜单
        self.table_sp.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_sp.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state)

    @staticmethod
    def _now_hhmm() -> str:
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def _format_watchlist_note(earnings: object = "", block_trade: object = "", lhb: object = "") -> str:
        return _format_watchlist_note(earnings, block_trade, lhb)

    def _touch_watchlist_update(self, stamp: str | None = None) -> bool:
        text = str(stamp or "").strip() or self._now_hhmm()
        if not text or text == self._watchlist_last_update:
            return False
        self._watchlist_last_update = text
        return True

    def _latest_trade_date_text(self) -> str:
        try:
            trade_date = MarketCalendar.get_latest_trade_date("CN", allow_refresh=False)
            if trade_date is not None:
                return trade_date.isoformat()
            return MarketCalendar.today("CN").isoformat()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _describe_watchlist_rows(self, rows: list[dict]):
        if self._watchlist_lineage_service is None:
            self._watchlist_lineage_service = create_tab_lineage_service(
                "watchlist",
                provider_status_reader=self._read_provider_status,
            )
        warnings = []
        if not rows:
            warnings.append("watchlist_rows_empty")
        return self._watchlist_lineage_service.describe(
            rows,
            trade_date=self._latest_trade_date_text(),
            triggered_network=False,
            warnings=warnings,
            extra={
                "startup_tasks_enabled": self._startup_tasks_enabled,
                "last_table_update": self._watchlist_last_update,
            },
        )

    def get_data_lineage(self) -> dict:
        result = self._last_watchlist_result
        if result is None:
            rows = list(getattr(self.model, "row_data", None) or [])
            result = self._describe_watchlist_rows(rows)
            self._last_watchlist_result = result
            self._last_watchlist_signature = result.signature
        return result.lineage.as_dynamic_dict()

    # ================================================================
    # 数据加载
    # ================================================================
    def _load_special_data(self, *, refresh_indicators: bool = True, indicator_delay_ms: int | None = None):
        """先显示表格壳，再在组件生命周期内后台读取并整形关注池数据。"""
        if self._closing:
            return

        self._watchlist_initial_data_finished = False

        live_data_map = _capture_watchlist_live_data(self.model)
        code_name_map = dict(getattr(self.data_provider, "code2name", {}) or {})
        if not live_data_map:
            self._schedule_initial_loading_overlay()
        else:
            self._finish_initial_data_loading()

        task_lifecycle_for(self, runner=task_manager).run_background(
            "initial_data",
            _build_watchlist_initial_job(code_name_map, live_data_map),
            on_success=partial(
                _apply_special_data_payload,
                self,
                refresh_indicators=refresh_indicators,
                indicator_delay_ms=indicator_delay_ms,
            ),
            on_error=partial(_on_special_data_error, self),
            task_id=task_registry.workspace("watchlist_initial_data").task_id,
            timeout_sec=15,
        )

    def _render_table(self, all_codes, data_dict, old_pool):
        """渲染关注池表格"""
        rows = _shape_watchlist_rows(
            tuple(all_codes),
            data_dict,
            old_pool,
            dict(getattr(self.data_provider, "code2name", {}) or {}),
            _capture_watchlist_live_data(self.model),
        )
        _apply_watchlist_rows(self, rows)

    def _can_fetch_live_quotes_now(self) -> bool:
        provider = getattr(self, "data_provider", None)
        is_online = getattr(provider, "is_online", None)
        if not callable(is_online) or not is_online():
            return False
        try:
            return bool(MarketCalendar.is_quote_refresh_time())
        except (RuntimeError, TypeError, ValueError):
            return False

    def _refresh_quotes_from_store_or_live(self, *, quote_task_id):
        self._apply_quote_store_snapshot()
        if self._can_fetch_live_quotes_now():
            self._refresh_quotes_async_local(quote_task_id=quote_task_id)

    def _refresh_quotes_async_local(self, *, quote_task_id):
        if self._closing:
            return
        self._pending_quote_task_id = quote_task_id
        if self._quote_refresh_timer is None:
            self._quote_refresh_timer = QTimer(self)
            self._quote_refresh_timer.setSingleShot(True)
            self._quote_refresh_timer.timeout.connect(self._run_pending_async_local_quote_refresh)
        self._quote_refresh_timer.start(0)

    def _run_async_local_quote_refresh(self, quote_task_id):
        if getattr(self, "_closing", False):
            return
        try:
            self.refresh_table_quotes_and_market_caps(
                quote_task_id=quote_task_id,
                async_local=True,
            )
        except TypeError as exc:
            if "async_local" not in str(exc):
                raise
            self.refresh_table_quotes_and_market_caps(quote_task_id=quote_task_id)

    def _update_status_summary(self):
        rows = list(getattr(self.model, "row_data", []) or [])
        total = len(rows)
        visible = self.proxy_model.rowCount()
        search_text = self.sp_search.text().strip()
        if total == 0:
            self.lbl_sp_status.setText(
                self.format_workspace_status(
                    "关注池为空",
                    result="0只",
                    freshness=self._watchlist_last_update or "待加载",
                    current_filter=search_text or "全部",
                    next_step="输入代码或从其他页面加入",
                )
            )
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无关注池数据")
            return

        source_tags = []
        for row in rows:
            for tag in watchlist_vm.normalize_source_tags(row.get("来源标签") or row.get("来源", "")):
                if tag not in source_tags:
                    source_tags.append(tag)

        extra_segments = []
        if source_tags:
            extra_segments.append(f"来源 {watchlist_vm.format_source_tags(source_tags)}")

        self.lbl_sp_status.setText(
            self.format_workspace_status(
                "关注池已就绪",
                result=f"{visible}/{total}只",
                freshness=self._watchlist_last_update or "待刷新",
                current_filter=search_text or "全部",
                next_step="",
                extra_segments=extra_segments,
            )
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()

    def _on_rows_reordered(self, new_codes_list):
        """当用户在表格手动拖拽重排后，更新VM字典保存并重新渲染"""
        # 1. 如果表格处于按某列排序模式(如按涨幅排)，禁止拖拽覆盖
        if self.proxy_model.sortColumn() != -1:
            from ui.components.toast_widget import show_toast

            show_toast("当前正处于条件排序状态，拖拽无效，请点击右上角【还原默认视图】后再拖拽！", "warning", self)
            self._load_special_data()  # 撤销刚刚拖拽引发的界面错乱，滚回原状
            return

        # 2. 调用 VM 写入磁盘
        watchlist_vm.reorder(new_codes_list)

        # 3. 再重新拉取一次保持严格同步
        self._load_special_data()

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, index):
        """双击行 → 打开 K 线图。"""
        if not index.isValid():
            return
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return
        code = self.model.row_data[row].get("代码")
        if code:
            watchlist_data = watchlist_vm.get_watchlist_data()
            code_list = []
            clicked_visual_row = index.row()
            for r in range(self.proxy_model.rowCount()):
                s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
                if s_idx.row() < len(self.model.row_data):
                    rd = dict(self.model.row_data[s_idx.row()] or {})
                    code_key = str(rd.get("代码", "")).strip()
                    merged = {"代码": code_key, "名称": rd.get("名称", "")}
                    persisted = watchlist_data.get(code_key, {})
                    if isinstance(persisted, dict):
                        for k, v in persisted.items():
                            if v not in (None, "", [], {}):
                                merged[k] = v
                    for k, v in rd.items():
                        if v not in (None, "", [], {}):
                            merged[k] = v
                    code_list.append(merged)

            current_idx = 0
            if 0 <= clicked_visual_row < len(code_list):
                current_idx = clicked_visual_row

            ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        """关注池右键菜单 — 委托给统一菜单工厂 (#2)"""
        index = self.table_sp.indexAt(pos)
        if not index.isValid():
            return

        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if not code or not name:
            return

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(self, code, name)

    def _get_a_share_name_map(self) -> dict:
        cached = getattr(self, "_a_share_name_map", None)
        if isinstance(cached, dict) and cached:
            return cached

        provider = self.data_provider
        code_map = {}
        if provider is not None:
            code_map = getattr(provider, "code2name", {}) or {}
            if not code_map and hasattr(provider, "get_all_codes"):
                try:
                    code_map = provider.get_all_codes() or {}
                    provider.code2name = code_map
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[关注池] 读取A股代码表失败: {e}")
                    code_map = {}

        normalized_map = {}
        for code, name in code_map.items():
            normalized_code = self._normalize_quote_code(code).zfill(6)
            if len(normalized_code) == 6 and normalized_code.isdigit():
                normalized_map[normalized_code] = str(name or normalized_code).strip()

        self._a_share_name_map = normalized_map
        return self._a_share_name_map

    def _remember_resolved_a_share_name(self, code: str, name_map: dict) -> str:
        for raw_code, raw_name in dict(name_map or {}).items():
            refreshed_code = self._normalize_quote_code(raw_code).zfill(6)
            if refreshed_code != code:
                continue
            name = str(raw_name or "").strip()
            if not name or name == code:
                return ""
            current_map = dict(getattr(self, "_a_share_name_map", {}) or {})
            current_map[code] = name
            self._a_share_name_map = current_map
            return name
        return ""

    def _resolve_missing_a_share_name(self, code: str) -> str:
        provider = self.data_provider
        ensure_name_map = getattr(provider, "ensure_code_name_map", None)
        if not callable(ensure_name_map):
            return ""

        try:
            refreshed_map = ensure_name_map([code], refresh_missing=False) or {}
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[关注池] 补齐股票名称失败({code}): {exc}")
            return ""

        return WatchlistTab._remember_resolved_a_share_name(self, code, refreshed_map)

    def _schedule_missing_a_share_name_resolution(self, code: str) -> None:
        if getattr(self, "_closing", False):
            return
        provider = getattr(self, "data_provider", None)
        if provider is None:
            return
        task_name = f"manual_stock_name_resolution:{code}"
        task_key = task_registry.transient_window(f"watchlist_manual_stock_name_resolution:{code}")
        task_lifecycle_for(self, runner=task_manager).run_background(
            task_name,
            partial(_resolve_a_share_name_in_background, provider, code),
            on_success=partial(self._on_missing_a_share_name_resolved, code),
            on_error=partial(self._on_missing_a_share_name_resolution_error, code),
            task_id=task_key,
            timeout_sec=12,
        )

    def _on_missing_a_share_name_resolved(self, code: str, refreshed_map: dict) -> None:
        if getattr(self, "_closing", False):
            return
        name = WatchlistTab._remember_resolved_a_share_name(self, code, refreshed_map)
        if not name:
            show_toast(f"{code} 未能核验为 A 股，请检查代码后重试", "warning", self)
            return

        if watchlist_vm.is_in_watchlist(code):
            watchlist_vm.patch_entry(code, {"名称": name})
            self.refresh_watchlist_names({code: name})
            return

        added = watchlist_vm.add_stock(
            code,
            name,
            {"代码": code, "名称": name, "code": code, "name": name},
            source_tags=["手动"],
        )
        if added:
            show_toast(f"{name} 已自动加入关注池，正在刷新行情与附加列", "success", self)
        else:
            show_toast(f"{name} 已在关注池", "info", self)

    def _on_missing_a_share_name_resolution_error(self, code: str, _error) -> None:
        if getattr(self, "_closing", False):
            return
        show_toast(f"{code} 名称核验失败，可稍后重试", "warning", self)

    def _add_custom_stock(self):
        raw_code = self.add_stock_input.text() if hasattr(self, "add_stock_input") else ""
        code = self._normalize_quote_code(raw_code).zfill(6)
        if len(code) != 6 or not code.isdigit():
            show_toast("请输入 6 位 A 股代码", "warning", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.setFocus()
                self.add_stock_input.selectAll()
            return

        name_map = self._get_a_share_name_map()
        name = str(name_map.get(code, "") or "").strip()
        if not name or name == code:
            name = self._resolve_missing_a_share_name(code)
        if not name:
            schedule_name_resolution = getattr(self, "_schedule_missing_a_share_name_resolution", None)
            if callable(schedule_name_resolution):
                schedule_name_resolution(code)
            show_toast(f"正在后台核验 {code}，成功后将自动加入关注池", "info", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()
            return

        if watchlist_vm.is_in_watchlist(code):
            show_toast(f"{name} 已在关注池", "info", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()
            return

        added = watchlist_vm.add_stock(
            code,
            name,
            {"代码": code, "名称": name, "code": code, "name": name},
            source_tags=["手动"],
        )
        if added:
            self.lbl_sp_status.setText(
                self.format_workspace_status(
                    "关注池已更新",
                    result=f"{len(getattr(self.model, 'row_data', []) or [])}只",
                    freshness=self._watchlist_last_update or self._now_hhmm(),
                    current_filter=self.sp_search.text().strip() or "全部",
                    next_step=f"等待 {name} 的行情与来源补齐",
                )
            )
            show_toast(f"{name} 已加入关注池，正在刷新行情与附加列", "success", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()
        else:
            show_toast(f"{name} 已在关注池", "info", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()

    def _reset_view(self):
        """取消强制排序：仅重置表格排序状态，不影响用户自定义的列宽"""
        # 还原默认排序列，使得可随意拖拽
        self.table_sp.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        show_toast("已解除列表排序，您可以自由拖拽个股顺序了", "success", self.window(), duration=2500)

    # ================================================================
    # EventBus 事件监听及同步更新
    # ================================================================
    def _gather_radar_data(self, codes=None):
        """主线程快速提取 UI 数据，供后台线程使用（避免跨线程访问UI崩溃）"""
        workspace = getattr(self.window(), "_workspace", None)
        if workspace is None:
            return {}, {}, {}, {}, {}, None

        try:
            return workspace.collect_watchlist_radar_data(
                include_source_cache_fallback=True,
                target_codes=codes,
                allow_lhb_cache_compute=False,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[关注池] 提取工作区雷达数据异常: {exc}")
            return {}, {}, {}, {}, {}, None

    def _apply_quote_snapshot(
        self,
        quotes: Mapping[str, Mapping[str, object]] | None,
    ):
        apply_snapshot = super()._apply_quote_snapshot
        return self._run_coalesced_model_update(
            lambda: apply_snapshot(quotes),
            changed_headers={"现价", "市价", "涨幅%", "涨幅", "市值", "买点"},
            reason="quote_snapshot",
        )

    def _on_watchlist_changed(self, action: str, _code: str):
        """外部请求关注池变更时，防抖 300ms 后再重新加载（防止快速增删导致任务堆积）"""
        if not hasattr(self, "_debounce_timer"):
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(self._do_watchlist_reload)
        # 每次新信号进来都重置计时器，只有最后一次 300ms 后才真正触发
        self._debounce_timer.start(80 if action == "add" else 300)

    def _do_watchlist_reload(self):
        """防抖后的实际重载逻辑"""
        self._load_special_data()

    def _refresh_vcp_indicators(self, codes_with_rows, radar_data_tuple=None, cancellation_token=None):
        """后台线程：计算关注池标的的 RPS 和跨 Tab 附加字段。"""
        if radar_data_tuple is None and codes_with_rows is not None:
            radar_data_tuple = self._gather_radar_data([code for _, code in codes_with_rows])
        return build_watchlist_indicator_results(
            codes_with_rows,
            radar_data=radar_data_tuple,
            rps_loader=load_active_rps_payload,
            cancellation_token=cancellation_token,
        )

    def _apply_vcp_indicators_ui(self, results: dict):
        """主线程：将 VCP 指标更新到 Model（按股票代码匹配，不再按行号，防止排序/拖拽后错位）"""
        if not results:
            return
        payload_signature = self._vcp_payload_signature(results)
        if payload_signature and payload_signature == self._last_vcp_payload_signature:
            return

        rows_changed = False
        with ui_stall_span(
            "WatchlistTab._apply_vcp_indicators_ui",
            tab="watchlist",
            signal=str(len(results)),
        ):
            # 构建 code -> row_idx 的当前映射（实时安全）
            current_rows = list(getattr(self.model, "row_data", []) or [])
            if not current_rows:
                return
            updated_rows = [dict(row_dict) for row_dict in current_rows]
            code_to_row = {}
            for idx, row_dict in enumerate(current_rows):
                c = row_dict.get("代码")
                if c:
                    code_to_row[c] = idx

            for code, data in results.items():
                row_idx = code_to_row.get(code, -1)
                if row_idx < 0 or row_idx >= len(updated_rows):
                    continue

                row_dict = updated_rows[row_idx]
                row_dict["RPS强度"] = data.get("rps", "--")
                if data.get("subsector"):
                    row_dict["细分板块"] = data["subsector"]

                # 三大阵营的数据注入 (如果原本有数据但不为空，我们不覆盖；如果本次扫到了，坚决覆盖)
                row_dict["摘要"] = str(data.get("remark", "") or "")
                row_dict["大宗交易"] = str(data.get("block_trade", "") or "")
                row_dict["大宗交易金额(万)"] = data.get("block_trade_amount_wan", "")
                row_dict["业绩异动"] = str(data.get("earnings", "") or "")
                row_dict["业绩环比%"] = data.get("earnings_qoq_pct", "")
                new_lhb = data.get("lhb", "")
                if isinstance(new_lhb, dict):
                    new_date = new_lhb.get("date", "")
                    new_net = new_lhb.get("net_wan", "")
                    new_buy_point = str(new_lhb.get("buy_point", "") or "").strip()
                    # 【逻辑变更】：根据龙虎榜表信息无条件刷新，不考虑历史日期锁定
                    row_dict["龙虎榜"] = BUY_POINT_TEXT if new_buy_point else ""
                    row_dict["龙虎榜日期"] = str(new_date or "")
                    row_dict["龙虎榜净额(万)"] = new_net if new_net not in (None, "") else ""
                else:
                    row_dict["龙虎榜"] = str(new_lhb or "")
                    row_dict["龙虎榜日期"] = ""
                    row_dict["龙虎榜净额(万)"] = ""
                row_dict["备注"] = self._format_watchlist_note(
                    row_dict.get("业绩异动", ""),
                    row_dict.get("大宗交易", ""),
                    row_dict.get("龙虎榜", ""),
                )

                source_tags = watchlist_vm.derive_source_tags(
                    row_dict,
                    existing_tags=row_dict.get("来源标签"),
                )
                row_dict["来源标签"] = source_tags
                row_dict["来源"] = watchlist_vm.format_source_tags(source_tags)

            rows_changed = updated_rows != current_rows
            if rows_changed:
                changed_headers = {
                    header
                    for header in (getattr(self.model, "headers", None) or ())
                    if any(
                        current_row.get(header) != updated_row.get(header)
                        for current_row, updated_row in zip(current_rows, updated_rows, strict=True)
                    )
                }
                self._run_coalesced_model_update(
                    lambda: self.model.update_data(updated_rows, hydrate_latest_quotes=False),
                    changed_headers=changed_headers,
                    reason="vcp_indicators",
                )

        if payload_signature:
            self._last_vcp_payload_signature = payload_signature
        if rows_changed:
            self._schedule_watchlist_metrics_persist(results)
        self._touch_watchlist_update()
        self._update_status_summary()

    def _schedule_watchlist_metrics_persist(self, results: dict):
        if self._closing or not results:
            return
        payload = {
            str(code).strip(): dict(data or {})
            for code, data in dict(results or {}).items()
            if str(code or "").strip()
        }
        if not payload:
            return
        task_lifecycle_for(self, runner=task_manager).run_background(
            "metrics_persist",
            partial(_run_metrics_persist, payload),
            on_error=lambda e: log.error(f"[watchlist] metrics persist failed: {e}"),
            task_id=task_registry.workspace("watchlist_vcp_persist").task_id,
            timeout_sec=30,
        )

    def _persist_watchlist_metrics(self, results: dict, cancellation_token=None):
        if not results:
            return
        patch_payload = build_watchlist_metric_patch(results, cancellation_token=cancellation_token)
        _commit_watchlist_metrics(patch_payload, cancellation_token)

    def _on_app_closing(self):
        """应用关闭前保存缓存"""
        self.shutdown()
        if self.model.row_data:
            self._save_special_cache_from_table()

    def shutdown(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._pending_quote_task_id = None
        self._vcp_task_generation += 1
        self._watchlist_lineage_generation = int(
            getattr(self, "_watchlist_lineage_generation", 0)
        ) + 1
        self._pending_vcp_calc = False
        self._deferred_vcp_payload = None
        self._deferred_vcp_signature = ()
        self._pending_vcp_apply_payload = None
        self._pending_vcp_apply_signature = ()
        self._pending_vcp_apply_noninteractive = False
        self._pending_vcp_apply_generation = 0
        finish_initial_loading = getattr(self, "_finish_initial_data_loading", None)
        if callable(finish_initial_loading):
            finish_initial_loading()
        for timer_name in (
            "_delayed_special_timer",
            "_initial_loading_timer",
            "_quote_refresh_timer",
            "_vcp_calc_timer",
            "_vcp_apply_timer",
            "_debounce_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is None:
                continue
            try:
                timer.stop()
            except RuntimeError:
                pass
        lifecycle = getattr(self, "_task_lifecycle", None)
        if lifecycle is not None:
            lifecycle.shutdown(timeout_ms=750)
        self._disconnect_runtime_signals()

    def _disconnect_runtime_signals(self):
        for signal, slot in (
            (event_bus.sig_watchlist_changed, self._on_watchlist_changed),
            (event_bus.sig_app_closing, self._on_app_closing),
            (event_bus.sig_cache_bootstrap_ready, self._on_cache_or_earnings_updated),
            (event_bus.sig_cache_reload_completed, self._on_cache_or_earnings_updated),
            (event_bus.sig_earnings_updated, self._on_cache_or_earnings_updated),
            (event_bus.sig_na_daily_updated, self._on_na_daily_updated),
            (event_bus.sig_ai_industry_chain_updated, self._on_ai_industry_chain_updated),
            (event_bus.sig_block_trade_updated, self._on_block_trade_updated),
            (event_bus.sig_lhb_pool_updated, self._on_cache_or_earnings_updated),
            (event_bus.sig_fund_holdings_updated, self._on_cache_or_earnings_updated),
            (event_bus.sig_stock_context_snapshot_updated, self._on_cache_or_earnings_updated),
            (event_bus.sig_vcp_watchlist_ready, self._on_vcp_watchlist_ready),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _save_special_cache_from_table(self):
        """应用关闭前将表格最新数据更新回 ViewModel，同时保存最终的视觉排序效果"""
        try:
            current_cache = watchlist_vm.get_watchlist_data()
            if not current_cache:
                return

            new_cache = {}
            # 从 proxy_model 里拿，确保记录的是屏幕上最终排序后的顺序
            row_count = self.proxy_model.rowCount()
            for r in range(row_count):
                source_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
                if not source_idx.isValid():
                    continue
                row_dict = self.model.row_data[source_idx.row()]
                code = str(row_dict.get("代码", ""))
                if not code or code not in current_cache:
                    continue

                # 更新最新的结构化指标到 ViewModel
                entry = current_cache[code]
                entry["RPS强度"] = str(row_dict.get("RPS强度", ""))
                entry["AI结论"] = str(row_dict.get("AI结论", ""))
                entry["细分板块"] = str(row_dict.get("细分板块", ""))
                entry["备注"] = str(row_dict.get("摘要", ""))
                entry["大宗交易"] = str(row_dict.get("大宗交易", ""))
                entry["大宗交易金额(万)"] = row_dict.get("大宗交易金额(万)", "")
                entry["业绩异动"] = str(row_dict.get("业绩异动", ""))
                entry["业绩环比%"] = row_dict.get("业绩环比%", "")
                entry["龙虎榜日期"] = str(row_dict.get("龙虎榜日期", ""))
                entry["龙虎榜净额(万)"] = row_dict.get("龙虎榜净额(万)", "")
                entry["来源标签"] = watchlist_vm.derive_source_tags(
                    row_dict,
                    existing_tags=row_dict.get("来源标签"),
                )
                entry.pop("催化剂", None)
                entry.pop("美股日报", None)
                entry.pop("热点板块", None)

                # 按视觉顺序保存
                new_cache[code] = entry

            # 防护网：如果用户有关闭前正在搜索过滤，没显示在表面的隐身票，原样追回防止丢票
            for code, entry in current_cache.items():
                if code not in new_cache:
                    new_cache[code] = entry

            if new_cache:
                watchlist_vm.replace_watchlist_data(new_cache)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[关注池] 同步缓存到 ViewModel 失败: {e}")

    def _on_cache_or_earnings_updated(self):
        """统一事件消费： F5缓存完成 or 业绩数据更新"""
        # F5 缓存完成后作为第二次刷新机会（此时 earnings/大宗交易/美股等可能已有数据）
        # 合并启动后期的重复触发
        self._request_vcp_calc(min_interval_ms=self.CONTEXT_REFRESH_MIN_INTERVAL_MS)

    def _on_na_daily_updated(self):
        """美股日报最近5份内容刷新后，同步关注池的细分板块缓存。"""
        self._request_vcp_calc(min_interval_ms=self.CONTEXT_REFRESH_MIN_INTERVAL_MS)

    def _on_ai_industry_chain_updated(self):
        """AI产业链刷新后，同步关注池的细分板块和备注来源。"""
        self._request_vcp_calc()

    def _on_block_trade_updated(self):
        """大宗交易数据刷新后，同步关注池的大宗交易缓存。"""
        self._request_vcp_calc()

    def _on_vcp_watchlist_ready(self, payload: object):
        if self._closing:
            return
        payload_dict = _vcp_payload_dict(payload)
        if not payload_dict:
            return
        payload_signature = self._vcp_payload_signature(payload_dict)
        preload = _pending_background_vcp_preload(self)
        if preload is not None:
            self._schedule_vcp_payload_apply(
                payload_dict,
                delay_ms=0,
                allow_noninteractive=True,
                generation=preload.vcp_generation,
                force=True,
            )
            return
        if _vcp_payload_matches_last(self, payload_signature, force=False):
            return
        if self._is_active_workspace_tab_for_vcp():
            self._schedule_vcp_payload_apply(payload_dict)
            return
        self._defer_vcp_payload(payload_dict, payload_signature)

    def _vcp_apply_delay_ms(self) -> int:
        delay_ms = self.FOREGROUND_VCP_APPLY_DELAY_MS
        shown_at = float(getattr(self, "_last_vcp_tab_shown_at", 0.0) or 0.0)
        if shown_at > 0.0:
            elapsed_ms = max(0.0, (time.monotonic() - shown_at) * 1000.0)
            if elapsed_ms < self.POST_SHOW_VCP_APPLY_SETTLE_MS:
                delay_ms = max(delay_ms, int(self.POST_SHOW_VCP_APPLY_SETTLE_MS - elapsed_ms))
        return delay_ms

    def _defer_vcp_payload(self, payload: dict, payload_signature: tuple | None = None) -> None:
        payload_dict = dict(payload or {})
        signature = payload_signature or self._vcp_payload_signature(payload_dict)
        if signature and signature == self._deferred_vcp_signature:
            return
        self._deferred_vcp_payload = payload_dict
        self._deferred_vcp_signature = signature

    def _ensure_vcp_apply_timer(self) -> QTimer:
        timer = getattr(self, "_vcp_apply_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._flush_pending_vcp_apply)
            self._vcp_apply_timer = timer
        return timer

    def _schedule_vcp_payload_apply(
        self,
        payload: object,
        *,
        delay_ms: int | None = None,
        allow_noninteractive: bool = False,
        generation: int = 0,
        force: bool = False,
    ) -> None:
        if self._closing:
            return
        payload_dict = _vcp_payload_dict(payload)
        if not payload_dict:
            return
        payload_signature = self._vcp_payload_signature(payload_dict)
        if _vcp_payload_matches_last(self, payload_signature, force=force):
            return
        if not allow_noninteractive:
            if not self._is_active_workspace_tab_for_vcp():
                self._defer_vcp_payload(payload_dict, payload_signature)
                return
        if _vcp_payload_matches_pending(self, payload_signature):
            return

        self._pending_vcp_apply_payload = payload_dict
        self._pending_vcp_apply_signature = payload_signature
        self._pending_vcp_apply_noninteractive = bool(allow_noninteractive)
        self._pending_vcp_apply_generation = int(generation)
        next_delay_ms = self._vcp_apply_delay_ms() if delay_ms is None else delay_ms
        self._ensure_vcp_apply_timer().start(max(0, int(next_delay_ms)))

    def _flush_pending_vcp_apply(self) -> None:
        payload = self._pending_vcp_apply_payload
        payload_signature = self._pending_vcp_apply_signature
        allow_noninteractive = bool(getattr(self, "_pending_vcp_apply_noninteractive", False))
        generation = int(getattr(self, "_pending_vcp_apply_generation", 0))
        self._pending_vcp_apply_payload = None
        self._pending_vcp_apply_signature = ()
        self._pending_vcp_apply_noninteractive = False
        self._pending_vcp_apply_generation = 0
        if self._closing or not payload:
            return
        if not allow_noninteractive and not self._is_active_workspace_tab_for_vcp():
            self._defer_vcp_payload(payload, payload_signature)
            return
        self._deferred_vcp_signature = ()
        if allow_noninteractive and payload_signature == self._last_vcp_payload_signature:
            self._last_vcp_payload_signature = ()
        log.debug(f"[watchlist] apply {len(payload)} metric rows")
        self._apply_vcp_indicators_ui(payload)
        if allow_noninteractive:
            self._complete_background_preload_vcp(generation)

    def _is_background_prewarm_indicator_blocked(self) -> bool:
        return (
            not self._startup_indicator_refresh_enabled
            and bool(getattr(self, "_workspace_noninteractive_loaded", False))
        )

    def _is_active_workspace_tab_for_vcp(self) -> bool:
        if not self._is_current_workspace_tab():
            return False
        try:
            return bool(self.isVisible())
        except RuntimeError:
            return False

    def _request_vcp_calc(
        self,
        delay_ms: int = 500,
        *,
        allow_noninteractive: bool = False,
        min_interval_ms: int | None = None,
    ):
        """请求计算 VCP 附加指标，带有防抖功能，防止启动时多次触发"""
        if self._closing:
            return
        if not allow_noninteractive and self._is_background_prewarm_indicator_blocked():
            self._pending_vcp_calc = True
            return
        if not allow_noninteractive and not self._is_active_workspace_tab_for_vcp():
            self._pending_vcp_calc = True
            return
        self._pending_vcp_calc = False
        self._vcp_calc_allow_noninteractive = bool(
            getattr(self, "_vcp_calc_allow_noninteractive", False) or allow_noninteractive
        )
        if not hasattr(self, "_vcp_calc_timer"):
            self._vcp_calc_timer = QTimer(self)
            self._vcp_calc_timer.setSingleShot(True)
            self._vcp_calc_timer.timeout.connect(self._do_vcp_calc)
        next_delay_ms = max(0, int(delay_ms))
        if min_interval_ms is not None and not allow_noninteractive:
            elapsed_ms = max(0.0, (time.monotonic() - float(self._last_vcp_calc_started_at or 0.0)) * 1000.0)
            remaining_ms = int(max(0.0, float(min_interval_ms) - elapsed_ms))
            next_delay_ms = max(next_delay_ms, remaining_ms)
        self._vcp_calc_timer.start(next_delay_ms)

    def prime_startup_state(self):
        """工作区联动：启动后主动补一次关注池行情与附加指标。"""
        if self._closing:
            return
        if not self.model or not getattr(self.model, "row_data", None):
            return
        self._refresh_quotes_from_store_or_live(
            quote_task_id=task_registry.quote_refresh("smart_startup_watchlist").task_id
        )
        self._request_vcp_calc(
            delay_ms=self._startup_indicator_refresh_delay_ms,
            allow_noninteractive=True,
        )

    @staticmethod
    def _background_preload_row_signature(rows) -> tuple[str, ...]:
        return tuple(
            str(row.get("代码", "") or "").strip()
            for row in rows or ()
            if isinstance(row, dict) and str(row.get("代码", "") or "").strip()
        )

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        if not self.model:
            return False

        updates = []
        for row_index, row in enumerate(getattr(self.model, "row_data", []) or []):
            code = str(row.get("代码", "")).strip()
            name = str(row.get("名称", "")).strip()
            if code and (not name or name == code):
                resolved = str(code2name.get(code, code)).strip()
                if resolved and resolved != name:
                    updates.append((row_index, resolved))

        if not updates:
            return False

        def _apply_name_updates() -> None:
            for row_index, resolved in updates:
                self.model.set_cell_value(row_index, "名称", resolved, record_flash=False)

        self._run_coalesced_model_update(
            _apply_name_updates,
            changed_headers={"名称"},
            reason="name_refresh",
        )
        return True

    def _do_vcp_calc(self):
        """实际计算"""
        if self._closing:
            return
        allow_noninteractive = bool(getattr(self, "_vcp_calc_allow_noninteractive", False))
        self._vcp_calc_allow_noninteractive = False
        if not allow_noninteractive and not self._is_active_workspace_tab_for_vcp():
            self._pending_vcp_calc = True
            return
        if _start_watchlist_vcp_refresh(self, allow_noninteractive=allow_noninteractive):
            return
        preload = getattr(self, "_background_preload", None)
        if allow_noninteractive and preload is not None and preload.vcp_pending:
            preload.vcp_pending = False
            preload.vcp_committed = True
            preload.complete = True

    # ================================================================
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称、拼音首字母"""
        self.set_proxy_filter_text(self.proxy_model, text)
        self._update_status_summary()

    def _on_rt_quotes_direct(self, quotes: Mapping[str, Mapping[str, object]]):
        super()._on_rt_quotes_direct(quotes)
        if not self.isVisible() or not quotes:
            return
        if self._touch_watchlist_update():
            self._update_status_summary()


_WATCHLIST_MODULE_IMPORT_MS = (time.perf_counter() - _WATCHLIST_MODULE_IMPORT_STARTED_AT) * 1000.0
