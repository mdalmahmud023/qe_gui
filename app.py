import io
import re
from pathlib import Path

import py3Dmol
import streamlit as st
import streamlit.components.v1 as components
from ase.calculators.espresso import Espresso, EspressoProfile
from ase.io import read, write


ATOMS_STATE_KEY = "structure_atoms"
CIF_BYTES_STATE_KEY = "structure_cif_bytes"
FILENAME_STATE_KEY = "structure_filename"
ERROR_STATE_KEY = "structure_error"
QE_PARAMS_STATE_KEY = "qe_parameters"
QE_INPUT_TEXT_STATE_KEY = "qe_input_text"
QE_INPUT_INTERNAL_PATH_STATE_KEY = "qe_input_internal_path"
QE_INPUT_UI_PATH_STATE_KEY = "qe_input_ui_path"

QE_CALCULATION_OPTIONS = ("scf", "nscf", "relax", "vc-relax")
QE_SMEARING_OPTIONS = ("gaussian", "methfessel-paxton", "marzari-vanderbilt")
QE_RUNS_DIR = Path("qe_runs")
QE_PSEUDO_DIR = QE_RUNS_DIR / "pseudos"

EMPTY_NAMELIST_RE = re.compile(r"(?ms)^&[A-Z_]+\n(?:\s*\n)*\s*/\n")


def clear_generated_qe_input_state() -> None:
    for key in (
        QE_INPUT_TEXT_STATE_KEY,
        QE_INPUT_INTERNAL_PATH_STATE_KEY,
        QE_INPUT_UI_PATH_STATE_KEY,
    ):
        st.session_state.pop(key, None)


def clear_structure_state() -> None:
    clear_generated_qe_input_state()
    for key in (
        ATOMS_STATE_KEY,
        CIF_BYTES_STATE_KEY,
        FILENAME_STATE_KEY,
        ERROR_STATE_KEY,
    ):
        st.session_state.pop(key, None)


def parse_cif_bytes(cif_bytes: bytes):
    return read(io.BytesIO(cif_bytes), format="cif", index=0)


def atoms_to_cif_text(atoms) -> str:
    cif_buffer = io.BytesIO()
    write(cif_buffer, atoms, format="cif")
    return cif_buffer.getvalue().decode("utf-8")


def build_structure_viewer_html(atoms, width: int = 900, height: int = 520) -> str:
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
    a, b, c, alpha, beta, gamma = atoms.cell.cellpar()
    return (
        f"a = {a:.3f} A\n"
        f"b = {b:.3f} A\n"
        f"c = {c:.3f} A\n"
        f"alpha = {alpha:.2f} deg\n"
        f"beta = {beta:.2f} deg\n"
        f"gamma = {gamma:.2f} deg"
    )


def store_uploaded_structure(uploaded_file) -> None:
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

    st.session_state[ATOMS_STATE_KEY] = atoms
    st.session_state[CIF_BYTES_STATE_KEY] = cif_bytes
    st.session_state[FILENAME_STATE_KEY] = uploaded_file.name
    st.session_state[ERROR_STATE_KEY] = None
    clear_generated_qe_input_state()


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
    system_data = {
        "ecutwfc": qe_parameters["ecutwfc"],
        "ecutrho": qe_parameters["ecutrho"],
        "smearing": qe_parameters["smearing"],
        "degauss": qe_parameters["degauss"],
    }
    if qe_parameters["degauss"] > 0:
        system_data["occupations"] = "smearing"

    return {
        "control": {
            "calculation": qe_parameters["calculation"],
        },
        "system": system_data,
    }


def build_placeholder_pseudopotentials(atoms) -> dict:
    species = dict.fromkeys(atoms.get_chemical_symbols())
    return {symbol: f"{symbol}.placeholder.UPF" for symbol in species}


def clean_generated_qe_input_text(input_text: str) -> str:
    cleaned = EMPTY_NAMELIST_RE.sub("", input_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return f"{cleaned}\n"


def generate_qe_input_file(atoms, qe_parameters: dict) -> tuple[str, str, str]:
    QE_PSEUDO_DIR.mkdir(parents=True, exist_ok=True)

    pseudopotentials = build_placeholder_pseudopotentials(atoms)
    input_data = build_qe_input_data(qe_parameters)
    profile = EspressoProfile(command="pw.x", pseudo_dir=QE_PSEUDO_DIR)
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


def render_generated_qe_input_preview() -> None:
    qe_input_text = st.session_state.get(QE_INPUT_TEXT_STATE_KEY)
    if not qe_input_text:
        return

    internal_path = st.session_state.get(QE_INPUT_INTERNAL_PATH_STATE_KEY, "qe_runs/espresso.pwi")
    ui_path = st.session_state.get(QE_INPUT_UI_PATH_STATE_KEY, "qe_runs/pw.in")

    st.success(
        "Generated Quantum ESPRESSO input files in "
        f"`{internal_path}` and `{ui_path}`."
    )
    st.warning(
        "Preview only: placeholder pseudopotential filenames are being used until "
        "real UPF files are provided."
    )
    st.code(qe_input_text, language="fortran")


def render_qe_input_form() -> None:
    defaults = get_qe_parameters()
    default_k_grid = defaults["k_points_grid"]

    st.divider()
    st.subheader("QE Input Parameters")
    st.caption("Basic parameters for a first Quantum ESPRESSO SCF-style workflow.")

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

        submitted = st.form_submit_button("Generate Input File", use_container_width=True)

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

        atoms = st.session_state.get(ATOMS_STATE_KEY)
        if atoms is None:
            clear_generated_qe_input_state()
            st.error("Upload a CIF file before generating a Quantum ESPRESSO input file.")
            return

        try:
            qe_input_text, internal_path, ui_path = generate_qe_input_file(atoms, qe_parameters)
        except Exception as exc:
            clear_generated_qe_input_state()
            st.error(f"Failed to generate the Quantum ESPRESSO input file: {exc}")
            return

        st.session_state[QE_INPUT_TEXT_STATE_KEY] = qe_input_text
        st.session_state[QE_INPUT_INTERNAL_PATH_STATE_KEY] = internal_path
        st.session_state[QE_INPUT_UI_PATH_STATE_KEY] = ui_path


st.set_page_config(page_title="QE Studio", layout="wide")

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

render_qe_input_form()
render_generated_qe_input_preview()
