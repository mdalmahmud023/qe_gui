from __future__ import annotations

from dataclasses import dataclass
import io
import os
import re
from pathlib import Path
from typing import Any

import streamlit as st
from ase.io import read

from electronic_properties import AnalysisFiles, find_available_analysis_files
from pseudopotentials import get_structure_signature, has_same_element_set
from qe_runner import (
    RUN_ACTIVE_STATE_KEY,
    RUN_FINISHED_STATE_KEY,
    RUN_JOB_LABEL_STATE_KEY,
    RUN_JOB_OWNER_STATE_KEY,
    RUN_OUTPUT_PATH_STATE_KEY,
    RUN_PHASE_LABEL_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    initialize_run_state,
    reset_run_state,
)
from structure_tools import make_primitive_atoms


ATOMS_STATE_KEY = "structure_atoms"
RAW_ATOMS_STATE_KEY = "structure_raw_atoms"
CIF_BYTES_STATE_KEY = "structure_cif_bytes"
FILENAME_STATE_KEY = "structure_filename"
ERROR_STATE_KEY = "structure_error"
STRUCTURE_MODE_STATE_KEY = "structure_mode"
STRUCTURE_MODE_STATUS_STATE_KEY = "structure_mode_status"
QE_PARAMS_STATE_KEY = "qe_parameters"
QE_INPUT_TEXT_STATE_KEY = "qe_input_text"
QE_INPUT_INTERNAL_PATH_STATE_KEY = "qe_input_internal_path"
QE_INPUT_UI_PATH_STATE_KEY = "qe_input_ui_path"
PSEUDO_SETTINGS_STATE_KEY = "pseudo_settings"
PSEUDO_DIR_STATE_KEY = "pseudo_dir"
PSEUDO_RESOLVED_MAP_STATE_KEY = "pseudo_resolved_map"
PSEUDO_RESOLVED_ROWS_STATE_KEY = "pseudo_resolved_rows"
PSEUDO_RESOLUTION_STATUS_STATE_KEY = "pseudo_resolution_status"
PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY = "pseudo_structure_signature"

PSEUDO_FUNCTIONAL_WIDGET_KEY = "pseudo_widget_functional"
PSEUDO_MODE_WIDGET_KEY = "pseudo_widget_mode"
PSEUDO_VERSION_WIDGET_KEY = "pseudo_widget_version"
PSEUDO_USE_CACHE_WIDGET_KEY = "pseudo_widget_use_cache"
BAND_NBND_WIDGET_KEY = "band_nbnd_widget"
BAND_COORDINATE_MODE_WIDGET_KEY = "band_coordinate_mode_widget"
BAND_PATH_WIDGET_KEY = "band_path_widget"
AUTO_BAND_PATH_POINTS_WIDGET_KEY = "auto_band_path_points_widget"
AUTO_BAND_PATH_STATUS_STATE_KEY = "auto_band_path_status"
BAND_ALIGNMENT_MODE_WIDGET_KEY = "band_alignment_mode_widget"
DOS_ALIGNMENT_WIDGET_KEY = "dos_align_to_fermi"

QE_CALCULATION_OPTIONS = ("scf", "nscf", "relax", "vc-relax")
QE_SMEARING_OPTIONS = ("gaussian", "methfessel-paxton", "marzari-vanderbilt")
PSEUDO_FUNCTIONAL_OPTIONS = ("PBE", "PBEsol")
PSEUDO_MODE_OPTIONS = ("efficiency", "precision")
PSEUDO_VERSION_OPTIONS = ("1.3.0",)
DEFAULT_PSEUDO_SETTINGS = {
    "functional": "PBE",
    "mode": "efficiency",
    "version": "1.3.0",
}

