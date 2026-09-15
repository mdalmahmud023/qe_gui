from __future__ import annotations

from ..services.baseline_defaults import default_qe_parameters

from dataclasses import dataclass
import math
from pathlib import Path

import streamlit as st

from ..convergence import inspect_metadata_dual_support
from ..core.project_context import ProjectContext
from ..core.project_paths import session_input_matches_workspace
from ..core.state_keys import (
    ATOMS_STATE_KEY,
    DEFAULT_PSEUDO_SETTINGS,
    PSEUDO_DIR_STATE_KEY,
    PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY,
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
    QE_CALCULATION_OPTIONS,
    QE_INPUT_INTERNAL_PATH_STATE_KEY,
    QE_INPUT_TEXT_STATE_KEY,
    QE_INPUT_UI_PATH_STATE_KEY,
    QE_OCCUPATIONS_OPTIONS,
    QE_PARAMS_STATE_KEY,
    QE_SMEARING_OPTIONS,
)
from ..core.structure_state import (
    clear_generated_qe_input_state,
    clear_pseudopotential_state,
    get_atomic_positions_coordinate_mode,
    get_current_active_structure_signature,
    get_current_pseudo_settings,
    get_current_source_metadata,
    get_qe_generation_blocker_message,
    get_workspace_pseudopotential_blocker,
    get_structure_mode,
    record_generated_input_provenance,
    set_resolution_status,
    sync_pseudopotential_settings_state,
)
from ..core.workspace import QE_RUNS_DIR
from ..services.input_generator import (
    CalculationContext,
    ElectronicCharacter,
    HubbardUSettings,
    HubbardUSpeciesParameter,
    VdwMethod,
    VdwSettings,
    build_calculation_context_from_request,
    classify_structure,
    get_atomic_species_order,
    generate_workflow_inputs,
    make_default_context,
    select_cutoffs,
)
from ..services.pseudopotentials import (
    PseudopotentialResolutionError,
    clear_pseudopotential_family_cache,
    get_structure_signature,
    get_unique_elements,
    resolve_fully_relativistic_pseudopotentials,
    resolve_sssp_pseudopotentials,
)
from ..services.qe_control import DEFAULT_OUTDIR, default_qe_prefix
from ..services.qe_runner import RUN_ACTIVE_STATE_KEY, reset_run_state
from ..services.reproducibility import (
    ValidationIssue,
    build_generation_validation_issues,
    build_input_reproducibility_metadata,
    write_input_reproducibility_metadata,
)
from .shared import render_path_captions, render_status_messages, render_table

# Internal state key for the conv_thr widget (not yet in state_keys.py to
# avoid a breaking migration; added here as a local constant).
_QE_CONV_THR_STATE_KEY = "qe_conv_thr"
_PSEUDO_WIDGET_SYNC_STATE_KEY = "_pseudo_widget_sync_pending"
_DEFAULT_CONV_THR = 1.0e-6
_DEFAULT_MIXING_BETA = 0.7
_DEFAULT_CUTOFF_TOLERANCE_MEV_PER_ATOM = 1.0
_VDW_METHOD_OPTIONS = tuple(VdwMethod)
_VDW_METHOD_LABELS: dict[VdwMethod, str] = {
    VdwMethod.NONE: "None",
    VdwMethod.D2: "Grimme D2",
    VdwMethod.D3_ZERO: "Grimme D3 (zero damping)",
    VdwMethod.D3_BJ: "Grimme D3 (BJ damping)",
    VdwMethod.TS: "Tkatchenko-Scheffler (TS)",
    VdwMethod.MBD: "Many-body dispersion (MBD)",
    VdwMethod.VDW_DF: "vdW-DF",
    VdwMethod.VDW_DF2: "vdW-DF2",
    VdwMethod.RVV10: "rVV10",
}


@dataclass(frozen=True)
class GenerationPersistResult:
    success: bool
    validation_issues: tuple[ValidationIssue, ...]
    qe_input_text: str | None = None
    internal_path: Path | None = None
    ui_path: Path | None = None
    metadata_path: Path | None = None
    generation_context: CalculationContext | None = None
    error_message: str | None = None





def _normalize_pseudopotential_widget_settings(settings: dict | None) -> dict[str, object]:
    """Normalize stored pseudo settings into supported widget values."""
    settings = dict(settings or {})
    return {
        "functional": (
            settings.get("functional")
            if settings.get("functional") in PSEUDO_FUNCTIONAL_OPTIONS
            else DEFAULT_PSEUDO_SETTINGS["functional"]
        ),
        "mode": (
            settings.get("mode")
            if settings.get("mode") in PSEUDO_MODE_OPTIONS
            else DEFAULT_PSEUDO_SETTINGS["mode"]
        ),
        "version": (
            settings.get("version")
            if settings.get("version") in PSEUDO_VERSION_OPTIONS
            else DEFAULT_PSEUDO_SETTINGS["version"]
        ),
        "fully_relativistic": bool(
            settings.get(
                "fully_relativistic",
                DEFAULT_PSEUDO_SETTINGS["fully_relativistic"],
            )
        ),
    }


def _apply_pending_pseudopotential_widget_sync() -> None:
    """Apply a queued pseudo-widget sync before the widgets are rendered."""
    pending = st.session_state.pop(_PSEUDO_WIDGET_SYNC_STATE_KEY, None)
    if pending is None:
        return
    normalized = _normalize_pseudopotential_widget_settings(pending)
    st.session_state[PSEUDO_FUNCTIONAL_WIDGET_KEY] = normalized["functional"]
    st.session_state[PSEUDO_MODE_WIDGET_KEY] = normalized["mode"]
    st.session_state[PSEUDO_VERSION_WIDGET_KEY] = normalized["version"]
    st.session_state[PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY] = normalized["fully_relativistic"]


