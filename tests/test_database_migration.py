# -*- coding: utf-8 -*-
"""DataStore schema migration, backup, downgrade, and recovery tests."""

from __future__ import annotations

import json
import os
import sqlite3
import weakref
from pathlib import Path

import pytest

from infra.storage.data_store import DataStore
from infra.storage.migrations import (
    BACKUP_MANIFEST_SUFFIX,
    BACKUP_ROOT_ENV,
    DATASTORE_APPLICATION_ID,
    LATEST_SCHEMA_VERSION,
    BackupRetentionPolicy,
    InvalidDataStoreSchemaError,
    InvalidMigrationBackupError,
    InvalidMigrationChainError,
    Migration,
    UnsupportedSchemaVersionError,
    create_migration_backup,
    list_migration_backups,
    migrate_database_with_backup,
    prune_migration_backups,
    read_application_id,
    read_schema_version,
    resolve_migration_backup_directory,
    restore_migration_backup,
    run_migrations,
    verify_migration_backup,
)


def _open_isolated_store(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> DataStore:
    monkeypatch.setenv(BACKUP_ROOT_ENV, str(db_path.parent / "migration-backups"))
    monkeypatch.setattr(DataStore, "_instance", None)
    monkeypatch.setattr(DataStore, "_instances", weakref.WeakSet())
    return DataStore(db_path=str(db_path))


def _no_op(_connection: sqlite3.Connection) -> None:
    return None


def test_empty_database_is_automatically_upgraded_and_backed_up(monkeypatch, tmp_path):
    db_path = tmp_path / "empty.db"
    store = _open_isolated_store(monkeypatch, db_path)
    try:
        assert store.schema_version == LATEST_SCHEMA_VERSION
        assert store.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'kv_store'"
        ) == {"name": "kv_store"}
        assert store._last_migration_backup is not None
        assert verify_migration_backup(store._last_migration_backup) == 0
        assert read_application_id(store._require_open_connection()) == DATASTORE_APPLICATION_ID
    finally:
        store.close()


def test_up_to_date_startup_does_not_create_another_backup(monkeypatch, tmp_path):
    db_path = tmp_path / "current.db"
    first = _open_isolated_store(monkeypatch, db_path)
    first_backup = first._last_migration_backup
    first.close()

    second = _open_isolated_store(monkeypatch, db_path)
    try:
        assert first_backup is not None
        assert second._last_migration_backup is None
        assert list_migration_backups(db_path) == (first_backup,)
    finally:
        second.close()


def test_restore_of_empty_version_zero_backup_is_auto_migrated_and_usable(monkeypatch, tmp_path):
    db_path = tmp_path / "empty-restore.db"
    backup_root = tmp_path / "migration-backups"
    connection = sqlite3.connect(db_path)
    connection.commit()
    try:
        empty_backup = create_migration_backup(
            connection,
            db_path,
            source_version=0,
            target_version=LATEST_SCHEMA_VERSION,
            backup_root=backup_root,
        )
    finally:
        connection.close()

    store = _open_isolated_store(monkeypatch, db_path)
    try:
        store.save_json("temporary", {"value": "discarded"})
        assert store.restore_backup(empty_backup) == LATEST_SCHEMA_VERSION
        assert store.schema_version == LATEST_SCHEMA_VERSION
        assert store.load_json("temporary") is None
        store.save_json("after_restore", {"usable": True})
        assert store.load_json("after_restore") == {"usable": True}
    finally:
        store.close()


def test_version_zero_database_keeps_legacy_data_and_backup_can_restore(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO kv_store (key, value) VALUES (?, ?)",
        ("existing", json.dumps({"value": "保留"}, ensure_ascii=False)),
    )
    connection.execute("INSERT INTO legacy_records (label) VALUES ('untouched')")
    connection.commit()
    connection.close()

    store = _open_isolated_store(monkeypatch, db_path)
    try:
        backup_path = store._last_migration_backup
        assert store.load_json("existing") == {"value": "保留"}
        assert store.fetch_one("SELECT label FROM legacy_records") == {"label": "untouched"}
        store.execute("DELETE FROM legacy_records")
        assert backup_path is not None
        backups_before_restore = set(list_migration_backups(db_path))
        assert store.restore_backup(backup_path) == LATEST_SCHEMA_VERSION
        assert store.schema_version == LATEST_SCHEMA_VERSION
        assert len(set(list_migration_backups(db_path)) - backups_before_restore) == 1
        assert store.load_json("existing") == {"value": "保留"}
        assert store.fetch_one("SELECT label FROM legacy_records") == {"label": "untouched"}
    finally:
        store.close()


