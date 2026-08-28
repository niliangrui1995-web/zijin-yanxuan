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
from collections.abc import Mapping
from contextlib import suppress

try:
    from PyQt6 import sip
except ImportError:  # pragma: no cover - PyQt runtime always provides sip.
    sip = None
from PyQt6.QtCore import QCoreApplication, QTimer

from app.services.runtime_constants import FINANCE_CACHE_FILE
from app.services.ui_diagnostics_service import ui_stall_span
from app.services.ui_json_cache_service import cache_file_signature, load_json_file
from app.services.ui_quote_service import (
    build_finance_quote_payload,
    build_offline_quotes,
    coerce_number,
    enrich_quotes_with_finance,
    get_total_shares,
    is_a_share_code,
    publish_rt_quotes,
)
from app.services.ui_task_lifecycle_service import invoke_with_cancellation, task_lifecycle_for
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


def load_local_tdx_capital_snapshot(codes, tdx_vipdoc):
    from app.services.runtime_services import load_local_tdx_capital_snapshot as load_snapshot

    return load_snapshot(codes, tdx_vipdoc)


def batch_get_finance_info(codes):
    from app.services.scan_runtime_service import batch_get_finance_info as load_finance

    return load_finance(codes)


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


def _owner_accepts_hidden_quote_projection(owner) -> bool:
    """Whether a tab keeps its model current while its widget is hidden.

    Ordinary tabs retain the historical defer-until-visible behavior.  Watchlist
    opts in explicitly so a tab return has one coherent model frame instead of
    a required show paint followed by a bulk quote repaint.
    """
    if not _is_owner_runtime_active(owner):
        return False
    checker = getattr(owner, "accepts_hidden_quote_projection", None)
    if callable(checker):
        try:
            return bool(checker())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
    return bool(getattr(owner, "_hidden_quote_projection_enabled", False))


def _owner_is_visible(owner) -> bool:
    is_visible = getattr(owner, "isVisible", None)
    if not callable(is_visible):
        return True
    try:
        return bool(is_visible())
    except (RuntimeError, TypeError):
        return False


def _owner_is_presentation_active(owner) -> bool:
    """Return whether a quote delta is allowed to create visible effects."""
    if not _owner_is_visible(owner):
        return False
    if not _owner_accepts_hidden_quote_projection(owner):
        return True
    return bool(getattr(owner, "_workspace_active", False))


def _apply_owner_quote_snapshot(owner, payload, *, record_flash: bool | None = None):
    """Apply a quote payload with presentation effects only on an active tab."""
    if record_flash is None:
        record_flash = (
            True
            if not _owner_accepts_hidden_quote_projection(owner)
            else _owner_is_presentation_active(owner)
        )
    apply_snapshot = getattr(owner, "_apply_quote_snapshot", None)
    if not callable(apply_snapshot):
        return None
    if record_flash:
        return apply_snapshot(payload)
    try:
        return apply_snapshot(payload, record_flash=False)
    except TypeError:
        # Keep compatible with unrelated legacy tab implementations.  The only
        # opt-in production tab is Watchlist, whose model honors this contract.
        return apply_snapshot(payload)


def _should_prime_local_snapshot(owner, *, async_local: bool) -> bool:
    if not async_local:
        return True
    return _owner_is_visible(owner) or _owner_accepts_hidden_quote_projection(owner)


def _latest_quote_snapshot() -> Mapping[str, Mapping[str, object]]:
    try:
        from core.global_store import global_store

        return global_store.get_latest_quotes() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {}


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

    snapshot = _latest_quote_snapshot()

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
        row_zbg = get_total_shares(row_dict)
        snapshot_zbg = get_total_shares(snapshot_entry)
        if row_zbg <= 0 and snapshot_zbg <= 0:
            missing.append(code)

    return list(dict.fromkeys(missing))


def load_cached_finance_snapshot(codes, *, tdx_vipdoc: str | None = None) -> dict[str, dict]:
    normalized_codes = [str(code or "").strip() for code in dict.fromkeys(codes or []) if is_a_share_code(code)]
    if not normalized_codes:
        return {}

    finance_snapshot: dict[str, dict] = {}
    if tdx_vipdoc:
        with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
            finance_snapshot.update(load_local_tdx_capital_snapshot(normalized_codes, tdx_vipdoc))

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
    return get_total_shares(entry) > 0