def _queue_pseudopotential_widget_sync(settings: dict | None) -> None:
    """Schedule pseudo widget state to be synchronized on the next rerun."""
    st.session_state[_PSEUDO_WIDGET_SYNC_STATE_KEY] = _normalize_pseudopotential_widget_settings(settings)


def render_page(ctx: ProjectContext) -> None:
    """Render the Setup page."""
    st.subheader("Setup")
    st.caption("Resolve pseudopotentials, configure the base Quantum ESPRESSO input, and review the generated `pw.in`.")

    if not ctx.structure.loaded:
        st.info(
            "No structure is loaded in this session. Load or build a structure on the Structure page to enable pseudopotential resolution and new QE input generation."
        )
        if ctx.input_files.available:
            st.caption("An existing generated input is still available from session state or disk.")

    render_pseudopotentials_section(ctx)
    st.divider()
    render_qe_input_form(ctx)
    st.divider()
    render_generated_qe_input_preview(ctx)
    st.divider()
    st.info("Navigate to the Studies page to configure and run convergence studies.")


def render_pseudopotential_status() -> None:
    """Render pseudopotential status messaging and the resolved table."""
    status = st.session_state.get(PSEUDO_RESOLUTION_STATUS_STATE_KEY)
    rows = st.session_state.get(PSEUDO_RESOLVED_ROWS_STATE_KEY, [])
    messages: list[tuple[str, str]] = []

    if status:
        message = status.get("message")
        if status.get("state") == "resolved" and message:
            messages.append(("success", message))
        elif status.get("state") == "stale" and message:
            messages.append(("warning", message))
        elif status.get("state") == "error" and message:
            messages.append(("error", message))

        for warning_message in status.get("warnings", []):
            messages.append(("warning", warning_message))
    elif rows:
        messages.append(("info", "Resolved pseudopotential data is available."))
    else:
        messages.append(("info", "Resolve pseudopotentials to enable runnable Quantum ESPRESSO input generation."))

    render_status_messages(messages)

    if rows:
        with st.expander("Resolved Pseudopotential Table", expanded=True):
            st.dataframe(rows, use_container_width=True, hide_index=True)