QE_RUNS_DIR = Path("qe_runs")
QE_RUN_COMMAND = ("pw.x", "-in", "pw.in")
QE_OUTPUT_FILENAME = "pw.out"
DOS_COMMAND = ("dos.x", "-i", "dos.in")
BANDS_PW_COMMAND = ("pw.x", "-in", "bands_pw.in")
BANDS_PP_COMMAND = ("bands.x", "-i", "bands_pp.in")
DOS_INPUT_FILENAME = "dos.in"
DOS_OUTPUT_FILENAME = "dos.out"
BANDS_PW_INPUT_FILENAME = "bands_pw.in"
BANDS_PW_OUTPUT_FILENAME = "bands_pw.out"
BANDS_PP_INPUT_FILENAME = "bands_pp.in"
BANDS_PP_OUTPUT_FILENAME = "bands_pp.out"
FILBAND_FILENAME = "filband"
FILBAND_GNU_FILENAME = "filband.gnu"
JOB_SUMMARIES_STATE_KEY = "qe_job_summaries"
JOB_OWNER_PW = "pw"
JOB_OWNER_DOS = "dos"
JOB_OWNER_BANDS = "bands"
DOS_BZ_SUM_OPTIONS = ("smearing", "tetrahedra", "tetrahedra_lin", "tetrahedra_opt")
BAND_COORDINATE_MODE_OPTIONS = ("crystal_b", "tpiba_b")
BAND_ALIGNMENT_MODE_OPTIONS = ("vbm", "fermi", "raw")
DEFAULT_BAND_ALIGNMENT_MODE = "vbm"
DEFAULT_BAND_PATH_TEXT = "0.0 0.0 0.0 20 ! G\n0.5 0.0 0.0 20 ! X\n0.5 0.5 0.5 1 ! L\n"
STRUCTURE_MODE_OPTIONS = ("uploaded", "primitive")


@dataclass(frozen=True)
class StructureSummary:
    loaded: bool
    filename: str | None
    formula: str | None
    atom_count: int
    cell_mode: str
    cell_mode_label: str
    mode_status: str | None
    cell_parameters: str | None
    error_message: str | None


@dataclass(frozen=True)
class PseudopotentialSummary:
    state: str
    ready: bool
    status_label: str
    message: str | None
    warning_count: int
    pseudo_dir: str | None
    resolved_count: int


@dataclass(frozen=True)
class InputSummary:
    available: bool
    status_label: str
    blocker_message: str | None
    preview_source: str
    ui_path: Path
    internal_path: Path
    has_preview: bool


@dataclass(frozen=True)
class JobStatusSummary:
    active_owner: str | None
    active_label: str | None
    active_phase: str | None
    run_active: bool
    run_finished: bool
    latest_status_label: str
    latest_status_detail: str | None


@dataclass(frozen=True)
class ArtifactSummary:
    has_any_artifact: bool
    dos_detected: bool
    bands_detected: bool
    metadata_detected: bool
    dos_file: Path | None
    band_file: Path | None
    band_input_file: Path | None
    pw_output_file: Path | None


@dataclass(frozen=True)
class ProjectContext:
    run_dir: Path
    atoms: Any | None
    raw_atoms: Any | None
    analysis_files: AnalysisFiles
    structure: StructureSummary
    pseudopotentials: PseudopotentialSummary
    input_files: InputSummary
    jobs: JobStatusSummary
    artifacts: ArtifactSummary


def clear_generated_qe_input_state() -> None:
    """Remove generated QE input preview state."""
    for key in (
        QE_INPUT_TEXT_STATE_KEY,
        QE_INPUT_INTERNAL_PATH_STATE_KEY,
        QE_INPUT_UI_PATH_STATE_KEY,
        AUTO_BAND_PATH_STATUS_STATE_KEY,
    ):
        st.session_state.pop(key, None)


def clear_pseudopotential_state(*, clear_structure_signature: bool = True) -> None:
    """Remove stored pseudopotential resolution state."""
    clear_generated_qe_input_state()
    for key in (
        PSEUDO_DIR_STATE_KEY,
        PSEUDO_RESOLVED_MAP_STATE_KEY,
        PSEUDO_RESOLVED_ROWS_STATE_KEY,
        PSEUDO_RESOLUTION_STATUS_STATE_KEY,
    ):
        st.session_state.pop(key, None)
    if clear_structure_signature:
        st.session_state.pop(PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY, None)


