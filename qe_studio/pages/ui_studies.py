from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, MutableMapping

import streamlit as st

from ..convergence import (
    CUTOFF_RATIO_RULE_OPTIONS,
    CUTOFF_STUDY_MANIFEST_FILENAME,
    CUTOFF_STUDY_SUMMARY_FILENAME,
    RATIO_RULE_CUSTOM,
    CutoffSweepSpec,
    EcutrhoMode,
    build_cutoff_study,
    build_cutoff_sweep_warnings,
    discover_cutoff_studies,
    inspect_metadata_dual_support,
    load_cutoff_study_manifest,
    load_cutoff_study_summary,
    refresh_cutoff_study,
    resolve_point_ecutrho,
    DimensionalityMode,
    KPOINT_STUDY_MANIFEST_FILENAME,
    KPOINT_STUDY_SUMMARY_FILENAME,
    KPointSweepSpec,
    build_kpoint_study,
    discover_kpoint_studies,
    load_kpoint_study_manifest,
    load_kpoint_study_summary,
    refresh_kpoint_study,
)
from ..core.project_context import ProjectContext
from ..core.state_keys import (
    ATOMS_STATE_KEY,
    CUTOFF_STUDY_LATEST_PATH_STATE_KEY,
    CUTOFF_STUDY_PARAMS_STATE_KEY,
    CUTOFF_STUDY_SELECTED_PATH_STATE_KEY,
    EOS_DISPLAY_MODE_WIDGET_KEY,
    KPOINT_STUDY_LATEST_PATH_STATE_KEY,
    KPOINT_STUDY_PARAMS_STATE_KEY,
    KPOINT_STUDY_SELECTED_PATH_STATE_KEY,
    EOS_STUDY_LATEST_PATH_STATE_KEY,
    EOS_STUDY_PARAMS_STATE_KEY,
    EOS_STUDY_SELECTED_PATH_STATE_KEY,
    PSEUDO_DIR_STATE_KEY,
    PSEUDO_RESOLVED_MAP_STATE_KEY,
)
from ..core.structure_state import (
    get_current_active_structure_signature,
    get_current_pseudo_settings,
    get_current_source_metadata,
    get_structure_mode,
)
from ..core.workspace import QE_CONVERGENCE_DIR
from ..services.input_generator import CalculationContext
from ..services.structure_tools import make_conventional_atoms
from ..convergence.models import (
    EOS_FIT_ENERGY_BASIS_CELL_TOTAL_EV,
    EOS_FIT_VOLUME_BASIS_CELL_TOTAL_ANG3,
)
from .shared import render_path_captions, render_status_messages, render_table
from .ui_setup import (
    _build_generation_context_from_parameters,
    _sanitize_saved_qe_parameters,
    _validate_qe_submission_parameters,
    default_qe_parameters,
    get_qe_parameters,
    QE_PARAMS_STATE_KEY,
)
from ..services.plotting.study_plots import plot_cutoff_convergence, plot_kpoint_convergence, plot_eos_study
from ..services.studies.study_summary_view import detect_baseline_drift

from ..convergence import (
    EosSweepSpec,
    build_eos_study,
    discover_eos_studies,
    load_eos_study_manifest,
    load_eos_study_summary,
    refresh_eos_study,
)
from ..convergence.study_runner import (
    build_cutoff_study_steps,
    build_eos_study_steps,
    build_kpoint_study_steps,
    is_pw_available,
)
from ..services.qe_runner import (
    DEFAULT_MAX_TAIL_LINES,
    RUN_FINISHED_STATE_KEY,
    RUN_PHASE_LABEL_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    apply_run_events,
    cleanup_run,
    drain_run_events,
    get_run_context,
    mark_runtime_lost,
    run_chained_jobs,
)

_DEFAULT_CUTOFF_TOLERANCE_MEV_PER_ATOM = 1.0
_EOS_DISPLAY_MODE_CELL_TOTAL = "cell_total"
_EOS_DISPLAY_MODE_PER_ATOM = "per_atom"
_EOS_DISPLAY_MODE_RELATIVE_PER_ATOM = "relative_per_atom"
_EOS_DISPLAY_MODE_LABELS = {
    _EOS_DISPLAY_MODE_CELL_TOTAL: "Cell total: Energy (eV) vs Volume (Å³)",
    _EOS_DISPLAY_MODE_PER_ATOM: "Per atom: Energy/atom (eV) vs Volume/atom (Å³)",
    _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM: "Relative: ΔE (meV/atom) vs Volume/atom (Å³/atom)",
}

_ECUTRHO_MODE_LABELS = {
    EcutrhoMode.METADATA_DUAL.value: "Pseudo metadata dual ratios",
    EcutrhoMode.RATIO_RULE.value: "Ratio rule",
    EcutrhoMode.MANUAL_ABSOLUTE.value: "Manual absolute ecutrho",
    EcutrhoMode.MANUAL_RATIO.value: "Manual ecutrho ratio",
}


def _coerce_positive_int(value: object) -> int | None:
    """Return a positive integer or ``None`` when unavailable."""
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def _coerce_positive_float(value: object, default: float) -> float:
    """Return a positive finite float or the supplied default."""
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(candidate) or candidate <= 0:
        return float(default)
    return candidate


def _eos_summary_has_current_fit_basis(summary: dict | None) -> bool:
    """Return True when an EOS summary uses the current fitted-cell total basis."""
    if not summary:
        return False
    fit = summary.get("fit", {})
    if fit.get("energy_basis") != EOS_FIT_ENERGY_BASIS_CELL_TOTAL_EV:
        return False
    if fit.get("volume_basis") != EOS_FIT_VOLUME_BASIS_CELL_TOTAL_ANG3:
        return False
    if "natoms_in_fitted_cell" not in fit:
        return False

    for point in summary.get("points", []):
        if str(point.get("status") or "") != "success":
            continue
        if "total_energy_ev" not in point or "energy_per_atom_ev" not in point:
            return False
    return True


def _resolve_eos_fitted_cell_atom_count(manifest: dict, summary: dict) -> tuple[int | None, str | None]:
    """Validate the fitted-cell atom count used for per-atom EOS display."""
    fit = summary.get("fit", {})
    fitted_cell_atom_count = _coerce_positive_int(fit.get("natoms_in_fitted_cell"))
    if fitted_cell_atom_count is None:
        return None, "Per-atom EOS display is unavailable because the fitted-cell atom count metadata is missing or invalid."

    manifest_counts = {
        count
        for count in (
            _coerce_positive_int(manifest.get("atom_count")),
            _coerce_positive_int(manifest.get("structure", {}).get("atom_count")),
        )
        if count is not None
    }
    if any(count != fitted_cell_atom_count for count in manifest_counts):
        return None, "Per-atom EOS display is unavailable because the fitted-cell atom count metadata is inconsistent."
    return fitted_cell_atom_count, None


def _eos_summary_has_equilibrium_geometry(summary: dict | None) -> bool:
    """Return True when one EOS summary includes equilibrium geometry metadata."""
    if not summary:
        return False
    fit = summary.get("fit", {})
    geometry = fit.get("equilibrium_geometry")
    if not isinstance(geometry, dict) or "available" not in geometry:
        return False
    conventional_cell = geometry.get("conventional_cell")
    if not isinstance(conventional_cell, dict) or "available" not in conventional_cell:
        return False
    return True


def _normalize_eos_display_mode(requested_mode: object, *, natoms_in_fitted_cell: int | None) -> str:
    """Resolve one requested EOS display mode onto a safe supported mode."""
    if requested_mode in {_EOS_DISPLAY_MODE_PER_ATOM, _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM} and natoms_in_fitted_cell is not None:
        return str(requested_mode)
    return _EOS_DISPLAY_MODE_CELL_TOTAL


def _get_available_eos_display_modes(*, natoms_in_fitted_cell: int | None) -> list[str]:
    """Return the EOS display modes that are safe for the current summary metadata."""
    available_modes = [_EOS_DISPLAY_MODE_CELL_TOTAL]
    if natoms_in_fitted_cell is not None:
        available_modes.extend([_EOS_DISPLAY_MODE_PER_ATOM, _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM])
    return available_modes