def render_pseudopotentials_section(ctx: ProjectContext) -> None:
    """Render the pseudopotential controls and resolution results."""
    st.markdown("### Pseudopotentials")
    st.caption("Resolve and cache pseudopotentials before generating runnable QE inputs.")

    _apply_pending_pseudopotential_widget_sync()
    structure_loaded = ctx.atoms is not None
    saved_pseudo_settings = st.session_state.get(PSEUDO_SETTINGS_STATE_KEY, DEFAULT_PSEUDO_SETTINGS)
    current_defaults = _normalize_pseudopotential_widget_settings(saved_pseudo_settings)

    top_row_col1, top_row_col2 = st.columns(2)
    top_row_col1.selectbox(
        "Functional",
        options=PSEUDO_FUNCTIONAL_OPTIONS,
        index=PSEUDO_FUNCTIONAL_OPTIONS.index(current_defaults["functional"]),
        key=PSEUDO_FUNCTIONAL_WIDGET_KEY,
        disabled=not structure_loaded,
    )
    top_row_col2.selectbox(
        "Mode",
        options=PSEUDO_MODE_OPTIONS,
        index=PSEUDO_MODE_OPTIONS.index(current_defaults["mode"]),
        key=PSEUDO_MODE_WIDGET_KEY,
        disabled=not structure_loaded,
    )

    with st.expander("Advanced pseudopotential options", expanded=False):
        st.selectbox(
            "Version",
            options=PSEUDO_VERSION_OPTIONS,
            index=PSEUDO_VERSION_OPTIONS.index(current_defaults["version"]),
            key=PSEUDO_VERSION_WIDGET_KEY,
            disabled=not structure_loaded,
        )
        st.checkbox(
            "Fully relativistic (SOC-ready, SG15 PBE)",
            value=bool(current_defaults["fully_relativistic"]),
            key=PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY,
            disabled=not structure_loaded,
        )
        current_use_cache = st.session_state.get(PSEUDO_USE_CACHE_WIDGET_KEY, True)
        st.checkbox(
            "Use cached files if available",
            value=bool(current_use_cache),
            key=PSEUDO_USE_CACHE_WIDGET_KEY,
            disabled=not structure_loaded,
        )
        clear_cache_clicked = st.button(
            "Clear pseudopotential cache",
            use_container_width=True,
            disabled=not structure_loaded,
        )

    if not structure_loaded:
        render_pseudopotential_status()
        return

    sync_pseudopotential_settings_state()
    current_settings = get_current_pseudo_settings()
    use_cache = bool(st.session_state.get(PSEUDO_USE_CACHE_WIDGET_KEY, True))
    fully_relativistic = bool(current_settings.get("fully_relativistic", False))
    elements = get_unique_elements(ctx.atoms)
    st.write(f"Detected elements: {', '.join(elements)}")
    if fully_relativistic:
        st.info(
            "Fully relativistic resolution uses the official SG15 ONCV PBE FR library when files are available. "
            "The SSSP mode/version selectors are ignored in this mode."
        )
        if current_settings["functional"] != "PBE":
            st.warning("Fully relativistic resolution currently supports the `PBE` functional only.")

    if clear_cache_clicked:
        clear_pseudopotential_family_cache(
            functional=current_settings["functional"],
            mode=current_settings["mode"],
            version=current_settings["version"],
            fully_relativistic=fully_relativistic,
            cache_root=ctx.paths.pseudo_base_dir if fully_relativistic else ctx.paths.sssp_cache_dir,
        )
        clear_pseudopotential_state(clear_structure_signature=False)
        st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = get_structure_signature(ctx.atoms)
        st.success("Cleared cached pseudopotentials for the selected family.")

    resolve_disabled = fully_relativistic and current_settings["functional"] != "PBE"
    resolve_clicked = st.button(
        "Resolve Pseudopotentials",
        use_container_width=True,
        disabled=resolve_disabled,
    )
    if resolve_clicked:
        clear_generated_qe_input_state()
        progress_status = st.empty()
        progress_bar = st.progress(0)

        def update_resolution_progress(fraction: float, message: str) -> None:
            percent = max(0, min(100, int(round(fraction * 100))))
            progress_bar.progress(percent)
            progress_status.info(f"{message} ({percent}%)")

        try:
            if fully_relativistic:
                resolution_result = resolve_fully_relativistic_pseudopotentials(
                    ctx.atoms,
                    functional=current_settings["functional"],
                    cache_root=ctx.paths.pseudo_base_dir,
                    use_cache=use_cache,
                    progress_callback=update_resolution_progress,
                )
            else:
                resolution_result = resolve_sssp_pseudopotentials(
                    ctx.atoms,
                    version=current_settings["version"],
                    functional=current_settings["functional"],
                    mode=current_settings["mode"],
                    cache_root=ctx.paths.sssp_cache_dir,
                    use_cache=use_cache,
                    progress_callback=update_resolution_progress,
                )
        except PseudopotentialResolutionError as exc:
            progress_bar.empty()
            progress_status.empty()
            set_resolution_status(
                state="error",
                message=f"Failed to resolve pseudopotentials: {exc}",
            )
        except Exception as exc:
            progress_bar.empty()
            progress_status.empty()
            set_resolution_status(
                state="error",
                message=f"Unexpected error while resolving pseudopotentials: {exc}",
            )
        else:
            progress_bar.empty()
            progress_status.empty()
            st.session_state[PSEUDO_DIR_STATE_KEY] = resolution_result["pseudo_dir"]
            st.session_state[PSEUDO_RESOLVED_MAP_STATE_KEY] = resolution_result["ase_pseudopotentials"]
            st.session_state[PSEUDO_RESOLVED_ROWS_STATE_KEY] = resolution_result["table_rows"]
            st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = resolution_result["structure_signature"]
            st.session_state[PSEUDO_SETTINGS_STATE_KEY] = resolution_result["settings"]
            _queue_pseudopotential_widget_sync(resolution_result["settings"])
            # Store raw manifest entries so the input form can display SSSP-
            # recommended cutoffs as caption hints without a separate lookup.
            st.session_state["pseudo_resolved_entries"] = resolution_result["resolved_entries"]
            # Clear previously saved form values so the fields re-initialise
            # from the freshly resolved SSSP cutoffs on re-render.
            st.session_state.pop(QE_PARAMS_STATE_KEY, None)

            set_resolution_status(
                state="resolved",
                message=(
                    "Resolved pseudopotentials for "
                    f"{', '.join(resolution_result['ase_pseudopotentials'])}."
                ),
                warnings=resolution_result["warnings"],
                resolved_settings=resolution_result["settings"],
                resolved_structure_signature=resolution_result["structure_signature"],
            )
            st.rerun()

    render_pseudopotential_status()