def _get_finance_cache_signature(path: str) -> tuple[int, int] | None:
    return cache_file_signature(path)


def _load_shared_finance_cache_payload(path: str) -> dict:
    global _FINANCE_CACHE_PATH, _FINANCE_CACHE_SIGNATURE, _FINANCE_CACHE_PAYLOAD

    signature = _get_finance_cache_signature(path)
    with _FINANCE_CACHE_LOCK:
        if path == _FINANCE_CACHE_PATH and signature == _FINANCE_CACHE_SIGNATURE and _FINANCE_CACHE_PAYLOAD is not None:
            return _FINANCE_CACHE_PAYLOAD

        if signature is None:
            _FINANCE_CACHE_PATH = path
            _FINANCE_CACHE_SIGNATURE = None
            _FINANCE_CACHE_PAYLOAD = {}
            return _FINANCE_CACHE_PAYLOAD

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


def _quote_entry_has_price(entry: Mapping[str, object] | None) -> bool:
    payload = dict(entry or {})
    return coerce_number(payload.get("close")) > 0 or coerce_number(payload.get("last_close")) > 0


def _quote_entry_has_cap(entry: Mapping[str, object] | None) -> bool:
    payload = dict(entry or {})
    return (
        get_total_shares(payload) > 0
        or coerce_number(payload.get("market_cap")) > 0
    )


def _normalize_quote_trade_date(value: object) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _f5_market_snapshot_trade_date(owner) -> str:
    if owner is None:
        return ""
    provider_available, provider = _owner_data_provider(owner)
    if not provider_available:
        return ""
    return _normalize_quote_trade_date(getattr(provider, "_market_data_snapshot_trade_date", ""))


def _f5_market_snapshot_identity(owner) -> tuple[str, int]:
    trade_date = _f5_market_snapshot_trade_date(owner)
    if not trade_date:
        return "", 0
    provider_available, provider = _owner_data_provider(owner)
    cache_data = getattr(provider, "cache_data", None) if provider_available else None
    return trade_date, id(cache_data)


def _f5_stale_quote_attempts(owner) -> set[str]:
    identity = _f5_market_snapshot_identity(owner)
    if not identity[0]:
        return set()
    state = getattr(owner, "_f5_stale_local_quote_attempts", None)
    if not isinstance(state, dict) or state.get("identity") != identity:
        return set()
    attempts = state.get("codes")
    return attempts if isinstance(attempts, set) else set()


def _mark_f5_stale_quote_attempt(owner, code: str) -> None:
    identity = _f5_market_snapshot_identity(owner)
    if not identity[0]:
        return
    state = getattr(owner, "_f5_stale_local_quote_attempts", None)
    if not isinstance(state, dict) or state.get("identity") != identity:
        state = {"identity": identity, "codes": set()}
        try:
            setattr(owner, "_f5_stale_local_quote_attempts", state)
        except (AttributeError, RuntimeError, TypeError):
            return
    attempts = state.get("codes")
    if isinstance(attempts, set):
        attempts.add(code)


def _quote_entry_is_older_than_f5_snapshot(owner, entry: Mapping[str, object] | None) -> bool:
    expected_date = _f5_market_snapshot_trade_date(owner)
    quote_date = _normalize_quote_trade_date(dict(entry or {}).get("date"))
    return bool(expected_date and quote_date and quote_date < expected_date)


def _collect_local_quote_targets(
    owner,
    model,
    latest_quotes: Mapping[str, object] | None = None,
) -> list[str]:
    latest_quotes = latest_quotes or {}
    stale_attempts = _f5_stale_quote_attempts(owner)
    target_codes: list[str] = []
    for code in collect_table_codes(owner, model):
        if not is_a_share_code(code):
            continue
        snapshot_entry = _quote_entry_copy(latest_quotes, code)
        is_stale = _quote_entry_is_older_than_f5_snapshot(owner, snapshot_entry)
        if (
            not _quote_entry_has_price(snapshot_entry)
            or not _quote_entry_has_cap(snapshot_entry)
            or (is_stale and code not in stale_attempts)
        ):
            target_codes.append(code)
    return list(dict.fromkeys(target_codes))


