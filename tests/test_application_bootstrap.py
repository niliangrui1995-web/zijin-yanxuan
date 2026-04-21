from __future__ import annotations

import types

from app.bootstrap.application_bootstrap import ApplicationBootstrap


class _DummyWindow:
    def __init__(self):
        self.data_provider = object()
        self.central_quotes_svc = "stale-service"
        self._workspace = types.SimpleNamespace(
            get_realtime_quote_codes=lambda: {"000001", "600519"}
        )


def test_install_central_quotes_respects_disabled_toggle(monkeypatch):
    window = _DummyWindow()
    bootstrap = ApplicationBootstrap(window)

    def fake_is_enabled(key, overrides=None):
        return False if key == "central_quotes_service" else True

    monkeypatch.setattr("app.bootstrap.application_bootstrap.service_toggle_registry.is_enabled", fake_is_enabled)

    assert bootstrap.install_central_quotes() is None
    assert window.central_quotes_svc is None


def test_install_central_quotes_wires_code_supplier(monkeypatch):
    window = _DummyWindow()
    bootstrap = ApplicationBootstrap(window)
    captured = {}

    class _FakeCentralQuotesService:
        def __init__(self, main_window, data_provider, code_supplier=None):
            captured["main_window"] = main_window
            captured["data_provider"] = data_provider
            captured["code_supplier"] = code_supplier

    monkeypatch.setattr(
        "app.bootstrap.application_bootstrap.service_toggle_registry.is_enabled",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.bootstrap.application_bootstrap.CentralQuotesService",
        _FakeCentralQuotesService,
    )

    service = bootstrap.install_central_quotes()

    assert service is window.central_quotes_svc
    assert captured["main_window"] is window
    assert captured["data_provider"] is window.data_provider
    assert callable(captured["code_supplier"])
    assert captured["code_supplier"]() == {"000001", "600519"}
