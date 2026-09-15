"""Versioned project identity and Phase 2 scientific schema migrations."""
from __future__ import annotations

import hashlib
import sqlite3

from ..core.project_models import PROJECT_SCHEMA_VERSION, ProjectError


SCHEMA_V1 = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        checksum TEXT NOT NULL
    )""",
    """CREATE TABLE project (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        id TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version > 0)
    )""",
)
SCHEMA_V1_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V1).encode()).hexdigest()


SCHEMA_V2 = (
    """CREATE TABLE structures (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, name TEXT NOT NULL,
        parent_structure_id TEXT REFERENCES structures(id), operation TEXT NOT NULL,
        operation_json TEXT NOT NULL, source_json TEXT NOT NULL,
        snapshot_relpath TEXT NOT NULL UNIQUE, snapshot_sha256 TEXT NOT NULL,
        structure_hash TEXT NOT NULL, hash_version INTEGER NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE pseudo_sets (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, settings_json TEXT NOT NULL,
        manifest_relpath TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL,
        pseudo_set_hash TEXT NOT NULL UNIQUE, entry_count INTEGER NOT NULL CHECK(entry_count > 0), created_at TEXT NOT NULL
    )""",
    """CREATE TABLE pseudo_entries (
        pseudo_set_id TEXT NOT NULL REFERENCES pseudo_sets(id), species TEXT NOT NULL,
        filename TEXT NOT NULL, relpath TEXT NOT NULL, sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL, PRIMARY KEY(pseudo_set_id, species)
    )""",
    """CREATE TABLE baselines (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE,
        structure_id TEXT NOT NULL REFERENCES structures(id),
        pseudo_set_id TEXT NOT NULL REFERENCES pseudo_sets(id),
        previous_baseline_id TEXT REFERENCES baselines(id),
        settings_json TEXT NOT NULL, rendered_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "ALTER TABLE project ADD COLUMN active_structure_id TEXT REFERENCES structures(id)",
    "ALTER TABLE project ADD COLUMN active_baseline_id TEXT REFERENCES baselines(id)",
    """CREATE TRIGGER pseudo_entries_sealed BEFORE INSERT ON pseudo_entries
        WHEN (SELECT count(*) FROM pseudo_entries WHERE pseudo_set_id=NEW.pseudo_set_id)
          >= (SELECT entry_count FROM pseudo_sets WHERE id=NEW.pseudo_set_id)
        BEGIN SELECT RAISE(ABORT, 'Pseudopotential set is immutable'); END""",
) + tuple(
    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'Scientific records are immutable'); END"
    for table in ("structures", "pseudo_sets", "pseudo_entries", "baselines")
    for action in ("UPDATE", "DELETE")
)
SCHEMA_V2_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V2).encode()).hexdigest()


def _apply_v2(connection, timestamp):
    for statement in SCHEMA_V2:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (2, ?, ?)", (timestamp, SCHEMA_V2_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=2")
    connection.execute("PRAGMA user_version=2")


def initialize_project_schema(connection: sqlite3.Connection, timestamp: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    for statement in SCHEMA_V1:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (1, ?, ?)", (timestamp, SCHEMA_V1_CHECKSUM))
    _apply_v2(connection, timestamp)


def migrate_project_schema(connection: sqlite3.Connection, timestamp: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    validate_project_schema(connection)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
        _apply_v2(connection, timestamp)


def validate_project_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"Project schema {version} is newer than supported schema {PROJECT_SCHEMA_VERSION}.")
    if version not in (1, 2):
        raise ProjectError(f"Unsupported project schema {version}.")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ProjectError("The project database failed its integrity check.")
    migrations = connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    expected = [(1, SCHEMA_V1_CHECKSUM)] + ([(2, SCHEMA_V2_CHECKSUM)] if version == 2 else [])
    if [(row["version"], row["checksum"]) for row in migrations] != expected:
        raise ProjectError("The project migration history is missing or inconsistent.")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected_tables = {"project", "schema_migrations"}
    if version == 2:
        expected_tables.update({"structures", "pseudo_sets", "pseudo_entries", "baselines"})
        expected_triggers = {f"{table}_immutable_{action}" for table in ("structures", "pseudo_sets", "pseudo_entries", "baselines") for action in ("update", "delete")}
        expected_triggers.add('pseudo_entries_sealed')
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if triggers != expected_triggers:
            raise ProjectError("The project's scientific immutability constraints are missing.")
    if tables != expected_tables:
        raise ProjectError("The project database does not match the supported schema.")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ProjectError("The project contains invalid record references.")
