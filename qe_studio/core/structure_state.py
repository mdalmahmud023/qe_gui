from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import streamlit as st

from .state_keys import (
    ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY,
    ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS,
    ATOMIC_POSITIONS_COORDINATE_MODE_STATE_KEY,
    ATOMS_STATE_KEY,
    CUTOFF_STUDY_LATEST_PATH_STATE_KEY,
    CUTOFF_STUDY_PARAMS_STATE_KEY,
    CUTOFF_STUDY_SELECTED_PATH_STATE_KEY,
    AUTO_BAND_PATH_STATUS_STATE_KEY,
    DEFAULT_PSEUDO_SETTINGS,
    DEFAULT_ATOMIC_POSITIONS_COORDINATE_MODE,
    ERROR_STATE_KEY,
    FILENAME_STATE_KEY,
    PSEUDO_DIR_STATE_KEY,
    PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY,
    PSEUDO_FUNCTIONAL_WIDGET_KEY,
    PSEUDO_MODE_WIDGET_KEY,
    PSEUDO_RESOLVED_MAP_STATE_KEY,
    PSEUDO_RESOLVED_ROWS_STATE_KEY,
    PSEUDO_RESOLUTION_STATUS_STATE_KEY,
    PSEUDO_SETTINGS_STATE_KEY,
    PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY,
    PSEUDO_VERSION_WIDGET_KEY,
    QE_INPUT_INTERNAL_PATH_STATE_KEY,
    QE_INPUT_TEXT_STATE_KEY,
    QE_INPUT_UI_PATH_STATE_KEY,
    RAW_ATOMS_STATE_KEY,
    RAW_STRUCTURE_SIGNATURE_STATE_KEY,
    SOURCE_DETAILS_STATE_KEY,
    SOURCE_FORMAT_STATE_KEY,
    SOURCE_KIND_STATE_KEY,
    SOURCE_LABEL_STATE_KEY,
    STRUCTURE_FILE_BYTES_STATE_KEY,
    STRUCTURE_MODE_OPTIONS,
    STRUCTURE_MODE_STATE_KEY,
    STRUCTURE_MODE_STATUS_STATE_KEY,
)
from .workspace import (
    PROVENANCE_RECORD_BANDS,
    PROVENANCE_RECORD_BASE_RUN,
    PROVENANCE_RECORD_DOS,
    PROVENANCE_RECORD_INPUT,
    QE_STUDIO_PROVENANCE_FILENAME,
    QE_STUDIO_PROVENANCE_VERSION,
)
from ..services.qe_runner import reset_run_state
from ..services.structure_tools import make_primitive_atoms

STRUCTURE_SIGNATURE_VERSION = 1
STRUCTURE_SIGNATURE_DIGITS = 8
STRUCTURE_SIGNATURE_TOLERANCE = 1.0e-8

QE_STUDIO_PROVENANCE_FILENAME = "qe_studio_provenance.json"
QE_STUDIO_PROVENANCE_VERSION = 1
PROVENANCE_RECORD_INPUT = "pw_input"
PROVENANCE_RECORD_BASE_RUN = "pw_run"
PROVENANCE_RECORD_DOS = "dos_workflow"
PROVENANCE_RECORD_BANDS = "bands_workflow"


@dataclass(frozen=True)
class ProvenanceComparison:
    state: str
    message: str | None


def normalize_structure_mode(mode: str | None) -> str:
    """Normalize invalid cell-mode values onto supported options."""
    normalized = (mode or "").strip().lower()
    return normalized if normalized in STRUCTURE_MODE_OPTIONS else "source"


def get_structure_mode() -> str:
    """Return the current structure cell-mode selection."""
    return normalize_structure_mode(st.session_state.get(STRUCTURE_MODE_STATE_KEY, "source"))


def normalize_atomic_positions_coordinate_mode(mode: str | None) -> str:
    """Normalize invalid QE atomic-position coordinate modes onto supported options."""
    normalized = (mode or "").strip().lower()
    if normalized in ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS:
        return normalized
    return DEFAULT_ATOMIC_POSITIONS_COORDINATE_MODE


def get_atomic_positions_coordinate_mode() -> str:
    """Return the current ATOMIC_POSITIONS coordinate-system selection."""
    return normalize_atomic_positions_coordinate_mode(
        st.session_state.get(
            ATOMIC_POSITIONS_COORDINATE_MODE_STATE_KEY,
            DEFAULT_ATOMIC_POSITIONS_COORDINATE_MODE,
        )
    )