def _get_eos_fit_display_values(
    fit: dict,
    *,
    display_mode: str,
    natoms_in_fitted_cell: int | None,
) -> dict[str, object]:
    """Return UI-facing equilibrium metrics without changing the internal fit basis."""
    resolved_mode = _normalize_eos_display_mode(
        display_mode,
        natoms_in_fitted_cell=natoms_in_fitted_cell,
    )
    e0_value = fit.get("E0")
    v0_value = fit.get("V0")

    if resolved_mode == _EOS_DISPLAY_MODE_PER_ATOM and natoms_in_fitted_cell is not None:
        e0_value = None if e0_value is None else float(e0_value) / natoms_in_fitted_cell
        v0_value = None if v0_value is None else float(v0_value) / natoms_in_fitted_cell
        return {
            "display_mode": resolved_mode,
            "volume_label": "Equilibrium Volume/atom (V0/N)",
            "volume_value": v0_value,
            "volume_unit": "Å³/atom",
            "energy_label": "Equilibrium Energy/atom (E0/N)",
            "energy_value": e0_value,
            "energy_unit": "eV/atom",
            "energy_display_text": None,
            "energy_note": None,
            "bulk_modulus_label": "Bulk Modulus (B0)",
            "bulk_modulus_value": fit.get("B0"),
        }
    if resolved_mode == _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM and natoms_in_fitted_cell is not None:
        e0_per_atom = None if e0_value is None else float(e0_value) / natoms_in_fitted_cell
        v0_value = None if v0_value is None else float(v0_value) / natoms_in_fitted_cell
        reference_note = "Relative mode uses ΔE = (E_total - E0_total) / N, where fitted E0 is unavailable."
        if e0_value is not None:
            reference_note = (
                "Relative mode uses ΔE = (E_total - E0_total) / N. "
                f"Fitted E0 = {float(e0_value):.4f} eV for the simulated fitted cell"
                + (
                    f" ({float(e0_per_atom):.4f} eV/atom)."
                    if e0_per_atom is not None
                    else "."
                )
            )
        return {
            "display_mode": resolved_mode,
            "volume_label": "Equilibrium Volume/atom (V0/N)",
            "volume_value": v0_value,
            "volume_unit": "Å³/atom",
            "energy_label": "Reference Energy",
            "energy_value": None,
            "energy_unit": None,
            "energy_display_text": "fitted E0",
            "energy_note": reference_note,
            "bulk_modulus_label": "Bulk Modulus (B0)",
            "bulk_modulus_value": fit.get("B0"),
        }

    return {
        "display_mode": _EOS_DISPLAY_MODE_CELL_TOTAL,
        "volume_label": "Equilibrium Volume (V0, fitted cell)",
        "volume_value": v0_value,
        "volume_unit": "Å³",
        "energy_label": "Equilibrium Energy (E0, fitted cell)",
        "energy_value": e0_value,
        "energy_unit": "eV",
        "energy_display_text": None,
        "energy_note": None,
        "bulk_modulus_label": "Bulk Modulus (B0)",
        "bulk_modulus_value": fit.get("B0"),
    }


def _resolve_standardized_conventional_lattice_a(atoms) -> tuple[float | None, str | None]:
    """Return the standardized conventional-cell ``a`` lattice parameter for one structure."""
    if atoms is None:
        return None, "The active structure is unavailable, so QE Studio could not determine the current conventional-cell lattice parameter."
    try:
        conventional_result = make_conventional_atoms(atoms)
    except ValueError as exc:
        return None, f"QE Studio could not determine a standardized conventional cell for the current structure: {exc}"

    try:
        lattice_a = float(conventional_result.atoms.cell.cellpar()[0])
    except (TypeError, ValueError, IndexError):
        return None, "QE Studio could not read the standardized conventional-cell lattice parameter from the current structure."
    if not math.isfinite(lattice_a) or lattice_a <= 0:
        return None, "QE Studio found an invalid standardized conventional-cell lattice parameter for the current structure."
    return lattice_a, None


def _apply_scaled_raw_structure(*, scale_factor: float, success_message: str) -> bool:
    """Apply one isotropically scaled raw structure back into the active session."""
    if not math.isfinite(scale_factor) or scale_factor <= 0:
        st.error("QE Studio could not apply the requested EOS scaling because the derived scale factor is invalid.")
        return False

    from ..core.structure_state import get_structure_mode, resolve_atoms_for_structure_mode, _store_active_structure
    from ..core.state_keys import (
        RAW_ATOMS_STATE_KEY,
        SOURCE_DETAILS_STATE_KEY,
        SOURCE_FORMAT_STATE_KEY,
        SOURCE_KIND_STATE_KEY,
        SOURCE_LABEL_STATE_KEY,
        STRUCTURE_FILE_BYTES_STATE_KEY,
    )

    raw_atoms = st.session_state.get(RAW_ATOMS_STATE_KEY)
    if raw_atoms is None:
        st.error("QE Studio could not update the active structure because the raw source cell is unavailable in session state.")
        return False

    new_raw_atoms = raw_atoms.copy()
    new_raw_atoms.set_cell(new_raw_atoms.cell * scale_factor, scale_atoms=True)

    active_atoms, resolved_mode, status_message = resolve_atoms_for_structure_mode(new_raw_atoms, get_structure_mode())

    _store_active_structure(
        new_raw_atoms,
        active_atoms,
        source_kind=st.session_state.get(SOURCE_KIND_STATE_KEY, "file"),
        source_label=st.session_state.get(SOURCE_LABEL_STATE_KEY),
        source_format=st.session_state.get(SOURCE_FORMAT_STATE_KEY),
        source_details=dict(st.session_state.get(SOURCE_DETAILS_STATE_KEY) or {}),
        structure_mode=resolved_mode,
        structure_mode_message=status_message,
        payload_bytes=st.session_state.get(STRUCTURE_FILE_BYTES_STATE_KEY),
    )
    st.success(success_message)
    st.rerun()
    return True


def _default_ratio_rule_fields(ratio: float) -> tuple[str, float | None]:
    """Map one numeric cutoff ratio onto the built-in rule selector."""
    normalized_ratio = _coerce_positive_float(ratio, 8.0)
    for option in CUTOFF_RATIO_RULE_OPTIONS:
        if option == RATIO_RULE_CUSTOM:
            continue
        option_value = float(option.rstrip("xX"))
        if abs(normalized_ratio - option_value) <= 1.0e-9:
            return option, None
    return RATIO_RULE_CUSTOM, normalized_ratio


def default_cutoff_study_parameters(
    resolved_entries: dict | None = None,
    atoms=None,
) -> dict:
    """Return default parameters for the cutoff convergence workflow."""
    defaults = default_qe_parameters(resolved_entries=resolved_entries, atoms=atoms)
    metadata_dual_info = inspect_metadata_dual_support(resolved_entries or {})
    ratio_value = defaults["ecutrho"] / defaults["ecutwfc"] if defaults["ecutwfc"] > 0 else 8.0
    ratio_rule, custom_ratio = _default_ratio_rule_fields(ratio_value)
    rounded_start = max(20.0, math.floor(max(defaults["ecutwfc"] - 20.0, 20.0) / 10.0) * 10.0)
    step_ry = 10.0
    start_ecutwfc_ry = rounded_start
    stop_ecutwfc_ry = start_ecutwfc_ry + 4 * step_ry
    default_mode = (
        EcutrhoMode.METADATA_DUAL.value
        if metadata_dual_info["available"]
        else EcutrhoMode.RATIO_RULE.value
    )

    return {
        "calculation": "scf",
        "prefix": defaults["prefix"],
        "prefix_source": defaults["prefix_source"],
        "outdir": defaults["outdir"],
        "outdir_source": defaults["outdir_source"],
        "occupations": defaults["occupations"],
        "smearing": defaults["smearing"],
        "degauss": defaults["degauss"],
        "nspin": defaults["nspin"],
        "spin_source": defaults["spin_source"],
        "starting_magnetization": defaults["starting_magnetization"],
        "noncolin": defaults["noncolin"],
        "lspinorb": defaults["lspinorb"],
        "dftu_enabled": defaults["dftu_enabled"],
        "hubbard_u": defaults["hubbard_u"],
        "vdw_method": defaults["vdw_method"],
        "k_points_grid": defaults["k_points_grid"],
        "conv_thr": defaults["conv_thr"],
        "mixing_beta": defaults["mixing_beta"],
        "start_ecutwfc_ry": start_ecutwfc_ry,
        "stop_ecutwfc_ry": stop_ecutwfc_ry,
        "step_ry": step_ry,
        "tolerance_mev_per_atom": _DEFAULT_CUTOFF_TOLERANCE_MEV_PER_ATOM,
        "ecutrho_mode": default_mode,
        "ratio_rule": ratio_rule,
        "custom_ratio": custom_ratio,
        "manual_ecutrho_ry": defaults["ecutrho"],
        "manual_ratio": ratio_value,
    }


def _sanitize_saved_cutoff_study_parameters(
    saved_parameters: dict | None,
    *,
    resolved_entries: dict | None = None,
    atoms=None,
) -> dict:
    """Normalize persisted cutoff-study parameters and drop stale values."""
    raw = dict(saved_parameters or {})
    defaults = default_cutoff_study_parameters(resolved_entries=resolved_entries, atoms=atoms)
    metadata_dual_info = inspect_metadata_dual_support(resolved_entries or {})
    shared_defaults = _sanitize_saved_qe_parameters(raw, resolved_entries=resolved_entries, atoms=atoms)

    normalized = dict(defaults)
    normalized.update(
        {
            "prefix": shared_defaults["prefix"],
            "prefix_source": shared_defaults["prefix_source"],
            "outdir": shared_defaults["outdir"],
            "outdir_source": shared_defaults["outdir_source"],
            "occupations": str(raw.get("occupations", defaults["occupations"])),
            "smearing": str(raw.get("smearing", defaults["smearing"])),
            "degauss": float(raw.get("degauss", defaults["degauss"])),
            "nspin": shared_defaults["nspin"],
            "spin_source": shared_defaults["spin_source"],
            "starting_magnetization": shared_defaults["starting_magnetization"],
            "noncolin": shared_defaults["noncolin"],
            "lspinorb": shared_defaults["lspinorb"],
            "dftu_enabled": shared_defaults["dftu_enabled"],
            "hubbard_u": shared_defaults["hubbard_u"],
            "vdw_method": shared_defaults["vdw_method"],
            "k_points_grid": [int(value) for value in raw.get("k_points_grid", defaults["k_points_grid"])],
            "conv_thr": float(raw.get("conv_thr", defaults["conv_thr"])),
            "mixing_beta": float(raw.get("mixing_beta", defaults["mixing_beta"])),
            "calculation": "scf",
        }
    )

    normalized["start_ecutwfc_ry"] = _coerce_positive_float(raw.get("start_ecutwfc_ry"), defaults["start_ecutwfc_ry"])
    normalized["stop_ecutwfc_ry"] = _coerce_positive_float(raw.get("stop_ecutwfc_ry"), defaults["stop_ecutwfc_ry"])
    normalized["step_ry"] = _coerce_positive_float(raw.get("step_ry"), defaults["step_ry"])
    normalized["tolerance_mev_per_atom"] = _coerce_positive_float(
        raw.get("tolerance_mev_per_atom"),
        defaults["tolerance_mev_per_atom"],
    )
    ratio_rule = str(raw.get("ratio_rule") or defaults["ratio_rule"])
    normalized["ratio_rule"] = ratio_rule if ratio_rule in CUTOFF_RATIO_RULE_OPTIONS else defaults["ratio_rule"]
    normalized["custom_ratio"] = _coerce_positive_float(raw.get("custom_ratio"), defaults["custom_ratio"] or 8.0)
    normalized["manual_ecutrho_ry"] = _coerce_positive_float(raw.get("manual_ecutrho_ry"), defaults["manual_ecutrho_ry"])
    normalized["manual_ratio"] = _coerce_positive_float(raw.get("manual_ratio"), defaults["manual_ratio"])

    requested_mode = str(raw.get("ecutrho_mode") or defaults["ecutrho_mode"])
    if requested_mode == EcutrhoMode.METADATA_DUAL.value and not metadata_dual_info["available"]:
        normalized["ecutrho_mode"] = EcutrhoMode.RATIO_RULE.value
    elif requested_mode in {mode.value for mode in EcutrhoMode}:
        normalized["ecutrho_mode"] = requested_mode
    else:
        normalized["ecutrho_mode"] = defaults["ecutrho_mode"]

    return normalized