def _quote_subset_for_codes(snapshot: Mapping[str, object], codes: list[str]) -> dict:
    return {
        code: dict(payload)
        for code in codes
        if isinstance((payload := snapshot.get(code)), Mapping) and payload
    }


def _prepare_local_quote_prime(owner, current_model=None) -> tuple[list[str], dict] | None:
    if not _is_owner_runtime_active(owner):
        return None
    if current_model is not None:
        owner._active_model_ref = current_model

    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return None

    latest_quotes = _latest_quote_snapshot()
    target_codes = _collect_local_quote_targets(owner, model, latest_quotes)
    if not target_codes:
        return None
    for code in target_codes:
        if _quote_entry_is_older_than_f5_snapshot(owner, _quote_entry_copy(latest_quotes, code)):
            _mark_f5_stale_quote_attempt(owner, code)
    return target_codes, _quote_subset_for_codes(latest_quotes, target_codes)


def _should_prime_f5_local_snapshot(owner, current_model=None) -> bool:
    if not _f5_market_snapshot_trade_date(owner):
        return False
    try:
        model = current_model or owner._resolve_active_quote_model()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    if not model or not hasattr(model, "row_data"):
        return False
    return bool(_collect_local_quote_targets(owner, model, _latest_quote_snapshot()))


def _owner_data_provider(owner) -> tuple[bool, object | None]:
    try:
        return True, owner.data_provider
    except (AttributeError, RuntimeError):
        return False, None


def _build_missing_offline_quotes(
    provider,
    target_codes: list[str],
    latest_quotes: Mapping[str, object],
    *,
    owner=None,
) -> dict:
    missing_price_codes = [
        code
        for code in target_codes
        if (
            not _quote_entry_has_price(_quote_entry_copy(latest_quotes, code))
            or _quote_entry_is_older_than_f5_snapshot(owner, _quote_entry_copy(latest_quotes, code))
        )
    ]
    return build_offline_quotes(provider, missing_price_codes) if missing_price_codes else {}


def _load_local_finance_snapshot(owner, target_codes: list[str]) -> dict:
    finance_loader = _resolve_cached_finance_loader(owner)
    try:
        return finance_loader(target_codes) or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}


def _quote_entry_copy(snapshot: Mapping[str, object], code: str) -> dict:
    entry = snapshot.get(code)
    return dict(entry) if isinstance(entry, Mapping) and entry else {}


def _fresh_quotes_for_merge(
    latest_quotes: Mapping[str, object],
    payload_codes: set[str],
) -> Mapping[str, object]:
    if not latest_quotes or not payload_codes:
        return {}
    return _latest_quote_snapshot()


def _merge_local_quote_payload(
    latest_quotes: Mapping[str, object],
    offline_quotes: Mapping[str, object],
    finance_snapshot: dict,
    *,
    owner=None,
) -> dict:
    payload_codes = set(offline_quotes) | set(finance_snapshot)
    current_quotes = _fresh_quotes_for_merge(latest_quotes, payload_codes)
    quote_payload = {}
    for code in payload_codes:
        current_entry = _quote_entry_copy(current_quotes, code) or _quote_entry_copy(latest_quotes, code)
        offline_entry = _quote_entry_copy(offline_quotes, code)
        if offline_entry and (
            not _quote_entry_has_price(current_entry)
            or _quote_entry_is_older_than_f5_snapshot(owner, current_entry)
        ):
            current_entry.update(offline_entry)
        if current_entry:
            quote_payload[code] = current_entry
    return enrich_quotes_with_finance(quote_payload, finance_snapshot)


