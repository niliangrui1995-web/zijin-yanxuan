# -*- coding: utf-8 -*-
"""Typed task registry for stable background task identifiers."""

from __future__ import annotations

from dataclasses import dataclass

from domains.runtime import TaskCategory


@dataclass(frozen=True)
class TaskKey:
    task_id: str
    category: TaskCategory
    description: str = ""

    def __str__(self) -> str:
        return self.task_id


TaskKeyLike = TaskKey | str | None


class TypedTaskRegistry:
    def __init__(self):
        self._known: dict[str, TaskKey] = {}

    def register(self, task_id: str, *, category: TaskCategory, description: str = "") -> TaskKey:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id must not be blank")
        task_key = self._known.get(normalized)
        if task_key is not None:
            return task_key
        task_key = TaskKey(
            task_id=normalized,
            category=category,
            description=str(description or "").strip(),
        )
        self._known[normalized] = task_key
        return task_key

    def resolve(self, value: TaskKeyLike) -> str | None:
        if value is None:
            return None
        if isinstance(value, TaskKey):
            return value.task_id
        normalized = str(value).strip()
        return normalized or None

    def category_for(self, value: TaskKeyLike) -> TaskCategory | None:
        task_id = self.resolve(value)
        task_key = self._known.get(task_id or "")
        return task_key.category if task_key is not None else None

    def quote_refresh(self, scope: str) -> TaskKey:
        normalized_scope = str(scope or "").strip()
        if not normalized_scope:
            raise ValueError("quote refresh scope must not be blank")
        if normalized_scope.endswith("_quotes"):
            task_id = normalized_scope
        else:
            task_id = f"{normalized_scope}_quotes"
        return self.register(
            task_id,
            category=TaskCategory.QUOTES,
            description=f"Quote refresh for {normalized_scope}",
        )

    def startup(self, task_id: str, *, description: str = "") -> TaskKey:
        return self.register(task_id, category=TaskCategory.STARTUP, description=description)

    def network(self, task_id: str, *, description: str = "") -> TaskKey:
        return self.register(task_id, category=TaskCategory.NETWORK, description=description)

    def quotes(self, task_id: str, *, description: str = "") -> TaskKey:
        return self.register(task_id, category=TaskCategory.QUOTES, description=description)

    def window(self, task_id: str, *, description: str = "") -> TaskKey:
        return self.register(task_id, category=TaskCategory.WINDOW, description=description)

    def transient_quotes(self, task_id: str, *, description: str = "") -> TaskKey:
        return self._transient(task_id, category=TaskCategory.QUOTES, description=description)

    def transient_window(self, task_id: str, *, description: str = "") -> TaskKey:
        return self._transient(task_id, category=TaskCategory.WINDOW, description=description)

    @staticmethod
    def _transient(task_id: str, *, category: TaskCategory, description: str = "") -> TaskKey:
        """Build a typed key for generation-scoped work without retaining it globally."""
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id must not be blank")
        return TaskKey(
            task_id=normalized,
            category=category,
            description=str(description or "").strip(),
        )

    def workspace(self, task_id: str, *, description: str = "") -> TaskKey:
        return self.register(task_id, category=TaskCategory.WORKSPACE, description=description)


task_registry = TypedTaskRegistry()

STARTUP_DEFERRED_LOAD = task_registry.register(
    "deferred_load",
    category=TaskCategory.STARTUP,
    description="Deferred cache bootstrap",
)
STARTUP_ASIAN_DATA_SYNC = task_registry.register(
    "asian_data_sync_bg",
    category=TaskCategory.STARTUP,
    description="Asian market silent sync",
)
STARTUP_SMART = task_registry.register(
    "smart_startup",
    category=TaskCategory.STARTUP,
    description="Smart startup connectivity probe",
)
STARTUP_F5_RETENTION = task_registry.register(
    "f5_startup_retention",
    category=TaskCategory.STARTUP,
    description="Prune stale isolated F5 runtime artifacts after first paint",
)
STARTUP_DATA_PROVIDER = task_registry.register(
    "startup_data_provider",
    category=TaskCategory.STARTUP,
    description="Build the local market-data provider after first paint",
)
NETWORK_GO_ONLINE = task_registry.register(
    "go_online",
    category=TaskCategory.NETWORK,
    description="Switch data provider online",
)
NETWORK_FORCE_RECONNECT = task_registry.register(
    "force_reconnect",
    category=TaskCategory.NETWORK,
    description="Reconnect realtime quote provider",
)
SHARED_MARKET_CAPS = task_registry.register(
    "shared_market_caps",
    category=TaskCategory.QUOTES,
    description="Shared finance / market-cap refresh batch",
)
CENTRAL_QUOTES_POLL = task_registry.register(
    "central_quotes",
    category=TaskCategory.QUOTES,
    description="Central realtime quote poller",
)


def task_id_of(value: TaskKeyLike) -> str | None:
    return task_registry.resolve(value)