def render_qe_input_form(ctx: ProjectContext) -> None:
    """Render the QE input form and gate runnable generation on pseudo resolution."""
    resolved_entries = st.session_state.get("pseudo_resolved_entries") or {}
    metadata_dual_info = inspect_metadata_dual_support(resolved_entries)
    current_pseudo_settings = get_current_pseudo_settings()
    blocker_message = get_workspace_pseudopotential_blocker(ctx.paths) or get_qe_generation_blocker_message(ctx.atoms)
    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))
    submit_block_reason = _get_generation_submit_block_reason(
        blocker_message=blocker_message,
        run_active=run_active,
    )

    cutoff_decision = None
    if resolved_entries and ctx.atoms is not None:
        try:
            cutoff_decision = select_cutoffs(resolved_entries)
        except Exception:
            pass

    st.markdown("### QE Input Parameters")
    st.caption("Configure either a standard `pw.x` input or a reproducible cutoff convergence study.")

    defaults = get_qe_parameters(resolved_entries=resolved_entries or None, atoms=ctx.atoms)
    default_k_grid = defaults["k_points_grid"]

    if blocker_message:
        if ctx.atoms is None:
            st.info(blocker_message)
        else:
            st.warning(blocker_message)
    if ctx.input_files.provenance_message:
        st.warning(ctx.input_files.provenance_message)
    elif run_active:
        st.info("Wait for the active calculation to finish before generating a new `pw.in` file.")

    if ctx.atoms is not None:
        classification = classify_structure(ctx.atoms)
        if classification.electronic_character == ElectronicCharacter.UNKNOWN:
            st.warning(
                "The structure's electronic character (metal vs insulator) could not be "
                "confidently determined. Occupations and smearing have been set to a "
                "heuristic default. Please verify these settings."
            )

    with st.container():
        row_one_col1, row_one_col1_5, row_one_col2, row_one_col3 = st.columns(4)
        calculation = row_one_col1.selectbox(
            "Calculation Type",
            options=QE_CALCULATION_OPTIONS,
            index=QE_CALCULATION_OPTIONS.index(defaults["calculation"]),
        )
        occupations = row_one_col1_5.selectbox(
            "Occupations",
            options=QE_OCCUPATIONS_OPTIONS,
            index=QE_OCCUPATIONS_OPTIONS.index(defaults.get("occupations", "smearing")),
        )
        smearing_disabled = (occupations != "smearing")
        smearing = row_one_col2.selectbox(
            "Smearing Type",
            options=QE_SMEARING_OPTIONS,
            index=QE_SMEARING_OPTIONS.index(defaults["smearing"]),
            disabled=smearing_disabled,
        )
        degauss = row_one_col3.number_input(
            "Smearing Width (degauss)",
            min_value=0.0,
            value=float(defaults["degauss"]),
            step=0.001,
            format="%.4f",
            disabled=smearing_disabled,
        )

        row_control_col1, row_control_col2 = st.columns(2)
        prefix = row_control_col1.text_input(
            "Prefix",
            value=str(defaults.get("prefix", default_qe_prefix(ctx.atoms))),
            help="Quantum ESPRESSO prefix used for save directories and downstream post-processing discovery.",
        )
        outdir = row_control_col2.text_input(
            "Outdir",
            value=str(defaults.get("outdir", DEFAULT_OUTDIR)),
            help=f"QE scratch directory written into `&CONTROL`. Relative paths resolve from `{ctx.paths.run_dir}`.",
        )

        row_two_col1, row_two_col2 = st.columns(2)
        ecutwfc = row_two_col1.number_input(
            "Kinetic Energy Cutoff for Wavefunctions (ecutwfc, Ry)",
            min_value=0.0,
            value=float(defaults["ecutwfc"]),
            step=5.0,
            format="%.1f",
        )
        if cutoff_decision is not None:
            source_label = "SSSP metadata" if cutoff_decision.source == "sssp_metadata" else "heuristic fallback"
            row_two_col1.caption(
                f"Auto-set from {source_label}: **{cutoff_decision.ecutwfc_ry:.0f} Ry** "
                f"(max across {len(cutoff_decision.per_element)} element(s))"
            )
        ecutrho = row_two_col2.number_input(
            "Kinetic Energy Cutoff for Charge Density (ecutrho, Ry)",
            min_value=0.0,
            value=float(defaults["ecutrho"]),
            step=5.0,
            format="%.1f",
        )
        if cutoff_decision is not None:
            row_two_col2.caption(
                f"Auto-set from {source_label}: **{cutoff_decision.ecutrho_ry:.0f} Ry** "
                f"(cutoff_rho from SSSP manifest, not assumed 4× ecutwfc)"
            )

        st.markdown("**K-Points Grid**")
        row_three_col1, row_three_col2, row_three_col3 = st.columns(3)
        kx = row_three_col1.number_input("K-Points X", min_value=1, value=int(default_k_grid[0]), step=1)
        ky = row_three_col2.number_input("K-Points Y", min_value=1, value=int(default_k_grid[1]), step=1)
        kz = row_three_col3.number_input("K-Points Z", min_value=1, value=int(default_k_grid[2]), step=1)

        st.markdown("**Relativistic Settings**")
        soc_enabled = st.checkbox(
            "Enable spin-orbit coupling (noncolin + lspinorb)",
            value=bool(defaults.get("noncolin") or defaults.get("lspinorb")),
        )
        if soc_enabled:
            st.caption(
                "SOC currently uses the minimal noncollinear path. Collinear "
                "spin polarization and starting magnetization are disabled in this mode."
            )

        st.markdown("**Magnetic Settings**")
        nspin_val = defaults.get("nspin", 1)

        nspin_toggle = st.checkbox(
            "Spin-polarized calculation (nspin=2)",
            value=(nspin_val == 2 and not soc_enabled),
            disabled=soc_enabled,
        )

        default_starting_magnetization = defaults.get("starting_magnetization", {})
        starting_magnetization: dict[str, dict[str, object]] = {}
        if soc_enabled:
            st.caption("Starting magnetization is unavailable while SOC mode is enabled.")
        elif nspin_toggle:
            unique_elements = sorted({atom.symbol for atom in ctx.atoms}) if ctx.atoms else []
            if unique_elements:
                st.caption("Fractional initial magnetization (-1.0 to 1.0)")
                mag_cols = st.columns(min(len(unique_elements), 4))
                for i, elem in enumerate(unique_elements):
                    default_mag = float(default_starting_magnetization.get(elem, {}).get("value", 0.0))
                    with mag_cols[i % 4]:
                        mag_val = st.number_input(
                            f"{elem}",
                            min_value=-1.0,
                            max_value=1.0,
                            value=float(default_mag),
                            step=0.1,
                        )
                        starting_magnetization[elem] = {
                            "species": elem,
                            "value": mag_val,
                            "source": str(
                                default_starting_magnetization.get(elem, {}).get("source", "user_defined")
                            ),
                        }

        st.markdown("**DFT+U Settings**")
        dftu_enabled = st.checkbox(
            "Enable DFT+U (lda_plus_u)",
            value=bool(defaults.get("dftu_enabled", False)),
        )
        hubbard_u_parameters: dict[str, dict[str, object]] = {}
        if dftu_enabled:
            st.warning(HubbardUSettings.METHOD_WARNING)
            species_order = get_atomic_species_order(ctx.atoms) if ctx.atoms is not None else ()
            if not species_order:
                st.info("Load a structure to configure species-resolved Hubbard U values.")
            for species in species_order:
                default_entry = defaults.get("hubbard_u", {}).get(species, {})
                selected = st.checkbox(
                    f"Apply U to {species}",
                    value=species in defaults.get("hubbard_u", {}),
                )
                if not selected:
                    continue
                u_ev = st.number_input(
                    f"U for {species} (eV)",
                    min_value=0.0,
                    value=float(default_entry.get("u_ev", 0.0)),
                    step=0.1,
                    format="%.2f",
                )
                hubbard_u_parameters[species] = {
                    "species": species,
                    "u_ev": u_ev,
                    "source": str(default_entry.get("source", "user_defined")),
                }
            has_positive_hubbard_u = any(float(entry.get("u_ev", 0.0)) > 0.0 for entry in hubbard_u_parameters.values())
            if not has_positive_hubbard_u:
                st.info("Select at least one species and enter a positive U value to generate a DFT+U input.")

        st.markdown("**van der Waals Settings**")
        default_vdw_method = _normalize_vdw_method_value(defaults.get("vdw_method", VdwMethod.NONE.value))
        vdw_method = st.selectbox(
            "vdW correction method",
            options=_VDW_METHOD_OPTIONS,
            index=_VDW_METHOD_OPTIONS.index(default_vdw_method),
            format_func=_format_vdw_method_label,
        )
        if vdw_method is not VdwMethod.NONE:
            vdw_settings_preview = VdwSettings(method=vdw_method)
            if vdw_settings_preview.backend == "semiempirical":
                st.info(VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE)
            else:
                st.warning(VdwSettings.NONLOCAL_WARNING)

        st.markdown("")  # Spacing
        with st.expander("Advanced numerical settings", expanded=False):
            adv_col1, adv_col2 = st.columns(2)
            conv_thr = adv_col1.number_input(
                "SCF Convergence Threshold (conv_thr, Ry)",
                min_value=1.0e-14,
                max_value=1.0e-2,
                value=float(defaults.get("conv_thr", _DEFAULT_CONV_THR)),
                step=1.0e-8,
                format="%.2e",
                help=(
                    "Energy convergence criterion for the SCF cycle.  "
                    "1e-6 Ry is typical for energies; tighten to 1e-8 Ry for forces or phonons."
                ),
            )
            mixing_beta = adv_col2.number_input(
                "Charge Density Mixing (mixing_beta)",
                min_value=0.01,
                max_value=1.0,
                value=float(defaults.get("mixing_beta", _DEFAULT_MIXING_BETA)),
                step=0.05,
                format="%.2f",
                help=(
                    "Linear mixing coefficient.  Reduce to 0.3–0.4 for magnetic, "
                    "oxide, or defect systems."
                ),
            )

        common_parameters = {
            "calculation": calculation,
            "prefix": prefix,
            "outdir": outdir,
            "occupations": occupations,
            "smearing": smearing,
            "degauss": degauss,
            "k_points_grid": [kx, ky, kz],
            "conv_thr": conv_thr,
            "mixing_beta": mixing_beta,
            "nspin": 1 if soc_enabled else (2 if nspin_toggle else 1),
            "starting_magnetization": {} if soc_enabled else starting_magnetization,
            "noncolin": bool(soc_enabled),
            "lspinorb": bool(soc_enabled),
            "dftu_enabled": bool(dftu_enabled),
            "hubbard_u": hubbard_u_parameters,
            "vdw_method": vdw_method.value,
        }
        common_parameters["prefix_source"] = (
            "auto" if str(prefix).strip() == str(defaults.get("prefix", "")).strip() else "user"
        )
        common_parameters["outdir_source"] = (
            "auto" if str(outdir).strip() == str(defaults.get("outdir", "")).strip() else "user"
        )
        common_parameters["spin_source"] = (
            "auto"
            if common_parameters["nspin"] == defaults.get("nspin", 1)
            and _flatten_starting_magnetization_state(common_parameters["starting_magnetization"])
            == _flatten_starting_magnetization_state(default_starting_magnetization)
            else "user"
        )
        preview_issues: list[ValidationIssue] = []
        preview_warnings: list[str] = []
        validation_error: str | None = None

        qe_parameters = {
            **common_parameters,
            "ecutwfc": ecutwfc,
            "ecutrho": ecutrho,
        }
        st.session_state[QE_PARAMS_STATE_KEY] = qe_parameters
        
        validation_error = _validate_qe_submission_parameters(qe_parameters)
        if ctx.atoms is not None and validation_error is None:
            try:
                preview_context = _build_generation_context_from_parameters(
                    ctx.atoms,
                    qe_parameters,
                    resolved_entries=resolved_entries or None,
                )
            except Exception:
                preview_context = None
            else:
                preview_issues = build_generation_validation_issues(
                    ctx.atoms,
                    preview_context,
                    requested_workflow=qe_parameters["calculation"],
                )

        blocking_preview_issues = [issue for issue in preview_issues if issue.level == "error"]
        warning_preview_issues = [issue for issue in preview_issues if issue.level == "warning"]
        warning_preview_issues = [issue for issue in preview_issues if issue.level == "warning"]
        if warning_preview_issues:
            st.markdown("**Warnings**")
            _render_validation_issues(warning_preview_issues)
        if blocking_preview_issues:
            st.markdown("**Blocking Errors**")
            _render_validation_issues(blocking_preview_issues)

        submitted = st.button(
            "Generate Input File",
            use_container_width=True,
            disabled=bool(submit_block_reason or blocking_preview_issues),
        )
        if submit_block_reason:
            st.caption(f"Generation is currently blocked: {submit_block_reason}")
        elif blocking_preview_issues:
            st.caption("Generation is currently blocked by the issues listed above.")

    if not submitted:
        return

    if submit_block_reason:
        if run_active:
            st.info(submit_block_reason)
        elif ctx.atoms is None:
            st.info(submit_block_reason)
        else:
            st.warning(submit_block_reason)
        return

    if validation_error:
        st.error(f"Failed to generate the Quantum ESPRESSO input file: {validation_error}")
        return

    pseudopotentials = st.session_state.get(PSEUDO_RESOLVED_MAP_STATE_KEY)
    pseudo_dir = st.session_state.get(PSEUDO_DIR_STATE_KEY)
    atoms = st.session_state.get(ATOMS_STATE_KEY)
    if atoms is None or not pseudopotentials or not pseudo_dir:
        st.error("Resolve pseudopotentials before generating a runnable Quantum ESPRESSO input file.")
        return

    result = _generate_and_persist_qe_input(
        atoms,
        qe_parameters,
        pseudopotentials,
        pseudo_dir,
        run_dir=ctx.paths.run_dir,
        current_pseudo_settings=current_pseudo_settings,
        resolved_entries=resolved_entries or None,
        source_metadata=get_current_source_metadata(),
        structure_signature=get_current_active_structure_signature(),
        structure_mode=get_structure_mode(),
    )
    if not result.success:
        if result.validation_issues:
            st.markdown("**Blocking Errors**")
            _render_validation_issues([issue for issue in result.validation_issues if issue.level == "error"])
        if result.error_message:
            st.error(result.error_message)
        return

    assert result.qe_input_text is not None
    assert result.internal_path is not None
    assert result.ui_path is not None
    assert result.metadata_path is not None
    assert result.generation_context is not None

    reset_run_state(st.session_state)
    st.session_state[QE_PARAMS_STATE_KEY] = qe_parameters
    st.session_state[QE_INPUT_TEXT_STATE_KEY] = result.qe_input_text
    st.session_state[QE_INPUT_INTERNAL_PATH_STATE_KEY] = str(result.internal_path)
    st.session_state[QE_INPUT_UI_PATH_STATE_KEY] = str(result.ui_path)
    record_generated_input_provenance(
        ctx.paths.run_dir,
        ui_path=str(result.ui_path),
        internal_path=str(result.internal_path),
        metadata_path=str(result.metadata_path),
    )
    st.success(f"Generated updated QE input files under `{ctx.paths.run_dir}`.")
    return



