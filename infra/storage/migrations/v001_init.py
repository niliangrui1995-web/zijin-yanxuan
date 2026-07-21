"""Initial DataStore schema."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from infra.storage.migrations.models import Migration


class InvalidDataStoreSchemaError(RuntimeError):
    """Raised when a legacy ``kv_store`` table is incompatible with DataStore."""


@dataclass(frozen=True, slots=True)
class _ColumnInfo:
    declared_type: str
    not_null: bool
    default: object
    primary_key: int


def _read_kv_store_columns(connection: sqlite3.Connection) -> dict[str, _ColumnInfo]:
    return {
        str(row[1]): _ColumnInfo(
            declared_type=str(row[2]).upper(),
            not_null=bool(row[3]),
            default=row[4],
            primary_key=int(row[5]),
        )
        for row in connection.execute("PRAGMA table_info(kv_store)").fetchall()
    }


def _validate_required_columns(columns: dict[str, _ColumnInfo]) -> None:
    required = {"key", "value", "updated_at"}
    missing = required.difference(columns)
    if missing:
        raise InvalidDataStoreSchemaError(
            "kv_store is missing required columns: " + ", ".join(sorted(missing))
        )


def _validate_key_column(columns: dict[str, _ColumnInfo]) -> None:
    key = columns["key"]
    primary_keys = [name for name, column in columns.items() if column.primary_key]
    if key.declared_type != "TEXT" or key.primary_key != 1 or primary_keys != ["key"]:
        raise InvalidDataStoreSchemaError("kv_store.key must be the sole TEXT primary key")


def _validate_value_column(columns: dict[str, _ColumnInfo]) -> None:
    value = columns["value"]
    if value.declared_type != "TEXT" or not value.not_null:
        raise InvalidDataStoreSchemaError("kv_store.value must be TEXT NOT NULL")


def _validate_updated_at_column(columns: dict[str, _ColumnInfo]) -> None:
    updated_at = columns["updated_at"]
    default = str(updated_at.default or "").strip("() ").upper()
    if updated_at.declared_type != "TIMESTAMP" or default != "CURRENT_TIMESTAMP":
        raise InvalidDataStoreSchemaError(
            "kv_store.updated_at must be TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )


def _validate_extra_columns(columns: dict[str, _ColumnInfo]) -> None:
    unsafe_extra = sorted(
        name
        for name, column in columns.items()
        if name not in {"key", "value", "updated_at"}
        and column.not_null
        and column.default is None
        and not column.primary_key
    )
    if unsafe_extra:
        raise InvalidDataStoreSchemaError(
            "kv_store has required extra columns without defaults: " + ", ".join(unsafe_extra)
        )


def validate_kv_store_schema(connection: sqlite3.Connection) -> None:
    """Reject a pre-existing table that cannot safely serve the DataStore API."""
    table = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = 'kv_store'"
    ).fetchone()
    if table is None or str(table[0]).lower() != "table":
        raise InvalidDataStoreSchemaError("kv_store must be a SQLite table")
    columns = _read_kv_store_columns(connection)
    _validate_required_columns(columns)
    _validate_key_column(columns)
    _validate_value_column(columns)
    _validate_updated_at_column(columns)
    _validate_extra_columns(columns)


def upgrade(connection: sqlite3.Connection) -> None:
    """Create the legacy-compatible key/value table without touching existing data."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    validate_kv_store_schema(connection)


def downgrade(connection: sqlite3.Connection) -> None:
    """Return to the pre-DataStore schema."""
    connection.execute("DROP TABLE IF EXISTS kv_store")


MIGRATION = Migration(
    version=1,
    name="001_init",
    upgrade=upgrade,
    downgrade=downgrade,
)
