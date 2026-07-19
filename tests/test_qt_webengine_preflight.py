import threading

from infra.diagnostics import qt_webengine_preflight as preflight


class _CompletedProcess:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.communicate_timeouts = []
        self.waited = False

    def communicate(self, timeout=None):
        self.communicate_timeouts.append(timeout)
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def wait(self):
        self.waited = True
        return self.returncode


class _StubbornProcess:
    def __init__(self, *, cancellation_event=None):
        self.returncode = None
        self.cancellation_event = cancellation_event
        self.actions = []
        self.terminated = False
        self.killed = False
        self.waited = False

    def communicate(self, timeout=None):
        self.actions.append(("communicate", timeout))
        if self.killed:
            return "cancel-out", "cancel-err"
        if self.terminated:
            raise preflight.subprocess.TimeoutExpired("preflight", timeout)
        if self.cancellation_event is not None:
            self.cancellation_event.set()
        raise preflight.subprocess.TimeoutExpired("preflight", timeout)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.actions.append(("terminate", None))
        self.terminated = True

    def kill(self):
        self.actions.append(("kill", None))
        self.killed = True
        self.returncode = -9

    def wait(self):
        self.actions.append(("wait", None))
        self.waited = True
        return self.returncode


class _UnkillableProcess:
    def __init__(self):
        self.returncode = None
        self.actions = []

    def communicate(self, timeout=None):
        self.actions.append(("communicate", timeout))
        raise preflight.subprocess.TimeoutExpired("preflight", timeout)

    def poll(self):
        return None

    def terminate(self):
        self.actions.append(("terminate", None))

    def kill(self):
        self.actions.append(("kill", None))

    def wait(self):
        raise AssertionError("an unreaped child must never enter an unbounded wait")


def test_qt_webengine_preflight_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VCP_KLINE_WEBENGINE_PREFLIGHT", "0")
    monkeypatch.setattr(
        preflight.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preflight should be disabled")),
    )

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is True
    assert result["disabled"] is True


def test_qt_webengine_preflight_hides_child_process(monkeypatch):
    captured = {}

    process = _CompletedProcess()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight, "windows_no_window_kwargs", lambda: {"creationflags": 123})
    monkeypatch.setattr(preflight.subprocess, "Popen", fake_popen)

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is True
    assert captured["command"][1] == "-c"
    expected_flags = 123
    if preflight.os.name == "nt":
        expected_flags |= preflight.subprocess.BELOW_NORMAL_PRIORITY_CLASS
    assert captured["kwargs"]["creationflags"] == expected_flags
    assert captured["kwargs"]["stdout"] is preflight.subprocess.PIPE
    assert captured["kwargs"]["stderr"] is preflight.subprocess.PIPE
    assert process.waited


def test_qt_webengine_preflight_uses_below_normal_priority_on_windows(monkeypatch):
    monkeypatch.setattr(preflight.os, "name", "nt")
    monkeypatch.setattr(preflight, "windows_no_window_kwargs", lambda: {"creationflags": 0x08000000})

    kwargs = preflight._webengine_subprocess_kwargs()

    assert kwargs["creationflags"] & 0x08000000
    assert kwargs["creationflags"] & preflight.subprocess.BELOW_NORMAL_PRIORITY_CLASS


def test_qt_webengine_preflight_smoke_uses_windowless_page():
    smoke_code = preflight._webengine_smoke_code()

    assert "QWebEnginePage" in smoke_code
    assert "QWebEngineView" not in smoke_code
    assert 'if not load_state["done"]' in smoke_code
    assert "sys.exit(3)" in smoke_code


def test_qt_webengine_preflight_formats_missing_or_invalid_returncode():
    assert preflight._returncode_hex(None) == ""
    assert preflight._returncode_hex("not-a-code") == ""


def test_qt_webengine_preflight_reports_timeout(monkeypatch):
    process = _StubbornProcess()
    ticks = iter((0.0, 0.0, 3.0, 3.0))
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(preflight.time, "perf_counter", lambda: next(ticks, 3.0))

    result = preflight.check_qt_webengine_available(timeout_s=2)

    assert result["ok"] is False
    assert result["timeout"] is True
    assert result["reason"] == "timeout>2s"
    assert result["elapsed_ms"] >= 0
    assert process.killed and process.waited
    assert [name for name, _value in process.actions][-5:] == [
        "terminate",
        "communicate",
        "kill",
        "communicate",
        "wait",
    ]


def test_qt_webengine_preflight_reports_subprocess_errors(monkeypatch):
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        preflight.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is False
    assert result["reason"] == "spawn failed"
    assert result["elapsed_ms"] >= 0


def test_qt_webengine_preflight_clamps_timeout(monkeypatch):
    process = _CompletedProcess(returncode=3, stdout="out" * 1000, stderr="err" * 1000)

    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = preflight.check_qt_webengine_available(timeout_s=0)

    assert 0 < process.communicate_timeouts[0] <= preflight._PREFLIGHT_POLL_SECONDS
    assert result["ok"] is False
    assert result["reason"] == "returncode=3 0x3"
    assert result["stdout"].endswith("out")
    assert result["stderr"].endswith("err")


def test_qt_webengine_preflight_cancels_before_spawn(monkeypatch):
    cancellation_event = threading.Event()
    cancellation_event.set()
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        preflight.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled preflight should not spawn")),
    )

    result = preflight.check_qt_webengine_available(cancellation_event=cancellation_event)

    assert result["ok"] is False
    assert result["cancelled"] is True
    assert result["reason"] == "cancelled"


def test_qt_webengine_preflight_cancel_terminates_kills_and_waits(monkeypatch):
    cancellation_event = threading.Event()
    process = _StubbornProcess(cancellation_event=cancellation_event)
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = preflight.check_qt_webengine_available(cancellation_event=cancellation_event)

    assert result["cancelled"] is True
    assert process.terminated and process.killed and process.waited
    assert process.poll() == -9
    assert [name for name, _value in process.actions][-5:] == [
        "terminate",
        "communicate",
        "kill",
        "communicate",
        "wait",
    ]


def test_qt_webengine_preflight_hard_cleanup_deadline_reports_unclean_child(monkeypatch):
    process = _UnkillableProcess()
    ticks = iter((0.0, 0.0, 3.0, 3.0))
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(preflight.time, "perf_counter", lambda: next(ticks, 3.0))

    result = preflight.check_qt_webengine_available(timeout_s=2)

    assert result["timeout"] is True
    assert result["process_cleanup_ok"] is False
    assert [name for name, _value in process.actions][-4:] == [
        "terminate",
        "communicate",
        "kill",
        "communicate",
    ]
