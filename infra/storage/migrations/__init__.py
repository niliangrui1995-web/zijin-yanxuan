"""Public DataStore schema migration API."""

from infra.storage.migrations.backup import (
    BACKUP_MANIFEST_SUFFIX,
    BACKUP_ROOT_ENV,
    BackupRetentionPolicy,
    InvalidMigrationBackupError,
    MigrationBackupError,
    MigrationRestoreError,
    MigrationResult,
    create_migration_backup,
    list_migration_backups,
    migrate_database_with_backup,
    prune_migration_backups,
    resolve_migration_backup_directory,
    restore_migration_backup,
    verify_migration_backup,
)
from infra.storage.migrations.identity import DATASTORE_APPLICATION_ID, read_application_id
from infra.storage.migrations.models import Migration
from infra.storage.migrations.registry import LATEST_SCHEMA_VERSION, MIGRATIONS
from infra.storage.migrations.runner import (
    InvalidMigrationChainError,
    InvalidTargetSchemaVersionError,
    MigrationError,
    MigrationPlan,
    UnsupportedSchemaVersionError,
    plan_migration,
    read_schema_version,
    run_migrations,
)
from infra.storage.migrations.v001_init import InvalidDataStoreSchemaError

__all__ = [
    "BACKUP_ROOT_ENV",
    "BACKUP_MANIFEST_SUFFIX",
    "DATASTORE_APPLICATION_ID",
    "LATEST_SCHEMA_VERSION",
    "MIGRATIONS",
    "BackupRetentionPolicy",
    "InvalidMigrationChainError",
    "InvalidMigrationBackupError",
    "InvalidDataStoreSchemaError",
    "InvalidTargetSchemaVersionError",
    "Migration",
    "MigrationBackupError",
    "MigrationError",
    "MigrationPlan",
    "MigrationRestoreError",
    "MigrationResult",
    "UnsupportedSchemaVersionError",
    "create_migration_backup",
    "list_migration_backups",
    "migrate_database_with_backup",
    "plan_migration",
    "prune_migration_backups",
    "read_schema_version",
    "read_application_id",
    "resolve_migration_backup_directory",
    "restore_migration_backup",
    "run_migrations",
    "verify_migration_backup",
]
