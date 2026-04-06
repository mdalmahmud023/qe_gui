from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from electronic_properties import AnalysisFiles, find_available_analysis_files
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
)
from structure_state import (
    ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY,
    ATOMS_STATE_KEY,
    AUTO_BAND_PATH_POINTS_WIDGET_KEY,
    AUTO_BAND_PATH_STATUS_STATE_KEY,
    BAND_ALIGNMENT_MODE_WIDGET_KEY,
    BAND_COORDINATE_MODE_WIDGET_KEY,
    BAND_NBND_WIDGET_KEY,
    BAND_PATH_WIDGET_KEY,
    CIF_BYTES_STATE_KEY,
    DEFAULT_PSEUDO_SETTINGS,
    DOS_ALIGNMENT_WIDGET_KEY,
    ERROR_STATE_KEY,
    FILENAME_STATE_KEY,
    PSEUDO_DIR_STATE_KEY,
    PSEUDO_FUNCTIONAL_OPTIONS,
    PSEUDO_FUNCTIONAL_WIDGET_KEY,
    PSEUDO_MODE_OPTIONS,
    PSEUDO_MODE_WIDGET_KEY,
    PSEUDO_RESOLVED_MAP_STATE_KEY,
    PSEUDO_RESOLVED_ROWS_STATE_KEY,
    PSEUDO_RESOLUTION_STATUS_STATE_KEY,
    PSEUDO_SETTINGS_STATE_KEY,
    PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY,
    PSEUDO_USE_CACHE_WIDGET_KEY,
    PSEUDO_VERSION_OPTIONS,
    PSEUDO_VERSION_WIDGET_KEY,
    PROVENANCE_RECORD_BANDS,
    PROVENANCE_RECORD_BASE_RUN,
    PROVENANCE_RECORD_DOS,
    PROVENANCE_RECORD_INPUT,
    QE_INPUT_INTERNAL_PATH_STATE_KEY,
    QE_INPUT_TEXT_STATE_KEY,
    QE_INPUT_UI_PATH_STATE_KEY,
    RAW_ATOMS_STATE_KEY,
    SOURCE_FORMAT_STATE_KEY,
    SOURCE_KIND_STATE_KEY,
    SOURCE_LABEL_STATE_KEY,
    STRUCTURE_MODE_OPTIONS,
    STRUCTURE_MODE_STATE_KEY,
    STRUCTURE_MODE_STATUS_STATE_KEY,
    apply_structure_mode_selection,
    clear_generated_qe_input_state,
    clear_pseudopotential_state,
    clear_structure_state,
    commit_structure_source,
    compare_current_structure_to_record,
    get_current_active_structure_signature,
    get_current_pseudo_settings,
    get_current_raw_structure_signature,
    get_structure_mode,
    has_resolved_pseudopotential_data,
    load_provenance,
    mark_pseudopotentials_stale,
    normalize_structure_mode,
    record_generated_input_provenance,
    record_workflow_provenance,
    set_resolution_status,
    set_structure_error,
    sync_pseudopotential_settings_state,
)


QE_PARAMS_STATE_KEY = "qe_parameters"
QE_CALCULATION_OPTIONS = ("scf", "nscf", "relax", "vc-relax")
QE_SMEARING_OPTIONS = ("gaussian", "methfessel-paxton", "marzari-vanderbilt")

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


@dataclass(frozen=True)
class StructureSummary:
    loaded: bool
    filename: str | None
    source_kind: str | None
    source_kind_label: str | None
    source_label: str | None
    source_format: str | None
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
    provenance_state: str
    provenance_message: str | None


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
    provenance_state: str
    provenance_message: str | None


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