def test_migration_chain_upgrades_downgrades_and_is_idempotent():
    connection = sqlite3.connect(":memory:")
    calls: list[str] = []

    def upgrade_v1(conn: sqlite3.Connection) -> None:
        calls.append("up-1")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (label) VALUES ('original')")

    def downgrade_v1(conn: sqlite3.Connection) -> None:
        calls.append("down-1")
        conn.execute("DROP TABLE sample")

    def upgrade_v2(conn: sqlite3.Connection) -> None:
        calls.append("up-2")
        conn.execute("CREATE TABLE sample_flags (sample_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL)")

    def downgrade_v2(conn: sqlite3.Connection) -> None:
        calls.append("down-2")
        conn.execute("DROP TABLE sample_flags")

    chain = (
        Migration(1, "001_init", upgrade_v1, downgrade_v1),
        Migration(2, "002_add_flags", upgrade_v2, downgrade_v2),
    )
    try:
        assert run_migrations(connection, chain) == 2
        assert run_migrations(connection, chain) == 2
        assert run_migrations(connection, chain, target_version=1) == 1
        assert run_migrations(connection, chain, target_version=1) == 1
        assert calls == ["up-1", "up-2", "down-2"]
        assert connection.execute("SELECT label FROM sample").fetchone() == ("original",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sample_flags'"
        ).fetchone() is None
    finally:
        connection.close()


def test_datastore_registry_downgrades_and_upgrades_again_cleanly():
    connection = sqlite3.connect(":memory:")
    try:
        assert run_migrations(connection) == LATEST_SCHEMA_VERSION
        assert read_application_id(connection) == DATASTORE_APPLICATION_ID

        assert run_migrations(connection, target_version=1) == 1
        assert read_application_id(connection) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'"
        ).fetchone() == ("kv_store",)

        assert run_migrations(connection, target_version=0) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'"
        ).fetchone() is None

        assert run_migrations(connection) == LATEST_SCHEMA_VERSION
        assert read_application_id(connection) == DATASTORE_APPLICATION_ID
    finally:
        connection.close()


