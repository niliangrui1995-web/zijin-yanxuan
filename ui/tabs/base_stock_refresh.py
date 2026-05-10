# -*- coding: utf-8 -*-
"""BaseStockTab 刷新编排辅助。

将报价回灌、市值补全、全局信号订阅从 BaseStockTab 主类中拆开，
避免公共基类继续堆叠 UI、系统集成和刷新调度职责。
"""

from __future__ import annotations

import logging
import os
import threading
import weakref
from importlib import import_module

try:
    from PyQt6 import sip
except ImportError:  # pragma: no cover - PyQt runtime always provides sip.
    sip = None
from PyQt6.QtCore import QCoreApplication, QTimer

from app.services import FINANCE_CACHE_FILE, batch_get_finance_info, load_local_tdx_capital_snapshot
from app.services.ui_runtime_service import (
    SHARED_MARKET_CAPS,
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    is_a_share_code,
    publish_rt_quotes,
    task_id_of,
    task_registry,
)
from core.ui_stall_probe import ui_stall_span

_FINANCE_CACHE_LOCK = threading.RLock()
_FINANCE_CACHE_PATH: str | None = None
_FINANCE_CACHE_SIGNATURE: tuple[int, int] | None = None
_FINANCE_CACHE_PAYLOAD: dict | None = None


def _is_qt_object_deleted(obj) -> bool:
    """Return True when a weakly-held Qt wrapper has already been destroyed."""
    if obj is None or sip is None:
        return obj is None
    try:
        return bool(sip.isdeleted(obj))
    except (AttributeError, RuntimeError, TypeError):
        return False


def _is_owner_runtime_active(owner) -> bool:
    return not _is_qt_object_deleted(owner) and not bool(getattr(owner, "_runtime_cleanup_done", False))


def _should_prime_local_snapshot(owner, *, async_local: bool) -> bool:
    if not async_local:
        return True
    is_visible = getattr(owner, "isVisible", None)
    if callable(is_visible):
        try:
            return bool(is_visible())
        except (RuntimeError, TypeError):
            return False
    return True


def _current_finance_cache_file() -> str:
    try:
        vcp_constants = import_module("vcp.constants")
        return str(getattr(vcp_constants, "FINANCE_CACHE_FILE", FINANCE_CACHE_FILE))
    except (ImportError, RuntimeError, TypeError, ValueError):
        return str(FINANCE_CACHE_FILE)


def collect_table_codes(owner, current_model=None) -> list[str]:
    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return []

    codes = []
    for row_dict in getattr(model, "row_data", []) or []:
        code = owner._normalize_quote_code(row_dict.get("代码", ""))
        if not code:
            continue
        if code.isdigit():
            code = code.zfill(6)
        codes.append(code)
    return list(dict.fromkeys(codes))


def collect_quote_refresh_codes(owner, current_model=None, force: bool = False) -> list[str]:
    model = current_model or owner._resolve_active_quote_model()
    codes = collect_table_codes(owner, model)
    if force or not model:
        return codes

    target_codes = []
    for row_dict in getattr(model, "row_data", []) or []:
        code = owner._normalize_quote_code(row_dict.get("代码", ""))
        if not code:
            continue
        if code.isdigit():
            code = code.zfill(6)

        if not is_a_share_code(code):
            continue

        price_blank = owner._is_blank_quote_value(
            row_dict.get("现价", row_dict.get("市价"))
        )
        pct_blank = owner._is_blank_quote_value(
            row_dict.get("涨幅%", row_dict.get("涨幅")),
            zero_is_blank=False,
        )
        if price_blank or pct_blank:
            target_codes.append(code)

    return list(dict.fromkeys(target_codes))


def collect_missing_finance_codes(owner, current_model=None) -> list[str]:
    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return []

    try:
        from core.global_store import global_store

        snapshot = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        snapshot = {}

    missing = []
    for row_dict in getattr(model, "row_data", []) or []:
        code = owner._normalize_quote_code(row_dict.get("代码", ""))
        if not code:
            continue
        if code.isdigit():
            code = code.zfill(6)
        if not is_a_share_code(code):
            continue

        snapshot_entry = snapshot.get(code) or {}
        row_zbg = coerce_number(row_dict.get("_zongguben", 0))
        snapshot_zbg = coerce_number(snapshot_entry.get("_zongguben") or snapshot_entry.get("zongguben"))
        if row_zbg <= 0 and snapshot_zbg <= 0:
            missing.append(code)

    return list(dict.fromkeys(missing))


