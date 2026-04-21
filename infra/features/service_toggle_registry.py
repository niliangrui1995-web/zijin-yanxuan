# -*- coding: utf-8 -*-
"""Small registry for stable service toggles used by runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceToggle:
    key: str
    enabled_by_default: bool = True
    description: str = ""


class ServiceToggleRegistry:
    def __init__(self):
        self._toggles: dict[str, ServiceToggle] = {}

    def register(self, key: str, *, enabled_by_default: bool = True, description: str = "") -> ServiceToggle:
        normalized = str(key or "").strip()
        if not normalized:
            raise ValueError("toggle key must not be blank")
        existing = self._toggles.get(normalized)
        if existing is not None:
            return existing
        toggle = ServiceToggle(
            key=normalized,
            enabled_by_default=bool(enabled_by_default),
            description=str(description or "").strip(),
        )
        self._toggles[normalized] = toggle
        return toggle

    def get(self, key: str) -> ServiceToggle | None:
        normalized = str(key or "").strip()
        return self._toggles.get(normalized) if normalized else None

    def is_enabled(self, key: str, overrides: dict[str, bool] | None = None) -> bool:
        toggle = self.get(key)
        if toggle is None:
            raise KeyError(f"toggle is not registered: {key}")
        if overrides and toggle.key in overrides:
            return bool(overrides[toggle.key])
        return bool(toggle.enabled_by_default)

    def snapshot(self) -> dict[str, ServiceToggle]:
        return dict(self._toggles)


service_toggle_registry = ServiceToggleRegistry()

service_toggle_registry.register(
    "central_quotes_service",
    enabled_by_default=True,
    description="Central realtime quote poller wired by the main window shell.",
)
service_toggle_registry.register(
    "silent_asian_sync",
    enabled_by_default=True,
    description="Background Asian-market cache sync scheduled during startup.",
)
service_toggle_registry.register(
    "workspace_auto_rt_monitor",
    enabled_by_default=True,
    description="Auto-start intraday monitor when market session and data conditions are met.",
)
