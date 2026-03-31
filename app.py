import io
import os
import re
import shutil
from pathlib import Path

import py3Dmol
import streamlit as st
import streamlit.components.v1 as components
from ase.calculators.espresso import Espresso, EspressoProfile
from ase.io import read, write
from electronic_properties import (
    DEFAULT_PLOTLY_CONFIG,
    AnalysisParseError,
    BandData,
    DOSData,
    FermiMetadata,
    extract_qe_fermi_metadata,
    find_available_analysis_files,
    make_band_figure,
    make_dos_figure,
    parse_qe_band_structure,
    parse_qe_dos,
)

from pseudopotentials import (
    PseudopotentialResolutionError,
    clear_sssp_family_cache,
    get_structure_signature,
    get_unique_elements,
    has_same_element_set,
    resolve_sssp_pseudopotentials,
)
from qe_runner import (
    ACTIVE_RUN_ID_STATE_KEY,
    DEFAULT_MAX_TAIL_LINES,
    RUN_ACTIVE_STATE_KEY,
    RUN_FINISHED_STATE_KEY,
    RUN_OUTPUT_PATH_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    apply_run_events,
    cleanup_run,
    drain_run_events,
    get_run_context,
    initialize_run_state,
    mark_runtime_lost,
    reset_run_state,
    set_run_started,
    start_qe_run,
)


ATOMS_STATE_KEY = "structure_atoms"
CIF_BYTES_STATE_KEY = "structure_cif_bytes"
FILENAME_STATE_KEY = "structure_filename"
ERROR_STATE_KEY = "structure_error"
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


def clear_generated_qe_input_state() -> None:
    """Remove generated QE input preview state."""
    for key in (
        QE_INPUT_TEXT_STATE_KEY,
        QE_INPUT_INTERNAL_PATH_STATE_KEY,
        QE_INPUT_UI_PATH_STATE_KEY,
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
        CIF_BYTES_STATE_KEY,
        FILENAME_STATE_KEY,
        ERROR_STATE_KEY,
    ):
        st.session_state.pop(key, None)


def parse_cif_bytes(cif_bytes: bytes):
    """Parse the first CIF structure block into ASE atoms."""
    return read(io.BytesIO(cif_bytes), format="cif", index=0)


def atoms_to_cif_text(atoms) -> str:
    """Serialize ASE atoms back to CIF text for py3Dmol."""
    cif_buffer = io.BytesIO()
    write(cif_buffer, atoms, format="cif")
    return cif_buffer.getvalue().decode("utf-8")


def build_structure_viewer_html(atoms, width: int = 900, height: int = 520) -> str:
    """Build the embedded py3Dmol viewer HTML."""
    viewer = py3Dmol.view(width=width, height=height)
    viewer.addModel(atoms_to_cif_text(atoms), "cif")
    viewer.setStyle({"sphere": {"scale": 0.3}, "stick": {"radius": 0.16}})
    viewer.setBackgroundColor("white")
    if atoms.cell.rank > 0 and atoms.cell.volume > 0:
        viewer.addUnitCell()
    viewer.zoomTo()
    viewer.render()
    return viewer._make_html()


def format_cell_parameters(atoms) -> str:
    """Format basic cell parameters for sidebar display."""
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


def store_uploaded_structure(uploaded_file) -> None:
    """Parse, store, and invalidate dependent state for an uploaded CIF file."""
    if uploaded_file is None:
        clear_structure_state()
        return

    cif_bytes = uploaded_file.getvalue()
    if cif_bytes == st.session_state.get(CIF_BYTES_STATE_KEY):
        return

    try:
        atoms = parse_cif_bytes(cif_bytes)
    except Exception as exc:
        clear_structure_state()
        st.session_state[ERROR_STATE_KEY] = f"Failed to parse CIF file: {exc}"
        return

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

    st.session_state[ATOMS_STATE_KEY] = atoms
    st.session_state[CIF_BYTES_STATE_KEY] = cif_bytes
    st.session_state[FILENAME_STATE_KEY] = uploaded_file.name
    st.session_state[ERROR_STATE_KEY] = None
    st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = new_structure_signature


def default_qe_parameters() -> dict:
    """Return default Quantum ESPRESSO form parameters."""
    return {
        "calculation": "scf",
        "ecutwfc": 40.0,
        "ecutrho": 160.0,
        "smearing": "gaussian",
        "degauss": 0.01,
        "k_points_grid": [4, 4, 4],
    }


