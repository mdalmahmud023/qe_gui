from __future__ import annotations

import io

import py3Dmol
import streamlit as st
import streamlit.components.v1 as components
from ase.io import write

from project_context import (
    ATOMS_STATE_KEY,
    ERROR_STATE_KEY,
    ProjectContext,
    STRUCTURE_MODE_OPTIONS,
    apply_structure_mode_selection,
    clear_structure_state,
    commit_structure_source,
    get_structure_mode,
    set_structure_error,
)
from structure_sources import (
    FILE_FORMAT_AUTO,
    FILE_FORMAT_CIF,
    FILE_FORMAT_VASP,
    FILE_FORMAT_XSF,
    FILE_FORMAT_XYZ,
    MANUAL_COORDINATE_MODE_CARTESIAN,
    MANUAL_COORDINATE_MODE_FRACTIONAL,
    build_manual_structure,
    build_ordered_double_perovskite,
    import_structure_file,
)


SOURCE_MODE_FILE = "file"
SOURCE_MODE_MANUAL = "manual"
SOURCE_MODE_PROTOTYPE = "prototype"
SOURCE_MODE_OPTIONS = (SOURCE_MODE_FILE, SOURCE_MODE_MANUAL, SOURCE_MODE_PROTOTYPE)

SOURCE_MODE_WIDGET_KEY = "structure_source_mode_widget"
STRUCTURE_NOTICE_STATE_KEY = "structure_notice_messages"
FILE_FORMAT_WIDGET_KEY = "structure_file_format_widget"
FILE_UPLOADER_KEY = "structure_file_uploader"
MANUAL_FORMULA_WIDGET_KEY = "structure_manual_formula"
MANUAL_A_WIDGET_KEY = "structure_manual_a"
MANUAL_B_WIDGET_KEY = "structure_manual_b"
MANUAL_C_WIDGET_KEY = "structure_manual_c"
MANUAL_ALPHA_WIDGET_KEY = "structure_manual_alpha"
MANUAL_BETA_WIDGET_KEY = "structure_manual_beta"
MANUAL_GAMMA_WIDGET_KEY = "structure_manual_gamma"
MANUAL_COORDINATE_MODE_WIDGET_KEY = "structure_manual_coordinate_mode"
MANUAL_ATOM_TABLE_WIDGET_KEY = "structure_manual_atom_table"
PROTOTYPE_A_SITE_WIDGET_KEY = "structure_prototype_a_site"
PROTOTYPE_B_SITE_WIDGET_KEY = "structure_prototype_b_site"
PROTOTYPE_B_PRIME_SITE_WIDGET_KEY = "structure_prototype_b_prime_site"
PROTOTYPE_X_SITE_WIDGET_KEY = "structure_prototype_x_site"
PROTOTYPE_A_PARAM_WIDGET_KEY = "structure_prototype_lattice_a"
PROTOTYPE_X_PARAM_WIDGET_KEY = "structure_prototype_x_parameter"


def render_page(ctx: ProjectContext) -> None:
    """Render the Structure page."""
    st.subheader("Structure")
    st.caption("Import or build a periodic structure, choose the active cell representation, and inspect the structure in 3D.")

    source_col, summary_col = st.columns([1.1, 0.9])
    with source_col:
        _render_structure_source_shell()

    with summary_col:
        _render_structure_summary(ctx)

    st.divider()
    _render_structure_mode_controls(ctx)
    st.divider()
    _render_structure_viewer(ctx)


def _render_structure_source_shell() -> None:
    st.markdown("#### Import / Build Structure")
    selected_mode = st.radio(
        "Structure Source",
        options=SOURCE_MODE_OPTIONS,
        index=0,
        horizontal=True,
        key=SOURCE_MODE_WIDGET_KEY,
        format_func=lambda value: {
            SOURCE_MODE_FILE: "Import File",
            SOURCE_MODE_MANUAL: "Manual Builder",
            SOURCE_MODE_PROTOTYPE: "Prototype Builder",
        }[value],
    )

    error_message = st.session_state.get(ERROR_STATE_KEY)
    if error_message:
        st.error(error_message)

    notice_messages = st.session_state.get(STRUCTURE_NOTICE_STATE_KEY, []) or []
    for warning_message in notice_messages:
        st.warning(warning_message)

    if selected_mode == SOURCE_MODE_FILE:
        _render_file_import_mode()
    elif selected_mode == SOURCE_MODE_MANUAL:
        _render_manual_builder_mode()
    else:
        _render_prototype_builder_mode()

    if st.session_state.get(ATOMS_STATE_KEY) is not None:
        if st.button("Clear Active Structure", use_container_width=True):
            clear_structure_state()
            st.session_state.pop(STRUCTURE_NOTICE_STATE_KEY, None)
            st.rerun()