def clear_structure_state() -> None:
    """Remove stored structure and dependent state."""
    clear_pseudopotential_state(clear_structure_signature=True)
    for key in (
        ATOMS_STATE_KEY,
        RAW_ATOMS_STATE_KEY,
        CIF_BYTES_STATE_KEY,
        FILENAME_STATE_KEY,
        ERROR_STATE_KEY,
        STRUCTURE_MODE_STATE_KEY,
        STRUCTURE_MODE_STATUS_STATE_KEY,
    ):
        st.session_state.pop(key, None)


def parse_cif_bytes(cif_bytes: bytes):
    """Parse the first CIF structure block into ASE atoms."""
    return read(io.BytesIO(cif_bytes), format="cif", index=0)


def get_structure_mode() -> str:
    """Return the current structure-mode selection."""
    return st.session_state.get(STRUCTURE_MODE_STATE_KEY, "uploaded")


def resolve_atoms_for_structure_mode(raw_atoms, mode: str) -> tuple[object, str, str]:
    """Return the active atoms object for the selected structure mode."""
    if raw_atoms is None:
        raise ValueError("No structure is loaded.")

    if mode != "primitive":
        return raw_atoms.copy(), "uploaded", "Using the uploaded cell."

    try:
        primitive_result = make_primitive_atoms(raw_atoms)
    except Exception as exc:
        return raw_atoms.copy(), "uploaded", f"Primitive-cell reduction failed; using the uploaded cell instead: {exc}"
    return primitive_result.atoms, "primitive", primitive_result.message


def apply_structure_mode_selection(mode: str, *, preserve_resolution_status: bool = True) -> None:
    """Apply a new active structure mode using the stored raw uploaded structure."""
    raw_atoms = st.session_state.get(RAW_ATOMS_STATE_KEY)
    if raw_atoms is None:
        return

    active_atoms, resolved_mode, status_message = resolve_atoms_for_structure_mode(raw_atoms, mode)
    st.session_state[ATOMS_STATE_KEY] = active_atoms
    st.session_state[STRUCTURE_MODE_STATE_KEY] = resolved_mode
    st.session_state[STRUCTURE_MODE_STATUS_STATE_KEY] = status_message
    st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = get_structure_signature(active_atoms)
    clear_generated_qe_input_state()
    reset_run_state(st.session_state)

    if preserve_resolution_status and has_resolved_pseudopotential_data():
        status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY)
        if status and status.get("state") == "resolved":
            set_resolution_status(
                state="resolved",
                message=status.get("message", "Resolved pseudopotentials are available."),
                warnings=list(status.get("warnings", [])),
                resolved_settings=status.get("resolved_settings"),
                resolved_structure_signature=get_structure_signature(active_atoms),
            )


def format_cell_parameters(atoms) -> str:
    """Format basic cell parameters for sidebar and structure display."""
    a, b, c, alpha, beta, gamma = atoms.cell.cellpar()
    return (
        f"a = {a:.3f} A\n"
        f"b = {b:.3f} A\n"
        f"c = {c:.3f} A\n"
        f"alpha = {alpha:.2f} deg\n"
        f"beta = {beta:.2f} deg\n"
        f"gamma = {gamma:.2f} deg"
    )


def has_resolved_pseudopotential_data() -> bool:
    """Return whether resolved pseudopotential data is present in session state."""
    return bool(
        st.session_state.get(PSEUDO_DIR_STATE_KEY)
        and st.session_state.get(PSEUDO_RESOLVED_MAP_STATE_KEY)
        and st.session_state.get(PSEUDO_RESOLVED_ROWS_STATE_KEY)
    )


def set_resolution_status(
    *,
    state: str,
    message: str,
    warnings: list[str] | None = None,
    resolved_settings: dict | None = None,
    resolved_structure_signature: dict | None = None,
) -> None:
    """Store a consistent pseudopotential resolution status payload."""
    current_status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    st.session_state[PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
        "state": state,
        "message": message,
        "warnings": list(warnings or []),
        "resolved_settings": resolved_settings or current_status.get("resolved_settings"),
        "resolved_structure_signature": (
            resolved_structure_signature or current_status.get("resolved_structure_signature")
        ),
    }


def mark_pseudopotentials_stale(message: str) -> None:
    """Keep the previous resolution visible, but block new QE generation."""
    if not has_resolved_pseudopotential_data():
        return
    clear_generated_qe_input_state()
    set_resolution_status(state="stale", message=message)