def load_cached_finance_snapshot(codes, *, tdx_vipdoc: str | None = None) -> dict[str, dict]:
    normalized_codes = [
        str(code or "").strip()
        for code in dict.fromkeys(codes or [])
        if is_a_share_code(code)
    ]
    if not normalized_codes:
        return {}

    finance_snapshot: dict[str, dict] = {}
    if tdx_vipdoc:
        try:
            finance_snapshot.update(load_local_tdx_capital_snapshot(normalized_codes, tdx_vipdoc))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass

    try:
        cache_payload = _load_shared_finance_cache_payload(_current_finance_cache_file())
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return finance_snapshot

    for code in normalized_codes:
        cached_entry = dict(cache_payload.get(code) or {})
        info = dict(cached_entry.get("info") or {})
        if not info:
            continue
        merged = finance_snapshot.setdefault(code, {})
        for key, value in info.items():
            merged.setdefault(key, value)
        merged.setdefault("source", "finance_cache")
    return finance_snapshot


def _get_finance_cache_signature(path: str) -> tuple[int, int] | None:
    try:
        stat_result = os.stat(path)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return (int(stat_result.st_mtime_ns), int(stat_result.st_size))


def _load_shared_finance_cache_payload(path: str) -> dict:
    global _FINANCE_CACHE_PATH, _FINANCE_CACHE_SIGNATURE, _FINANCE_CACHE_PAYLOAD

    signature = _get_finance_cache_signature(path)
    with _FINANCE_CACHE_LOCK:
        if (
            _FINANCE_CACHE_PATH == path
            and _FINANCE_CACHE_SIGNATURE == signature
            and _FINANCE_CACHE_PAYLOAD is not None
        ):
            return _FINANCE_CACHE_PAYLOAD

        if signature is None:
            _FINANCE_CACHE_PATH = path
            _FINANCE_CACHE_SIGNATURE = None
            _FINANCE_CACHE_PAYLOAD = {}
            return _FINANCE_CACHE_PAYLOAD

        from core.json_cache import load_json_file

        payload = load_json_file(path) or {}
        _FINANCE_CACHE_PATH = path
        _FINANCE_CACHE_SIGNATURE = signature
        _FINANCE_CACHE_PAYLOAD = dict(payload)
        return _FINANCE_CACHE_PAYLOAD


def _resolve_cached_finance_loader(owner):
    if not _is_owner_runtime_active(owner):
        return lambda _codes: {}

    loader = getattr(owner, "_load_cached_finance_snapshot", None)
    if callable(loader):
        return loader

    def _load(codes):
        if not _is_owner_runtime_active(owner):
            return {}
        data_provider = getattr(owner, "data_provider", None)
        return load_cached_finance_snapshot(
            codes,
            tdx_vipdoc=getattr(data_provider, "tdx_vipdoc", None),
        )

    return _load


def _collect_local_quote_targets(owner, model, latest_quotes: dict | None = None) -> list[str]:
    latest_quotes = latest_quotes or {}
    target_codes: list[str] = []
    for code in collect_table_codes(owner, model):
        if not is_a_share_code(code):
            continue
        snapshot_entry = dict(latest_quotes.get(code) or {})
        has_price = (
            coerce_number(snapshot_entry.get("close")) > 0
            or coerce_number(snapshot_entry.get("last_close")) > 0
        )
        has_cap = (
            coerce_number(snapshot_entry.get("_zongguben") or snapshot_entry.get("zongguben")) > 0
            or coerce_number(snapshot_entry.get("market_cap")) > 0
        )
        if not has_price or not has_cap:
            target_codes.append(code)
    return list(dict.fromkeys(target_codes))


def _build_local_quote_payload(owner, target_codes: list[str]) -> dict:
    if not _is_owner_runtime_active(owner):
        return {}

    offline_quotes = {}
    try:
        offline_builder = getattr(getattr(owner, "data_provider", None), "_build_offline_quotes", None)
    except RuntimeError:
        return {}
    if callable(offline_builder):
        try:
            offline_quotes = offline_builder(target_codes) or {}
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            offline_quotes = {}

    finance_loader = _resolve_cached_finance_loader(owner)
    try:
        finance_snapshot = finance_loader(target_codes) or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        finance_snapshot = {}

    return enrich_quotes_with_finance(offline_quotes, finance_snapshot)