def _build_local_quote_payload(
    owner,
    target_codes: list[str],
    *,
    latest_quotes: Mapping[str, object] | None = None,
) -> dict:
    if not _is_owner_runtime_active(owner):
        return {}

    provider_available, provider = _owner_data_provider(owner)
    if not provider_available:
        return {}

    latest_quotes = dict(latest_quotes or {})
    offline_quotes = _build_missing_offline_quotes(
        provider,
        target_codes,
        latest_quotes,
        owner=owner,
    )
    finance_snapshot = _load_local_finance_snapshot(owner, target_codes)
    return _merge_local_quote_payload(
        latest_quotes,
        offline_quotes,
        finance_snapshot,
        owner=owner,
    )


def prime_local_quote_snapshot(owner, current_model=None) -> dict:
    prepared = _prepare_local_quote_prime(owner, current_model)
    if prepared is None:
        return {}
    target_codes, latest_target_quotes = prepared
    warm_payload = _build_local_quote_payload(
        owner,
        target_codes,
        latest_quotes=latest_target_quotes,
    )
    if not warm_payload:
        return {}

    return publish_rt_quotes(
        warm_payload,
        source=f"{owner.__class__.__name__}.local_cache",
    )


def _run_owner_background(
    owner,
    runner,
    name,
    fn,
    *,
    task_id,
    timeout_sec,
    on_success,
    on_error,
    on_terminated=None,
) -> None:
    scheduler_kwargs = {}
    if on_terminated is not None:
        scheduler_kwargs["on_terminated"] = on_terminated
    task_lifecycle_for(owner, runner=runner).run_background(
        name,
        fn,
        task_id=task_id,
        timeout_sec=timeout_sec,
        on_success=on_success,
        on_error=on_error,
        runner=runner,
        **scheduler_kwargs,
    )


def _qt_runtime_available() -> bool:
    app = QCoreApplication.instance()
    return app is not None and not app.closingDown()


def _local_quote_task_key(owner, target_codes: list[str], *, scope: str = "visible"):
    task_signature = abs(hash(tuple(target_codes)))
    normalized_scope = str(scope or "visible").strip().lower()
    scope_suffix = "" if normalized_scope == "visible" else f"_{normalized_scope}"
    return task_registry.quote_refresh(
        f"{owner.__class__.__name__.lower()}_{id(owner)}_local_quote_snapshot{scope_suffix}_{task_signature}"
    )


def _local_quote_task_callbacks(owner, target_codes: list[str], latest_target_quotes: dict):
    owner_ref = weakref.ref(owner)
    owner_class_name = owner.__class__.__name__

    def _bg_local_quote(_cancellation_token):
        owner_obj = owner_ref()
        if not _is_owner_runtime_active(owner_obj) or not _qt_runtime_available():
            return {}
        try:
            return _build_local_quote_payload(
                owner_obj,
                target_codes,
                latest_quotes=latest_target_quotes,
            )
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
        if not published:
            return
        try:
            _apply_owner_quote_snapshot(owner_obj, published)
            _invoke_after_market_caps_updated(owner_obj)
        except RuntimeError:
            pass

    def _on_error(error_message: str):
        if error_message:
            logging.getLogger(__name__).debug(
                f"[{owner_class_name}] local quote snapshot task failed: {error_message}"
            )

    return _bg_local_quote, _on_success, _on_error


def prime_local_quote_snapshot_async(owner, current_model=None) -> bool:
    prepared = _prepare_local_quote_prime(owner, current_model)
    if prepared is None or not _qt_runtime_available():
        return False
    target_codes, latest_target_quotes = prepared

    from app.services.ui_task_service import background_job_runner as task_manager

    task_key = _local_quote_task_key(owner, target_codes)
    is_active_task = getattr(task_manager, "is_active_task", None)
    if callable(is_active_task) and is_active_task(task_key):
        return True

    target_codes = list(target_codes)
    background, on_success, on_error = _local_quote_task_callbacks(
        owner,
        target_codes,
        latest_target_quotes,
    )

    _run_owner_background(
        owner,
        task_manager,
        "local_quote_snapshot",
        background,
        task_id=task_key,
        timeout_sec=60.0,
        on_success=on_success,
        on_error=on_error,
    )
    return True