def get_current_pseudo_settings() -> dict:
    """Return the active pseudopotential selector values."""
    return {
        "functional": st.session_state.get(PSEUDO_FUNCTIONAL_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["functional"]),
        "mode": st.session_state.get(PSEUDO_MODE_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["mode"]),
        "version": st.session_state.get(PSEUDO_VERSION_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["version"]),
    }


def sync_pseudopotential_settings_state() -> None:
    """Persist current widget selections and mark stale on supported setting changes."""
    current_settings = get_current_pseudo_settings()
    st.session_state[PSEUDO_SETTINGS_STATE_KEY] = current_settings

    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY)
    if not status or status.get("state") != "resolved" or not has_resolved_pseudopotential_data():
        return

    resolved_settings = status.get("resolved_settings") or {}
    if any(current_settings[key] != resolved_settings.get(key) for key in ("functional", "mode", "version")):
        mark_pseudopotentials_stale(
            "Pseudopotential settings changed. Re-resolve pseudopotentials before generating a runnable Quantum ESPRESSO input."
        )


def store_uploaded_structure(uploaded_file) -> bool:
    """Parse, store, and invalidate dependent state for an uploaded CIF file."""
    if uploaded_file is None:
        had_state = any(
            st.session_state.get(key) is not None
            for key in (ATOMS_STATE_KEY, RAW_ATOMS_STATE_KEY, CIF_BYTES_STATE_KEY, FILENAME_STATE_KEY, ERROR_STATE_KEY)
        )
        clear_structure_state()
        return had_state

    cif_bytes = uploaded_file.getvalue()
    if cif_bytes == st.session_state.get(CIF_BYTES_STATE_KEY):
        return False

    try:
        raw_atoms = parse_cif_bytes(cif_bytes)
    except Exception as exc:
        clear_structure_state()
        st.session_state[ERROR_STATE_KEY] = f"Failed to parse CIF file: {exc}"
        return True

    requested_mode = get_structure_mode()
    try:
        atoms, resolved_mode, structure_mode_message = resolve_atoms_for_structure_mode(raw_atoms, requested_mode)
    except Exception as exc:
        clear_structure_state()
        st.session_state[ERROR_STATE_KEY] = f"Failed to prepare the selected structure mode: {exc}"
        return True

    previous_pseudo_signature = st.session_state.get(PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY)
    new_structure_signature = get_structure_signature(atoms)
    had_resolved_data = has_resolved_pseudopotential_data()

    clear_generated_qe_input_state()
    if had_resolved_data and previous_pseudo_signature:
        if has_same_element_set(previous_pseudo_signature, new_structure_signature):
            mark_pseudopotentials_stale(
                "The uploaded structure changed. Re-resolve pseudopotentials before generating a runnable Quantum ESPRESSO input."
            )
        else:
            clear_pseudopotential_state(clear_structure_signature=False)
    elif st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY):
        clear_pseudopotential_state(clear_structure_signature=False)

    st.session_state[RAW_ATOMS_STATE_KEY] = raw_atoms
    st.session_state[ATOMS_STATE_KEY] = atoms
    st.session_state[CIF_BYTES_STATE_KEY] = cif_bytes
    st.session_state[FILENAME_STATE_KEY] = uploaded_file.name
    st.session_state[ERROR_STATE_KEY] = None
    st.session_state[STRUCTURE_MODE_STATE_KEY] = resolved_mode
    st.session_state[STRUCTURE_MODE_STATUS_STATE_KEY] = structure_mode_message
    st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = new_structure_signature
    return True


def initialize_job_summaries_state() -> None:
    """Ensure durable per-section job summaries exist."""
    default_summary = {
        "status": "idle",
        "job_label": None,
        "current_phase": None,
        "last_phase": None,
        "returncode": None,
        "log_paths": {},
        "artifacts": {},
        "tail_lines": [],
        "current_output_path": None,
    }
    summaries = st.session_state.setdefault(JOB_SUMMARIES_STATE_KEY, {})
    for owner in (JOB_OWNER_PW, JOB_OWNER_DOS, JOB_OWNER_BANDS):
        summaries.setdefault(owner, dict(default_summary))


