from __future__ import annotations

from collections.abc import Mapping

from core.global_store import global_store
from core.logger import get_logger
from domains.quotes.snapshot import coerce_number
from domains.runtime import domain_events as event_bus

log = get_logger(__name__)


def normalize_quote_payload(payload: Mapping | None) -> dict[str, dict]:
    normalized: dict[str, dict] = {}
    for raw_code, raw_quote in dict(payload or {}).items():
        code = str(raw_code or "").strip()
        if not code:
            continue
        normalized[code] = dict(raw_quote or {})
    return normalized


def has_valid_quote(payload: Mapping | None) -> bool:
    for quote in dict(payload or {}).values():
        if coerce_number(dict(quote or {}).get("close")) > 0:
            return True
    return False


def publish_rt_quotes(
    payload: Mapping | None,
    *,
    source: str = "",
    require_valid: bool = False,
) -> dict[str, dict]:
    normalized = normalize_quote_payload(payload)
    if not normalized:
        return {}
    if require_valid and not has_valid_quote(normalized):
        return normalized

    global_store.merge_quotes(normalized)

    try:
        event_bus.sig_rt_quotes.emit(normalized)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        emitter = source or "unknown"
        log.error(f"[报价广播] 来源={emitter} 发送失败: {exc}")
    return normalized
