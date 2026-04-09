from __future__ import annotations

from pathlib import Path

import streamlit as st

from ..core.project_context import ProjectContext
from ..core.state_keys import (
    ATOMS_STATE_KEY,
    DEFAULT_PSEUDO_SETTINGS,
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
    get_current_pseudo_settings,
    get_qe_generation_blocker_message,
    record_generated_input_provenance,
    set_resolution_status,
    sync_pseudopotential_settings_state,
)
from ..core.workspace import QE_RUNS_DIR
from ..services.input_generator import (
    CalculationContext,
    ElectronicCharacter,
    NumericalSettings,
    PhysicalOptions,
    classify_structure,
    generate_workflow_inputs,
    make_default_context,
    select_cutoffs,
    suggest_kpoints_from_cell,
)
from ..services.pseudopotentials import (
    PseudopotentialResolutionError,
    clear_sssp_family_cache,
    get_structure_signature,
    get_unique_elements,
    resolve_sssp_pseudopotentials,
)
from ..services.qe_runner import RUN_ACTIVE_STATE_KEY, reset_run_state
from .shared import render_status_messages

# Internal state key for the conv_thr widget (not yet in state_keys.py to
# avoid a breaking migration; added here as a local constant).
_QE_CONV_THR_STATE_KEY = "qe_conv_thr"
_DEFAULT_CONV_THR = 1.0e-6
_DEFAULT_MIXING_BETA = 0.7


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
    st.caption("Resolve and cache SSSP pseudopotentials before generating runnable QE inputs.")

    structure_loaded = ctx.atoms is not None
    current_defaults = {
        "functional": st.session_state.get(PSEUDO_SETTINGS_STATE_KEY, DEFAULT_PSEUDO_SETTINGS).get(
            "functional", DEFAULT_PSEUDO_SETTINGS["functional"]
        ),
        "mode": st.session_state.get(PSEUDO_SETTINGS_STATE_KEY, DEFAULT_PSEUDO_SETTINGS).get(
            "mode", DEFAULT_PSEUDO_SETTINGS["mode"]
        ),
        "version": st.session_state.get(PSEUDO_SETTINGS_STATE_KEY, DEFAULT_PSEUDO_SETTINGS).get(
            "version", DEFAULT_PSEUDO_SETTINGS["version"]
        ),
    }

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
    elements = get_unique_elements(ctx.atoms)
    st.write(f"Detected elements: {', '.join(elements)}")

    if clear_cache_clicked:
        clear_sssp_family_cache(
            version=current_settings["version"],
            functional=current_settings["functional"],
            mode=current_settings["mode"],
        )
        clear_pseudopotential_state(clear_structure_signature=False)
        st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = get_structure_signature(ctx.atoms)
        st.success("Cleared cached pseudopotentials for the selected SSSP family.")

    resolve_clicked = st.button("Resolve Pseudopotentials", use_container_width=True)
    if resolve_clicked:
        clear_generated_qe_input_state()
        progress_status = st.empty()
        progress_bar = st.progress(0)

        def update_resolution_progress(fraction: float, message: str) -> None:
            percent = max(0, min(100, int(round(fraction * 100))))
            progress_bar.progress(percent)
            progress_status.info(f"{message} ({percent}%)")

        try:
            resolution_result = resolve_sssp_pseudopotentials(
                ctx.atoms,
                version=current_settings["version"],
                functional=current_settings["functional"],
                mode=current_settings["mode"],
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

    render_pseudopotential_status()


def render_qe_input_form(ctx: ProjectContext) -> None:
    """Render the QE input form and gate runnable generation on pseudo resolution."""
    # Resolve SSSP entries first so defaults can use them
    _resolved_entries_for_defaults = st.session_state.get("pseudo_resolved_entries") or {}
    defaults = get_qe_parameters(
        resolved_entries=_resolved_entries_for_defaults or None,
        atoms=ctx.atoms,
    )
    default_k_grid = defaults["k_points_grid"]
    blocker_message = get_qe_generation_blocker_message(ctx.atoms)
    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))

    # --- SSSP-suggested cutoff hints via CutoffDecision ---
    resolved_entries = st.session_state.get("pseudo_resolved_entries") or {}
    cutoff_decision = None
    if resolved_entries and ctx.atoms is not None:
        try:
            cutoff_decision = select_cutoffs(resolved_entries)
        except Exception:
            pass

    st.markdown("### QE Input Parameters")
    st.caption("Configure the base `pw.x` input for the current structure.")

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

    with st.form("qe_input_form"):
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

        if ctx.atoms is not None and "classification" in locals():
            suspicious = False
            reasons = []
            if 0 in classification.vacuum_directions and kx > 1:
                suspicious = True
                reasons.append(f"X is a vacuum direction for {classification.dimensionality.value} structure")
            if 1 in classification.vacuum_directions and ky > 1:
                suspicious = True
                reasons.append(f"Y is a vacuum direction for {classification.dimensionality.value} structure")
            if 2 in classification.vacuum_directions and kz > 1:
                suspicious = True
                reasons.append(f"Z is a vacuum direction for {classification.dimensionality.value} structure")
            
            total_k = kx * ky * kz
            if total_k > 5000 and calculation == "scf":
                suspicious = True
                reasons.append(f"{total_k} total k-points is unusually dense for a basic SCF calculation")
                
            if suspicious:
                st.warning(
                    f"**Suspicious K-Point Mesh**: You have selected [{kx}, {ky}, {kz}]. "
                    f"Possible issues: {', '.join(reasons)}. "
                    "Make sure this is intended before generating!"
                )

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

        submitted = st.form_submit_button(
            "Generate Input File",
            use_container_width=True,
            disabled=blocker_message is not None or run_active,
        )

    if not submitted:
        return

    qe_parameters = {
        "calculation": calculation,
        "ecutwfc": ecutwfc,
        "ecutrho": ecutrho,
        "occupations": occupations,
        "smearing": smearing,
        "degauss": degauss,
        "k_points_grid": [kx, ky, kz],
        "conv_thr": conv_thr,
        "mixing_beta": mixing_beta,
    }
    st.session_state[QE_PARAMS_STATE_KEY] = qe_parameters

    pseudopotentials = st.session_state.get(PSEUDO_RESOLVED_MAP_STATE_KEY)
    pseudo_dir = st.session_state.get(PSEUDO_DIR_STATE_KEY)
    atoms = st.session_state.get(ATOMS_STATE_KEY)
    if atoms is None or not pseudopotentials or not pseudo_dir:
        clear_generated_qe_input_state()
        st.error("Resolve pseudopotentials before generating a runnable Quantum ESPRESSO input file.")
        return

    try:
        qe_input_text, internal_path, ui_path = _generate_qe_input_via_context(
            atoms,
            qe_parameters,
            pseudopotentials,
            pseudo_dir,
        )
    except Exception as exc:
        clear_generated_qe_input_state()
        st.error(f"Failed to generate the Quantum ESPRESSO input file: {exc}")
        return

    reset_run_state(st.session_state)
    st.session_state[QE_INPUT_TEXT_STATE_KEY] = qe_input_text
    st.session_state[QE_INPUT_INTERNAL_PATH_STATE_KEY] = str(internal_path)
    st.session_state[QE_INPUT_UI_PATH_STATE_KEY] = str(ui_path)
    record_generated_input_provenance(
        QE_RUNS_DIR,
        ui_path=str(ui_path),
        internal_path=str(internal_path),
    )
    st.success("Generated updated QE input files under `qe_runs/`.")


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
    if preview_text:
        return preview_text
    if ctx.input_files.ui_path.is_file():
        return ctx.input_files.ui_path.read_text(encoding="utf-8")
    return None