def render_generated_qe_input_preview(ctx: ProjectContext) -> None:
    """Render the generated QE input preview if available."""
    st.markdown("### Generated Input")
    if not ctx.input_files.available:
        st.info("Generate `pw.in` from this page to preview the runnable QE input.")
        return

    preview_text = load_generated_input_preview_text(ctx)
    if preview_text is None:
        st.warning("The latest `pw.in` preview is unavailable. Generate it again from this page.")
        return

    st.success(
        "Generated Quantum ESPRESSO input files are available in "
        f"`{ctx.input_files.internal_path}` and `{ctx.input_files.ui_path}`."
    )
    st.caption(f"Preview source: `{ctx.input_files.preview_source}`")
    with st.expander("`pw.in` Preview", expanded=True):
        st.code(preview_text, language="fortran")





def load_generated_input_preview_text(ctx: ProjectContext) -> str | None:
    """Load the current `pw.in` preview from session state or disk."""
    preview_text = st.session_state.get(QE_INPUT_TEXT_STATE_KEY)
    if preview_text and session_input_matches_workspace(st.session_state, ctx.paths):
        return preview_text
    if ctx.input_files.ui_path.is_file():
        return ctx.input_files.ui_path.read_text(encoding="utf-8")
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


def _default_starting_magnetization_state(starting_magnetization: dict[str, float]) -> dict[str, dict[str, object]]:
    """Return the saved-form representation for species-wise starting magnetization."""
    return {
        species: {
            "species": species,
            "value": float(value),
            "source": "heuristic_default",
        }
        for species, value in starting_magnetization.items()
    }


