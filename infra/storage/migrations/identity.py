"""Persistent identity assigned to databases managed by DataStore."""

from __future__ import annotations

import sqlite3

DATASTORE_APPLICATION_ID = int.from_bytes(b"VCPH", byteorder="big")


def read_application_id(connection: sqlite3.Connection) -> int:
    """Return SQLite's application identifier from the database header."""
    row = connection.execute("PRAGMA application_id").fetchone()
    return int(row[0]) if row is not None else 0


def set_application_id(connection: sqlite3.Connection, application_id: int) -> None:
    """Persist a validated signed 32-bit SQLite application identifier."""
    if type(application_id) is not int or not 0 <= application_id <= 0x7FFF_FFFF:
        raise ValueError("application_id must be an integer between 0 and 2147483647")
    connection.execute(f"PRAGMA application_id = {application_id:d}")