def test_failed_upgrade_rolls_back_and_backup_restores_old_data(tmp_path):
    db_path = tmp_path / "failed-upgrade.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE sample (label TEXT NOT NULL)")
    connection.execute("INSERT INTO sample VALUES ('preserved')")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()

    def failing_upgrade(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM sample")
        conn.execute("CREATE TABLE transient_table (value TEXT)")
        raise RuntimeError("migration failed")

    chain = (
        Migration(1, "001_init", _no_op, _no_op),
        Migration(2, "002_failure", failing_upgrade, _no_op),
    )
    backup_root = tmp_path / "backups"
    try:
        with pytest.raises(RuntimeError, match="migration failed"):
            migrate_database_with_backup(connection, db_path, chain, backup_root=backup_root)

        backups = list_migration_backups(db_path, backup_root=backup_root)
        assert len(backups) == 1
        assert verify_migration_backup(backups[0]) == 1
        assert read_schema_version(connection) == 1
        assert connection.execute("SELECT label FROM sample").fetchone() == ("preserved",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transient_table'"
        ).fetchone() is None

        connection.execute("DELETE FROM sample")
        connection.commit()
        assert restore_migration_backup(connection, backups[0]) == 1
        assert connection.execute("SELECT label FROM sample").fetchone() == ("preserved",)
    finally:
        connection.close()


def test_failed_downgrade_rolls_back_all_steps_and_keeps_backup(tmp_path):
    db_path = tmp_path / "failed-downgrade.db"
    backup_root = tmp_path / "backups"
    connection = sqlite3.connect(db_path)

    def upgrade_v1(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE sample (label TEXT NOT NULL)")
        conn.execute("INSERT INTO sample VALUES ('preserved')")

    def downgrade_v1(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE sample")

    def upgrade_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE v2_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO v2_marker VALUES ('present')")

    def failing_downgrade(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM sample")
        conn.execute("DROP TABLE v2_marker")
        raise RuntimeError("downgrade failed")

    def upgrade_v3(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE v3_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO v3_marker VALUES ('present')")

    def downgrade_v3(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE v3_marker")

    chain = (
        Migration(1, "001_init", upgrade_v1, downgrade_v1),
        Migration(2, "002_marker", upgrade_v2, failing_downgrade),
        Migration(3, "003_marker", upgrade_v3, downgrade_v3),
    )
    try:
        assert run_migrations(connection, chain) == 3
        with pytest.raises(RuntimeError, match="downgrade failed"):
            migrate_database_with_backup(
                connection,
                db_path,
                chain,
                target_version=0,
                backup_root=backup_root,
            )
        backups = list_migration_backups(db_path, backup_root=backup_root)
        assert len(backups) == 1
        assert verify_migration_backup(backups[0]) == 3
        assert read_schema_version(connection) == 3
        assert connection.execute("SELECT label FROM sample").fetchone() == ("preserved",)
        assert connection.execute("SELECT value FROM v2_marker").fetchone() == ("present",)
        assert connection.execute("SELECT value FROM v3_marker").fetchone() == ("present",)
    finally:
        connection.close()


def test_chain_gap_is_rejected_before_any_schema_mutation():
    connection = sqlite3.connect(":memory:")
    chain = (
        Migration(1, "001_init", _no_op, _no_op),
        Migration(3, "003_gap", _no_op, _no_op),
    )
    try:
        with pytest.raises(InvalidMigrationChainError, match="contiguous"):
            run_migrations(connection, chain)
        assert read_schema_version(connection) == 0
    finally:
        connection.close()


def test_malformed_existing_kv_store_is_not_stamped_as_version_one(monkeypatch, tmp_path):
    db_path = tmp_path / "malformed.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE kv_store (key TEXT, value INTEGER)")
    connection.commit()
    connection.close()

    with pytest.raises(InvalidDataStoreSchemaError, match="missing required columns"):
        _open_isolated_store(monkeypatch, db_path)

    verification = sqlite3.connect(db_path)
    try:
        assert read_schema_version(verification) == 0
        assert verification.execute("PRAGMA table_info(kv_store)").fetchall() == [
            (0, "key", "TEXT", 0, None, 0),
            (1, "value", "INTEGER", 0, None, 0),
        ]
    finally:
        verification.close()


def test_migration_callback_cannot_commit_outside_runner_transaction():
    connection = sqlite3.connect(":memory:")

    def committing_upgrade(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE escaped_commit (value TEXT)")
        conn.commit()

    chain = (Migration(1, "001_committing", committing_upgrade, _no_op),)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            run_migrations(connection, chain)
        assert read_schema_version(connection) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='escaped_commit'"
        ).fetchone() is None
    finally:
        connection.close()


def test_future_schema_version_fails_fast_without_mutation_or_backup(monkeypatch, tmp_path):
    db_path = tmp_path / "future.db"
    connection = sqlite3.connect(db_path)
    future_version = LATEST_SCHEMA_VERSION + 1
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("CREATE TABLE future_marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO future_marker VALUES ('preserved')")
    connection.execute(f"PRAGMA user_version = {future_version:d}")
    connection.commit()
    connection.close()

    with pytest.raises(UnsupportedSchemaVersionError, match="newer than supported"):
        _open_isolated_store(monkeypatch, db_path)

    verification = sqlite3.connect(db_path)
    try:
        assert read_schema_version(verification) == future_version
        assert verification.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert verification.execute("SELECT value FROM future_marker").fetchone() == ("preserved",)
        assert list_migration_backups(db_path) == ()
    finally:
        verification.close()


def test_unmanifested_backup_is_rejected_before_destination_changes(tmp_path):
    db_path = tmp_path / "target.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserved')")
    connection.commit()
    corrupt = tmp_path / "unmanifested.sqlite3.bak"
    corrupt.write_bytes(b"not a sqlite database")
    try:
        with pytest.raises(InvalidMigrationBackupError, match="manifest does not exist"):
            restore_migration_backup(connection, corrupt)
        assert connection.execute("SELECT value FROM marker").fetchone() == ("preserved",)
    finally:
        connection.close()


def test_backup_for_another_database_is_rejected_before_destination_changes(tmp_path):
    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    backup_root = tmp_path / "backups"
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    source.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    source.execute("INSERT INTO marker VALUES ('source')")
    source.execute("PRAGMA user_version = 1")
    source.commit()
    target.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    target.execute("INSERT INTO marker VALUES ('target')")
    target.execute("PRAGMA user_version = 1")
    target.commit()
    try:
        backup = create_migration_backup(
            source,
            source_path,
            source_version=1,
            target_version=0,
            backup_root=backup_root,
        )
        with pytest.raises(InvalidMigrationBackupError, match="different database"):
            restore_migration_backup(target, backup, backup_root=backup_root)
        assert target.execute("SELECT value FROM marker").fetchone() == ("target",)
        assert list_migration_backups(target_path, backup_root=backup_root) == ()
    finally:
        source.close()
        target.close()


def test_tampered_backup_content_is_rejected_by_manifest_checksum(tmp_path):
    db_path = tmp_path / "content-tamper.db"
    backup_root = tmp_path / "backups"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserved')")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    try:
        backup = create_migration_backup(
            connection,
            db_path,
            source_version=1,
            target_version=0,
            backup_root=backup_root,
        )
        content = bytearray(backup.read_bytes())
        content[-1] ^= 0x01
        backup.write_bytes(content)
        with pytest.raises(InvalidMigrationBackupError, match="checksum does not match"):
            restore_migration_backup(connection, backup, backup_root=backup_root)
        assert connection.execute("SELECT value FROM marker").fetchone() == ("preserved",)
    finally:
        connection.close()


def test_tampered_backup_manifest_is_rejected(tmp_path):
    db_path = tmp_path / "manifest-tamper.db"
    backup_root = tmp_path / "backups"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    try:
        backup = create_migration_backup(
            connection,
            db_path,
            source_version=1,
            target_version=0,
            backup_root=backup_root,
        )
        manifest_path = backup.with_name(f"{backup.name}{BACKUP_MANIFEST_SUFFIX}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["database_identity"] = "0" * 16
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(InvalidMigrationBackupError, match="different database"):
            restore_migration_backup(connection, backup, backup_root=backup_root)
    finally:
        connection.close()


def test_restore_preserves_current_destination_as_a_safety_backup(tmp_path):
    db_path = tmp_path / "restore-safety.db"
    backup_root = tmp_path / "backups"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('before')")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    try:
        recovery_point = create_migration_backup(
            connection,
            db_path,
            source_version=1,
            target_version=0,
            backup_root=backup_root,
        )
        connection.execute("UPDATE marker SET value = 'current'")
        connection.commit()
        before = set(list_migration_backups(db_path, backup_root=backup_root))
        assert restore_migration_backup(
            connection,
            recovery_point,
            backup_root=backup_root,
        ) == 1
        added = set(list_migration_backups(db_path, backup_root=backup_root)) - before
        assert len(added) == 1
        safety_backup = added.pop()
        safety = sqlite3.connect(f"{safety_backup.as_uri()}?mode=ro", uri=True)
        try:
            assert safety.execute("SELECT value FROM marker").fetchone() == ("current",)
        finally:
            safety.close()
        assert connection.execute("SELECT value FROM marker").fetchone() == ("before",)
    finally:
        connection.close()


def test_retention_never_deletes_current_recovery_point(tmp_path):
    db_path = tmp_path / "retention.db"
    backup_root = tmp_path / "backups"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    try:
        backup = create_migration_backup(
            connection,
            db_path,
            source_version=1,
            target_version=0,
            backup_root=backup_root,
        )
        os.utime(backup, (1, 1))
        removed = prune_migration_backups(
            db_path,
            policy=BackupRetentionPolicy(max_backups=1, max_age_days=0),
            backup_root=backup_root,
            protected=backup,
        )
        assert removed == ()
        assert backup.is_file()
        assert verify_migration_backup(backup) == 1
        assert backup.parent == resolve_migration_backup_directory(db_path, backup_root=backup_root)
    finally:
        connection.close()


def test_repository_database_backups_default_outside_git_checkout(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = project_root / "data" / "production-like.db"
    backup_dir = resolve_migration_backup_directory(
        database_path,
        environ={
            "LOCALAPPDATA": str(tmp_path),
            "XDG_STATE_HOME": str(tmp_path),
        },
    )

    assert backup_dir.is_relative_to(tmp_path.resolve())
    assert not backup_dir.is_relative_to(project_root)