def _flatten_starting_magnetization_state(raw_state: dict | None) -> dict[str, float]:
    """Return a simple species-to-float mapping from the saved-form spin payload."""
    flattened: dict[str, float] = {}
    if not isinstance(raw_state, dict):
        return flattened
    for fallback_species, payload in raw_state.items():
        if isinstance(payload, dict):
            species = str(payload.get("species") or fallback_species)
            raw_value = payload.get("value", 0.0)
        else:
            species = str(fallback_species)
            raw_value = payload
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            flattened[species] = value
    return flattened


def _normalize_starting_magnetization_state(
    raw_state: dict | None,
    *,
    species_order: tuple[str, ...],
    default_state: dict[str, dict[str, object]],
    spin_source: str,
) -> tuple[dict[str, dict[str, object]], bool]:
    """Normalize saved spin state and report whether stale species were pruned."""
    requested = dict(raw_state or {})
    normalized: dict[str, dict[str, object]] = {}
    pruned = False

    if spin_source == "auto":
        for species in species_order:
            if species in default_state:
                normalized[species] = dict(default_state[species])
        return normalized, False

    for species in species_order:
        payload = requested.get(species)
        if payload is None:
            continue
        if isinstance(payload, dict):
            try:
                value = float(payload.get("value", 0.0))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            normalized[species] = {
                "species": species,
                "value": value,
                "source": str(payload.get("source") or "user_defined"),
            }
        else:
            try:
                value = float(payload)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            normalized[species] = {
                "species": species,
                "value": value,
                "source": "user_defined",
            }

    pruned = bool(set(requested) - set(species_order))
    return normalized, pruned


