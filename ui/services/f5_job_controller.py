# -*- coding: utf-8 -*-
"""Qt-side polling controller for the isolated F5 job lane."""

from __future__ import annotations

import concurrent.futures
import queue
import threading
import time

from PyQt6.QtCore import QObject, QTimer

from app.services.f5_job_contract import F5_JOB_SCHEMA_VERSION, F5JobRequest, F5JobResult, F5JobStatus
from app.services.f5_job_runner import persist_f5_terminal_result
from core.f5_activation_gate import wait_for_f5_snapshot_activation
from core.logger import get_logger

log = get_logger(__name__)

_WORKER_TERMINAL_STATUSES = {
    F5JobStatus.READY_TO_ACTIVATE,
    F5JobStatus.FAILED,
    F5JobStatus.CANCELLED,
}


def _worker_result_matches_request(result: F5JobResult, request: F5JobRequest) -> bool:
    return (
        result.schema_version == F5_JOB_SCHEMA_VERSION
        and result.run_id == request.run_id
        and result.requested_date == request.requested_date
        and result.status in _WORKER_TERMINAL_STATUSES
    )


def _cancelled_result(request: F5JobRequest, *, started_at: float, reason: str) -> F5JobResult:
    error_code = "deadline_exceeded" if reason == "deadline_exceeded" else "cancelled"
    return F5JobResult.cancelled(
        request,
        elapsed_seconds=max(0.0, time.monotonic() - started_at),
        reason=reason,
        error_code=error_code,
    )


def _prepare_controller_start(controller, request, *, on_event, on_finished) -> None:
    controller._request = request
    controller._last_request = request
    controller._last_result = None
    controller._last_worker_pid = None
    controller._on_event = on_event
    controller._on_finished = on_finished
    controller._started_at = time.monotonic()
    controller._timeout_requested = False
    controller._activation_cancelled.clear()
    controller._activation_cancel_reason = ""
    controller._controller_error = None


def _deliver_controller_events(controller, events) -> None:
    for event in events:
        if controller._on_event is not None:
            controller._on_event(event)


def _enforce_controller_deadline(controller, request: F5JobRequest) -> None:
    if controller._timeout_requested:
        return
    if time.monotonic() - controller._started_at < request.timeout_seconds:
        return
    controller._timeout_requested = True
    controller.cancel("deadline_exceeded")


def _finish_missing_worker_result(controller, request: F5JobRequest) -> None:
    controller._finish(
        F5JobResult.failed(
            request,
            error_code="worker_exited_without_result",
            error_message="F5 worker exited without a terminal result",
            elapsed_seconds=time.monotonic() - controller._started_at,
        )
    )


def _monitor_failure_error(exc: BaseException) -> RuntimeError:
    detail = str(exc).strip()
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return RuntimeError(f"F5 worker monitor failed: {message}")


def _enforce_monitor_deadline(monitor, deadline_requested: bool) -> bool:
    if deadline_requested:
        return True
    if time.monotonic() - monitor._started_at < monitor._request.timeout_seconds:
        return False
    monitor._apply_cancel("deadline_exceeded")
    return True


def _record_controller_monitor_cancel(controller, payload) -> None:
    reason = str(payload or "cancelled")
    if not controller._activation_cancel_reason or reason == "deadline_exceeded":
        controller._activation_cancel_reason = reason
    if reason == "deadline_exceeded":
        controller._timeout_requested = True
    controller._activation_cancelled.set()


def _finish_controller_worker_terminal(controller, request: F5JobRequest, payload) -> None:
    if controller._controller_error is not None:
        controller._finish_controller_error(controller._controller_error)
    elif payload is not None:
        controller._accept_worker_result(payload)
    else:
        _finish_missing_worker_result(controller, request)


def _handle_controller_worker_message(
    controller,
    request: F5JobRequest,
    kind: str,
    payload,
) -> bool:
    if kind == "events":
        _deliver_controller_events(controller, payload)
        return False
    if kind == "cancel_requested":
        _record_controller_monitor_cancel(controller, payload)
        return False
    if kind == "error":
        controller._finish_controller_error(controller._controller_error or payload)
        return True
    if kind != "terminal":
        return False
    _finish_controller_worker_terminal(controller, request, payload)
    return True