def get_qe_parameters() -> dict:
    """Return the last submitted QE parameters or defaults."""
    return st.session_state.get(QE_PARAMS_STATE_KEY, default_qe_parameters())


def build_qe_input_data(qe_parameters: dict) -> dict:
    """Build the ASE Espresso input_data mapping from form values."""
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
        }
    }

    if calc_type in ("relax", "md", "vc-relax", "vc-md"):
        input_data["ions"] = {"ion_dynamics": "bfgs"}
        
    if calc_type in ("vc-relax", "vc-md"):
        input_data["cell"] = {"cell_dynamics": "bfgs"}

    return input_data


def clean_generated_qe_input_text(input_text: str) -> str:
    """Smartly remove empty namelists unless they are strictly required by the calculation type."""
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
            return match.group(0)  # Preserve required empty namelists
        return ""  # Strip unneeded empty namelists to avoid QE warnings

    cleaned = re.sub(r"(?ms)^(&[A-Z_]+)\n(?:\s*\n)*\s*/\n", replacer, input_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return f"{cleaned}\n"


def generate_qe_input_file(
    atoms,
    qe_parameters: dict,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
) -> tuple[str, str, str]:
    """Generate runnable QE input files using resolved pseudopotentials."""
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
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_pseudopotentials_section(atoms) -> None:
    """Render the pseudopotential controls and resolution results."""
    st.divider()
    st.subheader("Pseudopotentials")
    st.caption("Resolve and cache SSSP pseudopotentials before generating runnable QE inputs.")

    structure_loaded = atoms is not None
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

    top_row_col1, top_row_col2, top_row_col3 = st.columns(3)
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
    top_row_col3.selectbox(
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

    if not structure_loaded:
        st.info("Upload a CIF file to detect elements and resolve SSSP pseudopotentials.")
        render_pseudopotential_status()
        return

    sync_pseudopotential_settings_state()
    current_settings = get_current_pseudo_settings()
    use_cache = bool(st.session_state.get(PSEUDO_USE_CACHE_WIDGET_KEY, True))
    elements = get_unique_elements(atoms)
    st.write(f"Detected elements: {', '.join(elements)}")

    button_col1, button_col2 = st.columns(2)
    resolve_clicked = button_col1.button("Resolve Pseudopotentials", use_container_width=True)
    clear_cache_clicked = button_col2.button("Clear pseudopotential cache", use_container_width=True)

    if clear_cache_clicked:
        clear_sssp_family_cache(
            version=current_settings["version"],
            functional=current_settings["functional"],
            mode=current_settings["mode"],
        )
        clear_pseudopotential_state(clear_structure_signature=False)
        st.session_state[PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = get_structure_signature(atoms)
        st.success("Cleared cached pseudopotentials for the selected SSSP family.")

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
                atoms,
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


def get_qe_generation_blocker_message(atoms) -> str | None:
    """Return a user-facing blocker message when runnable QE generation is not allowed."""
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


def render_generated_qe_input_preview() -> None:
    """Render the generated QE input preview if available."""
    qe_input_text = st.session_state.get(QE_INPUT_TEXT_STATE_KEY)
    if not qe_input_text:
        return

    internal_path = st.session_state.get(QE_INPUT_INTERNAL_PATH_STATE_KEY, "qe_runs/espresso.pwi")
    ui_path = st.session_state.get(QE_INPUT_UI_PATH_STATE_KEY, "qe_runs/pw.in")

    st.success(
        "Generated Quantum ESPRESSO input files in "
        f"`{internal_path}` and `{ui_path}` using resolved pseudopotentials."
    )
    st.code(qe_input_text, language="fortran")


def format_run_output_text() -> str:
    """Return the current rolling terminal tail for display."""
    tail_lines = st.session_state.get(RUN_TAIL_LINES_STATE_KEY, [])
    if tail_lines:
        return "".join(tail_lines)
    if st.session_state.get(RUN_ACTIVE_STATE_KEY):
        return "Waiting for Quantum ESPRESSO output...\n"
    if st.session_state.get(RUN_FINISHED_STATE_KEY):
        return "Quantum ESPRESSO finished without terminal output.\n"
    return "Run output will appear here.\n"


@st.fragment(run_every=1)
def render_live_run_output_fragment() -> None:
    """Poll the active run queue and repaint the live output tail."""
    output_placeholder = st.empty()
    active_run_id = st.session_state.get(ACTIVE_RUN_ID_STATE_KEY)

    if not active_run_id:
        mark_runtime_lost(st.session_state)
        output_placeholder.code(format_run_output_text(), language="text")
        st.rerun(scope="app")
        return

    context = get_run_context(active_run_id)
    if context is None:
        mark_runtime_lost(st.session_state)
        output_placeholder.code(format_run_output_text(), language="text")
        st.rerun(scope="app")
        return

    events = drain_run_events(active_run_id)
    run_completed = apply_run_events(
        st.session_state,
        events,
        max_tail_lines=DEFAULT_MAX_TAIL_LINES,
    )
    output_placeholder.code(format_run_output_text(), language="text")

    if not run_completed and st.session_state.get(RUN_ACTIVE_STATE_KEY) and not context.thread.is_alive():
        mark_runtime_lost(st.session_state)
        cleanup_run(active_run_id)
        st.rerun(scope="app")
        return

    if run_completed:
        cleanup_run(active_run_id)
        st.rerun(scope="app")


def render_qe_run_section() -> None:
    """Render local QE execution controls and live output."""
    qe_input_text = st.session_state.get(QE_INPUT_TEXT_STATE_KEY)
    ui_path_value = st.session_state.get(QE_INPUT_UI_PATH_STATE_KEY)
    if not qe_input_text or not ui_path_value:
        return

    input_path = Path(ui_path_value)
    if not input_path.is_file():
        st.warning("The generated `pw.in` file is missing from disk. Generate it again before running.")
        return

    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))
    run_finished = bool(st.session_state.get(RUN_FINISHED_STATE_KEY))
    run_returncode = st.session_state.get(RUN_RETURNCODE_STATE_KEY)
    output_path_value = st.session_state.get(RUN_OUTPUT_PATH_STATE_KEY)
    output_path_text = output_path_value or str(QE_RUNS_DIR / QE_OUTPUT_FILENAME)

    st.divider()
    st.subheader("Run Calculation")
    st.caption("Execute the generated input locally with Quantum ESPRESSO and stream the live terminal output.")

    if run_active:
        st.info("Quantum ESPRESSO is running. Live terminal output is updating below.")
    elif run_finished and run_returncode == 0:
        st.success(f"Quantum ESPRESSO finished successfully with exit code {run_returncode}.")
    elif run_finished:
        st.error(f"Quantum ESPRESSO exited with code {run_returncode}.")
    else:
        st.info("Launch `pw.x` against the generated `pw.in` input file.")

    st.caption(f"Full output log: `{output_path_text}`")

    run_clicked = st.button(
        "Run Calculation",
        use_container_width=True,
        disabled=run_active,
    )

    if run_clicked:
        if shutil.which(QE_RUN_COMMAND[0]) is None:
            st.error("Quantum ESPRESSO executable `pw.x` was not found on your PATH.")
        else:
            try:
                reset_run_state(st.session_state)
                run_id, output_path = start_qe_run(
                    run_dir=QE_RUNS_DIR,
                    command=QE_RUN_COMMAND,
                    output_filename=QE_OUTPUT_FILENAME,
                )
            except Exception as exc:
                reset_run_state(st.session_state)
                st.error(f"Failed to start Quantum ESPRESSO: {exc}")
            else:
                set_run_started(
                    st.session_state,
                    run_id=run_id,
                    output_path=output_path,
                )
                st.rerun()

    if st.session_state.get(RUN_ACTIVE_STATE_KEY):
        render_live_run_output_fragment()
    else:
        output_placeholder = st.empty()
        output_placeholder.code(format_run_output_text(), language="text")


def format_fermi_source_label(fermi_metadata: FermiMetadata | None) -> str:
    """Return a compact UI label for the active Fermi metadata source."""
    if fermi_metadata is None:
        return "Unavailable"
    if fermi_metadata.energy_ev is None:
        return f"{fermi_metadata.source_type} (no single reference)"
    return f"{fermi_metadata.source_type}: {fermi_metadata.reference_kind}"


def make_plotly_chart_config(filename: str) -> dict:
    """Return Plotly export settings with a figure-specific filename."""
    export_options = dict(DEFAULT_PLOTLY_CONFIG["toImageButtonOptions"])
    export_options["filename"] = filename
    config = dict(DEFAULT_PLOTLY_CONFIG)
    config["toImageButtonOptions"] = export_options
    return config


def render_artifact_summary_row(run_dir: Path, analysis_files, fermi_metadata: FermiMetadata | None) -> None:
    """Render a compact summary of detected analysis artifacts."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("DOS detected")
    col1.write("Yes" if analysis_files.dos_detected else "No")
    col2.caption("Bands detected")
    col2.write("Yes" if analysis_files.bands_detected else "No")
    col3.caption("Fermi source")
    col3.write(format_fermi_source_label(fermi_metadata))
    col4.caption("Run directory")
    col4.write(str(run_dir))


def render_dos_summary(dos_data: DOSData) -> None:
    """Render compact numeric DOS metadata."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Source file")
    col1.write(str(dos_data.source_file))
    col2.caption("Fermi energy")
    if dos_data.fermi_energy_ev is None:
        col2.write("Unavailable")
    else:
        col2.write(f"{dos_data.fermi_energy_ev:.4f} eV")
    col3.caption("Spin polarized")
    col3.write("Yes" if dos_data.spin_polarized else "No")
    col4.caption("Energy window")
    col4.write(f"{min(dos_data.energies_ev):.3f} to {max(dos_data.energies_ev):.3f} eV")


