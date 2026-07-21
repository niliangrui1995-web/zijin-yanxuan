"""Mark validated DataStore databases with a persistent application id."""

from __future__ import annotations

import sqlite3

from infra.storage.migrations.identity import (
    DATASTORE_APPLICATION_ID,
    read_application_id,
    set_application_id,
)
from infra.storage.migrations.models import Migration
from infra.storage.migrations.v001_init import InvalidDataStoreSchemaError, validate_kv_store_schema


def upgrade(connection: sqlite3.Connection) -> None:
    """Validate the legacy schema before claiming ownership of the database."""
    validate_kv_store_schema(connection)
    application_id = read_application_id(connection)
    if application_id not in (0, DATASTORE_APPLICATION_ID):
        raise InvalidDataStoreSchemaError(
            f"database belongs to application_id {application_id}, expected 0 or {DATASTORE_APPLICATION_ID}"
        )
    set_application_id(connection, DATASTORE_APPLICATION_ID)


def downgrade(connection: sqlite3.Connection) -> None:
    """Remove the DataStore identity when returning to schema version 1."""
    application_id = read_application_id(connection)
    if application_id not in (0, DATASTORE_APPLICATION_ID):
        raise InvalidDataStoreSchemaError(
            f"database belongs to application_id {application_id}, expected {DATASTORE_APPLICATION_ID}"
        )
    set_application_id(connection, 0)


MIGRATION = Migration(
    version=2,
    name="002_datastore_identity",
    upgrade=upgrade,
    downgrade=downgrade,
)