def get_job_summary(owner: str) -> dict:
    """Return a copy of the stored job summary for one UI section."""
    initialize_job_summaries_state()
    return dict(st.session_state[JOB_SUMMARIES_STATE_KEY].get(owner, {}))


def update_job_summary(owner: str, **updates) -> None:
    """Merge new fields into the stored summary for one UI section."""
    initialize_job_summaries_state()
    summaries = st.session_state[JOB_SUMMARIES_STATE_KEY]
    current_summary = dict(summaries.get(owner, {}))
    current_summary.update(updates)
    summaries[owner] = current_summary


def get_active_job_owner() -> str | None:
    """Return the globally active QE job owner, if any."""
    if not st.session_state.get(RUN_ACTIVE_STATE_KEY):
        return None
    return st.session_state.get(RUN_JOB_OWNER_STATE_KEY)


def get_active_job_label() -> str | None:
    """Return the globally active QE job label, if any."""
    if not st.session_state.get(RUN_ACTIVE_STATE_KEY):
        return None
    return st.session_state.get(RUN_JOB_LABEL_STATE_KEY)


def sync_active_job_summary_from_run_state() -> None:
    """Mirror generic runner state into the durable section summary."""
    owner = st.session_state.get(RUN_JOB_OWNER_STATE_KEY)
    if not owner:
        return

    current_phase = st.session_state.get(RUN_PHASE_LABEL_STATE_KEY)
    returncode = st.session_state.get(RUN_RETURNCODE_STATE_KEY)
    summary_updates = {
        "job_label": st.session_state.get(RUN_JOB_LABEL_STATE_KEY),
        "current_phase": current_phase,
        "returncode": returncode,
        "tail_lines": list(st.session_state.get(RUN_TAIL_LINES_STATE_KEY, [])),
        "current_output_path": st.session_state.get(RUN_OUTPUT_PATH_STATE_KEY),
    }
    if current_phase:
        summary_updates["last_phase"] = current_phase

    if st.session_state.get(RUN_ACTIVE_STATE_KEY):
        summary_updates["status"] = "running"
    elif st.session_state.get(RUN_FINISHED_STATE_KEY):
        summary_updates["status"] = "success" if returncode == 0 else "error"

    update_job_summary(owner, **summary_updates)


def build_project_context(run_dir: Path = QE_RUNS_DIR) -> ProjectContext:
    """Build a lightweight app context from session state plus disk summaries."""
    initialize_run_state(st.session_state)
    initialize_job_summaries_state()
    sync_active_job_summary_from_run_state()

    run_dir = Path(run_dir)
    atoms = st.session_state.get(ATOMS_STATE_KEY)
    raw_atoms = st.session_state.get(RAW_ATOMS_STATE_KEY)
    analysis_files = find_available_analysis_files(run_dir)

    structure_summary = _build_structure_summary(atoms)
    pseudo_summary = _build_pseudopotential_summary()
    input_summary = _build_input_summary(atoms, run_dir)
    job_summary = _build_job_status_summary()
    artifact_summary = ArtifactSummary(
        has_any_artifact=analysis_files.has_any_artifact,
        dos_detected=analysis_files.dos_detected,
        bands_detected=analysis_files.bands_detected,
        metadata_detected=analysis_files.metadata_detected,
        dos_file=analysis_files.dos_file,
        band_file=analysis_files.band_file,
        band_input_file=analysis_files.band_input_file,
        pw_output_file=analysis_files.pw_output_file,
    )

    return ProjectContext(
        run_dir=run_dir,
        atoms=atoms,
        raw_atoms=raw_atoms,
        analysis_files=analysis_files,
        structure=structure_summary,
        pseudopotentials=pseudo_summary,
        input_files=input_summary,
        jobs=job_summary,
        artifacts=artifact_summary,
    )


