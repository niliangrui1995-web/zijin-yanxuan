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
