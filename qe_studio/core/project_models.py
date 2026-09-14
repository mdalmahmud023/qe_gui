"""Project identity only; scientific records are introduced in later phases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_SCHEMA_VERSION = 1
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


@dataclass(frozen=True)
class RecentProject:
    project_id: str
    root_path: Path
    display_name: str
    last_opened_at: datetime
    available: bool
