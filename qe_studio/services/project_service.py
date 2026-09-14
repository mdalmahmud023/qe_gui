"""Create, validate and rename projects without moving scientific files."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
from uuid import UUID, uuid4

from ..core.project_models import PROJECT_FORMAT, PROJECT_SCHEMA_VERSION, Project, ProjectConflictError, ProjectError
from ..core.project_paths import ProjectPaths
from ..storage.database import connect
from ..storage.migrations import initialize_project_schema, validate_project_schema


def _name(value: str) -> str:
    if not isinstance(value, str):
        raise ProjectError("Display name must be text.")
    value = value.strip()
    if not value or len(value) > 200:
        raise ProjectError("Display name must contain 1–200 characters.")
    return value


def _timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ProjectError("Project timestamps must include a timezone.")
    return timestamp.astimezone(timezone.utc)


def _root_path(value: Path | str) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ProjectError(f"Invalid project location: {exc}") from exc


def _project_from_row(row, root: Path) -> Project:
    if row is None:
        raise ProjectError("The project database has no identity record.")
    identifier = str(UUID(row["id"]))
    if identifier != row["id"] or row["schema_version"] != PROJECT_SCHEMA_VERSION:
        raise ProjectError("The project identity or schema version is invalid.")
    if not isinstance(row["description"], str):
        raise ProjectError("Project description must be text.")
    project = Project(identifier, _name(row["display_name"]), row["description"],
                      _timestamp(row["created_at"]), _timestamp(row["modified_at"]), root,
                      row["schema_version"])
    if project.modified_at < project.created_at:
        raise ProjectError("Project modification time precedes its creation time.")
    return project


def open_project(root: Path | str) -> Project:
    """Read identity at its current location. Never create or migrate on open."""
    paths = ProjectPaths(_root_path(root))
    try:
        if not paths.root_path.is_dir():
            raise ProjectError(f"Project directory is missing: {paths.root_path}")
        if not paths.marker.is_file() or not paths.database.is_file():
            raise ProjectError("Select a project directory containing project.json and project.sqlite3.")
        if paths.marker.is_symlink() or paths.database.is_symlink():
            raise ProjectError("Project identity files must be stored inside the project, not linked elsewhere.")
        marker = json.loads(paths.marker.read_text(encoding="utf-8"))
        if not isinstance(marker, dict) or marker.get("format") != PROJECT_FORMAT:
            raise ProjectError("This directory is not a QE Studio project.")
        if marker.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise ProjectError(f"Unsupported project marker schema {marker.get('schema_version')}; supported schema is 1.")
        if marker.get("database") != "project.sqlite3":
            raise ProjectError("The project marker must reference project.sqlite3.")
        with connect(paths.database, read_only=True) as connection:
            validate_project_schema(connection)
            rows = connection.execute("SELECT * FROM project").fetchall()
            if len(rows) != 1:
                raise ProjectError("The project database must contain exactly one project.")
            project = _project_from_row(rows[0], paths.root_path)
        if marker.get("project_id") != project.id:
            raise ProjectError("Project marker UUID does not match the database UUID.")
        return project
    except ProjectError:
        raise
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, IndexError) as exc:
        raise ProjectError(f"Cannot open project at {paths.root_path}: {exc}") from exc


def create_project(parent: Path | str, display_name: str, description: str = "", *, folder_name: str | None = None) -> Project:
    """Create a new directory exclusively; remove only our own files on failure."""
    display_name = _name(display_name)
    identifier = str(uuid4())
    parent = _root_path(parent)
    automatic_folder = folder_name is None
    if folder_name is not None:
        folder_name = folder_name.strip()
        if not folder_name or folder_name in {".", ".."} or "/" in folder_name or "\\" in folder_name:
            raise ProjectError("Folder name must be a single directory name.")
    else:
        ascii_name = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
        folder_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", ascii_name).strip("-")[:80] or "Project"
        if (parent / folder_name).exists():
            folder_name = f"{folder_name}-{identifier[:8]}"
    paths = ProjectPaths(parent / folder_name)
    created_root = False
    created_parents = []
    try:
        missing = []
        ancestor = parent
        while not ancestor.exists():
            missing.append(ancestor)
            ancestor = ancestor.parent
        for directory in reversed(missing):
            try:
                directory.mkdir()
                created_parents.append(directory)
            except FileExistsError:
                if not directory.is_dir():
                    raise
        try:
            paths.root_path.mkdir()  # Never initialize an existing directory.
        except FileExistsError:
            if not automatic_folder:
                raise
            # Another tab may have allocated the same display-name folder after
            # our existence check. Keep both projects with distinct identities.
            paths = ProjectPaths(parent / f"{folder_name}-{identifier[:8]}")
            paths.root_path.mkdir()
        created_root = True
        paths.staging_dir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        with connect(paths.database, create=True) as connection:
            initialize_project_schema(connection, now)
            connection.execute("INSERT INTO project VALUES (1, ?, ?, ?, ?, ?, ?)",
                               (identifier, display_name, description, now, now, PROJECT_SCHEMA_VERSION))
        marker = {"format": PROJECT_FORMAT, "schema_version": PROJECT_SCHEMA_VERSION,
                  "project_id": identifier, "database": "project.sqlite3"}
        pending = paths.staging_dir / "project.json.tmp"
        pending.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        pending.replace(paths.marker)
        return open_project(paths.root_path)
    except Exception as exc:
        if created_root:
            shutil.rmtree(paths.root_path)
        for directory in reversed(created_parents):
            try:
                directory.rmdir()  # Do not remove files created by another caller.
            except OSError:
                pass
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError(f"Could not create project: {exc}") from exc


def rename_project(project: Project, display_name: str, *, description: str | None = None) -> Project:
    """Optimistic metadata update; identity and folder name never change."""
    display_name = _name(display_name)
    current = open_project(project.root_path)
    if current.id != project.id:
        raise ProjectConflictError("This directory now belongs to a different project.")
    timestamp = max(datetime.now(timezone.utc), project.modified_at + timedelta(microseconds=1))
    try:
        with connect(ProjectPaths(project.root_path).database) as connection:
            cursor = connection.execute(
                "UPDATE project SET display_name=?, description=?, modified_at=? WHERE id=? AND modified_at=?",
                (display_name, project.description if description is None else description,
                 timestamp.isoformat(), project.id, project.modified_at.isoformat()),
            )
            if cursor.rowcount != 1:
                raise ProjectConflictError("Project details changed in another tab. Reload before saving again.")
        return open_project(project.root_path)
    except sqlite3.Error as exc:
        raise ProjectError(f"Could not save project details: {exc}") from exc