def default_qe_parameters(
    resolved_entries: dict | None = None,
    atoms=None,
) -> dict:
    """Return physics-aware defaults for the QE input form. When ``atoms`` are available, uses :func:`~qe_studio.services.input_generator.make_default_context`
    which applies chemistry-aware policies (occupations, smearing, k-grid) based
    on the structure classification.
    """
    if atoms is not None:
        try:
            ctx = make_default_context(atoms, resolved_entries=resolved_entries)
            return {
                "calculation": "scf",
                "ecutwfc": ctx.numerical.ecutwfc,
                "ecutrho": ctx.numerical.ecutrho,
                "occupations": ctx.physical.occupations,
                "smearing": ctx.physical.smearing,
                "degauss": ctx.physical.degauss,
                "k_points_grid": ctx.physical.k_points_grid,
                "conv_thr": ctx.numerical.conv_thr,
                "mixing_beta": ctx.numerical.mixing_beta,
            }
        except Exception:
            pass

    ecutwfc = 40.0
    ecutrho = 160.0
    k_grid = [4, 4, 4]

    if resolved_entries:
        try:
            decision = select_cutoffs(resolved_entries)
            ecutwfc = decision.ecutwfc_ry
            ecutrho = decision.ecutrho_ry
        except Exception:
            pass

    return {
        "calculation": "scf",
        "ecutwfc": ecutwfc,
        "ecutrho": ecutrho,
        "occupations": "smearing",
        "smearing": "gaussian",
        "degauss": 0.01,
        "k_points_grid": k_grid,
        "conv_thr": _DEFAULT_CONV_THR,
        "mixing_beta": _DEFAULT_MIXING_BETA,
    }


def get_qe_parameters(resolved_entries: dict | None = None, atoms=None) -> dict:
    """Return the current QE parameters from session state or physics-aware defaults.

    If no user-saved parameters exist yet, computes defaults from the
    resolved SSSP entries so that the input fields are pre-populated with
    the correct SSSP-recommended cutoffs rather than hardcoded fallbacks.
    """
    saved = st.session_state.get(QE_PARAMS_STATE_KEY)
    if saved is not None:
        return saved
    return default_qe_parameters(resolved_entries=resolved_entries, atoms=atoms)


def _generate_qe_input_via_context(
    atoms,
    qe_parameters: dict,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
) -> tuple[str, Path, Path]:
    """Delegate pw.in generation to the modular input_generator service.

    This adapter converts the flat ``qe_parameters`` dict (as stored in
    session state) into a base :class:`CalculationContext` and calls
    :func:`generate_workflow_inputs`.  This generates all steps for the
    requested workflow while preserving backward compatibility for UI returns.
    """
    numerical = NumericalSettings(
        ecutwfc=float(qe_parameters["ecutwfc"]),
        ecutrho=float(qe_parameters["ecutrho"]),
        conv_thr=float(qe_parameters.get("conv_thr", _DEFAULT_CONV_THR)),
        mixing_beta=float(qe_parameters.get("mixing_beta", _DEFAULT_MIXING_BETA)),
    )
    physical = PhysicalOptions(
        occupations=qe_parameters.get("occupations", "smearing"),
        smearing=qe_parameters["smearing"],
        degauss=float(qe_parameters["degauss"]),
        k_points_grid=list(qe_parameters["k_points_grid"]),
    )
    ctx = CalculationContext(
        calculation=qe_parameters["calculation"],
        numerical=numerical,
        physical=physical,
    )
    steps = generate_workflow_inputs(
        atoms,
        ctx,
        pseudopotentials,
        pseudo_dir,
        workflow=qe_parameters["calculation"],
        run_dir=QE_RUNS_DIR,
    )
    # The current UI preview system displays the primary file (index 0).
    return steps[0][1], steps[0][2], steps[0][3]