def _render_file_import_mode() -> None:
    with st.form("structure_file_import_form"):
        st.caption("Supported v1 import formats: CIF, VASP POSCAR/CONTCAR, XSF, and XYZ/extXYZ with a valid 3D periodic cell.")
        selected_format = st.selectbox(
            "File format",
            options=(FILE_FORMAT_AUTO, FILE_FORMAT_CIF, FILE_FORMAT_VASP, FILE_FORMAT_XSF, FILE_FORMAT_XYZ),
            index=0,
            key=FILE_FORMAT_WIDGET_KEY,
            format_func=lambda value: {
                FILE_FORMAT_AUTO: "Auto-detect from filename",
                FILE_FORMAT_CIF: "CIF",
                FILE_FORMAT_VASP: "VASP POSCAR/CONTCAR",
                FILE_FORMAT_XSF: "XSF",
                FILE_FORMAT_XYZ: "XYZ / extXYZ",
            }[value],
        )
        uploaded_file = st.file_uploader(
            "Structure file",
            key=FILE_UPLOADER_KEY,
        )
        submitted = st.form_submit_button("Import Structure", use_container_width=True)

    if not submitted:
        return
    if uploaded_file is None:
        set_structure_error("Select a structure file to import.")
        st.rerun()
        return

    try:
        file_bytes = uploaded_file.getvalue()
        result = import_structure_file(
            filename=uploaded_file.name,
            file_bytes=file_bytes,
            requested_format=selected_format,
        )
        _commit_structure_result(result, payload_bytes=file_bytes)
    except Exception as exc:
        set_structure_error(f"Failed to import the structure file: {exc}")
    st.rerun()