class _F5WorkerMonitor:
    """Own every blocking handle operation outside the Qt GUI thread."""

    def __init__(self, *, handle, request: F5JobRequest, started_at: float, poll_interval_ms: int) -> None:
        self._handle = handle
        self._request = request
        self._started_at = float(started_at)
        self._poll_interval = max(0.05, int(poll_interval_ms or 150) / 1000.0)
        self._commands = queue.SimpleQueue()
        self._messages = queue.SimpleQueue()
        self._wake_event = threading.Event()
        self._done_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="f5-worker-monitor", daemon=True)

    @property
    def done(self) -> bool:
        return self._done_event.is_set()

    def start(self) -> None:
        self._thread.start()

    def request_cancel(self, reason: str) -> None:
        if self.done:
            return
        self._commands.put(("cancel", str(reason or "cancelled"), None))
        self._wake_event.set()

    def request_stop(self, reason: str, *, force_after_seconds: float) -> None:
        if self.done:
            return
        self._commands.put(
            ("stop", str(reason or "owner_shutdown"), max(0.0, float(force_after_seconds)))
        )
        self._wake_event.set()

    def wait(self, timeout: float) -> bool:
        return self._done_event.wait(max(0.0, float(timeout)))

    def pop_message(self):
        try:
            return self._messages.get_nowait()
        except queue.Empty:
            return None

    def _publish(self, kind: str, payload) -> None:
        self._messages.put((self._request.run_id, kind, payload))

    def _apply_cancel(self, reason: str) -> None:
        self._handle.cancel(reason)
        self._publish("cancel_requested", reason)

    def _drain_commands(self, *, cancel_grace: float, force_at: float | None):
        while True:
            try:
                command, reason, force_after = self._commands.get_nowait()
            except queue.Empty:
                return cancel_grace, force_at
            self._apply_cancel(reason)
            if command == "stop":
                cancel_grace = min(cancel_grace, 0.25)
                requested_force_at = time.monotonic() + float(force_after or 0.0)
                force_at = requested_force_at if force_at is None else min(force_at, requested_force_at)

    def _abort_after_error(self, *, result_called: bool) -> None:
        try:
            self._handle.cancel("controller_failed")
        except BaseException:
            pass
        try:
            running = self._handle.is_running()
        except BaseException:
            running = True
        if running:
            try:
                self._handle.force_terminate()
            except BaseException:
                pass
        if result_called:
            return
        try:
            running = self._handle.is_running()
        except BaseException:
            running = True
        if not running:
            try:
                self._handle.result()
            except BaseException:
                pass

    def _run(self) -> None:
        cancel_grace = 2.0
        force_at = None
        deadline_requested = False
        result_called = False
        try:
            while True:
                cancel_grace, force_at = self._drain_commands(
                    cancel_grace=cancel_grace,
                    force_at=force_at,
                )
                deadline_requested = _enforce_monitor_deadline(self, deadline_requested)

                events = self._handle.poll_events()
                if events:
                    self._publish("events", tuple(events))

                self._handle.enforce_cancel_grace(cancel_grace)
                running = self._handle.is_running()
                if running and force_at is not None and time.monotonic() >= force_at:
                    self._handle.force_terminate()
                    running = self._handle.is_running()
                if not running:
                    final_events = self._handle.poll_events()
                    if final_events:
                        self._publish("events", tuple(final_events))
                    result_called = True
                    result = self._handle.result()
                    self._publish("terminal", result)
                    return

                self._wake_event.wait(self._poll_interval)
                self._wake_event.clear()
        except BaseException as exc:
            self._abort_after_error(result_called=result_called)
            self._publish("error", _monitor_failure_error(exc))
        finally:
            self._done_event.set()