def render_band_summary(band_data: BandData) -> None:
    """Render compact numeric band metadata."""
    n_kpoints = len(band_data.distances)
    n_bands = len(band_data.eigenvalues_ev[0]) if band_data.eigenvalues_ev else 0
    flattened_energies = [value for row in band_data.eigenvalues_ev for value in row]

    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Source file")
    col1.write(str(band_data.source_file))
    col2.caption("Fermi energy")
    if band_data.fermi_energy_ev is None:
        col2.write("Unavailable")
    else:
        col2.write(f"{band_data.fermi_energy_ev:.4f} eV")
    col3.caption("Bands / k-points")
    col3.write(f"{n_bands} / {n_kpoints}")
    col4.caption("Energy window")
    col4.write(f"{min(flattened_energies):.3f} to {max(flattened_energies):.3f} eV")


def render_dos_subsection(analysis_files) -> None:
    """Render the DOS subsection with graceful parsing failure handling."""
    st.markdown("### DOS")
    if analysis_files.dos_file is None:
        st.info("DOS data has not been generated yet. Run QE DOS post-processing to produce a DOS file.")
        return

    try:
        dos_data = parse_qe_dos(analysis_files.dos_file, analysis_files=analysis_files)
    except AnalysisParseError as exc:
        st.error(f"Failed to parse DOS data from `{analysis_files.dos_file}`: {exc}")
        return
    except Exception as exc:
        st.error(f"Unexpected DOS parsing error for `{analysis_files.dos_file}`: {exc}")
        return

    render_dos_summary(dos_data)

    if dos_data.fermi_metadata is not None:
        st.caption(
            "Fermi metadata source: "
            f"`{dos_data.fermi_metadata.source_file}` ({dos_data.fermi_metadata.source_type})"
        )
        if dos_data.fermi_metadata.message:
            st.info(dos_data.fermi_metadata.message)

    align_to_fermi = st.checkbox(
        "Align DOS to Fermi level (0 eV)",
        value=dos_data.fermi_energy_ev is not None,
        disabled=dos_data.fermi_energy_ev is None,
        key="dos_align_to_fermi",
    )
    if dos_data.fermi_energy_ev is None:
        st.info("No Fermi energy was found for DOS alignment, so raw energies are shown.")

    figure = make_dos_figure(dos_data, align_to_fermi=align_to_fermi)
    st.plotly_chart(
        figure,
        use_container_width=True,
        config=make_plotly_chart_config("qe_studio_dos"),
    )