def _invoke_after_market_caps_updated(owner) -> None:
    after_cap_hook = getattr(owner, "_after_market_caps_updated", None)
    if callable(after_cap_hook):
        with suppress(AttributeError, RuntimeError, TypeError):
            after_cap_hook()


def _should_defer_cache_snapshot_apply(owner, *, async_local: bool, force_apply: bool = False) -> bool:
    if not async_local or not _is_owner_runtime_active(owner):
        return False

    is_f5_refresh = bool(force_apply or getattr(owner, "_f5_cache_snapshot_apply", False))
    if not is_f5_refresh and not _owner_is_visible(owner) and not _owner_accepts_hidden_quote_projection(owner):
        return False

    app = QCoreApplication.instance()
    return not (app is None or app.closingDown())


def _should_defer_cache_snapshot_until_visible(owner, *, async_local: bool, force_apply: bool = False) -> bool:
    """Keep hidden-tab cache projections off the GUI thread until activation."""
    if not async_local or not _is_owner_runtime_active(owner):
        return False
    if bool(force_apply or getattr(owner, "_f5_cache_snapshot_apply", False)):
        return False

    return not _owner_is_visible(owner) and not _owner_accepts_hidden_quote_projection(owner)


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
    signatures: dict[str, list[tuple]] = {}

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
        if not matched_code:
            continue

        display_values = tuple((header, _stable_signature_value(row.get(header))) for header in headers)
        internal_values = (
            ("total_shares", _stable_signature_value(get_total_shares(row))),
            ("_history_date", _stable_signature_value(row.get("_history_date"))),
        )
        signatures.setdefault(matched_code, []).append(display_values + internal_values)

    return {code: tuple(row_signatures) for code, row_signatures in signatures.items()}


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


def _apply_cache_snapshot_payload(
    owner,
    payload: dict,
    *,
    signal: str,
    record_flash: bool | None = None,
) -> None:
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
        result = _apply_owner_quote_snapshot(
            owner,
            filtered_payload,
            record_flash=record_flash,
        )
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

    _continue_interval_ms = 16
    _scheduled = False
    _pending: dict[int, tuple[weakref.ReferenceType, dict, bool]] = {}

    @classmethod
    def enqueue(cls, owner, payload: dict, *, async_local: bool, force_apply: bool = False) -> bool:
        force_apply = bool(force_apply or getattr(owner, "_f5_cache_snapshot_apply", False))
        if not payload or not _should_defer_cache_snapshot_apply(
            owner,
            async_local=async_local,
            force_apply=force_apply,
        ):
            return False

        if not _owner_is_visible(owner) and _owner_accepts_hidden_quote_projection(owner):
            # Never leave hidden Watchlist work in an event-loop queue: a quick
            # return would otherwise turn its first required reveal into a
            # second data-driven full repaint.
            _apply_cache_snapshot_payload(
                owner,
                payload,
                signal="hidden_cache_snapshot",
                record_flash=False,
            )
            return True

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
    def is_pending(cls, owner) -> bool:
        return id(owner) in cls._pending

    @classmethod
    def discard(cls, owner) -> None:
        cls._pending.pop(id(owner), None)

    @classmethod
    def _schedule(cls, delay_ms: int = 0) -> None:
        if cls._scheduled:
            return

        app = QCoreApplication.instance()
        if app is None or app.closingDown():
            cls._pending.clear()
            return

        cls._scheduled = True
        QTimer.singleShot(max(0, int(delay_ms or 0)), cls.flush_one)

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
            hidden_projection = not _owner_is_visible(owner) and _owner_accepts_hidden_quote_projection(owner)
            if hidden_projection:
                _apply_cache_snapshot_payload(
                    owner,
                    chunk,
                    signal="hidden_cache_snapshot",
                    record_flash=False,
                )
            else:
                _apply_cache_snapshot_payload(owner, chunk, signal="cache_snapshot")

        if cls._pending:
            cls._schedule(cls._continue_interval_ms)


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
                _apply_owner_quote_snapshot(owner, owner_payload)
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