class F5JobController(QObject):
    def __init__(self, *, runner, installer, parent=None, poll_interval_ms: int = 150) -> None:
        super().__init__(parent)
        self.runner = runner
        self.installer = installer
        self._handle = None
        self._monitor = None
        self._request = None
        self._last_request = None
        self._last_result = None
        self._last_worker_pid = None
        self._on_event = None
        self._on_finished = None
        self._activation_future = None
        self._controller_error = None
        self._activation_cancelled = threading.Event()
        self._activation_cancel_reason = ""
        self._activation_thread = None
        self._terminal_cleanup_thread = None
        self._started_at = 0.0
        self._closing = False
        self._timeout_requested = False
        self._timer = QTimer(self)
        self._poll_interval_ms = max(50, int(poll_interval_ms or 150))
        self._timer.setInterval(self._poll_interval_ms)
        self._timer.timeout.connect(self._poll)

    @property
    def is_running(self) -> bool:
        cleanup = self._terminal_cleanup_thread
        return bool(
            self._handle is not None
            or self._activation_future is not None
            or (cleanup is not None and cleanup.is_alive())
        )

    @property
    def last_request(self) -> F5JobRequest | None:
        return self._last_request

    @property
    def last_result(self) -> F5JobResult | None:
        return self._last_result

    @property
    def last_worker_pid(self) -> int | None:
        return self._last_worker_pid

    def start(self, request: F5JobRequest, *, on_event=None, on_finished=None) -> bool:
        if self.is_running or self._closing:
            return False
        _prepare_controller_start(self, request, on_event=on_event, on_finished=on_finished)
        try:
            self._handle = self.runner.start(request)
            process = getattr(self._handle, "process", None)
            worker_pid = getattr(process, "pid", None)
            self._last_worker_pid = worker_pid if isinstance(worker_pid, int) and worker_pid > 0 else None
            self._monitor = _F5WorkerMonitor(
                handle=self._handle,
                request=request,
                started_at=self._started_at,
                poll_interval_ms=self._poll_interval_ms,
            )
            self._monitor.start()
        except Exception as exc:
            self._finish(
                F5JobResult.failed(
                    request,
                    error_code=str(getattr(exc, "error_code", "worker_start_failed") or "worker_start_failed"),
                    error_message=str(exc),
                )
            )
            return True
        self._timer.start()
        return True

    def cancel(self, reason: str = "user_cancelled") -> None:
        monitor = self._monitor
        if monitor is not None:
            monitor.request_cancel(reason)
        if not self._activation_cancel_reason or reason == "deadline_exceeded":
            self._activation_cancel_reason = str(reason or "cancelled")
        if reason == "deadline_exceeded":
            self._timeout_requested = True
        self._activation_cancelled.set()

    def _finish(self, result: F5JobResult) -> None:
        callback = self._on_finished
        request = self._request
        self._last_result = result
        self._timer.stop()
        if request is not None:
            try:
                persist_f5_terminal_result(request, result)
            except Exception as exc:
                log.warning("[F5] terminal result persistence failed: %s", exc)
        if result.status is not F5JobStatus.SUCCEEDED:
            self._schedule_terminal_cleanup(result)
        self._handle = None
        self._monitor = None
        self._request = None
        self._activation_future = None
        self._activation_thread = None
        self._controller_error = None
        self._on_event = None
        self._on_finished = None
        if callback is not None:
            callback(result)

    def _schedule_terminal_cleanup(self, result: F5JobResult) -> None:
        cleanup = getattr(self.installer, "prune_after_terminal", None)
        if not callable(cleanup):
            return

        def _run_cleanup() -> None:
            try:
                cleanup(result)
            except Exception as exc:
                log.warning("[F5] terminal cleanup failed: %s", exc)

        self._terminal_cleanup_thread = threading.Thread(
            target=_run_cleanup,
            name="f5-terminal-cleanup",
            daemon=True,
        )
        self._terminal_cleanup_thread.start()

    def _poll(self) -> None:
        try:
            if self._activation_future is not None:
                self._poll_activation()
                return
            self._poll_worker()
        except Exception as exc:
            if self._request is not None:
                self._controller_error = exc
                monitor = self._monitor
                if monitor is None or monitor.done:
                    self._finish_controller_error(exc)
                else:
                    monitor.request_stop("controller_failed", force_after_seconds=0.0)

    def _poll_activation(self) -> None:
        if not self._activation_future.done():
            self._enforce_activation_deadline()
            return
        try:
            result = self._activation_future.result()
        except Exception as exc:
            result = F5JobResult.failed(
                self._request,
                error_code="activation_worker_failed",
                error_message=str(exc),
            )
        if self._timeout_requested and result.status is F5JobStatus.CANCELLED:
            result = F5JobResult.cancelled(
                self._request,
                elapsed_seconds=result.elapsed_seconds,
                reason="deadline_exceeded",
                error_code="deadline_exceeded",
                warnings=result.warnings,
            )
        self._finish(result)

    def _enforce_activation_deadline(self) -> None:
        request = self._request
        if request is None or self._timeout_requested:
            return
        if time.monotonic() - self._started_at < request.timeout_seconds:
            return
        self._timeout_requested = True
        self.cancel("deadline_exceeded")

    def _poll_worker(self) -> None:
        monitor, request = self._monitor, self._request
        if monitor is None or request is None:
            return
        _enforce_controller_deadline(self, request)
        while True:
            message = monitor.pop_message()
            if message is None:
                return
            run_id, kind, payload = message
            if run_id != request.run_id:
                continue
            if _handle_controller_worker_message(self, request, kind, payload):
                return

    def _finish_controller_error(self, exc) -> None:
        request = self._request
        if request is None:
            return
        self._finish(
            F5JobResult.failed(
                request,
                error_code="f5_controller_failed",
                error_message=str(exc),
            )
        )

    def _accept_worker_result(self, result: F5JobResult) -> None:
        request = self._request
        if request is None:
            return
        if not _worker_result_matches_request(result, request):
            self._finish(
                F5JobResult.failed(
                    request,
                    error_code="worker_result_mismatch",
                    error_message="F5 worker result does not match the active request",
                )
            )
            return
        if result.status is not F5JobStatus.READY_TO_ACTIVATE:
            self._finish(result)
            return
        if self._closing or self._activation_cancelled.is_set():
            reason = self._activation_cancel_reason or "parent_shutdown"
            error_code = "deadline_exceeded" if reason == "deadline_exceeded" else "cancelled"
            cancelled = F5JobResult.cancelled(self._request, reason=reason, error_code=error_code)
            self._finish(cancelled)
            return
        self._start_activation(result)

    def _start_activation(self, result: F5JobResult) -> None:
        future = concurrent.futures.Future()
        self._activation_future = future

        def _run() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                activated = self.installer.activate(
                    result,
                    cancelled_checker=self._activation_cancelled.is_set,
                )
            except Exception as exc:
                future.set_exception(exc)
                return
            future.set_result(activated)

        self._activation_thread = threading.Thread(target=_run, name="f5-activate", daemon=True)
        self._activation_thread.start()

    def shutdown(self, *, timeout_ms: int = 2_500) -> bool:
        self._closing = True
        self.cancel("owner_shutdown")
        deadline = time.monotonic() + max(0, int(timeout_ms or 0)) / 1000.0
        process_done = self._stop_worker_before(deadline)
        activation_done = self._stop_activation_before(deadline)
        cleanup_done = self._stop_terminal_cleanup_before(deadline)
        self._timer.stop()
        shutdown_runner = getattr(self.runner, "shutdown", None)
        if callable(shutdown_runner):
            shutdown_runner()
        return process_done and activation_done and cleanup_done

    def _stop_terminal_cleanup_before(self, deadline: float) -> bool:
        cleanup = self._terminal_cleanup_thread
        if cleanup is None:
            return True
        cleanup.join(timeout=max(0.0, deadline - time.monotonic()))
        stopped = not cleanup.is_alive()
        if stopped:
            self._terminal_cleanup_thread = None
        return stopped

    def _stop_worker_before(self, deadline: float) -> bool:
        monitor = self._monitor
        if monitor is None:
            return True
        remaining = max(0.0, deadline - time.monotonic())
        force_after = min(0.25, remaining / 2.0)
        reason = self._activation_cancel_reason or "owner_shutdown"
        monitor.request_stop(reason, force_after_seconds=force_after)
        process_done = monitor.wait(remaining)
        if process_done and self._handle is not None and self._activation_future is None and self._request is not None:
            reason = self._activation_cancel_reason or "owner_shutdown"
            self._finish(_cancelled_result(self._request, started_at=self._started_at, reason=reason))
        return process_done

    def _stop_activation_before(self, deadline: float) -> bool:
        future = self._activation_future
        if future is None:
            return True
        if future.cancel():
            if self._request is not None:
                reason = self._activation_cancel_reason or "owner_shutdown"
                self._finish(_cancelled_result(self._request, started_at=self._started_at, reason=reason))
            return True
        remaining = max(0.0, deadline - time.monotonic())
        if not wait_for_f5_snapshot_activation(remaining):
            return False
        remaining = max(0.0, deadline - time.monotonic())
        try:
            result = future.result(timeout=remaining)
        except concurrent.futures.TimeoutError:
            return False
        except Exception as exc:
            if self._request is not None:
                self._finish(
                    F5JobResult.failed(
                        self._request,
                        error_code="activation_worker_failed",
                        error_message=str(exc),
                        elapsed_seconds=max(0.0, time.monotonic() - self._started_at),
                    )
                )
            return True
        self._finish(result)
        return True


__all__ = ["F5JobController"]