def render_band_subsection(analysis_files) -> None:
    """Render the band-structure subsection with graceful parsing failure handling."""
    st.markdown("### Band Structure")
    if analysis_files.band_file is None:
        st.info(
            "Band-structure data is unavailable. A dedicated band calculation and `filband.gnu` output are required."
        )
        return

    try:
        band_data = parse_qe_band_structure(
            analysis_files.band_file,
            band_input_file=analysis_files.band_input_file,
            analysis_files=analysis_files,
        )
    except AnalysisParseError as exc:
        st.error(f"Failed to parse band data from `{analysis_files.band_file}`: {exc}")
        return
    except Exception as exc:
        st.error(f"Unexpected band parsing error for `{analysis_files.band_file}`: {exc}")
        return

    render_band_summary(band_data)

    if band_data.fermi_metadata is not None:
        st.caption(
            "Fermi metadata source: "
            f"`{band_data.fermi_metadata.source_file}` ({band_data.fermi_metadata.source_type})"
        )
        if band_data.fermi_metadata.message:
            st.info(band_data.fermi_metadata.message)
    if analysis_files.band_input_file is not None:
        st.caption(f"Band-path input: `{analysis_files.band_input_file}`")

    align_to_fermi = st.checkbox(
        "Align bands to Fermi level (0 eV)",
        value=band_data.fermi_energy_ev is not None,
        disabled=band_data.fermi_energy_ev is None,
        key="band_align_to_fermi",
    )
    if band_data.fermi_energy_ev is None:
        st.info("No Fermi energy was found for band alignment, so raw energies are shown.")

    figure = make_band_figure(band_data, align_to_fermi=align_to_fermi)
    st.plotly_chart(
        figure,
        use_container_width=True,
        config=make_plotly_chart_config("qe_studio_bands"),
    )