def prime_local_quote_snapshot(owner, current_model=None) -> dict:
    if not _is_owner_runtime_active(owner):
        return {}

    if current_model is not None:
        owner._active_model_ref = current_model

    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return {}

    try:
        from core.global_store import global_store

        latest_quotes = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        latest_quotes = {}

    target_codes = _collect_local_quote_targets(owner, model, latest_quotes)
    if not target_codes:
        return {}

    warm_payload = _build_local_quote_payload(owner, target_codes)
    if not warm_payload:
        return {}

    published = publish_rt_quotes(
        warm_payload,
        source=f"{owner.__class__.__name__}.local_cache",
    )
    return published


def prime_local_quote_snapshot_async(owner, current_model=None) -> bool:
    if not _is_owner_runtime_active(owner):
        return False

    if current_model is not None:
        owner._active_model_ref = current_model

    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return False

    try:
        from core.global_store import global_store

        latest_quotes = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        latest_quotes = {}

    target_codes = _collect_local_quote_targets(owner, model, latest_quotes)
    if not target_codes:
        return False

    app = QCoreApplication.instance()
    if app is None or app.closingDown():
        return False

    from app.services.ui_runtime_service import background_job_runner as task_manager

    task_signature = abs(hash(tuple(target_codes)))
    task_key = task_registry.quote_refresh(
        f"{owner.__class__.__name__.lower()}_{id(owner)}_local_quote_snapshot_{task_signature}"
    )
    is_active_task = getattr(task_manager, "is_active_task", None)
    if callable(is_active_task) and is_active_task(task_key):
        return True

    owner_ref = weakref.ref(owner)
    owner_class_name = owner.__class__.__name__
    target_codes = list(target_codes)

    def _bg_local_quote():
        owner_obj = owner_ref()
        if not _is_owner_runtime_active(owner_obj):
            return {}
        app_obj = QCoreApplication.instance()
        if app_obj is None or app_obj.closingDown():
            return {}
        try:
            return _build_local_quote_payload(owner_obj, target_codes)
        except RuntimeError:
            return {}

    def _on_success(warm_payload):
        owner_obj = owner_ref()
        if not _is_owner_runtime_active(owner_obj) or not warm_payload:
            return
        published = publish_rt_quotes(
            warm_payload,
            source=f"{owner_class_name}.local_cache_async",
        )
        if published:
            try:
                owner_obj._apply_quote_snapshot(published)
                _invoke_after_market_caps_updated(owner_obj)
            except RuntimeError:
                pass

    def _on_error(error_message: str):
        if error_message:
            logging.getLogger(__name__).debug(
                f"[{owner_class_name}] local quote snapshot task failed: {error_message}"
            )

    task_manager.run_in_background(
        _bg_local_quote,
        task_id=task_key,
        on_success=_on_success,
        on_error=_on_error,
    )
    return True


def _invoke_after_market_caps_updated(owner) -> None:
    after_cap_hook = getattr(owner, "_after_market_caps_updated", None)
    if callable(after_cap_hook):
        try:
            after_cap_hook()
        except (AttributeError, RuntimeError, TypeError):
            pass