def _submit_owner_quote_refresh(owner, task_manager, task_id: str, target_codes: list[str]) -> None:
    owner_ref = weakref.ref(owner)
    provider = owner.data_provider
    owner_class_name = owner.__class__.__name__
    owner._runtime_network_triggered = True

    def _bg_task(cancellation_token):
        return invoke_with_cancellation(
            provider.fetch_realtime_quotes_batch,
            cancellation_token,
            target_codes,
        )

    def _on_success(quotes):
        owner_obj = owner_ref()
        if not _is_owner_runtime_active(owner_obj) or not quotes:
            return
        published = owner_obj._publish_quote_payload(quotes, source=f"{owner_class_name}.quotes")
        _apply_owner_quote_snapshot(owner_obj, published or quotes)

    def _on_error(error_message: str):
        if error_message:
            logging.getLogger(__name__).debug(f"[{owner_class_name}] 表格补价失败: {error_message}")

    _run_owner_background(
        owner, task_manager, "realtime_quote_refresh", _bg_task,
        task_id=task_id, timeout_sec=30.0, on_success=_on_success, on_error=_on_error,
    )


def _prepare_table_refresh(owner, current_model, async_local: bool):
    if current_model is not None:
        owner._active_model_ref = current_model
    model = current_model or owner._resolve_active_quote_model()
    if not model or not hasattr(model, "row_data"):
        return None
    codes = collect_table_codes(owner, model)
    if not codes:
        return None
    if (
        _should_prime_local_snapshot(owner, async_local=async_local)
        or bool(getattr(owner, "_f5_cache_snapshot_apply", False))
        or _should_prime_f5_local_snapshot(owner, model)
    ):
        primer = prime_local_quote_snapshot_async if async_local else prime_local_quote_snapshot
        primer(owner, model)
    return model, codes


def refresh_table_quotes_and_market_caps(
    owner,
    current_model=None,
    force_quotes: bool = False,
    quote_task_id=None,
    *,
    async_local: bool = False,
) -> None:
    prepared = _prepare_table_refresh(owner, current_model, async_local)
    if prepared is None:
        return
    model, codes = prepared

    snapshot = _latest_quote_snapshot()

    quote_subset = {code: dict(snapshot[code]) for code in codes if code in snapshot}
    if quote_subset:
        _apply_owner_quote_snapshot(owner, quote_subset)

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

    _submit_owner_quote_refresh(owner, task_manager, task_id, target_codes)


def refresh_table_from_latest_snapshot(
    owner,
    current_model=None,
    *,
    async_local: bool = True,
    prime_local: bool = True,
) -> None:
    with ui_stall_span(
        "BaseStockRefresh.refresh_table_from_latest_snapshot",
        tab=owner.__class__.__name__,
        signal="cache_snapshot",
    ):
        _refresh_table_from_latest_snapshot_impl(
            owner,
            current_model=current_model,
            async_local=async_local,
            prime_local=prime_local,
        )


def _prepare_snapshot_refresh(owner, current_model, async_local: bool, prime_local: bool):
    if prime_local:
        return _prepare_table_refresh(owner, current_model, async_local)
    if current_model is not None:
        owner._active_model_ref = current_model
    model = current_model or owner._resolve_active_quote_model()
    if model is None:
        return None
    codes = collect_table_codes(owner, model)
    return (model, codes) if codes else None