def set_atomic_positions_coordinate_mode(mode: str | None) -> str:
    """Persist and return the normalized ATOMIC_POSITIONS coordinate-system selection."""
    normalized = normalize_atomic_positions_coordinate_mode(mode)
    st.session_state[ATOMIC_POSITIONS_COORDINATE_MODE_STATE_KEY] = normalized
    return normalized


def get_unique_elements(atoms) -> list[str]:
    """Return unique chemical symbols in deterministic sorted order."""
    return sorted(set(atoms.get_chemical_symbols()))


def _normalize_decimal(value: float) -> float:
    normalized = float(value)
    if abs(normalized) < STRUCTURE_SIGNATURE_TOLERANCE:
        normalized = 0.0
    return round(normalized, STRUCTURE_SIGNATURE_DIGITS)


def _normalize_fractional_coordinate(value: float) -> float:
    normalized = float(value) % 1.0
    if abs(normalized) < STRUCTURE_SIGNATURE_TOLERANCE or abs(normalized - 1.0) < STRUCTURE_SIGNATURE_TOLERANCE:
        normalized = 0.0
    return round(normalized, STRUCTURE_SIGNATURE_DIGITS)


def _build_structure_signature_payload(atoms) -> dict[str, Any]:
    cell_matrix = [
        [_normalize_decimal(component) for component in row]
        for row in atoms.cell.array.tolist()
    ]
    wrapped_scaled_positions = atoms.get_scaled_positions(wrap=True)
    site_rows = [
        [
            symbol,
            _normalize_fractional_coordinate(position[0]),
            _normalize_fractional_coordinate(position[1]),
            _normalize_fractional_coordinate(position[2]),
        ]
        for symbol, position in zip(atoms.get_chemical_symbols(), wrapped_scaled_positions, strict=False)
    ]
    site_rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return {
        "version": STRUCTURE_SIGNATURE_VERSION,
        "atom_count": len(atoms),
        "cell": cell_matrix,
        "sites": site_rows,
    }


def get_structure_signature(atoms) -> dict[str, Any]:
    """Return a deterministic structure signature for provenance and invalidation."""
    payload = _build_structure_signature_payload(atoms)
    serialized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "version": STRUCTURE_SIGNATURE_VERSION,
        "hash": hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest(),
        "elements": get_unique_elements(atoms),
        "formula": atoms.get_chemical_formula(),
        "atom_count": len(atoms),
    }


def structure_signatures_match(
    first_signature: dict[str, Any] | None,
    second_signature: dict[str, Any] | None,
) -> bool:
    """Return whether two structure signatures represent the same active structure."""
    if not first_signature or not second_signature:
        return False
    return (
        int(first_signature.get("version", -1)) == int(second_signature.get("version", -2))
        and str(first_signature.get("hash", "")) == str(second_signature.get("hash", "__missing__"))
    )


def has_same_element_set(
    first_signature: dict[str, Any] | None,
    second_signature: dict[str, Any] | None,
) -> bool:
    """Return whether two structure signatures contain the same element set."""
    if not first_signature or not second_signature:
        return False
    return list(first_signature.get("elements", [])) == list(second_signature.get("elements", []))


def get_current_raw_structure_signature() -> dict[str, Any] | None:
    """Return the stored raw source-cell signature."""
    return st.session_state.get(RAW_STRUCTURE_SIGNATURE_STATE_KEY)


def get_current_active_structure_signature() -> dict[str, Any] | None:
    """Return the stored active structure signature."""
    return st.session_state.get(ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY)


def get_current_source_metadata() -> dict[str, Any]:
    """Return a compact metadata summary for the active structure source."""
    return {
        "source_kind": st.session_state.get(SOURCE_KIND_STATE_KEY),
        "source_label": st.session_state.get(SOURCE_LABEL_STATE_KEY),
        "source_format": st.session_state.get(SOURCE_FORMAT_STATE_KEY),
        "source_details": dict(st.session_state.get(SOURCE_DETAILS_STATE_KEY) or {}),
    }