class MarketCapRefreshBatcher:
    """跨 Tab 合并缺失股本请求，避免重复 IO。"""

    _task_id = SHARED_MARKET_CAPS.task_id
    _debounce_ms = 40
    _scheduled = False
    _pending_codes: set[str] = set()
    _waiters: dict[int, tuple[weakref.ReferenceType, set[str]]] = {}

    @classmethod
    def enqueue(cls, owner, codes: list[str]) -> None:
        cleaned = {
            str(code or "").strip()
            for code in codes or []
            if str(code or "").strip()
        }
        if not cleaned:
            _invoke_after_market_caps_updated(owner)
            return

        owner_id = id(owner)
        waiter_ref, waiter_codes = cls._waiters.get(owner_id, (weakref.ref(owner), set()))
        waiter_codes.update(cleaned)
        cls._waiters[owner_id] = (waiter_ref, waiter_codes)
        cls._pending_codes.update(cleaned)
        cls._schedule_flush()

    @classmethod
    def _schedule_flush(cls) -> None:
        if cls._scheduled:
            return

        app = QCoreApplication.instance()
        if app is None or app.closingDown():
            return

        cls._scheduled = True
        QTimer.singleShot(cls._debounce_ms, cls.flush)

    @classmethod
    def _prune_waiters(cls) -> None:
        dead_waiters = [owner_id for owner_id, (owner_ref, _) in cls._waiters.items() if owner_ref() is None]
        for owner_id in dead_waiters:
            cls._waiters.pop(owner_id, None)

    @classmethod
    def _snapshot_waiters(cls) -> dict[int, tuple[weakref.ReferenceType, set[str]]]:
        cls._prune_waiters()
        waiters = cls._waiters
        cls._waiters = {}
        return waiters

    @classmethod
    def _notify_waiters(cls, waiters: dict[int, tuple[weakref.ReferenceType, set[str]]], payload: dict | None = None) -> None:
        payload = dict(payload or {})
        for owner_ref, requested_codes in waiters.values():
            owner = owner_ref()
            if not _is_owner_runtime_active(owner):
                continue
            owner_payload = {
                code: payload[code]
                for code in requested_codes
                if code in payload
            }
            if owner_payload:
                owner._apply_quote_snapshot(owner_payload)
            _invoke_after_market_caps_updated(owner)

    @classmethod
    def flush(cls) -> None:
        cls._scheduled = False
        if not cls._pending_codes:
            return

        app = QCoreApplication.instance()
        if app is None or app.closingDown():
            cls._pending_codes.clear()
            cls._waiters.clear()
            return

        from app.services.ui_runtime_service import background_job_runner as task_manager

        is_active_task = getattr(task_manager, "is_active_task", None)
        if callable(is_active_task) and is_active_task(cls._task_id):
            cls._schedule_flush()
            return

        batch_codes = sorted(cls._pending_codes)
        waiters = cls._snapshot_waiters()
        cls._pending_codes.clear()

        def _bg_cap():
            app_obj = QCoreApplication.instance()
            if app_obj is None or app_obj.closingDown():
                return {}

            try:
                return batch_get_finance_info(batch_codes)
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logging.getLogger(__name__).error(f"[市值统一刷新] 获取股本失败: {exc}")
                return {}

        def _on_success(finance_data):
            published = {}
            if finance_data:
                payload = build_finance_quote_payload(finance_data)
                if payload:
                    published = publish_rt_quotes(
                        payload,
                        source="MarketCapRefreshBatcher.finance",
                    )
            cls._notify_waiters(waiters, published)
            if cls._pending_codes:
                cls._schedule_flush()

        def _on_error(error_message: str):
            if error_message:
                logging.getLogger(__name__).debug(f"[市值统一刷新] 批量任务失败: {error_message}")
            cls._notify_waiters(waiters, {})
            if cls._pending_codes:
                cls._schedule_flush()

        task_manager.run_in_background(
            _bg_cap,
            task_id=SHARED_MARKET_CAPS,
            on_success=_on_success,
            on_error=_on_error,
        )


def refresh_table_quotes_and_market_caps(
    owner,
    current_model=None,
    force_quotes: bool = False,
    quote_task_id=None,
    *,
    async_local: bool = False,
) -> None:
    if current_model is not None:
        owner._active_model_ref = current_model

    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return

    codes = collect_table_codes(owner, model)
    if not codes:
        return

    if _should_prime_local_snapshot(owner, async_local=async_local):
        if async_local:
            prime_local_quote_snapshot_async(owner, model)
        else:
            prime_local_quote_snapshot(owner, model)

    try:
        from core.global_store import global_store

        snapshot = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        snapshot = {}

    quote_subset = {
        code: dict(snapshot[code])
        for code in codes
        if code in snapshot
    }
    if quote_subset:
        owner._apply_quote_snapshot(quote_subset)

    owner.async_update_market_caps()

    if not owner.data_provider or not hasattr(owner.data_provider, "fetch_realtime_quotes_batch"):
        return

    target_codes = collect_quote_refresh_codes(owner, model, force=force_quotes)
    if not target_codes:
        return

    from app.services.ui_runtime_service import background_job_runner as task_manager

    task_id = task_id_of(quote_task_id)
    if not task_id:
        task_id = task_registry.quote_refresh(owner.__class__.__name__.lower()).task_id
    is_active_task = getattr(task_manager, "is_active_task", None)
    if callable(is_active_task) and is_active_task(task_id):
        return

    def _bg_task():
        return owner.data_provider.fetch_realtime_quotes_batch(target_codes)

    def _on_success(quotes):
        if quotes:
            published = owner._publish_quote_payload(
                quotes,
                source=f"{owner.__class__.__name__}.quotes",
            )
            owner._apply_quote_snapshot(published or quotes)

    def _on_error(error_message: str):
        if error_message:
            logging.getLogger(__name__).debug(
                f"[{owner.__class__.__name__}] 表格补价失败: {error_message}"
            )

    task_manager.run_in_background(
        _bg_task,
        on_success=_on_success,
        on_error=_on_error,
        task_id=task_id,
    )


