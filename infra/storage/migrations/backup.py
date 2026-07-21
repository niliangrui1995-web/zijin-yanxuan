"""Consistent SQLite migration backups, retention, and restore support."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infra.storage.migrations.identity import read_application_id
from infra.storage.migrations.models import Migration
from infra.storage.migrations.registry import MIGRATIONS
from infra.storage.migrations.runner import (
    MigrationError,
    MigrationPlan,
    UnsupportedSchemaVersionError,
    read_schema_version,
    run_migrations,
)

log = logging.getLogger(__name__)

BACKUP_ROOT_ENV = "VCP_HUNTER_DB_BACKUP_DIR"
BACKUP_SUFFIX = ".sqlite3.bak"
BACKUP_MANIFEST_SUFFIX = ".manifest.json"
BACKUP_MANIFEST_FORMAT = "vcp-hunter-sqlite-migration-backup"
BACKUP_MANIFEST_VERSION = 1


class MigrationBackupError(MigrationError):
    """Raised when a consistent pre-migration backup cannot be created."""


class InvalidMigrationBackupError(MigrationBackupError):
    """Raised when a backup is missing, corrupt, or incompatible."""


class MigrationRestoreError(MigrationBackupError):
    """Raised when SQLite cannot atomically restore a validated backup."""


@dataclass(frozen=True, slots=True)
class BackupRetentionPolicy:
    """Bound retained backups by both count and age."""

    max_backups: int = 10
    max_age_days: int = 30


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Result of a version transition and its pre-change recovery point."""

    version: int
    backup_path: Path | None


