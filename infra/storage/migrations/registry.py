"""Ordered registry of DataStore schema migrations."""

from infra.storage.migrations.models import Migration
from infra.storage.migrations.v001_init import MIGRATION as V001_INIT
from infra.storage.migrations.v002_datastore_identity import MIGRATION as V002_DATASTORE_IDENTITY

MIGRATIONS: tuple[Migration, ...] = (V001_INIT, V002_DATASTORE_IDENTITY)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version
