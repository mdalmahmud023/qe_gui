from __future__ import annotations

import os
import re
from pathlib import Path

from ase.calculators.espresso import Espresso, EspressoProfile
import streamlit as st

from project_context import (
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
    QE_PARAMS_STATE_KEY,
    QE_RUNS_DIR,
    QE_SMEARING_OPTIONS,
    ProjectContext,
    clear_generated_qe_input_state,
    clear_pseudopotential_state,
    get_current_pseudo_settings,
    has_resolved_pseudopotential_data,
    mark_pseudopotentials_stale,
    set_resolution_status,
    sync_pseudopotential_settings_state,
)
from pseudopotentials import (
    PseudopotentialResolutionError,
    clear_sssp_family_cache,
    get_structure_signature,
    get_unique_elements,
    resolve_sssp_pseudopotentials,
)
from qe_runner import RUN_ACTIVE_STATE_KEY, reset_run_state


def render_page(ctx: ProjectContext) -> None:
    """Render the Setup page."""
    st.subheader("Setup")
    st.caption("Resolve pseudopotentials, configure the base Quantum ESPRESSO input, and review the generated `pw.in`.")

    if not ctx.structure.loaded:
        st.info(
            "No structure is loaded in this session. Upload a CIF file on the Structure page to enable pseudopotential resolution and new QE input generation."
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

    if status:
        message = status.get("message")
        if status.get("state") == "resolved" and message:
            st.success(message)
        elif status.get("state") == "stale" and message:
            st.warning(message)
        elif status.get("state") == "error" and message:
            st.error(message)

        for warning_message in status.get("warnings", []):
            st.warning(warning_message)
    elif rows:
        st.info("Resolved pseudopotential data is available.")
    else:
        st.info("Resolve pseudopotentials to enable runnable Quantum ESPRESSO input generation.")

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
    defaults = get_qe_parameters()
    default_k_grid = defaults["k_points_grid"]
    blocker_message = get_qe_generation_blocker_message(ctx.atoms)
    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))

    st.markdown("### QE Input Parameters")
    st.caption("Configure the base `pw.x` input for the current structure.")

    if blocker_message:
        if ctx.atoms is None:
            st.info(blocker_message)
        else:
            st.warning(blocker_message)
    elif run_active:
        st.info("Wait for the active calculation to finish before generating a new `pw.in` file.")

    with st.form("qe_input_form"):
        row_one_col1, row_one_col2, row_one_col3 = st.columns(3)
        calculation = row_one_col1.selectbox(
            "Calculation Type",
            options=QE_CALCULATION_OPTIONS,
            index=QE_CALCULATION_OPTIONS.index(defaults["calculation"]),
        )
        smearing = row_one_col2.selectbox(
            "Smearing Type",
            options=QE_SMEARING_OPTIONS,
            index=QE_SMEARING_OPTIONS.index(defaults["smearing"]),
        )
        degauss = row_one_col3.number_input(
            "Smearing Width (degauss)",
            min_value=0.0,
            value=float(defaults["degauss"]),
            step=0.001,
            format="%.4f",
        )

        row_two_col1, row_two_col2 = st.columns(2)
        ecutwfc = row_two_col1.number_input(
            "Kinetic Energy Cutoff for Wavefunctions (ecutwfc, Ry)",
            min_value=0.0,
            value=float(defaults["ecutwfc"]),
            step=5.0,
            format="%.1f",
        )
        ecutrho = row_two_col2.number_input(
            "Kinetic Energy Cutoff for Charge Density (ecutrho, Ry)",
            min_value=0.0,
            value=float(defaults["ecutrho"]),
            step=5.0,
            format="%.1f",
        )

        st.markdown("**K-Points Grid**")
        row_three_col1, row_three_col2, row_three_col3 = st.columns(3)
        kx = row_three_col1.number_input("K-Points X", min_value=1, value=int(default_k_grid[0]), step=1)
        ky = row_three_col2.number_input("K-Points Y", min_value=1, value=int(default_k_grid[1]), step=1)
        kz = row_three_col3.number_input("K-Points Z", min_value=1, value=int(default_k_grid[2]), step=1)

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
        "smearing": smearing,
        "degauss": degauss,
        "k_points_grid": [kx, ky, kz],
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
        qe_input_text, internal_path, ui_path = generate_qe_input_file(
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
    st.session_state[QE_INPUT_INTERNAL_PATH_STATE_KEY] = internal_path
    st.session_state[QE_INPUT_UI_PATH_STATE_KEY] = ui_path
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


def default_qe_parameters() -> dict:
    return {
        "calculation": "scf",
        "ecutwfc": 40.0,
        "ecutrho": 160.0,
        "smearing": "gaussian",
        "degauss": 0.01,
        "k_points_grid": [4, 4, 4],
    }


def get_qe_parameters() -> dict:
    return st.session_state.get(QE_PARAMS_STATE_KEY, default_qe_parameters())


def build_qe_input_data(qe_parameters: dict) -> dict:
    calc_type = qe_parameters["calculation"]

    system_data = {
        "ecutwfc": qe_parameters["ecutwfc"],
        "ecutrho": qe_parameters["ecutrho"],
        "smearing": qe_parameters["smearing"],
        "degauss": qe_parameters["degauss"],
    }
    if qe_parameters["degauss"] > 0:
        system_data["occupations"] = "smearing"

    input_data = {
        "control": {
            "calculation": calc_type,
        },
        "system": system_data,
        "electrons": {
            "conv_thr": 1.0e-6,
            "mixing_beta": 0.7,
        },
    }

    if calc_type in ("relax", "md", "vc-relax", "vc-md"):
        input_data["ions"] = {"ion_dynamics": "bfgs"}

    if calc_type in ("vc-relax", "vc-md"):
        input_data["cell"] = {"cell_dynamics": "bfgs"}

    return input_data


def clean_generated_qe_input_text(input_text: str) -> str:
    calc_match = re.search(r"calculation\s*=\s*['\"]([^'\"]+)['\"]", input_text, re.IGNORECASE)
    calc_type = calc_match.group(1).lower() if calc_match else "scf"

    required_namelists = {"&CONTROL", "&SYSTEM", "&ELECTRONS"}
    if calc_type in ("relax", "md", "vc-relax", "vc-md"):
        required_namelists.add("&IONS")
    if calc_type in ("vc-relax", "vc-md"):
        required_namelists.add("&CELL")

    def replacer(match):
        namelist_name = match.group(1).upper()
        if namelist_name in required_namelists:
            return match.group(0)
        return ""

    cleaned = re.sub(r"(?ms)^(&[A-Z_]+)\n(?:\s*\n)*\s*/\n", replacer, input_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return f"{cleaned}\n"


def generate_qe_input_file(
    atoms,
    qe_parameters: dict,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
) -> tuple[str, str, str]:
    QE_RUNS_DIR.mkdir(parents=True, exist_ok=True)

    input_data = build_qe_input_data(qe_parameters)
    pseudo_dir_path = Path(pseudo_dir)
    relative_pseudo_dir = Path(os.path.relpath(pseudo_dir_path, QE_RUNS_DIR))
    profile = EspressoProfile(command="pw.x", pseudo_dir=relative_pseudo_dir)
    calculator = Espresso(
        directory=QE_RUNS_DIR,
        profile=profile,
        input_data=input_data,
        kpts=tuple(qe_parameters["k_points_grid"]),
        pseudopotentials=pseudopotentials,
    )

    atoms_copy = atoms.copy()
    atoms_copy.calc = calculator
    calculator.write_inputfiles(atoms_copy, properties=["energy"])

    internal_path = QE_RUNS_DIR / "espresso.pwi"
    ui_path = QE_RUNS_DIR / "pw.in"

    generated_text = internal_path.read_text()
    preview_text = clean_generated_qe_input_text(generated_text)
    ui_path.write_text(preview_text)

    return preview_text, str(internal_path), str(ui_path)


def get_qe_generation_blocker_message(atoms) -> str | None:
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