def format_cell_parameters(atoms) -> str:
    """Format basic cell parameters for structure display."""
    a, b, c, alpha, beta, gamma = atoms.cell.cellpar()
    return (
        f"a = {a:.3f} A\n"
        f"b = {b:.3f} A\n"
        f"c = {c:.3f} A\n"
        f"alpha = {alpha:.2f} deg\n"
        f"beta = {beta:.2f} deg\n"
        f"gamma = {gamma:.2f} deg"
    )


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
    current_signature = get_current_active_structure_signature()
    provenance = load_provenance(run_dir)
    analysis_files = find_available_analysis_files(run_dir)

    structure_summary = _build_structure_summary(atoms)
    pseudo_summary = _build_pseudopotential_summary()
    input_summary = _build_input_summary(atoms, run_dir, current_signature, provenance)
    job_summary = _build_job_status_summary()
    artifact_summary = _build_artifact_summary(analysis_files, current_signature, provenance)

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
    source_kind = st.session_state.get(SOURCE_KIND_STATE_KEY)
    source_kind_label = {
        "file": "Imported File",
        "manual": "Manual Builder",
        "prototype": "Prototype Builder",
    }.get(source_kind)
    cell_mode = get_structure_mode()
    return StructureSummary(
        loaded=atoms is not None,
        filename=st.session_state.get(FILENAME_STATE_KEY),
        source_kind=source_kind,
        source_kind_label=source_kind_label,
        source_label=st.session_state.get(SOURCE_LABEL_STATE_KEY),
        source_format=st.session_state.get(SOURCE_FORMAT_STATE_KEY),
        formula=atoms.get_chemical_formula() if atoms is not None else None,
        atom_count=len(atoms) if atoms is not None else 0,
        cell_mode=cell_mode,
        cell_mode_label="Primitive Cell" if cell_mode == "primitive" else "Source Cell",
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


def _build_input_summary(
    atoms,
    run_dir: Path,
    current_signature: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> InputSummary:
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

    provenance_state, provenance_message = _resolve_provenance_status(
        current_signature,
        provenance.get("records", {}).get(PROVENANCE_RECORD_INPUT),
        subject_label="QE input files",
        record_available=available,
    )

    return InputSummary(
        available=available,
        status_label=status_label,
        blocker_message=blocker_message,
        preview_source=preview_source,
        ui_path=ui_path,
        internal_path=internal_path,
        has_preview=available,
        provenance_state=provenance_state,
        provenance_message=provenance_message,
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


def _build_artifact_summary(
    analysis_files: AnalysisFiles,
    current_signature: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> ArtifactSummary:
    comparisons: list[tuple[str, str | None]] = []
    records = provenance.get("records", {})

    if analysis_files.pw_output_file is not None:
        comparisons.append(
            _resolve_provenance_status(
                current_signature,
                records.get(PROVENANCE_RECORD_BASE_RUN),
                subject_label="base-run artifacts",
                record_available=True,
            )
        )
    if analysis_files.dos_detected:
        comparisons.append(
            _resolve_provenance_status(
                current_signature,
                records.get(PROVENANCE_RECORD_DOS),
                subject_label="DOS artifacts",
                record_available=True,
            )
        )
    if analysis_files.bands_detected:
        comparisons.append(
            _resolve_provenance_status(
                current_signature,
                records.get(PROVENANCE_RECORD_BANDS),
                subject_label="band-structure artifacts",
                record_available=True,
            )
        )

    provenance_state = "unknown"
    provenance_message = None
    if comparisons:
        states = [state for state, _message in comparisons]
        messages = [message for _state, message in comparisons if message]
        if any(state == "out_of_sync" for state in states):
            provenance_state = "out_of_sync"
            provenance_message = messages[0] if messages else (
                "One or more saved artifacts belong to a different active structure."
            )
        elif all(state == "in_sync" for state in states):
            provenance_state = "in_sync"
        elif messages:
            provenance_state = "unknown"
            provenance_message = messages[0]

    return ArtifactSummary(
        has_any_artifact=analysis_files.has_any_artifact,
        dos_detected=analysis_files.dos_detected,
        bands_detected=analysis_files.bands_detected,
        metadata_detected=analysis_files.metadata_detected,
        dos_file=analysis_files.dos_file,
        band_file=analysis_files.band_file,
        band_input_file=analysis_files.band_input_file,
        pw_output_file=analysis_files.pw_output_file,
        provenance_state=provenance_state,
        provenance_message=provenance_message,
    )


def _resolve_provenance_status(
    current_signature: dict[str, Any] | None,
    record: dict[str, Any] | None,
    *,
    subject_label: str,
    record_available: bool,
) -> tuple[str, str | None]:
    if not record_available or current_signature is None:
        return "unknown", None
    if not record or not isinstance(record.get("structure_signature"), dict):
        return (
            "unknown",
            f"Saved {subject_label} exist, but their provenance is unavailable, so QE Studio cannot confirm they match the current structure.",
        )
    comparison = compare_current_structure_to_record(
        current_signature,
        record.get("structure_signature"),
        subject_label=subject_label,
    )
    return comparison.state, comparison.message


def _get_qe_generation_blocker_message(atoms) -> str | None:
    if atoms is None:
        return "Load or build a structure before generating a Quantum ESPRESSO input file."

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