def _database_identity(database_path: Path) -> str:
    normalized = os.path.normcase(str(database_path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _safe_database_stem(database_path: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", database_path.stem).strip("-._")
    return safe[:48] or "database"


def _inside_git_checkout(database_path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (database_path.parent, *database_path.parents))


def _external_backup_root(environ: Mapping[str, str]) -> Path:
    configured = str(environ.get(BACKUP_ROOT_ENV, "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return (base / "VCPHunter" / "migration_backups").resolve()


def resolve_migration_backup_directory(
    database_path: str | Path,
    *,
    backup_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve a per-database directory, outside a Git checkout by default."""
    db_path = Path(database_path).expanduser().resolve()
    env = os.environ if environ is None else environ
    if backup_root is not None:
        root = Path(backup_root).expanduser().resolve()
    elif str(env.get(BACKUP_ROOT_ENV, "") or "").strip() or _inside_git_checkout(db_path):
        root = _external_backup_root(env)
    else:
        root = (db_path.parent / ".vcp_hunter_backups").resolve()
    return root / _database_identity(db_path)


def _connection_database_path(connection: sqlite3.Connection) -> Path | None:
    for _, name, filename in connection.execute("PRAGMA database_list").fetchall():
        if str(name) == "main" and str(filename):
            return Path(str(filename)).resolve()
    return None


def _assert_matching_source(connection: sqlite3.Connection, database_path: Path) -> None:
    actual = _connection_database_path(connection)
    if actual is None or os.path.normcase(str(actual)) != os.path.normcase(str(database_path)):
        raise MigrationBackupError(
            f"backup source does not match database path: expected {database_path}, got {actual}"
        )


def _assert_integrity(connection: sqlite3.Connection, *, label: str) -> None:
    try:
        messages = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall())
    except sqlite3.Error as exc:
        raise InvalidMigrationBackupError(f"{label} integrity check failed: {exc}") from exc
    if messages != ("ok",):
        detail = "; ".join(messages[:3]) or "no result"
        raise InvalidMigrationBackupError(f"{label} failed PRAGMA integrity_check: {detail}")


def _backup_filename(database_path: Path, source_version: int, target_version: int) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    nonce = uuid.uuid4().hex[:12]
    return (
        f"{_safe_database_stem(database_path)}.schema-v{source_version:04d}-to-v{target_version:04d}."
        f"{timestamp}.{nonce}{BACKUP_SUFFIX}"
    )


def _reserve_file(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}{BACKUP_MANIFEST_SUFFIX}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_backup_manifest(
    backup_path: Path,
    database_path: Path,
    *,
    source_version: int,
    target_version: int,
    application_id: int,
) -> None:
    manifest_path = _manifest_path(backup_path)
    partial_path = manifest_path.with_name(f"{manifest_path.name}.partial")
    payload = {
        "format": BACKUP_MANIFEST_FORMAT,
        "manifest_version": BACKUP_MANIFEST_VERSION,
        "database_identity": _database_identity(database_path),
        "database_name": database_path.name,
        "source_version": source_version,
        "target_version": target_version,
        "application_id": application_id,
        "sha256": _sha256_file(backup_path),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _reserve_file(partial_path)
    completed = False
    try:
        with partial_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial_path, manifest_path)
        completed = True
    finally:
        if not completed:
            partial_path.unlink(missing_ok=True)


def _read_backup_manifest(path: Path) -> dict[str, Any]:
    manifest_path = _manifest_path(path)
    if not manifest_path.is_file():
        raise InvalidMigrationBackupError(f"migration backup manifest does not exist: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidMigrationBackupError(f"migration backup manifest is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidMigrationBackupError("migration backup manifest must be a JSON object")
    return payload


def _manifest_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise InvalidMigrationBackupError(f"migration backup manifest has invalid {key}")
    return value


def _validate_manifest_header(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != BACKUP_MANIFEST_FORMAT:
        raise InvalidMigrationBackupError("migration backup manifest format is not supported")
    if payload.get("manifest_version") != BACKUP_MANIFEST_VERSION:
        raise InvalidMigrationBackupError("migration backup manifest version is not supported")


def _validate_manifest_identity(
    payload: Mapping[str, Any],
    expected_database_path: Path | None,
) -> None:
    database_identity = payload.get("database_identity")
    if not isinstance(database_identity, str) or not re.fullmatch(r"[0-9a-f]{16}", database_identity):
        raise InvalidMigrationBackupError("migration backup manifest has invalid database identity")
    if expected_database_path is not None and database_identity != _database_identity(expected_database_path):
        raise InvalidMigrationBackupError("migration backup belongs to a different database")


def _validate_manifest_checksum(path: Path, payload: Mapping[str, Any]) -> None:
    expected_checksum = payload.get("sha256")
    if not isinstance(expected_checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_checksum):
        raise InvalidMigrationBackupError("migration backup manifest has invalid SHA-256 checksum")
    try:
        actual_checksum = _sha256_file(path)
    except OSError as exc:
        raise InvalidMigrationBackupError(f"migration backup cannot be read: {exc}") from exc
    if not hmac.compare_digest(actual_checksum, expected_checksum):
        raise InvalidMigrationBackupError("migration backup SHA-256 checksum does not match its manifest")


def _validate_backup_manifest(
    path: Path,
    *,
    expected_database_path: Path | None,
) -> tuple[int, int]:
    payload = _read_backup_manifest(path)
    _validate_manifest_header(payload)
    source_version = _manifest_int(payload, "source_version")
    _manifest_int(payload, "target_version")
    application_id = _manifest_int(payload, "application_id")
    _validate_manifest_identity(payload, expected_database_path)
    _validate_manifest_checksum(path, payload)
    return source_version, application_id


def create_migration_backup(
    source: sqlite3.Connection,
    database_path: str | Path,
    *,
    source_version: int,
    target_version: int,
    backup_root: str | Path | None = None,
) -> Path:
    """Create and validate an online SQLite snapshot before a schema change."""
    if source.in_transaction:
        raise MigrationBackupError("backup source must not have an active transaction")
    db_path = Path(database_path).expanduser().resolve()
    _assert_matching_source(source, db_path)
    directory = resolve_migration_backup_directory(db_path, backup_root=backup_root)
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / _backup_filename(db_path, source_version, target_version)
    partial_path = final_path.with_name(f"{final_path.name}.partial")
    _reserve_file(partial_path)
    completed = False
    application_id = 0
    try:
        destination = sqlite3.connect(partial_path)
        try:
            source.backup(destination, pages=256, sleep=0.05)
            _assert_integrity(destination, label="migration backup")
            if read_schema_version(destination) != source_version:
                raise InvalidMigrationBackupError("migration backup schema version changed during capture")
            application_id = read_application_id(destination)
        finally:
            destination.close()
        _fsync_file(partial_path)
        os.replace(partial_path, final_path)
        _write_backup_manifest(
            final_path,
            db_path,
            source_version=source_version,
            target_version=target_version,
            application_id=application_id,
        )
        completed = True
    finally:
        if not completed:
            partial_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            _manifest_path(final_path).unlink(missing_ok=True)
    return final_path


def _backup_prefix(database_path: Path) -> str:
    return f"{_safe_database_stem(database_path)}.schema-v"


def list_migration_backups(
    database_path: str | Path,
    *,
    backup_root: str | Path | None = None,
) -> tuple[Path, ...]:
    """List only backups belonging to ``database_path``, newest first."""
    db_path = Path(database_path).expanduser().resolve()
    directory = resolve_migration_backup_directory(db_path, backup_root=backup_root)
    if not directory.is_dir():
        return ()
    prefix = _backup_prefix(db_path)
    candidates = (
        path for path in directory.iterdir() if path.is_file() and path.name.startswith(prefix) and path.name.endswith(BACKUP_SUFFIX)
    )
    return tuple(sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True))


def _validate_retention_policy(policy: BackupRetentionPolicy) -> None:
    if type(policy.max_backups) is not int or policy.max_backups < 1:
        raise ValueError("max_backups must be a positive integer")
    if type(policy.max_age_days) is not int or policy.max_age_days < 0:
        raise ValueError("max_age_days must be a non-negative integer")


def prune_migration_backups(
    database_path: str | Path,
    *,
    policy: BackupRetentionPolicy = BackupRetentionPolicy(),
    backup_root: str | Path | None = None,
    protected: Path | None = None,
) -> tuple[Path, ...]:
    """Apply bounded retention without ever deleting the current recovery point."""
    _validate_retention_policy(policy)
    backups = list_migration_backups(database_path, backup_root=backup_root)
    protected_path = protected.resolve() if protected is not None else None
    keep = {path.resolve() for path in backups[: policy.max_backups]}
    if protected_path is not None:
        keep.add(protected_path)
    cutoff = time.time() - (policy.max_age_days * 86400)
    removed: list[Path] = []
    for path in backups:
        if path.resolve() in keep and path.stat().st_mtime >= cutoff:
            continue
        if protected_path is not None and path.resolve() == protected_path:
            continue
        path.unlink(missing_ok=True)
        _manifest_path(path).unlink(missing_ok=True)
        removed.append(path)
    return tuple(removed)


def _open_readonly(database_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)


@dataclass(slots=True)
class _BackupHook:
    database_path: Path
    backup_root: str | Path | None
    backup_path: Path | None = None

    def __call__(self, plan: MigrationPlan) -> None:
        source = _open_readonly(self.database_path)
        try:
            self.backup_path = create_migration_backup(
                source,
                self.database_path,
                source_version=plan.current_version,
                target_version=plan.target_version,
                backup_root=self.backup_root,
            )
        finally:
            source.close()


def migrate_database_with_backup(
    connection: sqlite3.Connection,
    database_path: str | Path,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    target_version: int | None = None,
    backup_root: str | Path | None = None,
    retention: BackupRetentionPolicy = BackupRetentionPolicy(),
) -> MigrationResult:
    """Run a version transition with one validated backup under the writer lock."""
    db_path = Path(database_path).expanduser().resolve()
    _assert_matching_source(connection, db_path)
    hook = _BackupHook(db_path, backup_root)
    version = run_migrations(
        connection,
        migrations,
        target_version=target_version,
        before_change=hook,
    )
    if hook.backup_path is not None:
        try:
            prune_migration_backups(
                db_path,
                policy=retention,
                backup_root=backup_root,
                protected=hook.backup_path,
            )
        except OSError as exc:
            log.warning("migration backup retention cleanup failed: %s", exc)
    return MigrationResult(version, hook.backup_path)


def verify_migration_backup(
    backup_path: str | Path,
    *,
    expected_database_path: str | Path | None = None,
) -> int:
    """Validate a backup with SQLite and return its embedded schema version."""
    path = Path(backup_path).expanduser().resolve()
    if not path.is_file() or not path.name.endswith(BACKUP_SUFFIX):
        raise InvalidMigrationBackupError(f"migration backup does not exist: {path}")
    expected_path = (
        Path(expected_database_path).expanduser().resolve()
        if expected_database_path is not None
        else None
    )
    manifest_version, manifest_application_id = _validate_backup_manifest(
        path,
        expected_database_path=expected_path,
    )
    source = _open_readonly(path)
    try:
        _assert_integrity(source, label="migration backup")
        version = read_schema_version(source)
        if version != manifest_version:
            raise InvalidMigrationBackupError(
                "migration backup schema version does not match its manifest"
            )
        if read_application_id(source) != manifest_application_id:
            raise InvalidMigrationBackupError(
                "migration backup application id does not match its manifest"
            )
        return version
    finally:
        source.close()


def restore_migration_backup(
    target: sqlite3.Connection,
    backup_path: str | Path,
    *,
    max_supported_version: int | None = None,
    backup_root: str | Path | None = None,
) -> int:
    """Restore a validated backup after preserving the current destination."""
    if target.in_transaction:
        raise MigrationRestoreError("backup restore requires an idle SQLite connection")
    path = Path(backup_path).expanduser().resolve()
    target_path = _connection_database_path(target)
    if target_path is None:
        raise MigrationRestoreError("backup restore requires a file-backed destination database")
    if os.path.normcase(str(target_path)) == os.path.normcase(str(path)):
        raise MigrationRestoreError("backup source and restore destination must be different databases")
    version = verify_migration_backup(path, expected_database_path=target_path)
    if max_supported_version is not None and version > max_supported_version:
        raise UnsupportedSchemaVersionError(
            f"backup schema version {version} is newer than supported version {max_supported_version}"
        )
    safety_backup = create_migration_backup(
        target,
        target_path,
        source_version=read_schema_version(target),
        target_version=version,
        backup_root=backup_root,
    )
    log.info("created pre-restore database safety backup: %s", safety_backup)
    source = _open_readonly(path)
    try:
        source.backup(target, pages=256, sleep=0.05)
        _assert_integrity(target, label="restored database")
        if read_schema_version(target) != version:
            raise MigrationRestoreError("restored database schema version changed during restore")
    except sqlite3.Error as exc:
        raise MigrationRestoreError(f"SQLite backup restore failed: {exc}") from exc
    finally:
        source.close()
    return version