def render_electronic_properties_section(run_dir: Path) -> None:
    """Render the Electronic Properties Analysis expander when artifacts exist."""
    analysis_files = find_available_analysis_files(run_dir)
    if not analysis_files.has_any_artifact:
        return

    fermi_metadata = extract_qe_fermi_metadata(analysis_files)
    with st.expander("Electronic Properties Analysis", expanded=True):
        render_artifact_summary_row(run_dir, analysis_files, fermi_metadata)

        if analysis_files.is_metadata_only:
            st.info(
                "QE metadata was detected in this run directory, but no plottable DOS or `filband.gnu` data "
                "is available yet. Generate DOS and/or band-structure post-processing outputs to enable charts."
            )

        render_dos_subsection(analysis_files)
        st.divider()
        render_band_subsection(analysis_files)


def render_qe_input_form() -> None:
    """Render the QE input form and gate runnable generation on pseudo resolution."""
    defaults = get_qe_parameters()
    default_k_grid = defaults["k_points_grid"]
    atoms = st.session_state.get(ATOMS_STATE_KEY)
    blocker_message = get_qe_generation_blocker_message(atoms)
    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))

    st.divider()
    st.subheader("QE Input Parameters")
    st.caption("Basic parameters for a first Quantum ESPRESSO SCF-style workflow.")

    if blocker_message:
        if atoms is None:
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
        kx = row_three_col1.number_input(
            "K-Points X",
            min_value=1,
            value=int(default_k_grid[0]),
            step=1,
        )
        ky = row_three_col2.number_input(
            "K-Points Y",
            min_value=1,
            value=int(default_k_grid[1]),
            step=1,
        )
        kz = row_three_col3.number_input(
            "K-Points Z",
            min_value=1,
            value=int(default_k_grid[2]),
            step=1,
        )

        submitted = st.form_submit_button(
            "Generate Input File",
            use_container_width=True,
            disabled=blocker_message is not None or run_active,
        )

    if submitted:
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


st.set_page_config(page_title="QE Studio", layout="wide")
initialize_run_state(st.session_state)

with st.sidebar:
    st.title("QE Studio")
    st.write("A lightweight workspace for building Quantum ESPRESSO inputs.")
    uploaded_file = st.file_uploader("Upload a CIF file", type=["cif"], key="cif_uploader")
    store_uploaded_structure(uploaded_file)

    error_message = st.session_state.get(ERROR_STATE_KEY)
    atoms = st.session_state.get(ATOMS_STATE_KEY)

    if error_message:
        st.error(error_message)

    if atoms is not None:
        st.subheader("Structure Info")
        st.write(f"File: {st.session_state.get(FILENAME_STATE_KEY, 'Unknown')}")
        st.write(f"Formula: {atoms.get_chemical_formula()}")
        st.write(f"Atoms: {len(atoms)}")
        st.text(format_cell_parameters(atoms))

st.title("Structure Visualization")

atoms = st.session_state.get(ATOMS_STATE_KEY)
if atoms is None:
    st.info("Upload a CIF file from the sidebar to parse and visualize the structure.")
else:
    components.html(build_structure_viewer_html(atoms), height=520)

render_pseudopotentials_section(atoms)
render_qe_input_form()
render_generated_qe_input_preview()
render_qe_run_section()
render_electronic_properties_section(QE_RUNS_DIR)
