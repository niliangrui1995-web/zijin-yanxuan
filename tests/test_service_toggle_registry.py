from __future__ import annotations

from infra.features import service_toggle_registry


def test_service_toggle_registry_exposes_default_runtime_toggles():
    snapshot = service_toggle_registry.snapshot()

    assert "central_quotes_service" in snapshot
    assert "silent_asian_sync" in snapshot
    assert "workspace_auto_rt_monitor" in snapshot
    assert service_toggle_registry.is_enabled("central_quotes_service") is True
