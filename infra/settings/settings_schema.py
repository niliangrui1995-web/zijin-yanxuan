from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from PyQt6.QtCore import QSettings


class SettingsSchemaVersion:
    KEY = "settings/schema_version"
    CURRENT = 1


@dataclass(frozen=True)
class SettingsMigrationStep:
    target_version: int
    description: str
    handler: Callable[["QSettings"], None]


def _bootstrap_v1(_settings: "QSettings") -> None:
    """Reserve schema v1 for the centralized settings repository."""


DEFAULT_SETTINGS_MIGRATIONS: tuple[SettingsMigrationStep, ...] = (
    SettingsMigrationStep(
        target_version=SettingsSchemaVersion.CURRENT,
        description="bootstrap centralized settings repository",
        handler=_bootstrap_v1,
    ),
)


class SettingsMigrator:
    def __init__(
        self,
        settings: "QSettings",
        steps: Iterable[SettingsMigrationStep] = DEFAULT_SETTINGS_MIGRATIONS,
    ) -> None:
        self._settings = settings
        self._steps = tuple(sorted(steps, key=lambda step: step.target_version))

    def current_version(self) -> int:
        return int(self._settings.value(SettingsSchemaVersion.KEY, 0, type=int) or 0)

    def migrate(self) -> int:
        version = self.current_version()
        for step in self._steps:
            if step.target_version <= version:
                continue
            step.handler(self._settings)
            self._settings.setValue(SettingsSchemaVersion.KEY, step.target_version)
            version = step.target_version
        self._settings.sync()
        return version
