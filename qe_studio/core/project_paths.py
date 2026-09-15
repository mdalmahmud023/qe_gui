from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .state_keys import QE_INPUT_INTERNAL_PATH_STATE_KEY, QE_INPUT_UI_PATH_STATE_KEY


@dataclass(frozen=True)
class WorkspacePaths:
    """Paths for one legacy workspace; construction never creates files.

    Keep the supplied path spelling for compatibility with existing manifests.
    Resolve paths only when comparing ownership (including symlink targets).
    """

    run_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_dir", Path(self.run_dir))

    @property
    def studies_dir(self) -> Path:
        # All three legacy study types share this directory.
        return self.run_dir / "convergence" / "cutoff"

    @property
    def pseudo_base_dir(self) -> Path:
        return self.run_dir / "pseudopotentials"

    @property
    def sssp_cache_dir(self) -> Path:
        return self.pseudo_base_dir / "sssp"

    def owns_input(self, path: str | Path) -> bool:
        """Whether an input is directly inside this workspace."""
        return Path(path).resolve().parent == self.run_dir.resolve()

    def owns_pseudopotential(self, path: str | Path) -> bool:
        return Path(path).resolve().is_relative_to(self.pseudo_base_dir.resolve())


@dataclass(frozen=True)
class ProjectPaths(WorkspacePaths):
    """Project identity files alongside the existing workspace path contract."""

    @property
    def root_path(self) -> Path:
        return self.run_dir

    @property
    def marker(self) -> Path:
        return self.root_path / "project.json"

    @property
    def database(self) -> Path:
        return self.root_path / "project.sqlite3"

    @property
    def staging_dir(self) -> Path:
        return self.root_path / ".staging"

    @property
    def structures_dir(self) -> Path:
        return self.root_path / "structures"

    @property
    def studies_dir(self) -> Path:
        return self.root_path / "studies"

    def resolve_relative(self, relative: str | Path) -> Path:
        """Reject traversal and symlink escapes for project-owned scientific files."""
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Project payload paths must be project-relative.")
        result = self.root_path / relative
        if not result.resolve().is_relative_to(self.root_path.resolve()):
            raise ValueError("Project payload path leaves the project directory.")
        return result


def session_input_matches_workspace(state: Mapping[str, Any], paths: WorkspacePaths) -> bool:
    """Require recorded ownership before using a session input preview."""
    ui_path = state.get(QE_INPUT_UI_PATH_STATE_KEY)
    internal_path = state.get(QE_INPUT_INTERNAL_PATH_STATE_KEY)
    return bool(
        ui_path
        and paths.owns_input(ui_path)
        and (not internal_path or paths.owns_input(internal_path))
    )