def _build_structure_summary(atoms) -> StructureSummary:
    filename = st.session_state.get(FILENAME_STATE_KEY)
    cell_mode = get_structure_mode()
    return StructureSummary(
        loaded=atoms is not None,
        filename=filename,
        formula=atoms.get_chemical_formula() if atoms is not None else None,
        atom_count=len(atoms) if atoms is not None else 0,
        cell_mode=cell_mode,
        cell_mode_label="Primitive Cell" if cell_mode == "primitive" else "Uploaded Cell",
        mode_status=st.session_state.get(STRUCTURE_MODE_STATUS_STATE_KEY),
        cell_parameters=format_cell_parameters(atoms) if atoms is not None else None,
        error_message=st.session_state.get(ERROR_STATE_KEY),
    )


def _build_pseudopotential_summary() -> PseudopotentialSummary:
    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    rows = st.session_state.get(PSEUDO_RESOLVED_ROWS_STATE_KEY, []) or []
    state = status.get("state") or ("resolved" if rows else "unresolved")
    label_map = {
        "resolved": "Resolved",
        "stale": "Stale",
        "error": "Error",
        "unresolved": "Not resolved",
    }
    ready = state == "resolved" and has_resolved_pseudopotential_data()
    return PseudopotentialSummary(
        state=state,
        ready=ready,
        status_label=label_map.get(state, state.replace("_", " ").title()),
        message=status.get("message"),
        warning_count=len(status.get("warnings", [])),
        pseudo_dir=st.session_state.get(PSEUDO_DIR_STATE_KEY),
        resolved_count=len(rows),
    )


def _build_input_summary(atoms, run_dir: Path) -> InputSummary:
    ui_path = Path(st.session_state.get(QE_INPUT_UI_PATH_STATE_KEY, run_dir / "pw.in"))
    internal_path = Path(st.session_state.get(QE_INPUT_INTERNAL_PATH_STATE_KEY, run_dir / "espresso.pwi"))
    has_session_preview = bool(st.session_state.get(QE_INPUT_TEXT_STATE_KEY))
    ui_exists = ui_path.is_file()
    preview_source = "session" if has_session_preview else "disk" if ui_exists else "none"
    available = has_session_preview or ui_exists
    blocker_message = _get_qe_generation_blocker_message(atoms)
    if available:
        status_label = "Generated"
    elif blocker_message:
        status_label = "Blocked"
    else:
        status_label = "Not generated"
    return InputSummary(
        available=available,
        status_label=status_label,
        blocker_message=blocker_message,
        preview_source=preview_source,
        ui_path=ui_path,
        internal_path=internal_path,
        has_preview=available,
    )


def _build_job_status_summary() -> JobStatusSummary:
    active_owner = get_active_job_owner()
    active_label = get_active_job_label()
    active_phase = st.session_state.get(RUN_PHASE_LABEL_STATE_KEY)
    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))
    run_finished = bool(st.session_state.get(RUN_FINISHED_STATE_KEY))
    returncode = st.session_state.get(RUN_RETURNCODE_STATE_KEY)

    if run_active and active_label:
        latest_status_label = "Running"
        latest_status_detail = active_label if not active_phase else f"{active_label} ({active_phase})"
    elif run_finished:
        latest_status_label = "Succeeded" if returncode == 0 else "Failed"
        latest_status_detail = active_label
    else:
        latest_status_label = "Idle"
        latest_status_detail = None

    return JobStatusSummary(
        active_owner=active_owner,
        active_label=active_label,
        active_phase=active_phase,
        run_active=run_active,
        run_finished=run_finished,
        latest_status_label=latest_status_label,
        latest_status_detail=latest_status_detail,
    )


def _get_qe_generation_blocker_message(atoms) -> str | None:
    if atoms is None:
        return "Upload a CIF file before generating a Quantum ESPRESSO input file."

    if not has_resolved_pseudopotential_data():
        return "Resolve pseudopotentials before generating a runnable Quantum ESPRESSO input file."

    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    if status.get("state") == "stale":
        return status.get(
            "message",
            "Pseudopotentials are stale. Re-resolve them before generating a runnable Quantum ESPRESSO input file.",
        )
    if status.get("state") == "error":
        return status.get("message", "Pseudopotentials are not ready for runnable Quantum ESPRESSO input generation.")
    if status.get("state") != "resolved":
        return "Resolve pseudopotentials before generating a runnable Quantum ESPRESSO input file."

    return None
