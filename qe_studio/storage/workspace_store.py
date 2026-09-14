"""Disposable, machine-local project locations; never scientific ownership."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from ..core.project_models import Project, ProjectConflictError, ProjectError, RecentProject
from .database import connect


def workspace_database_path() -> Path:
    base = os.environ.get("QE_STUDIO_WORKSPACE_DIR")
    if base:
        return Path(base).expanduser() / "workspace.sqlite3"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "qe-studio/workspace.sqlite3"


class WorkspaceStore:
    def __init__(self, database: Path | None = None):
        self.database = Path(database) if database is not None else workspace_database_path()

    def _initialize(self, connection):
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ProjectError(f"Unsupported workspace index schema {version}.")
        if version == 0:
            if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ProjectError("Unrecognized workspace index schema.")
            connection.execute("""CREATE TABLE recent_projects (
                project_id TEXT PRIMARY KEY, root_path TEXT NOT NULL UNIQUE,
                display_name_cache TEXT NOT NULL, last_opened_at TEXT NOT NULL
            )""")
            connection.execute("PRAGMA user_version = 1")

    def remember(self, project: Project) -> None:
        """Update a moved UUID's location; reject two live copies of that UUID."""
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with connect(self.database, create=True) as connection:
                self._initialize(connection)
                prior = connection.execute("SELECT root_path FROM recent_projects WHERE project_id=?", (project.id,)).fetchone()
                if prior and Path(prior[0]) != project.root_path and Path(prior[0]).exists():
                    from ..services.project_service import open_project
                    try:
                        previous_project = open_project(prior[0])
                    except ProjectError:
                        previous_project = None
                    if previous_project is not None and previous_project.id == project.id:
                        raise ProjectConflictError("This project UUID exists at another location. Move the original directory; independent copies are not supported yet.")
                # A directory may have been replaced by a different valid project.
                connection.execute("DELETE FROM recent_projects WHERE root_path=? AND project_id<>?", (str(project.root_path), project.id))
                connection.execute("""INSERT INTO recent_projects VALUES (?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET root_path=excluded.root_path,
                    display_name_cache=excluded.display_name_cache, last_opened_at=excluded.last_opened_at""",
                    (project.id, str(project.root_path), project.display_name, datetime.now(timezone.utc).isoformat()))
        except sqlite3.Error as exc:
            raise ProjectError(f"Cannot update recent projects: {exc}") from exc
        except OSError as exc:
            raise ProjectError(f"Cannot access recent projects: {exc}") from exc

    def list_recent(self) -> list[RecentProject]:
        if not self.database.exists():
            return []
        try:
            with connect(self.database, read_only=True) as connection:
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ProjectError("Unsupported workspace index schema.")
                rows = connection.execute("SELECT * FROM recent_projects ORDER BY last_opened_at DESC").fetchall()
            return [RecentProject(row["project_id"], Path(row["root_path"]), row["display_name_cache"],
                                  datetime.fromisoformat(row["last_opened_at"]),
                                  (Path(row["root_path"]) / "project.json").is_file()
                                  and (Path(row["root_path"]) / "project.sqlite3").is_file()) for row in rows]
        except (sqlite3.Error, ValueError, OSError) as exc:
            raise ProjectError(f"Cannot read recent projects: {exc}") from exc