def clear_generated_qe_input_state() -> None:
    """Remove generated QE input preview state."""
    for key in (
        QE_INPUT_TEXT_STATE_KEY,
        QE_INPUT_INTERNAL_PATH_STATE_KEY,
        QE_INPUT_UI_PATH_STATE_KEY,
        CUTOFF_STUDY_PARAMS_STATE_KEY,
        CUTOFF_STUDY_SELECTED_PATH_STATE_KEY,
        CUTOFF_STUDY_LATEST_PATH_STATE_KEY,
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
        "pseudo_resolved_entries",  # raw SSSP manifest data used for cutoff hints
    ):
        st.session_state.pop(key, None)
    if clear_structure_signature:
        st.session_state.pop(PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY, None)


def clear_structure_state() -> None:
    """Remove stored structure and dependent session state."""
    clear_pseudopotential_state(clear_structure_signature=True)
    for key in (
        ATOMIC_POSITIONS_COORDINATE_MODE_STATE_KEY,
        ATOMS_STATE_KEY,
        RAW_ATOMS_STATE_KEY,
        STRUCTURE_FILE_BYTES_STATE_KEY,
        FILENAME_STATE_KEY,
        SOURCE_KIND_STATE_KEY,
        SOURCE_LABEL_STATE_KEY,
        SOURCE_FORMAT_STATE_KEY,
        SOURCE_DETAILS_STATE_KEY,
        RAW_STRUCTURE_SIGNATURE_STATE_KEY,
        ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY,
        ERROR_STATE_KEY,
        STRUCTURE_MODE_STATE_KEY,
        STRUCTURE_MODE_STATUS_STATE_KEY,
    ):
        st.session_state.pop(key, None)
    set_atomic_positions_coordinate_mode(DEFAULT_ATOMIC_POSITIONS_COORDINATE_MODE)


def set_structure_error(message: str | None) -> None:
    """Persist the latest structure-page error message."""
    st.session_state[ERROR_STATE_KEY] = message


def has_resolved_pseudopotential_data() -> bool:
    """Return whether resolved pseudopotential data is present in session state."""
    return bool(
        st.session_state.get(PSEUDO_DIR_STATE_KEY)
        and st.session_state.get(PSEUDO_RESOLVED_MAP_STATE_KEY)
        and st.session_state.get(PSEUDO_RESOLVED_ROWS_STATE_KEY)
    )


def get_qe_generation_blocker_message(atoms) -> str | None:
    """Return the current reason QE input generation is blocked, if any."""
    if atoms is None:
        return "Load or build a structure before generating a Quantum ESPRESSO input file."

    if not has_resolved_pseudopotential_data():
        return "Resolve pseudopotentials before generating a runnable Quantum ESPRESSO input file."

    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    if status.get("state") == "stale":
        _resolve_pseudopotential_state_for_active_structure(get_structure_signature(atoms))
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
    current_status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    set_resolution_status(
        state="stale",
        message=message,
        warnings=list(current_status.get("warnings", [])),
        resolved_settings=current_status.get("resolved_settings"),
        resolved_structure_signature=current_status.get("resolved_structure_signature"),
    )


def _get_mismatched_pseudopotential_setting_keys(
    *,
    current_settings: dict[str, Any] | None,
    resolved_settings: dict[str, Any] | None,
) -> list[str]:
    """Return selector fields whose current values differ from the resolved pseudo settings."""
    if not resolved_settings:
        return []

    resolved_settings = dict(resolved_settings)
    current_settings = dict(current_settings or {})
    fully_relativistic_current = bool(current_settings.get("fully_relativistic", False))
    fully_relativistic_resolved = bool(resolved_settings.get("fully_relativistic", False))
    keys_to_compare = ["functional", "fully_relativistic"]
    if not fully_relativistic_current and not fully_relativistic_resolved:
        keys_to_compare.extend(["mode", "version"])

    def _resolved_value_for_comparison(key: str):
        if key == "fully_relativistic":
            return bool(resolved_settings.get(key, False))
        return resolved_settings.get(key)

    return [
        key
        for key in keys_to_compare
        if current_settings.get(key) != _resolved_value_for_comparison(key)
    ]


def get_current_pseudo_settings() -> dict:
    """Return the active pseudopotential selector values."""
    return {
        "functional": st.session_state.get(PSEUDO_FUNCTIONAL_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["functional"]),
        "mode": st.session_state.get(PSEUDO_MODE_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["mode"]),
        "version": st.session_state.get(PSEUDO_VERSION_WIDGET_KEY, DEFAULT_PSEUDO_SETTINGS["version"]),
        "fully_relativistic": bool(
            st.session_state.get(
                PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY,
                DEFAULT_PSEUDO_SETTINGS["fully_relativistic"],
            )
        ),
    }


