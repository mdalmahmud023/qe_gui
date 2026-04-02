from __future__ import annotations

import io

import py3Dmol
import streamlit as st
import streamlit.components.v1 as components
from ase.io import write

from project_context import (
    ERROR_STATE_KEY,
    STRUCTURE_MODE_OPTIONS,
    ProjectContext,
    apply_structure_mode_selection,
    get_structure_mode,
    store_uploaded_structure,
)


def render_page(ctx: ProjectContext) -> None:
    """Render the Structure page."""
    st.subheader("Structure")
    st.caption("Upload a CIF file, choose the active cell representation, and inspect the structure in 3D.")

    upload_col, summary_col = st.columns([1.1, 0.9])
    with upload_col:
        uploaded_file = st.file_uploader("Upload a CIF file", type=["cif"], key="cif_uploader")
        structure_changed = store_uploaded_structure(uploaded_file)
        error_message = st.session_state.get(ERROR_STATE_KEY)
        if structure_changed and error_message is None:
            st.rerun()
        if error_message:
            st.error(error_message)

    with summary_col:
        _render_structure_summary(ctx)

    st.divider()
    _render_structure_mode_controls(ctx)
    st.divider()
    _render_structure_viewer(ctx)


def _render_structure_summary(ctx: ProjectContext) -> None:
    structure = ctx.structure
    st.markdown("#### Structure Summary")
    if not structure.loaded:
        st.info("No structure is loaded yet. Upload a CIF file to begin.")
        return

    col1, col2 = st.columns(2)
    col1.caption("Formula")
    col1.write(structure.formula or "Unknown")
    col2.caption("Atoms")
    col2.write(str(structure.atom_count))

    col3, col4 = st.columns(2)
    col3.caption("Cell mode")
    col3.write(structure.cell_mode_label)
    col4.caption("File")
    col4.write(structure.filename or "Unknown")

    if structure.cell_parameters:
        with st.expander("Cell Parameters", expanded=False):
            st.text(structure.cell_parameters)


def _render_structure_mode_controls(ctx: ProjectContext) -> None:
    st.markdown("#### Cell Representation")
    if ctx.raw_atoms is None:
        st.info("Upload a structure to choose between the uploaded cell and a primitive cell.")
        return

    current_mode = get_structure_mode()
    selected_mode = st.selectbox(
        "Cell Mode",
        options=STRUCTURE_MODE_OPTIONS,
        format_func=lambda value: "Primitive Cell" if value == "primitive" else "Uploaded Cell",
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
        st.info("Upload a CIF file to render the structure.")
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
