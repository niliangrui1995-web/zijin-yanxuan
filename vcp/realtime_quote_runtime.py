# -*- coding: utf-8 -*-
from __future__ import annotations

import queue
import threading
import time


class RealtimeQuoteRuntime:
    """Own a single pytdx quote connection and execute requests serially."""

    def __init__(self, provider, logger):
        self.provider = provider
        self._log = logger
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
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
            return {
                "inflight": self._inflight,
                "last_success_at": self._last_success_at,
                "consecutive_failures": self._consecutive_failures,
                "reconnect_count": self._reconnect_count,
                "worker_alive": self._thread.is_alive(),
                "server": self._server,
            }

    def close(self):
        self._stop_event.set()
        self._disconnect_api()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return

    def request(self, params_list, timeout_sec: float):
        if self._stop_event.is_set():
            raise RuntimeError("实时行情运行时已关闭")

        state = {
            "params": list(params_list),
            "done": threading.Event(),
            "result": None,
            "error": None,
        }
        with self._lock:
            self._inflight += 1
        self._queue.put(state)

        if not state["done"].wait(timeout_sec):
            raise TimeoutError(
                f"实时行情批次超时（{timeout_sec:.0f}s，{len(params_list)} 个标的）"
            )

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
            self._api = api
            self._server = server
            self._reconnect_count += 1
            return self._api

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

    def _worker_loop(self):
        handled_errors = (
            AttributeError,
            ConnectionError,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        )

        while not self._stop_event.is_set():
            try:
                state = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if state is None:
                continue

            try:
                api = self._ensure_api()
                quotes = api.get_security_quotes(state["params"])
                if not quotes:
                    raise RuntimeError("实时行情返回空结果")
                with self._lock:
                    self._last_success_at = time.time()
                    self._consecutive_failures = 0
                state["result"] = quotes
            except handled_errors as exc:
                with self._lock:
                    self._consecutive_failures += 1
                state["error"] = exc
                self._disconnect_api()
            finally:
                with self._lock:
                    self._inflight = max(0, self._inflight - 1)
                state["done"].set()

        self._disconnect_api()
