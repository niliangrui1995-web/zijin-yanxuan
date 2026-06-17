# -*- coding: utf-8 -*-
"""Lightweight UI-thread stall probe for the PyQt event loop."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from core.logger import get_logger
from core.observability import emit_structured_log, record_metric

log = get_logger(__name__)

_ACTIVE_PROBE: "UiStallProbe | None" = None
_UI_SPAN_STACK: ContextVar[tuple[dict, ...]] = ContextVar("ui_stall_span_stack", default=())


@dataclass(frozen=True)
class StallThresholds:
    warn_ms: float = 50.0
    critical_ms: float = 100.0


def _clean_text(value) -> str:
    return str(value or "").strip()


def _merge_context(*contexts: dict | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for context in contexts:
        for key, value in (context or {}).items():
            clean_key = _clean_text(key)
            clean_value = _clean_text(value)
            if clean_key and clean_value:
                merged[clean_key] = clean_value
    return merged


class UiStallProbe(QObject):
    """Samples event-loop delay and records slow UI spans."""

    def __init__(
        self,
        parent=None,
        *,
        timer_interval_ms: int = 25,
        thresholds: StallThresholds | None = None,
        context_provider: Callable[[], dict] | None = None,
        auto_start: bool = True,
    ):
        super().__init__(parent)
        self.thresholds = thresholds or StallThresholds()
        self.timer_interval_ms = max(5, int(timer_interval_ms or 25))
        self._context_provider = context_provider
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.timer_interval_ms)
        self._timer.timeout.connect(self._poll_event_loop)
        self._last_tick = time.perf_counter()
        self._last_span_context: dict[str, str] = {}
        self._last_event_loop_record_at = 0.0
        self._stall_counts = {
            "total_count": 0,
            "critical_count": 0,
            "event_loop_count": 0,
            "event_loop_critical_count": 0,
            "method_count": 0,
            "method_critical_count": 0,
        }
        self._max_elapsed_ms = 0.0
        if auto_start:
            self.start()

    def start(self) -> None:
        self._last_tick = time.perf_counter()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def current_context(self) -> dict[str, str]:
        provider_context = {}
        if callable(self._context_provider):
            try:
                provider_context = self._context_provider() or {}
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                provider_context = {"context_error": exc.__class__.__name__}
        stack = _UI_SPAN_STACK.get()
        span_context = stack[-1] if stack else {}
        return _merge_context(provider_context, span_context)

    def _event_loop_context(self) -> dict[str, str]:
        last_span_context = self._last_span_context
        self._last_span_context = {}
        return _merge_context(last_span_context, self.current_context())

    def record_span(self, elapsed_ms: float, context: dict | None) -> None:
        if elapsed_ms < self.thresholds.warn_ms:
            self._last_span_context = {}
            return
        self._last_span_context = _merge_context(context)
        self._record_stall(
            "ui.stall.method",
            elapsed_ms,
            context=context,
            metric_name="ui_method_stall_ms",
        )

    def stall_snapshot(self) -> dict:
        return {
            "installed": True,
            **self._stall_counts,
            "max_elapsed_ms": round(float(self._max_elapsed_ms), 3),
            "warn_threshold_ms": float(self.thresholds.warn_ms),
            "critical_threshold_ms": float(self.thresholds.critical_ms),
        }

    def reset_stall_snapshot(self) -> None:
        for key in self._stall_counts:
            self._stall_counts[key] = 0
        self._max_elapsed_ms = 0.0

    def _poll_event_loop(self) -> None:
        now = time.perf_counter()
        gap_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        late_ms = max(0.0, gap_ms - self.timer_interval_ms)
        if late_ms < self.thresholds.warn_ms:
            self._last_span_context = {}
            return

        # Avoid flooding logs after one long busy stretch.
        if now - self._last_event_loop_record_at < 0.2:
            self._last_span_context = {}
            return
        self._last_event_loop_record_at = now
        self._record_stall(
            "ui.stall.event_loop",
            late_ms,
            context=self._event_loop_context(),
            metric_name="ui_event_loop_stall_ms",
            extra={"event_loop_gap_ms": round(gap_ms, 3)},
        )

    def _record_stall(
        self,
        event: str,
        elapsed_ms: float,
        *,
        context: dict | None,
        metric_name: str,
        extra: dict | None = None,
    ) -> None:
        severity = "critical" if elapsed_ms >= self.thresholds.critical_ms else "warn"
        self._record_stall_stats(event, elapsed_ms, severity)
        fields = {
            "elapsed_ms": round(float(elapsed_ms), 3),
            "threshold_ms": self.thresholds.critical_ms if severity == "critical" else self.thresholds.warn_ms,
            "severity": severity,
            **_merge_context(context),
            **(extra or {}),
        }
        emit_structured_log(
            event,
            logger=log,
            level="warning" if severity == "critical" else "info",
            **fields,
        )
        record_metric(
            metric_name,
            float(elapsed_ms),
            unit="ms",
            tags={
                "severity": severity,
                "tab": fields.get("tab", ""),
                "method": fields.get("method", ""),
                "signal": fields.get("signal", ""),
            },
            logger=log,
            level="debug",
        )

    def _record_stall_stats(self, event: str, elapsed_ms: float, severity: str) -> None:
        event_type = "event_loop" if event == "ui.stall.event_loop" else "method"
        self._stall_counts["total_count"] += 1
        self._stall_counts[f"{event_type}_count"] += 1
        if severity == "critical":
            self._stall_counts["critical_count"] += 1
            self._stall_counts[f"{event_type}_critical_count"] += 1
        self._max_elapsed_ms = max(float(self._max_elapsed_ms), float(elapsed_ms))


def install_ui_stall_probe(
    app: QApplication | None = None,
    *,
    parent=None,
    context_provider: Callable[[], dict] | None = None,
    timer_interval_ms: int = 25,
) -> UiStallProbe | None:
    """Install one process-wide stall probe for the current QApplication."""

    global _ACTIVE_PROBE
    app = app or QApplication.instance()
    if app is None:
        return None
    if _ACTIVE_PROBE is not None:
        try:
            _ACTIVE_PROBE.objectName()
            return _ACTIVE_PROBE
        except RuntimeError:
            _ACTIVE_PROBE = None
    _ACTIVE_PROBE = UiStallProbe(
        parent=parent or app,
        timer_interval_ms=timer_interval_ms,
        context_provider=context_provider,
    )
    _ACTIVE_PROBE.destroyed.connect(_clear_active_probe)
    return _ACTIVE_PROBE


def get_ui_stall_probe() -> UiStallProbe | None:
    return _ACTIVE_PROBE


def _clear_active_probe(*_args) -> None:
    global _ACTIVE_PROBE
    _ACTIVE_PROBE = None


@contextmanager
def ui_stall_span(
    method: str,
    *,
    tab: str = "",
    signal: str = "",
    **metadata,
) -> Iterator[None]:
    """Annotate a UI method so slow spans can be attributed in logs."""

    context = _merge_context({"method": method, "tab": tab, "signal": signal}, metadata)
    stack = _UI_SPAN_STACK.get()
    token = _UI_SPAN_STACK.set((*stack, context))
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _UI_SPAN_STACK.reset(token)
        probe = get_ui_stall_probe()
        if probe is not None:
            probe.record_span(elapsed_ms, context)
