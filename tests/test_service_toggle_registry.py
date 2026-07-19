from __future__ import annotations

from infra.features import service_toggle_registry
from infra.features.service_toggle_registry import ServiceToggleRegistry


def test_service_toggle_registry_exposes_default_runtime_toggles():
    snapshot = service_toggle_registry.snapshot()

    assert "central_quotes_service" in snapshot
    assert "silent_asian_sync" in snapshot
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


def test_service_toggle_registry_covers_validation_duplicate_and_overrides(monkeypatch):
    registry = ServiceToggleRegistry()

    try:
        registry.register(" ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank toggle key should fail")

    toggle = registry.register("feature.alpha", enabled_by_default=False, description="Alpha")
    assert registry.register("feature.alpha") is toggle
    assert registry.get(" ") is None

    try:
        registry.override_env_name(" ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank env override key should fail")

    monkeypatch.setenv(registry.override_env_name("feature.alpha"), "maybe")
    assert registry.is_enabled("feature.alpha") is False
    assert registry.is_enabled("feature.alpha", overrides={"feature.alpha": True}) is True

    try:
        registry.is_enabled("missing")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown toggle key should fail")
