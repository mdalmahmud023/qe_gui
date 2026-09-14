from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from qe_studio.core.project_context import build_project_context
from qe_studio.core.project_paths import WorkspacePaths, session_input_matches_workspace
from qe_studio.core.state_keys import QE_INPUT_INTERNAL_PATH_STATE_KEY, QE_INPUT_UI_PATH_STATE_KEY
from qe_studio.core.workspace import QE_CONVERGENCE_DIR, QE_RUNS_DIR
from qe_studio.services.pseudopotentials import DEFAULT_PSEUDO_BASE_CACHE_ROOT, DEFAULT_PSEUDO_CACHE_ROOT


def test_paths_preserve_legacy_layout_without_creating_directories(tmp_path):
    paths = WorkspacePaths(QE_RUNS_DIR)
    assert paths.studies_dir == QE_CONVERGENCE_DIR
    assert paths.pseudo_base_dir == DEFAULT_PSEUDO_BASE_CACHE_ROOT
    assert paths.sssp_cache_dir == DEFAULT_PSEUDO_CACHE_ROOT
    absent = tmp_path / "not-created"
    custom = WorkspacePaths(str(absent))
    assert custom.studies_dir == absent / "convergence/cutoff"
    assert not absent.exists()
    with pytest.raises(FrozenInstanceError):
        custom.run_dir = tmp_path


def test_context_paths_are_derived_from_its_run_directory(tmp_path):
    ctx = build_project_context(tmp_path / "a")
    changed = replace(ctx, run_dir=tmp_path / "b")
    assert ctx.paths.run_dir == tmp_path / "a"
    assert changed.paths.run_dir == tmp_path / "b"


def test_path_ownership_resolves_relative_paths_and_symlinks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = WorkspacePaths("a")
    Path("a").mkdir()
    Path("b").mkdir()
    assert paths.owns_input(tmp_path / "a/pw.in")
    assert not paths.owns_input("b/pw.in")
    assert not paths.owns_input("a/study/pw.in")
    Path("a/pw.in").symlink_to(tmp_path / "b/pw.in")
    assert not paths.owns_input("a/pw.in")


def test_preview_requires_matching_recorded_paths(tmp_path):
    paths = WorkspacePaths(tmp_path / "a")
    assert not session_input_matches_workspace({}, paths)
    state = {QE_INPUT_UI_PATH_STATE_KEY: str(paths.run_dir / "pw.in")}
    assert session_input_matches_workspace(state, paths)
    state[QE_INPUT_INTERNAL_PATH_STATE_KEY] = str(tmp_path / "b/espresso.pwi")
    assert not session_input_matches_workspace(state, paths)