def _refresh_table_from_latest_snapshot_impl(
    owner,
    current_model=None,
    *,
    async_local: bool = True,
    prime_local: bool = True,
) -> None:
    prepared = _prepare_snapshot_refresh(owner, current_model, async_local, prime_local)
    if prepared is None:
        return
    model, codes = prepared

    snapshot = _latest_quote_snapshot()

    if not snapshot:
        return

    quote_subset = {code: dict(snapshot[code]) for code in codes if code in snapshot}
    if not quote_subset:
        return

    force_apply = bool(getattr(owner, "_f5_cache_snapshot_apply", False))
    if _should_defer_cache_snapshot_until_visible(
        owner,
        async_local=async_local,
        force_apply=force_apply,
    ):
        owner._deferred_quote_refresh = True
        record_metric(
            "cache_snapshot_deferred_until_visible_count",
            1.0,
            unit="count",
            tags={
                "tab": owner.__class__.__name__,
                "codes": str(len(quote_subset)),
                "signal": "cache_snapshot",
                "reason": "hidden",
            },
        )
        return

    # Hidden Watchlist data is intentionally applied as one coherent model
    # projection. Chunking it across later event-loop turns would reintroduce
    # a post-show quote burst when the user returns quickly.
    if not _owner_is_visible(owner) and _owner_accepts_hidden_quote_projection(owner):
        _apply_cache_snapshot_payload(
            owner,
            quote_subset,
            signal="hidden_cache_snapshot",
            record_flash=False,
        )
        return

    if not CacheSnapshotApplyQueue.enqueue(
        owner,
        quote_subset,
        async_local=async_local,
        force_apply=force_apply,
    ):
        _apply_cache_snapshot_payload(
            owner,
            quote_subset,
            signal="cache_snapshot" if async_local else "cache_snapshot_sync",
        )


def _finish_workspace_background_snapshot(owner, table_codes: list[str], warm_payload: dict | None = None) -> None:
    if not _is_owner_runtime_active(owner):
        return
    published = {}
    if warm_payload:
        published = publish_rt_quotes(
            warm_payload,
            source=f"{owner.__class__.__name__}.workspace_preload_local_cache",
        )

    latest = _latest_quote_snapshot()
    payload = _quote_subset_for_codes(latest, table_codes)
    for code, quote in dict(published or warm_payload or {}).items():
        if code in table_codes:
            payload[code] = dict(quote or {})

    if payload and not CacheSnapshotApplyQueue.enqueue(
        owner,
        payload,
        async_local=True,
        force_apply=True,
    ):
        _apply_cache_snapshot_payload(owner, payload, signal="workspace_background_preload")
    owner._workspace_background_snapshot_io_done = True


def _workspace_background_snapshot_callbacks(owner, table_codes, target_codes, latest_target_quotes):
    owner_ref = weakref.ref(owner)

    def _build(_cancellation_token):
        owner_obj = owner_ref()
        if not _is_owner_runtime_active(owner_obj):
            return {}
        return _build_local_quote_payload(
            owner_obj,
            list(target_codes),
            latest_quotes=latest_target_quotes,
        )

    def _complete(warm_payload) -> None:
        owner_obj = owner_ref()
        if _is_owner_runtime_active(owner_obj):
            _finish_workspace_background_snapshot(owner_obj, table_codes, warm_payload)

    def _failed(_error_message: str) -> None:
        owner_obj = owner_ref()
        if _is_owner_runtime_active(owner_obj):
            _finish_workspace_background_snapshot(owner_obj, table_codes)

    def _terminated() -> None:
        owner_obj = owner_ref()
        if owner_obj is not None and not _is_qt_object_deleted(owner_obj):
            owner_obj._workspace_background_snapshot_io_done = True

    return _build, _complete, _failed, _terminated


def _submit_workspace_background_snapshot(owner, table_codes, target_codes, latest_quotes) -> None:
    from app.services.ui_task_service import background_job_runner as task_manager

    latest_target_quotes = _quote_subset_for_codes(latest_quotes, target_codes)
    task_key = _local_quote_task_key(owner, target_codes, scope="workspace_background")
    owner._workspace_background_snapshot_task_id = task_id_of(task_key)
    build, complete, failed, terminated = _workspace_background_snapshot_callbacks(
        owner,
        table_codes,
        target_codes,
        latest_target_quotes,
    )
    _run_owner_background(
        owner,
        task_manager,
        "workspace_background_snapshot",
        build,
        task_id=task_key,
        timeout_sec=60.0,
        on_success=complete,
        on_error=failed,
        on_terminated=terminated,
    )


