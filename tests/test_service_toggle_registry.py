from __future__ import annotations

from infra.features import service_toggle_registry


def test_service_toggle_registry_exposes_default_runtime_toggles():
    snapshot = service_toggle_registry.snapshot()

    assert "central_quotes_service" in snapshot
    assert "silent_asian_sync" in snapshot
    assert "workspace_auto_rt_monitor" in snapshot
    assert "startup_history_cache_load" in snapshot
    assert service_toggle_registry.is_enabled("central_quotes_service") is True
    assert service_toggle_registry.is_enabled("startup_history_cache_load") is False


def test_service_toggle_registry_reads_env_override(monkeypatch):
    env_name = service_toggle_registry.override_env_name("central_quotes_service")
    monkeypatch.delenv(env_name, raising=False)

    monkeypatch.setenv(env_name, "0")
    assert service_toggle_registry.is_enabled("central_quotes_service") is False

    monkeypatch.setenv(env_name, "enabled")
    assert service_toggle_registry.is_enabled("central_quotes_service") is True