def _normalize_saved_hubbard_u_state(
    raw_state: dict | None,
    *,
    species_order: tuple[str, ...],
) -> tuple[dict[str, dict[str, object]], bool]:
    """Normalize saved Hubbard U payloads while preserving zero values."""
    requested = dict(raw_state or {})
    normalized: dict[str, dict[str, object]] = {}
    for species in species_order:
        payload = requested.get(species)
        if not isinstance(payload, dict):
            continue
        try:
            u_ev = float(payload.get("u_ev", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(u_ev):
            continue
        normalized[species] = {
            "species": species,
            "u_ev": u_ev,
            "source": str(payload.get("source") or "user_defined"),
        }
    return normalized, bool(set(requested) - set(species_order))


def _infer_saved_setting_source(
    explicit_source: object,
    *,
    current_value: object,
    default_value: object,
) -> str:
    """Infer whether one saved setting should keep following auto defaults."""
    normalized = str(explicit_source or "").strip().lower()
    if normalized in {"auto", "user"}:
        return normalized
    return "auto" if current_value == default_value else "user"


def get_qe_parameters(resolved_entries: dict | None = None, atoms=None) -> dict:
    """Return the current QE parameters from session state or physics-aware defaults.

    If no user-saved parameters exist yet, computes defaults from the
    resolved SSSP entries so that the input fields are pre-populated with
    the correct SSSP-recommended cutoffs rather than hardcoded fallbacks.
    """
    saved = st.session_state.get(QE_PARAMS_STATE_KEY)
    if saved is not None:
        sanitized = _sanitize_saved_qe_parameters(saved, resolved_entries=resolved_entries, atoms=atoms)
        if sanitized != saved:
            st.session_state[QE_PARAMS_STATE_KEY] = sanitized
        return sanitized
    return default_qe_parameters(resolved_entries=resolved_entries, atoms=atoms)


def _normalize_vdw_method_value(value: object) -> VdwMethod:
    """Return a supported vdW method enum, falling back to ``NONE``."""
    try:
        return value if isinstance(value, VdwMethod) else VdwMethod(str(value))
    except ValueError:
        return VdwMethod.NONE


def _format_vdw_method_label(method: VdwMethod) -> str:
    """Return a user-facing label for one vdW method."""
    return _VDW_METHOD_LABELS[method]


def _sanitize_saved_qe_parameters(
    qe_parameters: dict,
    *,
    resolved_entries: dict | None = None,
    atoms=None,
) -> dict:
    """Drop stale saved form states that cannot produce a valid QE input."""
    sanitized = dict(qe_parameters or {})

    defaults = default_qe_parameters(resolved_entries=resolved_entries, atoms=atoms)
    species_order = get_atomic_species_order(atoms) if atoms is not None else ()

    sanitized["prefix"] = str(sanitized.get("prefix") or defaults["prefix"])
    sanitized["outdir"] = str(sanitized.get("outdir") or defaults["outdir"])
    sanitized["prefix_source"] = _infer_saved_setting_source(
        sanitized.get("prefix_source"),
        current_value=sanitized["prefix"],
        default_value=defaults["prefix"],
    )
    sanitized["outdir_source"] = _infer_saved_setting_source(
        sanitized.get("outdir_source"),
        current_value=sanitized["outdir"],
        default_value=defaults["outdir"],
    )
    if sanitized["prefix_source"] == "auto":
        sanitized["prefix"] = defaults["prefix"]
    if sanitized["outdir_source"] == "auto":
        sanitized["outdir"] = defaults["outdir"]

    sanitized["nspin"] = 2 if int(sanitized.get("nspin", defaults["nspin"])) == 2 else 1
    sanitized["noncolin"] = bool(sanitized.get("noncolin", defaults["noncolin"]))
    sanitized["lspinorb"] = bool(sanitized.get("lspinorb", defaults["lspinorb"]))
    sanitized["spin_source"] = _infer_saved_setting_source(
        sanitized.get("spin_source"),
        current_value={
            "nspin": sanitized["nspin"],
            "starting_magnetization": _flatten_starting_magnetization_state(
                sanitized.get("starting_magnetization") or {}
            ),
        },
        default_value={
            "nspin": defaults["nspin"],
            "starting_magnetization": _flatten_starting_magnetization_state(
                defaults.get("starting_magnetization") or {}
            ),
        },
    )

    normalized_magnetization, magnetization_pruned = _normalize_starting_magnetization_state(
        sanitized.get("starting_magnetization"),
        species_order=species_order,
        default_state=defaults.get("starting_magnetization") or {},
        spin_source=sanitized["spin_source"],
    )
    sanitized["starting_magnetization"] = normalized_magnetization
    sanitized["spin_state_pruned"] = bool(sanitized.get("spin_state_pruned", False) or magnetization_pruned)

    normalized_hubbard, hubbard_pruned = _normalize_saved_hubbard_u_state(
        sanitized.get("hubbard_u"),
        species_order=species_order,
    )
    sanitized["dftu_enabled"] = bool(sanitized.get("dftu_enabled", defaults["dftu_enabled"]))
    sanitized["hubbard_u"] = normalized_hubbard
    sanitized["hubbard_u_pruned"] = bool(sanitized.get("hubbard_u_pruned", False) or hubbard_pruned)
    sanitized["vdw_method"] = _normalize_vdw_method_value(
        sanitized.get("vdw_method", defaults["vdw_method"])
    ).value

    return sanitized


def _validate_qe_submission_parameters(qe_parameters: dict) -> str | None:
    """Return a user-facing validation error for invalid UI-only combinations."""
    return None





def _get_generation_submit_block_reason(*, blocker_message: str | None, run_active: bool) -> str | None:
    """Return the user-facing reason generation should not proceed yet."""
    if run_active:
        return "Wait for the active calculation to finish before generating a new `pw.in` file."
    return blocker_message


def _render_validation_issues(issues: list[ValidationIssue]) -> None:
    """Render validation issues using the shared status-message helper."""
    render_status_messages([(issue.level, issue.message) for issue in issues])


def _build_generation_context_from_parameters(
    atoms,
    qe_parameters: dict,
    *,
    resolved_entries: dict[str, dict] | None = None,
) -> CalculationContext:
    """Build the typed generation context from the flat UI parameter dictionary."""
    return build_calculation_context_from_request(
        atoms,
        qe_parameters,
        resolved_entries=resolved_entries,
        atomic_positions_coordinate_mode=get_atomic_positions_coordinate_mode(),
    )


def _generate_qe_input_via_context(
    atoms,
    qe_parameters: dict,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
    *,
    run_dir: Path = QE_RUNS_DIR,
    resolved_entries: dict[str, dict] | None = None,
) -> tuple[str, Path, Path, CalculationContext]:
    """Delegate pw.in generation to the modular input_generator service.

    This adapter converts the flat ``qe_parameters`` dict (as stored in
    session state) into a base :class:`CalculationContext` and calls
    :func:`generate_workflow_inputs`.  This generates all steps for the
    requested workflow while preserving backward compatibility for UI returns.
    """
    ctx = _build_generation_context_from_parameters(
        atoms,
        qe_parameters,
        resolved_entries=resolved_entries,
    )
    steps = generate_workflow_inputs(
        atoms,
        ctx,
        pseudopotentials,
        pseudo_dir,
        workflow=qe_parameters["calculation"],
        run_dir=run_dir,
    )
    # The current UI preview system displays the primary file (index 0).
    return (
        steps[0][1],
        steps[0][2],
        steps[0][3],
        ctx,
    )


def _generate_and_persist_qe_input(
    atoms,
    qe_parameters: dict,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
    *,
    run_dir: Path,
    current_pseudo_settings: dict[str, object],
    resolved_entries: dict[str, dict] | None = None,
    source_metadata: dict[str, object] | None = None,
    structure_signature: dict[str, object] | None = None,
    structure_mode: str = "source",
) -> GenerationPersistResult:
    """Generate QE inputs and metadata, or return blocking issues without overwriting files."""
    try:
        generation_context = _build_generation_context_from_parameters(
            atoms,
            qe_parameters,
            resolved_entries=resolved_entries,
        )
    except Exception as exc:
        return GenerationPersistResult(
            success=False,
            validation_issues=(),
            error_message=f"Failed to prepare the Quantum ESPRESSO input context: {exc}",
        )

    validation_issues = tuple(
        build_generation_validation_issues(
            atoms,
            generation_context,
            requested_workflow=qe_parameters["calculation"],
        )
    )
    if any(issue.level == "error" for issue in validation_issues):
        return GenerationPersistResult(
            success=False,
            validation_issues=validation_issues,
            generation_context=generation_context,
        )

    try:
        qe_input_text, internal_path, ui_path, generation_context = _generate_qe_input_via_context(
            atoms,
            qe_parameters,
            pseudopotentials,
            pseudo_dir,
            run_dir=run_dir,
            resolved_entries=resolved_entries,
        )
    except Exception as exc:
        return GenerationPersistResult(
            success=False,
            validation_issues=validation_issues,
            generation_context=generation_context,
            error_message=f"Failed to generate the Quantum ESPRESSO input file: {exc}",
        )

    reproducibility_metadata = build_input_reproducibility_metadata(
        atoms,
        generation_context,
        requested_workflow=qe_parameters["calculation"],
        pseudo_settings=current_pseudo_settings,
        source_metadata=dict(source_metadata or {}),
        structure_signature=structure_signature,
        structure_mode=structure_mode,
        validation_issues=validation_issues,
        run_dir=run_dir,
    )
    metadata_path = write_input_reproducibility_metadata(run_dir, reproducibility_metadata)
    return GenerationPersistResult(
        success=True,
        validation_issues=validation_issues,
        qe_input_text=qe_input_text,
        internal_path=internal_path,
        ui_path=ui_path,
        metadata_path=metadata_path,
        generation_context=generation_context,
    )


def _build_hubbard_u_settings_from_parameters(qe_parameters: dict) -> HubbardUSettings:
    """Build typed DFT+U settings from the flat UI parameter dictionary."""
    enabled = bool(qe_parameters.get("dftu_enabled", False))
    raw_parameters = qe_parameters.get("hubbard_u") or {}
    species_parameters: dict[str, HubbardUSpeciesParameter] = {}

    if isinstance(raw_parameters, dict):
        for fallback_species, payload in raw_parameters.items():
            if not isinstance(payload, dict):
                continue
            species = str(payload.get("species") or fallback_species)
            source = str(payload.get("source") or "user_defined")
            u_ev = float(payload.get("u_ev", 0.0))
            species_parameters[species] = HubbardUSpeciesParameter(
                species=species,
                u_ev=u_ev,
                source=source,
            )

    return HubbardUSettings(
        enabled=enabled,
        species_parameters=species_parameters if enabled else {},
    )


def _build_vdw_settings_from_parameters(qe_parameters: dict) -> VdwSettings:
    """Build typed vdW settings from the flat UI parameter dictionary."""
    raw_method = qe_parameters.get("vdw_method", VdwMethod.NONE.value)
    try:
        method = raw_method if isinstance(raw_method, VdwMethod) else VdwMethod(str(raw_method))
    except ValueError as exc:
        raise ValueError(f"Unknown vdW method {raw_method!r}") from exc
    return VdwSettings(method=method)
