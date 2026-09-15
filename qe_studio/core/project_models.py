"""Project identity and immutable Phase 2 scientific records."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_SCHEMA_VERSION = 2
PROJECT_MARKER_VERSION = 1  # Container format; SQLite migrations are independent.
PROJECT_FORMAT = "qe-studio-project"


class ProjectError(ValueError):
    """An actionable project validation or lifecycle error."""


class ProjectConflictError(ProjectError):
    """Project metadata changed since the caller read it."""


@dataclass(frozen=True)
class Project:
    id: str
    display_name: str
    description: str
    created_at: datetime
    modified_at: datetime
    root_path: Path
    schema_version: int = PROJECT_SCHEMA_VERSION
    active_structure_id: str | None = None
    active_baseline_id: str | None = None


@dataclass(frozen=True)
class StructureRecord:
    id: str
    sequence: int
    name: str
    parent_structure_id: str | None
    operation: str
    operation_json: str
    source_json: str
    snapshot_relpath: str
    snapshot_sha256: str
    structure_hash: str
    hash_version: int
    created_at: str


@dataclass(frozen=True)
class PseudopotentialSet:
    id: str
    sequence: int
    settings_json: str
    manifest_relpath: str
    manifest_sha256: str
    pseudo_set_hash: str
    entry_count: int
    created_at: str


@dataclass(frozen=True)
class BaselineRevision:
    id: str
    sequence: int
    structure_id: str
    pseudo_set_id: str
    previous_baseline_id: str | None
    settings_json: str
    rendered_json: str
    created_at: str


@dataclass(frozen=True)
class RecentProject:
    project_id: str
    root_path: Path
    display_name: str
    last_opened_at: datetime
    available: bool
