# -*- coding: utf-8 -*-
"""Minimal observability helpers for structured logs and key runtime metrics."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

_log = get_logger(__name__)
_metric_lock = threading.Lock()
_metric_samples = deque(maxlen=512)


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    unit: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    recorded_at: float = field(default_factory=time.time)


def _normalize_tags(tags: dict | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (tags or {}).items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        normalized[clean_key] = str(value or "").strip()
    return normalized


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_elapsed_ms(value: Any) -> str:
    elapsed_ms = _coerce_float(value)
    if elapsed_ms is None:
        return ""
    if elapsed_ms >= 1000:
        seconds = elapsed_ms / 1000.0
        return f"{seconds:.1f}秒"
    return f"{int(round(elapsed_ms))}ms"


def _format_count(value: Any, suffix: str = "") -> str:
    count = _coerce_float(value)
    if count is None:
        return ""
    rounded = int(round(count))
    return f"{rounded}{suffix}"


def _compact_join(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _format_quote_clock(value: Any) -> str:
    text = str(value or "").strip()
    if "T" in text and len(text.split("T", 1)[1]) >= 8:
        return text.split("T", 1)[1][:8]
    if " " in text and len(text.rsplit(" ", 1)[-1]) >= 8:
        return text.rsplit(" ", 1)[-1][:8]
    return text


def _format_metric_event(fields: dict[str, Any]) -> str:
    metric = str(fields.get("metric") or "").strip() or "unknown_metric"
    unit = str(fields.get("unit") or "").strip()
    value = fields.get("value")
    tags = fields.get("tags") or {}

    if unit == "ms":
        value_text = _format_elapsed_ms(value)
    elif unit == "count":
        value_text = _format_count(value)
    else:
        numeric_value = _coerce_float(value)
        value_text = str(value) if numeric_value is None else f"{numeric_value:g}"
        if unit:
            value_text = f"{value_text}{unit}"

    extra = ""
    if isinstance(tags, dict) and tags:
        compact_tags = ",".join(
            f"{str(key).strip()}={str(tag_value).strip()}"
            for key, tag_value in sorted(tags.items())
            if str(key).strip()
        )
        extra = compact_tags

    return _compact_join(f"[指标] {metric}", value_text, extra)


def _format_quote_refresh_summary(fields: dict[str, Any]) -> str:
    status = "刷新异常" if fields.get("provider_failed") else "刷新完成"
    extra = "" if fields.get("valid_quotes") else "无有效行情"
    has_freshness_counts = any(key in fields for key in ("network_count", "cache_count", "stale_count"))
    if not has_freshness_counts:
        return _compact_join(
            f"[行情] {status}",
            _format_count(fields.get("batch_size"), "只"),
            _format_elapsed_ms(fields.get("elapsed_ms")),
            extra,
        )
    count_parts = (
        f"联网{_format_count(fields.get('network_count'), '只')}",
        f"缓存{_format_count(fields.get('cache_count'), '只')}",
        f"过期{_format_count(fields.get('stale_count'), '只')}",
    )
    missing_count = _coerce_float(fields.get("missing_count")) or 0.0
    missing_text = _format_count(missing_count, "只") if missing_count > 0 else ""
    latest_quote_time = _format_quote_clock(fields.get("latest_quote_time"))
    return _compact_join(
        f"[行情] {status}",
        *count_parts,
        f"缺失{missing_text}" if missing_text else "",
        f"报价{latest_quote_time}" if latest_quote_time else "",
        _format_elapsed_ms(fields.get("elapsed_ms")),
        extra,
    )


def _format_event_summary(event: str, fields: dict[str, Any]) -> str:
    normalized_event = str(event or "").strip() or "unknown"

    if normalized_event == "quotes.refresh.completed":
        return _format_quote_refresh_summary(fields)

    if normalized_event == "kline.opened":
        code = str(fields.get("code") or "").strip()
        name = str(fields.get("name") or "").strip()
        active_windows = _format_count(fields.get("active_windows"), "窗")
        title = " ".join(part for part in (code, name) if part).strip() or "新窗口"
        window_text = f"第{active_windows}" if active_windows else ""
        return _compact_join(
            f"[K线] {title}",
            window_text,
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "workspace.mounted":
        return _compact_join(
            "[启动] 工作区已加载",
            _format_count(fields.get("tab_count"), "页"),
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "startup.deferred_load.completed":
        cache_date = str(fields.get("cache_date") or "").strip()
        cache_state = "已载入缓存" if fields.get("cache_loaded") else "未找到缓存"
        return _compact_join(
            "[启动] 缓存加载完成",
            cache_state,
            cache_date,
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "startup.asian_sync.completed":
        return _compact_join(
            "[启动] 亚洲同步完成",
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "startup.network_probe.completed":
        network_state = "在线" if fields.get("online") else "离线"
        return _compact_join(
            "[启动] 网络检测完成",
            network_state,
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "main_window.first_paint":
        return _compact_join(
            "[启动] 主界面已显示",
            _format_elapsed_ms(fields.get("elapsed_ms")),
        )

    if normalized_event == "metric.recorded":
        return _format_metric_event(fields)

    field_parts = []
    for key, value in sorted(fields.items()):
        if value is None or value == "":
            continue
        field_parts.append(f"{key}={value}")
        if len(field_parts) >= 4:
            break
    return _compact_join(f"[事件] {normalized_event}", *field_parts)


def emit_structured_log(event: str, *, logger=None, level: str = "info", **fields) -> dict:
    payload = {
        "event": str(event or "").strip() or "unknown",
        "fields": {str(key): value for key, value in fields.items() if str(key or "").strip()},
    }
    target_logger = logger or _log
    normalized_level = str(level or "info").lower()
    writer = getattr(target_logger, normalized_level, None) or target_logger.info
    if normalized_level == "debug":
        writer("[structured] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        writer(_format_event_summary(payload["event"], payload["fields"]))
    return payload


def record_metric(
    name: str,
    value,
    *,
    unit: str = "",
    tags: dict | None = None,
    logger=None,
    level: str = "debug",
) -> MetricSample:
    sample = MetricSample(
        name=str(name or "").strip() or "unknown_metric",
        value=float(value),
        unit=str(unit or "").strip(),
        tags=_normalize_tags(tags),
    )
    with _metric_lock:
        _metric_samples.append(sample)

    emit_structured_log(
        "metric.recorded",
        logger=logger,
        level=level,
        metric=sample.name,
        value=sample.value,
        unit=sample.unit,
        tags=sample.tags,
        recorded_at=sample.recorded_at,
    )
    return sample


def metric_history(name: str | None = None) -> list[MetricSample]:
    with _metric_lock:
        samples = list(_metric_samples)
    if name is None:
        return samples
    normalized_name = str(name or "").strip()
    return [sample for sample in samples if sample.name == normalized_name]


def clear_metric_history() -> None:
    with _metric_lock:
        _metric_samples.clear()
