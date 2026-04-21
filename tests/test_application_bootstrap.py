from __future__ import annotations

import types

from app.bootstrap.application_bootstrap import ApplicationBootstrap
from core.observability import clear_metric_history, metric_history


class _DummyWindow:
    def __init__(self):
        self.data_provider = object()
        self.central_quotes_svc = "stale-service"
        self._workspace = types.SimpleNamespace(
            get_realtime_quote_codes=lambda: {"000001", "600519"}
        )
        self.created_central_quotes_services = []
        self.workspace_parent = None
        self.replaced_workspace = None
        self.tabs_wrapper = object()

    def create_central_quotes_service(self, *, code_supplier=None):
        service = {
            "main_window": self,
            "data_provider": self.data_provider,
            "code_supplier": code_supplier,
        }
        self.created_central_quotes_services.append(service)
        return service

    def create_workspace(self, *, parent=None):
        self.workspace_parent = parent
        return {"workspace": True}

    def replace_workspace(self, workspace):
        self.replaced_workspace = workspace


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

    monkeypatch.setattr(
        "app.bootstrap.application_bootstrap.service_toggle_registry.is_enabled",
        lambda *_args, **_kwargs: True,
    )

    service = bootstrap.install_central_quotes()

    assert service is window.central_quotes_svc
    assert len(window.created_central_quotes_services) == 1
    assert service["main_window"] is window
    assert service["data_provider"] is window.data_provider
    assert callable(service["code_supplier"])
    assert service["code_supplier"]() == {"000001", "600519"}


def test_mount_workspace_uses_host_factory_and_replace_hook():
    clear_metric_history()
    window = _DummyWindow()
    bootstrap = ApplicationBootstrap(window)

    workspace = bootstrap.mount_workspace()

    assert workspace == {"workspace": True}
    assert window.workspace_parent is window.tabs_wrapper
    assert window.replaced_workspace == {"workspace": True}
    samples = metric_history("workspace_mount_ms")
    assert samples
    assert samples[-1].unit == "ms"