def sync_pseudopotential_settings_state() -> None:
    """Persist current widget selections and mark stale on supported setting changes."""
    current_settings = get_current_pseudo_settings()
    st.session_state[PSEUDO_SETTINGS_STATE_KEY] = current_settings

    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY)
    if not status or status.get("state") != "resolved" or not has_resolved_pseudopotential_data():
        return

    resolved_settings = status.get("resolved_settings") or {}
    mismatched_keys = _get_mismatched_pseudopotential_setting_keys(
        current_settings=current_settings,
        resolved_settings=resolved_settings,
    )
    if mismatched_keys:
        mismatch_summary = ", ".join(
            f"{key}={current_settings[key]!r} "
            f"(resolved {bool(resolved_settings.get(key, False))!r})"
            if key == "fully_relativistic"
            else f"{key}={current_settings[key]!r} (resolved {resolved_settings.get(key)!r})"
            for key in mismatched_keys
        )
        mark_pseudopotentials_stale(
            "Pseudopotential settings changed. Re-resolve pseudopotentials before generating a runnable Quantum ESPRESSO input. "
            f"Mismatched settings: {mismatch_summary}."
        )


def resolve_atoms_for_structure_mode(raw_atoms, mode: str) -> tuple[object, str, str]:
    """Return the active atoms object for the selected structure mode."""
    if raw_atoms is None:
        raise ValueError("No structure is loaded.")

    normalized_mode = normalize_structure_mode(mode)
    if normalized_mode != "primitive":
        return raw_atoms.copy(), "source", "Using the source cell."

    try:
        primitive_result = make_primitive_atoms(raw_atoms)
    except Exception as exc:
        return raw_atoms.copy(), "source", f"Primitive-cell reduction failed; using the source cell instead: {exc}"
    return primitive_result.atoms, "primitive", primitive_result.message


def _resolve_pseudopotential_state_for_active_structure(active_signature: dict[str, Any]) -> None:
    resolved_pseudo_signature = st.session_state.get(PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY)
    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY, {})
    had_resolved_data = has_resolved_pseudopotential_data()
    resolved_settings = status.get("resolved_settings")
    mismatched_settings = _get_mismatched_pseudopotential_setting_keys(
        current_settings=get_current_pseudo_settings(),
        resolved_settings=resolved_settings,
    )

    if had_resolved_data and resolved_pseudo_signature:
        if structure_signatures_match(resolved_pseudo_signature, active_signature):
            if status.get("state") == "error":
                set_resolution_status(
                    state="error",
                    message=status.get("message", "Resolved pseudopotentials are not ready."),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=resolved_pseudo_signature,
                )
            elif status.get("state") == "stale" and mismatched_settings:
                set_resolution_status(
                    state="stale",
                    message=status.get(
                        "message",
                        "Pseudopotential settings changed. Re-resolve pseudopotentials before generating a runnable Quantum ESPRESSO input.",
                    ),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=resolved_pseudo_signature,
                )
            else:
                st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = active_signature
                set_resolution_status(
                    state="resolved",
                    message=status.get("message", "Resolved pseudopotentials are available."),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=active_signature,
                )
        elif has_same_element_set(resolved_pseudo_signature, active_signature):
            if status.get("state") == "error":
                set_resolution_status(
                    state="error",
                    message=status.get("message", "Resolved pseudopotentials are not ready."),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=resolved_pseudo_signature,
                )
            elif status.get("state") == "stale" and mismatched_settings:
                set_resolution_status(
                    state="stale",
                    message=status.get(
                        "message",
                        "Pseudopotential settings changed. Re-resolve pseudopotentials before generating a runnable Quantum ESPRESSO input.",
                    ),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=resolved_pseudo_signature,
                )
            else:
                st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = active_signature
                set_resolution_status(
                    state="resolved",
                    message=(
                        "Resolved pseudopotentials remain valid because the active structure keeps the same element set."
                    ),
                    warnings=list(status.get("warnings", [])),
                    resolved_settings=resolved_settings,
                    resolved_structure_signature=active_signature,
                )
        else:
            clear_pseudopotential_state(clear_structure_signature=True)
    elif st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY):
        clear_pseudopotential_state(clear_structure_signature=False)