def get_cutoff_study_parameters(resolved_entries: dict | None = None, atoms=None) -> dict:
    """Return the current cutoff-study parameters from session state or defaults."""
    saved = st.session_state.get(CUTOFF_STUDY_PARAMS_STATE_KEY)
    if saved is not None:
        sanitized = _sanitize_saved_cutoff_study_parameters(saved, resolved_entries=resolved_entries, atoms=atoms)
        if sanitized != saved:
            st.session_state[CUTOFF_STUDY_PARAMS_STATE_KEY] = sanitized
        return sanitized
    return default_cutoff_study_parameters(resolved_entries=resolved_entries, atoms=atoms)


def _available_ecutrho_modes(*, metadata_dual_available: bool) -> tuple[str, ...]:
    """Return the selectable ecutrho modes for the cutoff-study form."""
    options = [EcutrhoMode.RATIO_RULE.value, EcutrhoMode.MANUAL_ABSOLUTE.value, EcutrhoMode.MANUAL_RATIO.value]
    if metadata_dual_available:
        options.insert(0, EcutrhoMode.METADATA_DUAL.value)
    return tuple(options)


def _build_cutoff_sweep_spec_from_parameters(cutoff_parameters: dict) -> CutoffSweepSpec:
    """Build a typed cutoff-sweep specification from the flat UI payload."""
    return CutoffSweepSpec(
        start_ecutwfc_ry=float(cutoff_parameters["start_ecutwfc_ry"]),
        stop_ecutwfc_ry=float(cutoff_parameters["stop_ecutwfc_ry"]),
        step_ry=float(cutoff_parameters["step_ry"]),
        tolerance_mev_per_atom=float(cutoff_parameters["tolerance_mev_per_atom"]),
        ecutrho_mode=str(cutoff_parameters["ecutrho_mode"]),
        ratio_rule=str(cutoff_parameters.get("ratio_rule", "8x")),
        custom_ratio=(
            None
            if cutoff_parameters.get("custom_ratio") in (None, "")
            else float(cutoff_parameters["custom_ratio"])
        ),
        manual_ecutrho_ry=(
            None
            if cutoff_parameters.get("manual_ecutrho_ry") in (None, "")
            else float(cutoff_parameters["manual_ecutrho_ry"])
        ),
        manual_ratio=(
            None
            if cutoff_parameters.get("manual_ratio") in (None, "")
            else float(cutoff_parameters["manual_ratio"])
        ),
    )


def _build_qe_request_from_cutoff_parameters(
    cutoff_parameters: dict,
    *,
    sweep_spec: CutoffSweepSpec,
    resolved_entries: dict[str, dict] | None = None,
) -> dict:
    """Return the shared QE request payload for a cutoff study."""
    reference_ecutwfc = float(sweep_spec.stop_ecutwfc_ry)
    reference_ecutrho = resolve_point_ecutrho(
        sweep_spec,
        ecutwfc_ry=reference_ecutwfc,
        resolved_entries=resolved_entries or {},
    )
    return {
        "calculation": "scf",
        "prefix": cutoff_parameters["prefix"],
        "outdir": cutoff_parameters["outdir"],
        "ecutwfc": reference_ecutwfc,
        "ecutrho": reference_ecutrho,
        "occupations": cutoff_parameters["occupations"],
        "smearing": cutoff_parameters["smearing"],
        "degauss": cutoff_parameters["degauss"],
        "k_points_grid": cutoff_parameters["k_points_grid"],
        "conv_thr": cutoff_parameters["conv_thr"],
        "mixing_beta": cutoff_parameters["mixing_beta"],
        "nspin": cutoff_parameters["nspin"],
        "starting_magnetization": cutoff_parameters["starting_magnetization"],
        "noncolin": cutoff_parameters["noncolin"],
        "lspinorb": cutoff_parameters["lspinorb"],
        "dftu_enabled": cutoff_parameters["dftu_enabled"],
        "hubbard_u": cutoff_parameters["hubbard_u"],
        "vdw_method": cutoff_parameters["vdw_method"],
    }


def _build_cutoff_generation_context_from_parameters(
    atoms,
    cutoff_parameters: dict,
    *,
    resolved_entries: dict[str, dict] | None = None,
) -> tuple[CalculationContext, CutoffSweepSpec]:
    """Build the reference QE context plus sweep spec for one cutoff study."""
    sweep_spec = _build_cutoff_sweep_spec_from_parameters(cutoff_parameters)
    qe_request = _build_qe_request_from_cutoff_parameters(
        cutoff_parameters,
        sweep_spec=sweep_spec,
        resolved_entries=resolved_entries,
    )
    return (
        _build_generation_context_from_parameters(
            atoms,
            qe_request,
            resolved_entries=resolved_entries,
        ),
        sweep_spec,
    )


def _validate_cutoff_study_submission_parameters(
    cutoff_parameters: dict,
    *,
    metadata_dual_info: dict[str, object],
    resolved_entries: dict[str, dict] | None = None,
) -> str | None:
    """Return a blocking user-facing validation message for cutoff-study submissions."""
    if message := _validate_qe_submission_parameters(cutoff_parameters):
        return message
    try:
        sweep_spec = _build_cutoff_sweep_spec_from_parameters(cutoff_parameters)
        sweep_spec.validate()
    except Exception as exc:
        return str(exc)
    if (
        sweep_spec.normalized_ecutrho_mode == EcutrhoMode.METADATA_DUAL
        and not bool(metadata_dual_info.get("available"))
    ):
        return "Metadata-derived ecutrho mode is unavailable because at least one resolved pseudopotential lacks cutoff metadata."
    try:
        resolve_point_ecutrho(
            sweep_spec,
            ecutwfc_ry=sweep_spec.stop_ecutwfc_ry,
            resolved_entries=resolved_entries or {},
        )
    except Exception as exc:
        return str(exc)
    return None


