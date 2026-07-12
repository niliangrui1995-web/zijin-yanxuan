# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time

import pytest

from vcp.realtime_quote_runtime import RealtimeQuoteRuntime


class _Log:
    def __init__(self) -> None:
        self.messages = []

    def debug(self, _message):
        self.messages.append(_message)


class _BlockingApi:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.disconnected = False
        self.quote_thread_id: int | None = None
        self.disconnect_thread_id: int | None = None

    def get_security_quotes(self, params):
        self.quote_thread_id = threading.get_ident()
        self.started.set()
        self.release.wait(2.0)
        return [{"params": list(params)}]

    def disconnect(self) -> None:
        self.disconnected = True
        self.disconnect_thread_id = threading.get_ident()


class _Provider:
    def __init__(self, api: _BlockingApi) -> None:
        self.api = api

    def _create_api_client(self):
        return self.api

    def _connect_api_to_best_server(self, api, **_kwargs):
        assert api is self.api
        return "server-1"


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def test_close_completes_queued_requests_instead_of_stranding_them():
    api = _BlockingApi()
    runtime = RealtimeQuoteRuntime(_Provider(api), _Log())
    first_done = threading.Event()
    second_done = threading.Event()
    second_errors: list[BaseException] = []

    def _first_request() -> None:
        try:
            runtime.request([("000001", 0)], timeout_sec=2.0)
        except (RuntimeError, TimeoutError):
            pass
        finally:
            first_done.set()

    def _second_request() -> None:
        try:
            runtime.request([("600000", 1)], timeout_sec=1.0)
        except BaseException as exc:  # noqa: BLE001 - record the exact terminal outcome.
            second_errors.append(exc)
        finally:
            second_done.set()

    first = threading.Thread(target=_first_request)
    second = threading.Thread(target=_second_request)
    first.start()
    try:
        assert api.started.wait(0.5)
        second.start()
        assert _wait_until(lambda: runtime.snapshot()["inflight"] == 2)

        runtime.close()

        assert second_done.wait(0.25)
        assert len(second_errors) == 1
        assert isinstance(second_errors[0], RuntimeError)
        assert "关闭" in str(second_errors[0])
        assert runtime.snapshot()["inflight"] <= 1
    finally:
        api.release.set()
        first.join(2.0)
        second.join(2.0)
        runtime.close()


def test_request_after_close_is_rejected_without_incrementing_inflight():
    api = _BlockingApi()
    api.release.set()
    runtime = RealtimeQuoteRuntime(_Provider(api), _Log())
    runtime.close()

    with pytest.raises(RuntimeError, match="关闭"):
        runtime.request([("000001", 0)], timeout_sec=0.1)

    assert runtime.snapshot()["inflight"] == 0


def test_close_never_disconnects_an_active_api_from_the_calling_thread():
    api = _BlockingApi()
    runtime = RealtimeQuoteRuntime(_Provider(api), _Log())
    request_done = threading.Event()

    def _request() -> None:
        try:
            runtime.request([("000001", 0)], timeout_sec=2.0)
        except RuntimeError:
            pass
        finally:
            request_done.set()

    requester = threading.Thread(target=_request)
    requester.start()
    try:
        assert api.started.wait(0.5)

        assert runtime.close(timeout_sec=0.01) is False
        assert api.disconnected is False

        api.release.set()
        assert request_done.wait(1.0)
        assert _wait_until(lambda: not runtime.is_alive())
        assert api.disconnected is True
        assert api.disconnect_thread_id == api.quote_thread_id
    finally:
        api.release.set()
        requester.join(2.0)
        runtime.close(timeout_sec=1.0)


def test_runtime_success_and_empty_response_failure_reach_terminal_state():
    success_api = _BlockingApi()
    success_api.release.set()
    success_runtime = RealtimeQuoteRuntime(_Provider(success_api), _Log())
    try:
        assert success_runtime.is_alive() is True
        assert success_runtime.request([("000001", 0)], timeout_sec=0.5) == [
            {"params": [("000001", 0)]}
        ]
        success_snapshot = success_runtime.snapshot()
        assert success_snapshot["inflight"] == 0
        assert success_snapshot["last_success_at"] > 0
        assert success_snapshot["consecutive_failures"] == 0
        assert success_snapshot["reconnect_count"] == 1
    finally:
        assert success_runtime.close(timeout_sec=1.0) is True
        assert success_runtime.close(timeout_sec=0.0) is True

    class _EmptyApi(_BlockingApi):
        def get_security_quotes(self, _params):
            return []

    empty_api = _EmptyApi()
    empty_runtime = RealtimeQuoteRuntime(_Provider(empty_api), _Log())
    try:
        with pytest.raises(RuntimeError, match="空结果"):
            empty_runtime.request([("600000", 1)], timeout_sec=0.5)
        assert empty_runtime.snapshot()["consecutive_failures"] == 1
        assert empty_api.disconnected is True
    finally:
        empty_runtime.close(timeout_sec=1.0)


def test_runtime_logs_disconnect_failure_without_leaking_thread():
    class _BadDisconnectApi(_BlockingApi):
        def disconnect(self) -> None:
            raise RuntimeError("disconnect failed")

    api = _BadDisconnectApi()
    api.release.set()
    logger = _Log()
    runtime = RealtimeQuoteRuntime(_Provider(api), logger)
    runtime.request([("000001", 0)], timeout_sec=0.5)

    assert runtime.close(timeout_sec=1.0) is True
    assert any("断开实时 pytdx 连接失败" in message for message in logger.messages)