def _store_active_structure(
    raw_atoms,
    active_atoms,
    *,
    source_kind: str,
    source_label: str | None,
    source_format: str | None,
    source_details: dict[str, Any] | None,
    structure_mode: str,
    structure_mode_message: str,
    payload_bytes: bytes | None = None,
) -> None:
    raw_signature = get_structure_signature(raw_atoms)
    active_signature = get_structure_signature(active_atoms)

    clear_generated_qe_input_state()
    reset_run_state(st.session_state)
    _resolve_pseudopotential_state_for_active_structure(active_signature)

    st.session_state[RAW_ATOMS_STATE_KEY] = raw_atoms
    st.session_state[ATOMS_STATE_KEY] = active_atoms
    st.session_state[SOURCE_KIND_STATE_KEY] = source_kind
    st.session_state[SOURCE_LABEL_STATE_KEY] = source_label
    st.session_state[SOURCE_FORMAT_STATE_KEY] = source_format
    st.session_state[SOURCE_DETAILS_STATE_KEY] = dict(source_details or {})
    st.session_state[RAW_STRUCTURE_SIGNATURE_STATE_KEY] = raw_signature
    st.session_state[ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY] = active_signature
    st.session_state[FILENAME_STATE_KEY] = source_label if source_kind == "file" else None
    st.session_state[ERROR_STATE_KEY] = None
    st.session_state[STRUCTURE_MODE_STATE_KEY] = structure_mode
    st.session_state[STRUCTURE_MODE_STATUS_STATE_KEY] = structure_mode_message
    if payload_bytes is not None:
        st.session_state[STRUCTURE_FILE_BYTES_STATE_KEY] = payload_bytes
    else:
        st.session_state.pop(STRUCTURE_FILE_BYTES_STATE_KEY, None)


