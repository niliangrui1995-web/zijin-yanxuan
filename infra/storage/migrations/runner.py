"""Atomic, reversible SQLite migrations based on ``PRAGMA user_version``."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from infra.storage.migrations.models import Migration
from infra.storage.migrations.registry import MIGRATIONS


class MigrationError(RuntimeError):
    """Base error for invalid or unsupported schema migration state."""


class InvalidMigrationChainError(MigrationError):
    """Raised when the configured migration registry is not reversible and contiguous."""


class InvalidTargetSchemaVersionError(MigrationError):
    """Raised when a requested target is outside the configured migration chain."""


class UnsupportedSchemaVersionError(MigrationError):
    """Raised when a database was created by a newer application version."""


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A locked database transition plan supplied to pre-change hooks."""

    current_version: int
    target_version: int
    steps: tuple[Migration, ...]

    @property
    def changes_schema(self) -> bool:
        return self.current_version != self.target_version


BeforeMigration = Callable[[MigrationPlan], None]


def _deny_transaction_control(
    action_code: int,
    _arg1: str | None,
    _arg2: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    """Keep migration callbacks inside the transaction owned by this runner."""
    if action_code == sqlite3.SQLITE_TRANSACTION:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def read_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _validated_migrations(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    ordered = tuple(migrations)
    names: set[str] = set()
    for expected_version, migration in enumerate(ordered, start=1):
        name = migration.name.strip()
        if migration.version != expected_version:
            raise InvalidMigrationChainError(
                "migration chain must be contiguous from version 1: "
                f"expected {expected_version}, got {migration.version} ({migration.name})"
            )
        if not name or name in names:
            raise InvalidMigrationChainError(
                f"migration {migration.version} must have a unique, non-empty name"
            )
        if not callable(migration.upgrade) or not callable(migration.downgrade):
            raise InvalidMigrationChainError(
                f"migration {migration.version} must define callable upgrade and downgrade steps"
            )
        names.add(name)
    return ordered


def _resolve_target(target_version: int | None, latest: int) -> int:
    target = latest if target_version is None else target_version
    if type(target) is not int or not 0 <= target <= latest:
        raise InvalidTargetSchemaVersionError(
            f"target schema version must be an integer between 0 and {latest}, got {target!r}"
        )
    return target


def _build_plan(connection: sqlite3.Connection, ordered: tuple[Migration, ...], target: int) -> MigrationPlan:
    current = read_schema_version(connection)
    latest = ordered[-1].version if ordered else 0
    if current > latest:
        raise UnsupportedSchemaVersionError(
            f"database schema version {current} is newer than supported version {latest}"
        )
    if current < target:
        steps = ordered[current:target]
    else:
        steps = tuple(reversed(ordered[target:current]))
    return MigrationPlan(current, target, steps)


def plan_migration(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    target_version: int | None = None,
) -> MigrationPlan:
    """Validate the full chain and describe a transition without mutating the database."""
    ordered = _validated_migrations(migrations)
    latest = ordered[-1].version if ordered else 0
    return _build_plan(connection, ordered, _resolve_target(target_version, latest))


def _apply_plan(connection: sqlite3.Connection, plan: MigrationPlan) -> None:
    upgrading = plan.target_version > plan.current_version
    for migration in plan.steps:
        if upgrading:
            migration.upgrade(connection)
            version = migration.version
        else:
            migration.downgrade(connection)
            version = migration.version - 1
        connection.execute(f"PRAGMA user_version = {version:d}")


def run_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    target_version: int | None = None,
    before_change: BeforeMigration | None = None,
) -> int:
    """Move to ``target_version`` atomically; default to the registry's latest version."""
    initial_plan = plan_migration(connection, migrations, target_version=target_version)
    if not initial_plan.changes_schema:
        return initial_plan.target_version
    if connection.in_transaction:
        raise MigrationError("schema migration requires an idle SQLite connection")

    connection.execute("BEGIN IMMEDIATE")
    committed = False
    authorizer_installed = False
    try:
        locked_plan = plan_migration(connection, migrations, target_version=target_version)
        connection.set_authorizer(_deny_transaction_control)
        authorizer_installed = True
        if locked_plan.changes_schema and before_change is not None:
            before_change(locked_plan)
        _apply_plan(connection, locked_plan)
        connection.set_authorizer(None)
        authorizer_installed = False
        connection.commit()
        committed = True
    finally:
        if authorizer_installed:
            connection.set_authorizer(None)
        if not committed and connection.in_transaction:
            connection.rollback()
    return locked_plan.target_version
