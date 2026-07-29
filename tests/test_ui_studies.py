from __future__ import annotations

from pathlib import Path

from ase.build import bulk
import pytest
import streamlit as st
from qe_studio.pages import ui_studies
from qe_studio.services import qe_runner


@pytest.fixture(autouse=True)
def _clear_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def test_ui_studies_importable():
    assert ui_studies is not None


def test_build_study_selector_options_groups_latest_revision_by_material() -> None:
    studies = [
        Path("/tmp/na2cacdf6_20260422T130805Z"),
        Path("/tmp/na2cacdf6_20260422T130533Z"),
        Path("/tmp/h6cacdna2_20260418T032811Z"),
    ]
    manifest_by_path = {
        str(studies[0]): {
            "study_type": "eos",
            "created_at": "2026-04-22T13:08:05+00:00",
            "structure": {
                "formula": "Na2CaCdF6",
                "structure_signature": {"hash": "na2-signature"},
            },
        },
        str(studies[1]): {
            "study_type": "eos",
            "created_at": "2026-04-22T13:05:33+00:00",
            "structure": {
                "formula": "Na2CaCdF6",
                "structure_signature": {"hash": "na2-signature"},
            },
        },
        str(studies[2]): {
            "study_type": "eos",
            "created_at": "2026-04-18T03:28:11+00:00",
            "structure": {
                "formula": "H6CaCdNa2",
                "structure_signature": {"hash": "h6-signature"},
            },
        },
    }

    options, labels, canonical_paths = ui_studies._build_study_selector_options(
        studies,
        load_manifest=lambda path: manifest_by_path[str(path)],
        latest_per_material=True,
    )

    assert options == [str(studies[0]), str(studies[2])]
    assert canonical_paths[str(studies[0])] == str(studies[0])
    assert canonical_paths[str(studies[1])] == str(studies[0])
    assert labels[str(studies[0])].startswith("Na2CaCdF6")
    assert "2 revisions" in labels[str(studies[0])]


def test_build_study_selector_options_falls_back_to_timestampless_directory_prefix() -> None:
    studies = [
        Path("/tmp/na2cacdf6_20260422T130805Z"),
        Path("/tmp/na2cacdf6_20260422T130533Z"),
    ]
    manifest_by_path = {
        str(studies[0]): {
            "study_type": "eos",
            "created_at": "2026-04-22T13:08:05+00:00",
        },
        str(studies[1]): {
            "study_type": "eos",
            "created_at": "2026-04-22T13:05:33+00:00",
        },
    }

    options, labels, canonical_paths = ui_studies._build_study_selector_options(
        studies,
        load_manifest=lambda path: manifest_by_path[str(path)],
        latest_per_material=True,
    )

    assert options == [str(studies[0])]
    assert canonical_paths[str(studies[1])] == str(studies[0])
    assert labels[str(studies[0])].startswith("na2cacdf6")


class _DummyThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _DummyContext:
    def __init__(self, *, alive: bool = True) -> None:
        self.thread = _DummyThread(alive)


def test_sync_study_run_state_updates_running_phase() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-1"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "kpoint"

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="kpoint",
        get_context_fn=lambda _run_id: _DummyContext(alive=True),
        drain_events_fn=lambda _run_id: [
            qe_runner.RunEvent(
                type=qe_runner.EVENT_TYPE_PHASE,
                phase_label="pw.x (k-grid 4x4x4)",
                output_path="qe_runs/kpoint/4x4x4/pw.out",
                line="\n=== pw.x (k-grid 4x4x4) ===\n",
            ),
            qe_runner.RunEvent(type=qe_runner.EVENT_TYPE_LINE, line="total cpu time = 1.23s\n"),
        ],
    )

    assert result == "running"
    assert state[qe_runner.RUN_PHASE_LABEL_STATE_KEY] == "pw.x (k-grid 4x4x4)"
    assert state[qe_runner.RUN_OUTPUT_PATH_STATE_KEY] == "qe_runs/kpoint/4x4x4/pw.out"
    assert state[qe_runner.RUN_TAIL_LINES_STATE_KEY] == [
        "\n=== pw.x (k-grid 4x4x4) ===\n",
        "total cpu time = 1.23s\n",
    ]
    assert state[ui_studies._STUDY_RUN_ID_KEY] == "run-1"


