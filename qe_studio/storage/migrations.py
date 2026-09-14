"""The first project schema deliberately contains only two tables."""
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


def initialize_project_schema(connection: sqlite3.Connection, timestamp: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    for statement in SCHEMA_V1:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
                       (PROJECT_SCHEMA_VERSION, timestamp, SCHEMA_V1_CHECKSUM))
    connection.execute(f"PRAGMA user_version = {PROJECT_SCHEMA_VERSION}")


def validate_project_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"Project schema {version} is newer than supported schema {PROJECT_SCHEMA_VERSION}.")
    if version != PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"Unsupported project schema {version}; this release supports schema 1.")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ProjectError("The project database failed its integrity check.")
    migrations = connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    if [(row["version"], row["checksum"]) for row in migrations] != [(1, SCHEMA_V1_CHECKSUM)]:
        raise ProjectError("The project migration history is missing or inconsistent.")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != {"project", "schema_migrations"}:
        raise ProjectError("The project database does not match the supported identity-only schema.")