def refresh_table_from_latest_snapshot(owner, current_model=None, *, async_local: bool = True) -> None:
    with ui_stall_span(
        "BaseStockRefresh.refresh_table_from_latest_snapshot",
        tab=owner.__class__.__name__,
        signal="cache_snapshot",
    ):
        _refresh_table_from_latest_snapshot_impl(owner, current_model=current_model, async_local=async_local)


def _refresh_table_from_latest_snapshot_impl(owner, current_model=None, *, async_local: bool = True) -> None:
    if current_model is not None:
        owner._active_model_ref = current_model

    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return

    codes = collect_table_codes(owner, model)
    if not codes:
        return

    if _should_prime_local_snapshot(owner, async_local=async_local):
        if async_local:
            prime_local_quote_snapshot_async(owner, model)
        else:
            prime_local_quote_snapshot(owner, model)

    try:
        from core.global_store import global_store

        snapshot = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        snapshot = {}

    if not snapshot:
        return

    quote_subset = {
        code: dict(snapshot[code])
        for code in codes
        if code in snapshot
    }
    if quote_subset:
        owner._apply_quote_snapshot(quote_subset)


def subscribe_global_quotes(owner, current_model=None) -> None:
    if current_model:
        owner._active_model_ref = current_model

    model = owner._resolve_active_quote_model()
    if model and hasattr(model, "update_quotes"):
        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot = {}
        if snapshot:
            if owner.isVisible():
                model.update_quotes(snapshot)
            else:
                owner._deferred_quote_refresh = True

    try:
        owner._quote_signal_connected
    except AttributeError:
        owner._quote_signal_connected = False

    if owner._quote_signal_connected:
        try:
            from app.services.ui_runtime_service import domain_events as event_bus

            event_bus.sig_rt_quotes.disconnect(owner._on_rt_quotes_direct)
        except (TypeError, RuntimeError):
            pass

    from app.services.ui_runtime_service import domain_events as event_bus

    event_bus.sig_rt_quotes.connect(owner._on_rt_quotes_direct)
    owner._quote_signal_connected = True


def on_rt_quotes_direct(owner, quotes: dict) -> None:
    with ui_stall_span(
        "BaseStockRefresh.on_rt_quotes_direct",
        tab=owner.__class__.__name__,
        signal="sig_rt_quotes",
    ):
        if not owner.isVisible():
            owner._deferred_quote_refresh = True
            return

        owner._apply_quote_snapshot(quotes)


def replay_deferred_quotes(owner) -> None:
    with ui_stall_span(
        "BaseStockRefresh.replay_deferred_quotes",
        tab=owner.__class__.__name__,
        signal="showEvent",
    ):
        if not owner._deferred_quote_refresh:
            return

        owner._deferred_quote_refresh = False
        try:
            from core.global_store import global_store

            owner._apply_quote_snapshot(global_store.get_latest_quotes())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def async_update_market_caps(owner) -> None:
    if not _is_owner_runtime_active(owner):
        return

    app = QCoreApplication.instance()
    owner_window = owner.window()
    if app is None or app.closingDown():
        return
    if owner_window and getattr(owner_window, "_is_closing", False):
        return

    model = owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return

    try:
        from core.global_store import global_store

        latest_quotes = global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        latest_quotes = {}

    if latest_quotes:
        owner._apply_quote_snapshot(latest_quotes)

    codes_need_cap = collect_missing_finance_codes(owner, model)
    if not codes_need_cap:
        _invoke_after_market_caps_updated(owner)
        return

    MarketCapRefreshBatcher.enqueue(owner, codes_need_cap)
