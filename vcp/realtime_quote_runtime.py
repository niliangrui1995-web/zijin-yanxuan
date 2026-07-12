# -*- coding: utf-8 -*-
from __future__ import annotations

import queue
import threading
import time

_RUNTIME_ERRORS = (
    AttributeError,
    ConnectionError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
)


class RealtimeQuoteRuntime:
    """Own a single pytdx quote connection and execute requests serially."""

    def __init__(self, provider, logger):
        self.provider = provider
        self._log = logger
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="tdx-realtime-owner",
        )
        self._api = None
        self._server = None
        self._inflight = 0
        self._last_success_at = 0.0
        self._consecutive_failures = 0
        self._reconnect_count = 0
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def snapshot(self) -> dict:
        with self._lock:
            owner_thread_alive = self._thread.is_alive()
            return {
                "inflight": self._inflight,
                "last_success_at": self._last_success_at,
                "consecutive_failures": self._consecutive_failures,
                "reconnect_count": self._reconnect_count,
                "worker_alive": owner_thread_alive,
                "owner_thread_alive": owner_thread_alive,
                "server": self._server,
                "closed": self._closed,
            }

    def close(self, timeout_sec: float = 0.25) -> bool:
        with self._lock:
            first_close = not self._closed
            self._closed = True
            self._stop_event.set()

        if first_close:
            self._fail_queued_requests()
            self._queue.put_nowait(None)

        if threading.current_thread() is not self._thread:
            self._thread.join(max(0.0, float(timeout_sec or 0.0)))
        return not self._thread.is_alive()

    def request(self, params_list, timeout_sec: float):
        state = {
            "params": list(params_list),
            "done": threading.Event(),
            "result": None,
            "error": None,
            "completed": False,
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("实时行情运行时已关闭")
            self._inflight += 1
            self._queue.put_nowait(state)

        if not state["done"].wait(timeout_sec):
            raise TimeoutError(f"实时行情批次超时（{timeout_sec:.0f}s，{len(params_list)} 个标的）")

        if state["error"] is not None:
            raise state["error"]
        return state["result"] or []

    def _ensure_api(self):
        with self._lock:
            if self._api is not None:
                return self._api

        api = self.provider._create_api_client()
        server = self.provider._connect_api_to_best_server(
            api,
            time_out=5,
            require_security_count=True,
        )
        with self._lock:
            if self._closed:
                should_disconnect = True
            else:
                should_disconnect = False
                self._api = api
                self._server = server
                self._reconnect_count += 1
                return self._api
        if should_disconnect:
            try:
                api.disconnect()
            except (AttributeError, OSError, RuntimeError, TypeError):
                pass
            raise RuntimeError("实时行情运行时已关闭")

    def _disconnect_api(self):
        with self._lock:
            api = self._api
            self._api = None
            self._server = None
        if api is None:
            return
        try:
            api.disconnect()
        except (AttributeError, OSError, RuntimeError, TypeError) as exc:
            self._log.debug(f"[网络] 断开实时 pytdx 连接失败: {exc}")

    def _complete_state(self, state, *, result=None, error: BaseException | None = None) -> None:
        with self._lock:
            if state.get("completed"):
                return
            state["completed"] = True
            state["result"] = result
            state["error"] = error
            self._inflight = max(0, self._inflight - 1)
        state["done"].set()

    def _fail_queued_requests(self) -> None:
        while True:
            try:
                state = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                if state is not None:
                    self._complete_state(state, error=RuntimeError("实时行情运行时已关闭"))
            finally:
                self._queue.task_done()

    def _process_state(self, state) -> None:
        result = None
        error = None
        try:
            with self._lock:
                closed = self._closed
            if closed:
                raise RuntimeError("实时行情运行时已关闭")
            api = self._ensure_api()
            quotes = api.get_security_quotes(state["params"])
            if not quotes:
                raise RuntimeError("实时行情返回空结果")
            with self._lock:
                if self._closed:
                    raise RuntimeError("实时行情运行时已关闭")
                self._last_success_at = time.time()
                self._consecutive_failures = 0
            result = quotes
        except _RUNTIME_ERRORS as exc:
            with self._lock:
                self._consecutive_failures += 1
            error = exc
            self._disconnect_api()
        finally:
            self._complete_state(state, result=result, error=error)
            self._queue.task_done()

    def _worker_loop(self):
        while True:
            try:
                state = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            if state is None:
                self._queue.task_done()
                if self._stop_event.is_set():
                    break
                continue

            self._process_state(state)

        self._disconnect_api()
