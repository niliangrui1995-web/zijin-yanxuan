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
