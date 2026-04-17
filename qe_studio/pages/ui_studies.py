from __future__ import annotations

import math
from pathlib import Path

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
from .shared import render_path_captions, render_status_messages, render_table
from .ui_setup import (
    _build_generation_context_from_parameters,
    _sanitize_saved_qe_parameters,
    _validate_qe_submission_parameters,
    default_qe_parameters,
    get_qe_parameters,
    QE_PARAMS_STATE_KEY,
)
from ..services.plotting.study_plots import plot_cutoff_convergence, plot_kpoint_convergence
from ..services.studies.study_summary_view import detect_baseline_drift

_DEFAULT_CUTOFF_TOLERANCE_MEV_PER_ATOM = 1.0

_ECUTRHO_MODE_LABELS = {
    EcutrhoMode.METADATA_DUAL.value: "Pseudo metadata dual ratios",
    EcutrhoMode.RATIO_RULE.value: "Ratio rule",
    EcutrhoMode.MANUAL_ABSOLUTE.value: "Manual absolute ecutrho",
    EcutrhoMode.MANUAL_RATIO.value: "Manual ecutrho ratio",
}


def _coerce_positive_float(value: object, default: float) -> float:
    """Return a positive finite float or the supplied default."""
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(candidate) or candidate <= 0:
        return float(default)
    return candidate


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

    refresh_clicked = st.button("Refresh Cutoff Study Results", use_container_width=True)
    
    try:
        manifest = load_cutoff_study_manifest(study_dir)
    except Exception as exc:
        st.warning(f"Unable to read the study manifest: {exc}")
        return

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


KPOINT_STUDY_PARAMS_STATE_KEY = "kpoint_study_params"
KPOINT_STUDY_SELECTED_PATH_STATE_KEY = "kpoint_study_selected_path"
KPOINT_STUDY_LATEST_PATH_STATE_KEY = "kpoint_study_latest_path"

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

    refresh_clicked = st.button("Refresh K-Point Study Results", use_container_width=True)
    
    try:
        manifest = load_kpoint_study_manifest(study_dir)
    except Exception as exc:
        st.warning(f"Unable to read the study manifest: {exc}")
        return

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
        st.info("Equation of State (EOS) workflow is not yet implemented.")
