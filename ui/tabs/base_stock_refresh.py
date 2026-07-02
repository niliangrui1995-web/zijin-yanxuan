# -*- coding: utf-8 -*-
"""BaseStockTab 刷新编排辅助。

将报价回灌、市值补全、全局信号订阅从 BaseStockTab 主类中拆开，
避免公共基类继续堆叠 UI、系统集成和刷新调度职责。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import weakref

try:
    from PyQt6 import sip
except ImportError:  # pragma: no cover - PyQt runtime always provides sip.
    sip = None
from PyQt6.QtCore import QCoreApplication, QTimer

from app.services.runtime_constants import FINANCE_CACHE_FILE
from app.services.runtime_services import load_local_tdx_capital_snapshot
from app.services.scan_runtime_service import batch_get_finance_info
from app.services.ui_diagnostics_service import ui_stall_span
from app.services.ui_quote_service import (
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    is_a_share_code,
    publish_rt_quotes,
)
from app.services.ui_task_service import (
    SHARED_MARKET_CAPS,
    task_id_of,
    task_registry,
)
from core.observability import record_metric

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

        price_blank = owner._is_blank_quote_value(row_dict.get("现价", row_dict.get("市价")))
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
    normalized_codes = [str(code or "").strip() for code in dict.fromkeys(codes or []) if is_a_share_code(code)]
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


def _finance_entry_has_share_capital(entry: dict | None) -> bool:
    return coerce_number((entry or {}).get("_zongguben") or (entry or {}).get("zongguben")) > 0


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
        if _FINANCE_CACHE_PATH == path and _FINANCE_CACHE_SIGNATURE == signature and _FINANCE_CACHE_PAYLOAD is not None:
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
            coerce_number(snapshot_entry.get("close")) > 0 or coerce_number(snapshot_entry.get("last_close")) > 0
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

    from app.services.ui_task_service import background_job_runner as task_manager

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
            logging.getLogger(__name__).debug(f"[{owner_class_name}] local quote snapshot task failed: {error_message}")

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


def _should_defer_cache_snapshot_apply(owner, *, async_local: bool, force_apply: bool = False) -> bool:
    if not async_local or not _is_owner_runtime_active(owner):
        return False

    is_f5_refresh = bool(force_apply or getattr(owner, "_f5_cache_snapshot_apply", False))
    if not is_f5_refresh:
        is_visible = getattr(owner, "isVisible", None)
        if not callable(is_visible):
            return False
        try:
            if not is_visible():
                return False
        except (RuntimeError, TypeError):
            return False

    app = QCoreApplication.instance()
    if app is None or app.closingDown():
        return False
    return True


_QUOTE_SNAPSHOT_APPLY_CHUNK_SIZE = 48
_QUOTE_ROW_CODE_KEYS = ("\u4ee3\u7801", "浠ｇ爜", "code", "symbol")


def _stable_signature_value(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _stable_signature_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_stable_signature_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_stable_signature_value(item) for item in value))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _quote_code_candidates(owner, raw_code) -> list[str]:
    raw = str(raw_code or "").strip()
    candidates = []
    if raw:
        candidates.append(raw)
        if raw.isdigit():
            candidates.append(raw.zfill(6))

    normalize_code = getattr(owner, "_normalize_quote_code", None)
    if callable(normalize_code):
        try:
            normalized = str(normalize_code(raw_code) or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            normalized = ""
        if normalized:
            candidates.append(normalized)
            if normalized.isdigit():
                candidates.append(normalized.zfill(6))
            else:
                candidates.append(normalized.upper())
    return list(dict.fromkeys(candidates))


def _row_signature_by_code(owner, model, payload: dict) -> dict[str, tuple]:
    row_data = getattr(model, "row_data", None) or []
    headers = list(getattr(model, "headers", None) or getattr(model, "_headers", None) or [])
    wanted_codes = set(payload or {})
    signatures: dict[str, tuple] = {}

    for row in row_data:
        if not isinstance(row, dict):
            continue
        matched_code = ""
        for key in _QUOTE_ROW_CODE_KEYS:
            for code in _quote_code_candidates(owner, row.get(key, "")):
                if code in wanted_codes:
                    matched_code = code
                    break
            if matched_code:
                break
        if not matched_code or matched_code in signatures:
            continue

        display_values = tuple((header, _stable_signature_value(row.get(header))) for header in headers)
        internal_values = (
            ("_zongguben", _stable_signature_value(row.get("_zongguben"))),
            ("_history_date", _stable_signature_value(row.get("_history_date"))),
        )
        signatures[matched_code] = display_values + internal_values
        if len(signatures) == len(wanted_codes):
            break

    return signatures


def _payload_signature_for_codes(owner, payload: dict) -> dict[str, tuple]:
    model = getattr(owner, "_active_model_ref", None)
    if model is None:
        resolver = getattr(owner, "_resolve_active_quote_model", None)
        model = resolver() if callable(resolver) else None
    row_signatures = _row_signature_by_code(owner, model, payload) if model is not None else {}
    signatures: dict[str, tuple] = {}
    for code, quote in dict(payload or {}).items():
        signatures[str(code)] = (
            _stable_signature_value(dict(quote or {})),
            row_signatures.get(str(code)),
        )
    return signatures


def _filter_unchanged_cache_snapshot_payload(owner, payload: dict) -> dict:
    signatures = _payload_signature_for_codes(owner, payload)
    if not signatures:
        return {}
    payload_by_code = {str(code): dict(quote or {}) for code, quote in dict(payload or {}).items()}
    previous = getattr(owner, "_cache_snapshot_applied_signatures", None)
    if not isinstance(previous, dict):
        return payload_by_code
    return {code: payload_by_code[code] for code, signature in signatures.items() if previous.get(code) != signature}


def _remember_cache_snapshot_payload(owner, payload: dict) -> None:
    if not _is_owner_runtime_active(owner) or not payload:
        return
    signatures = _payload_signature_for_codes(owner, payload)
    if not signatures:
        return
    previous = getattr(owner, "_cache_snapshot_applied_signatures", None)
    if not isinstance(previous, dict):
        previous = {}
    previous.update(signatures)
    owner._cache_snapshot_applied_signatures = previous


def _extract_changed_rows(result) -> int | None:
    if isinstance(result, dict):
        try:
            return int(result.get("changed_rows"))
        except (TypeError, ValueError):
            return None
    try:
        return int(result)
    except (TypeError, ValueError):
        return None


def _cache_snapshot_apply_chunk_size() -> int:
    try:
        return max(1, int(os.environ.get("VCP_CACHE_SNAPSHOT_APPLY_CHUNK_SIZE", _QUOTE_SNAPSHOT_APPLY_CHUNK_SIZE)))
    except (TypeError, ValueError):
        return _QUOTE_SNAPSHOT_APPLY_CHUNK_SIZE


def _split_payload_chunk(payload: dict, chunk_size: int) -> tuple[dict, dict]:
    items = list(dict(payload or {}).items())
    chunk = dict(items[:chunk_size])
    remaining = dict(items[chunk_size:])
    return chunk, remaining


def _apply_cache_snapshot_payload(owner, payload: dict, *, signal: str) -> None:
    filtered_payload = _filter_unchanged_cache_snapshot_payload(owner, payload)
    if not filtered_payload:
        return

    started_at = time.perf_counter()
    result = None
    with ui_stall_span(
        "BaseStockRefresh.apply_cache_snapshot_batch",
        tab=owner.__class__.__name__,
        signal=signal,
    ):
        result = owner._apply_quote_snapshot(filtered_payload)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    changed_rows = _extract_changed_rows(result)
    record_metric(
        "cache_snapshot_apply_ms",
        elapsed_ms,
        unit="ms",
        tags={
            "tab": owner.__class__.__name__,
            "codes": str(len(filtered_payload)),
            "changed_rows": "" if changed_rows is None else str(changed_rows),
            "signal": signal,
        },
    )
    _remember_cache_snapshot_payload(owner, filtered_payload)


class CacheSnapshotApplyQueue:
    """Apply cache snapshot hits by tab and small code batches across event-loop turns."""

    _scheduled = False
    _pending: dict[int, tuple[weakref.ReferenceType, dict, bool]] = {}

    @classmethod
    def enqueue(cls, owner, payload: dict, *, async_local: bool) -> bool:
        force_apply = bool(getattr(owner, "_f5_cache_snapshot_apply", False))
        if not payload or not _should_defer_cache_snapshot_apply(
            owner,
            async_local=async_local,
            force_apply=force_apply,
        ):
            return False

        owner_id = id(owner)
        pending = cls._pending.get(owner_id)
        if pending is None:
            owner_ref, merged_payload, pending_force_apply = weakref.ref(owner), {}, False
        else:
            owner_ref, merged_payload, pending_force_apply = pending
        merged_payload = dict(merged_payload or {})
        for code, quote in dict(payload or {}).items():
            merged_payload[code] = dict(quote or {})
        cls._pending[owner_id] = (owner_ref, merged_payload, bool(pending_force_apply or force_apply))
        cls._schedule()
        return True

    @classmethod
    def _schedule(cls) -> None:
        if cls._scheduled:
            return

        app = QCoreApplication.instance()
        if app is None or app.closingDown():
            cls._pending.clear()
            return

        cls._scheduled = True
        QTimer.singleShot(0, cls.flush_one)

    @classmethod
    def flush_one(cls) -> None:
        cls._scheduled = False
        if not cls._pending:
            return

        app = QCoreApplication.instance()
        if app is None or app.closingDown():
            cls._pending.clear()
            return

        owner_id = next(iter(cls._pending))
        owner_ref, payload, force_apply = cls._pending.pop(owner_id)
        owner = owner_ref()
        if _should_defer_cache_snapshot_apply(owner, async_local=True, force_apply=force_apply):
            chunk, remaining = _split_payload_chunk(payload, _cache_snapshot_apply_chunk_size())
            if remaining:
                cls._pending[owner_id] = (owner_ref, remaining, force_apply)
            _apply_cache_snapshot_payload(owner, chunk, signal="cache_snapshot")

        if cls._pending:
            cls._schedule()


class MarketCapRefreshBatcher:
    """跨 Tab 合并缺失股本请求，避免重复 IO。"""

    _task_id = SHARED_MARKET_CAPS.task_id
    _debounce_ms = 40
    _scheduled = False
    _pending_codes: set[str] = set()
    _waiters: dict[int, tuple[weakref.ReferenceType, set[str]]] = {}

    @classmethod
    def enqueue(cls, owner, codes: list[str]) -> None:
        cleaned = {str(code or "").strip() for code in codes or [] if str(code or "").strip()}
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
    def _notify_waiters(
        cls, waiters: dict[int, tuple[weakref.ReferenceType, set[str]]], payload: dict | None = None
    ) -> None:
        payload = dict(payload or {})
        for owner_ref, requested_codes in waiters.values():
            owner = owner_ref()
            if not _is_owner_runtime_active(owner):
                continue
            owner_payload = {code: payload[code] for code in requested_codes if code in payload}
            if owner_payload:
                owner._apply_quote_snapshot(owner_payload)
            _invoke_after_market_caps_updated(owner)

    @classmethod
    def _load_waiter_finance_snapshot(
        cls, waiters: dict[int, tuple[weakref.ReferenceType, set[str]]], batch_codes: list[str]
    ) -> tuple[dict, list[str]]:
        finance_data: dict[str, dict] = {}
        missing_codes = set(batch_codes)
        for owner_ref, requested_codes in waiters.values():
            owner = owner_ref()
            if not _is_owner_runtime_active(owner):
                continue

            owner_codes = sorted(missing_codes.intersection(requested_codes))
            if not owner_codes:
                continue

            try:
                loader = getattr(owner, "_load_cached_finance_snapshot", None)
                if callable(loader):
                    snapshot = loader(owner_codes) or {}
                else:
                    data_provider = getattr(owner, "data_provider", None)
                    tdx_vipdoc = getattr(data_provider, "tdx_vipdoc", None)
                    snapshot = load_cached_finance_snapshot(owner_codes, tdx_vipdoc=tdx_vipdoc) if tdx_vipdoc else {}
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                snapshot = {}

            for code in owner_codes:
                entry = snapshot.get(code)
                if _finance_entry_has_share_capital(entry):
                    finance_data[code] = dict(entry or {})
                    missing_codes.discard(code)

        return finance_data, sorted(missing_codes)

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

        from app.services.ui_task_service import background_job_runner as task_manager

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
                finance_data, missing_codes = cls._load_waiter_finance_snapshot(waiters, batch_codes)
                if missing_codes:
                    finance_data.update(batch_get_finance_info(missing_codes) or {})
                return finance_data
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

    quote_subset = {code: dict(snapshot[code]) for code in codes if code in snapshot}
    if quote_subset:
        owner._apply_quote_snapshot(quote_subset)

    owner.async_update_market_caps()

    if not owner.data_provider or not hasattr(owner.data_provider, "fetch_realtime_quotes_batch"):
        return

    target_codes = collect_quote_refresh_codes(owner, model, force=force_quotes)
    if not target_codes:
        return

    from app.services.ui_task_service import background_job_runner as task_manager

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
            logging.getLogger(__name__).debug(f"[{owner.__class__.__name__}] 表格补价失败: {error_message}")

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

    quote_subset = {code: dict(snapshot[code]) for code in codes if code in snapshot}
    if quote_subset:
        if not CacheSnapshotApplyQueue.enqueue(owner, quote_subset, async_local=async_local):
            _apply_cache_snapshot_payload(
                owner,
                quote_subset,
                signal="cache_snapshot" if async_local else "cache_snapshot_sync",
            )


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
            from app.services.ui_event_service import domain_events as event_bus

            event_bus.sig_rt_quotes.disconnect(owner._on_rt_quotes_direct)
        except (TypeError, RuntimeError):
            pass

    from app.services.ui_event_service import domain_events as event_bus

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