def _format_optional_number(value: object, *, digits: int = 1) -> str:
    """Return a compact table-friendly numeric string."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return "Unavailable"
    if not math.isfinite(numeric_value):
        return "Unavailable"
    return f"{numeric_value:.{digits}f}"


# ---------------------------------------------------------------------------
# Study run controls — shared across Cutoff, K-Point, and EOS
# ---------------------------------------------------------------------------

# Per-study-type state key for the active run id so each tab tracks its own job
_STUDY_RUN_ID_KEY = "study_active_run_id"
_STUDY_RUN_OWNER_KEY = "study_active_run_owner"


def _eos_summary_needs_refresh(study_dir: Path, summary: dict | None) -> bool:
    """Return True when the stored EOS summary disagrees with files on disk."""
    if not summary:
        return True
    if not _eos_summary_has_current_fit_basis(summary):
        return True
    if not _eos_summary_has_equilibrium_geometry(summary):
        return True

    for point in summary.get("points", []):
        status = str(point.get("status") or "")
        if status == "success" and (
            point.get("total_energy_ev") is None or "energy_per_atom_ev" not in point
        ):
            return True
        if status not in {"missing_output", "missing_job_done"}:
            continue
        run_dir_name = point.get("run_dir_name")
        if not run_dir_name:
            continue
        if (study_dir / str(run_dir_name) / "pw.out").is_file():
            return True
    return False


def _sync_study_run_state(
    state: MutableMapping[str, object],
    *,
    owner_key: str,
    get_context_fn: Callable[[str | None], object | None] = get_run_context,
    drain_events_fn: Callable[[str | None], list] = drain_run_events,
    cleanup_fn: Callable[[str | None], None] = cleanup_run,
) -> str:
    """Poll one study run and merge queued events into session-like state."""
    active_run_id = state.get(_STUDY_RUN_ID_KEY)
    active_owner = state.get(_STUDY_RUN_OWNER_KEY)

    if active_run_id is None or active_owner != owner_key:
        return "inactive"

    run_id = str(active_run_id)
    context = get_context_fn(run_id)
    if context is None:
        mark_runtime_lost(state)
        state.pop(_STUDY_RUN_ID_KEY, None)
        state.pop(_STUDY_RUN_OWNER_KEY, None)
        return "runtime_lost"

    events = drain_events_fn(run_id)
    run_completed = apply_run_events(
        state,
        events,
        max_tail_lines=DEFAULT_MAX_TAIL_LINES,
    )
    if run_completed:
        cleanup_fn(run_id)
        state.pop(_STUDY_RUN_ID_KEY, None)
        state.pop(_STUDY_RUN_OWNER_KEY, None)
        return "completed"

    if not context.thread.is_alive():
        mark_runtime_lost(state)
        cleanup_fn(run_id)
        state.pop(_STUDY_RUN_ID_KEY, None)
        state.pop(_STUDY_RUN_OWNER_KEY, None)
        return "runtime_lost"

    return "running"


@st.fragment(run_every=1)
def _study_run_status_fragment(owner_key: str) -> None:
    """Render the currently running study step and keep it in sync."""
    result = _sync_study_run_state(st.session_state, owner_key=owner_key)

    if result == "running":
        phase = st.session_state.get(RUN_PHASE_LABEL_STATE_KEY, "")
        st.info(f"⚙️ Running… {phase}" if phase else "⚙️ Running…")
        return

    if result == "runtime_lost":
        st.error("❌ Live run state was lost. Refresh the results to inspect any completed points.")
        st.rerun(scope="app")
        return

    if result == "completed":
        st.rerun(scope="app")


@st.fragment(run_every=1)
def _study_live_console_fragment(owner_key: str) -> None:
    """Render the stored live tail for the active study run."""
    active_run_id = st.session_state.get(_STUDY_RUN_ID_KEY)
    active_owner = st.session_state.get(_STUDY_RUN_OWNER_KEY)
    if active_run_id is None or active_owner != owner_key:
        return

    tail: list[str] = list(st.session_state.get(RUN_TAIL_LINES_STATE_KEY, []))
    phase = str(st.session_state.get(RUN_PHASE_LABEL_STATE_KEY) or "")

    if tail:
        st.code("".join(tail[-80:]), language="text")
    else:
        st.info(f"⏳ Waiting for pw.x output… (step: {phase})" if phase else "⏳ Starting pw.x…")


def render_study_run_controls(
    *,
    study_dir: Path,
    manifest: dict,
    study_type: str,  # "cutoff" | "kpoint" | "eos"
    owner_key: str,
    on_run_complete_refresh: bool = True,
) -> None:
    """Render Run / Stop controls and a live console for one study type."""
    active_run_id = st.session_state.get(_STUDY_RUN_ID_KEY)
    active_owner = st.session_state.get(_STUDY_RUN_OWNER_KEY)
    this_study_running = active_run_id is not None and active_owner == owner_key
    any_job_running = active_run_id is not None  # global lock (only one pw.x at a time)

    # --- Count pending (no pw.out) vs total points ---
    points_data = manifest.get("points", [])
    pending_count = 0
    for p in points_data:
        if study_type == "eos":
            run_dir_name = p.get("run_dir_name")
            out = (study_dir / run_dir_name / "pw.out") if run_dir_name else None
        else:
            run_dir = Path(p.get("run_dir", ""))
            out = run_dir / "pw.out" if run_dir.name else None
        if out and not out.is_file():
            pending_count += 1

    total_count = len(points_data)

    # Status line
    finished = st.session_state.get(RUN_FINISHED_STATE_KEY, False)
    returncode = st.session_state.get(RUN_RETURNCODE_STATE_KEY)

    col_status, col_run, col_stop = st.columns([3, 1, 1])

    with col_status:
        if this_study_running:
            _study_run_status_fragment(owner_key)
        elif finished and active_run_id is None and active_owner == owner_key:
            if returncode == 0:
                st.success("✅ Study run completed successfully.")
            else:
                st.error(f"❌ Study run finished with exit code {returncode}.")
        else:
            if pending_count == 0:
                st.success(f"✅ All {total_count} point(s) have outputs.")
            else:
                st.warning(f"⚠️ {pending_count} of {total_count} point(s) have no `pw.out` yet.")

    run_label = "▶ Run Study" if pending_count > 0 else "↺ Re-run Study"

    with col_run:
        run_clicked = st.button(
            run_label,
            key=f"run_study_{owner_key}",
            disabled=any_job_running,
            use_container_width=True,
            type="primary",
        )

    with col_stop:
        stop_clicked = st.button(
            "⏹ Stop",
            key=f"stop_study_{owner_key}",
            disabled=not this_study_running,
            use_container_width=True,
        )

    if stop_clicked and this_study_running:
        ctx_obj = get_run_context(str(active_run_id))
        if ctx_obj and ctx_obj.process is not None:
            try:
                ctx_obj.process.terminate()
            except Exception:
                pass
        cleanup_run(str(active_run_id))
        st.session_state.pop(_STUDY_RUN_ID_KEY, None)
        st.session_state.pop(_STUDY_RUN_OWNER_KEY, None)
        st.rerun()

    if run_clicked:
        if not is_pw_available():
            st.error("❌ `pw.x` was not found on your PATH. Install Quantum ESPRESSO and ensure `pw.x` is accessible.")
        else:
            if study_type == "cutoff":
                steps = build_cutoff_study_steps(study_dir, manifest)
            elif study_type == "kpoint":
                steps = build_kpoint_study_steps(study_dir, manifest)
            elif study_type == "eos":
                steps = build_eos_study_steps(study_dir, manifest)
            else:
                steps = []

            if not steps:
                st.error("No runnable input files (`pw.in`) were found in this study. Regenerate the study.")
            else:
                # Clear previous run state for this study
                st.session_state[RUN_TAIL_LINES_STATE_KEY] = []
                st.session_state[RUN_FINISHED_STATE_KEY] = False
                st.session_state[RUN_RETURNCODE_STATE_KEY] = None
                st.session_state[RUN_PHASE_LABEL_STATE_KEY] = steps[0].label

                run_id, _ = run_chained_jobs(
                    job_label=f"{study_type.upper()} Convergence Study",
                    steps=steps,
                )
                st.session_state[_STUDY_RUN_ID_KEY] = run_id
                st.session_state[_STUDY_RUN_OWNER_KEY] = owner_key
                st.rerun()

    # Live console — only renders when this study type is active
    if this_study_running:
        with st.expander("🖥️ Live Console", expanded=False):
            _study_live_console_fragment(owner_key)
    elif st.session_state.get(RUN_TAIL_LINES_STATE_KEY) and active_owner == owner_key:
        with st.expander("📋 Last Run Output", expanded=False):
            tail = st.session_state.get(RUN_TAIL_LINES_STATE_KEY, [])
            st.code("".join(tail), language="text")


def render_cutoff_study_results(ctx: ProjectContext, active_baseline: dict, active_structure_signature: dict | None) -> None:
    """Render the cutoff-study selection, refresh controls, and summary table."""
    st.markdown("### Cutoff Study Results")
    studies = discover_cutoff_studies(QE_CONVERGENCE_DIR)
    if not studies:
        st.info("Generate a cutoff convergence study from this page to review its manifest and parsed results.")
        return

    st.session_state[CUTOFF_STUDY_LATEST_PATH_STATE_KEY] = str(studies[0])
    selected_path = str(st.session_state.get(CUTOFF_STUDY_SELECTED_PATH_STATE_KEY) or "")
    valid_paths = [str(path) for path in studies]
    if selected_path not in valid_paths:
        selected_path = valid_paths[0]
        st.session_state[CUTOFF_STUDY_SELECTED_PATH_STATE_KEY] = selected_path

    selected_path = st.selectbox(
        "Available cutoff studies",
        options=valid_paths,
        index=valid_paths.index(selected_path),
        format_func=lambda path: Path(path).name,
        key=CUTOFF_STUDY_SELECTED_PATH_STATE_KEY,
    )
    study_dir = Path(selected_path)
    manifest_path = study_dir / CUTOFF_STUDY_MANIFEST_FILENAME
    summary_path = study_dir / CUTOFF_STUDY_SUMMARY_FILENAME
    
    with st.expander("Study Paths", expanded=False):
        render_path_captions(
            [
                ("Study directory", str(study_dir)),
                ("Manifest", str(manifest_path)),
                ("Summary", str(summary_path)),
            ]
        )

    refresh_clicked = st.button("Refresh Results", key="refresh_cutoff", use_container_width=True)
    
    try:
        manifest = load_cutoff_study_manifest(study_dir)
    except Exception as exc:
        st.warning(f"Unable to read the study manifest: {exc}")
        return

    st.divider()
    st.markdown("#### Run Study")
    render_study_run_controls(
        study_dir=study_dir,
        manifest=manifest,
        study_type="cutoff",
        owner_key="cutoff",
    )
    st.divider()

    if refresh_clicked:
        try:
            summary = refresh_cutoff_study(study_dir)
        except Exception as exc:
            st.error(f"Failed to refresh the cutoff study summary: {exc}")
            return
    else:
        summary = load_cutoff_study_summary(study_dir)
        if summary is None:
            try:
                summary = refresh_cutoff_study(study_dir)
            except Exception as exc:
                st.warning(f"The cutoff study summary is unavailable: {exc}")
                return

    # Check for baseline drift
    drift_warnings = detect_baseline_drift(active_baseline, manifest, active_structure_signature)
    if drift_warnings:
        st.warning("Active Setup has changed since this study was generated. Results may no longer apply exactly.")
        for msg in drift_warnings:
            st.caption(f"- {msg}")
    else:
        st.success("This study matches the active Setup baseline.")

    recommendation = dict(summary.get("recommendation", {}))
    status = str(summary.get("study_status") or recommendation.get("status") or "incomplete")
    message = str(recommendation.get("message") or "Cutoff study summary is available.")
    
    # 2-column layout for Summary and Chart
    col_summary, col_chart = st.columns([1, 2])
    
    with col_summary:
        st.markdown("#### Study Summary")
        if status == "complete":
            st.success("Status: Complete")
        elif status == "unconverged":
            st.warning("Status: Unconverged")
        else:
            st.info(f"Status: {status.capitalize()}")
            
        points = summary.get("points", [])
        successful_points = [p for p in points if p.get("status") == "success"]
        
        st.write(f"**Points Tested:** {len(points)}")
        st.write(f"**Successful Runs:** {len(successful_points)}")
        
        sweep_spec = summary.get("sweep_spec", {})
        tolerance = sweep_spec.get("tolerance_mev_per_atom")
        if tolerance is not None:
            st.write(f"**Tolerance:** {tolerance} meV/atom")
            
        st.divider()
        st.write("**Recommendation:**")
        if status == "complete":
            st.success(message)
            if st.button("Apply Recommended Cutoff to Setup", use_container_width=True):
                # Update Setup Baseline
                if "QE_PARAMS_STATE_KEY" not in st.session_state:
                    st.session_state[QE_PARAMS_STATE_KEY] = active_baseline
                
                st.session_state[QE_PARAMS_STATE_KEY]["ecutwfc"] = float(recommendation["recommended_ecutwfc_ry"])
                st.session_state[QE_PARAMS_STATE_KEY]["ecutrho"] = float(recommendation["recommended_ecutrho_ry"])
                st.toast("Recommended cutoffs successfully applied to Setup baseline!")
        else:
            st.warning(message)

    with col_chart:
        fig = plot_cutoff_convergence(summary)
        st.plotly_chart(fig, use_container_width=True)

    summary_warnings = [str(warning) for warning in summary.get("warnings", [])]
    if summary_warnings:
        st.markdown("**Study Warnings**")
        render_status_messages([("warning", warning) for warning in summary_warnings])

    # Data Table
    rows = []
    for point in summary.get("points", []):
        output_path = point.get("output_path")
        rows.append(
            {
                "Point": str(point.get("index", "")),
                "ecutwfc (Ry)": _format_optional_number(point.get("ecutwfc_ry")),
                "ecutrho (Ry)": _format_optional_number(point.get("ecutrho_ry")),
                "Status": str(point.get("status", "unknown")).replace("_", " "),
                "Total Energy (Ry)": _format_optional_number(point.get("total_energy_ry"), digits=8),
                "Energy/atom (eV)": _format_optional_number(point.get("energy_per_atom_ev"), digits=8),
                "Delta (meV/atom)": _format_optional_number(point.get("delta_mev_per_atom"), digits=4),
                "pw.out": str(Path(output_path).name) if output_path else "Missing",
            }
        )
    render_table(rows, title="### Cutoff Convergence Data")

    with st.expander("Study Manifest Raw", expanded=False):
        st.json(manifest)


def render_cutoff_study_config(ctx: ProjectContext, baseline: dict, pseudopotentials: dict, pseudo_dir: str, resolved_entries: dict, atoms) -> None:
    """Render the configuration and generation UI for cutoff convergence."""
    st.markdown("### Cutoff Convergence")
    st.caption(
        "Vary `ecutwfc` to find the optimal cutoff. The current active Setup "
        "k-point grid and physics settings will be kept fixed as the baseline."
    )

    defaults = get_cutoff_study_parameters(resolved_entries=resolved_entries, atoms=atoms)
    metadata_dual_info = inspect_metadata_dual_support(resolved_entries)

    sweep_col1, sweep_col2, sweep_col3, sweep_col4 = st.columns(4)
    start_ecutwfc_ry = sweep_col1.number_input(
        "Start ecutwfc (Ry)",
        min_value=0.0,
        value=float(defaults["start_ecutwfc_ry"]),
        step=5.0,
        format="%.1f",
    )
    stop_ecutwfc_ry = sweep_col2.number_input(
        "Stop ecutwfc (Ry)",
        min_value=0.0,
        value=float(defaults["stop_ecutwfc_ry"]),
        step=5.0,
        format="%.1f",
    )
    step_ry = sweep_col3.number_input(
        "Step (Ry)",
        min_value=0.0,
        value=float(defaults["step_ry"]),
        step=1.0,
        format="%.1f",
    )
    tolerance_mev_per_atom = sweep_col4.number_input(
        "Tolerance (meV/atom)",
        min_value=0.0,
        value=float(defaults["tolerance_mev_per_atom"]),
        step=0.1,
        format="%.2f",
    )

    available_ecutrho_modes = _available_ecutrho_modes(metadata_dual_available=bool(metadata_dual_info["available"]))
    default_ecutrho_mode = defaults["ecutrho_mode"] if defaults["ecutrho_mode"] in available_ecutrho_modes else available_ecutrho_modes[0]

    ecutrho_mode_col1, ecutrho_mode_col2 = st.columns(2)
    ecutrho_mode = ecutrho_mode_col1.selectbox(
        "ecutrho mode",
        options=available_ecutrho_modes,
        index=available_ecutrho_modes.index(default_ecutrho_mode),
        format_func=lambda mode: _ECUTRHO_MODE_LABELS[mode],
    )

    ratio_rule = defaults["ratio_rule"]
    custom_ratio = defaults["custom_ratio"]
    manual_ecutrho_ry = defaults["manual_ecutrho_ry"]
    manual_ratio = defaults["manual_ratio"]

    if ecutrho_mode == EcutrhoMode.RATIO_RULE.value:
        ratio_rule = ecutrho_mode_col2.selectbox(
            "Ratio rule",
            options=CUTOFF_RATIO_RULE_OPTIONS,
            index=CUTOFF_RATIO_RULE_OPTIONS.index(defaults["ratio_rule"]),
        )
        if ratio_rule == RATIO_RULE_CUSTOM:
            custom_ratio = st.number_input(
                "Custom ecutrho / ecutwfc ratio",
                min_value=0.0,
                value=float(defaults["custom_ratio"] or 8.0),
                step=0.1,
                format="%.2f",
            )
    elif ecutrho_mode == EcutrhoMode.MANUAL_ABSOLUTE.value:
        manual_ecutrho_ry = ecutrho_mode_col2.number_input(
            "Manual ecutrho (Ry)",
            min_value=0.0,
            value=float(defaults["manual_ecutrho_ry"]),
            step=5.0,
            format="%.1f",
        )
    elif ecutrho_mode == EcutrhoMode.MANUAL_RATIO.value:
        manual_ratio = ecutrho_mode_col2.number_input(
            "Manual ecutrho / ecutwfc ratio",
            min_value=0.0,
            value=float(defaults["manual_ratio"]),
            step=0.1,
            format="%.2f",
        )
    else:
        dual_ratio_text = ", ".join(
            f"{species}: {ratio:.2f}x" for species, ratio in sorted((metadata_dual_info.get("dual_ratios") or {}).items())
        )
        ecutrho_mode_col2.caption(
            "Point ecutrho values will be derived from pseudopotential dual ratios and the maximum "
            f"species requirement will be used. {dual_ratio_text}"
        )

    if not metadata_dual_info["available"]:
        for warning_message in metadata_dual_info["warnings"]:
            st.warning(warning_message + " QE Studio defaults new cutoff studies to an explicit ratio-based ecutrho policy.")

    # We reuse the baseline dictionary but inject cutoff specifics
    qe_parameters = dict(baseline)
    qe_parameters.update({
        "calculation": "scf",
        "start_ecutwfc_ry": start_ecutwfc_ry,
        "stop_ecutwfc_ry": stop_ecutwfc_ry,
        "step_ry": step_ry,
        "tolerance_mev_per_atom": tolerance_mev_per_atom,
        "ecutrho_mode": ecutrho_mode,
        "ratio_rule": ratio_rule,
        "custom_ratio": custom_ratio,
        "manual_ecutrho_ry": manual_ecutrho_ry,
        "manual_ratio": manual_ratio,
    })

    validation_error = _validate_cutoff_study_submission_parameters(
        qe_parameters,
        metadata_dual_info=metadata_dual_info,
        resolved_entries=resolved_entries,
    )

    preview_warnings = []
    if not validation_error:
        try:
            _, sweep_spec = _build_cutoff_generation_context_from_parameters(
                atoms,
                qe_parameters,
                resolved_entries=resolved_entries,
            )
            preview_warnings = build_cutoff_sweep_warnings(
                sweep_spec,
                point_count=len(sweep_spec.ecutwfc_values()),
                metadata_dual_info=metadata_dual_info,
            )
        except Exception as exc:
            validation_error = str(exc)

    if preview_warnings:
        st.markdown("**Warnings**")
        render_status_messages([("warning", message) for message in preview_warnings])

    submitted = st.button(
        "Generate Cutoff Study",
        use_container_width=True,
        disabled=bool(validation_error),
    )

    if not submitted:
        if validation_error:
            st.caption(f"Generation blocked: {validation_error}")
        return

    if validation_error:
        st.error(f"Failed to generate the cutoff convergence study: {validation_error}")
        return

    try:
        base_ctx, sweep_spec = _build_cutoff_generation_context_from_parameters(
            atoms,
            qe_parameters,
            resolved_entries=resolved_entries,
        )
        study_result = build_cutoff_study(
            atoms,
            base_ctx,
            pseudopotentials,
            pseudo_dir,
            sweep_spec=sweep_spec,
            pseudo_settings=get_current_pseudo_settings(),
            source_metadata=get_current_source_metadata(),
            structure_signature=get_current_active_structure_signature(),
            structure_mode=get_structure_mode(),
            studies_root=QE_CONVERGENCE_DIR,
        )
    except Exception as exc:
        st.error(f"Failed to generate the cutoff convergence study: {exc}")
        return

    st.session_state[CUTOFF_STUDY_PARAMS_STATE_KEY] = qe_parameters
    st.session_state[CUTOFF_STUDY_LATEST_PATH_STATE_KEY] = str(study_result["study_dir"])
    st.session_state[CUTOFF_STUDY_SELECTED_PATH_STATE_KEY] = str(study_result["study_dir"])
    st.success(f"Generated cutoff convergence inputs under `{study_result['study_dir']}`.")




def _build_kpoint_generation_context_from_parameters(atoms, parameters: dict, *, resolved_entries: dict) -> tuple[CalculationContext, KPointSweepSpec]:
    ctx = _build_generation_context_from_parameters(
        atoms,
        parameters,
        resolved_entries=resolved_entries,
    )
    sweep_spec = KPointSweepSpec(
        mode=parameters["dimensionality_mode"],
        start_value=int(parameters["start_grid_value"]),
        stop_value=int(parameters["stop_grid_value"]),
        step=int(parameters["step_grid_value"]),
        tolerance_mev_per_atom=float(parameters["tolerance_mev_per_atom"]),
        baseline_k_grid=tuple(parameters.get("k_points_grid", (1, 1, 1))),
    )
    return ctx, sweep_spec


def render_kpoint_study_config(ctx: ProjectContext, baseline: dict, pseudopotentials: dict, pseudo_dir: str, resolved_entries: dict, atoms) -> None:
    st.markdown("### K-Point Study Configuration")
    
    st.info("The K-point study uses the active Setup parameters as a fixed baseline and sweeps the k-point grid.", icon="ℹ️")
    
    pbc = atoms.pbc
    if not any(pbc):
        st.warning("This structure appears to be 0D (e.g. isolated molecule). K-point convergence is not typically necessary as Gamma-only is sufficient.")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        dim_mode = st.selectbox(
            "Dimensionality Mode",
            options=[e.value for e in DimensionalityMode],
            index=0 if all(pbc) else 1 if sum(pbc) == 2 else 2 if sum(pbc) == 1 else 3,
            help="Select how the grid should be scaled."
        )
    with col2:
        start_grid = st.number_input("Start Grid Parameter", min_value=1, value=2, step=1, help="The starting multiplier for the periodic axes.")
        stop_grid = st.number_input("Stop Grid Parameter", min_value=2, value=8, step=1, help="The ending multiplier for the periodic axes.")
    with col3:
        step_grid = st.number_input("Step", min_value=1, value=2, step=1)
        tol_mev = st.number_input("Tolerance (meV/atom)", min_value=0.1, value=_DEFAULT_CUTOFF_TOLERANCE_MEV_PER_ATOM, step=0.1, format="%.2f")

    st.markdown("**Grid Scaling Explanation:**")
    if dim_mode == DimensionalityMode.ISOTROPIC_3D.value:
        st.caption("3D Isotropic: All three axes (kx, ky, kz) will be scaled equally.")
    elif dim_mode == DimensionalityMode.IN_PLANE_2D.value:
        st.caption("2D In-Plane: The kx and ky axes will be scaled equally. Kz will remain fixed at 1.")
    elif dim_mode == DimensionalityMode.WIRE_1D.value:
        st.caption("1D Wire: Only kz will be scaled. Kx and Ky will remain fixed at 1.")
    else:
        st.caption("0D Molecule: K-point grid remains fixed at 1x1x1 (Gamma-only).")

    qe_parameters = dict(baseline)
    qe_parameters.update({
        "calculation": "scf",
        "dimensionality_mode": dim_mode,
        "start_grid_value": start_grid,
        "stop_grid_value": stop_grid,
        "step_grid_value": step_grid,
        "tolerance_mev_per_atom": tol_mev,
    })

    if not st.button("Generate K-Point Convergence Study", type="primary"):
        return

    try:
        base_ctx, sweep_spec = _build_kpoint_generation_context_from_parameters(
            atoms,
            qe_parameters,
            resolved_entries=resolved_entries,
        )
        study_result = build_kpoint_study(
            atoms,
            base_ctx,
            pseudopotentials,
            pseudo_dir,
            sweep_spec=sweep_spec,
            pseudo_settings=get_current_pseudo_settings(),
            source_metadata=get_current_source_metadata(),
            structure_signature=get_current_active_structure_signature(),
            structure_mode=get_structure_mode(),
            studies_root=QE_CONVERGENCE_DIR,
        )
    except Exception as exc:
        st.error(f"Failed to generate the k-point convergence study: {exc}")
        return

    st.session_state[KPOINT_STUDY_PARAMS_STATE_KEY] = qe_parameters
    st.session_state[KPOINT_STUDY_LATEST_PATH_STATE_KEY] = str(study_result["study_dir"])
    st.session_state[KPOINT_STUDY_SELECTED_PATH_STATE_KEY] = str(study_result["study_dir"])
    st.success(f"Generated k-point convergence inputs under `{study_result['study_dir']}`.")


def render_kpoint_study_results(ctx: ProjectContext, active_baseline: dict, active_structure_signature: dict | None) -> None:
    st.markdown("### K-Point Study Results")
    studies = discover_kpoint_studies(QE_CONVERGENCE_DIR)
    if not studies:
        st.info("Generate a k-point convergence study from this page to review its manifest and parsed results.")
        return

    st.session_state[KPOINT_STUDY_LATEST_PATH_STATE_KEY] = str(studies[0])
    selected_path = str(st.session_state.get(KPOINT_STUDY_SELECTED_PATH_STATE_KEY) or "")
    valid_paths = [str(path) for path in studies]
    if selected_path not in valid_paths:
        selected_path = valid_paths[0]
        st.session_state[KPOINT_STUDY_SELECTED_PATH_STATE_KEY] = selected_path

    selected_path = st.selectbox(
        "Available k-point studies",
        options=valid_paths,
        index=valid_paths.index(selected_path),
        format_func=lambda path: Path(path).name,
        key=KPOINT_STUDY_SELECTED_PATH_STATE_KEY,
    )
    study_dir = Path(selected_path)
    manifest_path = study_dir / KPOINT_STUDY_MANIFEST_FILENAME
    summary_path = study_dir / KPOINT_STUDY_SUMMARY_FILENAME
    
    with st.expander("Study Paths", expanded=False):
        render_path_captions(
            [
                ("Study directory", str(study_dir)),
                ("Manifest", str(manifest_path)),
                ("Summary", str(summary_path)),
            ]
        )

    refresh_clicked = st.button("Refresh Results", key="refresh_kpoint", use_container_width=True)
    
    try:
        manifest = load_kpoint_study_manifest(study_dir)
    except Exception as exc:
        st.warning(f"Unable to read the study manifest: {exc}")
        return

    st.divider()
    st.markdown("#### Run Study")
    render_study_run_controls(
        study_dir=study_dir,
        manifest=manifest,
        study_type="kpoint",
        owner_key="kpoint",
    )
    st.divider()

    if refresh_clicked:
        try:
            summary = refresh_kpoint_study(study_dir)
        except Exception as exc:
            st.error(f"Failed to refresh the k-point study summary: {exc}")
            return
    else:
        summary = load_kpoint_study_summary(study_dir)
        if summary is None:
            try:
                summary = refresh_kpoint_study(study_dir)
            except Exception as exc:
                st.warning(f"The k-point study summary is unavailable: {exc}")
                return

    drift_warnings = detect_baseline_drift(active_baseline, manifest, active_structure_signature)
    if drift_warnings:
        st.warning("Active Setup has changed since this study was generated. Results may no longer apply exactly.")
        for msg in drift_warnings:
            st.caption(f"- {msg}")
    else:
        st.success("This study matches the active Setup baseline.")

    recommendation = dict(summary.get("recommendation", {}))
    status = str(summary.get("study_status") or recommendation.get("status") or "incomplete")
    message = str(recommendation.get("message") or "K-point study summary is available.")
    
    col_summary, col_chart = st.columns([1, 2])
    
    with col_summary:
        st.markdown("#### Study Summary")
        if status == "complete":
            st.success("Status: Complete")
        elif status == "unconverged":
            st.warning("Status: Unconverged")
        else:
            st.info(f"Status: {status.capitalize()}")
            
        points = summary.get("points", [])
        successful_points = [p for p in points if p.get("status") == "success"]
        
        st.write(f"**Points Tested:** {len(points)}")
        st.write(f"**Successful Runs:** {len(successful_points)}")
        
        sweep_spec = summary.get("sweep_spec", {})
        st.write(f"**Dimensionality Mode:** {sweep_spec.get('mode', 'N/A')}")
        tolerance = sweep_spec.get("tolerance_mev_per_atom")
        if tolerance is not None:
            st.write(f"**Tolerance:** {tolerance} meV/atom")
            
        st.divider()
        st.write("**Recommendation:**")
        if status == "complete":
            st.success(message)
            if st.button("Apply Recommended K-grid to Setup", use_container_width=True):
                if "QE_PARAMS_STATE_KEY" not in st.session_state:
                    st.session_state[QE_PARAMS_STATE_KEY] = active_baseline
                
                st.session_state[QE_PARAMS_STATE_KEY]["k_points_grid"] = recommendation["recommended_k_grid"]
                st.toast("Recommended k-points grid successfully applied to Setup baseline!")
        else:
            st.warning(message)

    with col_chart:
        fig = plot_kpoint_convergence(summary)
        st.plotly_chart(fig, use_container_width=True)

    summary_warnings = [str(warning) for warning in summary.get("warnings", [])]
    if summary_warnings:
        st.markdown("**Study Warnings**")
        render_status_messages([("warning", warning) for warning in summary_warnings])

    rows = []
    for point in summary.get("points", []):
        output_path = point.get("output_path")
        rows.append(
            {
                "Point": str(point.get("index", "")),
                "Grid": str(point.get("grid_label", "")),
                "Status": str(point.get("status", "unknown")).replace("_", " "),
                "Total Energy (Ry)": _format_optional_number(point.get("total_energy_ry"), digits=8),
                "Energy/atom (eV)": _format_optional_number(point.get("energy_per_atom_ev"), digits=8),
                "Delta (meV/atom)": _format_optional_number(point.get("delta_mev_per_atom"), digits=4),
                "pw.out": str(Path(output_path).name) if output_path else "Missing",
            }
        )
    render_table(rows, title="### K-Point Convergence Data")

    with st.expander("Study Manifest Raw", expanded=False):
        st.json(manifest)


def render_page(ctx: ProjectContext) -> None:
    """Render the Studies page."""
    st.subheader("Studies")
    st.caption("Validate and optimize baseline settings using multi-run workflows.")

    st.markdown("### Baseline Context")
    if not ctx.structure.loaded:
        st.info("No structure is loaded in this session. Load a structure and complete Setup first.")
        return

    pseudopotentials = st.session_state.get(PSEUDO_RESOLVED_MAP_STATE_KEY)
    pseudo_dir = st.session_state.get(PSEUDO_DIR_STATE_KEY)
    atoms = st.session_state.get(ATOMS_STATE_KEY)
    resolved_entries = st.session_state.get("pseudo_resolved_entries") or {}

    if atoms is None or not pseudopotentials or not pseudo_dir:
        st.warning("Pseudopotentials must be resolved on the Setup page before running studies.")
        return

    baseline = get_qe_parameters(resolved_entries=resolved_entries, atoms=ctx.atoms)
    k_grid_str = " x ".join(str(k) for k in baseline.get("k_points_grid", [1, 1, 1]))

    # Display baseline summary
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Structure", ctx.structure.formula or "Unknown")
    col2.metric("Prefix", baseline.get("prefix", "N/A"))
    col3.metric("K-Points", k_grid_str)
    col4.metric("Occupations", baseline.get("occupations", "N/A").capitalize())

    st.divider()

    st.markdown("### Study Type")
    tabs = st.tabs(["Cutoff Convergence", "K-Point Convergence", "Equation of State (EOS)"])

    with tabs[0]:
        render_cutoff_study_config(ctx, baseline, pseudopotentials, pseudo_dir, resolved_entries, atoms)
        st.divider()
        render_cutoff_study_results(ctx, baseline, get_current_active_structure_signature())

    with tabs[1]:
        render_kpoint_study_config(ctx, baseline, pseudopotentials, pseudo_dir, resolved_entries, atoms)
        st.divider()
        render_kpoint_study_results(ctx, baseline, get_current_active_structure_signature())

    with tabs[2]:
        st.subheader("Equation of State (EOS) Study")
        st.markdown("Determine the equilibrium lattice volume by isotropically scaling the cell and fitting the Birch-Murnaghan equation of state.")
        st.divider()
        render_eos_study_config(ctx, baseline, pseudopotentials, pseudo_dir, resolved_entries, atoms)
        st.divider()
        render_eos_study_results(ctx, baseline, get_current_active_structure_signature())


def default_eos_study_parameters() -> dict:
    return {
        "mode": "isotropic_scale",
        "start_scale": 0.95,
        "stop_scale": 1.05,
        "num_points": 7,
        "fit_model": "birch_murnaghan",
    }


def _sanitize_saved_eos_study_parameters(saved_parameters: dict | None) -> dict:
    raw = dict(saved_parameters or {})
    defaults = default_eos_study_parameters()
    return {
        "mode": str(raw.get("mode", defaults["mode"])),
        "start_scale": float(raw.get("start_scale", defaults["start_scale"])),
        "stop_scale": float(raw.get("stop_scale", defaults["stop_scale"])),
        "num_points": int(raw.get("num_points", defaults["num_points"])),
        "fit_model": str(raw.get("fit_model", defaults["fit_model"])),
    }


def get_eos_study_parameters() -> dict:
    saved = st.session_state.get(EOS_STUDY_PARAMS_STATE_KEY)
    if saved is not None:
        sanitized = _sanitize_saved_eos_study_parameters(saved)
        if sanitized != saved:
            st.session_state[EOS_STUDY_PARAMS_STATE_KEY] = sanitized
        return sanitized
    return default_eos_study_parameters()


def render_eos_study_config(ctx: ProjectContext, baseline: dict, pseudopotentials: dict, pseudo_dir: str, resolved_entries: dict, atoms) -> None:
    if atoms is None:
        st.warning("Please load or build a structure in the Setup page first.")
        return

    # Render Baseline Summary
    st.markdown("**Baseline Context**")
    cols = st.columns(4)
    cols[0].metric("Structure", atoms.get_chemical_formula())
    cols[1].metric("K-Point Grid", "x".join(map(str, baseline.get("k_points_grid", [1, 1, 1]))))
    cols[2].metric("Wavefunction Cutoff", f"{baseline.get('ecutwfc', 0)} Ry")
    cols[3].metric("Charge Cutoff", f"{baseline.get('ecutrho', 0)} Ry")

    if get_structure_mode() != "primitive":
        st.info("💡 EOS is often cleaner to perform on a primitive cell to minimize computational cost, but the source cell is valid.")

    st.markdown("### Generate EOS Study")
    params = get_eos_study_parameters()
    with st.form("eos_study_form"):
        col1, col2, col3 = st.columns(3)
        start_scale = col1.number_input(
            "Start Scale Factor",
            min_value=0.5,
            max_value=2.0,
            value=params["start_scale"],
            step=0.01,
            format="%.4f",
        )
        stop_scale = col2.number_input(
            "Stop Scale Factor",
            min_value=0.5,
            max_value=2.0,
            value=params["stop_scale"],
            step=0.01,
            format="%.4f",
        )
        num_points = col3.number_input(
            "Number of Points",
            min_value=3,
            max_value=21,
            value=params["num_points"],
            step=1,
        )

        submitted = st.form_submit_button("Generate EOS Study", use_container_width=True)
        if submitted:
            params["start_scale"] = start_scale
            params["stop_scale"] = stop_scale
            params["num_points"] = num_points
            st.session_state[EOS_STUDY_PARAMS_STATE_KEY] = params

            if start_scale >= stop_scale:
                st.error("Stop scale must be greater than start scale.")
                return

            sweep_spec = EosSweepSpec(
                mode=params["mode"],
                start_scale=start_scale,
                stop_scale=stop_scale,
                num_points=num_points,
                fit_model=params["fit_model"],
            )

            # Build generation context
            base_context = _build_generation_context_from_parameters(
                atoms,
                baseline,
                resolved_entries=resolved_entries,
            )

            with st.spinner("Generating Equation of State study inputs..."):
                study_dir, points = build_eos_study(
                    atoms,
                    base_context,
                    baseline,
                    sweep_spec,
                    QE_CONVERGENCE_DIR,
                    pseudopotentials,
                    pseudo_dir,
                )
            st.session_state[EOS_STUDY_LATEST_PATH_STATE_KEY] = str(study_dir)
            st.session_state[EOS_STUDY_SELECTED_PATH_STATE_KEY] = str(study_dir)
            st.success(f"Generated EOS study inputs under `{study_dir.name}`.")


def render_eos_study_results(ctx: ProjectContext, active_baseline: dict, active_structure_signature: dict | None) -> None:
    st.markdown("### View Study Results")
    studies = discover_eos_studies(QE_CONVERGENCE_DIR)
    if not studies:
        st.info("No EOS studies found.")
        return

    latest_path = st.session_state.get(EOS_STUDY_LATEST_PATH_STATE_KEY)
    selected_path = st.session_state.get(EOS_STUDY_SELECTED_PATH_STATE_KEY)
    
    if selected_path and Path(selected_path) in studies:
        default_index = studies.index(Path(selected_path))
    elif latest_path and Path(latest_path) in studies:
        default_index = studies.index(Path(latest_path))
    else:
        default_index = 0

    study_dir = st.selectbox(
        "Select EOS Study",
        studies,
        index=default_index,
        format_func=lambda p: p.name,
        key="eos_study_selector",
    )
    if not study_dir:
        return

    st.session_state[EOS_STUDY_SELECTED_PATH_STATE_KEY] = str(study_dir)

    with st.spinner("Loading study..."):
        manifest = load_eos_study_manifest(study_dir)
        if not manifest:
            st.error("Invalid or missing manifest.")
            return

        drift_warnings = detect_baseline_drift(active_baseline, manifest, active_structure_signature)
        if drift_warnings:
            st.warning("⚠️ **Baseline Mismatch:** Active Setup settings have changed since this study was generated.\n\n" + "\n".join(f"- {m}" for m in drift_warnings))
        else:
            st.success("✅ This study matches the active Setup baseline.")

        st.divider()
        st.markdown("#### Run Study")
        render_study_run_controls(
            study_dir=study_dir,
            manifest=manifest,
            study_type="eos",
            owner_key="eos",
        )
        st.divider()

        col_display, col_btn = st.columns([3, 1])
        with col_btn:
            if st.button("Refresh Results", key="refresh_eos", use_container_width=True):
                summary = refresh_eos_study(study_dir)
            else:
                summary = load_eos_study_summary(study_dir)
                if _eos_summary_needs_refresh(study_dir, summary):
                    summary = refresh_eos_study(study_dir)

        if not summary:
            st.error("Could not parse study results.")
            return

        status = summary.get("status", "unknown")
        points = summary.get("points", [])
        fit = summary.get("fit", {})
        fitted_cell_atom_count, atom_count_warning = _resolve_eos_fitted_cell_atom_count(manifest, summary)
        available_display_modes = _get_available_eos_display_modes(
            natoms_in_fitted_cell=fitted_cell_atom_count,
        )
        current_display_mode = _normalize_eos_display_mode(
            st.session_state.get(EOS_DISPLAY_MODE_WIDGET_KEY, _EOS_DISPLAY_MODE_CELL_TOTAL),
            natoms_in_fitted_cell=fitted_cell_atom_count,
        )
        st.session_state[EOS_DISPLAY_MODE_WIDGET_KEY] = current_display_mode

        with col_display:
            display_mode = st.selectbox(
                "EOS display mode",
                options=available_display_modes,
                index=available_display_modes.index(current_display_mode),
                format_func=lambda mode: _EOS_DISPLAY_MODE_LABELS[mode],
                key=EOS_DISPLAY_MODE_WIDGET_KEY,
            )
            st.caption(
                "Internal fit basis: the EOS fit is always performed on total energy (eV) and total volume (Å³) "
                "of the simulated fitted cell. This selector changes display values only, and relative mode shows "
                "energy relative to fitted E0."
            )
            if fitted_cell_atom_count is not None:
                st.caption(f"Fitted-cell atom count: {fitted_cell_atom_count}")
            if atom_count_warning:
                st.warning(atom_count_warning)

        st.markdown(f"**Study Status:** `{status}`")
        if status == "running":
            st.info("⏳ Calculation is still running or outputs are missing.")

        # Plotly chart
        fig = plot_eos_study(summary, display_mode=display_mode)
        st.plotly_chart(fig, use_container_width=True)

        # Summary Cards
        st.markdown("### Fit Summary")
        if fit.get("success"):
            fit_display = _get_eos_fit_display_values(
                fit,
                display_mode=display_mode,
                natoms_in_fitted_cell=fitted_cell_atom_count,
            )
            f1, f2, f3, f4 = st.columns(4)
            volume_value = fit_display.get("volume_value")
            energy_value = fit_display.get("energy_value")
            bulk_modulus_value = fit_display.get("bulk_modulus_value")
            f1.metric(
                str(fit_display["volume_label"]),
                f"{float(volume_value):.2f} {fit_display['volume_unit']}" if volume_value is not None else "Unavailable",
            )
            energy_display_text = fit_display.get("energy_display_text")
            if energy_display_text is not None:
                f2.metric(str(fit_display["energy_label"]), str(energy_display_text))
            else:
                f2.metric(
                    str(fit_display["energy_label"]),
                    f"{float(energy_value):.4f} {fit_display['energy_unit']}" if energy_value is not None else "Unavailable",
                )
            f3.metric(
                str(fit_display["bulk_modulus_label"]),
                f"{float(bulk_modulus_value):.1f} GPa" if bulk_modulus_value is not None else "Unavailable",
            )
            f4.metric("Fit Model", fit.get("model", "birch_murnaghan").replace("_", " ").title())
            energy_note = fit_display.get("energy_note")
            if energy_note:
                st.caption(str(energy_note))

            equilibrium_geometry = fit.get("equilibrium_geometry", {})
            conventional_geometry = equilibrium_geometry.get("conventional_cell", {})
            if isinstance(conventional_geometry, dict) and conventional_geometry.get("available"):
                st.caption(
                    "Standardized conventional-cell values below are derived from the fitted equilibrium structure. "
                    "They do not change the internal EOS fit basis."
                )
                g1, g2, g3, g4 = st.columns(4)
                g1.metric(
                    "Equilibrium Volume (V0, standardized conventional cell)",
                    f"{float(conventional_geometry['volume']):.2f} Å³",
                )
                g2.metric(
                    "Lattice a (standardized conventional cell)",
                    f"{float(conventional_geometry['a']):.4f} Å",
                )
                g3.metric(
                    "Lattice b (standardized conventional cell)",
                    f"{float(conventional_geometry['b']):.4f} Å",
                )
                g4.metric(
                    "Lattice c (standardized conventional cell)",
                    f"{float(conventional_geometry['c']):.4f} Å",
                )
                st.caption(
                    "Angles: "
                    f"α = {float(conventional_geometry['alpha']):.2f}°, "
                    f"β = {float(conventional_geometry['beta']):.2f}°, "
                    f"γ = {float(conventional_geometry['gamma']):.2f}°. "
                    f"Standardized conventional-cell atom count: {int(conventional_geometry['atom_count'])}."
                )

            geometry_warnings = equilibrium_geometry.get("warnings", [])
            for warning in geometry_warnings:
                st.warning(f"⚠️ {warning}")
            
            for warning in fit.get("warnings", []):
                st.warning(f"⚠️ {warning}")
        else:
            st.error("Could not fit Equation of State to the provided points.")
            for err in fit.get("errors", []):
                st.error(f"❌ {err}")

        # Apply logic
        if fit.get("success") and fit.get("V0") is not None:
            st.markdown("### Apply Equilibrium")
            st.info(
                "Applying the equilibrium volume uses the simulated fitted-cell equilibrium volume from the internal EOS fit. "
                "Applying the lattice parameter uses the standardized conventional-cell lattice a derived from the fitted equilibrium structure. "
                "Both actions are independent of the current display mode and will update your active structure by isotropic scaling. "
                "It may require re-resolving pseudopotentials or invalidating other studies."
            )
            conventional_geometry = fit.get("equilibrium_geometry", {}).get("conventional_cell", {})
            target_lattice_a = (
                float(conventional_geometry["a"])
                if isinstance(conventional_geometry, dict)
                and conventional_geometry.get("available")
                and conventional_geometry.get("a") is not None
                else None
            )

            apply_col1, apply_col2 = st.columns(2)
            with apply_col1:
                if st.button("Apply Equilibrium Volume to Active Structure", type="primary", use_container_width=True):
                    current_volume = ctx.atoms.get_volume() if ctx.atoms is not None else None
                    target_volume = fit.get("V0")
                    if current_volume is None or not math.isfinite(float(current_volume)) or float(current_volume) <= 0:
                        st.error("QE Studio could not apply the EOS equilibrium volume because the current active-cell volume is unavailable or invalid.")
                    elif target_volume is None or not math.isfinite(float(target_volume)) or float(target_volume) <= 0:
                        st.error("QE Studio could not apply the EOS equilibrium volume because the fitted V0 value is unavailable or invalid.")
                    else:
                        volume_ratio = float(target_volume) / float(current_volume)
                        scale_factor = volume_ratio ** (1.0 / 3.0)
                        _apply_scaled_raw_structure(
                            scale_factor=scale_factor,
                            success_message=f"Successfully applied equilibrium volume {float(target_volume):.2f} Å³. Active structure updated.",
                        )
            with apply_col2:
                apply_lattice_button_disabled = target_lattice_a is None
                if st.button(
                    "Apply Lattice Parameter to Active Structure",
                    use_container_width=True,
                    disabled=apply_lattice_button_disabled,
                ):
                    current_lattice_a, lattice_warning = _resolve_standardized_conventional_lattice_a(ctx.raw_atoms)
                    if lattice_warning:
                        st.error(lattice_warning)
                    elif target_lattice_a is None:
                        st.error("QE Studio could not apply the EOS lattice parameter because the fitted standardized conventional-cell lattice a is unavailable.")
                    else:
                        scale_factor = float(target_lattice_a) / float(current_lattice_a)
                        _apply_scaled_raw_structure(
                            scale_factor=scale_factor,
                            success_message=(
                                "Successfully applied standardized conventional-cell lattice "
                                f"a = {float(target_lattice_a):.4f} Å. Active structure updated."
                            ),
                        )
                if apply_lattice_button_disabled:
                    st.caption("The lattice-parameter action is unavailable because this EOS study does not include a standardized conventional-cell lattice a.")
                elif target_lattice_a is not None:
                    st.caption(
                        "This action isotropically rescales the active structure so its standardized conventional-cell "
                        f"lattice a matches the fitted equilibrium value of {float(target_lattice_a):.4f} Å."
                    )

        # Render points table
        st.caption(
            "Fit basis note: the EOS fit is always performed on simulated fitted-cell totals. "
            "Per-atom values and any relative-energy view are display-derived only, with relative mode referenced to fitted E0."
        )
        with st.expander("View Raw Data Points"):
            render_table(
                [
                    {
                        "Scale": f"{p['scale_factor']:.4f}",
                        "Volume (Å³, fitted cell)": f"{p['volume']:.2f}",
                        "Volume/atom (Å³)": (
                            f"{float(p['volume']) / fitted_cell_atom_count:.2f}"
                            if fitted_cell_atom_count is not None and p.get("volume") is not None
                            else "Unavailable"
                        ),
                        "Status": p.get("status", "unknown"),
                        "Total Energy (eV, fitted cell)": (
                            f"{float(p.get('total_energy_ev')):.6f}" if p.get("total_energy_ev") is not None else "-"
                        ),
                        "Energy/atom (eV)": (
                            f"{float(p.get('total_energy_ev')) / fitted_cell_atom_count:.6f}"
                            if fitted_cell_atom_count is not None and p.get("total_energy_ev") is not None
                            else "-"
                        ),
                        "Directory": p.get("run_dir_name", ""),
                    }
                    for p in points
                ]
            )
