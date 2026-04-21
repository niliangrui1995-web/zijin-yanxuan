# -*- coding: utf-8 -*-
"""Minimal observability helpers for structured logs and key runtime metrics."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field

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


def emit_structured_log(event: str, *, logger=None, level: str = "info", **fields) -> dict:
    payload = {
        "event": str(event or "").strip() or "unknown",
        "fields": {
            str(key): value
            for key, value in fields.items()
            if str(key or "").strip()
        },
    }
    target_logger = logger or _log
    writer = getattr(target_logger, str(level or "info").lower(), None) or target_logger.info
    writer("[structured] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def record_metric(
    name: str,
    value,
    *,
    unit: str = "",
    tags: dict | None = None,
    logger=None,
    level: str = "info",
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