def _render_manual_builder_mode() -> None:
    with st.form("structure_manual_builder_form"):
        st.caption("Enter lattice parameters plus an atom table to build a periodic crystal source cell.")
        formula_text = st.text_input(
            "Formula (optional validation only)",
            key=MANUAL_FORMULA_WIDGET_KEY,
            help="Use this only to validate the atom table. The structure is built from the rows below, not from the formula text.",
        )

        row_one_col1, row_one_col2, row_one_col3 = st.columns(3)
        a = row_one_col1.number_input("a (A)", min_value=0.0, value=6.0, step=0.1, key=MANUAL_A_WIDGET_KEY)
        b = row_one_col2.number_input("b (A)", min_value=0.0, value=6.0, step=0.1, key=MANUAL_B_WIDGET_KEY)
        c = row_one_col3.number_input("c (A)", min_value=0.0, value=6.0, step=0.1, key=MANUAL_C_WIDGET_KEY)

        row_two_col1, row_two_col2, row_two_col3 = st.columns(3)
        alpha = row_two_col1.number_input("alpha (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=MANUAL_ALPHA_WIDGET_KEY)
        beta = row_two_col2.number_input("beta (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=MANUAL_BETA_WIDGET_KEY)
        gamma = row_two_col3.number_input("gamma (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=MANUAL_GAMMA_WIDGET_KEY)

        coordinate_mode = st.radio(
            "Coordinate mode",
            options=(MANUAL_COORDINATE_MODE_FRACTIONAL, MANUAL_COORDINATE_MODE_CARTESIAN),
            horizontal=True,
            key=MANUAL_COORDINATE_MODE_WIDGET_KEY,
            format_func=lambda value: "Fractional" if value == MANUAL_COORDINATE_MODE_FRACTIONAL else "Cartesian",
        )
        atom_rows = st.data_editor(
            _default_manual_atom_rows(),
            key=MANUAL_ATOM_TABLE_WIDGET_KEY,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
        )
        submitted = st.form_submit_button("Build Structure", use_container_width=True)

    if not submitted:
        return

    try:
        result = build_manual_structure(
            a=float(a),
            b=float(b),
            c=float(c),
            alpha=float(alpha),
            beta=float(beta),
            gamma=float(gamma),
            coordinate_mode=coordinate_mode,
            atom_rows=atom_rows,
            formula_text=formula_text,
        )
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the manual structure: {exc}")
    st.rerun()


def _render_prototype_builder_mode() -> None:
    with st.form("structure_prototype_builder_form"):
        st.caption("Build an ordered cubic double perovskite / elpasolite starting structure for `A2BB'X6` systems.")
        row_one_col1, row_one_col2 = st.columns(2)
        a_site = row_one_col1.text_input("A site", value="Na", key=PROTOTYPE_A_SITE_WIDGET_KEY)
        x_site = row_one_col2.text_input("X site", value="H", key=PROTOTYPE_X_SITE_WIDGET_KEY)

        row_two_col1, row_two_col2 = st.columns(2)
        b_site = row_two_col1.text_input("B site", value="Ca", key=PROTOTYPE_B_SITE_WIDGET_KEY)
        b_prime_site = row_two_col2.text_input("B' site", value="Cd", key=PROTOTYPE_B_PRIME_SITE_WIDGET_KEY)

        lattice_parameter = st.number_input(
            "Cubic lattice parameter a (A)",
            min_value=0.0,
            value=8.0,
            step=0.1,
            key=PROTOTYPE_A_PARAM_WIDGET_KEY,
        )
        with st.expander("Advanced prototype options", expanded=False):
            x_parameter = st.number_input(
                "Anion Wyckoff x parameter",
                min_value=0.0,
                max_value=0.5,
                value=0.25,
                step=0.01,
                key=PROTOTYPE_X_PARAM_WIDGET_KEY,
                help="The ideal cubic double-perovskite value is 0.25.",
            )
        submitted = st.form_submit_button("Build Prototype Structure", use_container_width=True)

    if not submitted:
        return

    try:
        result = build_ordered_double_perovskite(
            a_site=a_site,
            b_site=b_site,
            b_prime_site=b_prime_site,
            x_site=x_site,
            lattice_parameter=float(lattice_parameter),
            x_parameter=float(st.session_state.get(PROTOTYPE_X_PARAM_WIDGET_KEY, 0.25)),
        )
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the prototype structure: {exc}")
    st.rerun()


def _commit_structure_result(result, *, payload_bytes: bytes | None = None) -> None:
    commit_structure_source(
        result.atoms,
        source_kind=result.source_kind,
        source_label=result.source_label,
        source_format=result.source_format,
        source_details=result.source_details,
        payload_bytes=payload_bytes,
    )
    set_structure_error(None)
    st.session_state[STRUCTURE_NOTICE_STATE_KEY] = list(result.warnings)


def _default_manual_atom_rows() -> list[dict[str, object]]:
    return [
        {"symbol": "", "x": 0.0, "y": 0.0, "z": 0.0},
        {"symbol": "", "x": 0.0, "y": 0.0, "z": 0.0},
    ]


def _render_structure_summary(ctx: ProjectContext) -> None:
    structure = ctx.structure
    st.markdown("#### Structure Summary")
    if not structure.loaded:
        st.info("No structure is loaded yet. Import or build one to begin.")
        return

    col1, col2 = st.columns(2)
    col1.caption("Formula")
    col1.write(structure.formula or "Unknown")
    col2.caption("Atoms")
    col2.write(str(structure.atom_count))

    col3, col4 = st.columns(2)
    col3.caption("Cell mode")
    col3.write(structure.cell_mode_label)
    col4.caption("Source")
    col4.write(structure.source_label or "Unknown")

    col5, col6 = st.columns(2)
    col5.caption("Source type")
    col5.write(structure.source_kind_label or "Unknown")
    col6.caption("Format")
    col6.write((structure.source_format or "Built").upper() if structure.source_format else "Built")

    if structure.cell_parameters:
        with st.expander("Cell Parameters", expanded=False):
            st.text(structure.cell_parameters)


def _render_structure_mode_controls(ctx: ProjectContext) -> None:
    st.markdown("#### Cell Representation")
    if ctx.raw_atoms is None:
        st.info("Load or build a structure to choose between the source cell and a primitive cell.")
        return

    current_mode = get_structure_mode()
    selected_mode = st.selectbox(
        "Cell Mode",
        options=STRUCTURE_MODE_OPTIONS,
        format_func=lambda value: "Primitive Cell" if value == "primitive" else "Source Cell",
        index=STRUCTURE_MODE_OPTIONS.index(current_mode) if current_mode in STRUCTURE_MODE_OPTIONS else 0,
    )
    if selected_mode != current_mode:
        try:
            apply_structure_mode_selection(selected_mode)
        except Exception as exc:
            st.error(f"Failed to apply the selected cell mode: {exc}")
        else:
            st.rerun()

    if ctx.structure.mode_status:
        if "failed" in ctx.structure.mode_status.lower():
            st.warning(ctx.structure.mode_status)
        else:
            st.caption(ctx.structure.mode_status)
    st.caption("Changing the cell mode affects newly generated QE inputs. Regenerate `pw.in` and rerun SCF before DOS or bands.")


def _render_structure_viewer(ctx: ProjectContext) -> None:
    st.markdown("#### 3D Visualization")
    if ctx.atoms is None:
        st.info("Load or build a structure to render it.")
        return

    components.html(_build_structure_viewer_html(ctx.atoms), height=520)


def _atoms_to_cif_text(atoms) -> str:
    cif_buffer = io.BytesIO()
    write(cif_buffer, atoms, format="cif")
    return cif_buffer.getvalue().decode("utf-8")


def _build_structure_viewer_html(atoms, width: int = 900, height: int = 520) -> str:
    viewer = py3Dmol.view(width=width, height=height)
    viewer.addModel(_atoms_to_cif_text(atoms), "cif")
    viewer.setStyle({"sphere": {"scale": 0.3}, "stick": {"radius": 0.16}})
    viewer.setBackgroundColor("white")
    if atoms.cell.rank > 0 and atoms.cell.volume > 0:
        viewer.addUnitCell()
    viewer.zoomTo()
    viewer.render()
    return viewer._make_html()
