import types

from infra.diagnostics import qt_webengine_preflight as preflight


def test_qt_webengine_preflight_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VCP_KLINE_WEBENGINE_PREFLIGHT", "0")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preflight should be disabled")),
    )

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is True
    assert result["disabled"] is True


def test_qt_webengine_preflight_hides_child_process(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight, "windows_no_window_kwargs", lambda: {"creationflags": 123})
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is True
    assert captured["command"][1] == "-c"
    assert captured["kwargs"]["creationflags"] == 123


def test_qt_webengine_preflight_smoke_uses_windowless_page():
    smoke_code = preflight._webengine_smoke_code()

    assert "QWebEnginePage" in smoke_code
    assert "QWebEngineView" not in smoke_code


def test_qt_webengine_preflight_formats_missing_or_invalid_returncode():
    assert preflight._returncode_hex(None) == ""
    assert preflight._returncode_hex("not-a-code") == ""


def test_qt_webengine_preflight_reports_timeout(monkeypatch):
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(preflight.subprocess.TimeoutExpired("cmd", 1)),
    )

    result = preflight.check_qt_webengine_available(timeout_s=2)

    assert result["ok"] is False
    assert result["timeout"] is True
    assert result["reason"] == "timeout>2s"
    assert result["elapsed_ms"] >= 0


def test_qt_webengine_preflight_reports_subprocess_errors(monkeypatch):
    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    result = preflight.check_qt_webengine_available()

    assert result["ok"] is False
    assert result["reason"] == "spawn failed"
    assert result["elapsed_ms"] >= 0


def test_qt_webengine_preflight_clamps_timeout(monkeypatch):
    captured = {}

    def fake_run(_command, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(returncode=3, stdout="out" * 1000, stderr="err" * 1000)

    monkeypatch.delenv("VCP_KLINE_WEBENGINE_PREFLIGHT", raising=False)
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.check_qt_webengine_available(timeout_s=0)

    assert captured["timeout"] == 1
    assert result["ok"] is False
    assert result["reason"] == "returncode=3 0x3"
    assert result["stdout"].endswith("out")
    assert result["stderr"].endswith("err")