def prime_workspace_background_snapshot(owner, current_model=None) -> bool:
    """Prepare local quote/finance data while a staged eager-preload tab is hidden."""
    if getattr(owner, "_workspace_background_snapshot_started", False):
        return True
    if not _is_owner_runtime_active(owner):
        return False

    if current_model is not None:
        owner._active_model_ref = current_model
    model = current_model or owner._resolve_active_quote_model()
    table_codes = collect_table_codes(owner, model) if model is not None else []
    owner._workspace_background_snapshot_started = True
    owner._workspace_background_snapshot_io_done = False
    owner._workspace_background_snapshot_ready = False
    owner._workspace_background_snapshot_cancelled = False
    if not table_codes:
        owner._workspace_background_snapshot_io_done = True
        return True

    latest_quotes = _latest_quote_snapshot()
    target_codes = _collect_local_quote_targets(owner, model, latest_quotes)
    if not target_codes:
        _finish_workspace_background_snapshot(owner, table_codes)
        return True

    _submit_workspace_background_snapshot(owner, table_codes, target_codes, latest_quotes)
    return True


def workspace_background_snapshot_complete(owner) -> bool:
    if not getattr(owner, "_workspace_background_snapshot_started", False):
        return False
    if not getattr(owner, "_workspace_background_snapshot_io_done", False):
        return False
    if CacheSnapshotApplyQueue.is_pending(owner):
        return False
    owner._workspace_background_snapshot_ready = True
    return True


def cancel_workspace_background_snapshot(owner) -> None:
    CacheSnapshotApplyQueue.discard(owner)
    owner._workspace_background_snapshot_started = False
    owner._workspace_background_snapshot_ready = False
    owner._workspace_background_snapshot_cancelled = True


def workspace_background_snapshot_cancellation_settled(owner) -> bool:
    if not getattr(owner, "_workspace_background_snapshot_cancelled", False):
        return False
    if not getattr(owner, "_workspace_background_snapshot_io_done", False):
        return False
    if CacheSnapshotApplyQueue.is_pending(owner):
        return False
    return True


def refresh_workspace_preloaded_snapshot(owner, current_model=None) -> None:
    owner._deferred_quote_refresh = False
    _refresh_table_from_latest_snapshot_impl(
        owner,
        current_model=current_model,
        async_local=True,
        prime_local=False,
    )
    owner._workspace_background_snapshot_ready = False


def subscribe_global_quotes(owner, current_model=None) -> None:
    if current_model:
        owner._active_model_ref = current_model

    model = owner._resolve_active_quote_model()
    if model and hasattr(model, "update_quotes"):
        snapshot: Mapping[str, Mapping[str, object]]
        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot = {}
        if snapshot:
            if _owner_is_visible(owner) or _owner_accepts_hidden_quote_projection(owner):
                _apply_owner_quote_snapshot(owner, snapshot)
            else:
                owner._deferred_quote_refresh = True

    if not hasattr(owner, "_quote_signal_connected"):
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


def on_rt_quotes_direct(
    owner,
    quotes: Mapping[str, Mapping[str, object]],
) -> None:
    with ui_stall_span(
        "BaseStockRefresh.on_rt_quotes_direct",
        tab=owner.__class__.__name__,
        signal="sig_rt_quotes",
    ):
        if not _owner_is_visible(owner) and not _owner_accepts_hidden_quote_projection(owner):
            owner._deferred_quote_refresh = True
            return

        _apply_owner_quote_snapshot(owner, quotes)


def replay_deferred_quotes(owner) -> None:
    with ui_stall_span(
        "BaseStockRefresh.replay_deferred_quotes",
        tab=owner.__class__.__name__,
        signal="showEvent",
    ):
        if not owner._deferred_quote_refresh:
            return

        owner._deferred_quote_refresh = False
        model = owner._resolve_active_quote_model()
        if model is None or not hasattr(model, "row_data"):
            snapshot = _latest_quote_snapshot()
            if snapshot:
                _apply_owner_quote_snapshot(owner, snapshot)
            return
        _refresh_table_from_latest_snapshot_impl(
            owner,
            async_local=True,
            prime_local=False,
        )


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
        _apply_owner_quote_snapshot(owner, latest_quotes)

    codes_need_cap = collect_missing_finance_codes(owner, model)
    if not codes_need_cap:
        _invoke_after_market_caps_updated(owner)
        return

    MarketCapRefreshBatcher.enqueue(owner, codes_need_cap)
