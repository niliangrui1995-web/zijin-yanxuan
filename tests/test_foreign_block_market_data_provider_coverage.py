# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import infra.market_data.foreign_block_provider as provider
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded


class _Token:
    def __init__(self, remaining: float | None, *, raise_on_call: int | None = None) -> None:
        self.remaining = remaining
        self.raise_on_call = raise_on_call
        self.raise_calls = 0

    def remaining_seconds(self) -> float | None:
        return self.remaining

    def raise_if_cancelled(self) -> None:
        self.raise_calls += 1
        if self.raise_calls == self.raise_on_call:
            raise TaskDeadlineExceeded("deadline")


def test_bounded_timeout_handles_floor_unbounded_and_deadline_limits() -> None:
    assert provider._bounded_timeout(-5, None) == 0.1
    assert provider._bounded_timeout(8, _Token(None)) == 8
    assert provider._bounded_timeout(8, _Token(2.5)) == 2.5
    assert provider._bounded_timeout(0.01, _Token(5)) == 0.1

    expired = _Token(0, raise_on_call=2)
    with pytest.raises(TaskDeadlineExceeded, match="deadline"):
        provider._bounded_timeout(8, expired)
    assert expired.raise_calls == 2


def test_calendar_subprocess_protocol_builds_isolated_command_and_converts_values(monkeypatch) -> None:
    observed: dict = {}
    token = _Token(4)

    def fake_process_env(*, extra):
        observed["env_extra"] = extra
        return {"SAFE": "1"}

    monkeypatch.setattr(provider, "build_domestic_process_env", fake_process_env)
    monkeypatch.setattr(provider, "windows_no_window_kwargs", lambda: {"creationflags": 123})

    def fake_run_process(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(stdout='["2026-07-10", 20260711, null]\n')

    monkeypatch.setattr(provider, "run_process", fake_run_process)

    assert provider.fetch_trade_calendar(timeout=9, cancellation_token=token) == [
        "2026-07-10",
        "20260711",
        "None",
    ]
    assert observed["env_extra"] == {"PYTHONIOENCODING": "utf-8"}
    assert observed["command"][:3] == [provider.sys.executable, "-c", provider._AKSHARE_FETCH_SNIPPET]
    assert observed["command"][3:] == ["calendar"]
    assert observed["kwargs"] == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
        "timeout": 4,
        "env": {"SAFE": "1"},
        "creationflags": 123,
        "check": True,
    }
    assert token.raise_calls == 2


def test_block_trade_subprocess_protocol_filters_non_record_payloads(monkeypatch) -> None:
    observed: dict = {}
    monkeypatch.setattr(provider, "build_domestic_process_env", lambda **_kwargs: {})
    monkeypatch.setattr(provider, "windows_no_window_kwargs", dict)

    def fake_run_process(command, **kwargs):
        observed["command"] = command
        observed["timeout"] = kwargs["timeout"]
        return SimpleNamespace(stdout='[{"代码":"000001"}, 7, null, ["x"], {"代码":"600000"}]')

    monkeypatch.setattr(provider, "run_process", fake_run_process)

    assert provider.fetch_block_trades(
        20260701,
        20260710,
        timeout=6,
    ) == [{"代码": "000001"}, {"代码": "600000"}]
    assert observed["command"][3:] == ["block_trade", "20260701", "20260710"]
    assert observed["timeout"] == 6


@pytest.mark.parametrize("stdout", ["", "  \n"])
def test_subprocess_protocol_treats_blank_stdout_as_empty(monkeypatch, stdout: str) -> None:
    monkeypatch.setattr(provider, "build_domestic_process_env", lambda **_kwargs: {})
    monkeypatch.setattr(provider, "windows_no_window_kwargs", dict)
    monkeypatch.setattr(provider, "run_process", lambda *_args, **_kwargs: SimpleNamespace(stdout=stdout))

    assert provider._run_akshare_process("calendar", timeout=1) == []


def test_subprocess_protocol_surfaces_malformed_json(monkeypatch) -> None:
    monkeypatch.setattr(provider, "build_domestic_process_env", lambda **_kwargs: {})
    monkeypatch.setattr(provider, "windows_no_window_kwargs", dict)
    monkeypatch.setattr(provider, "run_process", lambda *_args, **_kwargs: SimpleNamespace(stdout="not-json"))

    with pytest.raises(json.JSONDecodeError):
        provider._run_akshare_process("calendar", timeout=1)


def test_subprocess_result_is_rejected_when_owner_cancels_after_process(monkeypatch) -> None:
    token = _Token(5, raise_on_call=2)
    calls = []
    monkeypatch.setattr(provider, "build_domestic_process_env", lambda **_kwargs: {})
    monkeypatch.setattr(provider, "windows_no_window_kwargs", dict)

    def fake_run_process(*_args, **_kwargs):
        calls.append("run")
        return SimpleNamespace(stdout="[]")

    monkeypatch.setattr(provider, "run_process", fake_run_process)

    with pytest.raises(TaskDeadlineExceeded, match="deadline"):
        provider._run_akshare_process("calendar", timeout=10, cancellation_token=token)
    assert calls == ["run"]


def test_subprocess_is_not_started_when_owner_is_already_cancelled(monkeypatch) -> None:
    class CancelledToken:
        def raise_if_cancelled(self) -> None:
            raise TaskCancelledError("shutdown")

    monkeypatch.setattr(
        provider,
        "run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start")),
    )

    with pytest.raises(TaskCancelledError, match="shutdown"):
        provider.fetch_trade_calendar(timeout=10, cancellation_token=CancelledToken())