def test_sync_study_run_state_clears_active_keys_when_complete() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-2"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "eos"
    cleaned: list[str | None] = []

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="eos",
        get_context_fn=lambda _run_id: _DummyContext(alive=True),
        drain_events_fn=lambda _run_id: [
            qe_runner.RunEvent(
                type=qe_runner.EVENT_TYPE_PHASE,
                phase_label="pw.x (scale=1.0000)",
                output_path="qe_runs/eos/point_1/pw.out",
                line="\n=== pw.x (scale=1.0000) ===\n",
            ),
            qe_runner.RunEvent(type=qe_runner.EVENT_TYPE_COMPLETE, returncode=0),
        ],
        cleanup_fn=cleaned.append,
    )

    assert result == "completed"
    assert cleaned == ["run-2"]
    assert state[qe_runner.RUN_FINISHED_STATE_KEY] is True
    assert state[qe_runner.RUN_RETURNCODE_STATE_KEY] == 0
    assert ui_studies._STUDY_RUN_ID_KEY not in state
    assert ui_studies._STUDY_RUN_OWNER_KEY not in state


def test_sync_study_run_state_marks_runtime_lost_when_worker_disappears() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-3"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "cutoff"
    cleaned: list[str | None] = []

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="cutoff",
        get_context_fn=lambda _run_id: _DummyContext(alive=False),
        drain_events_fn=lambda _run_id: [],
        cleanup_fn=cleaned.append,
    )

    assert result == "runtime_lost"
    assert cleaned == ["run-3"]
    assert state[qe_runner.RUN_FINISHED_STATE_KEY] is True
    assert state[qe_runner.RUN_RETURNCODE_STATE_KEY] == qe_runner.DEFAULT_FAILURE_RETURNCODE
    assert "runtime state was lost" in "".join(state[qe_runner.RUN_TAIL_LINES_STATE_KEY]).lower()
    assert ui_studies._STUDY_RUN_ID_KEY not in state
    assert ui_studies._STUDY_RUN_OWNER_KEY not in state