def commit_structure_source(
    raw_atoms,
    *,
    source_kind: str,
    source_label: str | None,
    source_format: str | None = None,
    source_details: dict[str, Any] | None = None,
    requested_mode: str | None = None,
    payload_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Commit a newly imported or built source structure into session state."""
    if raw_atoms is None:
        raise ValueError("No structure was supplied.")

    requested_cell_mode = normalize_structure_mode(requested_mode or get_structure_mode())
    raw_atoms_copy = raw_atoms.copy()
    active_atoms, resolved_mode, structure_mode_message = resolve_atoms_for_structure_mode(raw_atoms_copy, requested_cell_mode)
    _store_active_structure(
        raw_atoms_copy,
        active_atoms,
        source_kind=source_kind,
        source_label=source_label,
        source_format=source_format,
        source_details=source_details,
        structure_mode=resolved_mode,
        structure_mode_message=structure_mode_message,
        payload_bytes=payload_bytes,
    )
    set_atomic_positions_coordinate_mode(DEFAULT_ATOMIC_POSITIONS_COORDINATE_MODE)
    return {
        "raw_signature": st.session_state.get(RAW_STRUCTURE_SIGNATURE_STATE_KEY),
        "active_signature": st.session_state.get(ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY),
    }


def apply_structure_mode_selection(mode: str) -> None:
    """Apply a new active structure mode using the stored raw source structure."""
    raw_atoms = st.session_state.get(RAW_ATOMS_STATE_KEY)
    if raw_atoms is None:
        return

    active_atoms, resolved_mode, status_message = resolve_atoms_for_structure_mode(raw_atoms, mode)
    _store_active_structure(
        raw_atoms.copy(),
        active_atoms,
        source_kind=st.session_state.get(SOURCE_KIND_STATE_KEY, "file"),
        source_label=st.session_state.get(SOURCE_LABEL_STATE_KEY),
        source_format=st.session_state.get(SOURCE_FORMAT_STATE_KEY),
        source_details=dict(st.session_state.get(SOURCE_DETAILS_STATE_KEY) or {}),
        structure_mode=resolved_mode,
        structure_mode_message=status_message,
        payload_bytes=st.session_state.get(STRUCTURE_FILE_BYTES_STATE_KEY),
    )


def get_provenance_path(run_dir: Path) -> Path:
    """Return the QE Studio provenance file path for a run directory."""
    return Path(run_dir) / QE_STUDIO_PROVENANCE_FILENAME


def load_provenance(run_dir: Path) -> dict[str, Any]:
    """Load QE Studio provenance metadata from disk."""
    provenance_path = get_provenance_path(run_dir)
    if not provenance_path.is_file():
        return {"version": QE_STUDIO_PROVENANCE_VERSION, "records": {}}
    try:
        data = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": QE_STUDIO_PROVENANCE_VERSION, "records": {}}
    if not isinstance(data, dict):
        return {"version": QE_STUDIO_PROVENANCE_VERSION, "records": {}}
    records = data.get("records")
    if not isinstance(records, dict):
        records = {}
    return {
        "version": int(data.get("version", QE_STUDIO_PROVENANCE_VERSION)),
        "records": records,
    }


def save_provenance(run_dir: Path, provenance: dict[str, Any]) -> Path:
    """Persist QE Studio provenance metadata to disk."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    provenance_path = get_provenance_path(run_dir)
    payload = {
        "version": QE_STUDIO_PROVENANCE_VERSION,
        "records": dict(provenance.get("records", {})),
    }
    provenance_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return provenance_path


def get_provenance_record(run_dir: Path, record_key: str) -> dict[str, Any] | None:
    """Return one stored provenance record, if available."""
    return load_provenance(run_dir).get("records", {}).get(record_key)


def update_provenance_record(
    run_dir: Path,
    record_key: str,
    *,
    structure_signature: dict[str, Any] | None,
    source_kind: str | None = None,
    source_label: str | None = None,
    source_format: str | None = None,
    paths: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Store or replace one provenance record in the shared run-directory metadata."""
    provenance = load_provenance(run_dir)
    records = dict(provenance.get("records", {}))
    records[record_key] = {
        "record_key": record_key,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "structure_signature": structure_signature,
        "source_kind": source_kind,
        "source_label": source_label,
        "source_format": source_format,
        "paths": dict(paths or {}),
        "extra": dict(extra or {}),
    }
    provenance["records"] = records
    return save_provenance(run_dir, provenance)


def get_input_structure_signature(run_dir: Path) -> dict[str, Any] | None:
    """Return the stored structure signature for the generated `pw.in`, if available."""
    record = get_provenance_record(run_dir, PROVENANCE_RECORD_INPUT)
    if not record:
        return None
    signature = record.get("structure_signature")
    return signature if isinstance(signature, dict) else None


def record_generated_input_provenance(
    run_dir: Path,
    *,
    ui_path: str,
    internal_path: str,
    metadata_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist provenance for the generated QE base input files."""
    signature = get_current_active_structure_signature()
    source_metadata = get_current_source_metadata()
    extra_metadata = dict(extra or {})
    extra_metadata["atomic_positions_coordinate_mode"] = get_atomic_positions_coordinate_mode()
    path_map = {
        "ui_path": str(ui_path),
        "internal_path": str(internal_path),
    }
    if metadata_path is not None:
        path_map["metadata_path"] = str(metadata_path)
    return update_provenance_record(
        run_dir,
        PROVENANCE_RECORD_INPUT,
        structure_signature=signature,
        source_kind=source_metadata.get("source_kind"),
        source_label=source_metadata.get("source_label"),
        source_format=source_metadata.get("source_format"),
        paths=path_map,
        extra=extra_metadata,
    )


def record_workflow_provenance(
    run_dir: Path,
    record_key: str,
    *,
    paths: dict[str, str],
    structure_signature: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist provenance for one QE workflow or artifact set."""
    signature = structure_signature or get_input_structure_signature(run_dir) or get_current_active_structure_signature()
    source_metadata = get_current_source_metadata()
    return update_provenance_record(
        run_dir,
        record_key,
        structure_signature=signature,
        source_kind=source_metadata.get("source_kind"),
        source_label=source_metadata.get("source_label"),
        source_format=source_metadata.get("source_format"),
        paths=paths,
        extra=extra,
    )


def compare_current_structure_to_record(
    current_signature: dict[str, Any] | None,
    record_signature: dict[str, Any] | None,
    *,
    subject_label: str,
) -> ProvenanceComparison:
    """Compare the current active structure to a stored provenance record."""
    if current_signature is None or record_signature is None:
        return ProvenanceComparison(state="unknown", message=None)
    if structure_signatures_match(current_signature, record_signature):
        return ProvenanceComparison(state="in_sync", message=None)
    return ProvenanceComparison(
        state="out_of_sync",
        message=(
            f"The saved {subject_label} were generated for a different active structure. "
            "Regenerate or rerun them for the current structure."
        ),
    )
