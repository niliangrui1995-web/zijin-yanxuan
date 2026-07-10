# -*- coding: utf-8 -*-
"""Small registry for stable service toggles used by runtime orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceToggle:
    key: str
    enabled_by_default: bool = True
    description: str = ""


class ServiceToggleRegistry:
    ENV_PREFIX = "VCP_TOGGLE_"

    def __init__(self):
        self._toggles: dict[str, ServiceToggle] = {}

    @staticmethod
    def _normalize(key: str) -> str:
        return str(key or "").strip()

    def register(self, key: str, *, enabled_by_default: bool = True, description: str = "") -> ServiceToggle:
        normalized = self._normalize(key)
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
        normalized = self._normalize(key)
        return self._toggles.get(normalized) if normalized else None

    def override_env_name(self, key: str) -> str:
        normalized = self._normalize(key)
        if not normalized:
            raise ValueError("toggle key must not be blank")
        sanitized = "".join(char if char.isalnum() else "_" for char in normalized.upper())
        return f"{self.ENV_PREFIX}{sanitized}"

    def _read_env_override(self, key: str) -> bool | None:
        raw = os.getenv(self.override_env_name(key))
        if raw is None:
            return None

        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
        return None

    def is_enabled(self, key: str, overrides: dict[str, bool] | None = None) -> bool:
        toggle = self.get(key)
        if toggle is None:
            raise KeyError(f"toggle is not registered: {key}")
        if overrides and toggle.key in overrides:
            return bool(overrides[toggle.key])

        env_override = self._read_env_override(toggle.key)
        if env_override is not None:
            return env_override

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
    "daily_global_earnings_calendar_sync",
    enabled_by_default=True,
    description="Daily fixed-time background global oligarch earnings calendar sync while the app is running.",
)
service_toggle_registry.register(
    "startup_history_cache_load",
    enabled_by_default=True,
    description="Preload the full local market-history cache during startup instead of loading it on demand.",
)