def test_eos_summary_needs_refresh_when_output_exists(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    point_dir = study_dir / "point_scale_1p0000"
    point_dir.mkdir(parents=True)
    (point_dir / "pw.out").write_text("JOB DONE.\n", encoding="utf-8")

    summary = {
        "fit": {
            "energy_basis": "cell_total_ev",
            "volume_basis": "cell_total_ang3",
            "natoms_in_fitted_cell": 2,
            "equilibrium_geometry": {
                "available": True,
                "conventional_cell": {
                    "available": True,
                },
            },
        },
        "points": [
            {
                "run_dir_name": "point_scale_1p0000",
                "status": "missing_output",
            }
        ]
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is True


def test_eos_summary_does_not_refresh_without_matching_output(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    study_dir.mkdir()

    summary = {
        "fit": {
            "energy_basis": "cell_total_ev",
            "volume_basis": "cell_total_ang3",
            "natoms_in_fitted_cell": 2,
            "equilibrium_geometry": {
                "available": True,
                "conventional_cell": {
                    "available": True,
                },
            },
        },
        "points": [
            {
                "run_dir_name": "point_scale_1p0000",
                "status": "missing_output",
            }
        ]
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is False


def test_eos_summary_needs_refresh_when_fit_basis_is_legacy(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    study_dir.mkdir()

    summary = {
        "fit": {
            "natoms_in_fitted_cell": 2,
        },
        "points": [],
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is True


def test_eos_summary_needs_refresh_when_equilibrium_geometry_is_missing(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    study_dir.mkdir()

    summary = {
        "fit": {
            "energy_basis": "cell_total_ev",
            "volume_basis": "cell_total_ang3",
            "natoms_in_fitted_cell": 2,
        },
        "points": [],
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is True


def test_resolve_eos_fitted_cell_atom_count_rejects_inconsistent_metadata() -> None:
    manifest = {
        "atom_count": 2,
        "structure": {"atom_count": 4},
    }
    summary = {
        "fit": {
            "natoms_in_fitted_cell": 2,
        }
    }

    natoms, warning = ui_studies._resolve_eos_fitted_cell_atom_count(manifest, summary)

    assert natoms is None
    assert warning is not None


def test_resolve_standardized_conventional_lattice_a_from_primitive_structure() -> None:
    atoms = bulk("Si", "diamond", a=5.43)

    lattice_a, warning = ui_studies._resolve_standardized_conventional_lattice_a(atoms)

    assert warning is None
    assert lattice_a is not None
    assert lattice_a == pytest.approx(5.43, rel=1e-6)


def test_resolve_standardized_conventional_lattice_a_rejects_missing_structure() -> None:
    lattice_a, warning = ui_studies._resolve_standardized_conventional_lattice_a(None)

    assert lattice_a is None
    assert warning is not None


def test_scale_structure_source_details_for_eos_apply_updates_prototype_lattice_parameter() -> None:
    scaled = ui_studies._scale_structure_source_details_for_eos_apply(
        source_kind="prototype",
        source_details={
            "_prototype_family_key": "ordered_double_perovskite_a2bbx6",
            "lattice_parameter": 8.0,
        },
        scale_factor=1.25,
    )

    assert scaled["lattice_parameter"] == pytest.approx(10.0)


def test_sync_scaled_structure_source_draft_updates_durable_prototype_value() -> None:
    ui_studies._sync_scaled_structure_source_draft(
        source_kind="prototype",
        source_details={
            "_prototype_family_key": "ordered_double_perovskite_a2bbx6",
            "lattice_parameter": 8.75,
        },
    )

    from qe_studio.pages import ui_structure

    prototype_draft = ui_structure._get_structure_drafts()["prototype"]
    assert prototype_draft["selected_family"] == "ordered_double_perovskite_a2bbx6"
    assert prototype_draft["families"]["ordered_double_perovskite_a2bbx6"]["lattice_parameter"] == pytest.approx(8.75)


def test_get_available_eos_display_modes_disables_normalized_modes_without_atom_count() -> None:
    assert ui_studies._get_available_eos_display_modes(natoms_in_fitted_cell=None) == ["cell_total"]
    assert ui_studies._get_available_eos_display_modes(natoms_in_fitted_cell=4) == [
        "cell_total",
        "per_atom",
        "relative_per_atom",
    ]
    assert ui_studies._normalize_eos_display_mode("relative_per_atom", natoms_in_fitted_cell=None) == "cell_total"
    assert ui_studies._EOS_DISPLAY_MODE_LABELS["relative_per_atom"] == "Relative: ΔE (meV/atom) vs Volume/atom (Å³/atom)"


def test_get_eos_fit_display_values_keep_bulk_modulus_invariant_across_modes() -> None:
    fit = {
        "E0": -20.0,
        "V0": 100.0,
        "B0": 120.0,
    }

    cell_total = ui_studies._get_eos_fit_display_values(
        fit,
        display_mode="cell_total",
        natoms_in_fitted_cell=4,
    )
    per_atom = ui_studies._get_eos_fit_display_values(
        fit,
        display_mode="per_atom",
        natoms_in_fitted_cell=4,
    )
    relative = ui_studies._get_eos_fit_display_values(
        fit,
        display_mode="relative_per_atom",
        natoms_in_fitted_cell=4,
    )

    assert cell_total["bulk_modulus_value"] == per_atom["bulk_modulus_value"] == relative["bulk_modulus_value"] == 120.0
    assert cell_total["volume_value"] == 100.0
    assert per_atom["volume_value"] == 25.0
    assert relative["volume_value"] == 25.0
    assert cell_total["energy_value"] == -20.0
    assert per_atom["energy_value"] == -5.0
    assert relative["energy_display_text"] == "fitted E0"
    assert "ΔE = (E_total - E0_total) / N" in str(relative["energy_note"])
