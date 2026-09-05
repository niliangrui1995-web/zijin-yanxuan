# -*- coding: utf-8 -*-
"""低基数的工作区标签切换阶段观测。"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from app.services.ui_diagnostics_service import ui_stall_span
from core.observability import emit_structured_log, record_metric

# Transition metadata is diagnostic context, not durable page state.  Keep it
# long enough to join the immediate queued show/layout work, but never let it
# label an unrelated native activation minutes later.
TAB_TRANSITION_CONTEXT_MAX_AGE_MS = 5_000


def begin_tab_transition(
    owner,
    *,
    source_tab: str,
    target_tab: str,
    reason: str,
    mounted_before: bool,
    preload_active_key: str,
    preload_ready: bool = True,
    target_preload_state: str = "",
) -> dict[str, object]:
    """创建一次切换关联上下文；序号仅写入结构化日志，不进入指标标签。"""

    sequence = int(getattr(owner, "_tab_transition_sequence", 0) or 0) + 1
    setattr(owner, "_tab_transition_sequence", sequence)
    active_key = str(preload_active_key or "").strip()
    normalized_target_state = str(target_preload_state or "").strip()
    if normalized_target_state not in {
        "cold",
        "background_active",
        "background_ready",
        "interactive_warm",
    }:
        normalized_target_state = "active" if active_key else ("ready" if preload_ready else "pending")
    return {
        "transition_id": str(sequence),
        "_created_at": time.monotonic(),
        "source_tab": str(source_tab or "").strip(),
        "target_tab": str(target_tab or "").strip(),
        "reason": str(reason or "").strip(),
        "mounted_before": bool(mounted_before),
        "preload_active_key": active_key,
        "preload_state": normalized_target_state,
    }


def attach_tab_transition_context(widget, context: dict[str, object] | None) -> None:
    if widget is None or not context:
        return
    try:
        setattr(widget, "_workspace_tab_transition_context", dict(context))
    except (AttributeError, RuntimeError, TypeError):
        return


def clear_tab_transition_context(widget) -> None:
    if widget is None:
        return
    try:
        setattr(widget, "_workspace_tab_transition_context", {})
    except (AttributeError, RuntimeError, TypeError):
        return


def tab_transition_context(owner, *, tab: str) -> dict[str, object]:
    context = getattr(owner, "_workspace_tab_transition_context", None)
    if not isinstance(context, dict):
        return {}
    if str(context.get("target_tab") or "").strip() != str(tab or "").strip():
        return {}
    created_at = context.get("_created_at")
    if isinstance(created_at, (int, float)) and created_at > 0:
        age_ms = (time.monotonic() - float(created_at)) * 1000.0
        if age_ms > TAB_TRANSITION_CONTEXT_MAX_AGE_MS:
            clear_tab_transition_context(owner)
            return {}
    return dict(context)


def _metric_tags(
    context: dict[str, object],
    *,
    stage: str,
    callback_kind: str,
    layout_signal: str,
) -> dict[str, str]:
    return {
        "source_tab": str(context.get("source_tab") or ""),
        "target_tab": str(context.get("target_tab") or ""),
        "reason": str(context.get("reason") or ""),
        "stage": str(stage or ""),
        "preload_state": str(context.get("preload_state") or ""),
        "mounted_before": str(bool(context.get("mounted_before"))).lower(),
        "callback_kind": str(callback_kind or ""),
        "layout_signal": str(layout_signal or ""),
    }


@contextmanager
def tab_transition_stage(
    owner,
    *,
    tab: str,
    method: str,
    stage: str,
    callback_kind: str = "",
    layout_signal: str = "",
) -> Iterator[None]:
    """仅在目标页仍携带当前切换上下文时记录阶段耗时。"""

    context = tab_transition_context(owner, tab=tab)
    started_at = time.perf_counter()
    span_metadata = {
        "transition_phase": str(stage or ""),
        "callback_kind": str(callback_kind or ""),
        "layout_signal": str(layout_signal or ""),
        **context,
    }
    with ui_stall_span(method, tab=tab, signal=str(stage or ""), **span_metadata):
        yield
    if not context:
        return

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    tags = _metric_tags(
        context,
        stage=stage,
        callback_kind=callback_kind,
        layout_signal=layout_signal,
    )
    record_metric("tab_transition_stage_ms", elapsed_ms, unit="ms", tags=tags)
    emit_structured_log(
        "workspace.tab_transition_stage",
        transition_id=str(context.get("transition_id") or ""),
        source_tab=tags["source_tab"],
        target_tab=tags["target_tab"],
        reason=tags["reason"],
        stage=tags["stage"],
        elapsed_ms=round(elapsed_ms, 3),
        preload_active_key=str(context.get("preload_active_key") or ""),
        preload_state=tags["preload_state"],
        mounted_before=tags["mounted_before"],
        callback_kind=tags["callback_kind"],
        layout_signal=tags["layout_signal"],
    )
